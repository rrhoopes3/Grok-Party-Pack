"""
Fleet cost ledger — per-agent / per-step token + USD accounting (spec §3, P0).

Generalizes the chess-arena per-side meter: live rollups, per-model breakdown,
budget caps, and single-flight reservations to limit parallel overshoot.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from forge.fleet.registry import ModelRegistry, clamp_nonneg_finite, clamp_tokens

log = logging.getLogger("forge.fleet.ledger")


class BudgetExceeded(Exception):
    """Raised when a ledger budget cap is hit and policy is ``abort``."""

    def __init__(self, total_cost: float, budget: float, message: str = ""):
        self.total_cost = total_cost
        self.budget = budget
        super().__init__(
            message
            or f"Fleet budget exceeded: ${total_cost:.6f} > ${budget:.6f}"
        )


@dataclass
class UsageRecord:
    agent_id: str
    step_id: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)


class CostLedger:
    """Thread-safe running tally of fleet token spend + reservations."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        budget: float | None = None,
        on_exceeded: str = "abort",  # "abort" | "signal"
        unknown_model_floor: float = 0.05,
        missing_usage_floor: float = 0.01,
    ):
        self._registry = registry
        self.budget = budget
        self.on_exceeded = on_exceeded
        self.unknown_model_floor = unknown_model_floor
        self.missing_usage_floor = clamp_nonneg_finite(missing_usage_floor, default=0.01)
        self._records: list[UsageRecord] = []
        self._lock = threading.RLock()
        self._budget_signaled = False
        # step_id → reserved USD (released/settled on record or release)
        self._reservations: dict[str, float] = {}

    def set_registry(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def set_budget(self, budget: float | None) -> None:
        with self._lock:
            if budget is not None:
                budget = clamp_nonneg_finite(budget, default=0.0)
            self.budget = budget
            self._budget_signaled = False

    def set_policy(self, on_exceeded: str) -> None:
        with self._lock:
            self.on_exceeded = on_exceeded if on_exceeded in ("abort", "signal") else "abort"

    # ── reservations (parallel budget guard) ──────────────────────────────

    def committed_cost(self) -> float:
        """Recorded spend + outstanding reservation headroom.

        For steps that already have records, only the remaining reservation
        (max(0, reserved - step_recorded)) is counted so intermediate
        settle=False records don't double-count.
        """
        with self._lock:
            recorded = sum(r.cost_usd for r in self._records)
            per_step = 0.0
            # accumulate per-step recorded
            by_step: dict[str, float] = {}
            for r in self._records:
                by_step[r.step_id] = by_step.get(r.step_id, 0.0) + r.cost_usd
            for sid, amt in self._reservations.items():
                already = by_step.get(sid, 0.0)
                per_step += max(0.0, amt - already)
            return recorded + per_step

    def remaining_budget(self) -> float | None:
        with self._lock:
            if self.budget is None:
                return None
            return max(0.0, self.budget - self.committed_cost())

    def try_reserve(self, step_id: str, amount: float) -> bool:
        """Atomically reserve ``amount`` against remaining budget.

        Uses the same headroom semantics as ``committed_cost()`` (does **not**
        double-count settle=False step records plus a full reservation).
        Prior reservation for ``step_id`` is dropped before the check so a
        re-reserve replaces rather than stacks.

        Returns False if reservation would exceed (caller should not dispatch).
        When no budget is set, always succeeds with a zero reservation.
        """
        amount = clamp_nonneg_finite(amount)
        with self._lock:
            if self.budget is None:
                self._reservations[step_id] = 0.0
                return True
            # Replace prior reservation for same step if any
            prev = self._reservations.pop(step_id, 0.0)
            # RLock-reentrant: committed_cost excludes this step's reservation
            # (just popped) and only counts max(0, reserved - step_recorded)
            # for other in-flight steps.
            committed = self.committed_cost()
            if committed + amount > self.budget + 1e-12:
                if prev:
                    self._reservations[step_id] = prev
                return False
            self._reservations[step_id] = amount
            return True

    def release_reservation(self, step_id: str) -> None:
        with self._lock:
            self._reservations.pop(step_id, None)

    # ── recording ─────────────────────────────────────────────────────────

    def record(
        self,
        *,
        agent_id: str,
        step_id: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
        meta: dict[str, Any] | None = None,
        raise_on_exceed: bool = True,
        settle: bool = True,
    ) -> UsageRecord:
        """Append a usage row and enforce budget policy.

        Costs and tokens are clamped to finite non-negative values.
        If ``cost_usd`` is None, price via the registry; unknown models use
        ``unknown_model_floor`` when a budget is active.

        ``settle``: when True (default), drop the step's reservation after
        recording (terminal attempt). Pass settle=False for intermediate
        retries so siblings cannot reclaim budget mid-step.
        """
        in_t = clamp_tokens(input_tokens)
        out_t = clamp_tokens(output_tokens)

        unk_floor = self.unknown_model_floor if self.budget is not None else None
        registry_cost: float | None = None
        if self._registry is not None:
            registry_cost = self._registry.calculate_cost(
                model, in_t, out_t, unknown_floor=unk_floor
            )

        if cost_usd is not None:
            cost_usd = clamp_nonneg_finite(cost_usd)
            if not math.isfinite(float(cost_usd)):
                cost_usd = 0.0
            # Never allow reported cost to undercut positive registry pricing
            # when tokens are known (budget-bypass via fake cost_usd=0.001).
            if (
                registry_cost is not None
                and self._registry is not None
                and model in self._registry
                and (in_t > 0 or out_t > 0)
            ):
                cost_usd = max(cost_usd, registry_cost)
            # Zero-token + explicit cost (incl. 0) under budget → missing-usage floor
            elif self.budget is not None and in_t == 0 and out_t == 0:
                cost_usd = max(cost_usd, self.missing_usage_floor)
        else:
            if in_t == 0 and out_t == 0 and self.budget is not None:
                cost_usd = self.missing_usage_floor
            elif registry_cost is not None:
                cost_usd = registry_cost
            else:
                cost_usd = clamp_nonneg_finite(unk_floor or 0.0)

        rec = UsageRecord(
            agent_id=agent_id,
            step_id=step_id,
            model=model,
            input_tokens=in_t,
            output_tokens=out_t,
            cost_usd=float(cost_usd),
            meta=dict(meta or {}),
        )
        with self._lock:
            if settle:
                self._reservations.pop(step_id, None)
            self._records.append(rec)
            total = sum(r.cost_usd for r in self._records)
            log.debug(
                "ledger +$%.6f model=%s step=%s total=$%.6f settle=%s",
                rec.cost_usd,
                model,
                step_id,
                total,
                settle,
            )
            if raise_on_exceed:
                self._enforce_budget(total)
            elif self.budget is not None and total > self.budget:
                self._budget_signaled = True
        return rec

    def _enforce_budget(self, total: float) -> None:
        if self.budget is None:
            return
        if total <= self.budget:
            return
        self._budget_signaled = True
        if self.on_exceeded == "signal":
            log.warning(
                "Fleet budget signal: $%.6f > $%.6f", total, self.budget
            )
            return
        raise BudgetExceeded(total, self.budget)

    # ── queries ───────────────────────────────────────────────────────────

    @property
    def budget_exceeded(self) -> bool:
        with self._lock:
            if self._budget_signaled:
                return True
            if self.budget is None:
                return False
            return sum(r.cost_usd for r in self._records) > self.budget

    @property
    def total_cost(self) -> float:
        with self._lock:
            return sum(r.cost_usd for r in self._records)

    @property
    def total_input_tokens(self) -> int:
        with self._lock:
            return sum(r.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        with self._lock:
            return sum(r.output_tokens for r in self._records)

    def would_exceed(self, additional_cost: float) -> bool:
        with self._lock:
            if self.budget is None:
                return False
            return (self.committed_cost() + clamp_nonneg_finite(additional_cost)) > self.budget

    def check_budget(self) -> None:
        with self._lock:
            if self.budget is None:
                return
            total = sum(r.cost_usd for r in self._records)
            if total > self.budget and self.on_exceeded == "abort":
                raise BudgetExceeded(total, self.budget)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            per_model: dict[str, dict[str, Any]] = {}
            per_agent: dict[str, dict[str, Any]] = {}
            per_step: dict[str, dict[str, Any]] = {}

            def _acc(bucket: dict, key: str, rec: UsageRecord) -> None:
                slot = bucket.setdefault(
                    key,
                    {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": 0.0,
                        "calls": 0,
                    },
                )
                slot["input_tokens"] += rec.input_tokens
                slot["output_tokens"] += rec.output_tokens
                slot["cost_usd"] = round(slot["cost_usd"] + rec.cost_usd, 8)
                slot["calls"] += 1

            for rec in self._records:
                _acc(per_model, rec.model, rec)
                _acc(per_agent, rec.agent_id or "unknown", rec)
                _acc(per_step, rec.step_id or "unknown", rec)

            total_cost = sum(r.cost_usd for r in self._records)
            reserved = sum(self._reservations.values())
            return {
                "total_cost_usd": round(total_cost, 8),
                "reserved_usd": round(reserved, 8),
                "committed_usd": round(total_cost + reserved, 8),
                "total_input_tokens": sum(r.input_tokens for r in self._records),
                "total_output_tokens": sum(r.output_tokens for r in self._records),
                "call_count": len(self._records),
                "budget": self.budget,
                "budget_exceeded": (
                    self._budget_signaled
                    or (self.budget is not None and total_cost > self.budget)
                ),
                "remaining_budget": (
                    None
                    if self.budget is None
                    else round(max(0.0, self.budget - total_cost - reserved), 8)
                ),
                "per_model": per_model,
                "per_agent": per_agent,
                "per_step": per_step,
            }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._reservations.clear()
            self._budget_signaled = False
