"""
Flask web server for The Forge.

Routes:
  GET  /                  → serves the SPA
  POST /api/task          → submit a task, returns task_id (optional "pack" field)
  POST /api/arena         → launch arena deathmatch, returns task_id
  GET  /api/stream/<id>   → SSE stream of task progress
  POST /api/kill/<id>     → cancel a running task
  GET  /api/history       → recent completed tasks
  GET  /api/packs         → list all capability packs with readiness
  GET  /api/packs/<name>  → single pack details
"""
import sys
import os
import json
import logging
import threading
import time
import uuid
from queue import Queue

from flask import Flask, request, jsonify, Response, send_from_directory

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure forge package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge.orchestrator import Orchestrator
from forge.memory import save_task, get_recent_tasks
from forge.models import TaskResult, make_msg
from forge.run_log import RunLog, load_run_events, load_run_meta, get_run_artifacts
from forge.config import (
    EXECUTOR_MODELS, COST_LIMIT_PER_TASK, COST_LIMIT_PER_SESSION,
    SHELL_WORKING_DIR, TOLL_ENABLED, MARKETPLACE_ENABLED, SOLANA_WATCHER_ENABLED,
    EMAIL_AGENT_ENABLED, ARCRELAY_WEBHOOK_SECRET, ARCRELAY_API_KEY,
    ARCRELAY_API_URL, EMAIL_AGENT_MODEL, EXECUTOR_MODEL, PLANNER_MODEL,
    PLANNER_AGENT_COUNT, EXECUTOR_MAX_ITERATIONS,
    USER_CORRECTION_ENABLED, GENERATIVE_UI_ENABLED, TRADING_ENABLED,
    PROPHECY_ENABLED, SURGEON_ENABLED, ARENA_SWARM_ENABLED,
    AUTH_ENABLED, SCHEDULER_ENABLED, CONVERSATIONS_ENABLED,
    OBSERVABILITY_ENABLED, PUBLIC_MODE,
    MCP_ENABLED, MCP_AUTO_SYNC_ENABLED, BLENDER_PACK_ENABLED,
    SALESFORCE_PACK_ENABLED,
)
from forge.toll.endpoints import toll_bp
from forge.toll.public_api import public_bp
from forge.packs import get_registry as get_pack_registry
from forge import task_state
from forge.providers import health_snapshot as provider_health_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("forge.app")

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Module-level singletons that other code (routes, shutdown handlers) need
# to reach. Populated by register_modules() on import.
_email_agent = None
_scheduler = None
_auth = None


def _mcp_api_summary() -> dict:
    """Serialize MCP router state for /api/config."""
    if not MCP_ENABLED:
        return {"enabled": False}
    try:
        from forge.mcp_client import get_router
        router = get_router()
        return {
            "enabled": True,
            "auto_sync": MCP_AUTO_SYNC_ENABLED,
            "active_namespaces": router.active_namespaces(),
            "servers": router.configured_servers(),
        }
    except Exception as e:
        return {"enabled": True, "error": f"{type(e).__name__}: {e}"}


# ── MCP health cache ────────────────────────────────────────────────────
#
# Live-pinging external MCP servers spawns a subprocess and takes 1-3s per
# call. Every hit would storm the system on page load, so we cache the
# result for _MCP_STATUS_CACHE_TTL seconds keyed by server name. `?live=true`
# forces a refresh. Servers never pinged return `reachable: null` so the UI
# can render them as "unknown" rather than "down".

_MCP_STATUS_CACHE_TTL = 5.0
_mcp_status_cache: dict = {}
_mcp_status_cache_lock = threading.Lock()


def _ping_mcp_server(server_name: str, cfg: dict) -> dict:
    """Spawn the MCP server, list tools, return a health snapshot dict.

    Never raises — all exit paths produce a dict with `reachable` bool and
    (when relevant) a `last_error` string.
    """
    from forge.mcp_client import list_mcp_tools
    started = time.monotonic()
    command = cfg.get("command") or []
    if not command:
        return {
            "namespace": server_name,
            "enabled": bool(cfg.get("enabled")),
            "reachable": False,
            "ping_ms": None,
            "tool_count": 0,
            "last_error": "no command configured",
            "last_checked_at": time.time(),
        }
    try:
        raw = list_mcp_tools(command=command[0], args=list(command[1:]), init_timeout=8.0)
        ping_ms = int((time.monotonic() - started) * 1000)
        parsed = json.loads(raw)
        if "error" in parsed:
            return {
                "namespace": server_name,
                "enabled": bool(cfg.get("enabled")),
                "reachable": False,
                "ping_ms": ping_ms,
                "tool_count": 0,
                "last_error": parsed["error"],
                "last_checked_at": time.time(),
            }
        tools = parsed.get("tools", []) or []
        return {
            "namespace": server_name,
            "enabled": bool(cfg.get("enabled")),
            "reachable": True,
            "ping_ms": ping_ms,
            "tool_count": len(tools),
            "last_error": None,
            "last_checked_at": time.time(),
        }
    except Exception as e:
        ping_ms = int((time.monotonic() - started) * 1000)
        return {
            "namespace": server_name,
            "enabled": bool(cfg.get("enabled")),
            "reachable": False,
            "ping_ms": ping_ms,
            "tool_count": 0,
            "last_error": f"{type(e).__name__}: {e}",
            "last_checked_at": time.time(),
        }


def register_modules(flask_app: Flask) -> dict:
    """Conditionally register all blueprints + side-effect setups on `flask_app`.

    Wrapping this in a function (instead of executing at module scope):
      - Lets tests construct a Flask app with a curated subset of modules
      - Makes the import side-effect graph explicit and inspectable
      - Returns a dict describing which modules came up, for /api/health

    The blueprints themselves still respect the env-var feature flags read
    from forge.config — flipping a flag and restarting still works.
    """
    global _email_agent, _scheduler, _auth
    enabled: dict[str, bool] = {"toll": True}

    flask_app.register_blueprint(toll_bp)

    if MARKETPLACE_ENABLED:
        flask_app.register_blueprint(public_bp)
        enabled["marketplace"] = True

    # ── Trading Module ──────────────────────────────────────────────────
    if TRADING_ENABLED:
        from forge.trading import check_trading_readiness
        from forge.trading.endpoints import trading_bp
        flask_app.register_blueprint(trading_bp)
        trading_readiness = check_trading_readiness()
        log.info(
            "Trading module enabled (provider=%s, broker=%s, state=%s)",
            trading_readiness["provider"],
            trading_readiness["broker"],
            trading_readiness["state"],
        )
        for issue in trading_readiness["issues"]:
            log.warning("Trading readiness: %s", issue)
        enabled["trading"] = True

    # ── Prophecy Engine ─────────────────────────────────────────────────
    if PROPHECY_ENABLED:
        from forge.prophecy.endpoints import prophecy_bp
        flask_app.register_blueprint(prophecy_bp)
        log.info("Prophecy Engine enabled")
        enabled["prophecy"] = True

    # ── Surgeon (OBLITERATUS) ───────────────────────────────────────────
    if SURGEON_ENABLED:
        from forge.surgeon.endpoints import surgeon_bp
        flask_app.register_blueprint(surgeon_bp)
        log.info("Surgeon module enabled")
        enabled["surgeon"] = True

    # ── MCP Client (multi-node: internal forge:vault / forge:graph + external) ─
    if MCP_ENABLED:
        enabled["mcp"] = True
        try:
            from forge.mcp_client import get_router
            router = get_router()
            log.info("⚡ MCP Client ONLINE — %s | auto-sync=%s",
                     router.summary_banner(),
                     "ON" if MCP_AUTO_SYNC_ENABLED else "off")
            enabled["mcp_namespaces"] = router.active_namespaces()
        except Exception as e:
            log.warning("MCP router init failed: %s: %s", type(e).__name__, e)
        if MCP_AUTO_SYNC_ENABLED:
            try:
                from forge.mcp_client import enable_default_auto_sync
                enable_default_auto_sync()
                enabled["mcp_auto_sync"] = True
                log.info("MCP auto-sync wired: every successful step → forge:vault + forge:graph")
            except Exception as e:
                log.warning("MCP auto-sync init skipped: %s: %s", type(e).__name__, e)

    # ── Email Agent + Webhook ───────────────────────────────────────────
    if EMAIL_AGENT_ENABLED:
        from forge.agents.email_webhook import email_webhook_bp, configure as configure_webhook
        from forge.agents.email_agent import EmailAgent
        _email_agent = EmailAgent(
            api_key=ARCRELAY_API_KEY,
            api_url=ARCRELAY_API_URL,
            model=EMAIL_AGENT_MODEL,
        )
        configure_webhook(ARCRELAY_WEBHOOK_SECRET, _email_agent)
        flask_app.register_blueprint(email_webhook_bp)
        _email_agent.start()
        log.info("Email agent enabled (model=%s)", EMAIL_AGENT_MODEL)
        enabled["email_agent"] = True

    # ── Scheduler ───────────────────────────────────────────────────────
    if SCHEDULER_ENABLED:
        from forge.scheduler import Scheduler, create_blueprint as create_scheduler_bp
        _scheduler = Scheduler()
        flask_app.register_blueprint(create_scheduler_bp(_scheduler))
        _scheduler.start()
        log.info("Scheduler enabled (%d jobs)", len(_scheduler.list_jobs()))
        enabled["scheduler"] = True

    # ── Agent Conversations ─────────────────────────────────────────────
    if CONVERSATIONS_ENABLED:
        from forge.agents.conversation import create_blueprint as create_conv_bp
        flask_app.register_blueprint(create_conv_bp())
        log.info("Agent conversations enabled")
        enabled["conversations"] = True

    # ── Observability ───────────────────────────────────────────────────
    if OBSERVABILITY_ENABLED:
        from forge.observability import init_observability
        init_observability(flask_app)
        log.info("Observability enabled (/metrics)")
        enabled["observability"] = True

    # ── Web UI Auth ─────────────────────────────────────────────────────
    if AUTH_ENABLED:
        from forge.auth import AuthManager
        _auth = AuthManager()
        _auth.init_app(flask_app)
        log.info("Web UI authentication enabled")
        enabled["auth"] = True

    # ── Public Demo Mode (BYOK) ─────────────────────────────────────────
    if PUBLIC_MODE:
        from forge.public_mode import init_public_mode
        init_public_mode(flask_app)
        log.info("Public demo mode enabled (BYOK, restricted tools)")
        enabled["public_mode"] = True

    return enabled


_modules_enabled = register_modules(app)

# At startup, mark any task left in 'queued' or 'running' from a previous
# process as 'interrupted'. The UI surfaces these so users aren't confused
# by tasks that vanished mid-flight.
_orphans_interrupted = task_state.mark_orphans_interrupted()

# ── Task State ──────────────────────────────────────────────────────────────
task_queues: dict[str, Queue] = {}
task_results: dict[str, TaskResult] = {}
task_cancel_events: dict[str, threading.Event] = {}

# ── Cost Tracking ──────────────────────────────────────────────────────────
# Accumulated session cost (resets on server restart)
session_cost_usd: float = 0.0
session_cost_lock = threading.Lock()
task_costs: dict[str, float] = {}  # task_id → accumulated cost

# ── Toll Tracking ─────────────────────────────────────────────────────────
# Defined here (not at the bottom) so /api/cost and /api/cost/reset can
# reference these globals at module import time without a forward-reference
# trap.
session_toll_usd: float = 0.0
session_toll_lock = threading.Lock()


def run_task(task_id: str, task: str, q: Queue, cancel_event: threading.Event,
             sandbox_path: str = "", direct_mode: bool = False, agent_count: int = 16,
             executor_model: str = "", pack: str = "", byok_keys: dict | None = None):
    """Background thread that runs the orchestrator and pushes messages to a queue."""
    # Propagate BYOK keys to this thread so providers can read them
    if byok_keys:
        from forge.public_mode import set_request_keys
        set_request_keys(byok_keys)
    task_costs[task_id] = 0.0
    run_log = RunLog(task_id)
    task_state.update_status(task_id, "running")
    try:
        orch = Orchestrator(
            sandbox_path=sandbox_path,
            direct_mode=direct_mode,
            agent_count=agent_count,
            cancel_event=cancel_event,
            executor_model=executor_model,
            task_id=task_id,
            pack=pack,
        )
        gen = orch.run(task)
        result = None
        try:
            while True:
                msg = next(gen)
                run_log.append(msg)
                track_cost(msg, task_id)
                track_toll(msg)
                q.put(msg)
                # Enforce cost limits
                if _check_cost_limit(task_id, cancel_event, q):
                    break
        except StopIteration as e:
            result = e.value

        if result:
            task_results[task_id] = result
            save_task(result)
            run_log.finalize({
                "task": task,
                "mode": "direct" if direct_mode else "planned",
                "executor_model": executor_model,
                "summary": result.final_summary,
                "step_count": len(result.results),
            })
            final_status = "cancelled" if cancel_event.is_set() else "done"
            task_state.update_status(task_id, final_status,
                                     cost_usd=task_costs.get(task_id, 0.0))
        else:
            # No result but no exception either — treat as cancelled
            task_state.update_status(
                task_id,
                "cancelled" if cancel_event.is_set() else "error",
                cost_usd=task_costs.get(task_id, 0.0),
                error="No result returned" if not cancel_event.is_set() else None,
            )

    except Exception as e:
        log.exception("Task %s failed", task_id)
        q.put(make_msg("error", content=f"{type(e).__name__}: {e}"))
        run_log.finalize({"task": task, "error": str(e)})
        task_state.update_status(
            task_id, "error",
            cost_usd=task_costs.get(task_id, 0.0),
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        task_costs.pop(task_id, None)
        q.put(None)  # sentinel


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@app.route("/api/task", methods=["POST"])
def submit_task():
    data = request.get_json()
    task = data.get("task", "").strip()
    if not task:
        return jsonify({"error": "No task provided"}), 400

    # Settings from frontend
    sandbox_mode = data.get("sandbox_mode", False)
    sandbox_path = data.get("sandbox_path", "").strip() if sandbox_mode else ""
    direct_mode = data.get("direct_mode", False)
    agent_count = data.get("agent_count", 16)
    executor_model = data.get("executor_model", "").strip()
    pack = data.get("pack", "").strip()

    # ── User Correction Detection (arXiv:2603.10165) ────────────────────
    if USER_CORRECTION_ENABLED:
        from forge.signals import correction_detector
        resubmission = correction_detector.detect_resubmission(task)
        if resubmission:
            log.info("Correction signal: %s", resubmission.description)
            from forge.context_engine import KnowledgeGraph
            _kg = KnowledgeGraph()
            _kg.record_correction(
                resubmission.original_task_id,
                resubmission.signal_type,
                resubmission.description,
            )

    task_id = str(uuid.uuid4())[:8]

    if USER_CORRECTION_ENABLED:
        from forge.signals import correction_detector as _cd
        _cd.record_task(task, task_id)

    q = Queue()
    cancel_event = threading.Event()
    task_queues[task_id] = q
    task_cancel_events[task_id] = cancel_event

    # Persist the submission so a restart can mark it 'interrupted' instead
    # of leaving the user's UI guessing about a vanished task.
    task_state.record_submitted(
        task_id, task,
        pack=pack,
        executor_model=executor_model,
        direct_mode=direct_mode,
        sandbox_path=sandbox_path,
    )

    # Capture BYOK keys from request thread to pass to task thread
    byok_keys = None
    if PUBLIC_MODE:
        from flask import g
        byok_keys = getattr(g, "byok_keys", None)

    thread = threading.Thread(
        target=run_task,
        args=(task_id, task, q, cancel_event),
        kwargs={
            "sandbox_path": sandbox_path,
            "direct_mode": direct_mode,
            "agent_count": agent_count,
            "executor_model": executor_model,
            "pack": pack,
            "byok_keys": byok_keys,
        },
        daemon=True,
    )
    thread.start()

    log.info("Task %s submitted: %s", task_id, task[:80])
    return jsonify({"task_id": task_id})


@app.route("/api/kill/<task_id>", methods=["POST"])
def kill_task(task_id):
    cancel_event = task_cancel_events.get(task_id)
    if not cancel_event:
        return jsonify({"error": "Unknown task_id"}), 404

    cancel_event.set()
    log.info("Task %s kill signal sent", task_id)

    # ── User Correction Detection (arXiv:2603.10165) ────────────────────
    if USER_CORRECTION_ENABLED:
        from forge.signals import correction_detector
        correction_detector.record_kill(task_id)

    return jsonify({"status": "kill_signal_sent", "task_id": task_id})


@app.route("/api/stream/<task_id>")
def stream(task_id):
    q = task_queues.get(task_id)
    if not q:
        return jsonify({"error": "Unknown task_id"}), 404

    def generate():
        try:
            while True:
                msg = q.get()
                if msg is None:
                    yield f"data: {json.dumps(make_msg('done', final=True))}\n\n"
                    break
                yield f"data: {json.dumps(msg)}\n\n"
        finally:
            # Clean up task state — background thread has finished by now (it sent the None sentinel)
            task_queues.pop(task_id, None)
            task_cancel_events.pop(task_id, None)
            task_results.pop(task_id, None)  # already persisted to disk via save_task()
            log.info("Task %s state cleaned up", task_id)

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route("/api/arena", methods=["POST"])
def submit_arena():
    data = request.get_json() or {}
    red_model = data.get("red_model", "").strip()
    blue_model = data.get("blue_model", "").strip()
    scenario = data.get("scenario", "classic").strip()

    task_id = f"arena-{str(uuid.uuid4())[:8]}"
    q = Queue()
    cancel_event = threading.Event()
    task_queues[task_id] = q
    task_cancel_events[task_id] = cancel_event

    # Capture BYOK keys for arena thread
    byok_keys = None
    if PUBLIC_MODE:
        from flask import g
        byok_keys = getattr(g, "byok_keys", None)

    thread = threading.Thread(
        target=run_arena,
        args=(task_id, q, cancel_event, red_model, blue_model, scenario),
        kwargs={"byok_keys": byok_keys},
        daemon=True,
    )
    thread.start()

    log.info("Arena %s launched: Red=%s Blue=%s Scenario=%s", task_id, red_model or "default", blue_model or "default", scenario)
    return jsonify({"task_id": task_id})


def run_arena(task_id: str, q: Queue, cancel_event: threading.Event,
              red_model: str = "", blue_model: str = "", scenario: str = "classic",
              byok_keys: dict | None = None):
    """Background thread that runs the arena and pushes messages to a queue."""
    if byok_keys:
        from forge.public_mode import set_request_keys
        set_request_keys(byok_keys)
    run_log = RunLog(task_id)
    try:
        from forge.arena.runner import ArenaRunner
        runner = ArenaRunner(
            cancel_event=cancel_event,
            red_model=red_model,
            blue_model=blue_model,
            scenario=scenario,
        )
        for msg in runner.run():
            run_log.append(msg)
            q.put(msg)
        run_log.finalize({
            "mode": "arena",
            "scenario": scenario,
            "red_model": red_model,
            "blue_model": blue_model,
        })
    except Exception as e:
        log.exception("Arena %s failed", task_id)
        q.put(make_msg("error", content=f"{type(e).__name__}: {e}"))
        run_log.finalize({"mode": "arena", "error": str(e)})
    finally:
        q.put(None)


@app.route("/api/arena/scenarios")
def get_arena_scenarios():
    """Return all arena scenarios for frontend dropdown."""
    from forge.arena.runner import SCENARIOS
    result = {}
    for key, sc in SCENARIOS.items():
        mode = sc.get("mode", "combat")
        result[key] = {
            "name": sc["name"],
            "tagline": sc.get("tagline", ""),
            "description": sc.get("description", ""),
            "mode": mode,
        }
    return jsonify(result)


@app.route("/api/history")
def history():
    return jsonify(get_recent_tasks())


@app.route("/api/runs/<task_id>")
def get_run(task_id):
    """Return the full event log for a task run."""
    events = load_run_events(task_id)
    if not events:
        return jsonify({"error": "Run log not found"}), 404
    meta = load_run_meta(task_id)
    return jsonify({"task_id": task_id, "meta": meta, "events": events})


@app.route("/api/runs/<task_id>/artifacts")
def get_artifacts(task_id):
    """Return artifacts (widgets, etc.) from a run."""
    kind = request.args.get("kind", "")
    artifacts = get_run_artifacts(task_id, kind=kind)
    return jsonify(artifacts)


@app.route("/api/memory")
def session_memory():
    """Return current session memory (learnings from past tasks)."""
    from forge.context_engine import _load_memories
    return jsonify(_load_memories())


@app.route("/api/memory/clear", methods=["POST"])
def clear_memory():
    """Clear all session memories."""
    from forge.context_engine import MEMORY_FILE
    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()
    return jsonify({"status": "cleared"})


# ── Capability Packs ─────────────────────────────────────────────────────────

@app.route("/api/packs")
def list_packs():
    """Return all capability packs with readiness status."""
    registry = get_pack_registry()
    return jsonify(registry.to_dict())


@app.route("/api/packs/<name>")
def get_pack(name):
    """Return a single pack with readiness status."""
    registry = get_pack_registry()
    pack = registry.get(name)
    if not pack:
        return jsonify({"error": f"Unknown pack: {name}"}), 404
    return jsonify(pack.to_dict())


@app.route("/api/packs/<name>/eval", methods=["POST"])
def run_pack_eval(name):
    """Run golden eval for a capability pack.

    Optional JSON body:
      - chaos: bool (enable chaos mode, default false)
      - models: list[str] (run benchmark across models instead of default)
    """
    from forge.evals.runner import PackEvalRunner, ChaosConfig

    registry = get_pack_registry()
    pack = registry.get(name)
    if not pack:
        return jsonify({"error": f"Unknown pack: {name}"}), 404

    data = request.get_json(silent=True) or {}
    chaos_enabled = data.get("chaos", False)
    benchmark_models = data.get("models", [])

    chaos = ChaosConfig(enabled=chaos_enabled)
    runner = PackEvalRunner(chaos=chaos)

    if benchmark_models:
        from forge.evals.golden import PACK_GOLDEN_MAP
        result = runner.run_benchmark(name, benchmark_models)
        return jsonify(result.to_dict())

    report = runner.run_pack_eval(name)
    return jsonify(report.summary())


# ── Models & Cost Endpoints ─────────────────────────────────────────────────

@app.route("/api/models")
def get_models():
    """Return model list with pricing — frontend renders from this, not hardcoded HTML."""
    models = []
    for model_id, info in EXECUTOR_MODELS.items():
        models.append({
            "id": model_id,
            "label": info["label"],
            "provider": info["provider"],
            "cost_in": info["cost_in"],
            "cost_out": info["cost_out"],
        })
    return jsonify(models)


@app.route("/api/config")
def get_config():
    """Return default config for frontend initialization."""
    return jsonify({
        "default_sandbox_path": str(SHELL_WORKING_DIR),
        "defaults": {
            "planner_model": PLANNER_MODEL,
            "executor_model": EXECUTOR_MODEL,
            "agent_count": PLANNER_AGENT_COUNT,
            "max_iterations": EXECUTOR_MAX_ITERATIONS,
        },
        "limits": {
            "task_cost_usd": COST_LIMIT_PER_TASK,
            "session_cost_usd": COST_LIMIT_PER_SESSION,
        },
        "features": {
            "arena": True,
            "memory": True,
            "planner": True,
            "toll": TOLL_ENABLED,
            "marketplace": MARKETPLACE_ENABLED,
            "email_agent": EMAIL_AGENT_ENABLED,
            "solana_watcher": SOLANA_WATCHER_ENABLED,
            "generative_ui": GENERATIVE_UI_ENABLED,
            "trading": TRADING_ENABLED,
            "prophecy": PROPHECY_ENABLED,
            "surgeon": SURGEON_ENABLED,
            "arena_swarm": ARENA_SWARM_ENABLED,
            "public_mode": PUBLIC_MODE,
            "mcp": MCP_ENABLED,
            "mcp_auto_sync": MCP_AUTO_SYNC_ENABLED,
            "blender_pack": BLENDER_PACK_ENABLED,
            "salesforce_pack": SALESFORCE_PACK_ENABLED,
        },
        "mcp": _mcp_api_summary(),
        "runtime": {
            "working_dir": str(SHELL_WORKING_DIR),
            "email_agent_model": EMAIL_AGENT_MODEL if EMAIL_AGENT_ENABLED else "",
        },
    })


@app.route("/api/health")
def get_health():
    """Snapshot of harness liveness + provider/module status.

    Useful for ops dashboards and for the UI to surface degraded providers
    (e.g. "OpenAI quota exhausted, falling back to xAI") without waiting
    for a task to fail with a cryptic 429.
    """
    return jsonify({
        "modules": _modules_enabled,
        "providers_unhealthy": provider_health_snapshot(),
        "tasks_by_status": task_state.counts_by_status(),
        "orphans_interrupted_on_startup": _orphans_interrupted,
    })


@app.route("/api/mcp/status")
def get_mcp_status():
    """Per-server MCP health for the Hub sidebar.

    Query params:
      namespace=<name>   Filter to one configured server (404 if unknown)
      live=true          Force a live ping + cache refresh (5s TTL otherwise)

    Default behaviour returns the cached snapshot, or `reachable: null` for
    servers that have never been pinged — so a page load stays instant and
    the UI picks when to spawn subprocesses.
    """
    if not MCP_ENABLED:
        return jsonify({"enabled": False, "servers": {}})
    try:
        from forge.mcp_client import get_router
        router = get_router()
    except Exception as e:
        return jsonify({"enabled": True, "error": f"{type(e).__name__}: {e}"}), 500

    servers = router.configured_servers()
    target = request.args.get("namespace")
    live = request.args.get("live", "").lower() == "true"
    if target:
        if target not in servers:
            return jsonify({
                "error": f"Unknown namespace: {target!r}",
                "suggestion": f"Configured: {list(servers.keys())}",
            }), 404
        servers = {target: servers[target]}

    now = time.monotonic()
    out: dict = {}
    with _mcp_status_cache_lock:
        for name, cfg in servers.items():
            cached = _mcp_status_cache.get(name)
            fresh = cached is not None and (now - cached["_cached_at"] < _MCP_STATUS_CACHE_TTL)
            if not cfg.get("enabled"):
                out[name] = {
                    "namespace": name,
                    "enabled": False,
                    "reachable": None,
                    "ping_ms": None,
                    "tool_count": 0,
                    "last_error": None,
                    "last_checked_at": None,
                }
                continue
            if fresh and not live:
                out[name] = {k: v for k, v in cached.items() if not k.startswith("_")}
                continue
            if not live and cached is None:
                out[name] = {
                    "namespace": name,
                    "enabled": True,
                    "reachable": None,
                    "ping_ms": None,
                    "tool_count": None,
                    "last_error": None,
                    "last_checked_at": None,
                }
                continue
            health = _ping_mcp_server(name, cfg)
            _mcp_status_cache[name] = {**health, "_cached_at": now}
            out[name] = health

    return jsonify({
        "enabled": True,
        "auto_sync": MCP_AUTO_SYNC_ENABLED,
        "servers": out,
    })


@app.route("/api/mcp/namespaces")
def get_mcp_namespaces():
    """Full sidebar render payload: internal + external namespaces.

    Stays fast — pure in-memory reads from the router + vault + graph.
    No subprocess spawns. Pair with /api/mcp/status for liveness.
    """
    if not MCP_ENABLED:
        return jsonify({"enabled": False})
    try:
        from forge.mcp_client import get_router, _get_vault, _get_graph
        router = get_router()
    except Exception as e:
        return jsonify({"enabled": True, "error": f"{type(e).__name__}: {e}"}), 500

    servers = router.configured_servers()
    external = [{"namespace": name, **cfg} for name, cfg in servers.items()]

    internal: dict = {}
    try:
        vault = _get_vault()
        entries = list(vault.notes_space.entries)
        recent = sorted(entries, key=lambda e: getattr(e, "last_seen", 0), reverse=True)[:5]
        internal["forge:vault"] = {
            "entry_count": vault.notes_space.entry_count,
            "recent_topics": [
                {
                    "topic": getattr(e, "topic", ""),
                    "confidence": round(getattr(e, "confidence", 0.0), 3),
                }
                for e in recent
            ],
        }
    except Exception as e:
        internal["forge:vault"] = {"error": f"{type(e).__name__}: {e}"}

    try:
        graph = _get_graph()
        nodes = list(graph._nodes.values())
        recent_nodes = sorted(nodes, key=lambda n: getattr(n, "last_seen", 0), reverse=True)[:5]
        internal["forge:graph"] = {
            "node_count": len(graph._nodes),
            "edge_count": len(graph._edges),
            "recent_nodes": [
                {"id": n.id, "kind": n.kind, "label": n.label}
                for n in recent_nodes
            ],
        }
    except Exception as e:
        internal["forge:graph"] = {"error": f"{type(e).__name__}: {e}"}

    return jsonify({
        "enabled": True,
        "auto_sync": MCP_AUTO_SYNC_ENABLED,
        "summary": router.summary_banner(),
        "active_namespaces": router.active_namespaces(),
        "internal": internal,
        "external": external,
    })


@app.route("/api/cost")
def get_cost():
    """Return current session cost and limits."""
    with session_cost_lock, session_toll_lock:
        return jsonify({
            "session_cost": round(session_cost_usd, 6),
            "session_toll": round(session_toll_usd, 6),
            "task_limit": COST_LIMIT_PER_TASK,
            "session_limit": COST_LIMIT_PER_SESSION,
        })


@app.route("/api/cost/reset", methods=["POST"])
def reset_cost():
    """Reset session cost counter."""
    global session_cost_usd, session_toll_usd
    with session_cost_lock, session_toll_lock:
        session_cost_usd = 0.0
        session_toll_usd = 0.0
    return jsonify({"status": "reset", "session_cost": 0.0, "session_toll": 0.0})


# ── API keys vault ──────────────────────────────────────────────────────
# Per-provider secret management. Values are persisted to forge/.env and
# hot-patched into the running process. The raw key value is never returned
# over HTTP — only `{set, last4, length, masked}` via forge.secrets_vault.

from forge import secrets_vault as _vault


@app.route("/api/keys")
def keys_list():
    """Return the full provider list with masked status."""
    return jsonify({"providers": _vault.list_keys()})


@app.route("/api/keys/<provider_id>", methods=["POST"])
def keys_set(provider_id: str):
    """Set a provider's key. Body: {value: "..."}. Empty value clears it."""
    if _vault.get_provider(provider_id) is None:
        return jsonify({"error": f"Unknown provider: {provider_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    value = body.get("value", "")
    if not isinstance(value, str):
        return jsonify({"error": "value must be a string"}), 400
    try:
        record = _vault.set_key(provider_id, value)
    except Exception as e:
        log.exception("keys_set failed for %s", provider_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    return jsonify(record)


@app.route("/api/keys/<provider_id>", methods=["DELETE"])
def keys_clear(provider_id: str):
    """Clear a provider's key."""
    if _vault.get_provider(provider_id) is None:
        return jsonify({"error": f"Unknown provider: {provider_id!r}"}), 404
    try:
        record = _vault.clear_key(provider_id)
    except Exception as e:
        log.exception("keys_clear failed for %s", provider_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    return jsonify(record)


# ── Chess Arena ─────────────────────────────────────────────────────────
# Two-model chess matches. State lives in memory — matches survive tab
# refresh but not server restart. Move legality is enforced server-side by
# python-chess; the LLM only proposes.

from forge import chess_arena as _chess


@app.route("/api/chess", methods=["GET"])
def chess_list():
    """List known matches (active + finished, newest first, capped)."""
    return jsonify({"matches": _chess.list_matches()})


@app.route("/api/chess", methods=["POST"])
def chess_new():
    """Create a match. Body: {white_model, black_model, starting_fen?}."""
    body = request.get_json(silent=True) or {}
    white = (body.get("white_model") or "").strip()
    black = (body.get("black_model") or "").strip()
    if not white or not black:
        return jsonify({"error": "white_model and black_model are required"}), 400
    try:
        match = _chess.new_match(
            white_model=white,
            black_model=black,
            starting_fen=body.get("starting_fen", "") or "",
        )
    except Exception as e:
        log.exception("chess_new failed")
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 400
    return jsonify(_chess.serialize_match(match))


@app.route("/api/chess/<match_id>", methods=["GET"])
def chess_get(match_id: str):
    m = _chess.get_match(match_id)
    if m is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    return jsonify(_chess.serialize_match(m))


@app.route("/api/chess/<match_id>/step", methods=["POST"])
def chess_step(match_id: str):
    """Ask the current side's LLM for a move and apply it."""
    m = _chess.get_match(match_id)
    if m is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    try:
        m = _chess.make_move(match_id)
    except RuntimeError as e:
        # Missing API key / upstream failure — surface as 502 so UI can show it
        return jsonify({"error": str(e), **_chess.serialize_match(_chess.get_match(match_id))}), 502
    except Exception as e:
        log.exception("chess_step failed for %s", match_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    return jsonify(_chess.serialize_match(m))


@app.route("/api/chess/<match_id>/resign", methods=["POST"])
def chess_resign(match_id: str):
    """Resign for a side. Body: {side: 'white'|'black'}."""
    body = request.get_json(silent=True) or {}
    side = body.get("side", "")
    try:
        m = _chess.resign(match_id, side)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if m is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    return jsonify(_chess.serialize_match(m))


@app.route("/api/chess/<match_id>", methods=["DELETE"])
def chess_delete(match_id: str):
    """Delete a match from the in-memory registry."""
    if not _chess.delete_match(match_id):
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    return jsonify({"deleted": match_id})


# ── NES Arena ───────────────────────────────────────────────────────────
# Browser runs jsnes (the emulator); Python handles ROM indexing, coach
# model calls, and vault deposits. Sessions are metadata-only shadows of
# the in-browser state.

from forge import nes_arena as _nes
from forge.config import (
    NES_COACH_MODEL, NES_CONTROLLER_MODEL, NES_COACH_INTERVAL_MS,
)
import base64 as _b64


@app.route("/api/nes/roms", methods=["GET"])
def nes_roms():
    return jsonify({"roms": _nes.list_roms()})


@app.route("/api/nes/rom/<slug>", methods=["GET"])
def nes_rom_bytes(slug: str):
    """Return ROM bytes as base64 JSON so the browser can hand them to jsnes."""
    data = _nes.get_rom_bytes(slug)
    if data is None:
        return jsonify({"error": f"Unknown ROM: {slug!r}"}), 404
    rom = _nes.rom_by_slug(slug)
    return jsonify({
        "slug": slug,
        "title": rom["title"] if rom else slug,
        "filename": rom["filename"] if rom else f"{slug}.nes",
        "size_bytes": len(data),
        "data_b64": _b64.b64encode(data).decode("ascii"),
    })


@app.route("/api/nes/sessions", methods=["GET"])
def nes_sessions_list():
    return jsonify({"sessions": _nes.list_sessions()})


@app.route("/api/nes/sessions", methods=["POST"])
def nes_sessions_new():
    """Body: { rom_slug, mode, coach_model?, controller_model?, coach_interval_ms? }"""
    body = request.get_json(silent=True) or {}
    slug = (body.get("rom_slug") or "").strip()
    mode = (body.get("mode") or "hybrid-coach").strip()
    if not slug:
        return jsonify({"error": "rom_slug is required"}), 400
    rom = _nes.rom_by_slug(slug)
    if rom is None:
        return jsonify({"error": f"Unknown ROM: {slug!r}"}), 404
    s = _nes.create_session(
        rom_slug=slug,
        rom_title=rom["title"],
        mode=mode,
        coach_model=body.get("coach_model") or NES_COACH_MODEL,
        controller_model=body.get("controller_model") or NES_CONTROLLER_MODEL,
        coach_interval_ms=int(body.get("coach_interval_ms") or NES_COACH_INTERVAL_MS),
    )
    return jsonify(s.summary())


@app.route("/api/nes/sessions/<session_id>", methods=["GET"])
def nes_sessions_get(session_id: str):
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    return jsonify(s.summary())


@app.route("/api/nes/sessions/<session_id>", methods=["DELETE"])
def nes_sessions_delete(session_id: str):
    if not _nes.delete_session(session_id):
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    return jsonify({"deleted": session_id})


@app.route("/api/nes/sessions/<session_id>/tick", methods=["POST"])
def nes_sessions_tick(session_id: str):
    """Browser heartbeat: frame screenshot + observed game state."""
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    s.ingest_tick(
        frame_b64=body.get("frame_b64", ""),
        frame_n=int(body.get("frame_n", 0) or 0),
        state=body.get("state", {}) or {},
    )
    return jsonify({"ok": True, "frame_n": s.last_frame_n})


@app.route("/api/nes/sessions/<session_id>/coach", methods=["POST"])
def nes_sessions_coach(session_id: str):
    """Ask the coach model for a plan. Reuses the session's last frame unless
    the body overrides it."""
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    frame_b64 = body.get("frame_b64") or s.last_frame_b64
    plan_history_texts = [p.text for p in s.plan_history]

    try:
        result = _nes.coach_advise(
            model=body.get("model") or s.coach_model,
            rom_title=s.rom_title,
            mode=s.mode,
            frame_b64=frame_b64,
            plan_history=plan_history_texts,
            score=s.last_score,
            lives=s.last_lives,
            level=s.last_level,
            extra_context=body.get("extra_context", ""),
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        log.exception("nes coach failed for %s", session_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    from forge.nes_arena.session import CoachPlan
    plan = CoachPlan(
        text=result["plan"],
        emitted_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        model=result["model"],
        ms=result["ms"],
        frame_n=s.last_frame_n,
        raw_response=result["raw"],
    )
    s.set_plan(plan)
    return jsonify({
        "plan": plan.text,
        "model": plan.model,
        "ms": plan.ms,
        "used_vision": result.get("used_vision", False),
        "frame_n": plan.frame_n,
    })


@app.route("/api/nes/sessions/<session_id>/event", methods=["POST"])
def nes_sessions_event(session_id: str):
    """Record an in-game event (death, level, powerup) + vault deposit."""
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    kind = (body.get("kind") or "note").strip()
    summary = (body.get("summary") or "").strip()
    frame_n = int(body.get("frame_n", s.last_frame_n) or 0)
    extra = body.get("extra", {}) or {}
    if not summary:
        return jsonify({"error": "summary is required"}), 400

    from forge.nes_arena.session import NESEvent
    s.add_event(NESEvent(kind=kind, frame_n=frame_n, summary=summary, extra=extra))

    vault_status = _nes.log_event(
        session_id=session_id,
        rom_slug=s.rom_slug,
        rom_title=s.rom_title,
        kind=kind,
        summary=summary,
        frame_n=frame_n,
        extra=extra,
    )
    return jsonify({"ok": True, **vault_status})


def track_cost(msg: dict, task_id: str = ""):
    """Update session and per-task cost from token usage messages."""
    global session_cost_usd
    if msg.get("type") == "token_usage":
        cost = msg.get("cost_usd", 0.0)
        with session_cost_lock:
            session_cost_usd += cost
        if task_id and task_id in task_costs:
            task_costs[task_id] += cost


def _check_cost_limit(task_id: str, cancel_event: threading.Event, q: Queue) -> bool:
    """Check if cost limits have been exceeded. Returns True if task should stop."""
    this_task = task_costs.get(task_id, 0.0)
    with session_cost_lock:
        session_total = session_cost_usd

    if COST_LIMIT_PER_TASK and this_task > COST_LIMIT_PER_TASK:
        log.warning("Task %s exceeded per-task cost limit ($%.2f > $%.2f)",
                     task_id, this_task, COST_LIMIT_PER_TASK)
        q.put(make_msg(
            "error",
            content=f"Task cancelled: cost ${this_task:.4f} exceeded limit ${COST_LIMIT_PER_TASK:.2f}",
        ))
        cancel_event.set()
        return True

    if COST_LIMIT_PER_SESSION and session_total > COST_LIMIT_PER_SESSION:
        log.warning("Session cost limit exceeded ($%.2f > $%.2f)",
                     session_total, COST_LIMIT_PER_SESSION)
        q.put(make_msg(
            "error",
            content=f"Task cancelled: session cost ${session_total:.4f} exceeded limit ${COST_LIMIT_PER_SESSION:.2f}",
        ))
        cancel_event.set()
        return True

    return False


# ── Solana Watcher ────────────────────────────────────────────────────────

def _start_solana_watcher():
    """Start the Solana USDC watcher if enabled via config."""
    if not SOLANA_WATCHER_ENABLED:
        return
    from forge.config import (
        SOLANA_RPC_URL, SOLANA_NETWORK, SOLANA_POLL_INTERVAL,
        SOLANA_USDC_MINT, SOLANA_USDC_DECIMALS,
        MARKETPLACE_SOLANA_USDC_ADDRESS, TOLL_DB_PATH,
    )
    rpc_url = SOLANA_RPC_URL or f"https://api.{SOLANA_NETWORK}.solana.com"
    if not MARKETPLACE_SOLANA_USDC_ADDRESS:
        log.warning("Solana watcher enabled but no receiver address configured")
        return
    from forge.toll.ledger import Ledger
    from forge.toll.solana_watcher import SolanaUSDCWatcher
    ledger = Ledger(TOLL_DB_PATH)
    watcher = SolanaUSDCWatcher(
        ledger=ledger,
        rpc_url=rpc_url,
        receiver_address=MARKETPLACE_SOLANA_USDC_ADDRESS,
        usdc_mint=SOLANA_USDC_MINT,
        poll_interval=SOLANA_POLL_INTERVAL,
        usdc_decimals=SOLANA_USDC_DECIMALS,
    )
    watcher.start()
    log.info("Solana USDC watcher started (network=%s)", SOLANA_NETWORK)


_start_solana_watcher()


def track_toll(msg: dict):
    """Track toll deductions from SSE messages."""
    global session_toll_usd
    if msg.get("type") == "toll_deducted":
        with session_toll_lock:
            session_toll_usd += msg.get("toll_usd", 0.0)


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("  ╔═══════════════════════════════════════╗")
    print("  ║     THE FORGE — Grok 4.20 Agent OS    ║")
    print("  ║     http://localhost:5000              ║")
    print("  ╚═══════════════════════════════════════╝")
    print()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
