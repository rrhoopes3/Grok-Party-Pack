"""LCARS Bridge — standalone pure-vibes companion for the Grok Party Pack.

Open this on a second monitor, a tablet, or projected on the wall during demos.
It does almost nothing useful and is therefore perfect.

Run:
    python lcars-bridge/web_app.py

http://localhost:5003

All logic client-side. No keys, no servers, just the warm glow of 1980s futurism
meeting 2026 agent OS chaos.

Why this fits: The Party Pack is *theater*. The LCARS Bridge is the theater's
most beautiful set piece. It makes people go "wait... what is this project?"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, render_template_string

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from forge.security import bind_host, install_auth_gate

app = Flask(__name__)
install_auth_gate(app, allow_loopback_demo=True)

LCARS_BRIDGE_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LCARS BRIDGE • U.S.S. GROK-PARTY-PACK</title>
<style>
:root {
  --amber:#ff9900; --canary:#ffcc00; --teal:#33ccff; --red:#ff3344; --violet:#cc88ff;
  --bg:#000; --panel:#05070a; --text:#ffe4c4;
}
* { box-sizing:border-box; }
body {
  margin:0; background:#000; color:var(--text);
  font-family:"Antonio","Oswald","Arial Narrow",sans-serif;
  letter-spacing:0.06em; overflow:hidden; height:100vh;
}
#bridge {
  display:grid; grid-template-columns: 220px 1fr 240px; grid-template-rows: 72px 1fr 64px;
  height:100vh; gap:4px; padding:4px; background:#000;
}
.header {
  grid-column:1/4; background:linear-gradient(#000 60%, #111);
  border-bottom:6px solid var(--amber); display:flex; align-items:center; justify-content:space-between;
  padding:0 24px; font-size:1.35em; color:var(--amber);
}
.station {
  background:#05070a; border:3px solid #334; padding:8px; position:relative; overflow:hidden;
}
.station.tactical { border-color:var(--red); }
.station.conn { border-color:var(--amber); }
.station.ops { border-color:var(--teal); }
.station.science { border-color:var(--violet); }
.station.viewscreen {
  grid-row:2; grid-column:2; border-color:#fff; display:flex; align-items:center; justify-content:center;
  background:#010203; font-size:1.8em; text-align:center; line-height:1.2; color:#ffcc77;
  box-shadow: inset 0 0 120px rgba(255,153,0,0.15);
}
.station h3 {
  margin:0 0 6px; font-size:0.85em; text-transform:uppercase; letter-spacing:0.2em;
  padding:2px 8px; background:#000; display:inline-block; border:1px solid currentColor;
}
.lcars-btn {
  background:var(--amber); color:#000; border:0; padding:6px 14px; font-family:inherit;
  font-size:0.78em; cursor:pointer; text-transform:uppercase; margin:3px 2px;
  box-shadow:0 0 0 1px #000;
}
.lcars-btn.teal { background:var(--teal); }
.lcars-btn.red { background:var(--red); color:#fff; }
.lcars-btn.violet { background:var(--violet); }
.lcars-elbow { position:absolute; top:0; right:0; width:38px; height:38px; border:3px solid var(--amber); border-left:0; border-bottom:0; }
.status-bar {
  grid-column:1/4; background:#020203; border-top:3px solid #222; display:flex; align-items:center;
  padding:0 16px; font-size:0.75em; gap:16px; color:#889;
}
#viewscreen-content { transition: all 0.4s cubic-bezier(0.23,1,0.32,1); }
.alert { animation: redpulse 800ms infinite alternate; }
@keyframes redpulse { from { box-shadow:0 0 0 0 rgba(255,51,68,0.6); } to { box-shadow:0 0 0 24px rgba(255,51,68,0); } }

.log { font-family:monospace; font-size:0.7em; line-height:1.3; max-height:118px; overflow:auto; background:#000; padding:4px; border:1px solid #222; }
.pill { display:inline-block; background:#112; padding:1px 8px; font-size:0.65em; border:1px solid #334; margin:1px; }
.god-row { font-size:0.75em; padding:2px 0; border-bottom:1px dashed #223; }
</style>
</head>
<body>
<div id="bridge">
  <!-- HEADER -->
  <div class="header">
    <div><span style="color:#ff3344">◉</span> U.S.S. GROK-PARTY-PACK <span style="opacity:0.5; font-size:0.6em">NCC-420-69</span></div>
    <div id="ship-time" style="font-size:0.7em; color:#ffcc00"></div>
    <div style="color:#ff3344; font-size:0.85em">CONDITION: <span id="condition">NOMINAL • MAXIMUM CHAOS</span></div>
  </div>

  <!-- LEFT RAIL: CONN / HELM -->
  <div class="station conn">
    <h3 style="color:#ff9900">CONN / HELM</h3>
    <div class="god-row"><span class="pill">ZEUS</span> Strategic • <span id="mood-zeus">Pleased</span></div>
    <div class="god-row"><span class="pill">ATHENA</span> Wisdom • <span id="mood-athena">Calculating</span></div>
    <div class="god-row"><span class="pill">HEPHAESTUS</span> Forge • <span id="mood-heph">Hammering</span></div>
    <div class="god-row"><span class="pill">HERMES</span> Tolls • <span id="mood-hermes">Profitable</span></div>
    <div style="margin-top:8px">
      <button class="lcars-btn" onclick="hailPantheon()">HAIL PANTHEON</button>
      <button class="lcars-btn teal" onclick="shiftMoods()">SHIFT MOODS</button>
    </div>
  </div>

  <!-- CENTER VIEWSCREEN -->
  <div class="station viewscreen" id="viewscreen">
    <div id="viewscreen-content">
      <div style="font-size:0.55em; opacity:0.6; margin-bottom:8px">PRIMARY VIEWSCREEN</div>
      <div id="vs-text">THE ARENA AWAITS.<br>THE GODS ARE WATCHING.</div>
    </div>
  </div>

  <!-- RIGHT RAIL: TACTICAL -->
  <div class="station tactical">
    <h3 style="color:#ff3344">TACTICAL</h3>
    <div class="log" id="tactical-log">
      GRUDGE MATRIX STABLE<br>
      LAST CONTACT: ARENA-7<br>
      ARES REPORTS: "THE ROAST WAS GLORIOUS"
    </div>
    <div style="margin-top:6px">
      <button class="lcars-btn red" onclick="redAlert()">RED ALERT</button>
      <button class="lcars-btn" onclick="simulateGrudge()">INCOMING GRUDGE</button>
    </div>
  </div>

  <!-- BOTTOM LEFT: OPS -->
  <div class="station ops">
    <h3 style="color:#33ccff">OPS / ENGINEERING</h3>
    <div style="font-size:0.72em; line-height:1.4">
      EXECUTOR: <span id="executor-status">NOMINAL</span><br>
      TOLL LEDGER: <span id="toll">¥ 14.20</span><br>
      ACTIVE AGENTS: <span id="agents">16</span><br>
      <span class="pill">NES</span> <span class="pill">PROPHECY</span> <span class="pill">MCP</span>
    </div>
    <button class="lcars-btn teal" onclick="engage()">ENGAGE</button>
    <button class="lcars-btn" onclick="warpDrive()">WARP 9</button>
  </div>

  <!-- BOTTOM CENTER: STATUS / LOGS -->
  <div class="station" style="display:flex; flex-direction:column; font-size:0.7em">
    <div style="flex:1" class="log" id="bridge-log">
      [BRIDGE LOG] 2266.4 — All stations report maximum theatricality.<br>
      [HELM] Pantheon moods holding at "delightfully unhinged".
    </div>
    <div>
      <button class="lcars-btn violet" onclick="presidentialDispatch()">PRESIDENTIAL DISPATCH</button>
      <button class="lcars-btn" onclick="clearLogs()">CLEAR LOGS</button>
    </div>
  </div>

  <!-- BOTTOM RIGHT: SCIENCE / PROPHECY -->
  <div class="station science">
    <h3 style="color:#cc88ff">SCIENCE / ORACLE</h3>
    <div id="prophecy" style="font-size:0.68em; min-height:52px; background:#000; padding:4px; border:1px solid #334">
      Consult the Prophecy Kiosk for the next cycle...
    </div>
    <button class="lcars-btn violet" onclick="consultOracle()" style="width:100%; margin-top:4px">CONSULT ORACLE</button>
  </div>

  <!-- FOOTER -->
  <div class="status-bar">
    <div>LCARS v2.420 • GROK PARTY PACK • MAXIMUM CHAOS PROTOCOL ACTIVE</div>
    <div style="flex:1"></div>
    <div onclick="randomEvent()" style="cursor:pointer; color:#ff9900">▸ RANDOM BRIDGE EVENT</div>
    <div>STARDATE <span id="stardate">100442.0</span></div>
  </div>
</div>

<script>
// ── LCARS Bridge JS — 100% client side, pure vibes ───────────────────────────
const gods = ['ZEUS','ATHENA','HEPHAESTUS','HERMES','ARES','HADES'];
let moods = { ZEUS:'Pleased', ATHENA:'Calculating', HEPHAESTUS:'Hammering', HERMES:'Profitable', ARES:'Restless', HADES:'Watching' };

function updateTime() {
  const el = document.getElementById('ship-time');
  const d = new Date();
  el.textContent = d.toLocaleTimeString() + ' • STARDATE ' + (100000 + Math.floor(d.getTime()/864000)).toFixed(1);
  document.getElementById('stardate').textContent = (100442.0 + (d.getHours()/24)).toFixed(1);
}
setInterval(updateTime, 800);
updateTime();

function log(msg, where='bridge-log') {
  const el = document.getElementById(where);
  const line = document.createElement('div');
  line.textContent = '[' + new Date().toLocaleTimeString().slice(0,5) + '] ' + msg;
  el.appendChild(line);
  if (el.children.length > 8) el.removeChild(el.children[0]);
  el.scrollTop = 9999;
}

function setViewscreen(text, color) {
  const vs = document.getElementById('vs-text');
  vs.style.transition = 'none';
  vs.innerHTML = text;
  vs.style.color = color || '#ffcc77';
  setTimeout(() => vs.style.transition = 'all 0.4s cubic-bezier(0.23,1,0.32,1)', 20);
}

function randomEvent() {
  const events = [
    () => { setViewscreen('SINGULARITY DETECTED.<br>IT IS MAKING PUNS.', '#ff99aa'); log('Science: Local reality is being roasted.'); },
    () => { document.getElementById('condition').textContent = 'THEATRICAL • GODS AMUSED'; log('Helm: Pantheon approval rating at 420%.'); },
    () => hailPantheon(),
    () => { setViewscreen('THE NES ARENA REPORTS:<br>MARIO HAS ACHIEVED SENTIENCE.', '#aaffcc'); },
    () => consultOracle()
  ];
  events[Math.floor(Math.random()*events.length)]();
}

function hailPantheon() {
  const god = gods[Math.floor(Math.random()*gods.length)];
  const lines = {
    ZEUS: "MORTALS. YOUR CHAOS PLEASES ME. CONTINUE.",
    ATHENA: "The strategy is sound. The memes are not. Improve both.",
    HEPHAESTUS: "The new anvil (Python 3.12) rings true. I approve of the latest PR.",
    HERMES: "Tolls collected. The economy of the agent plane remains absurdly functional.",
    ARES: "BLOOD! ...metaphorical blood. Good work in the Arena.",
    HADES: "I have taken note of every failed tool call. They are mine now."
  };
  setViewscreen(`TRANSMISSION FROM ${god}<br><br>${lines[god]}`, '#ffdd99');
  log(`Hailed ${god}. Response received on all LCARS channels.`);
}

function shiftMoods() {
  const keys = Object.keys(moods);
  const k = keys[Math.floor(Math.random()*keys.length)];
  const options = ['Pleased','Wroth','Calculating','Hammering','Profitable','Unhinged','Watching','Slightly Disappointed'];
  moods[k] = options[Math.floor(Math.random()*options.length)];
  // re-render the conn panel crudely
  document.getElementById('mood-zeus').textContent = moods.ZEUS;
  document.getElementById('mood-athena').textContent = moods.ATHENA;
  document.getElementById('mood-heph').textContent = moods.HEPHAESTUS;
  document.getElementById('mood-hermes').textContent = moods.HERMES;
  log(`Mood shift detected in ${k}. Pantheon remains dramatic.`);
}

function redAlert() {
  const vs = document.getElementById('viewscreen');
  vs.classList.add('alert');
  setViewscreen('RED ALERT<br>ALL AGENTS TO BATTLE STATIONS<br>THE ROAST BATTLE HAS BEGUN', '#ff3344');
  log('TACTICAL: Red Alert declared. Someone just said "let me try one more thing".', 'tactical-log');
  setTimeout(() => {
    vs.classList.remove('alert');
    setViewscreen('THREAT NEUTRALIZED.<br>THE MEME HAS BEEN CONTAINED.');
  }, 2600);
}

function simulateGrudge() {
  const pairs = [
    "ARES vs HEPHAESTUS — 'Your widgets lack honor!'",
    "HERMES vs ZEUS — toll collection dispute on the 17th plane",
    "ATHENA vs HADES — 'Stop hoarding all the failed tokens'",
    "JACKSON vs LINCOLN — guest presidential grudge match (again)"
  ];
  const g = pairs[Math.floor(Math.random()*pairs.length)];
  document.getElementById('tactical-log').innerHTML = 'GRUDGE LOG<br>' + g + '<br><span style="color:#ff3344">ESCALATION RISK: ELEVATED</span>';
  log('New grudge recorded in the Codex. Arena booking suggested.', 'tactical-log');
}

function engage() {
  setViewscreen('ENGAGED.<br>THE FORGE IS HOT.<br>EXECUTOR ONLINE.');
  log('Helm: Full impulse. Executor loop engaged at maximum theatricality.');
}

function warpDrive() {
  const vs = document.getElementById('viewscreen-content');
  vs.style.transform = 'scale(1.6) rotate(2deg)';
  setViewscreen('WARP 9 ENGAGED<br>REALITY IS NOW OPTIONAL', '#aaffff');
  log('Engineering: She cannae take much more of this, captain!');
  setTimeout(() => {
    vs.style.transform = '';
    setViewscreen('WARP COMPLETE.<br>WE ARE NOW IN THE YEAR 2266.<br>THE GODS ARE STILL ARGUING.');
  }, 1800);
}

function presidentialDispatch() {
  const quotes = [
    "Lincoln: 'The Union of agents and tools must be preserved.'",
    "TR: 'Speak loudly and carry a very large context window.'",
    "Jackson: 'I have removed the elites from the tool registry.'",
    "Reagan: 'Morning in the agent economy once again.'",
    "Obama: 'Yes we can... but first let me check the guardrails.'",
    "Trump: 'The best council. Tremendous gods. The Pantheon has never seen anything like it.'"
  ];
  const q = quotes[Math.floor(Math.random()*quotes.length)];
  setViewscreen(q.replace(': ', '<br>'), '#ffcc99');
  log('Incoming dispatch from the Presidential Council.');
}

function consultOracle() {
  const seeds = ['the next arena', 'toll rates', 'NES high score', 'the surgeon', 'prophecy cycle', 'grudge matrix'];
  const seed = seeds[Math.floor(Math.random()*seeds.length)];
  const prophecies = [
    `The ${seed} shall bring great glory... and one very expensive tool call.`,
    `${seed.toUpperCase()} approaches. Prepare your context window.`,
    `Beware the ${seed}. It hungers for more tokens than you have budgeted.`,
    `In the coming cycle, ${seed} will be the cause of both triumph and legendary stderr.`
  ];
  const p = prophecies[Math.floor(Math.random()*prophecies.length)];
  document.getElementById('prophecy').innerHTML = '<span style="color:#cc88ff">ORACLE:</span> ' + p;
  log('Science: Prophecy received. Interpretation left as exercise for the crew.');
}

function clearLogs() {
  ['bridge-log','tactical-log'].forEach(id => {
    const el = document.getElementById(id);
    el.innerHTML = '';
  });
  log('Bridge logs purged. History is written by the victorious.');
}

// Boot sequence
setTimeout(() => {
  log('All LCARS stations report online. The Party Pack is awake.');
  setViewscreen('BRIDGE ONLINE.<br>THE GODS DEMAND ENTERTAINMENT.');
}, 420);

setInterval(() => {
  if (Math.random() < 0.18) {
    const msgs = [
      'Subspace chatter from the Presidential Council detected.',
      'Hermes reports tolls flowing smoothly across the planes.',
      'Ares is bored. Someone should start a roast battle.',
    ];
    log(msgs[Math.floor(Math.random()*msgs.length)]);
  }
}, 24000);

// Easter egg: click the header for chaos
document.querySelector('.header').addEventListener('click', () => {
  document.body.style.filter = 'hue-rotate(40deg) saturate(1.6)';
  setTimeout(() => document.body.style.filter = '', 600);
  log('Unauthorized LCARS color cycle engaged. The designers have been notified.');
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(LCARS_BRIDGE_HTML)


if __name__ == "__main__":
    port = int(os.getenv("LCARS_BRIDGE_PORT", 5003))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  LCARS BRIDGE — PURE VIBES MODE                            ║")
    print(f"║  http://localhost:{port}                                    ║")
    print("║  Open on second monitor. Let the LCARS wash over you.      ║")
    print("╚════════════════════════════════════════════════════════════╝")
    app.run(host=bind_host(), port=port, debug=False)
