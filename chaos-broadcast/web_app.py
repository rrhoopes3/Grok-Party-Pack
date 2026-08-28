"""Chaos Broadcast System — THE EMERGENCY RELIC NETWORK

A standalone, gloriously over-the-top Party Pack broadcast tower.
Pulls drama from the other relics (Chronicler, Pantheon League, etc.)
and turns it into breaking news, propaganda, and interference.

Run:
    python chaos-broadcast/web_app.py

http://localhost:5005

State lives in ~/.chaos-broadcast/ so the signal persists.

Why this fits the Party Pack:
We now have multiple independent chaotic relics. They needed a central
dramatic broadcast hub that makes the whole mess feel like one living,
noisy, slightly unhinged universe. Maximum "what the hell is happening
in this project" energy.
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
from forge.security import bind_host, install_auth_gate

app = Flask(__name__)
install_auth_gate(app, allow_loopback_demo=True)

BROADCAST_HOME = Path.home() / ".chaos-broadcast"
BROADCAST_HOME.mkdir(parents=True, exist_ok=True)
ARCHIVE_FILE = BROADCAST_HOME / "broadcasts.json"

# Known relic sources we can listen to
RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
}

FREQUENCIES = [
    "PANTHEON NEWS NETWORK",
    "LCARS TRAFFIC CONTROL",
    "CHRONICLER DISPATCH",
    "OLYMPUS SPORTS",
    "WHITE HOUSE AFTER DARK",
    "EMERGENCY THEOLOGY",
]

INTERFERENCE_LINES = [
    "SIGNAL DEGRADING...",
    "HERMES IS SKIMMING THE TOLL AGAIN",
    "ZEUS IS ANGRY AT THE WEATHER SATELLITES",
    "JACKSON JUST CHALLENGED THE TRANSMITTER TO A DUEL",
    "TEMPORAL ECHO DETECTED",
]

def load_archive() -> list[dict]:
    if ARCHIVE_FILE.exists():
        try:
            return json.loads(ARCHIVE_FILE.read_text())
        except Exception:
            pass
    return []

def save_broadcast(broadcast: dict) -> None:
    archive = load_archive()
    archive.append(broadcast)
    if len(archive) > 50:
        archive = archive[-50:]
    ARCHIVE_FILE.write_text(json.dumps(archive, indent=2))

def listen_to_relics() -> list[str]:
    """Scavenge recent drama from known relics."""
    lines = []
    # Chronicler myths
    myth_path = RELICT_PATHS["chronicler"]
    if myth_path.exists():
        try:
            myths = json.loads(myth_path.read_text())
            for m in myths[-2:]:
                if "saga" in m:
                    lines.append(m["saga"][:180] + "...")
        except Exception:
            pass

    # League recent history
    league_path = RELICT_PATHS["league"]
    if league_path.exists():
        try:
            league = json.loads(league_path.read_text())
            for h in league.get("history", [])[-3:]:
                lines.append(f"{h['winner']} just humiliated {h['loser']} — grudge now at {h.get('grudge_level', '?')}")
        except Exception:
            pass

    return lines or ["The relics are quiet. Too quiet."]

def generate_broadcast() -> dict:
    """Generate a chaotic emergency broadcast."""
    sources = listen_to_relics()
    freq = random.choice(FREQUENCIES)
    interference = random.choice(INTERFERENCE_LINES) if random.random() > 0.6 else None

    headline = random.choice([
        "BREAKING: OLYMPUS DEMANDS MORE BLOOD IN THE ARENA",
        "PRESIDENTIAL COUNCIL ISSUES JOINT STATEMENT ON 'VIBES'",
        "HERMES CAUGHT CHARGING TOLLS ON THE BROADCAST SPECTRUM",
        "LOCAL GODS REPORT 'UNUSUALLY HIGH GRUDGE DENSITY'",
        "CHRONICLER REFUSES TO STOP SINGING ABOUT YOUR LAST FAILED TOOL CALL",
    ])

    body_lines = [
        f"THIS IS AN EMERGENCY BROADCAST FROM THE {freq}.",
        "",
        headline,
    ]

    if sources:
        body_lines.append("")
        body_lines.append("LATEST FROM THE RELICS:")
        for s in sources[:2]:
            body_lines.append(f"• {s}")

    if interference:
        body_lines.append("")
        body_lines.append(f"[INTERFERENCE] {interference}")

    body_lines.append("")
    body_lines.append(f"— {datetime.utcnow().strftime('%H:%M')} UTC — THE SIGNAL MUST BE PRESERVED —")

    text = "\n".join(body_lines)

    broadcast = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "frequency": freq,
        "headline": headline,
        "text": text,
        "interference": bool(interference),
    }
    save_broadcast(broadcast)
    return broadcast

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_CHAOS_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:16px; letter-spacing:0.05em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; }
.broadcast { font-family:monospace; background:#010203; padding:18px; border:4px solid var(--red); white-space:pre; line-height:1.25; color:#ffddaa; min-height:220px; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:8px 18px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:4px; font-size:1em; }
.lcars-btn.danger { background:#ff3344; color:#fff; }
.log { font-size:0.8em; max-height:180px; overflow:auto; border-left:4px solid #ff9900; padding-left:10px; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>CHAOS BROADCAST SYSTEM</title>
<style>{LCARS_CHAOS_CSS}</style>
</head><body>
<div style="max-width:960px; margin:0 auto">
<div class="lcars-frame">
  <h1 style="margin:0; color:#ff9900">CHAOS BROADCAST SYSTEM</h1>
  <div style="opacity:0.6">PARTY PACK EMERGENCY RELIC NETWORK • ALL FREQUENCIES</div>
</div>

<div class="lcars-frame">
  <h3 style="color:#ff3344; margin:0 0 8px">LIVE TRANSMISSION</h3>
  <div id="broadcast" class="broadcast">PRESS TRANSMIT TO BEGIN THE SIGNAL.</div>
  <div style="margin-top:12px">
    <button class="lcars-btn danger" onclick="transmit()">TRANSMIT EMERGENCY BROADCAST</button>
    <button class="lcars-btn" onclick="loadLatest()">LOAD LAST BROADCAST</button>
  </div>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">RECENT TRANSMISSIONS</h3>
  <div id="log" class="log"></div>
</div>

<div style="text-align:center; opacity:0.4; font-size:0.75em">
  All broadcasts are canon. The relics are listening.
</div>
</div>

<script>
async function transmit() {{
  const el = document.getElementById('broadcast');
  el.textContent = 'TRANSMITTING...';
  const r = await fetch('/api/transmit', {{method:'POST'}});
  const b = await r.json();
  el.textContent = b.text;
  loadLog();
}}

async function loadLatest() {{
  const r = await fetch('/api/latest');
  const b = await r.json();
  if (b.text) {{
    document.getElementById('broadcast').textContent = b.text;
  }}
}}

async function loadLog() {{
  const r = await fetch('/api/log');
  const logs = await r.json();
  const el = document.getElementById('log');
  el.innerHTML = logs.slice(-8).reverse().map(l => 
    `<div style="margin:4px 0"><span style="color:#ff9900">${{l.ts.slice(11,16)}}</span> — ${{l.headline}}</div>`
  ).join('');
}}

window.onload = () => {{
  loadLatest();
  loadLog();
  setInterval(loadLog, 15000); // auto-refresh log
}};
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/transmit", methods=["POST"])
def api_transmit():
    b = generate_broadcast()
    return jsonify(b)

@app.route("/api/latest")
def api_latest():
    archive = load_archive()
    return jsonify(archive[-1] if archive else {})

@app.route("/api/log")
def api_log():
    return jsonify(load_archive())

if __name__ == "__main__":
    port = int(os.getenv("CHAOS_BROADCAST_PORT", 5005))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  CHAOS BROADCAST SYSTEM — EMERGENCY RELIC NETWORK          ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  TRANSMIT. RECEIVE. INTERFERE. REPEAT.                     ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host=bind_host(), port=port, debug=False)
