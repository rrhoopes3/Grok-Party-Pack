"""Relic Tavern — The Aftermath

A self-contained, gloriously rowdy tavern where the gods and presidents
(and occasional relic personifications) drink, argue, and process the day's
suffering.

Run:
    python relic-tavern/web_app.py

http://localhost:5012

This is where the relics go to get drunk and tell stories.

Why this fits the Party Pack:
The relics have been generating incredible drama all day.
They now have a place to unwind, gossip, start fights, and turn that drama
into even more lore. Maximum "the characters have a bar" energy.
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

TAVERN_HOME = Path.home() / ".relic-tavern"
TAVERN_HOME.mkdir(parents=True, exist_ok=True)
LOGS_FILE = TAVERN_HOME / "nights.json"

RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
    "broadcast": Path.home() / ".chaos-broadcast" / "broadcasts.json",
    "gazette": Path.home() / ".relic-gazette" / "editions.json",
}

GODS = ["ZEUS", "ATHENA", "HEPHAESTUS", "HERMES", "ARES", "HADES"]
PRESIDENTS = ["JACKSON", "LINCOLN", "TR", "REAGAN"]

DRINKS = [
    "Thunder Mead", "Olympic Old Fashioned", "Grudge Grog",
    "Hermes' Expresso Martini", "Ares' Blood & Sand", "Hades' Last Word"
]

def load_json_safe(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            if "history" in data: return data["history"]
            if "editions" in data: return data["editions"]
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []

def get_todays_drama() -> list[str]:
    """Harvest fresh gossip from the other relics."""
    lines = []

    for h in load_json_safe(RELICT_PATHS["league"])[-5:]:
        if "flavor" in h:
            lines.append(f"{h.get('winner')} absolutely cooked {h.get('loser')}. {h['flavor'][:120]}")

    for g in load_json_safe(RELICT_PATHS["gazette"])[-2:]:
        for headline in g.get("headlines", [])[:2]:
            lines.append(f"Headlines: {headline.get('headline', '')} — {headline.get('body', '')[:100]}")

    for b in load_json_safe(RELICT_PATHS["broadcast"])[-3:]:
        if "headline" in b:
            lines.append(b["headline"])

    return lines or ["It was a quiet day. Suspiciously quiet."]

def generate_night_log() -> dict:
    """Generate a night of tavern chaos."""
    drama = get_todays_drama()
    patrons = random.sample(GODS + PRESIDENTS, k=4)

    log_lines = [
        f"THE AFTERMAT — {datetime.utcnow().strftime('%Y-%m-%d')}",
        "═══════════════════════════════════════",
        "",
    ]

    # Opening scene
    log_lines.append(f"{patrons[0]} stumbles in, still covered in arena dust.")
    log_lines.append(f"{patrons[1]} is already three drinks deep and arguing with the jukebox.")

    if drama:
        log_lines.append("")
        log_lines.append("The room goes quiet when someone brings up today's events:")
        for d in drama[:2]:
            log_lines.append(f"• {d}")

    # Random interactions
    log_lines.append("")
    log_lines.append(f"{patrons[2]} buys a round of {random.choice(DRINKS)} for the table.")
    log_lines.append(f"{patrons[3]} immediately starts telling an increasingly embellished version of what just happened.")

    # Chaos moment
    chaos = random.choice([
        "A fistfight breaks out over whose grudge is more legitimate.",
        "Someone tries to start a betting pool on tomorrow's matches.",
        "Hermes gets caught trying to charge a cover fee on the way out.",
        "Ares starts reciting battle poetry. Everyone claps politely.",
        "Two presidents start debating which one of them would win in a fight. The gods take bets.",
    ])
    log_lines.append("")
    log_lines.append(chaos)

    log_lines.append("")
    log_lines.append("Last call is eventually called. Nobody listens.")
    log_lines.append("The night ends the way these nights always end: with promises of tomorrow's violence and a bar tab nobody wants to look at too closely.")

    night = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "patrons": patrons,
        "log": "\n".join(log_lines),
    }
    return night

def save_night(night: dict) -> None:
    archive = []
    if LOGS_FILE.exists():
        try:
            archive = json.loads(LOGS_FILE.read_text())
        except Exception:
            pass
    archive.append(night)
    if len(archive) > 30:
        archive = archive[-30:]
    LOGS_FILE.write_text(json.dumps(archive, indent=2))

def load_recent_nights() -> list[dict]:
    if not LOGS_FILE.exists():
        return []
    try:
        return json.loads(LOGS_FILE.read_text())[-6:][::-1]
    except Exception:
        return []

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_TAVERN_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:16px; letter-spacing:0.04em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; }
.tavern { background:#1a1208; border:4px solid #8b4513; padding:20px; font-family:Georgia, serif; white-space:pre-wrap; line-height:1.5; min-height:320px; color:#ffddaa; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:8px 16px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:4px; }
.patron { color:#ffcc00; font-weight:bold; }
.log-entry { background:#000; border-left:4px solid #ff9900; padding:12px; margin:8px 0; font-size:0.9em; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>THE AFTERMATH</title>
<style>{LCARS_TAVERN_CSS}</style>
</head><body>
<div style="max-width:980px; margin:0 auto">
<div class="lcars-frame">
  <h1 style="margin:0; color:#ff9900">THE AFTERMATH</h1>
  <div style="opacity:0.6">THE OFFICIAL TAVERN OF THE RELIC CIVILIZATION</div>
</div>

<div class="lcars-frame">
  <button class="lcars-btn" onclick="openTavern()">OPEN THE TAVERN</button>
  <span style="margin-left:12px; opacity:0.5" id="status"></span>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">TONIGHT'S LOG</h3>
  <div class="tavern" id="log">The bar is currently closed. Press "OPEN THE TAVERN" to begin the night's debauchery.</div>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">RECENT NIGHTS</h3>
  <div id="history"></div>
</div>

<div style="text-align:center; opacity:0.4; font-size:0.7em">
  All bar tabs are canon. All fights are encouraged.
</div>
</div>

<script>
async function openTavern() {{
  document.getElementById('status').textContent = 'Last call is never called...';
  const r = await fetch('/api/open', {{method: 'POST'}});
  const night = await r.json();
  document.getElementById('log').textContent = night.log;
  loadHistory();
}}

async function loadHistory() {{
  const r = await fetch('/api/history');
  const nights = await r.json();
  const el = document.getElementById('history');
  el.innerHTML = nights.map(n => 
    `<div class="log-entry"><strong>${{n.date}}</strong><br>${{n.log.slice(0,280)}}...</div>`
  ).join('');
}}

window.onload = loadHistory;
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/open", methods=["POST"])
def api_open():
    night = generate_night_log()
    save_night(night)
    return jsonify(night)

@app.route("/api/history")
def api_history():
    return jsonify(load_recent_nights())

if __name__ == "__main__":
    port = int(os.getenv("RELIC_TAVERN_PORT", 5012))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  THE AFTERMATH — THE RELICS GO TO GET DRUNK                ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  All grudges are settled here. All lies are told here.     ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host="0.0.0.0", port=port, debug=True)
