"""
Core orchestrator — wires the planner and executor together.

Flow: User Task → Plan (multi-agent) → Parse Steps → Execute Each Step (single agent + tools) → Result
Direct Mode: User Task → Execute directly (single agent + tools) → Result

Enhanced with OpenDev-inspired context engineering:
  - Lazy tool discovery (only inject tools needed per step)
  - Adaptive context compaction (summarize old step outputs)
  - Session memory (learn from previous tasks)
  - Instruction reminders (prevent goal drift)
  - Auto model routing (select model by task complexity)

Cross-pollinated with "Intelligent AI Delegation" (arXiv:2602.11865):
  - Delegation contracts (formal specs per step with verification criteria)
  - Trust calibration (EMA-based trust scoring across delegation outcomes)
  - Accountability chains (transitive custody tracking across hops)
  - Adaptive reassignment (fallback to alternate models on failure)
  - Dynamic assessment (real-time delegatee performance monitoring)

Cross-pollinated with "AgentOS" (arXiv:2603.08938):
  - Agent Kernel (LLM resource scheduling: token budgets, rate limits)
  - Semantic Firewall (pre-execution safety layer for tool calls)
  - Knowledge Graph (graph-based session memory with entity relationships)
"""
from __future__ import annotations
import logging
import threading
import time
import uuid
from typing import Generator

from forge.config import (
    XAI_API_KEY, COST_LIMIT_PER_TASK,
    SIGNALS_ENABLED, JUDGE_ENABLED, JUDGE_MODEL, DIRECTIVES_ENABLED,
)
from forge.models import PlanStep, StepResult, TaskResult
from forge.tools import create_registry
from forge.tools.registry import resolve_tools_for_step
from forge.providers import detect_provider
from forge.packs import get_registry as get_pack_registry, CapabilityPack
from forge.guardrails import GuardrailEngine
from forge.context_engine import (
    compact_context, recall_relevant, remember_task,
    extract_key_paths, auto_select_model, KnowledgeGraph,
)
from forge.delegation import (
    TrustLedger, AccountabilityChain, AdaptiveRouter,
    DelegationAssessor, build_step_contract,
)
from forge.kernel import AgentKernel
from forge.firewall import SemanticFirewall
from forge.vault import AgentVault
from forge import planner, executor
from forge.config import TOLL_ENABLED, TOLL_DB_PATH

log = logging.getLogger("forge.orchestrator")


class Orchestrator:
    def __init__(
        self,
        sandbox_path: str = "",
        direct_mode: bool = False,
        agent_count: int = 16,
        cancel_event: threading.Event | None = None,
        executor_model: str = "",
        task_id: str = "",
        toll_sender: str = "",
        guardrails_enabled: bool = True,
        pack: str = "",
    ):
        # ── Capability Pack (if specified) ────────────────────────────────
        self._pack: CapabilityPack | None = None
        if pack:
            pack_obj = get_pack_registry().get(pack)
            if pack_obj:
                self._pack = pack_obj
                # Pack overrides: model, guardrails
                if not executor_model and pack_obj.default_model:
                    executor_model = pack_obj.default_model
                if pack_obj.guardrail_profile == "strict":
                    guardrails_enabled = True
                elif pack_obj.guardrail_profile == "permissive":
                    guardrails_enabled = False
                log.info("Pack '%s' active: model=%s, guardrails=%s, budget=$%.2f/%d steps",
                         pack_obj.name, executor_model, pack_obj.guardrail_profile,
                         pack_obj.budget.max_cost_usd, pack_obj.budget.max_steps)
            else:
                log.warning("Unknown pack '%s' — ignoring", pack)

        self._client = None  # xAI Client created lazily — only when needed
        self._toll_sender = toll_sender  # external agent ID for billing
        self._toll_relay = None
        if TOLL_ENABLED:
            from forge.toll import TollRelay, Ledger, RateEngine
            ledger = Ledger(TOLL_DB_PATH)
            self._toll_relay = TollRelay(ledger, RateEngine())
            log.info("Toll relay enabled")
        self.registry = create_registry()
        self.guardrail_engine = GuardrailEngine(enabled=guardrails_enabled)
        self.sandbox_path = sandbox_path
        self.direct_mode = direct_mode
        self.agent_count = max(4, min(16, agent_count))  # clamp 4-16
        self.cancel_event = cancel_event or threading.Event()
        self.executor_model = executor_model  # empty string = use default from config
        self.task_id = task_id  # use caller-provided ID if given

        # ── Delegation framework (arXiv:2602.11865) ──────────────────────
        self._trust = TrustLedger()
        self._router = AdaptiveRouter(self._trust)

        # ── AgentOS kernel + firewall (arXiv:2603.08938) ─────────────────
        self._kernel = AgentKernel()
        self._firewall = SemanticFirewall(block_danger=True)
        self._knowledge_graph = KnowledgeGraph()
        self._vault_cache: dict[str, AgentVault] = {}

        log.info("Forge initialized. Tools: %s | Sandbox: %s | Direct: %s | Agents: %d | Model: %s | Guardrails: %s",
                 self.registry.list_tools(), sandbox_path or "OFF", direct_mode, self.agent_count,
                 executor_model or "default", guardrails_enabled)

    def _resolve_model(self, task: str) -> str:
        """Resolve the executor model — supports 'auto' routing."""
        if self.executor_model == "auto":
            model = auto_select_model(task, trust_ledger=self._trust)
            log.info("Auto-routed to model: %s", model)
            return model
        return self.executor_model

    def _get_vault(self, agent_id: str) -> AgentVault:
        """Get or create a persistent memory vault for an agent."""
        if agent_id not in self._vault_cache:
            self._vault_cache[agent_id] = AgentVault(agent_id)
        return self._vault_cache[agent_id]

    @property
    def client(self):
        """Lazy xAI Client — only created when actually needed (xAI model selected)."""
        if self._client is None:
            from xai_sdk import Client
            from forge.config import PUBLIC_MODE
            key = XAI_API_KEY
            if PUBLIC_MODE:
                from forge.public_mode import get_request_key
                byok = get_request_key("xai")
                if byok:
                    key = byok
            self._client = Client(api_key=key)
            # In public mode, don't cache — each task gets its own Orchestrator instance anyway
        return self._client

    def _needs_xai_client(self, model: str = "") -> bool:
        """Check if the given model (or default executor model) requires the xAI client."""
        from forge.config import EXECUTOR_MODEL
        effective_model = model or self.executor_model or EXECUTOR_MODEL
        return detect_provider(effective_model) == "xai"

    def run(self, task: str) -> Generator[dict, None, TaskResult]:
        """
        Run a full task through the plan → execute pipeline (or direct mode).

        Yields SSE-style dicts for real-time UI updates.
        Returns the final TaskResult.
        """
        task_id = self.task_id or str(uuid.uuid4())[:8]
        yield {"type": "status", "content": f"Forge task {task_id} started"}

        if self.direct_mode:
            return (yield from self._run_direct(task_id, task))
        else:
            return (yield from self._run_planned(task_id, task))

    def _run_direct(self, task_id: str, task: str) -> Generator[dict, None, TaskResult]:
        """Skip planner — send task straight to executor as a single step."""
        resolved_model = self._resolve_model(task)

        # Trust-aware routing: check if primary model should be swapped
        routed_model = self._router.select_model(resolved_model) if resolved_model else resolved_model

        # Kernel: allocate token budget for task
        self._kernel.tokens.allocate_task(task_id)

        # Session memory + knowledge graph: recall relevant learnings
        memory_context = recall_relevant(task)
        graph_context = self._knowledge_graph.recall_graph_context(task)
        context = ""
        if memory_context:
            context += memory_context
        if graph_context:
            context += "\n" + graph_context

        # Vault recall: persistent knowledge from previous sessions (Ars Contexta)
        vault = self._get_vault(routed_model or resolved_model or "default")
        vault_context = vault.recall_vault_context(task)
        if vault_context:
            context += "\n" + vault_context
            log.info("Injected vault context into direct executor (%d chars)", len(vault_context))

        if routed_model != resolved_model:
            yield {"type": "status", "phase": "executing",
                   "content": f"Direct mode — trust-routed from {resolved_model} to {routed_model}"}
        elif resolved_model != self.executor_model and self.executor_model == "auto":
            yield {"type": "status", "phase": "executing",
                   "content": f"Direct mode — auto-routed to {routed_model}"}
        else:
            yield {"type": "status", "phase": "executing", "content": "Direct mode — executing with tools..."}

        effective_model = routed_model or resolved_model
        step_start = time.time()
        step_output = ""
        tools_used = []
        error = None

        # ── Signal Extraction (arXiv:2603.10165) ──────────────────────────
        signal_extractor = None
        if SIGNALS_ENABLED:
            from forge.signals import SignalExtractor
            signal_extractor = SignalExtractor(expected_latency=60.0, step_number=1)

        # Only create xAI client if the executor model needs it
        xai_client = self.client if self._needs_xai_client(effective_model) else None

        # Pack-based tool filtering for direct mode
        pack_tool_filter = None
        if self._pack:
            pack_tool_filter = resolve_tools_for_step(self._pack.tools)

        # Public mode: restrict to safe tools only
        from forge.config import PUBLIC_MODE
        if PUBLIC_MODE:
            from forge.public_mode import SAFE_TOOLS
            if pack_tool_filter:
                pack_tool_filter = pack_tool_filter & SAFE_TOOLS
            else:
                pack_tool_filter = set(SAFE_TOOLS)

        gen = executor.execute_step(
            client=xai_client,
            registry=self.registry,
            step_title="Direct execution",
            step_description=task,
            context=context,
            sandbox_path=self.sandbox_path,
            cancel_event=self.cancel_event,
            model=effective_model,
            task_goal=task,
            guardrail_engine=self.guardrail_engine,
            tool_filter=pack_tool_filter,
        )
        if self._toll_relay:
            gen = self._toll_relay.meter(gen, sender=self._toll_sender or "orchestrator",
                                        receiver="executor_direct", session_id=task_id)

        escalation = None
        try:
            while True:
                msg = next(gen)
                if signal_extractor:
                    signal_extractor.observe(msg)
                yield msg
                if msg.get("type") == "content":
                    step_output += msg["content"]
                elif msg.get("type") == "tool_call":
                    tools_used.append(msg["name"])
                elif msg.get("type") == "error":
                    error = msg["content"]
                elif msg.get("type") == "cancelled":
                    error = "Cancelled"
                    break
                elif msg.get("type") == "escalation":
                    escalation = msg
                    break
        except StopIteration as e:
            if e.value:
                step_output = e.value

        cancelled = self.cancel_event.is_set()
        escalated = escalation is not None
        status = "cancelled" if cancelled else ("escalated" if escalated else ("failed" if error else "success"))
        latency = time.time() - step_start

        # ── Signal Finalization (arXiv:2603.10165) ────────────────────────
        step_signals = signal_extractor.finalize(latency) if signal_extractor else None

        # Record trust outcome
        if effective_model:
            self._trust.record_outcome(
                effective_model,
                success=(status == "success"),
                latency_seconds=latency,
                quality_signal=step_signals.aggregate_score if step_signals else None,
            )

        result = StepResult(
            step_number=1,
            status=status,
            output=step_output[:2000],
            tools_used=list(set(tools_used)),
            error=error,
            delegatee_model=effective_model,
            trust_score_after=self._trust.get_trust(effective_model) if effective_model else None,
            latency_seconds=round(latency, 2),
        )

        # Session memory + knowledge graph: remember what we learned
        if status == "success":
            key_paths = extract_key_paths([step_output])
            remember_task(task, tools_used, key_paths, step_output[:300])
            self._knowledge_graph.record_task_knowledge(
                task, tools_used, key_paths, step_output[:300],
            )
            vault.process_6rs(
                task=task, tools_used=tools_used, key_paths=key_paths,
                outcome=step_output[:300], success=True,
                latency_seconds=latency, step_count=1,
            )

        # Guardrail summary
        if self.guardrail_engine.violation_count > 0:
            yield {"type": "guardrail_summary", **self.guardrail_engine.summary()}

        summary = ("Task cancelled" if cancelled
                   else "Escalated to human" if escalated
                   else "Direct execution complete" if not error
                   else "Direct execution failed")
        yield {"type": "done", "summary": summary,
               "firewall": self._firewall.stats(),
               "kernel": self._kernel.summary()}

        return TaskResult(
            task_id=task_id,
            task=task,
            plan_raw="(direct mode — no plan)",
            results=[result],
            final_summary=summary,
        )

    def _run_planned(self, task_id: str, task: str) -> Generator[dict, None, TaskResult]:
        """Full pipeline: multi-agent planner → executor.

        Enhanced with:
        - Session memory injection into planner context
        - Lazy tool discovery per step (only inject tools the planner says are needed)
        - Adaptive context compaction (summarize older steps as context grows)
        - Instruction reminders (pass task goal to executor for drift prevention)
        - Auto model routing (resolve model before execution)

        Cross-pollinated (arXiv:2602.11865):
        - Delegation contracts per step with verification criteria
        - Accountability chain tracking across all hops
        - Dynamic assessment with real-time performance signals
        - Adaptive reassignment: failed steps retried on fallback models
        - Trust calibration updated per step outcome
        """
        resolved_model = self._resolve_model(task)

        # ── Kernel: allocate resources (arXiv:2603.08938) ──────────────
        self._kernel.tokens.allocate_task(task_id)

        # ── Accountability Chain (paper §3.3) ────────────────────────────
        chain = AccountabilityChain(task_id, task)

        # ── Background PRM Judge (arXiv:2603.10165) ──────────────────────
        background_judge = None
        if JUDGE_ENABLED:
            from forge.judge import BackgroundJudge
            background_judge = BackgroundJudge(model=JUDGE_MODEL)

        # ── Phase 1: Plan (always xAI — multi-agent Pantheon) ────────────
        # Inject session memory + knowledge graph into planner context
        memory_hint = recall_relevant(task)
        graph_hint = self._knowledge_graph.recall_graph_context(task)
        enriched_task = task
        if memory_hint:
            enriched_task = f"{task}\n\n{memory_hint}"
            log.info("Injected session memory into planner (%d chars)", len(memory_hint))
        if graph_hint:
            enriched_task = f"{enriched_task}\n\n{graph_hint}"
            log.info("Injected knowledge graph into planner (%d chars)", len(graph_hint))

        # Vault recall: persistent knowledge from previous sessions (Ars Contexta)
        vault = self._get_vault(resolved_model or "default")
        vault_hint = vault.recall_vault_context(task)
        if vault_hint:
            enriched_task = f"{enriched_task}\n\n{vault_hint}"
            log.info("Injected vault context into planner (%d chars)", len(vault_hint))

        if resolved_model != self.executor_model and self.executor_model == "auto":
            yield {"type": "status", "phase": "planning",
                   "content": f"Auto-routed executor to {resolved_model}. Launching {self.agent_count}-agent planner..."}
        else:
            yield {"type": "status", "phase": "planning",
                   "content": f"Launching {self.agent_count}-agent planner..."}

        # Record planner delegation in accountability chain
        planner_hop = chain.add_hop("orchestrator", "planner", "planner_delegation")

        plan_raw = ""
        steps: list[PlanStep] = []

        gen = planner.plan(self.client, enriched_task, agent_count=self.agent_count, cancel_event=self.cancel_event)
        if self._toll_relay:
            gen = self._toll_relay.meter(gen, sender=self._toll_sender or "orchestrator",
                                        receiver="planner", session_id=task_id)
        try:
            while True:
                msg = next(gen)
                yield msg
                if msg.get("type") == "cancelled":
                    chain.complete_hop(planner_hop.hop_number, error="Cancelled")
                    yield {"type": "done", "summary": "Task cancelled"}
                    return TaskResult(task_id=task_id, task=task, plan_raw=plan_raw,
                                     final_summary="Task cancelled",
                                     accountability_chain=chain.summary())
        except StopIteration as e:
            if e.value:
                plan_raw, steps = e.value

        if not steps:
            chain.complete_hop(planner_hop.hop_number, error="No steps returned")
            yield {"type": "error", "content": "Planner returned no steps"}
            return TaskResult(task_id=task_id, task=task, plan_raw=plan_raw,
                              accountability_chain=chain.summary())

        chain.complete_hop(planner_hop.hop_number, output_summary=f"{len(steps)} steps planned")

        yield {"type": "status", "phase": "executing", "content": f"Executing {len(steps)} steps..."}

        # ── Phase 2: Execute ────────────────────────────────────────────
        results: list[StepResult] = []
        context_so_far = ""
        all_tools_used = []
        all_step_outputs = []

        for step in steps:
            # Check cancellation before each step
            if self.cancel_event.is_set():
                yield {"type": "cancelled", "content": f"Task cancelled before step {step.step_number}"}
                break

            # ── Trust-Aware Model Selection (paper §4.2) ──────────────
            step_model = self._router.select_model(resolved_model) if resolved_model else resolved_model

            # ── Delegation Contract (paper §3.1) ─────────────────────
            contract = build_step_contract(
                step_number=step.step_number,
                step_title=step.title,
                step_description=step.description,
                delegatee_model=step_model or resolved_model,
                tools_needed=step.tools_needed,
                sandbox_path=self.sandbox_path,
                budget_usd=COST_LIMIT_PER_TASK / max(len(steps), 1),
                timeout_seconds=120.0,
            )
            step.contract_id = contract.contract_id
            step.verification_criteria = contract.verification_criteria

            # ── Accountability Hop ─────────────────────────────────────
            step_hop = chain.add_hop("orchestrator", step_model or "executor", contract.contract_id)

            # ── Lazy Tool Discovery ──────────────────────────────────────
            tool_filter = None
            if step.tools_needed:
                tool_filter = resolve_tools_for_step(step.tools_needed)
                log.info("Step %d: lazy discovery → %d tools (from %s)",
                         step.step_number, len(tool_filter), step.tools_needed)

            # Intersect with pack allowlist if active
            if self._pack:
                pack_tools = resolve_tools_for_step(self._pack.tools)
                if tool_filter:
                    tool_filter = tool_filter & pack_tools
                else:
                    tool_filter = pack_tools

            # Public mode: restrict to safe tools only
            from forge.config import PUBLIC_MODE
            if PUBLIC_MODE:
                from forge.public_mode import SAFE_TOOLS
                if tool_filter:
                    tool_filter = tool_filter & SAFE_TOOLS
                else:
                    tool_filter = set(SAFE_TOOLS)

            yield {
                "type": "step_start",
                "step": step.step_number,
                "title": step.title,
                "description": step.description,
                "tools_filtered": len(tool_filter) if tool_filter else len(self.registry.list_tools()),
                "contract_id": contract.contract_id,
                "delegatee": step_model or "default",
                "verification_criteria": contract.verification_criteria,
            }

            # ── Execute with Dynamic Assessment (paper §4.1) ──────────
            result = yield from self._execute_step_with_assessment(
                task_id=task_id,
                step=step,
                step_model=step_model or resolved_model,
                contract=contract,
                resolved_model=resolved_model,
                context_so_far=context_so_far,
                tool_filter=tool_filter,
                task_goal=task,
            )

            # ── Accountability tracking ─────────────────────────────────
            chain.complete_hop(
                step_hop.hop_number,
                output_summary=result.output[:200],
                error=result.error or "",
            )

            results.append(result)
            all_tools_used.extend(result.tools_used)
            all_step_outputs.append(result.output)
            context_so_far += f"\nStep {step.step_number} ({step.title}): {result.output}\n"

            # Submit completed step for background PRM judging
            if background_judge and result.status in ("success", "failed"):
                background_judge.submit(step, result, task)

            yield {
                "type": "step_done",
                "step": step.step_number,
                "status": result.status,
                "delegatee": result.delegatee_model,
                "was_reassigned": result.was_reassigned,
                "trust_score": result.trust_score_after,
                "latency_s": result.latency_seconds,
            }

            if self.cancel_event.is_set():
                break

        # ── Collect Judge Scores + Generate Directives (arXiv:2603.10165) ──
        judge_scores = []
        directives = []
        if background_judge:
            judge_scores = background_judge.collect()
            background_judge.shutdown()
            # Feed judge scores into trust (secondary update)
            for js in judge_scores:
                mr = next((r for r in results if r.step_number == js.step_number), None)
                if mr and mr.delegatee_model:
                    self._trust.record_outcome(
                        mr.delegatee_model, success=(js.score >= 5.0),
                        quality_signal=js.score / 10.0,
                    )

        if DIRECTIVES_ENABLED:
            from forge.directives import detect_reassignment_directives
            directives = detect_reassignment_directives(results, steps, task)

        # Emit judge scores to UI
        if judge_scores:
            yield {"type": "judge_scores",
                   "scores": [{"step": js.step_number, "score": js.score,
                                "rationale": js.rationale} for js in judge_scores]}

        # ── Summary ─────────────────────────────────────────────────────
        # Guardrail summary
        if self.guardrail_engine.violation_count > 0:
            yield {"type": "guardrail_summary", **self.guardrail_engine.summary()}

        if self.cancel_event.is_set():
            summary = f"Cancelled after {len(results)}/{len(steps)} steps"
        else:
            success_count = sum(1 for r in results if r.status == "success")
            reassigned_count = sum(1 for r in results if r.was_reassigned)
            summary = f"Completed {success_count}/{len(results)} steps"
            if reassigned_count > 0:
                summary += f" ({reassigned_count} reassigned)"

            # Session memory + knowledge graph: remember what we learned
            if success_count > 0:
                key_paths = extract_key_paths(all_step_outputs)
                outcome = "; ".join(
                    f"Step {r.step_number}: {r.status}" for r in results
                )
                remember_task(task, all_tools_used, key_paths, outcome)
                self._knowledge_graph.record_task_knowledge(
                    task, all_tools_used, key_paths, outcome,
                )
                vault.process_6rs(
                    task=task, tools_used=all_tools_used, key_paths=key_paths,
                    outcome=outcome, success=(success_count > 0),
                    latency_seconds=sum(r.latency_seconds for r in results),
                    step_count=len(results),
                    directives=directives,
                    judge_scores=judge_scores,
                )

        yield {"type": "done", "summary": summary,
               "accountability_chain": chain.summary(),
               "firewall": self._firewall.stats(),
               "kernel": self._kernel.summary()}

        return TaskResult(
            task_id=task_id,
            task=task,
            plan_raw=plan_raw,
            results=results,
            final_summary=summary,
            accountability_chain=chain.summary(),
        )

    def _execute_step_with_assessment(
        self,
        task_id: str,
        step: PlanStep,
        step_model: str,
        contract,
        resolved_model: str,
        context_so_far: str,
        tool_filter: set[str] | None,
        task_goal: str,
    ) -> Generator[dict, None, StepResult]:
        """Execute a step with dynamic assessment and adaptive reassignment.

        If the primary model fails or underperforms, attempts reassignment
        to a fallback model from the trust-ranked chain.
        """
        effective_model = step_model
        was_reassigned = False
        reassigned_from = ""
        max_attempts = 2  # primary + one fallback

        for attempt in range(max_attempts):
            step_start = time.time()

            # ── Adaptive Context Compaction ───────────────────────────────
            compacted_context = compact_context(context_so_far, step.step_number)

            # ── Dynamic Assessor (paper §4.1) ────────────────────────────
            assessor = DelegationAssessor(contract)

            # ── Signal Extraction (arXiv:2603.10165) ──────────────────────
            signal_extractor = None
            if SIGNALS_ENABLED:
                from forge.signals import SignalExtractor
                signal_extractor = SignalExtractor(
                    expected_latency=contract.timeout_seconds or 60.0,
                    step_number=step.step_number,
                )

            xai_client = self.client if self._needs_xai_client(effective_model) else None

            gen = executor.execute_step(
                client=xai_client,
                registry=self.registry,
                step_title=step.title,
                step_description=step.description,
                context=compacted_context,
                sandbox_path=self.sandbox_path,
                cancel_event=self.cancel_event,
                model=effective_model,
                tool_filter=tool_filter,
                task_goal=task_goal,
                guardrail_engine=self.guardrail_engine,
            )
            if self._toll_relay:
                gen = self._toll_relay.meter(
                    gen, sender=self._toll_sender or "planner",
                    receiver=f"executor_step_{step.step_number}",
                    session_id=task_id,
                )

            step_output = ""
            tools_used = []
            error = None
            escalation = None

            try:
                while True:
                    msg = next(gen)

                    # Feed message to assessor for real-time monitoring
                    assessor.observe(msg)
                    if signal_extractor:
                        signal_extractor.observe(msg)

                    # ── Semantic Firewall (arXiv:2603.08938) ──────────
                    if msg.get("type") == "tool_call":
                        verdict = self._firewall.check(msg["name"], msg.get("args", {}))
                        if not verdict.allowed:
                            yield {"type": "firewall_block", "tool": msg["name"],
                                   "reason": verdict.blocked_reason}
                            error = f"Firewall blocked: {verdict.blocked_reason}"
                            break

                    yield msg
                    if msg.get("type") == "content":
                        step_output += msg["content"]
                    elif msg.get("type") == "tool_call":
                        tools_used.append(msg["name"])
                    elif msg.get("type") == "error":
                        error = msg["content"]
                    elif msg.get("type") == "cancelled":
                        error = "Cancelled"
                        break
                    elif msg.get("type") == "escalation":
                        escalation = msg
                        break
            except StopIteration as e:
                if e.value:
                    step_output = e.value

            latency = time.time() - step_start
            cancelled = self.cancel_event.is_set()
            escalated = escalation is not None
            status = "cancelled" if cancelled else ("escalated" if escalated else ("failed" if error else "success"))

            # ── Signal Finalization (arXiv:2603.10165) ────────────────────
            step_signals = signal_extractor.finalize(latency) if signal_extractor else None

            # ── Trust Update ─────────────────────────────────────────────
            self._trust.record_outcome(
                effective_model,
                success=(status == "success"),
                latency_seconds=latency,
                was_reassigned=was_reassigned,
                quality_signal=step_signals.aggregate_score if step_signals else None,
            )

            # ── Dynamic Assessment verdict (paper §4.1) ─────────────────
            assessment = assessor.assess()
            if assessment.concerns:
                log.info("Assessment concerns (step %d): %s",
                         step.step_number, "; ".join(assessment.concerns))

            # ── Adaptive Reassignment (paper §4.3) ───────────────────────
            # If step failed or assessor recommends reassignment, try a fallback
            if (status == "failed" or (assessment.should_reassign and status != "success")) and attempt == 0 and not cancelled:
                fallback = self._router.get_fallback(effective_model)
                if fallback:
                    # Penalize the OUTGOING model — separate from the
                    # incoming rescue worker's outcome accounting. This
                    # was previously double-counted onto the incoming
                    # model, making rescue workers lose trust on success.
                    reassign_reason = (error or "; ".join(assessment.concerns) or "underperformed")[:160]
                    self._trust.record_reassignment_from(effective_model, reason=reassign_reason)
                    yield {
                        "type": "status",
                        "phase": "reassigning",
                        "content": f"Step {step.step_number} failed on {effective_model}, "
                                   f"reassigning to {fallback}...",
                    }
                    reassigned_from = effective_model
                    effective_model = fallback
                    was_reassigned = True
                    continue  # retry with fallback model

            # Step completed (success, final failure, escalated, or cancelled)
            return StepResult(
                step_number=step.step_number,
                status=status,
                output=step_output[:2000],
                tools_used=list(set(tools_used)),
                error=error,
                contract_id=contract.contract_id,
                delegatee_model=effective_model,
                was_reassigned=was_reassigned,
                reassigned_from=reassigned_from,
                trust_score_after=self._trust.get_trust(effective_model),
                latency_seconds=round(latency, 2),
            )

        # Should not reach here, but safety net
        return StepResult(
            step_number=step.step_number,
            status="failed",
            error="Exhausted all delegation attempts",
            delegatee_model=effective_model,
            was_reassigned=was_reassigned,
            reassigned_from=reassigned_from,
        )
