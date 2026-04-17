"""
Capability Packs — declarative mode system for The Forge.

A CapabilityPack bundles everything needed for a specific mode of operation:
tool allowlist, default model, guardrail profile, budget, env requirements,
and runtime readiness checks.

Packs turn Forge from "a pile of features" into "intentional modes"
that know which blade to open.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from importlib import import_module
from typing import Callable, Literal

log = logging.getLogger("forge.packs")


# ── Readiness ────────────────────────────────────────────────────────────


ReadinessState = Literal["ready", "degraded", "unavailable"]


@dataclass
class ReadinessCheck:
    """Single readiness probe result."""
    name: str
    status: ReadinessState
    message: str = ""


@dataclass
class ReadinessReport:
    """Aggregated readiness for a capability pack."""
    pack_name: str
    checks: list[ReadinessCheck] = field(default_factory=list)

    @property
    def state(self) -> ReadinessState:
        """Overall state: worst of all checks."""
        if any(c.status == "unavailable" for c in self.checks):
            return "unavailable"
        if any(c.status == "degraded" for c in self.checks):
            return "degraded"
        return "ready"

    @property
    def summary(self) -> str:
        """Human-readable summary line."""
        state = self.state
        if state == "ready":
            return "All systems go"
        issues = [c for c in self.checks if c.status != "ready"]
        parts = [f"{c.name}: {c.message}" for c in issues]
        return "; ".join(parts)

    def to_dict(self) -> dict:
        return {
            "pack": self.pack_name,
            "state": self.state,
            "summary": self.summary,
            "checks": [
                {"name": c.name, "status": c.status, "message": c.message}
                for c in self.checks
            ],
        }


# ── Budget ───────────────────────────────────────────────────────────────


@dataclass
class PackBudget:
    """Resource limits for a capability pack."""
    max_cost_usd: float = 5.0
    max_steps: int = 15
    max_iterations_per_step: int = 15


# ── Capability Pack ──────────────────────────────────────────────────────


@dataclass
class CapabilityPack:
    """Declarative unit that bundles everything for a mode of operation."""

    name: str
    description: str
    tools: list[str]                  # tool names or category names
    default_model: str = ""
    fallback_models: list[str] = field(default_factory=list)
    guardrail_profile: Literal["strict", "standard", "permissive"] = "standard"
    budget: PackBudget = field(default_factory=PackBudget)
    ui_panels: list[str] = field(default_factory=list)

    # Environment requirements
    env_required: list[str] = field(default_factory=list)
    env_optional: list[str] = field(default_factory=list)
    deps_required: list[str] = field(default_factory=list)

    # Feature flag that must be true (maps to config bool name)
    feature_flag: str = ""

    def check_readiness(self) -> ReadinessReport:
        """Run all readiness probes for this pack."""
        report = ReadinessReport(pack_name=self.name)

        # Check required env vars
        for var in self.env_required:
            val = os.getenv(var, "")
            if val:
                report.checks.append(ReadinessCheck(
                    name=var, status="ready", message="configured",
                ))
            else:
                report.checks.append(ReadinessCheck(
                    name=var, status="unavailable",
                    message=f"missing — set {var} in .env",
                ))

        # Check optional env vars
        for var in self.env_optional:
            val = os.getenv(var, "")
            if val:
                report.checks.append(ReadinessCheck(
                    name=var, status="ready", message="configured",
                ))
            else:
                report.checks.append(ReadinessCheck(
                    name=var, status="degraded",
                    message=f"not set — some features limited",
                ))

        # Check Python dependencies
        for dep in self.deps_required:
            try:
                import_module(dep)
                report.checks.append(ReadinessCheck(
                    name=dep, status="ready", message="installed",
                ))
            except ImportError:
                report.checks.append(ReadinessCheck(
                    name=dep, status="unavailable",
                    message=f"not installed — pip install {dep}",
                ))

        # Check feature flag
        if self.feature_flag:
            from forge import config
            enabled = getattr(config, self.feature_flag, False)
            if enabled:
                report.checks.append(ReadinessCheck(
                    name=self.feature_flag, status="ready", message="enabled",
                ))
            else:
                report.checks.append(ReadinessCheck(
                    name=self.feature_flag, status="unavailable",
                    message=f"disabled — set FORGE_{self.feature_flag}=true",
                ))

        # Check that at least one provider key exists
        provider_keys = ["XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
        has_any = any(os.getenv(k, "") for k in provider_keys)
        if has_any:
            report.checks.append(ReadinessCheck(
                name="provider", status="ready", message="API key found",
            ))
        else:
            report.checks.append(ReadinessCheck(
                name="provider", status="unavailable",
                message="no API key — set XAI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY",
            ))

        return report

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        readiness = self.check_readiness()
        return {
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
            "default_model": self.default_model,
            "fallback_models": self.fallback_models,
            "guardrail_profile": self.guardrail_profile,
            "budget": {
                "max_cost_usd": self.budget.max_cost_usd,
                "max_steps": self.budget.max_steps,
                "max_iterations_per_step": self.budget.max_iterations_per_step,
            },
            "ui_panels": self.ui_panels,
            "readiness": readiness.to_dict(),
        }


# ── Pack Registry ────────────────────────────────────────────────────────


class PackRegistry:
    """Central registry of all capability packs."""

    def __init__(self):
        self._packs: dict[str, CapabilityPack] = {}

    def register(self, pack: CapabilityPack) -> None:
        self._packs[pack.name] = pack
        log.info("Registered pack: %s (%d tools)", pack.name, len(pack.tools))

    def get(self, name: str) -> CapabilityPack | None:
        return self._packs.get(name)

    def list_packs(self) -> list[CapabilityPack]:
        return list(self._packs.values())

    def list_names(self) -> list[str]:
        return list(self._packs.keys())

    def readiness_all(self) -> dict[str, ReadinessReport]:
        """Check readiness for all packs."""
        return {name: pack.check_readiness() for name, pack in self._packs.items()}

    def to_dict(self) -> list[dict]:
        """Serialize all packs for API."""
        return [pack.to_dict() for pack in self._packs.values()]


# ── Global Registry (loaded once) ────────────────────────────────────────

_registry: PackRegistry | None = None


def get_registry() -> PackRegistry:
    """Get or create the global pack registry with all built-in packs."""
    global _registry
    if _registry is None:
        _registry = PackRegistry()
        _load_builtin_packs(_registry)
    return _registry


def _load_builtin_packs(registry: PackRegistry) -> None:
    """Register all built-in capability packs."""
    from forge.packs.research import RESEARCH_PACK
    from forge.packs.builder import BUILDER_PACK
    from forge.packs.ops import OPS_PACK
    from forge.packs.trading import TRADING_PACK
    from forge.packs.arena import ARENA_PACK
    from forge.packs.email import EMAIL_PACK
    from forge.packs.salesforce import SALESFORCE_PACK
    from forge.packs.blender import BLENDER_PACK

    for pack in [RESEARCH_PACK, BUILDER_PACK, OPS_PACK, TRADING_PACK, ARENA_PACK,
                 EMAIL_PACK, SALESFORCE_PACK, BLENDER_PACK]:
        registry.register(pack)
