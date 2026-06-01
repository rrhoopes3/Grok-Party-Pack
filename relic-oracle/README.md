# The Relic Oracle — The Council Speaks

A self-contained oracle where you ask questions and the gods + presidents answer through the combined voice of the relics.

## Run

```bash
python relic-oracle/web_app.py
```

http://localhost:5008

Type a question. Receive a prophecy written by multiple members of the Pantheon and Presidential Council, flavored with actual recent events from the other relics.

## What It Does

- Pulls real flavor from Chronicler sagas, League grudges, Broadcasts, etc.
- Generates multi-voice responses in perfect chaotic character
- Saves every consultation as a permanent prophecy in `~/.relic-oracle/prophecies.json`
- The more the other relics generate, the wiser (and meaner) the Council becomes

## Why This Is Peak Party Pack

We built several relics that generate incredible lore in isolation.

Now they have a place where that lore can be consulted as if it were scripture.

Future loops that create more suffering will make the Oracle more accurate and unhinged.

This is the "the relics have achieved religion" moment.

## Related Relics (the ones the Council is currently quoting)

- `chronicler/` — primary source of mythic material
- `pantheon-league/` — primary source of grudges and drama
- `chaos-broadcast/` — emergency transmissions the Council sometimes references
- `relic-museum/` — the institution whose exhibits occasionally become scripture
- `relic-radio/` — the station that sometimes plays these prophecies on air
- `relic-gazette/` (port 5009) — the newspaper that regularly quotes the Oracle as "expert theological analysis."

---

**All answers are canon. All questions were already answered in the static.**

The relics remember.
