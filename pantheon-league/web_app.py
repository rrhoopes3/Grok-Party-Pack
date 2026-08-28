"""The Pantheon League — standalone persistent arena league companion.

Gods and Presidents now have ELO, grudges, and dramatic highlight reels.

Run:
    python pantheon-league/web_app.py

http://localhost:5004

State lives in ~/.pantheon-league/league.json so grudges survive reboots and
even extraction of the surgeon.

Why this fits the Party Pack: Arenas are the star attraction. This makes the
characters *persistent* across time. The grudges become canon. The propaganda
posters become shareable artifacts. Maximum "I need to see what happens next".
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import sys

from flask import Flask, jsonify, render_template_string, request

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from forge.security import bind_host, install_auth_gate, require_auth

app = Flask(__name__)
install_auth_gate(app, allow_loopback_demo=True)

LEAGUE_HOME = Path.home() / ".pantheon-league"
LEAGUE_HOME.mkdir(parents=True, exist_ok=True)
STATE_FILE = LEAGUE_HOME / "league.json"

# ── Core Roster (Gods + Presidential Guests for chaos) ───────────────────────
ROSTER = {
    # Pantheon core
    "ZEUS": {"type": "god", "power": 92, "title": "Lord of the Thunder"},
    "ATHENA": {"type": "god", "power": 88, "title": "Clear-Eyed Strategist"},
    "HEPHAESTUS": {"type": "god", "power": 85, "title": "Master of the Anvil"},
    "HERMES": {"type": "god", "power": 79, "title": "The Tolltaker"},
    "ARES": {"type": "god", "power": 84, "title": "Bringer of Carnage"},
    "HADES": {"type": "god", "power": 81, "title": "Keeper of Failed Tokens"},
    # Presidential guests (slightly nerfed but dangerous)
    "JACKSON": {"type": "president", "power": 71, "title": "The People's Brawler"},
    "LINCOLN": {"type": "president", "power": 77, "title": "The Union Preserver"},
    "TR": {"type": "president", "power": 74, "title": "The Big Stick"},
    "REAGAN": {"type": "president", "power": 68, "title": "The Great Communicator"},
}

DEFAULT_STATE = {
    "standings": {
        k: {"elo": 1500 + (v["power"] - 80) * 3, "wins": 0, "losses": 0, "last_played": None}
        for k, v in ROSTER.items()
    },
    "grudges": {k: {o: 0 for o in ROSTER if o != k} for k in ROSTER},
    "history": [],
    "season": "I — THE FIRST CHAOS",
}


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


state = load_state()


def get_standings() -> list[dict[str, Any]]:
    rows = []
    for name, data in state["standings"].items():
        rows.append(
            {
                "name": name,
                "elo": data["elo"],
                "wins": data["wins"],
                "losses": data["losses"],
                "type": ROSTER[name]["type"],
                "title": ROSTER[name]["title"],
            }
        )
    return sorted(rows, key=lambda r: r["elo"], reverse=True)


def get_grudge_matrix() -> dict[str, Any]:
    """Returns a structured grudge matrix for visual rendering."""
    fighters = list(ROSTER.keys())
    matrix = {}
    max_g = 0
    for a in fighters:
        matrix[a] = {}
        for b in fighters:
            if a == b:
                matrix[a][b] = None
                continue
            val = state["grudges"].get(a, {}).get(b, 0)
            matrix[a][b] = val
            if val > max_g:
                max_g = val
    return {"fighters": fighters, "matrix": matrix, "max_grudge": max_g}


def get_rivals_report() -> str:
    """Generates dramatic text about the current top rivalries."""
    fighters = list(ROSTER.keys())
    pairs = []
    for a in fighters:
        for b in fighters:
            if a >= b:
                continue
            g_ab = state["grudges"].get(a, {}).get(b, 0)
            g_ba = state["grudges"].get(b, {}).get(a, 0)
            heat = max(g_ab, g_ba)
            if heat > 0:
                pairs.append((heat, a, b, g_ab, g_ba))

    if not pairs:
        return "The arena is quiet. No grudges burn bright enough to sing about... yet."

    pairs.sort(reverse=True)
    top = pairs[:3]

    lines = ["THE GRUDGE CODEX — CURRENT RIVALRIES", "═══════════════════════════════════════"]
    for heat, a, b, gab, gba in top:
        intensity = "SEETHING" if heat >= 8 else "FESTERING" if heat >= 5 else "SIMMERING"
        lines.append(f"\n{intensity} ({heat}): {a} ↔ {b}")
        lines.append(f"  • {a}'s grudge toward {b}: {gab}")
        lines.append(f"  • {b}'s grudge toward {a}: {gba}")
        if heat >= 7:
            lines.append("  The next meeting will echo in the halls of Olympus and the White House alike.")
    return "\n".join(lines)


def simulate_match(a: str, b: str) -> dict[str, Any]:
    """Lightweight but flavorful deterministic-ish simulator with grudge influence."""
    pa = ROSTER[a]["power"] + random.randint(-4, 6)
    pb = ROSTER[b]["power"] + random.randint(-4, 6)

    # Grudge modifies the fight
    g = state["grudges"].get(a, {}).get(b, 0)
    if g > 4:
        pa += 7  # motivated by hate
    elif g < -3:
        pa -= 4  # reluctant

    if pa > pb:
        winner, loser = a, b
    else:
        winner, loser = b, a

    # Update ELO (crude but fun)
    wa = state["standings"][winner]
    la = state["standings"][loser]
    wa["elo"] += 18
    la["elo"] -= 14
    wa["wins"] += 1
    la["losses"] += 1
    wa["last_played"] = datetime.utcnow().isoformat()
    la["last_played"] = wa["last_played"]

    # Grudge evolution
    state["grudges"][winner][loser] = min(12, state["grudges"][winner].get(loser, 0) + 2)
    state["grudges"][loser][winner] = max(-8, state["grudges"][loser].get(winner, 0) - 1)

    # Flavor text
    flavor = generate_flavor(winner, loser, g)

    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "winner": winner,
        "loser": loser,
        "winner_elo": wa["elo"],
        "loser_elo": la["elo"],
        "flavor": flavor,
        "grudge_level": g,
    }
    state["history"].append(record)
    if len(state["history"]) > 40:
        state["history"] = state["history"][-40:]

    save_state(state)
    return record


def generate_flavor(winner: str, loser: str, grudge_before: int) -> str:
    w_type = ROSTER[winner]["type"]
    l_type = ROSTER[loser]["type"]

    lines = [
        f"After a {'brutal' if grudge_before > 3 else 'fierce'} exchange, {winner} landed the decisive blow.",
    ]

    if w_type == "god" and l_type == "president":
        lines.append(f"{winner} reminded the mortal that even presidents answer to Olympus.")
    if "JACKSON" in (winner, loser):
        lines.append("The common man roared. History took notes.")
    if winner == "ARES" or loser == "ARES":
        lines.append("Ares laughed the entire time. It was unsettling.")
    if winner == "HERMES":
        lines.append("Hermes collected the toll *and* the victory. Efficient.")
    if grudge_before > 5:
        lines.append("The grudge had been festering for cycles. This was personal.")

    lines.append(f"{loser} will remember this.")
    return " ".join(lines)


def generate_poster(match: dict[str, Any]) -> str:
    """Returns glorious propaganda poster text."""
    w, l = match["winner"], match["loser"]
    return f"""
╔══════════════════════════════════════════════════════════════╗
║  THE PANTHEON LEAGUE PRESENTS                                ║
║                                                              ║
║   {w}  DEFEATS  {l}                                          ║
║                                                              ║
║   {ROSTER[w]["title"].upper()}                               ║
║                                                              ║
║   IN A BATTLE FOR THE AGES • GRUDGE LEVEL {match["grudge_level"]}           ║
║                                                              ║
║   "THE GODS DEMAND ENTERTAINMENT — AND THEY GOT IT."         ║
║                                                              ║
║   ELO SHIFT: +18 / -14                                       ║
║   SEASON {state["season"]}                                   ║
╚══════════════════════════════════════════════════════════════╝
"""


LCARS_LEAGUE_CSS = """
:root { --amber:#ff9900; --canary:#ffcc00; --teal:#33ccff; --red:#ff3344; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:12px; letter-spacing:0.04em; }
.lcars-frame { border:4px solid var(--amber); background:#05070a; padding:12px; margin-bottom:12px; }
table { width:100%; border-collapse:collapse; font-size:0.9em; }
th, td { padding:6px 10px; text-align:left; border-bottom:1px solid #223; }
th { color:#ff9900; }
.god { color:#ffcc00; }
.pres { color:#88ccff; }
.poster { font-family:monospace; background:#010203; padding:14px; border:3px solid #ff3344; white-space:pre; line-height:1.15; color:#ffddaa; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:6px 14px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:3px; }

/* CHAOS REFINEMENT: Grudge Heat Matrix */
.grudge-matrix { width:100%; border-collapse:collapse; font-size:0.75em; text-align:center; }
.grudge-matrix th, .grudge-matrix td { padding:4px 6px; border:1px solid #334; }
.grudge-matrix th { background:#112; color:#ffcc00; font-size:0.7em; }
.grudge-cell { font-family:monospace; font-weight:bold; }
.grudge-0 { background:#112; color:#666; }
.grudge-low { background:#331100; color:#ffaa66; }
.grudge-med { background:#552200; color:#ffdd88; }
.grudge-high { background:#aa2200; color:#ffeedd; }
.grudge-extreme { background:#ff3344; color:#000; font-weight:900; }
.rivals-report { background:#0a0d12; border:2px solid #ff9900; padding:10px; margin-top:8px; font-size:0.9em; white-space:pre-wrap; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>PANTHEON LEAGUE • SEASON I</title>
<style>{LCARS_LEAGUE_CSS}</style>
</head><body>
<div style="max-width:1080px; margin:0 auto">
<div class="lcars-frame">
  <h1 style="margin:0 0 8px; color:#ff9900">THE PANTHEON LEAGUE — {state["season"]}</h1>
  <div style="opacity:0.7">Persistent grudges. Real drama. No take-backs.</div>
</div>

<div style="display:grid; grid-template-columns: 1fr 380px; gap:12px">
  <!-- STANDINGS -->
  <div class="lcars-frame">
    <h3 style="color:#ff9900; margin:0 0 8px">CURRENT STANDINGS</h3>
    <table id="standings"><thead><tr><th>COMPETITOR</th><th>ELO</th><th>W-L</th><th>TITLE</th></tr></thead><tbody></tbody></table>
  </div>

  <!-- BOOK A MATCH -->
  <div class="lcars-frame">
    <h3 style="color:#ff9900; margin:0 0 8px">BOOK AN EXHIBITION</h3>
    <select id="fighter-a"></select> vs
    <select id="fighter-b"></select>
    <button class="lcars-btn" onclick="bookMatch()" style="margin-top:8px">SIMULATE &amp; RECORD</button>
    <div id="last-match" style="margin-top:12px; font-size:0.85em"></div>
  </div>
</div>

<!-- POSTER + GRUDGES -->
<div class="lcars-frame">
  <h3 style="color:#ff3344">BATTLE PROPAGANDA</h3>
  <div id="poster" class="poster">Select two fighters and press SIMULATE to generate a poster worthy of the LCARS halls.</div>
  <button class="lcars-btn" onclick="copyPoster()" style="margin-top:6px">COPY FOR THE CHRONICLER</button>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">THE GRUDGE HEAT MATRIX</h3>
  <div id="grudge-matrix" style="overflow-x:auto"></div>
  <button class="lcars-btn" onclick="demandRivalryReport()" style="margin-top:8px">DEMAND A RIVALRY REPORT</button>
  <div id="rivals-report" class="rivals-report" style="display:none; margin-top:8px"></div>
</div>

<div class="lcars-frame" style="font-size:0.8em">
  <strong>RECENT HISTORY</strong>
  <div id="history" style="max-height:160px; overflow:auto; margin-top:6px"></div>
  <button class="lcars-btn" onclick="councilCommentary()" style="margin-top:8px">REQUEST PRESIDENTIAL COMMENTARY</button>
</div>

<div style="text-align:center; opacity:0.5; font-size:0.7em; margin-top:12px">
  The Pantheon League is canon. What happens in the arena stays in the codex forever.
</div>
</div>

<script>
let currentPoster = '';

async function refreshAll() {{
  const s = await (await fetch('/api/standings')).json();
  const tbody = document.querySelector('#standings tbody');
  tbody.innerHTML = '';
  s.forEach(r => {{
    const tr = document.createElement('tr');
    tr.innerHTML = `<td class="${{r.type}}">${{r.name}}</td><td>${{r.elo}}</td><td>${{r.wins}}-${{r.losses}}</td><td>${{r.title}}</td>`;
    tbody.appendChild(tr);
  }});

  // populate selects
  const opts = s.map(r => `<option value="${{r.name}}">${{r.name}} (${{r.elo}})</option>`).join('');
  document.getElementById('fighter-a').innerHTML = opts;
  document.getElementById('fighter-b').innerHTML = opts.split('</option>').reverse().join('</option>') + '</option>';

  // grudges
  const g = await (await fetch('/api/grudges')).json();
  const gel = document.getElementById('grudges');
  gel.innerHTML = Object.entries(g).map(([k, vs]) => 
    `<div><strong>${{k}}</strong>: ` + Object.entries(vs).filter(([,v])=>v!==0).map(([o,v])=>`${{o}}:${{v}}`).join(' ') + `</div>`
  ).join('');

  // history
  const h = await (await fetch('/api/history')).json();
  const hel = document.getElementById('history');
  hel.innerHTML = h.slice(-6).reverse().map(m => 
    `<div style="margin:3px 0; border-left:3px solid #ff9900; padding-left:6px">${{m.winner}} defeated ${{m.loser}} — ${{m.flavor.slice(0,110)}}...</div>`
  ).join('');
}}

async function bookMatch() {{
  const a = document.getElementById('fighter-a').value;
  const b = document.getElementById('fighter-b').value;
  if (!a || !b || a===b) {{ alert('Choose two different fighters'); return; }}
  const r = await fetch('/api/simulate', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{a,b}})}});
  const match = await r.json();
  document.getElementById('last-match').innerHTML = `<strong>${{match.winner}}</strong> defeated <strong>${{match.loser}}</strong>. Grudge shift recorded.`;
  currentPoster = match.poster;
  document.getElementById('poster').textContent = match.poster;
  refreshAll();
}}

function copyPoster() {{
  if (!currentPoster) return;
  navigator.clipboard.writeText(currentPoster);
  alert('Propaganda copied. Go feed the Chronicler.');
}}

async function councilCommentary() {{
  const r = await fetch('/api/commentary');
  const c = await r.json();
  alert('PRESIDENTIAL COUNCIL DISPATCH:\\n\\n' + c.text);
}}

// ── CHAOS REFINEMENT: Visual Grudge Heat Matrix + Rivalry Reports (client side)
function renderGrudgeMatrix(gm) {{
  const container = document.getElementById('grudge-matrix');
  if (!container || !gm || !gm.fighters) return;

  const fighters = gm.fighters;
  let html = '<table class="grudge-matrix"><thead><tr><th></th>';
  fighters.forEach(f => {{ html += `<th>${{f}}</th>`; }});
  html += '</tr></thead><tbody>';

  fighters.forEach(a => {{
    html += `<tr><th>${{a}}</th>`;
    fighters.forEach(b => {{
      const val = gm.matrix[a] ? gm.matrix[a][b] : null;
      if (val === null) {{
        html += '<td style="background:#111"></td>';
      }} else {{
        let cls = 'grudge-0';
        if (val >= 9) cls = 'grudge-extreme';
        else if (val >= 6) cls = 'grudge-high';
        else if (val >= 3) cls = 'grudge-med';
        else if (val > 0) cls = 'grudge-low';
        html += `<td class="grudge-cell ${{cls}}">${{val}}</td>`;
      }}
    }});
    html += '</tr>';
  }});
  html += '</tbody></table>';
  container.innerHTML = html;
}}

async function demandRivalryReport() {{
  const box = document.getElementById('rivals-report');
  box.style.display = 'block';
  box.textContent = 'Consulting the Grudge Codex...';
  const r = await fetch('/api/rivals-report');
  const data = await r.json();
  box.textContent = data.report || 'The gods are silent on this matter.';
}}

window.onload = refreshAll;
</script>
</body></html>
"""


@app.route("/")
def index():
    global state
    state = load_state()
    return render_template_string(INDEX)


@app.route("/api/standings")
@require_auth
def api_standings():
    return jsonify(get_standings())


@app.route("/api/grudges")
def api_grudges():
    return jsonify(state.get("grudges", {}))


@app.route("/api/history")
def api_history():
    return jsonify(state.get("history", []))


# ── CHAOS REFINEMENT CYCLE: Visual Grudge Heat Matrix + Dramatic Rivalry Reports
@app.route("/api/grudge-matrix")
def api_grudge_matrix():
    return jsonify(get_grudge_matrix())


@app.route("/api/rivals-report")
def api_rivals_report():
    return jsonify({"report": get_rivals_report()})


@app.route("/api/simulate", methods=["POST"])
@require_auth
def api_simulate():
    data = request.json or {}
    a, b = data.get("a"), data.get("b")
    if a not in ROSTER or b not in ROSTER or a == b:
        return jsonify({"error": "invalid fighters"}), 400
    match = simulate_match(a, b)
    match["poster"] = generate_poster(match)
    return jsonify(match)


@app.route("/api/commentary")
def api_commentary():
    # Pull a random recent winner or just flavor
    hist = state.get("history", [])
    if not hist:
        text = "The Council has nothing to say until blood has been spilled in the arena."
    else:
        last = hist[-1]
        pres = random.choice(["JACKSON", "LINCOLN", "TR", "REAGAN"])
        text = f'President {pres}: "That was a fine display of {last["winner"]} over {last["loser"]}. Reminds me of the old days."'
    return jsonify({"text": text})


if __name__ == "__main__":
    port = int(os.getenv("PANTHEON_LEAGUE_PORT", 5004))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  THE PANTHEON LEAGUE — SEASON I OF CHAOS                   ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  Book matches. Watch grudges grow. Generate propaganda.    ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host=bind_host(), port=port, debug=False)
