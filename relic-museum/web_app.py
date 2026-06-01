"""Relic Museum — The Grok Party Pack Museum of Atrocities

A self-contained, gloriously LCARS-flavored museum that hoovers up generated
artifacts from the other chaotic relics and displays them as sacred exhibits.

Run:
    python relic-museum/web_app.py

http://localhost:5006

It scans:
- ~/.chronicler/myths.json          → Sagas Wing
- ~/.pantheon-league/league.json    → Propaganda Hall + Grudge Codex
- ~/.chaos-broadcast/broadcasts.json → Broadcast Archives

Curators (random gods + presidents) offer commentary.

This is the place where the chaos becomes history.

Why this fits the Party Pack:
The relics were generating beautiful, unhinged output in isolation.
Now they have a museum where their suffering and drama is preserved,
curated, and commented on by the very same gods and presidents who cause it.
Maximum "the lore is accumulating" energy.
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

MUSEUM_HOME = Path.home() / ".relic-museum"
MUSEUM_HOME.mkdir(parents=True, exist_ok=True)

RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
    "broadcast": Path.home() / ".chaos-broadcast" / "broadcasts.json",
}

CURATORS = [
    "ZEUS", "ATHENA", "HEPHAESTUS", "HERMES", "ARES", "HADES",
    "JACKSON", "LINCOLN", "TR", "REAGAN"
]

CURATOR_QUOTES = {
    "ZEUS": ["This one has the proper thunder.", "I would smite the author personally."],
    "ATHENA": ["Strategically sound propaganda.", "The subtext is doing the heavy lifting."],
    "HEPHAESTUS": ["The craftsmanship on this saga is acceptable.", "Too many words, not enough anvils."],
    "HERMES": ["Someone is going to pay for this. Probably you."],
    "ARES": ["Blood. Good.", "More screaming next time."],
    "HADES": ["This one belongs in the ledger.", "Delightfully doomed."],
    "JACKSON": ["This is what happens when you let the common man write."],
    "LINCOLN": ["A house divided against itself cannot stand... but this exhibit is fine."],
    "TR": ["Bully for the author.", "Speak softly and carry a bigger poster."],
    "REAGAN": ["There you go again... making excellent relics."],
}

def load_json_safe(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            # Handle league state shape
            if "history" in data:
                return data["history"]
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []

def gather_exhibits() -> list[dict]:
    exhibits = []

    # Chronicler sagas
    for m in load_json_safe(RELICT_PATHS["chronicler"]):
        if "saga" in m or "text" in m:
            exhibits.append({
                "type": "saga",
                "wing": "Sagas Wing",
                "title": m.get("headline", "Untitled Epic"),
                "content": m.get("saga", m.get("text", "")),
                "date": m.get("ts", ""),
                "source": "Chronicler",
            })

    # League history (as propaganda)
    for h in load_json_safe(RELICT_PATHS["league"]):
        if "poster" in h or "flavor" in h:
            exhibits.append({
                "type": "propaganda",
                "wing": "Propaganda Hall",
                "title": f"{h.get('winner', '?')} Defeats {h.get('loser', '?')}",
                "content": h.get("poster", h.get("flavor", "")),
                "date": h.get("ts", ""),
                "source": "Pantheon League",
                "grudge": h.get("grudge_level"),
            })

    # Broadcasts
    for b in load_json_safe(RELICT_PATHS["broadcast"]):
        exhibits.append({
            "type": "broadcast",
            "wing": "Broadcast Archives",
            "title": b.get("headline", "Emergency Transmission"),
            "content": b.get("text", ""),
            "date": b.get("ts", ""),
            "source": "Chaos Broadcast",
            "frequency": b.get("frequency"),
        })

    # Sort by date descending
    exhibits.sort(key=lambda x: x.get("date", ""), reverse=True)
    return exhibits

def get_random_curator_comment(exhibit: dict) -> str:
    curator = random.choice(CURATORS)
    base = random.choice(CURATOR_QUOTES.get(curator, ["Remarkable."]))
    flavor = ""
    if exhibit["type"] == "saga":
        flavor = " The gods approve of this level of suffering."
    elif exhibit["type"] == "propaganda":
        flavor = " This one would look excellent on the side of a temple."
    elif exhibit["type"] == "broadcast":
        flavor = " The signal interference only adds to the drama."
    return f"Curator {curator}: \"{base}{flavor}\""

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_MUSEUM_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:16px; letter-spacing:0.04em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; }
.exhibit { background:#010203; border:3px solid var(--red); padding:14px; margin-bottom:12px; }
.exhibit-header { color:#ff9900; font-size:1.1em; margin-bottom:6px; }
.exhibit-meta { font-size:0.75em; opacity:0.6; margin-bottom:8px; }
.exhibit-content { font-family:monospace; white-space:pre-wrap; line-height:1.3; background:#000; padding:10px; border-left:4px solid #ff9900; }
.curator { font-style:italic; color:#88ccff; margin-top:8px; font-size:0.9em; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:6px 14px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:3px; }
.wing { color:#33ccff; font-size:0.8em; letter-spacing:0.1em; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>THE RELIC MUSEUM</title>
<style>{LCARS_MUSEUM_CSS}</style>
</head><body>
<div style="max-width:1100px; margin:0 auto">
<div class="lcars-frame">
  <h1 style="margin:0; color:#ff9900">THE RELIC MUSEUM</h1>
  <div style="opacity:0.6">OFFICIAL ARCHIVE OF PARTY PACK ATROCITIES • EST. CHAOS ERA I</div>
</div>

<div class="lcars-frame">
  <button class="lcars-btn" onclick="refresh()">RESCAN ALL RELICS</button>
  <span style="margin-left:12px; opacity:0.5" id="status"></span>
</div>

<div id="museum"></div>

<div style="text-align:center; opacity:0.4; font-size:0.7em; margin-top:20px">
  All relics are canon. All suffering is archived. The curators are always watching.
</div>
</div>

<script>
async function refresh() {{
  document.getElementById('status').textContent = 'Cataloguing atrocities...';
  const r = await fetch('/api/exhibits');
  const exhibits = await r.json();
  render(exhibits);
  document.getElementById('status').textContent = exhibits.length + ' exhibits on display';
}}

function render(exhibits) {{
  const container = document.getElementById('museum');
  container.innerHTML = '';

  const byWing = {{}};
  exhibits.forEach(e => {{
    if (!byWing[e.wing]) byWing[e.wing] = [];
    byWing[e.wing].push(e);
  }});

  Object.keys(byWing).forEach(wing => {{
    const frame = document.createElement('div');
    frame.className = 'lcars-frame';
    frame.innerHTML = `<h3 class="wing">${{wing}}</h3>`;

    byWing[wing].forEach(ex => {{
      const div = document.createElement('div');
      div.className = 'exhibit';
      let meta = ex.date ? ex.date.slice(0,16) + ' • ' + ex.source : ex.source;
      if (ex.grudge) meta += ' • Grudge ' + ex.grudge;

      div.innerHTML = `
        <div class="exhibit-header">${{ex.title}}</div>
        <div class="exhibit-meta">${{meta}}</div>
        <div class="exhibit-content">${{ex.content}}</div>
        <div class="curator">${{ex.curator_comment}}</div>
      `;
      frame.appendChild(div);
    }});

    container.appendChild(frame);
  }});
}}

window.onload = refresh;
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/exhibits")
def api_exhibits():
    exhibits = gather_exhibits()
    for ex in exhibits:
        ex["curator_comment"] = get_random_curator_comment(ex)
    return jsonify(exhibits)

if __name__ == "__main__":
    port = int(os.getenv("RELIC_MUSEUM_PORT", 5006))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  THE RELIC MUSEUM — OFFICIAL PARTY PACK ARCHIVE            ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  Where the chaos becomes canon.                            ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host="0.0.0.0", port=port, debug=True)
