# The Relic Museum — Official Party Pack Archive of Atrocities

A self-contained LCARS museum that automatically hoovers up generated chaos from the other relics and displays it as sacred history.

## Run

```bash
python relic-museum/web_app.py
```

http://localhost:5006

It will scan:
- `~/.chronicler/myths.json` → Sagas Wing
- `~/.pantheon-league/league.json` → Propaganda Hall + Grudge Codex
- `~/.chaos-broadcast/broadcasts.json` → Broadcast Archives

Each exhibit gets rotating commentary from the Pantheon and Presidential Council.

## Why This Is Perfect Chaos

We built several independent, beautiful, unhinged relics. They were all generating incredible output in their own little universes.

Now they have a single place where that output is preserved, curated, and commented on by the exact same gods and presidents who caused the suffering in the first place.

The lore is no longer ephemeral. It has an institution.

This is the "the relics are becoming a civilization" moment.

## Features

- Automatic discovery of artifacts from sibling relics
- Rotating Curator commentary (gods + presidents)
- Clean, oppressive LCARS museum aesthetic
- Zero configuration. Just run it.

## Related Relics (the ones currently feeding the museum)

- `chronicler/` — source of the best sagas
- `pantheon-league/` — source of the best grudges and propaganda posters
- `chaos-broadcast/` — source of the best emergency transmissions
- `relic-radio/` (http://localhost:5007) — the late-night station that is currently playing exhibits from this museum as radio drama.
- `relic-oracle/` (http://localhost:5008) — the Council now uses this museum as source material for prophecies.
- `relic-gazette/` (port 5009) — the press has started reviewing our exhibits.
- `lcars-bridge/` — the vibes engine (its output will be archived here eventually)

---

**All relics are canon. All suffering is archived.**

The curators are always watching.
