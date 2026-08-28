"""The Chronicler — standalone myth-weaving companion for the Grok Party Pack.

This is pure maximalist joy: your agent's grim, token-by-token death marches
through the Windows wastes are transmuted into Homeric verse, LCARS terminal
recitations, and blood feuds between gods.

Run:
    python chronicler/web_app.py

Opens on http://localhost:5002 (or CHRONICLER_PORT).

Reads real Forge run logs when present (../forge/data/runs or env var).
Falls back to glorious canned epics so it always delights even in a fresh clone.

Why this fits the Party Pack: The soul is theatricality and "run logs as lore".
Every failed shell command becomes the labors of Hephaestus. Every toll paid
is a sacrifice to Hermes. The Chronicler makes the chaos *mean* something.
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request

# Allow running as python chronicler/web_app.py
sys.path.insert(0, str(Path(__file__).parent.parent))

from forge.security import bind_host, install_auth_gate, require_auth

app = Flask(__name__)
install_auth_gate(app, allow_loopback_demo=True)

# ── Configuration ────────────────────────────────────────────────────────────
CHRONICLER_HOME = Path.home() / ".chronicler"
CHRONICLER_HOME.mkdir(parents=True, exist_ok=True)
MYTHS_FILE = CHRONICLER_HOME / "myths.json"

# Default search locations for the real Forge — feel free to set env
DEFAULT_FORGE_RUNS = [
    Path(__file__).parent.parent / "forge" / "data" / "runs",
    Path("forge/data/runs"),
    Path("../forge/data/runs"),
]

# LCARS palette (condensed from the mothership)
LCARS_CSS = """
:root {
  --lcars-bg: #000000;
  --lcars-amber: #ff9900;
  --lcars-canary: #ffcc00;
  --lcars-teal: #33ccff;
  --lcars-red: #ff3344;
  --lcars-text: #ffe4c4;
  --lcars-panel: #05070a;
}
body { font-family: "Antonio","Oswald","Arial Narrow",sans-serif; background:#000; color:var(--lcars-text); margin:0; padding:0; letter-spacing:0.04em; }
.lcars-frame { border: 6px solid var(--lcars-amber); margin: 12px; background: var(--lcars-panel); }
.topbar { background: #000; color: var(--lcars-amber); padding: 12px 24px; font-size: 1.4em; border-bottom: 4px solid var(--lcars-amber); display:flex; justify-content:space-between; align-items:center; }
.lcars-btn { background: var(--lcars-amber); color:#000; border:0; padding:8px 18px; font-family:inherit; font-size:0.9em; cursor:pointer; text-transform:uppercase; margin:4px; }
.lcars-btn:hover { background: var(--lcars-canary); }
.lcars-btn.secondary { background:#112; color:var(--lcars-teal); border:1px solid var(--lcars-teal); }
.scroll { background:#020203; border:2px solid var(--lcars-amber); padding:16px; font-family:monospace; white-space:pre-wrap; line-height:1.35; max-height:420px; overflow:auto; }
.run-card { background:#0a0d12; border:1px solid #334; padding:8px 12px; margin:4px 0; cursor:pointer; }
.run-card:hover { border-color:var(--lcars-amber); }
.run-card.active { border-color:var(--lcars-canary); background:#1a1408; }
.god { color:var(--lcars-canary); font-weight:bold; }
"""

# Pantheon narrators + epithets (stolen lovingly from the mothership lore)
GODS = {
    "zeus": "Zeus the Thunderer, Lord of the Forge Council",
    "athena": "Athena of the Clear Eyes, Weaver of Strategies",
    "hephaestus": "Hephaestus the Lame Smith, Forger of Relics",
    "hermes": "Hermes the Quick, Patron of Tolls and Messages",
    "ares": "Ares the Roarer, Bringer of Glorious Carnage",
    "hades": "Hades the Unseen, Keeper of the Dead Tokens",
}

EPITHETS = {
    "shell": ["the shell-crier", "lord of cmd.exe", "the cd-singer"],
    "browser": ["the net-strider", "winged browser of the false links"],
    "python": ["the serpent-whisperer", "executor of the sacred repl"],
    "error": ["the thwarted", "he who tasted bitter stderr"],
    "toll": ["the coin-giver", "sacrificer at the ledger gate"],
    "success": ["the triumphant", "who brought the task to completion"],
}

# Canned epics for instant joy (no data required)
CANNED = [
    {
        "id": "canned-01",
        "task": "write a sentence",
        "model": "grok-4.3",
        "summary": "Direct execution complete",
        "events": 4,
        "tools": ["run_command"],
        "saga": "In the beginning there was only the prompt. And the prompt was with the User, and the prompt was 'write a sentence'. Then did the Executor stir, and Hephaestus cried 'cd' into the void. But the Windows wastes answered not with paths, only with scorn. And lo, a second toll was paid to Hermes, and the sentence was written. And it was mid.",
    },
    {
        "id": "canned-02",
        "task": "explore the current directory like a pirate",
        "model": "grok-4.20-reasoning",
        "summary": "Found three cursed JSONL scrolls and one LICENSE",
        "events": 19,
        "tools": ["filesystem", "shell"],
        "saga": "Hear now the tale of the digital corsair who bade the Executor map the treasure hold! With dir and ls did Hermes race, while Hephaestus struck flint against the LICENSE. Three .jsonl scrolls were hauled from the deep runs/ trench. No doubloons, only the ghosts of previous quests. The pirate declared victory and paid the toll with a glad heart. The Chronicler sang of it for nine cycles.",
    },
]

# In-memory + persisted myths
myths: list[dict[str, Any]] = []


def load_myths() -> None:
    global myths
    if MYTHS_FILE.exists():
        try:
            myths = json.loads(MYTHS_FILE.read_text())
        except Exception:
            myths = []


def save_myth(myth: dict[str, Any]) -> None:
    global myths
    myth["etched_at"] = datetime.utcnow().isoformat() + "Z"
    myths.append(myth)
    MYTHS_FILE.write_text(json.dumps(myths, indent=2))


def discover_real_runs() -> list[dict[str, Any]]:
    """Scan Forge run logs if they exist. Returns lightweight saga seeds."""
    runs: list[dict[str, Any]] = []
    seen = set()

    for base in DEFAULT_FORGE_RUNS:
        if not base.exists():
            continue
        for meta_file in sorted(base.glob("*.meta.json")):
            try:
                meta = json.loads(meta_file.read_text())
                run_id = meta.get("task_id", meta_file.stem)
                if run_id in seen:
                    continue
                seen.add(run_id)

                # Try to peek the jsonl for tool usage and drama
                jsonl = base / f"{run_id}.jsonl"
                tools: set[str] = set()
                toll_total = 0.0
                error_count = 0
                event_count = meta.get("event_count", 0)

                if jsonl.exists():
                    for line in jsonl.read_text().splitlines():
                        if not line.strip():
                            continue
                        try:
                            evt = json.loads(line)
                        except Exception:
                            continue
                        if evt.get("type") == "tool_call":
                            tools.add(evt.get("name", "unknown"))
                        if evt.get("type") == "tool_result":
                            res = str(evt.get("result", ""))
                            if "error" in res.lower() or "stderr" in res.lower() and len(res) > 10:
                                error_count += 1
                        if evt.get("type") == "toll_deducted":
                            toll_total += float(evt.get("toll_usd", 0))

                runs.append(
                    {
                        "id": run_id,
                        "task": meta.get("task", "unnamed quest")[:120],
                        "model": meta.get("executor_model", "unknown"),
                        "summary": meta.get("summary", ""),
                        "events": event_count,
                        "tools": sorted(list(tools))[:6] or ["direct"],
                        "toll": round(toll_total, 4),
                        "errors": error_count,
                        "source": "forge",
                    }
                )
            except Exception:
                continue
    return runs[:24]  # cap for sanity


def get_all_seeds() -> list[dict[str, Any]]:
    real = discover_real_runs()
    if not real:
        # Return canned as seeds
        return [
            {
                "id": c["id"],
                "task": c["task"],
                "model": c["model"],
                "summary": c["summary"],
                "events": c["events"],
                "tools": c["tools"],
                "toll": 0.0,
                "errors": 0,
                "source": "canned",
            }
            for c in CANNED
        ]
    return real


def choose_narrator(seed: dict[str, Any]) -> tuple[str, str]:
    """Pick a god based on dominant tool or drama."""
    tools = [t.lower() for t in seed.get("tools", [])]
    if "shell" in tools or "run_command" in tools:
        return "hephaestus", GODS["hephaestus"]
    if any(t in tools for t in ["browser", "http", "playwright"]):
        return "hermes", GODS["hermes"]
    if seed.get("errors", 0) > 2:
        return "hades", GODS["hades"]
    if seed.get("toll", 0) > 0.05:
        return "hermes", GODS["hermes"]
    return "zeus", GODS["zeus"]


def weave_saga(seed: dict[str, Any], narrator: str | None = None) -> str:
    """The heart of the Chronicler. Rule-based epic generator. Glorious and silly."""
    if narrator is None:
        narrator, _ = choose_narrator(seed)

    task = seed.get("task", "a forgotten errand")
    tools = seed.get("tools", [])
    errors = seed.get("errors", 0)
    toll = seed.get("toll", 0.0)
    model = seed.get("model", "the nameless")

    fragments: list[str] = []

    # Opening
    fragments.append(
        f"From the black throne of the LCARS core, {GODS.get(narrator, 'the Council')} speaks:"
    )

    # The Call
    fragments.append(
        f"\n\"Hear me, mortals of the prompt! A hero once called upon the Forge with the quest: '{task}'."
    )

    # The Journey (tool by tool)
    if tools:
        for t in tools[:3]:
            epithet_list = EPITHETS.get(t.lower(), EPITHETS.get("shell"))
            epithet = random.choice(epithet_list)
            if "error" in str(t).lower() or errors > 0:
                fragments.append(
                    f"  Yet {epithet} of the {t} stumbled in the wastes and tasted bitter stderr."
                )
            else:
                fragments.append(
                    f"  And {epithet} of the {t} labored mightily, striking sparks from the machine."
                )

    # The Sacrifice
    if toll > 0:
        fragments.append(
            f"  Tolls were paid in blood-amber to Hermes the Accountant — {toll} USD laid upon the altar."
        )
    else:
        fragments.append("  No coin was demanded that day; the gods worked for glory alone.")

    # The Trial
    if errors > 0:
        fragments.append(f"  {errors} times did the hero taste defeat. Hades smiled in the dark.")
    else:
        fragments.append("  The path was straight. Athena nodded once.")

    # Closing
    god_voice = {
        "hephaestus": "the anvil rang with the final success.",
        "hermes": "the message flew true on swift wings.",
        "hades": "even the lost tokens were counted.",
        "zeus": "the thunder of completion shook the halls.",
    }.get(narrator, "the task was sealed in the great log.")

    fragments.append(f"\nAnd thus the quest was ended. {god_voice}\n")
    fragments.append(f"Model that served: {model}. Let this saga be etched forever.")

    # Occasional presidential cameo for chaos
    if random.random() < 0.25:
        pres = random.choice(["Lincoln", "TR", "Jackson", "Reagan", "Obama"])
        fragments.append(
            f'\n(From the adjacent chamber the voice of President {pres} was heard: "Now *that\'s* how you execute a task." )'
        )

    return "\n".join(fragments)


def weave_cycle(seeds: list[dict[str, Any]]) -> str:
    """An epic cycle combining several runs into one Iliad of the agent OS."""
    title = "THE CHRONICLES OF THE FORGE — CYCLE OF THE RUNNING GODS"
    body = [title, "=" * 60, ""]
    for i, s in enumerate(seeds[:5], 1):
        body.append(f"BOOK {i}: {s['task'].upper()}")
        body.append(weave_saga(s))
        body.append("\n---\n")
    body.append("Thus ends the cycle. May future executors add new verses.")
    return "\n".join(body)


# ── Routes ────────────────────────────────────────────────────────────────────

INDEX_HTML = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>THE CHRONICLER • FORGE MYTH WEAVER</title>
<style>
{LCARS_CSS}
h1 {{ color:var(--lcars-amber); margin:0; font-size:1.6em; }}
.container {{ display:flex; gap:12px; padding:12px; }}
.sidebar {{ width:320px; flex-shrink:0; }}
.main {{ flex:1; }}
.footer {{ font-size:0.7em; color:#666; padding:8px 24px; border-top:1px solid #222; }}
</style>
</head>
<body>
<div class="topbar">
  <div><span style="color:#ff3344">★</span> THE CHRONICLER <span style="font-size:0.6em;opacity:0.7">ORACLE OF THE RUNS</span></div>
  <div style="font-size:0.7em; color:#ffcc00">GROK PARTY PACK // MAXIMUM CHAOS EDITION</div>
</div>

<div class="container">
  <div class="sidebar lcars-frame">
    <h3 style="color:#ff9900; padding:0 8px;">ARCHIVE SCROLLS</h3>
    <button class="lcars-btn" onclick="loadRuns()" style="width:100%">⟳ RESCAN FORGE</button>
    <button class="lcars-btn secondary" onclick="weaveCycle()" style="width:100%">WEAVE EPIC CYCLE</button>
    <div id="runs" style="margin-top:8px; max-height:380px; overflow:auto;"></div>
    <div style="margin-top:16px; font-size:0.75em; opacity:0.6; padding:0 8px;">
      Real runs appear when Forge data lives nearby.<br>
      Canned epics always available for the ritual.
    </div>
  </div>

  <div class="main lcars-frame">
    <div style="padding:12px 18px; border-bottom:2px solid #ff9900; display:flex; gap:8px; align-items:center;">
      <div style="flex:1">
        <strong id="current-title">SELECT A SCROLL OR SUMMON A CYCLE</strong>
        <div id="current-meta" style="font-size:0.8em; opacity:0.7;"></div>
      </div>
      <button class="lcars-btn" onclick="weaveCurrent()" id="weave-btn" disabled>WEAVE SAGA</button>
      <button class="lcars-btn secondary" onclick="etchMyth()" id="etch-btn" disabled>ETCH INTO CODEX</button>
    </div>

    <div class="scroll" id="output" style="min-height:280px; margin:12px;">
      The halls are silent. Choose a deed from the archive, or demand the full Cycle of the Running Gods.
    </div>

    <div style="padding:8px 18px;">
      <button class="lcars-btn secondary" onclick="showMyths()">VIEW ETCHED MYTHS ({len(myths)})</button>
      <button class="lcars-btn secondary" onclick="clearOutput()">CLEAR</button>
    </div>
  </div>
</div>

<div class="footer">
  The Chronicler does not lie. It merely makes everything sound like it happened on Mount Olympus during a particularly drunk feast of the gods.
  Data source: Forge run logs + pure theatrical invention.
</div>

<script>
let currentSeed = null;
let allRuns = [];

async function loadRuns() {{
  const r = await fetch('/api/runs');
  allRuns = await r.json();
  const el = document.getElementById('runs');
  el.innerHTML = '';
  allRuns.forEach(run => {{
    const div = document.createElement('div');
    div.className = 'run-card';
    div.innerHTML = `<strong>${{run.task}}</strong><br><small>${{run.model}} • ${{run.events}} events • ${{run.tools.join(', ')}}</small>`;
    div.onclick = () => selectRun(run, div);
    el.appendChild(div);
  }});
}}

function selectRun(run, div) {{
  document.querySelectorAll('.run-card').forEach(d => d.classList.remove('active'));
  div.classList.add('active');
  currentSeed = run;
  document.getElementById('current-title').textContent = run.task;
  document.getElementById('current-meta').textContent = `${{run.model}} • ${{run.events}} events • toll ${{run.toll || 0}}`;
  document.getElementById('weave-btn').disabled = false;
  document.getElementById('etch-btn').disabled = true;
  document.getElementById('output').textContent = 'The muses await your command. Press WEAVE SAGA.';
}}

async function weaveCurrent() {{
  if (!currentSeed) return;
  const r = await fetch('/api/weave', {{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{id: currentSeed.id, seed: currentSeed}})
  }});
  const data = await r.json();
  document.getElementById('output').textContent = data.saga;
  document.getElementById('etch-btn').disabled = false;
  window._lastMyth = {{...currentSeed, saga: data.saga, narrator: data.narrator}};
}}

async function weaveCycle() {{
  const r = await fetch('/api/cycle');
  const data = await r.json();
  document.getElementById('output').textContent = data.saga;
  document.getElementById('current-title').textContent = 'THE EPIC CYCLE';
  document.getElementById('current-meta').textContent = 'Combined from the archive';
  document.getElementById('weave-btn').disabled = true;
  window._lastMyth = {{id: 'cycle-' + Date.now(), task: 'The Great Cycle', saga: data.saga}};
  document.getElementById('etch-btn').disabled = false;
}}

async function etchMyth() {{
  if (!window._lastMyth) return;
  const r = await fetch('/api/etch', {{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify(window._lastMyth)
  }});
  const res = await r.json();
  alert('Etched into the Codex: ' + res.count + ' myths now archived.');
  document.getElementById('etch-btn').disabled = true;
}}

async function showMyths() {{
  const r = await fetch('/api/myths');
  const list = await r.json();
  const out = document.getElementById('output');
  if (!list.length) {{
    out.textContent = 'The codex is empty. Weave and etch legends first.';
    return;
  }}
  out.innerHTML = list.map(m => 
    `<div style="margin-bottom:18px; border-left:4px solid #ff9900; padding-left:12px;">
      <strong>${{m.task || m.id}}</strong> <small>(${{(m.etched_at||'').slice(0,10)}})</small><br>
      <pre style="margin:4px 0; white-space:pre-wrap; font-size:0.85em;">${{m.saga}}</pre>
    </div>`
  ).join('');
}}

function clearOutput() {{
  document.getElementById('output').textContent = 'The halls are silent once more.';
  document.getElementById('etch-btn').disabled = true;
}}

window.onload = () => {{
  loadRuns();
  // Prime with one canned epic visible
  setTimeout(() => {{
    const first = document.querySelector('.run-card');
    if (first) first.click();
  }}, 120);
}};
</script>
</body>
</html>
""".replace("{len(myths)}", str(len(myths)))  # initial count


@app.route("/")
def index():
    load_myths()
    return render_template_string(INDEX_HTML)


@app.route("/api/runs")
@require_auth
def api_runs():
    seeds = get_all_seeds()
    return jsonify(seeds)


@app.route("/api/weave", methods=["POST"])
@require_auth
def api_weave():
    data = request.json or {}
    seed = data.get("seed") or {"id": data.get("id", "unknown"), "task": "unknown quest"}
    narrator, name = choose_narrator(seed)
    saga = weave_saga(seed, narrator)
    return jsonify({"saga": saga, "narrator": name, "god_key": narrator})


@app.route("/api/cycle")
def api_cycle():
    seeds = get_all_seeds()
    random.shuffle(seeds)
    saga = weave_cycle(seeds)
    return jsonify({"saga": saga})


@app.route("/api/etch", methods=["POST"])
@require_auth
def api_etch():
    myth = request.json or {}
    if not myth.get("saga"):
        return jsonify({"error": "no saga"}), 400
    save_myth(myth)
    load_myths()
    return jsonify({"status": "etched", "count": len(myths)})


@app.route("/api/myths")
def api_myths():
    load_myths()
    # Return newest first, trimmed for UI sanity
    return jsonify(sorted(myths, key=lambda m: m.get("etched_at", ""), reverse=True)[:12])


if __name__ == "__main__":
    load_myths()
    port = int(os.getenv("CHRONICLER_PORT", 5002))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  THE CHRONICLER AWAKENS                                    ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  Your run logs will become legend.                         ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host=bind_host(), port=port, debug=False)
