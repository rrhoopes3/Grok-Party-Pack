from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Literal
from datetime import datetime


# ── SSE Protocol ────────────────────────────────────────────────────────────
# Single source of truth for the "type" field on every SSE message. Anything
# not in this list is a contract violation. The server-side helpers
# (forge.run_log, forge.app, the executor, etc.) should pass these strings
# instead of free-form literals to catch typos at the producer side.
MessageType = Literal[
    "content",
    "tool_call",
    "tool_result",
    "status",
    "error",
    "cancelled",
    "done",
    "escalation",
    "guardrail_violation",
    "firewall_block",
    "token_usage",
    "toll_deducted",
    "plan_content",
    "widget",
]
ALL_MESSAGE_TYPES: tuple[str, ...] = MessageType.__args__  # type: ignore[attr-defined]


class RunMessage(BaseModel):
    """Typed SSE message. Use `make_msg(...)` to construct.

    Existing producers still pass plain dicts — this type exists so new code
    can opt in and so consumers (UI, RunLog, cost tracker) get a documented
    contract instead of guessing fields.
    """
    type: MessageType
    content: Any | None = None
    extras: dict[str, Any] = Field(default_factory=dict)

    def to_sse_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.content is not None:
            out["content"] = self.content
        out.update(self.extras)
        return out


def make_msg(msg_type: str, **fields: Any) -> dict[str, Any]:
    """Build an SSE-shaped dict and validate the type at the producer side.

    Raises ValueError if `msg_type` isn't a known message type — much better
    than silently emitting a typo'd event the UI ignores.
    """
    if msg_type not in ALL_MESSAGE_TYPES:
        raise ValueError(
            f"Unknown SSE message type {msg_type!r}; "
            f"add it to MessageType in forge.models if intentional."
        )
    return {"type": msg_type, **fields}


class PlanStep(BaseModel):
    step_number: int
    title: str
    description: str
    tools_needed: list[str] = Field(
        default_factory=list,
        description="Tools likely needed: read_file, write_file, run_command, etc.",
    )
    expected_output: str = ""
    # Delegation metadata (populated by delegation framework)
    contract_id: str = ""
    verification_criteria: list[str] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    task_summary: str
    steps: list[PlanStep]
    success_criteria: str = ""


class StepResult(BaseModel):
    step_number: int
    status: Literal["success", "failed", "skipped", "cancelled", "escalated"] = "success"
    output: str = ""
    tools_used: list[str] = Field(default_factory=list)
    error: str | None = None
    # Delegation metadata
    contract_id: str = ""
    delegatee_model: str = ""
    was_reassigned: bool = False
    reassigned_from: str = ""
    trust_score_after: float | None = None
    latency_seconds: float = 0.0


class TaskResult(BaseModel):
    task_id: str
    task: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    plan: ExecutionPlan | None = None
    plan_raw: str = ""
    results: list[StepResult] = Field(default_factory=list)
    final_summary: str = ""
    # Delegation metadata
    accountability_chain: dict | None = None  # from AccountabilityChain.summary()
