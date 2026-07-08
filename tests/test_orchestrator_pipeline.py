"""Shared orchestrator pipeline stages used by both direct and planned modes.

These tests call the real shipped helpers on Orchestrator and fail if either
mode path stops using them (by asserting method identity / single definition
and exercising the real functions).
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from forge.orchestrator import Orchestrator


@pytest.fixture
def orch():
    with patch("forge.orchestrator.create_registry") as reg:
        reg.return_value = MagicMock()
        reg.return_value.list_tools.return_value = []
        o = Orchestrator(direct_mode=True, guardrails_enabled=False)
        return o


def test_shared_helpers_exist_and_are_single_methods(orch):
    """Both modes must call the same three helpers (not private copies)."""
    for name in ("_assemble_context", "_resolve_tool_filter", "_record_task_outcomes"):
        assert hasattr(Orchestrator, name), f"missing helper {name}"
        method = getattr(Orchestrator, name)
        assert callable(method)
        # Exactly one definition on the class (not dual wrappers)
        assert name in Orchestrator.__dict__


def test_assemble_context_used_by_both_run_paths():
    """Source of _run_direct and _run_planned must both call _assemble_context."""
    src_direct = inspect.getsource(Orchestrator._run_direct)
    src_planned = inspect.getsource(Orchestrator._run_planned)
    assert "self._assemble_context" in src_direct
    assert "self._assemble_context" in src_planned
    # Neither path may re-implement vault.recall_vault_context inline
    assert "recall_vault_context" not in src_direct
    assert "recall_vault_context" not in src_planned


def test_resolve_tool_filter_used_by_both_run_paths():
    src_direct = inspect.getsource(Orchestrator._run_direct)
    src_planned = inspect.getsource(Orchestrator._run_planned)
    assert "self._resolve_tool_filter" in src_direct
    assert "self._resolve_tool_filter" in src_planned
    # No second copy of public-mode intersection inside either path
    assert "SAFE_TOOLS" not in src_direct
    assert "SAFE_TOOLS" not in src_planned


def test_record_task_outcomes_used_by_both_run_paths():
    src_direct = inspect.getsource(Orchestrator._run_direct)
    src_planned = inspect.getsource(Orchestrator._run_planned)
    assert "self._record_task_outcomes" in src_direct
    assert "self._record_task_outcomes" in src_planned
    assert "remember_task(" not in src_direct
    assert "remember_task(" not in src_planned
    assert "process_6rs" not in src_direct
    assert "process_6rs" not in src_planned


def test_assemble_context_merges_memory_graph_vault(orch):
    vault = MagicMock()
    vault.recall_vault_context.return_value = "VAULT_CTX"
    orch._get_vault = MagicMock(return_value=vault)
    orch._knowledge_graph.recall_graph_context = MagicMock(return_value="GRAPH_CTX")

    with patch("forge.orchestrator.recall_relevant", return_value="MEM_CTX"):
        ctx = orch._assemble_context("do a thing", vault_agent_id="model-a")

    assert "MEM_CTX" in ctx
    assert "GRAPH_CTX" in ctx
    assert "VAULT_CTX" in ctx
    orch._get_vault.assert_called_with("model-a")
    vault.recall_vault_context.assert_called_once_with("do a thing")


def test_resolve_tool_filter_intersects_pack_and_public(orch):
    from forge.packs import CapabilityPack, PackBudget

    pack = CapabilityPack(
        name="testpack",
        description="t",
        tools=["filesystem"],
        budget=PackBudget(),
    )
    orch._pack = pack

    def _resolve(tools):
        if tools == pack.tools:
            return {"read_file", "write_file", "run_command"}
        return set(tools)

    with patch("forge.orchestrator.resolve_tools_for_step", side_effect=_resolve):
        with patch("forge.config.PUBLIC_MODE", True):
            with patch("forge.public_mode.SAFE_TOOLS", {"read_file", "list_directory"}):
                result = orch._resolve_tool_filter(
                    tools_needed=["read_file", "write_file", "delete_file"],
                )

    # tools_needed ∩ pack ∩ SAFE_TOOLS
    assert result == {"read_file"}


def test_record_task_outcomes_writes_memory_graph_vault(orch):
    vault = MagicMock()
    orch._get_vault = MagicMock(return_value=vault)
    orch._knowledge_graph.record_task_knowledge = MagicMock()

    with patch("forge.orchestrator.extract_key_paths", return_value=["/tmp/a"]):
        with patch("forge.orchestrator.remember_task") as remember:
            orch._record_task_outcomes(
                "task text",
                ["read_file"],
                ["step output here"],
                outcome_summary="ok",
                success=True,
                latency_seconds=1.5,
                step_count=1,
                vault_agent_id="m1",
            )
            remember.assert_called_once()
            orch._knowledge_graph.record_task_knowledge.assert_called_once()
            vault.process_6rs.assert_called_once()
            kwargs = vault.process_6rs.call_args.kwargs
            assert kwargs["task"] == "task text"
            assert kwargs["success"] is True
            assert kwargs["step_count"] == 1


def test_record_task_outcomes_skips_on_failure(orch):
    vault = MagicMock()
    orch._get_vault = MagicMock(return_value=vault)
    with patch("forge.orchestrator.remember_task") as remember:
        orch._record_task_outcomes(
            "t", [], [], outcome_summary="fail", success=False,
            latency_seconds=0, step_count=0,
        )
        remember.assert_not_called()
        vault.process_6rs.assert_not_called()
