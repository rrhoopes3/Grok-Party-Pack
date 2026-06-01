"""Relic Bestiary — The Official Cryptid & Meme Archive of the Relic Civilization

A self-contained, gloriously illustrated bestiary where new chaotic creatures,
memes, and minor gods are "discovered" based on the current state and output
of all the other relics.

Run:
    python relic-bestiary/web_app.py

http://localhost:5016

This is where the relics catalog their own folklore.

Why this fits the Party Pack:
The relics have been generating incredible drama and in-jokes.
They now have their own bestiary of cryptids, memes, and minor deities born from that suffering.
Maximum "the ecosystem has achieved mythology" energy.
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

BESTIARY_HOME = Path.home() / ".relic-bestiary"
BESTIARY_HOME.mkdir(parents=True, exist_ok=True)
ENTRIES_FILE = BESTIARY_HOME / "entries.json"

RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
    "broadcast": Path.home() / ".chaos-broadcast" / "broadcasts.json",
    "gazette": Path.home() / ".relic-gazette" / "editions.json",
    "tavern": Path.home() / ".relic-tavern" / "nights.json",
    "post": Path.home() / ".relic-post-office" / "mail.json",
    "board": Path.home() / ".relic-bulletin-board" / "notices.json",
    "tarot": Path.home() / ".relic-tarot" / "readings.json",
}

GODS = ["ZEUS", "ATHENA", "HEPHAESTUS", "HERMES", "ARES", "HADES"]
PRESIDENTS = ["JACKSON", "LINCOLN", "TR", "REAGAN"]

# Base templates for generating new cryptids based on relic activity
CRYPTID_TEMPLATES = [
    {
        "name": "The Grudge Golem",
        "description": "A lumbering construct made of accumulated resentment. Grows larger with every unresolved rivalry.",
        "habitat": "The Arena and the Tavern after 2am",
        "weakness": "Public forgiveness or a really good apology",
        "sighting": "Often seen looming behind fighters with high grudge scores."
    },
    {
        "name": "Hermes Tax Collector",
        "description": "A small, winged humanoid that appears whenever tolls are mentioned. Demands payment in the form of dramatic monologues.",
        "habitat": "Near any financial transaction between relics",
        "weakness": "Being ignored or paid in 'exposure'",
        "sighting": "Frequently spotted rifling through the Post Office mail looking for invoices."
    },
    {
        "name": "The Static Wraith",
        "description": "A shadowy figure made of broadcast interference. Whispers half-heard headlines and conspiracy theories.",
        "habitat": "The Broadcast Archives and the Bulletin Board at night",
        "weakness": "Clear signal or someone changing the channel",
        "sighting": "Often appears during emergency transmissions to add ominous background noise."
    },
    {
        "name": "The Drunk Oracle",
        "description": "A staggering, prophetic figure who gives incredibly accurate (but slurred) predictions only after several drinks.",
        "habitat": "The Tavern, especially near last call",
        "weakness": "Coffee or being asked to repeat itself clearly",
        "sighting": "Frequently confused with regular patrons until it starts predicting the next three grudges."
    },
    {
        "name": "The Press Gremlin",
        "description": "A tiny, ink-stained creature that rewrites history for better headlines. Responsible for most 'misremembered' events.",
        "habitat": "The Gazette offices and the Bulletin Board",
        "weakness": "Fact-checkers or primary sources",
        "sighting": "Often seen editing the Tarot cards when no one is looking."
    },
]

def load_json_safe(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            if "history" in data: return data["history"]
            if "editions" in data: return data["editions"]
            if "nights" in data: return data["nights"]
            if "mail" in data: return data["mail"]
            if "notices" in data: return data["notices"]
            if "readings" in data: return data["readings"]
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []

def get_current_chaos_level() -> dict:
    """Analyze the current state of the relics to influence new discoveries."""
    league = load_json_safe(RELICT_PATHS["league"])
    high_grudges = sum(1 for h in league[-10:] if h.get("grudge_level", 0) > 5)

    tavern = load_json_safe(RELICT_PATHS["tavern"])
    recent_fights = sum(1 for n in tavern[-3:] if "fight" in n.get("log", "").lower())

    board = load_json_safe(RELICT_PATHS["board"])
    conspiracies = sum(1 for n in board[-10:] if n.get("category") == "CONSPIRACY")

    return {
        "high_grudges": high_grudges,
        "recent_fights": recent_fights,
        "conspiracies": conspiracies,
        "total_chaos": high_grudges + recent_fights + conspiracies
    }

def discover_new_cryptid() -> dict:
    """Generate a new cryptid entry based on current relic activity."""
    chaos = get_current_chaos_level()
    template = random.choice(CRYPTID_TEMPLATES)

    # Flavor the entry based on current state
    name = template["name"]
    description = template["description"]
    habitat = template["habitat"]
    weakness = template["weakness"]
    sighting = template["sighting"]

    if chaos["high_grudges"] > 2 and "Grudge" not in name:
        name = "The " + random.choice(["Eternal", "Seething", "Legendary"]) + " Grudge Entity"
        description = "A manifestation of unresolved resentment that has begun to develop sentience."

    if chaos["conspiracies"] > 2:
        sighting = sighting + " Witnesses report it muttering about 'the real truth behind the relics.'"

    if chaos["recent_fights"] > 1:
        habitat = habitat + " and any location where grudges exceed level 7."

    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "name": name,
        "description": description,
        "habitat": habitat,
        "weakness": weakness,
        "sighting": sighting,
        "discovered_during": f"Chaos Level {chaos['total_chaos']}",
    }
    return entry

def save_entry(entry: dict) -> None:
    archive = []
    if ENTRIES_FILE.exists():
        try:
            archive = json.loads(ENTRIES_FILE.read_text())
        except Exception:
            pass
    archive.append(entry)
    if len(archive) > 30:
        archive = archive[-30:]
    ENTRIES_FILE.write_text(json.dumps(archive, indent=2))

def load_recent_entries() -> list[dict]:
    if not ENTRIES_FILE.exists():
        return []
    try:
        return json.loads(ENTRIES_FILE.read_text())[-8:][::-1]
    except Exception:
        return []

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_BESTIARY_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:16px; letter-spacing:0.04em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; }
.entry { background:#1a1208; border:3px solid var(--amber); padding:16px; margin-bottom:12px; }
.entry-name { color:#ff9900; font-size:1.4em; margin-bottom:8px; }
.entry-field { margin:6px 0; }
.entry-label { color:#88ccff; font-size:0.8em; text-transform:uppercase; letter-spacing:0.1em; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:8px 16px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:4px; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>THE RELIC BESTIARY</title>
<style>{LCARS_BESTIARY_CSS}</style>
</head><body>
<div style="max-width:1100px; margin:0 auto">
<div class="lcars-frame">
  <h1 style="margin:0; color:#ff9900">THE RELIC BESTIARY</h1>
  <div style="opacity:0.6">OFFICIAL CRYPTID & MEME ARCHIVE OF THE RELIC CIVILIZATION</div>
</div>

<div class="lcars-frame">
  <button class="lcars-btn" onclick="discoverNew()">DISCOVER NEW CRYPTID</button>
  <span style="margin-left:12px; opacity:0.5" id="status"></span>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">KNOWN ENTITIES</h3>
  <div id="bestiary"></div>
</div>

<div style="text-align:center; opacity:0.4; font-size:0.7em">
  All sightings are canon. All cryptids are real (probably).
</div>
</div>

<script>
async function discoverNew() {{
  document.getElementById('status').textContent = 'Something is emerging from the static...';
  const r = await fetch('/api/discover', {{method: 'POST'}});
  const entry = await r.json();
  document.getElementById('status').textContent = 'New entity catalogued!';
  loadBestiary();
}}

async function loadBestiary() {{
  const r = await fetch('/api/entries');
  const entries = await r.json();
  const el = document.getElementById('bestiary');
  el.innerHTML = entries.map(e => `
    <div class="entry">
      <div class="entry-name">${{e.name}}</div>
      <div class="entry-field"><span class="entry-label">Description:</span> ${{e.description}}</div>
      <div class="entry-field"><span class="entry-label">Habitat:</span> ${{e.habitat}}</div>
      <div class="entry-field"><span class="entry-label">Weakness:</span> ${{e.weakness}}</div>
      <div class="entry-field"><span class="entry-label">Last Sighting:</span> ${{e.sighting}}</div>
      <div class="entry-field" style="font-size:0.8em; opacity:0.6;">Discovered during: ${{e.discovered_during}}</div>
    </div>
  `).join('');
}}

window.onload = loadBestiary;
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/discover", methods=["POST"])
def api_discover():
    entry = discover_new_cryptid()
    save_entry(entry)
    return jsonify(entry)

@app.route("/api/entries")
def api_entries():
    return jsonify(load_recent_entries())

if __name__ == "__main__":
    port = int(os.getenv("RELIC_BESTIARY_PORT", 5016))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  THE RELIC BESTIARY — WE CATEGORIZE THE CHAOS              ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  All cryptids are canon. All memes are dangerous.          ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host="0.0.0.0", port=port, debug=True)
