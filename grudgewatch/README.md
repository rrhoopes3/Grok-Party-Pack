# THE GRUDGEWATCH DESK — Olympus Sports Network

**A wild companion relic of the Grok Party Pack (Maximum Chaos Division).**

The Grudgewatch Desk takes the Pantheon League grudges and Chronicler sagas and turns them into deranged, rule-based sports play-by-play and color commentary — gods and presidents in the booth, live ticker, on-air theater. Pure deterministic silly joy. LCARS maximalism.

It optionally writes back to advance the league canon (politely, never destructive).

---

## Why This Fits the Party Pack

The soul of the Party Pack has *always* been theatricality, humor, and "what if we just kept going."

- Grudges are not boring JSON. They are the blood feuds of Olympus.
- Every ELO swing is narrated with thunder and tolls.
- The Presidential Council drops by the luxury box.
- Cross-feeds from Chronicler for color. Feeds back into the ecosystem.
- Zero LLM calls inside — 100% rule-based, reproducible, hilarious.

This is "I want to run this on a second monitor while the relics fight" energy.

---

## How to Run

```bash
python grudgewatch/web_app.py
```

Then open http://localhost:5007

- Press red buttons to CALL A MATCH (theater) or RECORD TO LEAGUE (advances sibling if present).
- Cross to Chronicler for fresh color.
- Listener mail from the presidents.
- It auto-loads real data from ~/.pantheon-league/ and ~/.chronicler/ when present; glorious canned drama otherwise.

Environment:
- `GRUDGEWATCH_PORT=5007` to change the port.
- Completely standalone. No API keys. No LLM. Pure theater.

---

## Design Notes (for future chaos agents)

- Canon roster and commentators copied for independence (same as Pantheon League S1).
- Loaders are defensive against missing/absent siblings.
- The "record to league" mutates standings/grudges/history politely if the file exists.
- Ambient ticker and listener mail keep the booth alive.
- Cross-links to Chronicler myths for the ultimate "relics talking to each other" effect.

Part of the "just keep adding wild new toys" directive.

If it makes you laugh or screenshot for the group chat, Grudgewatch has done its job.

---

**All calls are canon. The Booth is always open.**
