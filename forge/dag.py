"""
DAG Workflow Engine — branching/conditional step execution for The Forge.

Extends the linear planner pipeline with directed acyclic graph (DAG)
support: parallel fan-out, conditional branches, fallback paths, and
join/barrier nodes.

Architecture:
    1. Planner generates a DAG plan (nodes + edges) instead of a linear list
    2. DAG executor topologically sorts the graph
    3. Independent nodes run in parallel (thread pool)
    4. Conditional edges evaluate predicates on parent results
    5. Failed nodes can route to fallback branches

Node Types:
    - task:      Normal executor step (same as today)
    - condition: Evaluates a predicate, routes to true/false branch
    - parallel:  Fan-out marker — all children run concurrently
    - join:      Barrier — waits for all parents to complete
    - fallback:  Runs only if a specified parent failed

Usage:
    from forge.dag import DAGPlan, DAGExecutor

    plan = DAGPlan()
    plan.add_node("fetch", task="Download the dataset")
    plan.add_node("parse", task="Parse CSV into rows", depends_on=["fetch"])
    plan.add_node("validate", task="Validate schema", depends_on=["fetch"])
    plan.add_node("transform", task="Clean and transform", depends_on=["parse", "validate"])
    plan.add_node("fallback", task="Use cached data", fallback_for="fetch")

    executor = DAGExecutor(plan)
    for event in executor.run():
        print(event)
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Generator

log = logging.getLogger("forge.dag")


class NodeType(str, Enum):
    TASK = "task"
    CONDITION = "condition"
    PARALLEL = "parallel"
    JOIN = "join"
    FALLBACK = "fallback"


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DAGNode:
    """A single node in the execution DAG."""
    id: str
    task: str = ""                     # task description for executor
    node_type: str = "task"            # NodeType value
    depends_on: list[str] = field(default_factory=list)  # parent node IDs
    fallback_for: str = ""             # node ID this is a fallback for
    condition: str = ""                # predicate expression for condition nodes
    true_branch: list[str] = field(default_factory=list)  # nodes to enable if condition is true
    false_branch: list[str] = field(default_factory=list) # nodes to enable if condition is false
    tools_needed: list[str] = field(default_factory=list)
    status: str = "pending"            # NodeStatus value
    result: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    executor_model: str = ""

    @property
    def duration(self) -> float:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 2)
        return 0.0


@dataclass
class DAGPlan:
    """A directed acyclic graph of execution nodes."""
    id: str = ""
    task: str = ""                     # original user task
    nodes: dict[str, DAGNode] = field(default_factory=dict)
    created_at: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()

    def add_node(
        self,
        node_id: str,
        task: str = "",
        node_type: str = "task",
        depends_on: list[str] | None = None,
        fallback_for: str = "",
        condition: str = "",
        true_branch: list[str] | None = None,
        false_branch: list[str] | None = None,
        tools_needed: list[str] | None = None,
        executor_model: str = "",
    ) -> DAGNode:
        """Add a node to the DAG."""
        node = DAGNode(
            id=node_id,
            task=task,
            node_type=node_type,
            depends_on=depends_on or [],
            fallback_for=fallback_for,
            condition=condition,
            true_branch=true_branch or [],
            false_branch=false_branch or [],
            tools_needed=tools_needed or [],
            executor_model=executor_model,
        )
        self.nodes[node_id] = node
        return node

    def get_roots(self) -> list[DAGNode]:
        """Get nodes with no dependencies (entry points)."""
        return [
            n for n in self.nodes.values()
            if not n.depends_on and n.node_type != NodeType.FALLBACK
        ]

    def get_children(self, node_id: str) -> list[DAGNode]:
        """Get nodes that depend on the given node."""
        return [
            n for n in self.nodes.values()
            if node_id in n.depends_on
        ]

    def get_fallbacks(self, node_id: str) -> list[DAGNode]:
        """Get fallback nodes for the given node."""
        return [
            n for n in self.nodes.values()
            if n.fallback_for == node_id
        ]

    def get_ready_nodes(self) -> list[DAGNode]:
        """Get nodes whose dependencies are all satisfied."""
        ready = []
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue
            if node.node_type == NodeType.FALLBACK:
                # Only ready if the node it's a fallback for has failed
                target = self.nodes.get(node.fallback_for)
                if target and target.status == NodeStatus.FAILED:
                    ready.append(node)
                continue

            # Check all dependencies are completed
            deps_met = True
            for dep_id in node.depends_on:
                dep = self.nodes.get(dep_id)
                if not dep or dep.status not in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
                    deps_met = False
                    break
            if deps_met:
                ready.append(node)

        return ready

    def topological_order(self) -> list[str]:
        """Return node IDs in topological order."""
        visited: set[str] = set()
        order: list[str] = []

        def visit(node_id: str):
            if node_id in visited:
                return
            visited.add(node_id)
            node = self.nodes.get(node_id)
            if node:
                for dep in node.depends_on:
                    visit(dep)
                order.append(node_id)

        for node_id in self.nodes:
            visit(node_id)

        return order

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task": self.task,
            "nodes": {k: asdict(v) for k, v in self.nodes.items()},
            "created_at": self.created_at,
        }

    def summary(self) -> dict:
        """Summarize DAG execution status."""
        statuses = {}
        for node in self.nodes.values():
            statuses[node.status] = statuses.get(node.status, 0) + 1
        return {
            "total_nodes": len(self.nodes),
            "statuses": statuses,
            "all_complete": all(
                n.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.FAILED)
                for n in self.nodes.values()
            ),
        }


# ── DAG Executor ─────────────────────────────────────────────────────────

class DAGExecutor:
    """Executes a DAG plan with parallel fan-out and conditional routing."""

    def __init__(
        self,
        plan: DAGPlan,
        step_callback: Callable | None = None,
        max_parallel: int = 4,
        cancel_event: threading.Event | None = None,
    ):
        self.plan = plan
        self._step_callback = step_callback  # (node: DAGNode) -> dict with result/error
        self._max_parallel = max_parallel
        self._cancel_event = cancel_event or threading.Event()

    def run(self) -> Generator[dict, None, dict]:
        """Execute the DAG, yielding status events.

        Returns a summary dict when complete.
        """
        yield {
            "type": "dag_start",
            "dag_id": self.plan.id,
            "total_nodes": len(self.plan.nodes),
            "topology": self.plan.topological_order(),
        }

        with ThreadPoolExecutor(max_workers=self._max_parallel) as pool:
            while not self._cancel_event.is_set():
                ready = self.plan.get_ready_nodes()
                if not ready:
                    # Check if we're done or deadlocked
                    summary = self.plan.summary()
                    if summary["all_complete"]:
                        break
                    # Deadlock: no ready nodes but not all complete
                    pending = [n.id for n in self.plan.nodes.values() if n.status == NodeStatus.PENDING]
                    if pending:
                        yield {
                            "type": "dag_deadlock",
                            "pending_nodes": pending,
                        }
                        break
                    break

                # Handle condition nodes synchronously
                condition_nodes = [n for n in ready if n.node_type == NodeType.CONDITION]
                task_nodes = [n for n in ready if n.node_type != NodeType.CONDITION]

                for cond in condition_nodes:
                    self._evaluate_condition(cond)
                    yield {
                        "type": "dag_condition",
                        "node_id": cond.id,
                        "result": cond.result,
                        "status": cond.status,
                    }

                # Execute task nodes in parallel
                if task_nodes:
                    futures = {}
                    for node in task_nodes:
                        node.status = NodeStatus.RUNNING
                        node.started_at = time.time()
                        yield {
                            "type": "dag_node_start",
                            "node_id": node.id,
                            "task": node.task,
                            "node_type": node.node_type,
                        }
                        future = pool.submit(self._execute_node, node)
                        futures[future] = node

                    for future in as_completed(futures):
                        node = futures[future]
                        try:
                            result = future.result()
                            node.result = result.get("result", "")
                            node.error = result.get("error", "")
                            node.status = NodeStatus.COMPLETED if not node.error else NodeStatus.FAILED
                        except Exception as e:
                            node.error = f"{type(e).__name__}: {e}"
                            node.status = NodeStatus.FAILED

                        node.finished_at = time.time()
                        yield {
                            "type": "dag_node_complete",
                            "node_id": node.id,
                            "status": node.status,
                            "duration": node.duration,
                            "error": node.error,
                        }

        summary = self.plan.summary()
        yield {
            "type": "dag_complete",
            "dag_id": self.plan.id,
            **summary,
        }
        return summary

    def _execute_node(self, node: DAGNode) -> dict:
        """Execute a single node. Uses the step_callback if provided."""
        if self._step_callback:
            return self._step_callback(node)
        # Default: just mark as done (no actual execution)
        return {"result": f"Node {node.id} completed (no executor configured)"}

    def _evaluate_condition(self, node: DAGNode) -> None:
        """Evaluate a condition node and route to true/false branches."""
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()

        # Simple predicate evaluation: check if parent results contain keywords
        condition_met = False
        if node.condition:
            # Check parent results for condition string
            for dep_id in node.depends_on:
                dep = self.plan.nodes.get(dep_id)
                if dep and dep.result:
                    if node.condition.lower() in dep.result.lower():
                        condition_met = True
                        break

        node.result = "true" if condition_met else "false"
        node.status = NodeStatus.COMPLETED
        node.finished_at = time.time()

        # Skip the branch that wasn't taken
        skip_branch = node.false_branch if condition_met else node.true_branch
        enable_branch = node.true_branch if condition_met else node.false_branch

        for node_id in skip_branch:
            skip_node = self.plan.nodes.get(node_id)
            if skip_node:
                skip_node.status = NodeStatus.SKIPPED

        log.info(
            "Condition %s evaluated to %s — enabled: %s, skipped: %s",
            node.id, node.result, enable_branch, skip_branch,
        )


# ── Plan Builder (from planner output) ───────────────────────────────────

def plan_from_linear(steps: list[dict], task: str = "") -> DAGPlan:
    """Convert a linear plan (list of steps) to a DAG plan.

    Each step becomes a node with a dependency on the previous step.
    This is the simplest conversion — maintains backward compatibility.
    """
    plan = DAGPlan(task=task)
    prev_id = ""
    for i, step in enumerate(steps):
        node_id = f"step_{i + 1}"
        plan.add_node(
            node_id=node_id,
            task=step.get("description", step.get("title", f"Step {i + 1}")),
            depends_on=[prev_id] if prev_id else [],
            tools_needed=step.get("tools_needed", []),
        )
        prev_id = node_id
    return plan
