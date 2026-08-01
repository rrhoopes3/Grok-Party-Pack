"""
Fleet orchestrator — DAG-parallel multi-model execution (spec §6, §10).

Headless-friendly entrypoint::

    result = FleetOrchestrator(...).run(steps, fleet_config=..., budget=...)

Reuses ``forge.dag.DAGPlan`` semantics for dependency scheduling, adds
per-step model binding, file-scope serialization, retry + failover, and
ledger rollup with budget reservation.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Sequence

from forge.dag import DAGNode, DAGPlan
from forge.fleet.executor import GrokBuildExecutor, GrokBuildResult
from forge.fleet.health import mark_provider_unhealthy
from forge.fleet.ledger import BudgetExceeded, CostLedger
from forge.fleet.registry import ModelRegistry, normalize_provider
from forge.fleet.router import AutoRouter, NoHealthyProvider
from forge.fleet.types import (
    MAX_RETRIES_CAP,
    MAX_STEPS_CAP,
    FleetConfig,
    FleetResult,
    FleetStep,
    StepResult,
)

log = logging.getLogger("forge.fleet.orchestrator")


def steps_to_dag(steps: Sequence[FleetStep], task: str = "") -> DAGPlan:
    """Convert fleet steps into a ``DAGPlan`` (deps only; scopes handled later)."""
    plan = DAGPlan(task=task)
    for s in steps:
        plan.add_node(
            node_id=s.id,
            task=s.task,
            depends_on=list(s.depends_on),
            executor_model=s.model or "",
        )
    return plan


def plan_from_linear(
    steps: Sequence[dict | FleetStep | str],
    task: str = "",
) -> list[FleetStep]:
    """Port of ``forge.dag.plan_from_linear`` — ordered list → sequential chain.

    Accepts dicts ``{id?, task/description/title, model?, task_class?, ...}``,
    ``FleetStep`` instances, or bare task strings.
    """
    out: list[FleetStep] = []
    prev = ""
    for i, raw in enumerate(steps):
        if isinstance(raw, FleetStep):
            sid = raw.id or f"step_{i + 1}"
            fs = FleetStep(
                id=sid,
                task=raw.task,
                depends_on=[prev] if prev else list(raw.depends_on),
                task_class=raw.task_class,
                model=raw.model,
                persona_default=raw.persona_default,
                file_scopes=raw.file_scopes,
                max_retries=raw.max_retries,
            )
        elif isinstance(raw, str):
            sid = f"step_{i + 1}"
            fs = FleetStep(
                id=sid,
                task=raw,
                depends_on=[prev] if prev else [],
                task_class="implement",
                model="auto",
            )
        else:
            sid = str(raw.get("id") or f"step_{i + 1}")
            fs = FleetStep(
                id=sid,
                task=str(
                    raw.get("task")
                    or raw.get("description")
                    or raw.get("title")
                    or f"Step {i + 1}"
                ),
                depends_on=[prev] if prev else list(raw.get("depends_on") or []),
                task_class=str(raw.get("task_class") or "implement"),
                model=raw.get("model"),
                persona_default=raw.get("persona_default"),
                file_scopes=raw.get("file_scopes"),
                max_retries=raw.get("max_retries"),
            )
        out.append(fs)
        prev = fs.id
    return out


def normalize_scope_path(path: str) -> str:
    """Normalize a file-scope path for conflict detection."""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return ""
    # Drop leading ./
    while p.startswith("./"):
        p = p[2:]
    # normpath then re-slash
    p = os.path.normpath(p).replace("\\", "/")
    # Remove trailing slash except root
    if len(p) > 1:
        p = p.rstrip("/")
    if os.name == "nt":
        p = p.casefold()
    return p


def scopes_conflict(a: list[str] | None, b: list[str] | None) -> bool:
    """True if two steps must not run concurrently (spec §9).

    - Either undeclared (None/empty) → conservative conflict
    - Exact match after normalize, or parent/child prefix → conflict
    """
    a_raw = [p for p in (a or []) if p]
    b_raw = [p for p in (b or []) if p]
    if not a_raw or not b_raw:
        return True
    a_set = {normalize_scope_path(p) for p in a_raw}
    b_set = {normalize_scope_path(p) for p in b_raw}
    a_set.discard("")
    b_set.discard("")
    if not a_set or not b_set:
        return True
    for ap in a_set:
        for bp in b_set:
            if ap == bp:
                return True
            # parent/child: src vs src/a.py
            if ap.startswith(bp + "/") or bp.startswith(ap + "/"):
                return True
    return False


def _reachable(deps: dict[str, set[str]], start: str, target: str) -> bool:
    """True if ``target`` is reachable walking deps of ``start`` (ancestors)."""
    stack = list(deps.get(start, ()))
    seen: set[str] = set()
    while stack:
        n = stack.pop()
        if n == target:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(deps.get(n, ()))
    return False


def validate_steps(steps: Sequence[FleetStep]) -> None:
    """Raise ValueError on empty/duplicate ids or missing depends_on targets."""
    if len(steps) > MAX_STEPS_CAP:
        raise ValueError(
            f"fleet plan has {len(steps)} steps; max allowed is {MAX_STEPS_CAP}"
        )
    ids: list[str] = []
    for s in steps:
        if not s.id or not str(s.id).strip():
            raise ValueError("fleet step id must be non-empty")
        ids.append(s.id)
    if len(ids) != len(set(ids)):
        seen: set[str] = set()
        dups = []
        for i in ids:
            if i in seen:
                dups.append(i)
            seen.add(i)
        raise ValueError(f"duplicate fleet step ids: {sorted(set(dups))}")
    id_set = set(ids)
    for s in steps:
        for d in s.depends_on:
            if d not in id_set:
                raise ValueError(
                    f"step {s.id!r} depends_on unknown id {d!r}"
                )


class FleetOrchestrator:
    """Schedule fleet steps across models with ledger + failover."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        ledger: CostLedger | None = None,
        router: AutoRouter | None = None,
        executor: GrokBuildExecutor | None = None,
        config: FleetConfig | None = None,
    ):
        self.config = config or FleetConfig()
        if registry is None:
            registry = ModelRegistry.from_executor_models(
                override_path=self.config.models_path
            )
        self.registry = registry
        self.ledger = ledger or CostLedger(
            registry=self.registry,
            budget=self.config.budget,
            on_exceeded=self.config.on_budget_exceeded,
            unknown_model_floor=self.config.unknown_model_floor_usd,
        )
        if self.ledger._registry is None:  # noqa: SLF001
            self.ledger.set_registry(self.registry)
        self.router = router or AutoRouter(registry=self.registry)
        if executor is None:
            executor = GrokBuildExecutor(
                command=self.config.grok_build_command,
                timeout=self.config.timeout_per_step,
                working_dir=self.config.working_dir,
                registry=self.registry,
            )
        self.executor = executor
        if getattr(self.executor, "registry", None) is None:
            self.executor.registry = self.registry
        self._cancel = threading.Event()
        self._budget_abort = False  # cancel reason: budget vs user
        self._lock = threading.Lock()
        self._reroute_log: list[dict[str, Any]] = []
        # scope soft-edge sources: child_id → set of scope-predecessor ids
        self._scope_preds: dict[str, set[str]] = {}

    # ── public API ────────────────────────────────────────────────────────

    def run(
        self,
        task_or_plan: str | Sequence[FleetStep] | DAGPlan,
        fleet_config: FleetConfig | None = None,
        budget: float | None = None,
        *,
        reset_ledger: bool | None = None,
    ) -> FleetResult:
        """Execute a fleet plan; returns structured statuses + ledger snapshot.

        By default the ledger is cleared at the start of each run so sequential
        runs on a reused orchestrator do not stack costs. Pass
        ``reset_ledger=False`` (or ``FleetConfig.reset_ledger=False``) to
        aggregate across runs intentionally.
        """
        cfg = fleet_config or self.config
        do_reset = cfg.reset_ledger if reset_ledger is None else reset_ledger
        if do_reset:
            self.ledger.clear()

        # Always sync budget + policy from resolved cfg / explicit override
        effective_budget = budget if budget is not None else cfg.budget
        self.ledger.set_budget(effective_budget)
        self.ledger.set_policy(cfg.on_budget_exceeded)
        self.ledger.unknown_model_floor = cfg.unknown_model_floor_usd
        self.ledger.missing_usage_floor = cfg.missing_usage_floor_usd

        try:
            steps = self._normalize_steps(task_or_plan)
            validate_steps(steps)
        except ValueError as exc:
            return FleetResult(
                success=False,
                error=str(exc),
                ledger=self.ledger.snapshot(),
            )

        if not steps:
            return FleetResult(
                success=False,
                error="empty fleet plan",
                ledger=self.ledger.snapshot(),
            )

        plan_id = uuid.uuid4().hex[:12]
        results: dict[str, StepResult] = {
            s.id: StepResult(step_id=s.id, status="pending") for s in steps
        }
        self._reroute_log = []
        self._cancel.clear()
        self._budget_abort = False

        effective_deps, scope_preds = self._effective_dependencies(steps)
        self._scope_preds = scope_preds

        # When budget is set, serialize by default so cheap reservation floors
        # cannot admit N concurrent expensive steps (spec budget integrity).
        dispatch_parallel = max(1, cfg.max_parallel)
        if effective_budget is not None and cfg.serialize_on_budget:
            dispatch_parallel = 1

        log.info(
            "fleet run %s: %d steps max_parallel=%d (dispatch=%d) budget=%s",
            plan_id,
            len(steps),
            cfg.max_parallel,
            dispatch_parallel,
            self.ledger.budget,
        )

        budget_exceeded = False
        fatal_error = ""

        try:
            with ThreadPoolExecutor(max_workers=dispatch_parallel) as pool:
                while not self._cancel.is_set():
                    ready = self._ready_steps(
                        steps, results, effective_deps, scope_preds
                    )
                    if not ready:
                        if all(
                            results[s.id].status
                            in ("completed", "failed", "skipped")
                            for s in steps
                        ):
                            break
                        pending = [
                            s.id
                            for s in steps
                            if results[s.id].status == "pending"
                        ]
                        if pending:
                            fatal_error = f"fleet deadlock; pending={pending}"
                            log.error(fatal_error)
                            for pid in pending:
                                results[pid].status = "failed"
                                results[pid].error = fatal_error
                        break

                    if self.ledger.budget_exceeded:
                        budget_exceeded = True
                        self._mark_budget_stop(results, cfg)
                        break

                    # Build batch with budget reservation
                    batch: list[FleetStep] = []
                    reserve_blocked = False
                    for step in ready:
                        if len(batch) >= dispatch_parallel:
                            break
                        model_hint = self._peek_model(step)
                        remaining = self.ledger.remaining_budget()
                        floor = self.registry.estimate_step_floor(
                            model_hint,
                            unknown_floor=cfg.unknown_model_floor_usd,
                            missing_usage_floor=cfg.missing_usage_floor_usd,
                            estimated_cost_usd=step.estimated_cost_usd,
                            budget=self.ledger.budget,
                            remaining_budget=remaining,
                        )
                        if self.ledger.budget is not None:
                            if remaining is not None and remaining <= 0:
                                budget_exceeded = True
                                break
                            if floor <= 0:
                                if remaining is not None and remaining <= 0:
                                    budget_exceeded = True
                                reserve_blocked = True
                                continue  # try other ready steps
                            if not self.ledger.try_reserve(step.id, floor):
                                rem = self.ledger.remaining_budget()
                                if rem is not None and rem <= 0:
                                    budget_exceeded = True
                                    break
                                # Headroom left (or will free) — try next ready
                                # step; do not permanent-skip this wave yet.
                                reserve_blocked = True
                                continue
                        batch.append(step)

                    if not batch:
                        if budget_exceeded or self.ledger.budget_exceeded:
                            budget_exceeded = True
                            self._mark_budget_stop(results, cfg)
                            break
                        if reserve_blocked:
                            rem = self.ledger.remaining_budget()
                            in_flight = any(
                                results[s.id].status == "running" for s in steps
                            )
                            if rem is not None and rem <= 0:
                                budget_exceeded = True
                                self._mark_budget_stop(results, cfg)
                                break
                            if in_flight:
                                # Leave ready pending; wait for in-flight to
                                # release reservations/headroom, then re-loop.
                                # (No futures this wave — yield via continue.)
                                time.sleep(0.01)
                                continue
                            if rem is not None and rem < cfg.missing_usage_floor_usd:
                                budget_exceeded = True
                                self._mark_budget_stop(results, cfg)
                                break
                            # No in-flight, remaining > dust, still cannot admit:
                            # permanent-skip only as last resort (true stuck).
                            for s in ready:
                                if results[s.id].status == "pending":
                                    results[s.id].status = "skipped"
                                    results[s.id].error = (
                                        "insufficient budget headroom "
                                        "for step reserve"
                                    )
                            break
                        break

                    futures = {}
                    for step in batch:
                        results[step.id].status = "running"
                        fut = pool.submit(self._run_one_step, step, cfg)
                        futures[fut] = step

                    for fut in as_completed(futures):
                        step = futures[fut]
                        try:
                            sr = fut.result()
                        except BudgetExceeded as exc:
                            budget_exceeded = True
                            fatal_error = str(exc)
                            self.ledger.release_reservation(step.id)
                            sr = StepResult(
                                step_id=step.id,
                                status="skipped",
                                error="budget exceeded",
                            )
                            self._trigger_budget_cancel(cfg)
                        except Exception as exc:  # noqa: BLE001
                            self.ledger.release_reservation(step.id)
                            sr = StepResult(
                                step_id=step.id,
                                status="failed",
                                error=f"{type(exc).__name__}: {exc}",
                            )
                        results[step.id] = sr
                        if self.ledger.budget_exceeded:
                            budget_exceeded = True
                            self._trigger_budget_cancel(cfg)

                    if budget_exceeded:
                        self._mark_budget_stop(results, cfg)
                        break
        except BudgetExceeded as exc:
            budget_exceeded = True
            fatal_error = str(exc)
            self._skip_all_nonterminal(results, reason="budget exceeded")

        if budget_exceeded:
            self._skip_all_nonterminal(results, reason="budget exceeded")

        success = (
            not budget_exceeded
            and all(r.status == "completed" for r in results.values())
        )
        return FleetResult(
            success=success,
            steps=results,
            ledger=self.ledger.snapshot(),
            reroute_log=list(self._reroute_log),
            budget_exceeded=budget_exceeded or self.ledger.budget_exceeded,
            error=fatal_error,
            plan_id=plan_id,
        )

    def cancel(self) -> None:
        """User-initiated cancel (in-flight steps surface as failed/cancelled)."""
        self._budget_abort = False
        self._cancel.set()

    def _trigger_budget_cancel(self, cfg: FleetConfig) -> None:
        if cfg.on_budget_exceeded == "abort":
            self._budget_abort = True
            self._cancel.set()

    def _mark_budget_stop(
        self, results: dict[str, StepResult], cfg: FleetConfig
    ) -> None:
        self._skip_all_nonterminal(results, reason="budget exceeded")
        self._trigger_budget_cancel(cfg)

    # ── step execution ────────────────────────────────────────────────────

    def _peek_model(self, step: FleetStep) -> str:
        """Best-effort model id for reservation without health fail-closed."""
        try:
            d = self.router.resolve(
                model=step.model,
                task_class=str(step.task_class or "implement"),
                persona_default=step.persona_default,
                skip_unhealthy=True,
                fail_closed=False,
            )
            return d.model
        except Exception:  # noqa: BLE001
            return step.model or "auto"

    def _cancel_step_result(
        self,
        step: FleetStep,
        model: str,
        models_tried: list[str],
        reroutes: list[dict[str, Any]],
        started: float,
    ) -> StepResult:
        """Terminal result when cancel is set — budget abort vs user cancel."""
        self.ledger.release_reservation(step.id)
        if self._budget_abort or (
            self.ledger.budget is not None and self.ledger.budget_exceeded
        ):
            return StepResult(
                step_id=step.id,
                status="skipped",
                model=model,
                models_tried=models_tried,
                error="budget exceeded",
                reroutes=reroutes,
                cost_usd=self._step_cost(step.id),
                duration_s=round(time.time() - started, 3),
            )
        return StepResult(
            step_id=step.id,
            status="failed",
            model=model,
            models_tried=models_tried,
            error="cancelled",
            reroutes=reroutes,
            cost_usd=self._step_cost(step.id),
            duration_s=round(time.time() - started, 3),
        )

    def _run_one_step(self, step: FleetStep, cfg: FleetConfig) -> StepResult:
        reroutes: list[dict[str, Any]] = []
        models_tried: list[str] = []
        started = time.time()

        try:
            decision = self.router.resolve(
                model=step.model,
                task_class=str(step.task_class or "implement"),
                persona_default=step.persona_default,
                skip_unhealthy=True,
                fail_closed=True,
            )
        except NoHealthyProvider as exc:
            self.ledger.release_reservation(step.id)
            return StepResult(
                step_id=step.id,
                status="failed",
                model=exc.model,
                error=str(exc),
                duration_s=round(time.time() - started, 3),
            )

        if decision.health_rerouted and decision.from_model:
            ev = self.router.reroute_event(
                step.id,
                decision.from_model,
                decision.model,
                decision.reason,
                attempt=0,
                event_type="health",
            )
            reroutes.append(ev.to_dict())
            with self._lock:
                self._reroute_log.append(ev.to_dict())

        model = decision.model
        raw_retries = (
            step.max_retries
            if step.max_retries is not None
            else cfg.max_retries
        )
        max_retries = max(0, min(int(raw_retries), MAX_RETRIES_CAP))
        tried: set[str] = set()
        last_error = ""
        last_result: GrokBuildResult | None = None
        attempt = 0

        while True:
            if self._cancel.is_set():
                return self._cancel_step_result(
                    step, model, models_tried, reroutes, started
                )

            # Budget gate between attempts (do not raise under signal)
            if self.ledger.budget is not None and self.ledger.budget_exceeded:
                self.ledger.release_reservation(step.id)
                return StepResult(
                    step_id=step.id,
                    status="skipped",
                    model=model,
                    models_tried=models_tried,
                    error="budget exceeded",
                    reroutes=reroutes,
                    cost_usd=self._step_cost(step.id),
                    duration_s=round(time.time() - started, 3),
                )

            tried.add(model)
            models_tried.append(model)
            attempt += 1

            same_model_attempts = 0
            while same_model_attempts <= max_retries:
                if self._cancel.is_set():
                    return self._cancel_step_result(
                        step, model, models_tried, reroutes, started
                    )

                if self.ledger.budget is not None and self.ledger.budget_exceeded:
                    break

                entry = self.registry.get(model)
                base_url = entry.base_url if entry else None
                gb = self.executor.run_step(
                    task=step.task,
                    model=model,
                    step_id=step.id,
                    timeout=cfg.timeout_per_step,
                    base_url=base_url,
                )
                last_result = gb

                # Intermediate record: keep reservation until step is terminal
                self._record_usage(step, model, gb, cfg, settle=False)

                if gb.ok:
                    self.ledger.release_reservation(step.id)
                    return StepResult(
                        step_id=step.id,
                        status="completed",
                        model=model,
                        models_tried=models_tried,
                        exit_code=gb.exit_code,
                        output=gb.stdout,
                        error="",
                        cost_usd=self._step_cost(step.id),
                        input_tokens=gb.input_tokens,
                        output_tokens=gb.output_tokens,
                        reroutes=reroutes,
                        duration_s=round(time.time() - started, 3),
                    )

                last_error = gb.error or gb.stderr or f"exit {gb.exit_code}"
                same_model_attempts += 1
                if same_model_attempts <= max_retries:
                    if self.ledger.budget is not None and self.ledger.budget_exceeded:
                        break
                    ev = self.router.reroute_event(
                        step.id,
                        model,
                        model,
                        f"retry same model after failure: {last_error[:120]}",
                        attempt=same_model_attempts,
                        event_type="retry",
                    )
                    reroutes.append(ev.to_dict())
                    with self._lock:
                        self._reroute_log.append(ev.to_dict())

            if self.ledger.budget is not None and self.ledger.budget_exceeded:
                self.ledger.release_reservation(step.id)
                return StepResult(
                    step_id=step.id,
                    status="skipped",
                    model=model,
                    models_tried=models_tried,
                    error="budget exceeded",
                    reroutes=reroutes,
                    cost_usd=self._step_cost(step.id),
                    duration_s=round(time.time() - started, 3),
                )

            if self._looks_like_provider_failure(last_error, last_result):
                prov = normalize_provider(self.registry.provider_of(model))
                mark_provider_unhealthy(prov, last_error)

            fb = self.router.pick_fallback(
                model,
                tried=tried,
                task_class=str(step.task_class or "implement"),
            )
            if fb is None:
                break

            ev = self.router.reroute_event(
                step.id,
                model,
                fb.model,
                fb.reason or last_error[:160],
                attempt=attempt,
                event_type="fallback",
            )
            reroutes.append(ev.to_dict())
            with self._lock:
                self._reroute_log.append(ev.to_dict())
            model = fb.model

        self.ledger.release_reservation(step.id)
        return StepResult(
            step_id=step.id,
            status="failed",
            model=model,
            models_tried=models_tried,
            exit_code=last_result.exit_code if last_result else None,
            output=last_result.stdout if last_result else "",
            error=last_error or "step failed with no fallback",
            cost_usd=self._step_cost(step.id),
            input_tokens=last_result.input_tokens if last_result else 0,
            output_tokens=last_result.output_tokens if last_result else 0,
            reroutes=reroutes,
            duration_s=round(time.time() - started, 3),
        )

    def _record_usage(
        self,
        step: FleetStep,
        model: str,
        gb: GrokBuildResult,
        cfg: FleetConfig,
        *,
        settle: bool = False,
    ) -> None:
        """Record tokens/cost. Successful steps always get a ledger row.

        When model is registered and tokens are known, charge is at least
        registry token price (reported cost_usd cannot undercut pricing).

        When budget is set and usage is missing after success, apply the
        configured floor. Intermediate attempts use settle=False so the
        reservation is held until the step is terminal.
        """
        cost = gb.cost_usd
        in_t = gb.input_tokens
        out_t = gb.output_tokens
        meta: dict[str, Any] = {
            "exit_code": gb.exit_code,
            "timed_out": gb.timed_out,
        }

        unk_floor = (
            cfg.unknown_model_floor_usd
            if self.ledger.budget is not None
            else None
        )
        registry_cost = self.registry.calculate_cost(
            model, in_t, out_t, unknown_floor=unk_floor
        )
        miss_floor = cfg.missing_usage_floor_usd

        if in_t == 0 and out_t == 0:
            # Zero-token path: under budget always charge at least missing-usage
            # floor (even when agent reports cost_usd=0 / tiny).
            reported = 0.0 if cost is None else float(cost)
            if self.ledger.budget is not None:
                cost = max(reported, miss_floor)
                if cost > reported:
                    meta["usage_estimated"] = True
            elif cost is None:
                cost = 0.0
        elif cost is None:
            cost = registry_cost
        elif model in self.registry and (in_t > 0 or out_t > 0):
            # Never undercut registry-priced tokens
            cost = max(float(cost), registry_cost)
            if cost > float(gb.cost_usd or 0):
                meta["cost_clamped_to_registry"] = True

        self.ledger.record(
            agent_id=f"fleet:{step.id}",
            step_id=step.id,
            model=model,
            input_tokens=in_t,
            output_tokens=out_t,
            cost_usd=cost,
            meta=meta,
            raise_on_exceed=False,
            settle=settle,
        )

    def _step_cost(self, step_id: str) -> float:
        snap = self.ledger.snapshot()
        return float(snap.get("per_step", {}).get(step_id, {}).get("cost_usd", 0.0))

    @staticmethod
    def _looks_like_provider_failure(
        error: str, result: GrokBuildResult | None
    ) -> bool:
        text = (error or "").lower()
        markers = (
            "502",
            "503",
            "504",
            "429",
            "timeout",
            "connection",
            "provider",
            "rate limit",
            "overloaded",
            "unavailable",
        )
        if any(m in text for m in markers):
            return True
        if result and result.timed_out:
            return True
        return False

    @staticmethod
    def _skip_all_nonterminal(
        results: dict[str, StepResult], reason: str
    ) -> None:
        for sr in results.values():
            if sr.status in ("pending", "running"):
                # running results may still be overwritten by future.result
                if sr.status == "pending":
                    sr.status = "skipped"
                    sr.error = reason

    # ── scheduling helpers ────────────────────────────────────────────────

    def _normalize_steps(
        self, task_or_plan: str | Sequence[FleetStep] | DAGPlan
    ) -> list[FleetStep]:
        if isinstance(task_or_plan, str):
            return [
                FleetStep(
                    id="step_1",
                    task=task_or_plan,
                    task_class="implement",
                    model="auto",
                )
            ]
        if isinstance(task_or_plan, DAGPlan):
            out: list[FleetStep] = []
            for nid in task_or_plan.topological_order():
                node: DAGNode = task_or_plan.nodes[nid]
                out.append(
                    FleetStep(
                        id=node.id,
                        task=node.task,
                        depends_on=list(node.depends_on),
                        model=node.executor_model or "auto",
                        task_class="implement",
                    )
                )
            return out
        return list(task_or_plan)

    def _effective_dependencies(
        self, steps: Sequence[FleetStep]
    ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """Explicit deps + cycle-safe file-scope serialization edges.

        Returns (all_deps, scope_only_preds).
        """
        deps: dict[str, set[str]] = {s.id: set(s.depends_on) for s in steps}
        scope_preds: dict[str, set[str]] = {s.id: set() for s in steps}
        ordered = list(steps)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if not scopes_conflict(a.file_scopes, b.file_scopes):
                    continue
                # If b already reaches a (or a reaches b) via deps, skip edge
                if _reachable(deps, b.id, a.id):
                    # b already waits on a transitively
                    continue
                if _reachable(deps, a.id, b.id):
                    # a waits on b — adding b→a would cycle; invert not needed
                    # because a already serializes after b
                    continue
                # Serialize: b waits on a (stable by input order)
                deps[b.id].add(a.id)
                scope_preds[b.id].add(a.id)
                log.debug(
                    "file-scope serialize: %s waits on %s", b.id, a.id
                )
        return deps, scope_preds

    def _ready_steps(
        self,
        steps: Sequence[FleetStep],
        results: dict[str, StepResult],
        effective_deps: dict[str, set[str]],
        scope_preds: dict[str, set[str]],
    ) -> list[FleetStep]:
        ready: list[FleetStep] = []
        for s in steps:
            if results[s.id].status != "pending":
                continue
            deps = effective_deps.get(s.id, set())
            ok = True
            for d in deps:
                st = results.get(d)
                if st is None:
                    ok = False
                    break
                if st.status == "completed":
                    continue
                # skipped / failed never satisfy
                is_scope = d in scope_preds.get(s.id, set())
                if st.status == "failed":
                    results[s.id].status = "failed"
                    results[s.id].error = (
                        f"file-scope predecessor {d} failed"
                        if is_scope
                        else f"dependency {d} failed"
                    )
                    ok = False
                    break
                if st.status == "skipped":
                    results[s.id].status = "skipped"
                    results[s.id].error = (
                        f"file-scope predecessor {d} skipped"
                        if is_scope
                        else f"dependency {d} skipped"
                    )
                    ok = False
                    break
                # still running/pending
                ok = False
                break
            if ok:
                ready.append(s)

        selected: list[FleetStep] = []
        for s in ready:
            if any(
                scopes_conflict(s.file_scopes, o.file_scopes) for o in selected
            ):
                continue
            selected.append(s)
        return selected


def plan_from_fleet_steps(steps: Sequence[FleetStep], task: str = "") -> DAGPlan:
    """Public helper: fleet steps → DAGPlan (for inspection / forge.dag reuse)."""
    return steps_to_dag(steps, task=task)
