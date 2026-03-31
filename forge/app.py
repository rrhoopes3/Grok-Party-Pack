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
from forge.models import TaskResult
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
)
from forge.toll.endpoints import toll_bp
from forge.toll.public_api import public_bp
from forge.packs import get_registry as get_pack_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("forge.app")

# ── Parallel Prefetch (providers, deps, vault, data dirs) ────────────
from forge.prefetch import run_prefetch
_prefetch_results = run_prefetch()

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.register_blueprint(toll_bp)
if MARKETPLACE_ENABLED:
    app.register_blueprint(public_bp)

# ── Trading Module ──────────────────────────────────────────────────────
if TRADING_ENABLED:
    from forge.trading import check_trading_readiness
    from forge.trading.endpoints import trading_bp
    app.register_blueprint(trading_bp)
    trading_readiness = check_trading_readiness()
    log.info(
        "Trading module enabled (provider=%s, broker=%s, state=%s)",
        trading_readiness["provider"],
        trading_readiness["broker"],
        trading_readiness["state"],
    )
    for issue in trading_readiness["issues"]:
        log.warning("Trading readiness: %s", issue)

# ── Prophecy Engine ────────────────────────────────────────────────────
if PROPHECY_ENABLED:
    from forge.prophecy.endpoints import prophecy_bp
    app.register_blueprint(prophecy_bp)
    log.info("Prophecy Engine enabled")

# ── Surgeon (OBLITERATUS) ─────────────────────────────────────────────
if SURGEON_ENABLED:
    from forge.surgeon.endpoints import surgeon_bp
    app.register_blueprint(surgeon_bp)
    log.info("Surgeon module enabled")

# ── Email Agent + Webhook ────────────────────────────────────────────────
_email_agent = None
if EMAIL_AGENT_ENABLED:
    from forge.agents.email_webhook import email_webhook_bp, configure as configure_webhook
    from forge.agents.email_agent import EmailAgent
    _email_agent = EmailAgent(
        api_key=ARCRELAY_API_KEY,
        api_url=ARCRELAY_API_URL,
        model=EMAIL_AGENT_MODEL,
    )
    configure_webhook(ARCRELAY_WEBHOOK_SECRET, _email_agent)
    app.register_blueprint(email_webhook_bp)

# ── Scheduler ──────────────────────────────────────────────────────────
_scheduler = None
if SCHEDULER_ENABLED:
    from forge.scheduler import Scheduler, create_blueprint as create_scheduler_bp
    _scheduler = Scheduler()
    app.register_blueprint(create_scheduler_bp(_scheduler))
    _scheduler.start()
    log.info("Scheduler enabled (%d jobs)", len(_scheduler.list_jobs()))

# ── Agent Conversations ────────────────────────────────────────────────
if CONVERSATIONS_ENABLED:
    from forge.agents.conversation import create_blueprint as create_conv_bp
    app.register_blueprint(create_conv_bp())
    log.info("Agent conversations enabled")

# ── Observability ──────────────────────────────────────────────────────
if OBSERVABILITY_ENABLED:
    from forge.observability import init_observability
    init_observability(app)
    log.info("Observability enabled (/metrics)")

# ── Web UI Auth ────────────────────────────────────────────────────────
if AUTH_ENABLED:
    from forge.auth import AuthManager
    _auth = AuthManager()
    _auth.init_app(app)
    log.info("Web UI authentication enabled")
    _email_agent.start()
    log.info("Email agent enabled (model=%s)", EMAIL_AGENT_MODEL)

# ── Public Demo Mode (BYOK) ──────────────────────────────────────────────
if PUBLIC_MODE:
    from forge.public_mode import init_public_mode
    init_public_mode(app)
    log.info("Public demo mode enabled (BYOK, restricted tools)")

# ── Task State ──────────────────────────────────────────────────────────────
task_queues: dict[str, Queue] = {}
task_results: dict[str, TaskResult] = {}
task_cancel_events: dict[str, threading.Event] = {}

# ── Cost Tracking ──────────────────────────────────────────────────────────
# Accumulated session cost (resets on server restart)
session_cost_usd: float = 0.0
session_cost_lock = threading.Lock()
task_costs: dict[str, float] = {}  # task_id → accumulated cost


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

    except Exception as e:
        log.exception("Task %s failed", task_id)
        q.put({"type": "error", "content": f"{type(e).__name__}: {e}"})
        run_log.finalize({"task": task, "error": str(e)})
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
                    yield f"data: {json.dumps({'type': 'done', 'final': True})}\n\n"
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
        q.put({"type": "error", "content": f"{type(e).__name__}: {e}"})
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
        },
        "runtime": {
            "working_dir": str(SHELL_WORKING_DIR),
            "email_agent_model": EMAIL_AGENT_MODEL if EMAIL_AGENT_ENABLED else "",
        },
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
        q.put({"type": "error",
               "content": f"Task cancelled: cost ${this_task:.4f} exceeded limit ${COST_LIMIT_PER_TASK:.2f}"})
        cancel_event.set()
        return True

    if COST_LIMIT_PER_SESSION and session_total > COST_LIMIT_PER_SESSION:
        log.warning("Session cost limit exceeded ($%.2f > $%.2f)",
                     session_total, COST_LIMIT_PER_SESSION)
        q.put({"type": "error",
               "content": f"Task cancelled: session cost ${session_total:.4f} exceeded limit ${COST_LIMIT_PER_SESSION:.2f}"})
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


# ── Toll Tracking ─────────────────────────────────────────────────────────
session_toll_usd: float = 0.0
session_toll_lock = threading.Lock()


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
