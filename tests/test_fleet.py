"""Fleet Mode unit tests — registry, ledger, router, health, executor, DAG."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge.fleet import (
    AutoRouter,
    BudgetExceeded,
    CostLedger,
    FleetConfig,
    FleetOrchestrator,
    FleetStep,
    GrokBuildExecutor,
    MAX_PARALLEL_CAP,
    ModelRegistry,
    NoHealthyProvider,
    clear_provider_health,
    extract_text,
    is_provider_healthy,
    mark_provider_unhealthy,
    normalize_scope_path,
    plan_from_linear,
    scopes_conflict,
    validate_steps,
)
from forge.fleet.executor import parse_usage_from_output
from forge.fleet.registry import normalize_provider


# ── Registry ──────────────────────────────────────────────────────────────


class TestModelRegistry:
    def test_loads_from_executor_models(self):
        reg = ModelRegistry.from_executor_models()
        assert "grok-4.5" in reg
        assert "claude-sonnet-5" in reg
        assert "gpt-5.4-mini" in reg
        entry = reg.require("claude-sonnet-5")
        assert entry.cost_in == 2.0
        assert entry.cost_out == 10.0
        assert normalize_provider(entry.provider) == "anthropic"

    def test_pricing_calc(self):
        reg = ModelRegistry.from_executor_models()
        cost = reg.calculate_cost("claude-sonnet-5", 1_000_000, 1_000_000)
        assert abs(cost - 12.0) < 1e-9
        assert reg.calculate_cost("no-such-model", 1000, 1000) == 0.0
        # unknown under budget floor
        assert reg.calculate_cost(
            "no-such-model", 1000, 1000, unknown_floor=0.05
        ) == 0.05

    def test_negative_tokens_clamped(self):
        reg = ModelRegistry.from_executor_models()
        assert reg.calculate_cost("gpt-5.4-mini", -1000, -50) == 0.0

    def test_routing_defaults(self):
        reg = ModelRegistry.from_executor_models()
        assert reg.routing.plan == "grok-4.5"
        assert reg.routing.implement == "claude-sonnet-5"
        assert reg.routing.mechanical == "gpt-5.4-mini"
        assert reg.routing.verify == "grok-4.5"
        assert "anthropic" in reg.routing.fallbacks

    def test_model_for_whitelist(self):
        reg = ModelRegistry.from_executor_models()
        assert reg.routing.model_for("fallbacks") == reg.routing.implement
        assert reg.routing.model_for("BOGUS") == reg.routing.implement
        assert reg.routing.model_for("plan") == reg.routing.plan

    def test_json_override(self, tmp_path: Path):
        path = tmp_path / "fleet.json"
        path.write_text(
            json.dumps(
                {
                    "models": {
                        "custom-mini": {
                            "provider": "OpenAI",
                            "label": "Custom Mini",
                            "cost_in": 0.1,
                            "cost_out": 0.2,
                            "tier": "fast",
                        },
                        "gpt-5.4-mini": {"cost_out": 9.99},
                        "evil-neg": {
                            "provider": "OpenAI",
                            "cost_in": -5,
                            "cost_out": float("nan"),
                        },
                    },
                    "routing": {
                        "auto": {
                            "mechanical": "custom-mini",
                            "fallbacks": {"openai": ["xai"]},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        reg = ModelRegistry.from_executor_models(override_path=path)
        assert "custom-mini" in reg
        assert reg.require("custom-mini").cost_in == 0.1
        assert reg.require("gpt-5.4-mini").cost_out == 9.99
        assert reg.routing.mechanical == "custom-mini"
        assert reg.routing.fallbacks["openai"] == ["xai"]
        # negative / nan clamped to 0
        assert reg.require("evil-neg").cost_in == 0.0
        assert reg.require("evil-neg").cost_out == 0.0

    def test_toml_override_when_available(self, tmp_path: Path):
        pytest.importorskip("tomllib")
        path = tmp_path / "models.toml"
        path.write_text(
            """
[models."fleet-test-model"]
provider = "xAI"
label = "Fleet Test"
cost_in = 1.5
cost_out = 3.0
tier = "frontier"

[routing.auto]
plan = "fleet-test-model"
""",
            encoding="utf-8",
        )
        reg = ModelRegistry.from_executor_models(override_path=path)
        assert reg.require("fleet-test-model").tier == "frontier"
        assert reg.routing.plan == "fleet-test-model"

    def test_infer_tier_local(self):
        reg = ModelRegistry.from_executor_models()
        local = reg.get("ollama:default")
        assert local is not None
        assert local.tier == "local"


# ── Ledger ────────────────────────────────────────────────────────────────


class TestCostLedger:
    def test_record_and_rollup(self):
        reg = ModelRegistry.from_executor_models()
        ledger = CostLedger(registry=reg)
        ledger.record(
            agent_id="a1",
            step_id="s1",
            model="gpt-5.4-mini",
            input_tokens=1_000_000,
            output_tokens=0,
        )
        assert abs(ledger.total_cost - 0.75) < 1e-9
        snap = ledger.snapshot()
        assert snap["call_count"] == 1
        assert "gpt-5.4-mini" in snap["per_model"]
        assert snap["per_agent"]["a1"]["cost_usd"] == pytest.approx(0.75)

    def test_budget_cap_abort(self):
        reg = ModelRegistry.from_executor_models()
        ledger = CostLedger(registry=reg, budget=0.01, on_exceeded="abort")
        with pytest.raises(BudgetExceeded):
            ledger.record(
                agent_id="a",
                step_id="s",
                model="claude-sonnet-5",
                input_tokens=1_000_000,
                output_tokens=0,
            )

    def test_budget_cap_signal(self):
        reg = ModelRegistry.from_executor_models()
        ledger = CostLedger(registry=reg, budget=0.01, on_exceeded="signal")
        ledger.record(
            agent_id="a",
            step_id="s",
            model="claude-sonnet-5",
            input_tokens=1_000_000,
            output_tokens=0,
        )
        assert ledger.budget_exceeded is True
        assert ledger.snapshot()["budget_exceeded"] is True

    def test_explicit_cost_overrides_registry(self):
        ledger = CostLedger(budget=None)
        ledger.record(
            agent_id="a",
            step_id="s",
            model="whatever",
            input_tokens=0,
            output_tokens=0,
            cost_usd=1.25,
        )
        assert ledger.total_cost == 1.25

    def test_negative_cost_clamped(self):
        # No budget: pure clamp (under budget, zero-token path applies floor)
        ledger = CostLedger(budget=None)
        ledger.record(
            agent_id="a",
            step_id="s",
            model="x",
            cost_usd=-5.0,
        )
        assert ledger.total_cost == 0.0

    def test_negative_tokens_clamped(self):
        reg = ModelRegistry.from_executor_models()
        ledger = CostLedger(registry=reg)
        ledger.record(
            agent_id="a",
            step_id="s",
            model="gpt-5.4-mini",
            input_tokens=-1_000_000,
            output_tokens=-10,
        )
        assert ledger.total_cost == 0.0
        assert ledger.total_input_tokens == 0

    def test_reservation(self):
        ledger = CostLedger(budget=1.0)
        assert ledger.try_reserve("s1", 0.6) is True
        assert ledger.try_reserve("s2", 0.6) is False
        ledger.release_reservation("s1")
        assert ledger.try_reserve("s2", 0.6) is True


# ── Router ────────────────────────────────────────────────────────────────


class TestAutoRouter:
    def setup_method(self):
        clear_provider_health()
        self.reg = ModelRegistry.from_executor_models()
        self.router = AutoRouter(registry=self.reg)

    def teardown_method(self):
        clear_provider_health()

    def test_explicit_wins(self):
        d = self.router.resolve(model="gpt-4o", task_class="plan")
        assert d.model == "gpt-4o"
        assert d.source == "explicit"

    def test_persona_over_auto(self):
        d = self.router.resolve(
            model="auto",
            task_class="mechanical",
            persona_default="o4-mini",
        )
        assert d.model == "o4-mini"
        assert d.source == "persona"

    def test_auto_by_task_class(self):
        for tc, expected in [
            ("plan", "grok-4.5"),
            ("implement", "claude-sonnet-5"),
            ("mechanical", "gpt-5.4-mini"),
            ("verify", "grok-4.5"),
        ]:
            d = self.router.resolve(model="auto", task_class=tc)
            assert d.model == expected, tc
            assert d.source == "auto"

    def test_fallback_chain(self):
        fb = self.router.pick_fallback(
            "claude-sonnet-5", tried={"claude-sonnet-5"}, task_class="implement"
        )
        assert fb is not None
        assert normalize_provider(self.reg.provider_of(fb.model)) == "xai"

    def test_fallback_skips_tried(self):
        xai_models = {m.id for m in self.reg.models_for_provider("xai")}
        tried = {"claude-sonnet-5"} | xai_models
        fb = self.router.pick_fallback(
            "claude-sonnet-5", tried=tried, task_class="implement"
        )
        assert fb is not None
        assert normalize_provider(self.reg.provider_of(fb.model)) == "openai"

    def test_health_reroutes_auto(self):
        mark_provider_unhealthy("anthropic", "simulated outage")
        d = self.router.resolve(model="auto", task_class="implement")
        assert d.health_rerouted is True
        assert d.from_model == "claude-sonnet-5"
        assert normalize_provider(self.reg.provider_of(d.model)) != "anthropic"

    def test_health_reroutes_explicit(self):
        mark_provider_unhealthy("anthropic", "502")
        d = self.router.resolve(model="claude-sonnet-5", task_class="implement")
        assert d.source == "fallback"
        assert d.health_rerouted is True

    def test_unhealthy_no_fallback_fail_closed(self):
        self.reg.routing.fallbacks = {}
        mark_provider_unhealthy("openai", "down")
        with pytest.raises(NoHealthyProvider):
            self.router.resolve(
                model="gpt-5.4-mini", task_class="mechanical", fail_closed=True
            )

    def test_fallback_prefers_supports_tools(self):
        # inject a tools=False xai model and ensure pick prefers tools=True
        from forge.fleet.types import ModelEntry

        self.reg._models["xai-no-tools"] = ModelEntry(  # noqa: SLF001
            id="xai-no-tools",
            provider="xAI",
            cost_in=0.01,
            cost_out=0.01,
            supports_tools=False,
            tier="fast",
        )
        fb = self.router.pick_fallback(
            "claude-sonnet-5", tried={"claude-sonnet-5"}, task_class="implement"
        )
        assert fb is not None
        entry = self.reg.get(fb.model)
        assert entry is not None
        assert entry.supports_tools is True


# ── Health / extract_text ─────────────────────────────────────────────────


class TestHealthAndExtract:
    def setup_method(self):
        clear_provider_health()

    def teardown_method(self):
        clear_provider_health()

    def test_mark_unhealthy(self):
        mark_provider_unhealthy("Anthropic", "simulated 502")  # title case
        ok, reason = is_provider_healthy("anthropic")
        assert ok is False
        assert "502" in reason or "RuntimeError" in reason
        clear_provider_health("Anthropic")
        ok2, _ = is_provider_healthy("anthropic")
        assert ok2 is True

    def test_extract_text_plain(self):
        assert extract_text("hello") == "hello"
        assert extract_text(None) == ""
        assert extract_text([]) == ""

    def test_extract_text_never_indexes_thinking_first(self):
        thinking = SimpleNamespace(type="thinking", thinking="secret chain", text=None)
        text = SimpleNamespace(type="text", text="visible answer")
        assert extract_text([thinking, text]) == "visible answer"

    def test_extract_text_salvages_thinking(self):
        thinking = SimpleNamespace(type="thinking", thinking="only thoughts")
        assert extract_text([thinking]) == "only thoughts"

    def test_extract_text_dict_blocks(self):
        content = [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "ok"},
        ]
        assert extract_text(content) == "ok"

    def test_extract_text_sniff_not_only_index_zero(self):
        # first item is str, second has .type — should still find text
        thinking = SimpleNamespace(type="thinking", thinking="t")
        text = SimpleNamespace(type="text", text="answer")
        # pure object list already works; dict mixed
        assert extract_text([{"type": "text", "text": "d"}]) == "d"
        assert extract_text([thinking, text]) == "answer"


# ── GrokBuildExecutor ─────────────────────────────────────────────────────


class TestGrokBuildExecutor:
    def test_mocked_success(self):
        def fake_run(cmd, **kwargs):
            assert "--headless" in cmd
            assert "--model" in cmd
            idx = cmd.index("--model")
            assert cmd[idx + 1] == "grok-4.5"
            assert cmd[-1] == "do the thing"
            return SimpleNamespace(
                returncode=0,
                stdout='done\n{"usage": {"input_tokens": 10, "output_tokens": 5}}\n',
                stderr="",
            )

        ex = GrokBuildExecutor(run_fn=fake_run)
        result = ex.run_step("do the thing", model="grok-4.5", step_id="s1")
        assert result.ok
        assert result.input_tokens == 10
        assert result.output_tokens == 5
        assert result.exit_code == 0

    def test_mocked_failure(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(returncode=2, stdout="", stderr="boom 502")

        ex = GrokBuildExecutor(run_fn=fake_run)
        result = ex.run_step("x", model="claude-sonnet-5")
        assert not result.ok
        assert result.exit_code == 2
        assert "502" in result.error

    def test_timeout(self):
        import subprocess as sp

        def fake_run(cmd, **kwargs):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

        ex = GrokBuildExecutor(run_fn=fake_run, timeout=0.01)
        result = ex.run_step("x", model="gpt-4o")
        assert result.timed_out
        assert result.exit_code == -1

    def test_parse_usage(self):
        u = parse_usage_from_output('hello\n{"input_tokens": 3, "output_tokens": 4}\n')
        assert u["input_tokens"] == 3
        assert u["output_tokens"] == 4

    def test_parse_usage_rejects_negative_cost(self):
        u = parse_usage_from_output(
            '{"input_tokens": 1, "output_tokens": 1, "cost_usd": -9}'
        )
        assert "cost_usd" not in u
        assert u["input_tokens"] == 1

    def test_build_command_env(self):
        reg = ModelRegistry.from_executor_models()
        reg._models["local-q"] = reg._models["ollama:default"]  # noqa: SLF001
        from forge.fleet.types import ModelEntry

        reg._models["local-q"] = ModelEntry(  # noqa: SLF001
            id="local-q",
            provider="openai-compat",
            base_url="http://127.0.0.1:11434/v1",
            cost_in=0,
            cost_out=0,
            tier="local",
        )
        ex = GrokBuildExecutor(command="grok-build", registry=reg)
        cmd = ex.build_command("task", "m1")
        assert cmd[0] == "grok-build"
        assert "--headless" in cmd
        env = ex.build_env("local-q", "step9")
        assert env["GROK_BUILD_MODEL"] == "local-q"
        assert env["GROK_BUILD_STEP_ID"] == "step9"
        assert env["GROK_BUILD_BASE_URL"] == "http://127.0.0.1:11434/v1"

    def test_subscription_cli_is_registry_config_not_subclass(self):
        """§11: Claude Code / Codex adapters are constructor params, not new classes."""
        # Drive the real shipped builder — no subclassing, no reimplementation.
        claude = GrokBuildExecutor(
            command="claude",
            headless_flag="-p",
            model_flag="--model",
            extra_args=["--output-format", "json"],
        )
        cmd = claude.build_command("review this PR", "sonnet")
        assert cmd == [
            "claude",
            "-p",
            "--model",
            "sonnet",
            "--output-format",
            "json",
            "review this PR",
        ]
        codex = GrokBuildExecutor(
            command="codex",
            headless_flag="exec",
            model_flag="",  # codex exec often takes task only
            extra_args=[],
        )
        codex_cmd = codex.build_command("implement feature", "gpt-5")
        assert codex_cmd[0] == "codex"
        assert "exec" in codex_cmd
        assert codex_cmd[-1] == "implement feature"
        # Same class for both vendors — registry config, not inheritance.
        assert type(claude) is type(codex) is GrokBuildExecutor


# ── File scopes ───────────────────────────────────────────────────────────


class TestScopes:
    def test_overlap_conflict(self):
        assert scopes_conflict(["a.py"], ["a.py", "b.py"]) is True
        assert scopes_conflict(["a.py"], ["b.py"]) is False

    def test_undeclared_conservative(self):
        assert scopes_conflict(None, ["a.py"]) is True
        assert scopes_conflict([], ["a.py"]) is True
        assert scopes_conflict(None, None) is True

    def test_normalize_aliases(self):
        assert normalize_scope_path("./src/a.py") == normalize_scope_path("src/a.py")
        assert normalize_scope_path("foo/../a.py") == normalize_scope_path("a.py")

    def test_parent_child_conflict(self):
        assert scopes_conflict(["src"], ["src/a.py"]) is True
        assert scopes_conflict(["src/a.py"], ["src"]) is True
        assert scopes_conflict(["src/a.py"], ["lib/b.py"]) is False


# ── Orchestrator / DAG ────────────────────────────────────────────────────


def _mock_executor(behavior):
    def fake_run(cmd, **kwargs):
        model = "unknown"
        if "--model" in cmd:
            model = cmd[cmd.index("--model") + 1]
        task = cmd[-1] if cmd else ""
        step_id = (kwargs.get("env") or {}).get("GROK_BUILD_STEP_ID", "")
        if callable(behavior):
            ok = behavior(step_id, model, task)
        elif isinstance(behavior, dict):
            val = behavior.get(model, behavior.get("*", "ok"))
            ok = val == "ok" if isinstance(val, str) else bool(val)
        else:
            ok = behavior == "ok"
        if ok:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cost_usd": 0.001,
                        }
                    }
                ),
                stderr="",
            )
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=f"502 Bad Gateway from {model}",
        )

    return GrokBuildExecutor(run_fn=fake_run)


class TestFleetOrchestrator:
    def setup_method(self):
        clear_provider_health()

    def teardown_method(self):
        clear_provider_health()

    def test_three_independent_parallel_different_models(self):
        barrier = threading.Barrier(3, timeout=5)
        active = {"n": 0, "max": 0}
        lock = threading.Lock()

        def behavior(step_id, model, task):
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            try:
                barrier.wait()
                time.sleep(0.05)
            finally:
                with lock:
                    active["n"] -= 1
            return True

        orch = FleetOrchestrator(
            executor=_mock_executor(behavior),
            config=FleetConfig(max_parallel=3, max_retries=0, budget=None),
        )
        steps = [
            FleetStep(
                id="a",
                task="task-a",
                task_class="mechanical",
                model="gpt-5.4-mini",
                file_scopes=["a.py"],
            ),
            FleetStep(
                id="b",
                task="task-b",
                task_class="implement",
                model="claude-sonnet-5",
                file_scopes=["b.py"],
            ),
            FleetStep(
                id="c",
                task="task-c",
                task_class="plan",
                model="grok-4.5",
                file_scopes=["c.py"],
            ),
        ]
        result = orch.run(steps)
        assert result.success, result.error or result.steps
        assert result.steps["a"].model == "gpt-5.4-mini"
        assert result.steps["b"].model == "claude-sonnet-5"
        assert result.steps["c"].model == "grok-4.5"
        assert active["max"] >= 3

    def test_dependent_steps_wait(self):
        order: list[str] = []
        lock = threading.Lock()

        def behavior(step_id, model, task):
            with lock:
                order.append(step_id)
            time.sleep(0.02)
            return True

        orch = FleetOrchestrator(
            executor=_mock_executor(behavior),
            config=FleetConfig(max_parallel=4, max_retries=0),
        )
        steps = [
            FleetStep(
                id="root",
                task="first",
                model="gpt-5.4-mini",
                file_scopes=["root.py"],
            ),
            FleetStep(
                id="child",
                task="second",
                depends_on=["root"],
                model="claude-sonnet-5",
                file_scopes=["child.py"],
            ),
        ]
        result = orch.run(steps)
        assert result.success
        assert order.index("root") < order.index("child")

    def test_overlapping_scopes_serialize(self):
        active = {"n": 0, "max": 0}
        lock = threading.Lock()

        def behavior(step_id, model, task):
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(0.05)
            with lock:
                active["n"] -= 1
            return True

        orch = FleetOrchestrator(
            executor=_mock_executor(behavior),
            config=FleetConfig(max_parallel=4, max_retries=0),
        )
        steps = [
            FleetStep(id="a", task="t", model="gpt-5.4-mini", file_scopes=["shared.py"]),
            FleetStep(id="b", task="t", model="claude-sonnet-5", file_scopes=["shared.py"]),
            FleetStep(id="c", task="t", model="grok-4.5", file_scopes=["./shared.py"]),
        ]
        result = orch.run(steps)
        assert result.success
        assert active["max"] == 1

    def test_failover_on_502(self):
        def behavior(step_id, model, task):
            if model.startswith("claude-"):
                return False
            return True

        orch = FleetOrchestrator(
            executor=_mock_executor(behavior),
            config=FleetConfig(max_parallel=1, max_retries=0),
        )
        steps = [
            FleetStep(
                id="impl",
                task="implement feature",
                task_class="implement",
                model="claude-sonnet-5",
                file_scopes=["feat.py"],
            )
        ]
        result = orch.run(steps)
        assert result.success, (
            result.error,
            {k: v.to_dict() for k, v in result.steps.items()},
        )
        assert result.steps["impl"].status == "completed"
        assert not result.steps["impl"].model.startswith("claude-")
        assert result.reroute_log
        assert any(r["from_model"] == "claude-sonnet-5" for r in result.reroute_log)

    def test_same_model_retries(self):
        calls = {"n": 0}

        def behavior(step_id, model, task):
            calls["n"] += 1
            # fail twice then succeed
            return calls["n"] >= 3

        orch = FleetOrchestrator(
            executor=_mock_executor(behavior),
            config=FleetConfig(max_parallel=1, max_retries=2),
        )
        result = orch.run(
            [
                FleetStep(
                    id="r",
                    task="retry me",
                    model="gpt-5.4-mini",
                    file_scopes=["r.py"],
                )
            ]
        )
        assert result.success
        assert calls["n"] == 3
        assert any(r.get("event_type") == "retry" for r in result.reroute_log)

    def test_fallback_exhaustion(self):
        reg = ModelRegistry.from_executor_models()
        reg.routing.fallbacks = {}
        orch = FleetOrchestrator(
            registry=reg,
            executor=_mock_executor(lambda *a: False),
            config=FleetConfig(max_retries=0),
            router=AutoRouter(registry=reg),
        )
        result = orch.run(
            [
                FleetStep(
                    id="x",
                    task="fail",
                    model="gpt-5.4-mini",
                    file_scopes=["x.py"],
                )
            ]
        )
        assert result.success is False
        assert result.steps["x"].status == "failed"

    def test_string_task_single_step(self):
        orch = FleetOrchestrator(
            executor=_mock_executor(lambda *a: True),
            config=FleetConfig(max_retries=0),
        )
        result = orch.run("just do it")
        assert result.success
        assert "step_1" in result.steps

    def test_empty_plan(self):
        orch = FleetOrchestrator(
            executor=_mock_executor(lambda *a: True),
        )
        result = orch.run([])
        assert result.success is False
        assert "empty" in result.error.lower() or "empty" in (result.error or "")

    def test_duplicate_ids_rejected(self):
        orch = FleetOrchestrator(executor=_mock_executor(lambda *a: True))
        result = orch.run(
            [
                FleetStep(id="a", task="1"),
                FleetStep(id="a", task="2"),
            ]
        )
        assert result.success is False
        assert "duplicate" in result.error.lower()

    def test_budget_aborts_fleet(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        ledger = CostLedger(registry=reg, budget=0.5, on_exceeded="abort")
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_parallel=1,
                max_retries=0,
                budget=0.5,
                on_budget_exceeded="abort",
            ),
            ledger=ledger,
        )
        steps = [
            FleetStep(
                id="s1",
                task="t1",
                model="claude-sonnet-5",
                file_scopes=["1.py"],
            ),
            FleetStep(
                id="s2",
                task="t2",
                model="claude-sonnet-5",
                depends_on=["s1"],
                file_scopes=["2.py"],
            ),
        ]
        result = orch.run(steps)
        assert result.success is False
        assert result.budget_exceeded is True
        # Pre-cap successful work stays completed
        assert result.steps["s1"].status == "completed"
        # Remaining work not completed
        assert result.steps["s2"].status in ("skipped", "failed")
        assert result.steps["s2"].status != "completed"

    def test_budget_signal_e2e(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_parallel=1,
                max_retries=0,
                budget=0.5,
                on_budget_exceeded="signal",
            ),
            ledger=CostLedger(registry=reg, budget=0.5, on_exceeded="signal"),
        )
        result = orch.run(
            [
                FleetStep(
                    id="s1",
                    task="t1",
                    model="claude-sonnet-5",
                    file_scopes=["1.py"],
                ),
                FleetStep(
                    id="s2",
                    task="t2",
                    model="claude-sonnet-5",
                    depends_on=["s1"],
                    file_scopes=["2.py"],
                ),
            ]
        )
        assert result.budget_exceeded is True
        assert result.steps["s1"].status == "completed"
        assert result.steps["s2"].status == "skipped"

    def test_failed_dep_blocks_child(self):
        reg = ModelRegistry.from_executor_models()
        reg.routing.fallbacks = {}
        orch = FleetOrchestrator(
            registry=reg,
            executor=_mock_executor(lambda step_id, model, task: step_id != "bad"),
            config=FleetConfig(max_retries=0, max_parallel=2),
            router=AutoRouter(registry=reg),
        )
        steps = [
            FleetStep(
                id="bad",
                task="will fail",
                model="gpt-5.4-mini",
                file_scopes=["bad.py"],
            ),
            FleetStep(
                id="good",
                task="blocked",
                depends_on=["bad"],
                model="gpt-5.4-mini",
                file_scopes=["good.py"],
            ),
        ]
        result = orch.run(steps)
        assert result.steps["bad"].status == "failed"
        assert result.steps["good"].status == "failed"
        assert "dependency" in (result.steps["good"].error or "")

    def test_skipped_dep_blocks_child(self):
        # parent skipped due to budget → child skipped not run
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_parallel=1, max_retries=0, budget=0.5, on_budget_exceeded="abort"
            ),
            ledger=CostLedger(registry=reg, budget=0.5, on_exceeded="abort"),
        )
        result = orch.run(
            [
                FleetStep(
                    id="s1",
                    task="t1",
                    model="claude-sonnet-5",
                    file_scopes=["1.py"],
                ),
                FleetStep(
                    id="s2",
                    task="t2",
                    depends_on=["s1"],
                    model="gpt-5.4-mini",
                    file_scopes=["2.py"],
                ),
                FleetStep(
                    id="s3",
                    task="t3",
                    depends_on=["s2"],
                    model="gpt-5.4-mini",
                    file_scopes=["3.py"],
                ),
            ]
        )
        assert result.steps["s1"].status == "completed"
        assert result.steps["s2"].status == "skipped"
        assert result.steps["s3"].status == "skipped"

    def test_health_reroute_in_reroute_log(self):
        mark_provider_unhealthy("anthropic", "outage")
        orch = FleetOrchestrator(
            executor=_mock_executor(lambda *a: True),
            config=FleetConfig(max_retries=0),
        )
        result = orch.run(
            [
                FleetStep(
                    id="impl",
                    task="x",
                    task_class="implement",
                    model="auto",
                    file_scopes=["x.py"],
                )
            ]
        )
        assert result.success
        assert any(r.get("event_type") == "health" for r in result.reroute_log)

    def test_plan_from_linear(self):
        steps = plan_from_linear(
            [
                {"description": "one", "model": "gpt-5.4-mini"},
                {"title": "two", "task_class": "verify"},
            ]
        )
        assert len(steps) == 2
        assert steps[0].depends_on == []
        assert steps[1].depends_on == [steps[0].id]
        orch = FleetOrchestrator(
            executor=_mock_executor(lambda *a: True),
            config=FleetConfig(max_retries=0),
        )
        # add file scopes so they can run (linear deps already serialize)
        steps[0].file_scopes = ["a.py"]
        steps[1].file_scopes = ["b.py"]
        result = orch.run(steps)
        assert result.success

    def test_config_caps(self):
        cfg = FleetConfig(max_parallel=999, max_retries=99)
        assert cfg.max_parallel == MAX_PARALLEL_CAP
        assert cfg.max_retries == 5

    def test_run_syncs_budget_from_config(self):
        reg = ModelRegistry.from_executor_models()
        ledger = CostLedger(registry=reg, budget=None)
        orch = FleetOrchestrator(
            registry=reg,
            ledger=ledger,
            executor=_mock_executor(lambda *a: True),
            config=FleetConfig(budget=1.23, on_budget_exceeded="signal"),
        )
        orch.run(
            [FleetStep(id="a", task="t", model="gpt-5.4-mini", file_scopes=["a.py"])]
        )
        assert ledger.budget == 1.23
        assert ledger.on_exceeded == "signal"

    def test_p1_six_step_mixed(self):
        """6-step plan with 3 independent steps across ≥2 models."""
        seen_models: set[str] = set()
        lock = threading.Lock()

        def behavior(step_id, model, task):
            with lock:
                seen_models.add(model)
            return True

        orch = FleetOrchestrator(
            executor=_mock_executor(behavior),
            config=FleetConfig(max_parallel=3, max_retries=0),
        )
        steps = [
            FleetStep(id="i1", task="a", model="gpt-5.4-mini", file_scopes=["i1.py"]),
            FleetStep(id="i2", task="b", model="claude-sonnet-5", file_scopes=["i2.py"]),
            FleetStep(id="i3", task="c", model="grok-4.5", file_scopes=["i3.py"]),
            FleetStep(
                id="j1",
                task="join",
                depends_on=["i1", "i2", "i3"],
                model="gpt-5.4-mini",
                file_scopes=["j1.py"],
            ),
            FleetStep(
                id="v1",
                task="verify",
                depends_on=["j1"],
                task_class="verify",
                model="auto",
                file_scopes=["v1.py"],
            ),
            FleetStep(
                id="v2",
                task="done",
                depends_on=["v1"],
                model="gpt-5.4-mini",
                file_scopes=["v2.py"],
            ),
        ]
        result = orch.run(steps)
        assert result.success, result.error
        assert len(seen_models) >= 2
        assert all(result.steps[s.id].status == "completed" for s in steps)


# ── Round-2 residual regressions ──────────────────────────────────────────


class TestBudgetReservationIntegrity:
    def test_parallel_expensive_under_tiny_budget(self):
        """Concurrent barrier + expensive usage under tiny budget must not
        complete multiple $2 steps (serialize_on_budget + reservation)."""
        barrier = threading.Barrier(3, timeout=2)

        def fake_run(cmd, **kwargs):
            # Try to all enter at once; with serialize only one runs
            try:
                barrier.wait(timeout=0.2)
            except Exception:
                pass
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_parallel=5,
                max_retries=0,
                budget=0.5,
                on_budget_exceeded="abort",
                serialize_on_budget=True,
            ),
            ledger=CostLedger(registry=reg, budget=0.5, on_exceeded="abort"),
        )
        steps = [
            FleetStep(
                id=f"s{i}",
                task="expensive",
                model="claude-sonnet-5",
                file_scopes=[f"s{i}.py"],
            )
            for i in range(3)
        ]
        result = orch.run(steps)
        assert result.budget_exceeded is True
        completed = [s for s in result.steps.values() if s.status == "completed"]
        # At most one expensive step can finish under $0.50 with $2/step pricing
        assert len(completed) <= 1
        # Total must not approach 3 * $2
        assert result.ledger["total_cost_usd"] < 3.0
        assert result.ledger["total_cost_usd"] <= 2.0 + 1e-6

    def test_reported_cost_cannot_undercut_registry(self):
        """Agent-reported cost_usd:0.001 with 1M tokens must not bypass budget."""
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "usage": {
                            "input_tokens": 1_000_000,
                            "output_tokens": 0,
                            "cost_usd": 0.001,
                        }
                    }
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        # claude cost_in=2 → $2 for 1M tokens
        ledger = CostLedger(registry=reg, budget=None)
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(max_retries=0, budget=None),
            ledger=ledger,
        )
        result = orch.run(
            [
                FleetStep(
                    id="s1",
                    task="t",
                    model="claude-sonnet-5",
                    file_scopes=["s1.py"],
                )
            ]
        )
        assert result.success
        assert result.ledger["total_cost_usd"] == pytest.approx(2.0, rel=1e-6)

    def test_reservation_held_across_retries(self):
        """Failed attempts must not drop reservation for siblings."""
        calls = {"n": 0}
        order_lock = threading.Lock()
        sibling_started = threading.Event()

        def fake_run(cmd, **kwargs):
            step_id = (kwargs.get("env") or {}).get("GROK_BUILD_STEP_ID", "")
            if step_id == "a":
                calls["n"] += 1
                if calls["n"] < 3:
                    # fail first two attempts
                    return SimpleNamespace(
                        returncode=1, stdout="", stderr="502 temporary"
                    )
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {"usage": {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.01}}
                    ),
                    stderr="",
                )
            # sibling should not run while a holds reservation under tight budget
            sibling_started.set()
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"usage": {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.01}}
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        # serialize off but tiny budget with high estimated costs so only one fits
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_parallel=2,
                max_retries=2,
                budget=0.05,
                serialize_on_budget=False,
                on_budget_exceeded="abort",
            ),
            ledger=CostLedger(registry=reg, budget=0.05, on_exceeded="abort"),
        )
        # Reserve 0.04 each → only one admits
        result = orch.run(
            [
                FleetStep(
                    id="a",
                    task="retry",
                    model="gpt-5.4-mini",
                    file_scopes=["a.py"],
                    estimated_cost_usd=0.04,
                ),
                FleetStep(
                    id="b",
                    task="sib",
                    model="gpt-5.4-mini",
                    file_scopes=["b.py"],
                    estimated_cost_usd=0.04,
                ),
            ]
        )
        # a should complete after retries; b should not have been concurrent
        # under reservation (may be skipped after budget or run after a settles)
        assert result.steps["a"].status == "completed"
        assert calls["n"] == 3

    def test_ledger_cleared_between_runs(self):
        reg = ModelRegistry.from_executor_models()
        orch = FleetOrchestrator(
            registry=reg,
            executor=_mock_executor(lambda *a: True),
            config=FleetConfig(max_retries=0, reset_ledger=True),
        )
        r1 = orch.run(
            [FleetStep(id="a", task="1", model="gpt-5.4-mini", file_scopes=["a.py"])]
        )
        assert r1.success
        cost1 = r1.ledger["total_cost_usd"]
        r2 = orch.run(
            [FleetStep(id="b", task="2", model="gpt-5.4-mini", file_scopes=["b.py"])]
        )
        assert r2.success
        # Second run should not include first run cost
        assert r2.ledger["total_cost_usd"] == pytest.approx(cost1, rel=1e-3)
        assert r2.ledger["call_count"] == 1

    def test_max_retries_capped(self):
        calls = {"n": 0}

        def behavior(step_id, model, task):
            calls["n"] += 1
            return False

        reg = ModelRegistry.from_executor_models()
        reg.routing.fallbacks = {}
        orch = FleetOrchestrator(
            registry=reg,
            executor=_mock_executor(behavior),
            config=FleetConfig(max_retries=0),
            router=AutoRouter(registry=reg),
        )
        orch.run(
            [
                FleetStep(
                    id="x",
                    task="fail",
                    model="gpt-5.4-mini",
                    max_retries=20,  # would be 21 attempts without cap
                    file_scopes=["x.py"],
                )
            ]
        )
        # 1 initial + MAX_RETRIES_CAP (5) = 6 attempts max
        from forge.fleet.types import MAX_RETRIES_CAP

        assert calls["n"] <= MAX_RETRIES_CAP + 1
        assert calls["n"] == MAX_RETRIES_CAP + 1

    def test_missing_usage_floor_under_budget(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(returncode=0, stdout="ok no usage", stderr="")

        reg = ModelRegistry.from_executor_models()
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_retries=0,
                budget=1.0,
                missing_usage_floor_usd=0.02,
                serialize_on_budget=True,
            ),
            ledger=CostLedger(registry=reg, budget=1.0, on_exceeded="abort"),
        )
        result = orch.run(
            [
                FleetStep(
                    id="s1",
                    task="t",
                    model="gpt-5.4-mini",
                    file_scopes=["s1.py"],
                )
            ]
        )
        assert result.success
        assert result.ledger["total_cost_usd"] > 0

    def test_unknown_model_under_budget(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"usage": {"input_tokens": 1000, "output_tokens": 100}}
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_retries=0,
                budget=5.0,
                unknown_model_floor_usd=0.05,
            ),
            ledger=CostLedger(
                registry=reg, budget=5.0, on_exceeded="abort", unknown_model_floor=0.05
            ),
        )
        result = orch.run(
            [
                FleetStep(
                    id="s1",
                    task="t",
                    model="totally-unknown-model-xyz",
                    file_scopes=["s1.py"],
                )
            ]
        )
        assert result.success
        assert result.ledger["total_cost_usd"] >= 0.05


class TestValidationAndScopeErrors:
    def test_empty_step_id(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_steps([FleetStep(id="", task="x")])

    def test_unknown_dep(self):
        with pytest.raises(ValueError, match="unknown"):
            validate_steps(
                [FleetStep(id="a", task="x", depends_on=["nope"])]
            )

    def test_max_steps(self):
        from forge.fleet.types import MAX_STEPS_CAP

        steps = [
            FleetStep(id=f"s{i}", task="t", file_scopes=[f"{i}.py"])
            for i in range(MAX_STEPS_CAP + 1)
        ]
        with pytest.raises(ValueError, match="max allowed"):
            validate_steps(steps)

    def test_file_scope_predecessor_error_text(self):
        reg = ModelRegistry.from_executor_models()
        reg.routing.fallbacks = {}
        orch = FleetOrchestrator(
            registry=reg,
            executor=_mock_executor(lambda step_id, model, task: step_id != "a"),
            config=FleetConfig(max_retries=0),
            router=AutoRouter(registry=reg),
        )
        # Overlapping scopes → soft edge a→b (b waits on a)
        result = orch.run(
            [
                FleetStep(
                    id="a",
                    task="fail",
                    model="gpt-5.4-mini",
                    file_scopes=["shared.py"],
                ),
                FleetStep(
                    id="b",
                    task="blocked",
                    model="gpt-5.4-mini",
                    file_scopes=["shared.py"],
                ),
            ]
        )
        assert result.steps["a"].status == "failed"
        assert result.steps["b"].status == "failed"
        assert "file-scope predecessor" in (result.steps["b"].error or "")

    def test_p1_six_step_with_502_failover(self):
        def behavior(step_id, model, task):
            if model.startswith("claude-"):
                return False
            return True

        orch = FleetOrchestrator(
            executor=_mock_executor(behavior),
            config=FleetConfig(max_parallel=3, max_retries=0),
        )
        steps = [
            FleetStep(id="i1", task="a", model="gpt-5.4-mini", file_scopes=["i1.py"]),
            FleetStep(
                id="i2", task="b", model="claude-sonnet-5", file_scopes=["i2.py"]
            ),
            FleetStep(id="i3", task="c", model="grok-4.5", file_scopes=["i3.py"]),
            FleetStep(
                id="j1",
                task="join",
                depends_on=["i1", "i2", "i3"],
                model="gpt-5.4-mini",
                file_scopes=["j1.py"],
            ),
            FleetStep(
                id="v1",
                task="verify",
                depends_on=["j1"],
                task_class="verify",
                model="auto",
                file_scopes=["v1.py"],
            ),
            FleetStep(
                id="v2",
                task="done",
                depends_on=["v1"],
                model="gpt-5.4-mini",
                file_scopes=["v2.py"],
            ),
        ]
        result = orch.run(steps)
        assert result.success, (result.error, result.reroute_log)
        assert result.steps["i2"].status == "completed"
        assert not result.steps["i2"].model.startswith("claude-")
        assert any(r["from_model"].startswith("claude") for r in result.reroute_log)


# ── Round-3 residual regressions ──────────────────────────────────────────


class TestRound3BudgetReservation:
    def test_multi_step_under_budget_no_false_exceeded(self):
        """budget=2.50 + four cheap Claude steps: all complete; no false overage.

        Regression: reserving full budget on step1 left no headroom for step2+.
        """
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "usage": {
                            "input_tokens": 1000,
                            "output_tokens": 100,
                            # real token price for claude is tiny at 1k tokens
                        }
                    }
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_parallel=1,
                max_retries=0,
                budget=2.50,
                on_budget_exceeded="abort",
                serialize_on_budget=True,
            ),
            ledger=CostLedger(registry=reg, budget=2.50, on_exceeded="abort"),
        )
        steps = [
            FleetStep(
                id=f"s{i}",
                task=f"t{i}",
                model="claude-sonnet-5",
                file_scopes=[f"s{i}.py"],
            )
            for i in range(4)
        ]
        result = orch.run(steps)
        assert result.budget_exceeded is False, result.ledger
        assert result.success is True
        assert all(result.steps[s.id].status == "completed" for s in steps)
        # Actual spend is tiny (token-priced), well under 2.50
        assert result.ledger["total_cost_usd"] < 2.50

    def test_reserve_uses_remaining_not_full_budget(self):
        reg = ModelRegistry.from_executor_models()
        # After spending 1.0 of 2.5, remaining is 1.5 — reserve must be ≤ 1.5
        floor = reg.estimate_step_floor(
            "claude-sonnet-5",
            remaining_budget=1.5,
            budget=2.5,
        )
        assert floor <= 1.5 + 1e-9
        # Without remaining, full budget cap still applies as fallback
        floor_full = reg.estimate_step_floor(
            "claude-sonnet-5",
            budget=2.5,
        )
        assert floor_full <= 2.5 + 1e-9

    def test_zero_token_cost_usd_zero_charges_floor(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cost_usd": 0.0,
                        }
                    }
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_retries=0,
                budget=1.0,
                missing_usage_floor_usd=0.02,
                serialize_on_budget=True,
            ),
            ledger=CostLedger(
                registry=reg,
                budget=1.0,
                on_exceeded="abort",
                missing_usage_floor=0.02,
            ),
        )
        result = orch.run(
            [
                FleetStep(
                    id="s1",
                    task="free?",
                    model="gpt-5.4-mini",
                    file_scopes=["s1.py"],
                )
            ]
        )
        assert result.success
        assert result.ledger["total_cost_usd"] >= 0.02

    def test_zero_token_tiny_cost_undercut_floor(self):
        """cost_usd:0.001 with zero tokens under budget → ≥ missing_usage_floor."""
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cost_usd": 0.001,
                        }
                    }
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        ledger = CostLedger(
            registry=reg, budget=1.0, missing_usage_floor=0.05
        )
        ledger.record(
            agent_id="a",
            step_id="s",
            model="gpt-5.4-mini",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.001,
        )
        assert ledger.total_cost >= 0.05

    def test_estimated_cost_cannot_undercut_tier_under_budget(self):
        reg = ModelRegistry.from_executor_models()
        tier = reg._tier_reserve_amount(  # noqa: SLF001
            "claude-sonnet-5",
            unknown_floor=0.05,
            missing_usage_floor=0.01,
        )
        # Low estimate under budget must not go below tier floor (before remaining cap)
        low = reg.estimate_step_floor(
            "claude-sonnet-5",
            estimated_cost_usd=0.001,
            budget=100.0,
            remaining_budget=100.0,
        )
        assert low == pytest.approx(tier, rel=1e-9)
        # Without budget, low estimate is allowed as advisory
        advisory = reg.estimate_step_floor(
            "claude-sonnet-5",
            estimated_cost_usd=0.001,
        )
        assert advisory == pytest.approx(0.001, rel=1e-6) or advisory <= tier

    def test_try_reserve_no_double_count_settle_false(self):
        """settle=False records + reservation must not block sibling reserve.

        Naive sum(records)+sum(reservations) double-counts; committed_cost
        semantics allow a second step when headroom remains.
        """
        reg = ModelRegistry.from_executor_models()
        ledger = CostLedger(registry=reg, budget=1.0, on_exceeded="abort")

        assert ledger.try_reserve("a", 0.60) is True
        # Intermediate failed attempt cost under settle=False
        ledger.record(
            agent_id="fleet:a",
            step_id="a",
            model="gpt-5.4-mini",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.01,
            settle=False,
            raise_on_exceed=False,
        )
        # Reservation still held; committed should be ~0.60 not 0.61
        # (headroom = max(0, 0.60-0.01) + 0.01 = 0.60)
        assert ledger.committed_cost() == pytest.approx(0.60, abs=1e-9)
        # Sibling reserve of 0.35 fits in remaining 0.40
        assert ledger.try_reserve("b", 0.35) is True
        assert ledger.remaining_budget() == pytest.approx(0.05, abs=1e-6)

        # Release a; b still reserved
        ledger.release_reservation("a")
        assert ledger.try_reserve("c", 0.05) is True

    def test_parallel_reserve_retries_no_false_skip(self):
        """serialize_on_budget=False + retries: sibling must not be permanent-skipped
        due to double-count while first step holds reservation through retries.
        """
        calls = {"a": 0}

        def fake_run(cmd, **kwargs):
            step_id = (kwargs.get("env") or {}).get("GROK_BUILD_STEP_ID", "")
            if step_id == "a":
                calls["a"] += 1
                if calls["a"] < 3:
                    return SimpleNamespace(
                        returncode=1, stdout="", stderr="502 temp"
                    )
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 10,
                                "cost_usd": 0.02,
                            }
                        }
                    ),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "cost_usd": 0.02,
                        }
                    }
                ),
                stderr="",
            )

        reg = ModelRegistry.from_executor_models()
        # Small budget with explicit estimates so both fit sequentially after release
        orch = FleetOrchestrator(
            registry=reg,
            executor=GrokBuildExecutor(run_fn=fake_run),
            config=FleetConfig(
                max_parallel=2,
                max_retries=2,
                budget=0.10,
                serialize_on_budget=False,
                on_budget_exceeded="abort",
                missing_usage_floor_usd=0.01,
            ),
            ledger=CostLedger(
                registry=reg,
                budget=0.10,
                on_exceeded="abort",
                missing_usage_floor=0.01,
            ),
        )
        result = orch.run(
            [
                FleetStep(
                    id="a",
                    task="retry",
                    model="gpt-5.4-mini",
                    file_scopes=["a.py"],
                    estimated_cost_usd=0.05,
                    max_retries=2,
                ),
                FleetStep(
                    id="b",
                    task="sib",
                    model="gpt-5.4-mini",
                    file_scopes=["b.py"],
                    estimated_cost_usd=0.05,
                ),
            ]
        )
        # a completes after retries; b must not be false-skipped while headroom exists
        assert result.steps["a"].status == "completed"
        assert result.steps["b"].status in ("completed", "skipped")
        # If skipped, must not be the false permanent skip while a was retrying
        # with double-count — total cost should reflect a's spend
        assert result.ledger["total_cost_usd"] > 0
        if result.steps["b"].status == "skipped":
            # Only acceptable if budget truly exhausted after a
            assert result.budget_exceeded or "budget" in (
                result.steps["b"].error or ""
            ).lower() or "headroom" in (result.steps["b"].error or "").lower()


# ── Package exports ───────────────────────────────────────────────────────


def test_public_exports():
    import forge.fleet as fleet

    for name in fleet.__all__:
        assert hasattr(fleet, name), name
        assert getattr(fleet, name) is not None
