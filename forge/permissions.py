"""
Tool Permission Middleware — per-tool approval modes layered on top of the firewall.

Pattern borrowed from CLI agent permission systems: each tool can operate in one
of three modes:

  - AUTO:    Execute without confirmation (default for SAFE tools)
  - CONFIRM: Requires explicit approval before execution (default for CAUTION/DANGER)
  - DENY:    Always blocked, never executed

The PermissionEngine sits between the firewall verdict and actual tool execution,
adding an interactive approval layer for web/CLI sessions. In headless mode,
CONFIRM falls back to the configured `headless_policy` (allow or deny).

Overrides can be set per-tool, per-category, or per-session via the API or config.
"""
from __future__ import annotations

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable

from forge.firewall import RiskLevel, SemanticFirewall, FirewallVerdict

log = logging.getLogger("forge.permissions")


class PermissionMode(str, Enum):
    AUTO = "auto"          # Execute without asking
    CONFIRM = "confirm"    # Ask before executing
    DENY = "deny"          # Always block


@dataclass
class PermissionDecision:
    """Result of a permission check."""
    tool_name: str
    mode: PermissionMode
    allowed: bool
    reason: str = ""
    firewall_verdict: FirewallVerdict | None = None


# Default mappings: firewall risk → permission mode
_RISK_TO_MODE: dict[RiskLevel, PermissionMode] = {
    RiskLevel.SAFE: PermissionMode.AUTO,
    RiskLevel.CAUTION: PermissionMode.CONFIRM,
    RiskLevel.DANGER: PermissionMode.DENY,
}


class PermissionEngine:
    """Per-tool permission middleware that wraps the SemanticFirewall.

    Usage:
        engine = PermissionEngine(firewall=my_firewall)
        engine.set_mode("run_command", PermissionMode.CONFIRM)
        decision = engine.check("run_command", {"command": "ls"})
        if decision.mode == PermissionMode.CONFIRM:
            # prompt user for approval, then call engine.approve/deny
    """

    def __init__(
        self,
        firewall: SemanticFirewall | None = None,
        headless_policy: PermissionMode = PermissionMode.DENY,
        approval_callback: Callable[[str, str, dict], bool] | None = None,
    ):
        self._firewall = firewall or SemanticFirewall()
        self._headless_policy = headless_policy
        self._approval_callback = approval_callback

        # Per-tool overrides: tool_name → PermissionMode
        self._tool_overrides: dict[str, PermissionMode] = {}
        # Per-category overrides: category → PermissionMode
        self._category_overrides: dict[str, PermissionMode] = {}
        # Session-scoped approvals: tool_name → set of approved arg hashes
        self._session_approvals: set[str] = set()
        # Audit log
        self._decisions: list[PermissionDecision] = []

    # ── Configuration ────────────────────────────────────────────────

    def set_mode(self, tool_name: str, mode: PermissionMode) -> None:
        """Override the permission mode for a specific tool."""
        self._tool_overrides[tool_name] = mode
        log.info("Permission override: %s → %s", tool_name, mode.value)

    def set_category_mode(self, category: str, mode: PermissionMode) -> None:
        """Override the permission mode for an entire tool category."""
        self._category_overrides[category] = mode
        log.info("Category permission override: %s → %s", category, mode.value)

    def approve_for_session(self, tool_name: str) -> None:
        """Grant AUTO permission for a tool for the rest of this session."""
        self._session_approvals.add(tool_name)
        log.info("Session approval granted: %s", tool_name)

    def revoke_session_approval(self, tool_name: str) -> None:
        """Revoke a previously granted session approval."""
        self._session_approvals.discard(tool_name)

    def set_approval_callback(self, callback: Callable[[str, str, dict], bool]) -> None:
        """Set a callback for interactive approval prompts.

        callback(tool_name, reason, args) -> bool
        """
        self._approval_callback = callback

    # ── Core check ───────────────────────────────────────────────────

    def _resolve_mode(self, tool_name: str, firewall_risk: RiskLevel) -> PermissionMode:
        """Determine the effective permission mode for a tool call."""
        # Session approvals override everything
        if tool_name in self._session_approvals:
            return PermissionMode.AUTO

        # Explicit per-tool override
        if tool_name in self._tool_overrides:
            return self._tool_overrides[tool_name]

        # Per-category override
        try:
            from forge.tools.registry import TOOL_TO_CATEGORY
        except ImportError:
            TOOL_TO_CATEGORY = {}
        category = TOOL_TO_CATEGORY.get(tool_name)
        if category and category in self._category_overrides:
            return self._category_overrides[category]

        # Default: map from firewall risk level
        return _RISK_TO_MODE.get(firewall_risk, PermissionMode.CONFIRM)

    def check(self, tool_name: str, args: dict) -> PermissionDecision:
        """Check whether a tool call is permitted.

        Returns a PermissionDecision. If mode is CONFIRM, the caller should
        prompt the user and then call approve_for_session() or let it be denied.
        """
        # Run firewall first
        verdict = self._firewall.check(tool_name, args)

        # If firewall hard-blocks it, deny regardless
        if not verdict.allowed:
            decision = PermissionDecision(
                tool_name=tool_name,
                mode=PermissionMode.DENY,
                allowed=False,
                reason=verdict.blocked_reason or "Blocked by firewall",
                firewall_verdict=verdict,
            )
            self._decisions.append(decision)
            return decision

        mode = self._resolve_mode(tool_name, verdict.risk)

        if mode == PermissionMode.AUTO:
            decision = PermissionDecision(
                tool_name=tool_name,
                mode=mode,
                allowed=True,
                reason="Auto-approved",
                firewall_verdict=verdict,
            )
        elif mode == PermissionMode.DENY:
            decision = PermissionDecision(
                tool_name=tool_name,
                mode=mode,
                allowed=False,
                reason=f"Tool '{tool_name}' is denied by permission policy",
                firewall_verdict=verdict,
            )
        else:
            # CONFIRM mode — try callback, fall back to headless policy
            approved = False
            reason = "Awaiting confirmation"

            if self._approval_callback:
                try:
                    concern_str = "; ".join(verdict.concerns) if verdict.concerns else f"Tool requires confirmation: {tool_name}"
                    approved = self._approval_callback(tool_name, concern_str, args)
                    reason = "Approved by user" if approved else "Denied by user"
                except Exception as e:
                    log.warning("Approval callback failed for %s: %s", tool_name, e)
                    approved = self._headless_policy == PermissionMode.AUTO
                    reason = f"Callback failed, headless policy: {self._headless_policy.value}"
            else:
                # No callback — use headless policy
                approved = self._headless_policy == PermissionMode.AUTO
                reason = f"No approval callback, headless policy: {self._headless_policy.value}"

            decision = PermissionDecision(
                tool_name=tool_name,
                mode=mode,
                allowed=approved,
                reason=reason,
                firewall_verdict=verdict,
            )

        self._decisions.append(decision)

        if not decision.allowed:
            log.warning("PERMISSION DENIED: %s — %s", tool_name, decision.reason)
        elif mode == PermissionMode.CONFIRM:
            log.info("PERMISSION CONFIRMED: %s — %s", tool_name, decision.reason)

        return decision

    # ── Bulk configuration ───────────────────────────────────────────

    def allow_all(self) -> None:
        """Set all tools to AUTO mode. Use with caution (e.g., CI pipelines)."""
        self._headless_policy = PermissionMode.AUTO
        log.warning("Permission engine set to allow-all mode")

    def lockdown(self) -> None:
        """Set headless policy to DENY and clear all session approvals."""
        self._headless_policy = PermissionMode.DENY
        self._session_approvals.clear()
        log.info("Permission engine locked down")

    # ── Introspection ────────────────────────────────────────────────

    @property
    def audit_log(self) -> list[PermissionDecision]:
        return list(self._decisions)

    def stats(self) -> dict:
        total = len(self._decisions)
        allowed = sum(1 for d in self._decisions if d.allowed)
        denied = total - allowed
        by_mode = {}
        for d in self._decisions:
            by_mode[d.mode.value] = by_mode.get(d.mode.value, 0) + 1
        return {
            "total_checks": total,
            "allowed": allowed,
            "denied": denied,
            "by_mode": by_mode,
            "session_approvals": list(self._session_approvals),
        }

    def get_tool_modes(self) -> dict[str, str]:
        """Return a summary of all effective tool modes (overrides + defaults)."""
        try:
            from forge.tools.registry import TOOL_TO_CATEGORY
        except ImportError:
            TOOL_TO_CATEGORY = {}
        modes = {}
        for tool_name in TOOL_TO_CATEGORY:
            if tool_name in self._session_approvals:
                modes[tool_name] = PermissionMode.AUTO.value
            elif tool_name in self._tool_overrides:
                modes[tool_name] = self._tool_overrides[tool_name].value
            else:
                modes[tool_name] = "(default)"
        return modes
