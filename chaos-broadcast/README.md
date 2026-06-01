# Chaos Broadcast System — Emergency Relic Network

A standalone, gloriously maximalist "Party Pack Emergency Broadcast System".

It listens to the other chaotic relics (Chronicler myths, Pantheon League grudges/history, etc.), then turns that raw suffering into dramatic breaking-news bulletins, complete with interference, presidential dispatches, and divine spin.

## Run

```bash
python chaos-broadcast/web_app.py
```

Then open http://localhost:5005

Big red **TRANSMIT** button. Do it. The relics are waiting.

## What It Does

- Pulls recent drama from `~/.chronicler/myths.json` and `~/.pantheon-league/league.json`
- Generates over-the-top LCARS-flavored emergency broadcasts
- Has "interference" events because Hermes is always skimming something
- Archives every transmission in `~/.chaos-broadcast/broadcasts.json` so the signal history survives reboots and acts of god
- Looks like it belongs on the bridge of a starship that lost its mind

## Why This Fits the Party Pack

We built several independent chaotic relics. They were talking past each other. Now they have a central, noisy, slightly unhinged broadcast hub that makes the whole mess feel like one living universe.

It turns isolated cool shit into *connected* cool shit.

Maximum "what the hell is even happening in this project" energy.

## Frequencies

- PANTHEON NEWS NETWORK
- LCARS TRAFFIC CONTROL
- CHRONICLER DISPATCH
- OLYMPUS SPORTS
- WHITE HOUSE AFTER DARK
- EMERGENCY THEOLOGY

All canon. All slightly cursed.

## Related Relics

- `chronicler/` — source of the best sagas
- `pantheon-league/` — source of the best grudges and propaganda
- `lcars-bridge/` — the vibes engine that sometimes leaks onto the airwaves
- `relic-museum/` (http://localhost:5006) — the official institution now archiving our broadcasts. We are becoming history.
- `grudgewatch/` (port 5007) — OLYMPUS SPORTS now has a permanent, unhinged home. The Booth is live-calling every grudge we steal. The frequencies are talking to each other.

---

**The signal must be preserved.**

(And yes, you can run this on a second monitor while the other relics do their thing. It is encouraged.)
