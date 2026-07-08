"""Relic Tarot — The Official Divination System of the Relic Civilization

A self-contained, gloriously theatrical tarot deck where the cards are flavored
by the current state of all the other relics, read in the voices of the gods
and presidents.

Run:
    python relic-tarot/web_app.py

http://localhost:5015

This is where the relics tell your fortune (and it's never good).

Why this fits the Party Pack:
The relics have been generating incredible drama.
They now have their own divination system. The civilization has achieved mysticism.
Maximum "the cards know about your last tool failure" energy.
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

# Repo root on sys.path so relics.bootstrap is importable when run as a script
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from flask import jsonify, render_template_string

from relics.bootstrap import create_relic_app, load_json_safe, run_relic

app = create_relic_app(__name__)

TAROT_HOME = Path.home() / ".relic-tarot"
TAROT_HOME.mkdir(parents=True, exist_ok=True)
READINGS_FILE = TAROT_HOME / "readings.json"

RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
    "broadcast": Path.home() / ".chaos-broadcast" / "broadcasts.json",
    "gazette": Path.home() / ".relic-gazette" / "editions.json",
    "tavern": Path.home() / ".relic-tavern" / "nights.json",
    "post": Path.home() / ".relic-post-office" / "mail.json",
    "board": Path.home() / ".relic-bulletin-board" / "notices.json",
}

GODS = ["ZEUS", "ATHENA", "HEPHAESTUS", "HERMES", "ARES", "HADES"]
PRESIDENTS = ["JACKSON", "LINCOLN", "TR", "REAGAN"]

# Base tarot-like cards with relic-flavored interpretations
BASE_CARDS = [
    {"name": "The Grudge", "upright": "A festering rivalry will define the coming cycle.", "reversed": "Old grudges may finally be settled... or weaponized."},
    {"name": "The Messenger", "upright": "Important news (or mail) is on its way.", "reversed": "A message will be delayed, intercepted, or dramatically misread."},
    {"name": "The Arena", "upright": "Conflict is inevitable and will be entertaining.", "reversed": "The fight is already over. You missed it."},
    {"name": "The Ledger", "upright": "Debts (toll or otherwise) must be paid.", "reversed": "Someone is trying to skip out on their tab."},
    {"name": "The Prophet", "upright": "A prophecy will come true in the most inconvenient way.", "reversed": "The oracle was vague on purpose."},
    {"name": "The Broadcast", "upright": "Your private suffering will become public knowledge.", "reversed": "The signal will be jammed at the worst moment."},
    {"name": "The Drunk", "upright": "Poor decisions will be made at the tavern tonight.", "reversed": "You will be the poor decision."},
    {"name": "The Throne", "upright": "Divine (or presidential) intervention is imminent.", "reversed": "The gods are busy. You're on your own."},
    {"name": "The Press", "upright": "The headlines will not be kind.", "reversed": "The story will be exaggerated for clicks."},
    {"name": "The Market", "upright": "Values will fluctuate wildly based on grudges.", "reversed": "Someone is manipulating the exchange."},
]


def get_current_state_flavor() -> str:
    """Pull real current drama to flavor the reading."""
    candidates = []

    for h in load_json_safe(RELICT_PATHS["league"])[-3:]:
        if "flavor" in h:
            candidates.append(f"A recent arena event involving {h.get('winner')} and {h.get('loser')}.")

    for g in load_json_safe(RELICT_PATHS["gazette"])[-1:]:
        for headline in g.get("headlines", [])[:2]:
            candidates.append(f"Recent headlines: {headline.get('headline', '')}")

    for b in load_json_safe(RELICT_PATHS["broadcast"])[-2:]:
        if "headline" in b:
            candidates.append(b["headline"])

    return random.choice(candidates) if candidates else "The relics have been deceptively quiet."

def draw_cards(count: int = 3) -> list[dict]:
    """Draw cards and flavor them with current relic state."""
    flavor = get_current_state_flavor()
    deck = random.sample(BASE_CARDS, k=count)

    drawn = []
    for card in deck:
        is_reversed = random.random() > 0.5
        orientation = "reversed" if is_reversed else "upright"
        interpretation = card["reversed"] if is_reversed else card["upright"]

        # Inject real flavor
        full_interpretation = f"{interpretation} {flavor}"

        drawn.append({
            "name": card["name"],
            "orientation": orientation,
            "interpretation": full_interpretation,
        })

    return drawn

def generate_reading(spread_type: str = "three_card") -> dict:
    """Generate a full tarot-style reading."""
    cards = draw_cards(3 if spread_type == "three_card" else 5)

    reader = random.choice(GODS + PRESIDENTS)

    reading = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "reader": reader,
        "spread_type": spread_type,
        "cards": cards,
        "summary": f"The Council has spoken through {reader}. The relics are watching.",
    }
    return reading

def save_reading(reading: dict) -> None:
    archive = []
    if READINGS_FILE.exists():
        try:
            archive = json.loads(READINGS_FILE.read_text())
        except Exception:
            pass
    archive.append(reading)
    if len(archive) > 30:
        archive = archive[-30:]
    READINGS_FILE.write_text(json.dumps(archive, indent=2))

def load_recent_readings() -> list[dict]:
    if not READINGS_FILE.exists():
        return []
    try:
        return json.loads(READINGS_FILE.read_text())[-6:][::-1]
    except Exception:
        return []

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_TAROT_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:16px; letter-spacing:0.04em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; }
.card { background:#1a1208; border:3px solid var(--amber); padding:16px; margin:8px; min-height:180px; font-family:Georgia, serif; }
.card-name { color:#ff9900; font-size:1.3em; margin-bottom:6px; }
.card-orientation { font-size:0.8em; color:#ff3344; text-transform:uppercase; letter-spacing:0.1em; }
.card-interpretation { margin-top:10px; line-height:1.4; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:8px 16px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:4px; }
.reading { background:#010203; border-left:4px solid #ff9900; padding:14px; margin:8px 0; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>THE RELIC TAROT</title>
<style>{LCARS_TAROT_CSS}</style>
</head><body>
<div style="max-width:1000px; margin:0 auto">
<div class="lcars-frame">
  <h1 style="margin:0; color:#ff9900">THE RELIC TAROT</h1>
  <div style="opacity:0.6">THE OFFICIAL DIVINATION SYSTEM OF THE RELIC CIVILIZATION</div>
</div>

<div class="lcars-frame">
  <button class="lcars-btn" onclick="drawThreeCard()">DRAW THREE-CARD SPREAD</button>
  <button class="lcars-btn" onclick="drawFiveCard()">DRAW FIVE-CARD SPREAD</button>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">CURRENT READING</h3>
  <div id="reading"></div>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">RECENT READINGS</h3>
  <div id="history"></div>
</div>

<div style="text-align:center; opacity:0.4; font-size:0.7em">
  The cards are canon. The future is already in the static.
</div>
</div>

<script>
async function drawThreeCard() {{
  const r = await fetch('/api/draw', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{spread: 'three_card'}})}});
  const reading = await r.json();
  renderReading(reading);
  loadHistory();
}}

async function drawFiveCard() {{
  const r = await fetch('/api/draw', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{spread: 'five_card'}})}});
  const reading = await r.json();
  renderReading(reading);
  loadHistory();
}}

function renderReading(reading) {{
  const el = document.getElementById('reading');
  let html = `<div class="reading"><strong>Read by ${{reading.reader}}</strong><br>`;
  reading.cards.forEach((c, i) => {{
    html += `<div class="card"><div class="card-name">${{c.name}}</div><div class="card-orientation">${{c.orientation.toUpperCase()}}</div><div class="card-interpretation">${{c.interpretation}}</div></div>`;
  }});
  html += `</div>`;
  el.innerHTML = html;
}}

async function loadHistory() {{
  const r = await fetch('/api/history');
  const readings = await r.json();
  const el = document.getElementById('history');
  el.innerHTML = readings.map(r => 
    `<div class="reading"><strong>${{r.reader}} — ${{r.ts.slice(0,10)}}</strong><br>${{r.cards.map(c => c.name).join(', ')}}</div>`
  ).join('');
}}

window.onload = loadHistory;
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/draw", methods=["POST"])
def api_draw():
    data = request.json or {}
    spread = data.get("spread", "three_card")
    reading = generate_reading(spread)
    save_reading(reading)
    return jsonify(reading)

@app.route("/api/history")
def api_history():
    return jsonify(load_recent_readings())


if __name__ == "__main__":
    run_relic(
        app,
        default_port=5015,
        env_var="RELIC_TAROT_PORT",
        banner=[
            "Relic: relic-tarot",
            "http://localhost:{port}",
        ],
    )
