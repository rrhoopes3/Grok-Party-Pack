"""
Delegation framework — cross-pollinated from "Intelligent AI Delegation"
(Tomašev, Franklin, Osindero; arXiv:2602.11865, Feb 2026).

Implements the paper's five pillars adapted for The Forge:

  1. Dynamic Assessment     — real-time delegatee performance monitoring
  2. Adaptive Execution     — reassign failing steps to alternate models/providers
  3. Structural Transparency — delegation contracts with verification criteria
  4. Scalable Market Coordination — trust calibration and reputation scoring
  5. Systemic Resilience    — cascading fallback chains and degradation handling

The core abstractions:

  DelegationContract  — formal spec: constraints, verification, authority bounds
  TrustLedger         — per-agent trust scores, calibrated from delegation outcomes
  AccountabilityChain — transitive accountability tracking across delegation hops
  DelegationAssessor  — real-time performance signal aggregation
  AdaptiveRouter      — reassignment logic when delegatees underperform
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from forge.config import DATA_DIR

log = logging.getLogger("forge.delegation")


# ── Delegation Contract ─────────────────────────────────────────────────────
# Paper §3.1: "Contract-First Decomposition — a delegator is forbidden from
# assigning a task unless the outcome can be precisely verified."


@dataclass
class DelegationContract:
    """Formal contract between delegator and delegatee.

    Specifies what the delegatee must do, under what constraints,
    and how success will be verified. Inspired by the paper's
    Contract-First Decomposition principle.
    """
    contract_id: str = field(default_factory=lambda: f"dc_{uuid.uuid4().hex[:10]}")
    delegator: str = ""           # who is delegating (e.g. "orchestrator", "planner")
    delegatee: str = ""           # who is receiving (e.g. "executor_step_1", model name)
    task_summary: str = ""        # what must be done
    verification_criteria: list[str] = field(default_factory=list)  # how to verify success
    authority_bounds: AuthorityBounds | None = None
    budget_usd: float = 0.0      # max cost allowed
    timeout_seconds: float = 0.0  # max wall-clock time
    created_at: float = field(default_factory=time.time)
    status: Literal["active", "completed", "failed", "reassigned"] = "active"


@dataclass
class AuthorityBounds:
    """Defines what a delegatee is allowed to do.

    Paper §2.3: "Authority gradients — zone of indifference
    within which the delegatee may act autonomously."
    """
    allowed_tools: list[str] = field(default_factory=list)  # empty = all tools
    sandbox_path: str = ""        # filesystem boundary
    max_iterations: int = 15      # iteration cap
    can_sub_delegate: bool = False  # can the delegatee delegate further?
    allowed_providers: list[str] = field(default_factory=list)  # empty = any


# ── Trust Ledger ────────────────────────────────────────────────────────────
# Paper §4.2: "Trust calibration — continuously updated based on
# delegation outcomes, not assumed from credentials."

TRUST_FILE = DATA_DIR / "trust_ledger.json"

# Default trust for unknown agents — paper recommends "zero-trust" start,
# but in practice we start at a neutral 0.5 so new models get a fair chance.
DEFAULT_TRUST_SCORE = 0.5

# How much a single outcome shifts trust (exponential moving average weight)
TRUST_ALPHA = 0.15

# Below this trust score, the adaptive router will prefer alternatives
TRUST_REASSIGNMENT_THRESHOLD = 0.3


@dataclass
class TrustEntry:
    """Trust record for a delegatee (identified by model name or agent ID)."""
    agent_id: str
    trust_score: float = DEFAULT_TRUST_SCORE
    total_delegations: int = 0
    successes: int = 0
    failures: int = 0
    reassignments: int = 0  # times work was taken away mid-execution
    avg_latency_seconds: float = 0.0
    last_updated: float = field(default_factory=time.time)


class TrustLedger:
    """Persistent trust calibration across delegation outcomes.

    Uses exponential moving average to weight recent outcomes more heavily,
    matching the paper's "dynamic trust calibration" requirement.
    """

    def __init__(self, path: Path | None = None):
        self._path = path or TRUST_FILE
        self._entries: dict[str, TrustEntry] = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for item in data:
                    entry = TrustEntry(**item)
                    self._entries[entry.agent_id] = entry
            except json.JSONDecodeError:
                log.warning("Corrupted trust ledger at %s — starting fresh", self._path)
                self._entries = {}
            except Exception as exc:
                log.warning("Failed to load trust ledger at %s: %s — starting fresh", self._path, exc)
                self._entries = {}

    def _save(self):
        data = []
        for entry in self._entries.values():
            data.append({
                "agent_id": entry.agent_id,
                "trust_score": round(entry.trust_score, 4),
                "total_delegations": entry.total_delegations,
                "successes": entry.successes,
                "failures": entry.failures,
                "reassignments": entry.reassignments,
                "avg_latency_seconds": round(entry.avg_latency_seconds, 2),
                "last_updated": entry.last_updated,
            })
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_trust(self, agent_id: str) -> float:
        """Get current trust score for an agent (0.0 to 1.0)."""
        entry = self._entries.get(agent_id)
        return entry.trust_score if entry else DEFAULT_TRUST_SCORE

    def get_entry(self, agent_id: str) -> TrustEntry:
        """Get or create a trust entry."""
        if agent_id not in self._entries:
            self._entries[agent_id] = TrustEntry(agent_id=agent_id)
        return self._entries[agent_id]

    def record_outcome(
        self,
        agent_id: str,
        success: bool,
        latency_seconds: float = 0.0,
        was_reassigned: bool = False,
        quality_signal: float | None = None,
    ):
        """Update trust based on a delegation outcome.

        Uses EMA: new_trust = α * outcome + (1 - α) * old_trust

        If quality_signal is provided (0.0-1.0), it replaces the binary
        outcome for richer trust calibration (OpenClaw-RL §PRM).

        Semantics of `was_reassigned`:
          - This agent picked up work *because* a previous agent was
            reassigned-from. It is NOT a penalty signal for the current
            agent — that agent is a rescue worker and should be rewarded
            (or at minimum, neutrally credited) for completing the work.
          - The penalty for the original failure must be applied to the
            agent that GOT reassigned-FROM, via `record_reassignment_from`.
        """
        entry = self.get_entry(agent_id)
        entry.total_delegations += 1

        if quality_signal is not None:
            # Continuous signal from SignalExtractor or PRM judge
            outcome_signal = max(0.0, min(1.0, quality_signal))
            if success:
                entry.successes += 1
            else:
                entry.failures += 1
        elif success:
            entry.successes += 1
            outcome_signal = 1.0
        else:
            entry.failures += 1
            outcome_signal = 0.0

        # `was_reassigned` historically zeroed the outcome — that was a bug.
        # If we got here as a rescue worker AND succeeded, the success signal
        # stands. We do not double-count the predecessor's failure here.

        # Exponential moving average
        entry.trust_score = (
            TRUST_ALPHA * outcome_signal + (1 - TRUST_ALPHA) * entry.trust_score
        )
        entry.trust_score = max(0.0, min(1.0, entry.trust_score))

        # Update average latency
        if latency_seconds > 0:
            if entry.avg_latency_seconds == 0:
                entry.avg_latency_seconds = latency_seconds
            else:
                entry.avg_latency_seconds = (
                    TRUST_ALPHA * latency_seconds
                    + (1 - TRUST_ALPHA) * entry.avg_latency_seconds
                )

        entry.last_updated = time.time()
        self._save()
        log.info(
            "Trust updated: %s → %.3f (success=%s, total=%d, reassigned=%s)",
            agent_id, entry.trust_score, success, entry.total_delegations, was_reassigned,
        )

    def record_reassignment_from(self, agent_id: str, reason: str = "") -> None:
        """Penalize an agent because work was taken away from it mid-flight.

        Call this on the OUTGOING (failed) model when reassigning. The
        rescue worker (incoming model) then records a normal outcome via
        `record_outcome` without an extra penalty.
        """
        entry = self.get_entry(agent_id)
        entry.total_delegations += 1
        entry.failures += 1
        entry.reassignments += 1
        # EMA toward 0 — the failure signal
        entry.trust_score = TRUST_ALPHA * 0.0 + (1 - TRUST_ALPHA) * entry.trust_score
        entry.trust_score = max(0.0, min(1.0, entry.trust_score))
        entry.last_updated = time.time()
        self._save()
        log.info(
            "Trust penalty (reassigned-from): %s → %.3f (reason=%s)",
            agent_id, entry.trust_score, reason or "no reason given",
        )

    def ranked_agents(self, candidates: list[str]) -> list[tuple[str, float]]:
        """Rank candidate agents by trust score (highest first)."""
        scored = [(c, self.get_trust(c)) for c in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


# ── Accountability Chain ────────────────────────────────────────────────────
# Paper §3.3: "Transitive Accountability — Agent B remains fully accountable
# to Agent A for Agent C's work."


@dataclass
class AccountabilityHop:
    """A single hop in a delegation chain."""
    hop_number: int
    delegator: str
    delegatee: str
    contract_id: str
    status: Literal["active", "completed", "failed", "reassigned"] = "active"
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    output_summary: str = ""
    error: str = ""


class AccountabilityChain:
    """Tracks the full chain of custody for a delegated task.

    Ensures that when orchestrator → planner → executor, every hop
    is recorded and the chain of responsibility is maintained.
    """

    def __init__(self, task_id: str, original_task: str):
        self.task_id = task_id
        self.original_task = original_task
        self.hops: list[AccountabilityHop] = []
        self.created_at = time.time()

    def add_hop(self, delegator: str, delegatee: str, contract_id: str) -> AccountabilityHop:
        """Record a new delegation hop."""
        hop = AccountabilityHop(
            hop_number=len(self.hops) + 1,
            delegator=delegator,
            delegatee=delegatee,
            contract_id=contract_id,
        )
        self.hops.append(hop)
        log.info("Accountability chain %s: hop %d (%s → %s)",
                 self.task_id, hop.hop_number, delegator, delegatee)
        return hop

    def complete_hop(self, hop_number: int, output_summary: str = "", error: str = ""):
        """Mark a hop as completed (success or failure)."""
        for hop in self.hops:
            if hop.hop_number == hop_number:
                hop.status = "completed" if not error else "failed"
                hop.completed_at = time.time()
                hop.output_summary = output_summary[:500]
                hop.error = error
                return
        log.warning("Hop %d not found in chain %s", hop_number, self.task_id)

    def reassign_hop(self, hop_number: int, new_delegatee: str, new_contract_id: str) -> AccountabilityHop:
        """Mark a hop as reassigned and create a replacement hop."""
        original_delegator = "orchestrator"
        found = False
        for hop in self.hops:
            if hop.hop_number == hop_number:
                hop.status = "reassigned"
                hop.completed_at = time.time()
                hop.error = f"Reassigned to {new_delegatee}"
                original_delegator = hop.delegator
                found = True
                break
        if not found:
            log.warning("Hop %d not found in chain %s for reassignment", hop_number, self.task_id)
        return self.add_hop(
            delegator=original_delegator,
            delegatee=new_delegatee,
            contract_id=new_contract_id,
        )

    def summary(self) -> dict:
        """Produce a summary of the full accountability chain."""
        return {
            "task_id": self.task_id,
            "original_task": self.original_task[:200],
            "total_hops": len(self.hops),
            "chain": [
                {
                    "hop": h.hop_number,
                    "delegator": h.delegator,
                    "delegatee": h.delegatee,
                    "status": h.status,
                    "duration_s": round(h.completed_at - h.started_at, 2) if h.completed_at else None,
                    "error": h.error or None,
                }
                for h in self.hops
            ],
        }


# ── Delegation Assessor ────────────────────────────────────────────────────
# Paper §4.1: "Dynamic Assessment — continuously monitor the state and
# capacity of the delegatee."


@dataclass
class PerformanceSignals:
    """Real-time performance signals collected during delegation."""
    iteration_count: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    content_length: int = 0
    elapsed_seconds: float = 0.0
    error_messages: list[str] = field(default_factory=list)
    stall_detected: bool = False  # no progress across iterations


class DelegationAssessor:
    """Monitors delegatee performance and flags degradation.

    Signals that trigger reassignment consideration:
    - Too many tool errors (> 50% error rate)
    - Stalling (no content or tool progress across 3+ iterations)
    - Exceeding budget or timeout constraints
    """

    # Thresholds
    TOOL_ERROR_RATE_THRESHOLD = 0.5     # > 50% tool errors → concern
    STALL_ITERATION_THRESHOLD = 3       # no progress for 3 iterations → stall
    MIN_TOOL_CALLS_FOR_ASSESSMENT = 3   # need at least this many to assess error rate

    def __init__(self, contract: DelegationContract):
        self.contract = contract
        self.signals = PerformanceSignals()
        self._last_content_length = 0
        self._last_tool_calls = 0
        self._stall_counter = 0
        self._start_time = time.time()

    def observe(self, msg: dict):
        """Feed an SSE message into the assessor."""
        self.signals.elapsed_seconds = time.time() - self._start_time

        msg_type = msg.get("type", "")
        if msg_type == "content":
            self.signals.content_length += len(msg.get("content", ""))
        elif msg_type == "tool_call":
            self.signals.tool_calls += 1
        elif msg_type == "tool_result":
            result = msg.get("result", "")
            result_lower = result.lower()
            # Check for actual error indicators, not benign mentions like "no errors"
            if re.search(r"\b(error|exception|traceback)\b", result_lower):
                # Exclude false positives like "no error", "0 errors", "error_count: 0"
                if not re.search(r"\b(no\s+errors?|0\s+errors?|error.{0,10}:\s*0)\b", result_lower):
                    self.signals.tool_errors += 1
        elif msg_type == "error":
            self.signals.error_messages.append(msg.get("content", "")[:200])

    def check_iteration(self) -> AssessmentVerdict:
        """Called at the end of each executor iteration to assess health."""
        self.signals.iteration_count += 1

        # Check for stalling (no progress since last check)
        content_progress = self.signals.content_length - self._last_content_length
        tool_progress = self.signals.tool_calls - self._last_tool_calls

        if content_progress == 0 and tool_progress == 0:
            self._stall_counter += 1
        else:
            self._stall_counter = 0

        self._last_content_length = self.signals.content_length
        self._last_tool_calls = self.signals.tool_calls

        if self._stall_counter >= self.STALL_ITERATION_THRESHOLD:
            self.signals.stall_detected = True

        return self.assess()

    def assess(self) -> AssessmentVerdict:
        """Produce an assessment verdict based on accumulated signals."""
        self.signals.elapsed_seconds = time.time() - self._start_time
        concerns: list[str] = []
        should_reassign = False

        # Tool error rate
        if self.signals.tool_calls >= self.MIN_TOOL_CALLS_FOR_ASSESSMENT:
            error_rate = self.signals.tool_errors / self.signals.tool_calls
            if error_rate > self.TOOL_ERROR_RATE_THRESHOLD:
                concerns.append(
                    f"High tool error rate: {error_rate:.0%} "
                    f"({self.signals.tool_errors}/{self.signals.tool_calls})"
                )
                should_reassign = True

        # Stalling
        if self.signals.stall_detected:
            concerns.append(
                f"Stall detected: no progress for {self._stall_counter} iterations"
            )
            should_reassign = True

        # Budget exceeded
        if self.contract.timeout_seconds > 0:
            if self.signals.elapsed_seconds > self.contract.timeout_seconds:
                concerns.append(
                    f"Timeout exceeded: {self.signals.elapsed_seconds:.0f}s > "
                    f"{self.contract.timeout_seconds:.0f}s limit"
                )
                should_reassign = True

        return AssessmentVerdict(
            healthy=len(concerns) == 0,
            should_reassign=should_reassign,
            concerns=concerns,
            signals=self.signals,
        )


@dataclass
class AssessmentVerdict:
    """Result of a delegation assessment."""
    healthy: bool
    should_reassign: bool
    concerns: list[str] = field(default_factory=list)
    signals: PerformanceSignals | None = None


# ── Adaptive Router ─────────────────────────────────────────────────────────
# Paper §4.3: "Adaptive Task Reassignment — if a sub-agent begins to
# hallucinate non-compliant outputs, the parent agent can revoke authority
# mid-execution and reassign the task."

# Fallback chains: if the primary model fails, try these in order.
# Order matters and entries within a chain must be distinct from the key.
# The chain is a *priority list*, not a set — but Python silently drops
# duplicate dict keys, so the LAST top-level key wins. Keep keys unique.
FALLBACK_CHAINS: dict[str, list[str]] = {
    # The non-reasoning Grok variant is a fast tool-capable executor that
    # acts as the first-line fallback for the reasoning Grok.
    "grok-4.20-0309-reasoning": [
        "grok-4.20-0309-non-reasoning",
        "grok-4-1-fast-reasoning",
        "claude-sonnet-4-20250514",
        "gpt-4o",
    ],
    "grok-4.20-0309-non-reasoning": [
        "grok-4.20-0309-reasoning",
        "grok-4-1-fast-reasoning",
        "claude-sonnet-4-20250514",
        "gpt-4o",
    ],
    "grok-4-1-fast-reasoning": [
        "grok-4.20-0309-reasoning",
        "claude-sonnet-4-20250514",
        "gpt-4o",
    ],
    "claude-sonnet-4-20250514": [
        "grok-4.20-0309-reasoning",
        "gpt-4o",
        "claude-haiku-4-20250414",
    ],
    "gpt-4o": [
        "grok-4.20-0309-reasoning",
        "claude-sonnet-4-20250514",
        "gpt-4o-mini",
    ],
}

# Default fallback for models not in the chain map
DEFAULT_FALLBACK_CHAIN = [
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning",
    "claude-sonnet-4-20250514",
    "gpt-4o",
]


class AdaptiveRouter:
    """Selects the best model for a delegation, incorporating trust scores
    and fallback chains.

    When the primary model underperforms (low trust or assessment failure),
    routes to the next-best alternative from the fallback chain.
    """

    def __init__(self, trust_ledger: TrustLedger):
        self.trust = trust_ledger

    def select_model(self, primary_model: str) -> str:
        """Select the best model, considering trust scores.

        If the primary model's trust is below threshold, pick the
        highest-trust alternative from its fallback chain.
        """
        primary_trust = self.trust.get_trust(primary_model)

        if primary_trust >= TRUST_REASSIGNMENT_THRESHOLD:
            return primary_model

        # Primary model is below trust threshold — find the best alternative
        fallbacks = FALLBACK_CHAINS.get(primary_model, DEFAULT_FALLBACK_CHAIN)
        candidates = [primary_model] + fallbacks
        ranked = self.trust.ranked_agents(candidates)

        selected = ranked[0][0]  # highest trust
        if selected != primary_model:
            log.info(
                "Adaptive routing: %s (trust=%.3f) → %s (trust=%.3f)",
                primary_model, primary_trust, selected, ranked[0][1],
            )
        return selected

    def get_fallback(self, failed_model: str) -> str | None:
        """Get the next fallback model after a failure.

        Returns None if no more fallbacks available.
        """
        chain = FALLBACK_CHAINS.get(failed_model, DEFAULT_FALLBACK_CHAIN)

        # Filter out models with trust below threshold
        for candidate in chain:
            if candidate != failed_model:
                trust = self.trust.get_trust(candidate)
                if trust >= TRUST_REASSIGNMENT_THRESHOLD:
                    log.info("Fallback: %s → %s (trust=%.3f)", failed_model, candidate, trust)
                    return candidate

        # All alternatives are below threshold — return the one with highest trust
        if chain:
            ranked = self.trust.ranked_agents(chain)
            best = ranked[0]
            if best[0] != failed_model:
                log.info("Fallback (best-effort): %s → %s (trust=%.3f)",
                         failed_model, best[0], best[1])
                return best[0]

        return None


# ── Contract Builder ────────────────────────────────────────────────────────
# Convenience function for the orchestrator to create contracts per step.

def build_step_contract(
    step_number: int,
    step_title: str,
    step_description: str,
    delegatee_model: str,
    tools_needed: list[str],
    sandbox_path: str = "",
    budget_usd: float = 5.0,
    timeout_seconds: float = 120.0,
) -> DelegationContract:
    """Build a delegation contract for a plan step.

    Maps The Forge's PlanStep to the paper's Contract-First Decomposition.
    """
    # Derive verification criteria from the step description
    verification = _infer_verification(step_description, tools_needed)

    return DelegationContract(
        delegator="orchestrator",
        delegatee=delegatee_model,
        task_summary=f"Step {step_number}: {step_title}",
        verification_criteria=verification,
        authority_bounds=AuthorityBounds(
            allowed_tools=tools_needed,
            sandbox_path=sandbox_path,
            can_sub_delegate=False,
            allowed_providers=[],
        ),
        budget_usd=budget_usd,
        timeout_seconds=timeout_seconds,
    )


def _infer_verification(description: str, tools: list[str]) -> list[str]:
    """Infer verification criteria from step description and tools.

    Simple heuristic — the paper advocates for explicit criteria,
    but we bootstrap them from context when not provided.
    """
    criteria = []
    desc_lower = description.lower()

    if any(t in tools for t in ("write_file", "append_file")):
        criteria.append("Output file exists and is non-empty")
    if "run_command" in tools or "run_python" in tools:
        criteria.append("Command/script exits with status 0")
    if any(t in tools for t in ("git_commit", "git_status")):
        criteria.append("Git operation completes without errors")
    if any(t in tools for t in ("http_get", "http_post")):
        criteria.append("HTTP request returns successful status code")

    if "test" in desc_lower:
        criteria.append("All tests pass")
    if "build" in desc_lower or "compile" in desc_lower:
        criteria.append("Build completes without errors")
    if "deploy" in desc_lower:
        criteria.append("Deployment target is reachable post-deploy")

    if not criteria:
        criteria.append("Step produces non-empty output without errors")

    return criteria
