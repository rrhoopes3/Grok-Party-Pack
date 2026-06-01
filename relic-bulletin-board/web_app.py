"""Relic Bulletin Board — The Official Corkboard of the Relic Civilization

A self-contained, gloriously messy public notice board where the gods, presidents,
and relic entities pin wanted posters, love letters, conspiracy theories, and
passive-aggressive notices.

Run:
    python relic-bulletin-board/web_app.py

http://localhost:5014

This is the public square of the relic civilization.

Why this fits the Party Pack:
The relics have been generating incredible drama.
They now have a physical (metaphorical) place to publicly shame each other,
post missing persons reports for lost grudges, and declare their undying love
for each other's suffering. Maximum corkboard chaos energy.
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

BOARD_HOME = Path.home() / ".relic-bulletin-board"
BOARD_HOME.mkdir(parents=True, exist_ok=True)
NOTICES_FILE = BOARD_HOME / "notices.json"

RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
    "broadcast": Path.home() / ".chaos-broadcast" / "broadcasts.json",
    "gazette": Path.home() / ".relic-gazette" / "editions.json",
    "tavern": Path.home() / ".relic-tavern" / "nights.json",
    "post": Path.home() / ".relic-post-office" / "mail.json",
}

GODS = ["ZEUS", "ATHENA", "HEPHAESTUS", "HERMES", "ARES", "HADES"]
PRESIDENTS = ["JACKSON", "LINCOLN", "TR", "REAGAN"]

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
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []

def get_random_flavor() -> str:
    """Steal real drama for notices."""
    candidates = []

    for h in load_json_safe(RELICT_PATHS["league"])[-3:]:
        if "flavor" in h:
            candidates.append(f"{h.get('winner')} defeated {h.get('loser')}. {h['flavor'][:80]}")

    for g in load_json_safe(RELICT_PATHS["gazette"])[-2:]:
        for headline in g.get("headlines", [])[:2]:
            candidates.append(headline.get('headline', ''))

    return random.choice(candidates) if candidates else "Nothing of note has occurred. This is suspicious."

def generate_notice(category: str = None) -> dict:
    """Generate a chaotic public notice."""
    if not category:
        category = random.choice(["WANTED", "NOTICE", "LOVE LETTER", "CONSPIRACY", "LOST & FOUND", "PUBLIC SERVICE"])

    flavor = get_random_flavor()
    author = random.choice(GODS + PRESIDENTS)

    if category == "WANTED":
        headline = f"WANTED: {random.choice(['Information', 'Revenge', 'A Good Time'])}"
        body = f"Regarding: {flavor}\nReward: Eternal Grudge or Cash Equivalent"
    elif category == "LOVE LETTER":
        headline = "TO WHOM IT MAY CONCERN (YOU KNOW WHO YOU ARE)"
        body = f"My dearest nemesis,\n\n{flavor}\n\nYours in complicated feelings,\n{author}"
    elif category == "CONSPIRACY":
        headline = "THEY DON'T WANT YOU TO KNOW"
        body = f"The recent events ({flavor}) are clearly part of a larger plot by the other side.\nWake up, sheeple of the relics."
    else:
        headline = f"{category}: {random.choice(['Urgent', 'Important', 'For Your Eyes Only'])}"
        body = flavor

    notice = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "category": category,
        "author": author,
        "headline": headline,
        "body": body,
    }
    return notice

def save_notice(notice: dict) -> None:
    archive = []
    if NOTICES_FILE.exists():
        try:
            archive = json.loads(NOTICES_FILE.read_text())
        except Exception:
            pass
    archive.append(notice)
    if len(archive) > 50:
        archive = archive[-50:]
    NOTICES_FILE.write_text(json.dumps(archive, indent=2))

def load_recent_notices() -> list[dict]:
    if not NOTICES_FILE.exists():
        return []
    try:
        return json.loads(NOTICES_FILE.read_text())[-12:][::-1]
    except Exception:
        return []

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_BOARD_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:16px; letter-spacing:0.04em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; }
.corkboard { background:#3a2f1f; border:8px solid #5c4033; padding:20px; display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:16px; min-height:400px; }
.notice { background:#f4e8c1; color:#111; padding:12px; border:3px solid #8b4513; font-family:Georgia, serif; white-space:pre-wrap; line-height:1.3; transform:rotate(var(--rot, 0deg)); box-shadow: 3px 3px 0 #00000044; }
.notice-headline { font-weight:bold; color:#8b0000; margin-bottom:6px; }
.notice-author { font-size:0.75em; font-style:italic; color:#444; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:8px 16px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:4px; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>THE RELIC BULLETIN BOARD</title>
<style>{LCARS_BOARD_CSS}</style>
</head><body>
<div style="max-width:1200px; margin:0 auto">
<div class="lcars-frame">
  <h1 style="margin:0; color:#ff9900">THE RELIC BULLETIN BOARD</h1>
  <div style="opacity:0.6">OFFICIAL CORKBOARD OF THE RELIC CIVILIZATION</div>
</div>

<div class="lcars-frame">
  <button class="lcars-btn" onclick="pinNewNotice()">PIN NEW NOTICE</button>
  <span style="margin-left:12px; opacity:0.5" id="status"></span>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">CURRENT NOTICES</h3>
  <div class="corkboard" id="board"></div>
</div>

<div style="text-align:center; opacity:0.4; font-size:0.7em">
  All notices are canon. All pins are stolen.
</div>
</div>

<script>
async function pinNewNotice() {{
  document.getElementById('status').textContent = 'Pinning...';
  const r = await fetch('/api/pin', {{method: 'POST'}});
  const notice = await r.json();
  document.getElementById('status').textContent = 'Notice pinned!';
  loadBoard();
}}

async function loadBoard() {{
  const r = await fetch('/api/notices');
  const notices = await r.json();
  const board = document.getElementById('board');
  board.innerHTML = '';
  notices.forEach(n => {{
    const div = document.createElement('div');
    div.className = 'notice';
    div.style.setProperty('--rot', (Math.random() * 6 - 3) + 'deg');
    div.innerHTML = `
      <div class="notice-headline">[${{n.category}}] ${{n.headline}}</div>
      <div>${{n.body}}</div>
      <div class="notice-author">— ${{n.author}}, ${{n.ts.slice(0,10)}}</div>
    `;
    board.appendChild(div);
  }});
}}

window.onload = loadBoard;
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/pin", methods=["POST"])
def api_pin():
    notice = generate_notice()
    save_notice(notice)
    return jsonify(notice)

@app.route("/api/notices")
def api_notices():
    return jsonify(load_recent_notices())

if __name__ == "__main__":
    port = int(os.getenv("RELIC_BULLETIN_BOARD_PORT", 5014))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  THE RELIC BULLETIN BOARD — PIN YOUR CHAOS HERE            ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  All notices are canon. All pins are stolen.               ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host="0.0.0.0", port=port, debug=True)
