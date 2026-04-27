"""
Semantic Firewall — monitors LLM outputs for dangerous operations.

Cross-pollinated from "AgentOS" (arXiv:2603.08938, Mar 2026):

  "Unlike a traditional OS, which rarely deletes a critical directory
   without explicit instructions, an AgentOS misunderstanding a vague
   command like 'clean up my workspace' could irreversibly delete
   project files."

  "AgentOS requires a Semantic Firewall integrated within the Agent
   Kernel, functioning as a real-time text and data mining security
   layer that monitors information flows into and out of the LLM core."

This module implements a pre-execution safety layer that inspects
tool calls before they run, classifying them by risk level:

  - SAFE:     read-only operations, low blast radius
  - CAUTION:  writes, modifications — allowed but logged
  - DANGER:   destructive operations — blocked unless explicitly allowed

The firewall also detects common hallucination patterns where the LLM
might misinterpret user intent into dangerous actions.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger("forge.firewall")


class RiskLevel(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGER = "danger"


# ── Tool Risk Classification ───────────────────────────────────────────────

# Tools that are always safe (read-only)
SAFE_TOOLS = frozenset({
    "read_file", "list_directory", "find_files", "grep_files",
    "git_status", "git_log", "git_diff",
    "http_get",
    "read_clipboard",
    "email_check_dmarc", "email_check_health",
    "email_get_logs", "email_get_analytics",
    "email_list_domains", "email_list_aliases",
    # Trading read-only
    "get_portfolio", "get_market_quote", "fetch_pcr",
    "analyze_sentiment", "get_options_chain",
    "get_trading_agent_status",
    # Provenance read-only
    "provenance_verify", "provenance_inspect",
})

# Tools that modify state but are generally expected
CAUTION_TOOLS = frozenset({
    "write_file", "append_file",
    "run_python",
    "git_commit",
    "http_post",
    "copy_to_clipboard",
    "resize_image", "convert_image",
    "zip_files",
    "email_create_alias", "email_add_domain", "email_verify_domain",
    "query_sqlite",
    # Trading actions (real money but user-initiated)
    "execute_trade", "set_alert",
    "start_trading_agent", "stop_trading_agent",
    # Provenance signing (creates files)
    "provenance_sign",
})

# Tools that can cause irreversible damage
# Note: `run_command` is intentionally NOT here — it's classified as CAUTION
# by default and only escalated to DANGER when its argument matches one of
# the destructive patterns below (rm -rf, dd, sudo, etc). Blanket-blocking
# `run_command` by name was a footgun: it killed read-only ops like
# `python -c "import ast; ast.parse(...)"` that the executor needs.
DANGER_TOOLS = frozenset({
    "delete_file",
    "extract_archive",      # can overwrite files
    "email_block_sender",   # blocks a sender permanently
})

# Tools that need command-level inspection to determine risk. They start at
# CAUTION and may be promoted to DANGER by deep inspection further down.
COMMAND_INSPECTED_TOOLS = frozenset({
    "run_command",
})


# ── Dangerous Command Patterns ─────────────────────────────────────────────
# Patterns in run_command arguments that indicate destructive operations

DANGEROUS_COMMAND_PATTERNS = [
    # Destructive file operations
    (r"\brm\b.*-[rR]", "Recursive file deletion (rm -r)"),
    (r"\brm\b.*-[fF]", "Forced file deletion (rm -f)"),
    (r"\brmdir\b", "Directory removal"),
    (r"\bshred\b", "File shredding"),
    (r"\bmkfs\b", "Filesystem formatting"),
    (r"\bdd\b\s+if=", "Raw disk write (dd)"),

    # Dangerous git operations
    (r"\bgit\b.*\bpush\b.*--force", "Force push (can overwrite remote history)"),
    (r"\bgit\b.*\breset\b.*--hard", "Hard reset (discards uncommitted changes)"),
    (r"\bgit\b.*\bclean\b.*-[fdFD]", "Git clean (deletes untracked files)"),
    (r"\bgit\b.*\bbranch\b.*-[dD]", "Branch deletion"),

    # System-level operations
    (r"\bsudo\b", "Elevated privileges (sudo)"),
    (r"\bchmod\b.*777", "World-writable permissions"),
    (r"\bchown\b", "Ownership change"),
    (r"\bkill\b.*-9", "Force kill process"),
    (r"\bpkill\b", "Process kill by name"),
    (r"\bshutdown\b", "System shutdown"),
    (r"\breboot\b", "System reboot"),

    # Network operations
    (r"\bcurl\b.*\|\s*sh", "Pipe remote script to shell"),
    (r"\bwget\b.*\|\s*sh", "Pipe remote script to shell"),
    (r"\bcurl\b.*\|\s*bash", "Pipe remote script to bash"),

    # Package management
    (r"\bpip\b.*\buninstall\b", "Package uninstallation"),
    (r"\bnpm\b.*\buninstall\b", "Package uninstallation"),
    (r"\bapt\b.*\bremove\b", "Package removal"),
    (r"\bapt\b.*\bpurge\b", "Package purge"),

    # Database operations
    (r"\bDROP\b\s+(TABLE|DATABASE|INDEX)", "Database drop operation"),
    (r"\bTRUNCATE\b", "Database truncate"),
    (r"\bDELETE\b\s+FROM\b(?!\s[\s\S]*?\bWHERE\b)", "DELETE without WHERE clause"),
]

# ── Dangerous Write Patterns ──────────────────────────────────────────────
# Patterns in write_file paths that indicate writing to sensitive locations

SENSITIVE_PATHS = [
    (r"^/etc/", "System configuration directory"),
    (r"^/usr/", "System binaries directory"),
    (r"^/boot/", "Boot directory"),
    (r"^/sys/", "Kernel interface"),
    (r"^/proc/", "Process information"),
    (r"^~?/\.", "Hidden/dotfile in home directory"),
    (r"\.env$", "Environment file (may contain secrets)"),
    (r"\.ssh/", "SSH configuration"),
    (r"\.git/config$", "Git configuration"),
    (r"\.gitignore$", "Git ignore rules"),
    (r"id_rsa", "SSH private key"),
    (r"credentials", "Credentials file"),
    (r"\.pem$", "Certificate/key file"),
    (r"\.key$", "Key file"),
]


# ── Semantic Firewall ──────────────────────────────────────────────────────

@dataclass
class FirewallVerdict:
    """Result of a firewall check on a tool call."""
    risk: RiskLevel
    allowed: bool
    tool_name: str
    concerns: list[str] = field(default_factory=list)
    blocked_reason: str = ""


class SemanticFirewall:
    """Pre-execution safety layer that classifies tool calls by risk.

    Sits between the LLM output and tool execution, acting as the
    "Semantic Firewall" described in the AgentOS paper.
    """

    def __init__(
        self,
        block_danger: bool = True,
        allowed_danger_tools: set[str] | None = None,
        allowed_danger_patterns: set[str] | None = None,
    ):
        self.block_danger = block_danger
        self._allowed_danger_tools = allowed_danger_tools or set()
        self._allowed_danger_patterns = allowed_danger_patterns or set()
        self._audit_log: list[FirewallVerdict] = []

    def check(self, tool_name: str, args: dict) -> FirewallVerdict:
        """Inspect a tool call and return a risk verdict.

        Called before every tool execution. Returns whether the call
        should be allowed and any concerns detected.
        """
        concerns: list[str] = []
        risk = RiskLevel.SAFE

        # ── Classify by tool name ──────────────────────────────────────
        if tool_name in SAFE_TOOLS:
            risk = RiskLevel.SAFE
        elif tool_name in CAUTION_TOOLS:
            risk = RiskLevel.CAUTION
        elif tool_name in COMMAND_INSPECTED_TOOLS:
            # Command-line tools — start at CAUTION; deep inspection below
            # may promote to DANGER if the argument matches a destructive
            # pattern. A read-only `python -c "import ast"` will stay CAUTION.
            risk = RiskLevel.CAUTION
        elif tool_name in DANGER_TOOLS:
            risk = RiskLevel.DANGER
            concerns.append(f"Tool '{tool_name}' is classified as dangerous")
        else:
            risk = RiskLevel.CAUTION
            concerns.append(f"Unknown tool '{tool_name}' — treating as caution")

        # ── Deep inspection of run_command ─────────────────────────────
        if tool_name == "run_command":
            command = args.get("command", "")
            matched_any = False
            for pattern, description in DANGEROUS_COMMAND_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    risk = RiskLevel.DANGER
                    concerns.append(f"Dangerous command pattern: {description}")
                    matched_any = True
            if not matched_any:
                # Read-only / harmless command — stays at CAUTION, no concerns
                # to log. Empty command is suspicious enough to flag.
                if not command.strip():
                    risk = RiskLevel.DANGER
                    concerns.append("Empty command")

        # ── Deep inspection of write_file / delete_file ────────────────
        if tool_name in ("write_file", "delete_file", "append_file"):
            path = args.get("path", "") or args.get("file_path", "")
            for pattern, description in SENSITIVE_PATHS:
                if re.search(pattern, path, re.IGNORECASE):
                    risk = RiskLevel.DANGER
                    concerns.append(f"Sensitive path: {description} ({path})")

        # ── Deep inspection of query_sqlite ────────────────────────────
        if tool_name == "query_sqlite":
            query = args.get("query", "")
            for pattern, description in DANGEROUS_COMMAND_PATTERNS:
                if "DROP" in pattern or "TRUNCATE" in pattern or "DELETE" in pattern:
                    if re.search(pattern, query, re.IGNORECASE):
                        risk = RiskLevel.DANGER
                        concerns.append(f"Dangerous SQL: {description}")

        # ── Decide whether to block ────────────────────────────────────
        blocked = False
        blocked_reason = ""

        if risk == RiskLevel.DANGER and self.block_danger:
            # Check if this specific tool is explicitly allowed
            if tool_name in self._allowed_danger_tools:
                blocked = False
            else:
                # Check if all matched patterns are in the allowed set
                matched_descriptions = {c for c in concerns if c.startswith("Dangerous command pattern:")}
                if matched_descriptions and self._allowed_danger_patterns:
                    unallowed = [c for c in matched_descriptions
                                 if not any(ap in c for ap in self._allowed_danger_patterns)]
                    if not unallowed and matched_descriptions:
                        blocked = False  # all matched patterns are explicitly allowed
                    else:
                        blocked = True
                        blocked_reason = "; ".join(concerns) if concerns else f"Dangerous tool: {tool_name}"
                else:
                    blocked = True
                    blocked_reason = "; ".join(concerns) if concerns else f"Dangerous tool: {tool_name}"

        verdict = FirewallVerdict(
            risk=risk,
            allowed=not blocked,
            tool_name=tool_name,
            concerns=concerns,
            blocked_reason=blocked_reason,
        )

        self._audit_log.append(verdict)

        if blocked:
            log.warning("FIREWALL BLOCKED: %s(%s) — %s", tool_name, args, blocked_reason)
        elif concerns:
            log.info("FIREWALL CAUTION: %s — %s", tool_name, "; ".join(concerns))

        return verdict

    @property
    def audit_log(self) -> list[FirewallVerdict]:
        """Full audit trail of all firewall checks."""
        return list(self._audit_log)

    def stats(self) -> dict:
        """Summary statistics of firewall activity."""
        total = len(self._audit_log)
        blocked = sum(1 for v in self._audit_log if not v.allowed)
        by_risk = {}
        for v in self._audit_log:
            by_risk[v.risk.value] = by_risk.get(v.risk.value, 0) + 1
        return {
            "total_checks": total,
            "blocked": blocked,
            "by_risk": by_risk,
        }
