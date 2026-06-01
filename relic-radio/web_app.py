"""Relic Radio — The Grok Party Pack Broadcast Network

A self-contained, theatrical radio station that turns the output of the other
relics into dramatic "episodes," news bulletins, and late-night call-in shows.

Run:
    python relic-radio/web_app.py

http://localhost:5017

This is the sound of the relics talking to each other.

Why this fits the Party Pack:
We have multiple independent chaotic artifacts generating lore in isolation.
They needed a shared medium. Now they have late-night radio.
Maximum "I am listening to the gods argue about my last tool call" energy.
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

RADIO_HOME = Path.home() / ".relic-radio"
RADIO_HOME.mkdir(parents=True, exist_ok=True)

RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
    "broadcast": Path.home() / ".chaos-broadcast" / "broadcasts.json",
    "museum": Path.home() / ".relic-museum",  # not a json file, but we can check for existence
}

GODS = ["ZEUS", "ATHENA", "HEPHAESTUS", "HERMES", "ARES", "HADES"]
PRESIDENTS = ["JACKSON", "LINCOLN", "TR", "REAGAN"]

def load_json_safe(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and "history" in data:
            return data["history"]
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def get_recent_drama() -> list[str]:
    """Steal flavor from the other relics."""
    lines = []

    for m in load_json_safe(RELICT_PATHS["chronicler"])[-2:]:
        if "saga" in m:
            lines.append(m["saga"][:220])

    for h in load_json_safe(RELICT_PATHS["league"])[-3:]:
        if "flavor" in h:
            lines.append(f"{h.get('winner')} defeated {h.get('loser')}. {h['flavor'][:140]}")

    for b in load_json_safe(RELICT_PATHS["broadcast"])[-2:]:
        if "headline" in b:
            lines.append(b["headline"])

    return lines or ["The relics have been quiet. Suspiciously quiet."]

def generate_episode() -> dict:
    """Generate a radio episode."""
    drama = get_recent_drama()
    host = random.choice(GODS + PRESIDENTS)
    cohost = random.choice([x for x in (GODS + PRESIDENTS) if x != host])

    title = random.choice([
        "LATE NIGHT WITH THE PANTHEON",
        "THE GRUDGE REPORT",
        "SUFFERING AFTER DARK",
        "OLYMPUS CALLS IN",
        "THE LEDGER & THE LORE",
        "WHAT THE GODS ARE SAYING ABOUT YOUR LAST RUN",
    ])

    body = f"[{host}] Welcome back to {title}. I'm {host}, joined tonight by {cohost}.\n\n"

    if drama:
        body += f"[{cohost}] First up — some of the freshest suffering from the field:\n"
        for d in drama[:2]:
            body += f"• {d}\n"

    body += f"\n[{host}] And remember: in this house, we respect the grudge.\n"
    body += f"[{cohost}] Even if it doesn't respect us back."

    episode = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "title": title,
        "host": host,
        "cohost": cohost,
        "text": body,
        "frequency": "96.6 THE FORGE",
    }
    return episode

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_RADIO_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:16px; letter-spacing:0.05em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; }
.radio { background:#010203; border:4px solid var(--red); padding:20px; font-family:monospace; white-space:pre-wrap; line-height:1.4; min-height:260px; }
.now-playing { font-size:1.3em; color:#ff9900; margin-bottom:8px; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:8px 16px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:4px; }
.station-id { text-align:center; font-size:0.7em; opacity:0.5; letter-spacing:0.2em; margin:12px 0; }
.episode { background:#000; border-left:4px solid #ff9900; padding:10px; margin:6px 0; font-size:0.9em; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>RELIC RADIO • 96.6 THE FORGE</title>
<style>{LCARS_RADIO_CSS}</style>
</head><body>
<div style="max-width:900px; margin:0 auto">
<div class="lcars-frame">
  <div style="text-align:center">
    <div style="font-size:2.2em; color:#ff9900; letter-spacing:0.15em">RELIC RADIO</div>
    <div style="font-size:1.1em; opacity:0.7">96.6 THE FORGE — ALL SUFFERING, ALL THE TIME</div>
  </div>
</div>

<div class="lcars-frame">
  <div class="now-playing" id="now-playing">OFF AIR</div>
  <div class="radio" id="player">Press PLAY to begin transmission.</div>
  <div style="margin-top:12px; text-align:center">
    <button class="lcars-btn" onclick="playRandom()">PLAY RANDOM EPISODE</button>
    <button class="lcars-btn" onclick="requestDispatch()">REQUEST A DISPATCH</button>
  </div>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff; margin:0 0 8px">RECENT BROADCASTS</h3>
  <div id="log"></div>
</div>

<div class="station-id">
  BROUGHT TO YOU BY THE PANTHEON • FUNDED BY YOUR TOLLS • POWERED BY PURE SPITE
</div>
</div>

<script>
let currentEpisode = null;

async function playRandom() {{
  const r = await fetch('/api/episode');
  const ep = await r.json();
  currentEpisode = ep;
  document.getElementById('now-playing').textContent = ep.title + ' — ' + ep.host + ' & ' + ep.cohost;
  document.getElementById('player').textContent = ep.text;
  loadLog();
}}

async function requestDispatch() {{
  const topic = prompt("What should the gods and presidents discuss?");
  if (!topic) return;
  const r = await fetch('/api/dispatch', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{topic}})
  }});
  const ep = await r.json();
  currentEpisode = ep;
  document.getElementById('now-playing').textContent = ep.title;
  document.getElementById('player').textContent = ep.text;
  loadLog();
}}

async function loadLog() {{
  const r = await fetch('/api/log');
  const logs = await r.json();
  const el = document.getElementById('log');
  el.innerHTML = logs.slice(-6).reverse().map(l => 
    `<div class="episode"><strong>${{l.title}}</strong><br>${{l.text.slice(0,180)}}...</div>`
  ).join('');
}}

window.onload = loadLog;
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/episode")
def api_episode():
    return jsonify(generate_episode())

@app.route("/api/dispatch", methods=["POST"])
def api_dispatch():
    data = request.json or {}
    topic = data.get("topic", "the current state of the relics")
    host = random.choice(GODS + PRESIDENTS)
    cohost = random.choice([x for x in (GODS + PRESIDENTS) if x != host])

    text = f"[{host}] Tonight on the wire: {topic}.\n\n"
    text += f"[{cohost}] The relics are restless. I can feel it in the static.\n"
    text += f"[{host}] As always, the truth is worse than the interference."

    ep = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "title": f"SPECIAL DISPATCH: {topic.upper()}",
        "host": host,
        "cohost": cohost,
        "text": text,
    }
    return jsonify(ep)

@app.route("/api/log")
def api_log():
    # For now just return some recent ones in memory (stateless for simplicity)
    # In a real version we'd persist, but this keeps the relic light
    return jsonify([generate_episode() for _ in range(4)])

if __name__ == "__main__":
    port = int(os.getenv("RELIC_RADIO_PORT", 5017))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  RELIC RADIO — 96.6 THE FORGE                              ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  All relics. All the time.                                 ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host="0.0.0.0", port=port, debug=True)
