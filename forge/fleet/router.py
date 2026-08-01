"""
Smart routing + failover for Fleet Mode (spec §6, P1).

Resolution order for a step's model:
  1. Explicit model (not ``auto`` / empty)
  2. Persona / step default
  3. ``auto`` routing table by ``task_class``

Health is consulted for *every* candidate before dispatch (spec §5).
On unhealthy primary with no fallback → fail closed (no silent dispatch).

On failure: bounded same-model retry → provider fallback chain → mark failed.
Reroutes are always logged with reasons; never silent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from forge.fleet.health import is_provider_healthy
from forge.fleet.registry import ModelRegistry, normalize_provider
from forge.fleet.types import RerouteEvent

log = logging.getLogger("forge.fleet.router")

HealthFn = Callable[[str], tuple[bool, str]]


class NoHealthyProvider(Exception):
    """Raised when the resolved model is unhealthy and no fallback exists."""

    def __init__(self, model: str, reason: str = ""):
        self.model = model
        self.reason = reason
        super().__init__(
            f"No healthy provider for model {model!r}"
            + (f": {reason}" if reason else "")
        )


@dataclass
class RouteDecision:
    model: str
    source: str  # explicit | persona | auto | fallback
    task_class: str = "implement"
    provider: str = ""
    reason: str = ""
    # Populated when resolve had to leave the primary due to health
    from_model: str = ""
    health_rerouted: bool = False


@dataclass
class AutoRouter:
    """Resolves models and walks the provider fallback chain."""

    registry: ModelRegistry
    health_fn: HealthFn = field(default=is_provider_healthy)
    prefer_healthy: bool = True

    def resolve(
        self,
        *,
        model: str | None = None,
        task_class: str = "implement",
        persona_default: str | None = None,
        skip_unhealthy: bool = True,
        fail_closed: bool = True,
    ) -> RouteDecision:
        """Pick the model for a step (explicit > persona > auto).

        After candidate selection, if ``skip_unhealthy`` and the provider is
        unhealthy, walk the fallback chain. If none available and
        ``fail_closed``, raise ``NoHealthyProvider``.
        """
        explicit = (model or "").strip()
        if explicit and explicit.lower() != "auto":
            decision = self._decision(
                explicit, "explicit", task_class, "step model override"
            )
        else:
            persona = (persona_default or "").strip()
            if persona and persona.lower() != "auto":
                decision = self._decision(
                    persona, "persona", task_class, "persona default"
                )
            else:
                auto_model = self.registry.routing.model_for(task_class)
                decision = self._decision(
                    auto_model,
                    "auto",
                    task_class,
                    f"auto routing for task_class={task_class}",
                )

        if not (skip_unhealthy and self.prefer_healthy):
            return decision

        healthy, reason = self._model_healthy(decision.model)
        if healthy:
            return decision

        alt = self.pick_fallback(
            decision.model, tried={decision.model}, task_class=task_class
        )
        if alt:
            log.info(
                "route: %s unhealthy (%s) → %s",
                decision.model,
                reason,
                alt.model,
            )
            return RouteDecision(
                model=alt.model,
                source="fallback",
                task_class=task_class,
                provider=self.registry.provider_of(alt.model),
                reason=f"primary unhealthy: {reason}",
                from_model=decision.model,
                health_rerouted=True,
            )

        if fail_closed:
            raise NoHealthyProvider(decision.model, reason)
        # Soft path (tests / opt-in): still return unhealthy primary
        decision.reason = f"unhealthy (no fallback): {reason}"
        return decision

    def _decision(
        self, model: str, source: str, task_class: str, reason: str
    ) -> RouteDecision:
        return RouteDecision(
            model=model,
            source=source,
            task_class=task_class,
            provider=self.registry.provider_of(model),
            reason=reason,
        )

    def _model_healthy(self, model_id: str) -> tuple[bool, str]:
        provider = normalize_provider(self.registry.provider_of(model_id))
        return self.health_fn(provider)

    def pick_fallback(
        self,
        failed_model: str,
        *,
        tried: set[str] | None = None,
        task_class: str = "implement",
    ) -> RouteDecision | None:
        """Next model from the provider fallback chain, skipping tried/unhealthy.

        Preference within a provider: supports_tools → routing preferred →
        tier match → lower cost_out.
        """
        tried = set(tried or set())
        tried.add(failed_model)
        failed_provider = normalize_provider(
            self.registry.provider_of(failed_model)
        )
        chain = list(self.registry.routing.fallbacks.get(failed_provider, []))
        preferred = self.registry.routing.model_for(task_class)
        preferred_entry = self.registry.get(preferred)
        preferred_tier = preferred_entry.tier if preferred_entry else ""

        for prov in chain:
            prov_key = normalize_provider(prov)
            healthy, reason = self.health_fn(prov_key)
            if not healthy:
                log.info(
                    "skip fallback provider %s (unhealthy: %s)",
                    prov_key,
                    reason,
                )
                continue
            candidates = [
                m
                for m in self.registry.models_for_provider(prov_key)
                if m.id not in tried and m.id != "auto"
            ]
            if not candidates:
                continue

            def _rank(m):
                return (
                    0 if m.supports_tools else 1,
                    0 if m.id == preferred else 1,
                    0 if m.tier == preferred_tier else 1,
                    m.cost_out,
                    m.id,
                )

            ordered = sorted(candidates, key=_rank)
            entry = ordered[0]
            return RouteDecision(
                model=entry.id,
                source="fallback",
                task_class=task_class,
                provider=entry.provider,
                reason=(
                    f"fallback after {failed_model} "
                    f"({failed_provider} → {prov_key})"
                ),
            )
        return None

    def reroute_event(
        self,
        step_id: str,
        from_model: str,
        to_model: str,
        reason: str,
        attempt: int = 0,
        event_type: str = "fallback",
    ) -> RerouteEvent:
        ev = RerouteEvent(
            step_id=step_id,
            from_model=from_model,
            to_model=to_model,
            reason=reason,
            attempt=attempt,
            event_type=event_type,
        )
        log.warning(
            "fleet %s step=%s %s → %s (%s)",
            event_type,
            step_id,
            from_model,
            to_model,
            reason,
        )
        return ev
