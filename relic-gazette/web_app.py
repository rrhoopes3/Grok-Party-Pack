"""Relic Gazette — The Official Newspaper of the Relic Civilization

A self-contained, theatrical newspaper that turns the output of all the other
relics into proper in-universe journalism, editorials, sports, and classifieds.

Run:
    python relic-gazette/web_app.py

http://localhost:5009

This is the voice of the relics speaking to each other.

Why this fits the Party Pack:
The relics have been generating incredible lore in isolation.
They now have their own press. The civilization is complete.
Maximum "I am reading the gods' own newspaper about my last tool call" energy.
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

GAZETTE_HOME = Path.home() / ".relic-gazette"
GAZETTE_HOME.mkdir(parents=True, exist_ok=True)
ARCHIVE_FILE = GAZETTE_HOME / "editions.json"

RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
    "broadcast": Path.home() / ".chaos-broadcast" / "broadcasts.json",
    "museum": Path.home() / ".relic-museum",
    "radio": Path.home() / ".relic-radio",
    "oracle": Path.home() / ".relic-oracle" / "prophecies.json",
}

GODS = ["ZEUS", "ATHENA", "HEPHAESTUS", "HERMES", "ARES", "HADES"]
PRESIDENTS = ["JACKSON", "LINCOLN", "TR", "REAGAN"]


def get_recent_material() -> dict[str, list[str]]:
    """Harvest fresh drama from the other relics."""
    material = {
        "sagas": [],
        "matches": [],
        "broadcasts": [],
        "prophecies": [],
    }

    for m in load_json_safe(RELICT_PATHS["chronicler"])[-3:]:
        if "saga" in m or "text" in m:
            material["sagas"].append(m.get("saga", m.get("text", ""))[:200])

    for h in load_json_safe(RELICT_PATHS["league"])[-4:]:
        if "flavor" in h:
            material["matches"].append(f"{h.get('winner')} defeated {h.get('loser')}. {h['flavor'][:140]}")

    for b in load_json_safe(RELICT_PATHS["broadcast"])[-2:]:
        if "headline" in b:
            material["broadcasts"].append(b["headline"])

    for p in load_json_safe(RELICT_PATHS["oracle"])[-3:]:
        if "text" in p:
            material["prophecies"].append(p["text"][:180])

    return material

def generate_edition() -> dict:
    """Generate a full newspaper edition."""
    mat = get_recent_material()
    edition_date = datetime.utcnow().strftime("%Y-%m-%d")

    headlines = []

    # Front page
    if mat["matches"]:
        match = random.choice(mat["matches"])
        headlines.append({
            "section": "FRONT PAGE",
            "headline": "GRUDGE BOILS OVER IN THE ARENA",
            "byline": f"By {random.choice(GODS)}",
            "body": f"In a stunning display, {match}. The Pantheon is said to be taking notes."
        })

    if mat["sagas"]:
        saga = random.choice(mat["sagas"])
        headlines.append({
            "section": "CULTURE",
            "headline": "NEW EPIC FROM THE FIELD",
            "byline": "Arts Desk",
            "body": f"The Chronicler has delivered another masterpiece. Excerpt: \"{saga[:220]}...\""
        })

    if mat["broadcasts"]:
        b = random.choice(mat["broadcasts"])
        headlines.append({
            "section": "BREAKING",
            "headline": "EMERGENCY TRANSMISSION INTERCEPTED",
            "byline": "Signal Desk",
            "body": f"Authorities are investigating reports of: {b}"
        })

    if mat["prophecies"]:
        p = random.choice(mat["prophecies"])
        headlines.append({
            "section": "OPINION",
            "headline": "THE COUNCIL HAS SPOKEN — AGAIN",
            "byline": "Editorial Board",
            "body": f"Latest prophecy from the Oracle: {p[:200]}..."
        })

    # Always have at least one piece
    if not headlines:
        headlines.append({
            "section": "FRONT PAGE",
            "headline": "RELICS REMAIN ACTIVE",
            "byline": "Staff",
            "body": "In a shocking turn of events, the relics continue to generate drama at an alarming rate."
        })

    # Add a classified or sports piece
    headlines.append({
        "section": "CLASSIFIEDS",
        "headline": "WANTED: MORE SUFFERING",
        "byline": "Hermes",
        "body": "Will pay top toll for interesting tool failures. Contact via static."
    })

    edition = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "date": edition_date,
        "edition_number": len(load_archive()) + 1,
        "headlines": headlines,
    }
    return edition

def save_edition(edition: dict) -> None:
    archive = load_archive()
    archive.append(edition)
    if len(archive) > 50:
        archive = archive[-50:]
    ARCHIVE_FILE.write_text(json.dumps(archive, indent=2))

def load_archive() -> list[dict]:
    if ARCHIVE_FILE.exists():
        try:
            return json.loads(ARCHIVE_FILE.read_text())
        except Exception:
            pass
    return []

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_GAZETTE_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:12px; letter-spacing:0.04em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; max-width:1100px; margin-left:auto; margin-right:auto; }
.newspaper { background:#f4e8c1; color:#111; padding:20px; font-family:Georgia, serif; line-height:1.5; }
.newspaper h1 { font-family:"Antonio","Oswald",sans-serif; color:#000; border-bottom:4px double #000; padding-bottom:8px; }
.article { margin-bottom:24px; }
.article-headline { font-size:1.35em; font-weight:bold; color:#000; margin:4px 0; }
.article-byline { font-size:0.8em; font-style:italic; color:#444; margin-bottom:6px; }
.article-body { font-size:1em; }
.section { color:#ff3344; font-size:0.75em; letter-spacing:0.15em; text-transform:uppercase; margin-top:20px; border-top:1px solid #000; padding-top:4px; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:8px 16px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:4px; }
.edition { background:#010203; border:3px solid #ff9900; padding:14px; margin-bottom:10px; font-size:0.9em; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>THE RELIC GAZETTE</title>
<style>{LCARS_GAZETTE_CSS}</style>
</head><body>
<div class="lcars-frame">
  <h1 style="margin:0; color:#ff9900">THE RELIC GAZETTE</h1>
  <div style="opacity:0.6">THE OFFICIAL NEWSPAPER OF THE RELIC CIVILIZATION</div>
</div>

<div class="lcars-frame">
  <button class="lcars-btn" onclick="generateEdition()">PRINT NEW EDITION</button>
  <span style="margin-left:12px; opacity:0.5" id="status"></span>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">ARCHIVES</h3>
  <div id="archives"></div>
</div>

<div style="text-align:center; opacity:0.4; font-size:0.7em">
  All editions are canon. Read responsibly.
</div>

<script>
async function generateEdition() {{
  document.getElementById('status').textContent = 'The presses are rolling...';
  const r = await fetch('/api/edition', {{method: 'POST'}});
  const ed = await r.json();
  document.getElementById('status').textContent = 'Edition ' + ed.edition_number + ' published';
  loadArchives();
}}

async function loadArchives() {{
  const r = await fetch('/api/archives');
  const archives = await r.json();
  const el = document.getElementById('archives');
  el.innerHTML = archives.map(ed => {{
    let html = `<div class="edition"><strong>EDITION ${{ed.edition_number}}</strong> — ${{ed.date}}<br>`;
    ed.headlines.forEach(h => {{
      html += `<div style="margin:8px 0"><span style="color:#ff3344">${{h.section}}</span><br><strong>${{h.headline}}</strong><br><span style="font-size:0.85em">${{h.body}}</span></div>`;
    }});
    html += `</div>`;
    return html;
  }}).join('');
}}

window.onload = loadArchives;
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/edition", methods=["POST"])
def api_edition():
    edition = generate_edition()
    save_edition(edition)
    return jsonify(edition)

@app.route("/api/archives")
def api_archives():
    return jsonify(load_archive()[-10:][::-1])


if __name__ == "__main__":
    run_relic(
        app,
        default_port=5009,
        env_var="RELIC_GAZETTE_PORT",
        banner=[
            "Relic: relic-gazette",
            "http://localhost:{port}",
        ],
    )
