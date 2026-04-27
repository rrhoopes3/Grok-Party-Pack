"""
Concurrent Guardrail Layer — runs safety checks in parallel with executor output.

Inspired by OpenAI's "A Practical Guide to Building Agents" recommendation:
guardrails run concurrently alongside the primary agent, triggering exceptions
if constraints are breached. Uses optimistic execution by default.

Guardrails:
  - Input guardrails: validate tool call arguments before execution
  - Output guardrails: validate tool results and agent text after generation
  - Policy guardrails: enforce configurable rules (path blocklist, command blocklist, etc.)

Each guardrail returns a GuardrailResult. If any guardrail trips, the executor
receives a violation event and can halt or warn.
"""
from __future__ import annotations
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("forge.guardrails")

# ── Data Structures ──────────────────────────────────────────────────────


@dataclass
class GuardrailResult:
    """Result of a single guardrail check."""
    passed: bool
    guardrail_name: str
    message: str = ""
    severity: str = "warning"  # "warning" | "block"


@dataclass
class GuardrailViolation:
    """Emitted when a guardrail trips."""
    guardrail_name: str
    message: str
    severity: str  # "warning" | "block"
    context: dict = field(default_factory=dict)


# Type alias for guardrail functions
# Input guardrails: (tool_name, args) -> GuardrailResult
InputGuardrailFn = Callable[[str, dict], GuardrailResult]
# Output guardrails: (content) -> GuardrailResult
OutputGuardrailFn = Callable[[str], GuardrailResult]


# ── Built-in Guardrails ──────────────────────────────────────────────────

# Dangerous shell patterns that should be blocked
_DANGEROUS_COMMANDS = [
    r"\brm\s+-rf\s+/",            # rm -rf /
    r"\bmkfs\b",                    # format filesystem
    r"\bdd\s+if=.*of=/dev/",       # dd to device
    r">\s*/dev/sd[a-z]",           # redirect to disk device
    r"\bcurl\b.*\|\s*\bbash\b",    # curl | bash
    r"\bwget\b.*\|\s*\bbash\b",    # wget | bash
    r"\bchmod\s+777\s+/",         # chmod 777 on root
    r"\b:(){ :\|:& };:",          # fork bomb
    r"\bshutdown\b",               # system shutdown
    r"\breboot\b",                  # system reboot
    r"\bpkill\s+-9\s+-1\b",       # kill all processes
]

# Sensitive file patterns that shouldn't be read/written
_SENSITIVE_PATHS = [
    r"/etc/shadow",
    r"/etc/passwd",
    r"\.ssh/id_",
    r"\.env\.prod",
    r"\.env\.production",
    r"/\.aws/credentials",
    r"/\.kube/config",
    r"private[_-]?key",
]

# Patterns in output that suggest credential leakage
_CREDENTIAL_PATTERNS = [
    r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}",
    r"(?:secret|token|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}",
    r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----",
    r"sk-[A-Za-z0-9]{20,}",         # OpenAI-style keys
    r"xai-[A-Za-z0-9]{20,}",        # xAI keys
    r"ghp_[A-Za-z0-9]{36,}",        # GitHub PATs
    r"AKIA[A-Z0-9]{16}",            # AWS access keys
]


def check_dangerous_command(tool_name: str, args: dict) -> GuardrailResult:
    """Block dangerous shell commands."""
    if tool_name != "run_command":
        return GuardrailResult(passed=True, guardrail_name="dangerous_command")

    command = args.get("command", "")
    for pattern in _DANGEROUS_COMMANDS:
        if re.search(pattern, command, re.IGNORECASE):
            return GuardrailResult(
                passed=False,
                guardrail_name="dangerous_command",
                message=f"Blocked dangerous command pattern: {pattern}",
                severity="block",
            )
    return GuardrailResult(passed=True, guardrail_name="dangerous_command")


def check_sensitive_paths(tool_name: str, args: dict) -> GuardrailResult:
    """Warn on access to sensitive file paths."""
    path_args = []
    for key in ("path", "input_path", "output_path", "directory", "database"):
        if key in args:
            path_args.append(args[key])

    if not path_args:
        return GuardrailResult(passed=True, guardrail_name="sensitive_paths")

    for path in path_args:
        for pattern in _SENSITIVE_PATHS:
            if re.search(pattern, path, re.IGNORECASE):
                return GuardrailResult(
                    passed=False,
                    guardrail_name="sensitive_paths",
                    message=f"Access to sensitive path blocked: {path}",
                    severity="block",
                )
    return GuardrailResult(passed=True, guardrail_name="sensitive_paths")


def check_credential_leakage(content: str) -> GuardrailResult:
    """Detect potential credential leakage in output."""
    for pattern in _CREDENTIAL_PATTERNS:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return GuardrailResult(
                passed=False,
                guardrail_name="credential_leakage",
                message=f"Potential credential detected in output (pattern: {pattern[:30]}...)",
                severity="warning",
            )
    return GuardrailResult(passed=True, guardrail_name="credential_leakage")


def check_media_provenance(tool_name: str, args: dict) -> GuardrailResult:
    """Warn when image tools process media without VRC-48M provenance."""
    _MEDIA_TOOLS = {"resize_image", "convert_image"}
    if tool_name not in _MEDIA_TOOLS:
        return GuardrailResult(passed=True, guardrail_name="media_provenance")

    input_path = args.get("input_path", "")
    if not input_path:
        return GuardrailResult(passed=True, guardrail_name="media_provenance")

    import os as _os
    if not _os.path.exists(input_path + ".vrc48m.json"):
        return GuardrailResult(
            passed=False,
            guardrail_name="media_provenance",
            message=f"Media has no VRC-48M provenance sidecar: {input_path}",
            severity="warning",
        )
    return GuardrailResult(passed=True, guardrail_name="media_provenance")


def check_output_length(content: str) -> GuardrailResult:
    """Warn if output is suspiciously large (potential data exfiltration)."""
    if len(content) > 100_000:
        return GuardrailResult(
            passed=False,
            guardrail_name="output_length",
            message=f"Unusually large output ({len(content)} chars) — possible data dump",
            severity="warning",
        )
    return GuardrailResult(passed=True, guardrail_name="output_length")


# ── Guardrail Engine ─────────────────────────────────────────────────────


class GuardrailEngine:
    """Concurrent guardrail engine that validates inputs and outputs.

    Runs all registered guardrails in a thread pool for minimal latency impact.
    Supports both input guardrails (pre-tool-call) and output guardrails (post-output).
    """

    def __init__(self, enabled: bool = True, max_workers: int = 4):
        self.enabled = enabled
        self._input_guardrails: list[InputGuardrailFn] = []
        self._output_guardrails: list[OutputGuardrailFn] = []
        self._violations: list[GuardrailViolation] = []
        self._lock = threading.Lock()
        self._max_workers = max_workers

        if enabled:
            # Register built-in guardrails
            self.add_input_guardrail(check_dangerous_command)
            self.add_input_guardrail(check_sensitive_paths)
            self.add_output_guardrail(check_credential_leakage)
            self.add_output_guardrail(check_output_length)
            self.add_input_guardrail(check_media_provenance)

    def add_input_guardrail(self, fn: InputGuardrailFn):
        """Register an input guardrail (runs before tool execution)."""
        self._input_guardrails.append(fn)

    def add_output_guardrail(self, fn: OutputGuardrailFn):
        """Register an output guardrail (runs on tool results / agent output)."""
        self._output_guardrails.append(fn)

    def check_input(self, tool_name: str, args: dict) -> list[GuardrailViolation]:
        """Run all input guardrails concurrently. Returns list of violations."""
        if not self.enabled or not self._input_guardrails:
            return []

        violations = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(fn, tool_name, args): fn
                for fn in self._input_guardrails
            }
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=5)
                    if not result.passed:
                        v = GuardrailViolation(
                            guardrail_name=result.guardrail_name,
                            message=result.message,
                            severity=result.severity,
                            context={"tool": tool_name, "args_keys": list(args.keys())},
                        )
                        violations.append(v)
                except Exception as e:
                    log.warning("Input guardrail %s failed: %s", futures[future].__name__, e)

        if violations:
            with self._lock:
                self._violations.extend(violations)
            for v in violations:
                log.warning("Guardrail violation [%s/%s]: %s",
                            v.severity, v.guardrail_name, v.message)

        return violations

    def check_output(self, content: str) -> list[GuardrailViolation]:
        """Run all output guardrails concurrently. Returns list of violations."""
        if not self.enabled or not self._output_guardrails or not content:
            return []

        violations = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(fn, content): fn
                for fn in self._output_guardrails
            }
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=5)
                    if not result.passed:
                        v = GuardrailViolation(
                            guardrail_name=result.guardrail_name,
                            message=result.message,
                            severity=result.severity,
                            context={"content_length": len(content)},
                        )
                        violations.append(v)
                except Exception as e:
                    log.warning("Output guardrail %s failed: %s", futures[future].__name__, e)

        if violations:
            with self._lock:
                self._violations.extend(violations)
            for v in violations:
                log.warning("Guardrail violation [%s/%s]: %s",
                            v.severity, v.guardrail_name, v.message)

        return violations

    def has_blocking_violation(self, violations: list[GuardrailViolation]) -> bool:
        """Check if any violation is severity=block."""
        return any(v.severity == "block" for v in violations)

    @property
    def violations(self) -> list[GuardrailViolation]:
        """All violations recorded this session."""
        with self._lock:
            return list(self._violations)

    @property
    def violation_count(self) -> int:
        with self._lock:
            return len(self._violations)

    def reset(self):
        """Clear violation history."""
        with self._lock:
            self._violations.clear()

    def summary(self) -> dict:
        """Return a summary of guardrail activity."""
        with self._lock:
            blocks = sum(1 for v in self._violations if v.severity == "block")
            warnings = sum(1 for v in self._violations if v.severity == "warning")
            return {
                "total_violations": len(self._violations),
                "blocks": blocks,
                "warnings": warnings,
                "guardrails_active": len(self._input_guardrails) + len(self._output_guardrails),
            }
