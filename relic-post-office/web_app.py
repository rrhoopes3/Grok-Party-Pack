"""Relic Post Office — The Official Postal Service of the Relic Civilization

A self-contained, gloriously bureaucratic mail system where the gods and presidents
write letters to each other about the chaos happening in the other relics.

Run:
    python relic-post-office/web_app.py

http://localhost:5013

This is where the relics communicate when they're not drinking or fighting.

Why this fits the Party Pack:
The relics have been generating incredible drama.
They now have an official postal service to complain about it, scheme about it,
and occasionally send each other passive-aggressive thank-you notes.
Maximum "the characters have mail" energy.
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

POST_HOME = Path.home() / ".relic-post-office"
POST_HOME.mkdir(parents=True, exist_ok=True)
MAIL_FILE = POST_HOME / "mail.json"

RELICT_PATHS = {
    "chronicler": Path.home() / ".chronicler" / "myths.json",
    "league": Path.home() / ".pantheon-league" / "league.json",
    "broadcast": Path.home() / ".chaos-broadcast" / "broadcasts.json",
    "gazette": Path.home() / ".relic-gazette" / "editions.json",
    "tavern": Path.home() / ".relic-tavern" / "nights.json",
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
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []

def get_recent_drama() -> list[str]:
    """Harvest fresh material for correspondence."""
    lines = []

    for h in load_json_safe(RELICT_PATHS["league"])[-4:]:
        if "flavor" in h:
            lines.append(f"{h.get('winner')} defeated {h.get('loser')}. {h['flavor'][:100]}")

    for g in load_json_safe(RELICT_PATHS["gazette"])[-2:]:
        for headline in g.get("headlines", [])[:2]:
            lines.append(f"Headline: {headline.get('headline', '')}")

    for b in load_json_safe(RELICT_PATHS["broadcast"])[-2:]:
        if "headline" in b:
            lines.append(b["headline"])

    return lines or ["The relics have been relatively well-behaved. This is concerning."]

def generate_letter(from_char: str, to_char: str, topic: str = None) -> dict:
    """Generate a dramatic letter between two characters."""
    drama = get_recent_drama()
    drama_snippet = random.choice(drama) if drama else "nothing of note has occurred"

    if not topic:
        topic = random.choice([
            "the recent arena events",
            "the state of the treasury",
            "certain individuals' behavior at the tavern",
            "the latest headlines",
            "divine interventions",
            "the current market situation",
        ])

    if from_char in GODS:
        opener = random.choice([
            f"My dear {to_char},",
            f"{to_char},",
            f"To the esteemed {to_char},",
        ])
    else:
        opener = random.choice([
            f"To {to_char},",
            f"My fellow {to_char},",
            f"{to_char}, you old scoundrel,",
        ])

    body_lines = [
        opener,
        "",
        f"I write to you concerning {topic}.",
        "",
        f"As you may have heard, {drama_snippet}.",
    ]

    if from_char == "HERMES":
        body_lines.append("I have already prepared an invoice for my services in this matter.")
    elif from_char == "ARES":
        body_lines.append("There was much screaming. It was glorious.")
    elif from_char == "JACKSON":
        body_lines.append("I handled it the way any true American would.")
    elif from_char == "ATHENA":
        body_lines.append("I have several strategic observations, if you're interested.")

    body_lines.append("")
    body_lines.append("Yours in chaos,")
    body_lines.append(from_char)

    letter = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "from": from_char,
        "to": to_char,
        "topic": topic,
        "text": "\n".join(body_lines),
    }
    return letter

def save_letter(letter: dict) -> None:
    archive = []
    if MAIL_FILE.exists():
        try:
            archive = json.loads(MAIL_FILE.read_text())
        except Exception:
            pass
    archive.append(letter)
    if len(archive) > 100:
        archive = archive[-100:]
    MAIL_FILE.write_text(json.dumps(archive, indent=2))

def load_recent_mail() -> list[dict]:
    if not MAIL_FILE.exists():
        return []
    try:
        return json.loads(MAIL_FILE.read_text())[-8:][::-1]
    except Exception:
        return []

# ── UI ──────────────────────────────────────────────────────────────────────

LCARS_POST_CSS = """
:root { --amber:#ff9900; --red:#ff3344; --teal:#33ccff; --text:#ffe4c4; }
body { font-family:"Antonio","Oswald",sans-serif; background:#000; color:var(--text); margin:0; padding:16px; letter-spacing:0.04em; }
.lcars-frame { border:5px solid var(--amber); background:#05070a; padding:16px; margin-bottom:16px; }
.letter { background:#f4e8c1; color:#111; padding:20px; font-family:Georgia, serif; white-space:pre-wrap; line-height:1.5; border:3px solid #8b4513; margin-bottom:12px; }
.lcars-btn { background:#ff9900; color:#000; border:0; padding:8px 16px; font-family:inherit; cursor:pointer; text-transform:uppercase; margin:4px; }
.mail-entry { background:#010203; border-left:4px solid #ff9900; padding:12px; margin:8px 0; font-size:0.9em; }
"""

INDEX = f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>THE RELIC POST OFFICE</title>
<style>{LCARS_POST_CSS}</style>
</head><body>
<div style="max-width:980px; margin:0 auto">
<div class="lcars-frame">
  <h1 style="margin:0; color:#ff9900">THE RELIC POST OFFICE</h1>
  <div style="opacity:0.6">OFFICIAL MAIL SERVICE OF THE RELIC CIVILIZATION</div>
</div>

<div class="lcars-frame">
  <h3 style="color:#ff9900">COMPOSE NEW CORRESPONDENCE</h3>
  <select id="from"></select> to
  <select id="to"></select>
  <button class="lcars-btn" onclick="sendLetter()" style="margin-top:8px">SEND LETTER</button>
</div>

<div class="lcars-frame">
  <h3 style="color:#33ccff">RECENT MAIL</h3>
  <div id="mail-log"></div>
</div>

<div style="text-align:center; opacity:0.4; font-size:0.7em">
  All correspondence is canon. All stamps are forged.
</div>
</div>

<script>
const characters = ["ZEUS","ATHENA","HEPHAESTUS","HERMES","ARES","HADES","JACKSON","LINCOLN","TR","REAGAN"];

function populateSelects() {{
  const fromSel = document.getElementById('from');
  const toSel = document.getElementById('to');
  characters.forEach(c => {{
    fromSel.innerHTML += `<option value="${{c}}">${{c}}</option>`;
    toSel.innerHTML += `<option value="${{c}}">${{c}}</option>`;
  }});
}}

async function sendLetter() {{
  const from = document.getElementById('from').value;
  const to = document.getElementById('to').value;
  if (from === to) {{ alert("A god cannot write to themselves. That's what the tavern is for."); return; }}
  const r = await fetch('/api/send', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{from, to}})
  }});
  const letter = await r.json();
  alert("Letter sent. The recipient will be... informed.");
  loadMail();
}}

async function loadMail() {{
  const r = await fetch('/api/mail');
  const mail = await r.json();
  const el = document.getElementById('mail-log');
  el.innerHTML = mail.map(m => 
    `<div class="mail-entry"><strong>${{m.from}} → ${{m.to}}</strong><br>${{m.text.slice(0,220)}}...</div>`
  ).join('');
}}

window.onload = () => {{
  populateSelects();
  loadMail();
}};
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.json or {}
    from_char = data.get("from", random.choice(GODS))
    to_char = data.get("to", random.choice(PRESIDENTS))
    letter = generate_letter(from_char, to_char)
    save_letter(letter)
    return jsonify(letter)

@app.route("/api/mail")
def api_mail():
    return jsonify(load_recent_mail())

if __name__ == "__main__":
    port = int(os.getenv("RELIC_POST_OFFICE_PORT", 5013))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  THE RELIC POST OFFICE — THE MAIL MUST GO THROUGH          ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  All letters are canon. All stamps are forged.             ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host="0.0.0.0", port=port, debug=True)
