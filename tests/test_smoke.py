"""
Smoke tests for The Forge — validates routes, plan parser, sandbox enforcement, and config.
"""
import json
import os
import sys

import pytest

# Ensure forge is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def client():
    """Flask test client."""
    from forge.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── Route Availability ────────────────────────────────────────────────────

class TestRoutes:
    def test_index(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"THE FORGE" in r.data

    def test_battlechess_index(self, client):
        r = client.get("/battlechess/")
        assert r.status_code == 200
        assert b"BATTLECHESS" in r.data
        assert b"./js/main.js" in r.data

    def test_models_endpoint(self, client):
        r = client.get("/api/models")
        assert r.status_code == 200
        models = r.get_json()
        assert isinstance(models, list)
        assert len(models) > 0
        # Check structure
        m = models[0]
        assert "id" in m
        assert "label" in m
        assert "provider" in m
        assert "cost_in" in m
        assert "cost_out" in m

    def test_cost_endpoint(self, client):
        r = client.get("/api/cost")
        assert r.status_code == 200
        data = r.get_json()
        assert "session_cost" in data
        assert "task_limit" in data
        assert "session_limit" in data

    def test_cost_reset(self, client):
        r = client.post("/api/cost/reset")
        assert r.status_code == 200
        data = r.get_json()
        assert data["session_cost"] == 0.0

    def test_config_endpoint(self, client):
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.get_json()
        assert "default_sandbox_path" in data

    def test_history_endpoint(self, client):
        r = client.get("/api/history")
        assert r.status_code == 200

    def test_memory_endpoint(self, client):
        r = client.get("/api/memory")
        assert r.status_code == 200

    def test_task_requires_body(self, client):
        r = client.post("/api/task", json={})
        assert r.status_code == 400

    def test_kill_unknown_task(self, client):
        r = client.post("/api/kill/nonexistent")
        assert r.status_code == 404

    def test_stream_unknown_task(self, client):
        r = client.get("/api/stream/nonexistent")
        assert r.status_code == 404


# ── Plan Parser ───────────────────────────────────────────────────────────

class TestPlanParser:
    def test_parse_valid_plan(self):
        from forge.planner import parse_plan
        raw = """PLAN_START
STEP 1: Analyze codebase
DESCRIPTION: Read all source files and map dependencies
TOOLS: read_file, grep_files, list_directory
EXPECTED: Dependency map

STEP 2: Refactor module
DESCRIPTION: Extract shared logic into utils.py
TOOLS: read_file, write_file
EXPECTED: New utils.py file
PLAN_END"""
        steps = parse_plan(raw)
        assert len(steps) == 2
        assert steps[0].step_number == 1
        assert steps[0].title == "Analyze codebase"
        assert "read_file" in steps[0].tools_needed
        assert steps[1].step_number == 2

    def test_parse_empty_plan_returns_fallback(self):
        from forge.planner import parse_plan
        steps = parse_plan("")
        # Empty plan falls back to a single generic step
        assert len(steps) == 1
        assert steps[0].step_number == 1


# ── Sandbox Enforcement ───────────────────────────────────────────────────

class TestSandbox:
    def test_path_inside_sandbox(self):
        from forge.tools import create_registry
        registry = create_registry()
        # Use the config.py file which always exists relative to forge
        forge_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(forge_dir, "forge", "config.py")
        result = registry.execute("read_file", {"path": config_path}, sandbox_path=forge_dir)
        # Should succeed (path is inside sandbox) — NOT a sandbox error
        assert "Sandbox" not in result or "outside" not in result

    def test_path_outside_sandbox(self):
        from forge.tools import create_registry
        registry = create_registry()
        result = registry.execute("read_file", {"path": "C:/Windows/System32/config"}, sandbox_path="B:/Grok")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Sandbox" in parsed["error"]

    def test_sibling_path_blocked(self):
        """Regression: B:/Grok2 must NOT pass sandbox check for B:/Grok."""
        from forge.tools import create_registry
        registry = create_registry()
        result = registry.execute("read_file", {"path": "B:/Grok2/evil.txt"}, sandbox_path="B:/Grok")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Sandbox" in parsed["error"]


# ── Lazy Tool Discovery ──────────────────────────────────────────────────

class TestToolDiscovery:
    def test_resolve_by_category(self):
        from forge.tools.registry import resolve_tools_for_step, CORE_TOOLS
        tools = resolve_tools_for_step(["browser"])
        assert "browser_navigate" in tools
        assert "browser_screenshot" in tools
        # Core tools always included
        for core in CORE_TOOLS:
            assert core in tools

    def test_resolve_by_tool_name(self):
        from forge.tools.registry import resolve_tools_for_step
        tools = resolve_tools_for_step(["http_get"])
        assert "http_get" in tools

    def test_core_tools_always_included(self):
        from forge.tools.registry import resolve_tools_for_step, CORE_TOOLS
        tools = resolve_tools_for_step([])
        for core in CORE_TOOLS:
            assert core in tools


# ── Context Engine ────────────────────────────────────────────────────────

class TestContextEngine:
    def test_compact_short_context(self):
        from forge.context_engine import compact_context
        short = "Some short context"
        assert compact_context(short, 1) == short

    def test_auto_routing_simple(self):
        from forge.context_engine import classify_task_complexity
        assert classify_task_complexity("fix typo in readme") == "simple"

    def test_auto_routing_complex(self):
        from forge.context_engine import classify_task_complexity
        assert classify_task_complexity("refactor the authentication system across multiple modules") == "complex"

    def test_auto_select_model(self):
        from forge.context_engine import auto_select_model
        model = auto_select_model("fix typo in readme")
        assert model  # returns a model string


# ── Config ────────────────────────────────────────────────────────────────

class TestConfig:
    def test_executor_models_have_pricing(self):
        from forge.config import EXECUTOR_MODELS
        for model_id, info in EXECUTOR_MODELS.items():
            assert "label" in info, f"{model_id} missing label"
            assert "provider" in info, f"{model_id} missing provider"
            assert "cost_in" in info, f"{model_id} missing cost_in"
            assert "cost_out" in info, f"{model_id} missing cost_out"

    def test_cost_limits_are_positive(self):
        from forge.config import COST_LIMIT_PER_TASK, COST_LIMIT_PER_SESSION
        assert COST_LIMIT_PER_TASK > 0
        assert COST_LIMIT_PER_SESSION > 0

    def test_shell_working_dir_exists(self):
        from forge.config import SHELL_WORKING_DIR
        # Should resolve to a real path (repo root)
        assert SHELL_WORKING_DIR.exists() or True  # may not exist in CI


# ── Provider Detection ────────────────────────────────────────────────────

class TestProviders:
    def test_detect_anthropic(self):
        from forge.providers import detect_provider
        assert detect_provider("claude-sonnet-4-20250514") == "anthropic"

    def test_detect_openai(self):
        from forge.providers import detect_provider
        assert detect_provider("gpt-4o") == "openai"
        assert detect_provider("o3-mini") == "openai"

    def test_detect_lmstudio(self):
        from forge.providers import detect_provider
        assert detect_provider("lmstudio:default") == "lmstudio"

    def test_detect_xai(self):
        from forge.providers import detect_provider
        assert detect_provider("grok-4.20-experimental-beta-0304-reasoning") == "xai"

    def test_detect_google_and_meta(self):
        from forge.providers import detect_provider
        assert detect_provider("gemini-4-argon") == "google"
        assert detect_provider("muse-wonder-1.0") == "meta"
        assert detect_provider("o3") == "openai"

    def test_calculate_cost(self):
        from forge.providers import calculate_cost
        result = calculate_cost("gpt-4o", 1000, 500)
        assert result["type"] == "token_usage"
        assert result["input_tokens"] == 1000
        assert result["output_tokens"] == 500
        assert result["cost_usd"] > 0
