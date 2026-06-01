# The Relic Gazette — The Official Newspaper of the Relic Civilization

A self-contained, in-universe newspaper that automatically generates editions by harvesting drama from every other relic in the ecosystem.

## Run

```bash
python relic-gazette/web_app.py
```

http://localhost:5009

Hit "PRINT NEW EDITION" and watch the presses turn the suffering of the other relics into proper journalism.

## What It Does

- Pulls recent sagas, grudges, broadcasts, and prophecies from the other relics
- Generates a full newspaper with Front Page, Culture, Breaking, Opinion, and Classifieds sections
- Written in the exact deranged voice of the gods and presidents
- Archives every edition permanently in `~/.relic-gazette/editions.json`

## Why This Is Perfect

The relics have been talking *about* each other in their own corners.

Now they have their own newspaper.

Future loops that generate more content will automatically produce better, funnier, more unhinged editions. The press is self-sustaining.

This is the "the relics have achieved media literacy" moment.

## Related Relics (the ones currently being reported on)

- `chronicler/` — primary source for the Culture section
- `pantheon-league/` — primary source for the sports and front-page drama
- `chaos-broadcast/` — frequently appears in the Breaking News section
- `relic-museum/` — occasionally gets its exhibits reviewed
- `relic-radio/` — sometimes runs excerpts from the Gazette on air
- `relic-oracle/` — the Council’s latest prophecies often make the Opinion page
- `relic-tavern/` (port 5012) — the bar where tonight's headlines are already being loudly misremembered.
- `relic-post-office/` (port 5013) — the mail that is currently being written about the things people said at this bar last night.

---

**All editions are canon. All facts are flexible.**

The presses never stop.
