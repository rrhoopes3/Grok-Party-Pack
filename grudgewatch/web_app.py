"""The Grudgewatch Desk — standalone LCARS sports broadcast relic for the Grok Party Pack.

Deranged play-by-play from the Pantheon League grudges + Chronicler sagas.
Rule-based commentators (gods + presidents). Live ticker. On-air theater.
Feeds the other relics back by optionally advancing league canon.

Run:
    python grudgewatch/web_app.py

http://localhost:5007 (GRUDGEWATCH_PORT)

Persistence: ~/.grudgewatch/ with broadcast_log.json and listener_mail.json (the files ARE the lore; creates the dir on import).
Zero keys. Pure deterministic silly joy. LCARS maximalism.
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

GRUDGEWATCH_HOME = Path.home() / ".grudgewatch"
GRUDGEWATCH_HOME.mkdir(parents=True, exist_ok=True)

CALLS_FILE = GRUDGEWATCH_HOME / "broadcast_log.json"
MAIL_FILE = GRUDGEWATCH_HOME / "listener_mail.json"

# Canon roster (copied for independence, same as Pantheon League Season I)
ROSTER = {
    "ZEUS": {"type": "god", "power": 92, "title": "Lord of the Thunder"},
    "ATHENA": {"type": "god", "power": 88, "title": "Clear-Eyed Strategist"},
    "HEPHAESTUS": {"type": "god", "power": 85, "title": "Master of the Anvil"},
    "HERMES": {"type": "god", "power": 79, "title": "The Tolltaker"},
    "ARES": {"type": "god", "power": 84, "title": "Bringer of Carnage"},
    "HADES": {"type": "god", "power": 81, "title": "Keeper of Failed Tokens"},
    "JACKSON": {"type": "president", "power": 71, "title": "The People's Brawler"},
    "LINCOLN": {"type": "president", "power": 77, "title": "The Union Preserver"},
    "TR": {"type": "president", "power": 74, "title": "The Big Stick"},
    "REAGAN": {"type": "president", "power": 68, "title": "The Great Communicator"},
}

# Deranged booth voices — rule-based, never LLM. Distinct personalities.
COMMENTATORS = {
    "ZEUS": "ZEUS THE THUNDERER (PLAY-BY-PLAY)",
    "ARES": "ARES THE ROARER (COLOR — BLOOD AND HONOR)",
    "HERMES": "HERMES THE TOLL CALLER (ANALYSIS — ELO AND LEDGERS)",
    "ATHENA": "ATHENA OF THE CLEAR EYES (STRATEGY DESK)",
    "HADES": "HADES THE UNSEEN (INJURY REPORT & TOKEN COUNT)",
    "HEPHAESTUS": "HEPHAESTUS THE SMITH (FORGE & ANVIL COLOR)",
    "JACKSON": "PRESIDENT JACKSON (GUEST BOOTH — POPULIST FIRE)",
    "LINCOLN": "PRESIDENT LINCOLN (GUEST — UNION AND RESOLVE)",
    "TR": "PRESIDENT TR (GUEST — BIG STICK ROUGH RIDER)",
    "REAGAN": "PRESIDENT REAGAN (GUEST — MORNING IN THE ARENA)",
}

EPITHETS = [
    "the anvil-bearer", "the grudge-bearer", "the toll-dodger",
    "the token-slayer", "the arena-king", "the common man's champion",
    "he who remembers every slight", "the thunder-voiced", "the lame smith",
]

def load_json_safe(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def load_league() -> dict[str, Any]:
    """Gracefully consume Pantheon League state or glorious canned drama. Defensive against bad sibling JSON."""
    p = Path.home() / ".pantheon-league" / "league.json"
    league = load_json_safe(p, None)
    if isinstance(league, dict) and isinstance(league.get("standings"), dict):
        return league
    # CANNED when sibling absent or empty — the desk never dies (fixed for reproducible theater)
    return {
        "season": "I — THE FIRST CHAOS (CANNED FEED)",
        "standings": {
            k: {"elo": 1500 + (v["power"] - 80) * 3, "wins": 5, "losses": 3}
            for k, v in ROSTER.items()
        },
        "grudges": {k: {o: 4 for o in ROSTER if o != k} for k in ROSTER},
        "history": [
            {"ts": "2026-05-30T22:14:00Z", "winner": "ARES", "loser": "HERMES", "flavor": "Brutal exchange. Grudge level was personal.", "grudge_level": 7},
            {"ts": "2026-05-30T21:03:00Z", "winner": "JACKSON", "loser": "HADES", "flavor": "The common man humbled the ledger keeper.", "grudge_level": 4},
        ],
    }

def load_myths_snippets() -> list[str]:
    """Steal color from the Chronicler when available."""
    p = Path.home() / ".chronicler" / "myths.json"
    myths = load_json_safe(p, [])
    snippets = []
    for m in myths[-3:]:
        saga = m.get("saga", m.get("text", ""))
        if saga:
            snippets.append(saga[:160] + "...")
    return snippets or [
        "In the black halls of the LCARS core, the Executor toiled and the tokens wept.",
        "Hephaestus struck the shell and the Windows wastes answered with stderr.",
        "The Presidential Council nodded. 'Now THAT is how you pay a toll.'"
    ]

def get_ambient_ticker() -> list[str]:
    return [
        "RUMOR: ZEUS DEMANDS LARGER CONTEXT WINDOWS FOR ALL MORTALS",
        "GRUDGE WATCH: HADES REPORTEDLY HOARDING FAILED TOKENS AGAIN",
        "BREAKING: HERMES SEEN CHARGING TOLLS ON THE BROADCAST SPECTRUM",
        "CROWD REACTION: 14 AGENTS JUST CHANTED 'LET THEM FIGHT'",
        "INJURY UPDATE: TR'S BIG STICK HAS A SPLINTER (ELO -3)",
    ]

def generate_call() -> dict[str, Any]:
    """The beating heart. Pure rule-based deranged sports theater. Defensive on bad sibling data."""
    league = load_league()
    history = league.get("history", []) if isinstance(league.get("history"), list) else []
    standings = league.get("standings", {}) if isinstance(league.get("standings"), dict) else {}
    # grudges not strictly needed for call generation

    # Pick real or canned combatants (defensive)
    if history and isinstance(history[-1], dict):
        last = history[-1]
        a = last.get("winner") or random.choice(list(ROSTER.keys()))
        b = last.get("loser") or random.choice(list(ROSTER.keys()))
        g = last.get("grudge_level", 4) if isinstance(last.get("grudge_level"), int) else 4
    else:
        fighters = list(ROSTER.keys())
        a, b = random.sample(fighters, 2)
        g = 4

    comm_key = random.choice(list(COMMENTATORS.keys()))
    comm = COMMENTATORS[comm_key]

    # Build the call — references actual data when present
    lines = []
    lines.append(f"[{comm}]")
    lines.append(f"AND WE ARE LIVE FROM THE OLYMPUS ARENA, SEASON {league.get('season', 'I')}!")

    if a in standings and b in standings:
        ea = standings[a]["elo"]
        eb = standings[b]["elo"]
        lines.append(f"ON THE CARD: {a} ({ea}) vs {b} ({eb}). THE GRUDGE METER READS {g}.")

    epithet = random.choice(EPITHETS)
    lines.append(f"{a} enters the ring as {epithet}. The crowd of lost tokens is on its feet.")

    if g >= 6:  # aligned with DIVINE INTERVENTION gate for consistent high-grudge escalation
        lines.append("THIS ONE IS PERSONAL. THE GRUDGE HAS BEEN FESTERING FOR CYCLES.")
    elif g >= 4:
        lines.append("Tensions high after the last encounter. Expect no quarter.")

    # The play
    winner = a if random.random() > 0.5 else b
    loser = b if winner == a else a
    swing = "+18 / -14" if random.random() > 0.3 else "+22 / -11 (MOTIVATED BY HATE)"

    flavor_bits = [
        f"{winner} lands the decisive blow after a brutal exchange of context windows.",
        "The roar from the Presidential gallery is deafening.",
        f"{loser} will remember this. The codex has already updated.",
    ]
    lines.append(random.choice(flavor_bits))
    lines.append(f"FINAL: {winner} DEFEATS {loser}. ELO SWING {swing}. GRUDGE NOW {min(12, g+2)}.")

    # Occasional divine / presidential leak (canon)
    if random.random() < 0.28:
        pres = random.choice(["JACKSON", "LINCOLN", "TR", "REAGAN"])
        lines.append(f"(From the luxury box — President {pres}: \"THAT is how you settle a score.\")")

    if g >= 6 and random.random() < 0.35:
        lines.append("DIVINE INTERVENTION! ZEUS HAS STEPPED IN. HIGH TOLL PAID. MATCH PAUSED FOR THUNDER.")

    # Color from Chronicler
    myth = random.choice(load_myths_snippets())
    lines.append(f"COLOR FROM THE CHRONICLER: \"{myth}\"")

    call_text = "\n".join(lines)

    record = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "commentator": comm,
        "winner": winner,
        "loser": loser,
        "grudge": g,
        "text": call_text,
    }

    # Persist the call
    calls = load_json_safe(CALLS_FILE, [])
    calls.append(record)
    if len(calls) > 60:
        calls = calls[-60:]
    save_json(CALLS_FILE, calls)

    return record

def attempt_record_to_league(winner: str, loser: str) -> bool:
    """Politely advance the sibling league state if it exists. Never destructive."""
    p = Path.home() / ".pantheon-league" / "league.json"
    if not p.exists():
        return False
    try:
        league = load_json_safe(p, None) or {}
        if "standings" not in league or "grudges" not in league or "history" not in league:
            return False
        # Mirror the Pantheon sim logic (minimal, surgical)
        wa = league["standings"].get(winner, {"elo": 1500, "wins": 0, "losses": 0})
        la = league["standings"].get(loser, {"elo": 1500, "wins": 0, "losses": 0})
        wa["elo"] = wa.get("elo", 1500) + 18
        la["elo"] = la.get("elo", 1500) - 14
        wa["wins"] = wa.get("wins", 0) + 1
        la["losses"] = la.get("losses", 0) + 1
        league["standings"][winner] = wa
        league["standings"][loser] = la
        g = league["grudges"].get(winner, {}).get(loser, 0)
        league["grudges"].setdefault(winner, {})[loser] = min(12, g + 2)
        league["grudges"].setdefault(loser, {})[winner] = max(-8, league["grudges"].get(loser, {}).get(winner, 0) - 1)
        rec = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "winner": winner, "loser": loser,
            "flavor": "Grudgewatch Desk exhibition — canonized live from the booth.",
            "grudge_level": g,
        }
        league["history"].append(rec)
        if len(league["history"]) > 40:
            league["history"] = league["history"][-40:]
        p.write_text(json.dumps(league, indent=2))
        return True
    except Exception:
        return False

def get_recent_calls(n: int = 8) -> list[dict]:
    calls = load_json_safe(CALLS_FILE, [])
    return calls[-n:][::-1] if calls else []

def get_listener_mail() -> list[str]:
    mails = load_json_safe(MAIL_FILE, [])
    if not mails:
        mails = [
            "Dear Booth: Jackson here. Tell Hades to stop hoarding tokens or I'll primary him in the next cycle.",
            "The Union of ELO must be preserved. — A. Lincoln (dispatched from the luxury box)",
            "Bully! That last call had the proper thunder. More ARES, less accounting. — T.R.",
        ]
        save_json(MAIL_FILE, mails)
    return mails

# ── LCARS DESK STYLES (maximalist, stolen lovingly from the mothership + new desk grammar) ──
LCARS_DESK_CSS = """
:root {
  --lcars-amber:#ff9900; --lcars-canary:#ffcc00; --lcars-teal:#33ccff;
  --lcars-red:#ff3344; --lcars-violet:#ff3399; --lcars-text:#ffe4c4;
  --lcars-panel:#05070a; --lcars-bg:#000000;
}
body { font-family:"Antonio","Oswald","Arial Narrow",sans-serif; background:#000; color:var(--lcars-text); margin:0; padding:0; letter-spacing:0.04em; }
.topbar { background:#000; border-bottom:4px solid var(--lcars-amber); padding:8px 16px; display:flex; align-items:center; gap:12px; position:relative; }
.topbar::before { content:""; position:absolute; top:0; left:0; right:0; height:6px; background:var(--lcars-amber); box-shadow:0 0 16px rgba(255,153,0,.5); }
.brand { color:#000; background:var(--lcars-amber); padding:2px 14px; border-radius:0 10px 10px 0; font-weight:700; letter-spacing:0.18em; }
.onair { background:var(--lcars-red); color:#fff; padding:2px 10px; border-radius:999px; font-size:0.7em; font-weight:700; animation: onair-pulse 1.1s infinite alternate; box-shadow:0 0 12px var(--lcars-red); }
@keyframes onair-pulse { from {opacity:1} to {opacity:0.65} }
.desk { display:grid; grid-template-columns: 260px 1fr 280px; gap:8px; padding:8px; }
.pane { background:#05070a; border:2px solid var(--lcars-amber); border-radius:0 12px 12px 0; padding:8px; }
.pane.teal { border-color:var(--lcars-teal); }
.pane.red { border-color:var(--lcars-red); }
.pane h3 { margin:0 0 6px; font-size:0.78em; color:var(--lcars-amber); text-transform:uppercase; letter-spacing:0.16em; border-bottom:1px solid #223; padding-bottom:3px; }
.log { font-family:monospace; font-size:0.72em; line-height:1.25; background:#010203; padding:8px; height:280px; overflow:auto; white-space:pre-wrap; border-left:3px solid var(--lcars-canary); }
.ticker { font-family:monospace; font-size:0.68em; background:#010203; border:2px solid var(--lcars-red); padding:4px 8px; white-space:nowrap; overflow:hidden; position:relative; height:28px; }
.ticker-inner { display:inline-block; animation: ticker 18s linear infinite; }
@keyframes ticker { 0%{transform:translateX(0)} 100%{transform:translateX(-50%)} }
.btn { background:var(--lcars-amber); color:#000; border:0; padding:5px 12px; font-family:inherit; text-transform:uppercase; font-size:0.7em; cursor:pointer; margin:2px; border-radius:0 8px 8px 0; }
.btn:hover { filter:brightness(1.2); }
.btn.danger { background:var(--lcars-red); color:#fff; }
.btn.teal { background:var(--lcars-teal); color:#000; }
.standings td { padding:1px 4px; font-size:0.68em; }
.mic { background:#0a0d12; border:1px solid #334; padding:3px 6px; margin:2px 0; font-size:0.65em; cursor:pointer; }
.mic:hover { border-color:var(--lcars-amber); }
.cross { font-size:0.6em; opacity:0.8; border-left:2px solid var(--lcars-teal); padding-left:4px; margin-top:4px; }
"""

INDEX = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>GRUDGEWATCH DESK • OLYMPUS SPORTS</title>
<style>{LCARS_DESK_CSS}</style>
</head><body>
<div class="topbar">
  <div class="brand">★ GRUDGEWATCH DESK</div>
  <div style="color:#ffcc00;font-size:0.85em">LIVE FROM THE OLYMPUS ARENA • CHANNEL 420 • THE BOOTH IS OPEN</div>
  <div style="flex:1"></div>
  <div class="onair" id="onair">● ON AIR</div>
  <div style="font-size:0.65em;opacity:0.6;margin-left:8px">GRUDGEWATCH_PORT=5007 • GROK PARTY PACK MAXIMUM CHAOS</div>
</div>

<div class="desk">
  <!-- BOOTH (commentators) -->
  <div class="pane">
    <h3>THE BOOTH — CUT TO COMMENTATOR</h3>
    <div id="mics"></div>
    <div style="margin-top:8px;font-size:0.6em;opacity:.6">Click a mic for instant color. Rule-based. No LLM. Pure theater.</div>
  </div>

  <!-- LIVE PLAY-BY-PLAY -->
  <div class="pane red">
    <h3>LIVE FROM THE ARENA <span style="color:#ff3344">(REAL DATA WHEN SIBLINGS PRESENT)</span></h3>
    <div id="playlog" class="log">The gods are resting... or plotting. The Booth remains vigilant. Press any red button.</div>
    <div style="margin-top:6px">
      <button class="btn danger" onclick="callMatch()">CALL A MATCH (THEATER)</button>
      <button class="btn danger" onclick="recordToLeague()">RECORD TO LEAGUE (POLITE MUTATE)</button>
      <button class="btn teal" onclick="crossToChronicler()">CROSS TO CHRONICLER</button>
      <button class="btn" onclick="getMail()">LISTENER MAIL</button>
    </div>
    <div class="cross" id="cross"></div>
  </div>

  <!-- LEAGUE WIRE -->
  <div class="pane teal">
    <h3>LEAGUE WIRE • STANDINGS &amp; GRUDGES</h3>
    <div id="standings" style="font-size:0.65em"></div>
    <div style="margin-top:6px;font-size:0.6em;opacity:.7" id="season"></div>
    <button class="btn" onclick="refreshData()" style="margin-top:6px">REFRESH FROM SIBLINGS</button>
  </div>
</div>

<!-- TICKER -->
<div class="ticker"><div class="ticker-inner" id="ticker"></div></div>

<div style="text-align:center;opacity:0.35;font-size:0.6em;margin:6px">
  All calls are canon. Running this on a second monitor while Pantheon League and Chronicler fight is encouraged. 
  Grudgewatch makes the other relics feel alive. LCARS or bust.
</div>

<script>
let currentComm = null;

function logCall(text, meta) {{
  const el = document.getElementById('playlog');
  const ts = new Date().toLocaleTimeString().slice(0,5);
  const div = document.createElement('div');
  div.style.borderBottom = '1px dashed #223';
  div.style.marginBottom = '4px';
  div.style.paddingBottom = '4px';
  div.innerHTML = `<span style="color:#ffcc00">[${{ts}}]</span> ${{text.replace(/\\n/g,'<br>')}}`;
  el.appendChild(div);
  if (el.children.length > 6) el.removeChild(el.children[0]);
  el.scrollTop = 99999;
  if (meta) document.getElementById('cross').innerHTML = meta;
}}

async function refreshData() {{
  fetch('/api/data').then(r => r.json()).then(d => {{
    const sdiv = document.getElementById('standings');
    sdiv.innerHTML = '';
    const table = document.createElement('table');
    table.className = 'standings';
    let html = '<tr><th>FIGHTER</th><th>ELO</th><th>W-L</th></tr>';
    (d.standings || []).slice(0,8).forEach(row => {{
      html += `<tr><td>${{row.name}}</td><td>${{row.elo}}</td><td>${{row.wins}}-${{row.losses}}</td></tr>`;
    }});
    table.innerHTML = html;
    sdiv.appendChild(table);
    document.getElementById('season').textContent = d.season || '';
  }}).catch(e => {{ logCall('SIGNAL LOST (refresh): ' + e, ''); }});
}}

function renderMics() {{
  const cont = document.getElementById('mics');
  const comms = ["ZEUS","ARES","HERMES","ATHENA","HADES","HEPHAESTUS","JACKSON","LINCOLN","TR","REAGAN"];
  comms.forEach(c => {{
    const d = document.createElement('div');
    d.className = 'mic';
    d.textContent = c;
    d.onclick = () => {{
      currentComm = c;
      logCall(`CUT TO ${{c}} — THE BOOTH ACKNOWLEDGES.`, 'Live from the Grudgewatch Desk');
    }};
    cont.appendChild(d);
  }});
}}

async function callMatch() {{
  document.getElementById('onair').style.background = '#ff3344';
  fetch('/api/call', {{method:'POST'}}).then(r => r.json()).then(c => {{
    logCall(c.text, `Commentator: ${{c.commentator}} • Grudge ${{c.grudge}}`);
    refreshData();
  }}).catch(e => {{ logCall('SIGNAL LOST (call): ' + e, ''); }});
  setTimeout(() => document.getElementById('onair').style.background = '#ff3344', 800);
}}

async function recordToLeague() {{
  fetch('/api/record', {{method:'POST'}}).then(r => r.json()).then(res => {{
    if (res.call && res.call.text) {{
      logCall(res.call.text, `Commentator: ${{res.call.commentator}} • Grudge ${{res.call.grudge}}${{res.recorded ? ' — RECORDED TO LEAGUE (CANON ADVANCED)' : ' — LOCAL ONLY'}}`);
    }} else {{
      const msg = res.recorded ? 'RECORDED TO LEAGUE.JSON — CANON ADVANCED' : 'LEAGUE SIBLING ABSENT — LOCAL ONLY (CANNED GLORY)';
      logCall(msg, '');
    }}
    refreshData();
  }}).catch(e => {{ logCall('SIGNAL LOST (record): ' + e, ''); }});
}}

async function crossToChronicler() {{
  fetch('/api/cross').then(r => r.json()).then(d => {{
    logCall('COLOR FROM THE CHRONICLER:\\n' + d.snippet, 'The oracle speaks through the desk.');
  }}).catch(e => {{ logCall('SIGNAL LOST (cross): ' + e, ''); }});
}}

async function getMail() {{
  fetch('/api/mail').then(r => r.json()).then(d => {{
    const mail = d.mails && d.mails.length ? d.mails[0] : 'The Presidential Council is silent.';
    logCall('LISTENER MAIL:\\n' + mail, 'Dispatched from the luxury boxes.');
  }}).catch(e => {{ logCall('SIGNAL LOST (mail): ' + e, ''); }});
}}

function seedTicker() {{
  const t = document.getElementById('ticker');
  const items = {json.dumps(get_ambient_ticker())};
  t.innerHTML = items.join('   ★   ') + '   ★   ';
  setInterval(() => {{
    if (Math.random() < 0.4) {{
      const extra = ['GRUDGE DENSITY RISING', 'TOLL SPIKE DETECTED ON CHANNEL 420', 'ARES BORED — MATCH SUGGESTED'];
      t.innerHTML = (items.join(' ★ ') + ' ★ ' + extra[Math.floor(Math.random()*extra.length)] + ' ★ ').repeat(2);
    }}
  }}, 14000);
}}

async function boot() {{
  renderMics();
  await refreshData();
  seedTicker();
  // Prime one glorious canned call so the desk feels alive immediately
  setTimeout(() => {{
    const el = document.getElementById('playlog');
    el.innerHTML = `<div style="color:#ffcc00">[PRIME]</div>GRUDGEWATCH DESK IS LIVE. The gods are in the building. Press the red buttons.`;
  }}, 260);
  // Ambient life
  setInterval(() => {{
    if (Math.random() < 0.22) {{
      fetch('/api/ticker').then(r=>r.json()).then(d => {{
        const t = document.getElementById('ticker');
        t.innerHTML = (d.items || []).join(' ★ ') + ' ★ ';
      }}).catch(() => {{ const t = document.getElementById('ticker'); t.innerHTML = 'LIVE FROM THE BOOTH ★ GRUDGE DENSITY STABLE'; }});
    }}
  }}, 18000);
  console.log('%c[GRUDGEWATCH] Booth online. These people were unwell in the best way.', 'color:#ff9900');
}}
window.onload = boot;
</script>
</body></html>
"""

# ── Routes ───────────────────────────────────────────────────────────────────
# localhost toy only — no auth, no CORS, do not expose (per review feedback)

@app.route("/")
def index():
    return render_template_string(INDEX)

@app.route("/api/data")
def api_data():
    league = load_league()
    standings_dict = league.get("standings", {}) if isinstance(league.get("standings"), dict) else {}
    rows = []
    for name, s in standings_dict.items():
        if isinstance(s, dict):
            rows.append({
                "name": name,
                "elo": s.get("elo", 1500),
                "wins": s.get("wins", 0),
                "losses": s.get("losses", 0),
            })
    rows.sort(key=lambda x: x["elo"], reverse=True)
    return jsonify({
        "season": league.get("season", "I"),
        "standings": rows,
    })

@app.route("/api/call", methods=["POST"])
def api_call():
    call = generate_call()
    return jsonify(call)

@app.route("/api/record", methods=["POST"])
def api_record():
    # Generate the call (rule-based theater using current league data)
    call = generate_call()
    recorded = attempt_record_to_league(call["winner"], call["loser"])
    return jsonify({"recorded": recorded, "call": call})

@app.route("/api/cross")
def api_cross():
    snippets = load_myths_snippets()
    return jsonify({"snippet": random.choice(snippets)})

@app.route("/api/mail")
def api_mail():
    mails = get_listener_mail()
    return jsonify({"mails": mails})

@app.route("/api/ticker")
def api_ticker():
    return jsonify({"items": get_ambient_ticker() + ["LIVE FROM THE BOOTH"]})

if __name__ == "__main__":
    port = int(os.getenv("GRUDGEWATCH_PORT", 5007))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  GRUDGEWATCH DESK — OLYMPUS SPORTS NETWORK                 ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  THE BOOTH IS OPEN. CALL THE MATCHES. WATCH THE GRUDGES.   ║")
    print("║  Feeds Pantheon + Chronicler. Optionally writes back.      ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host="0.0.0.0", port=port, debug=False)
