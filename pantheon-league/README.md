# THE PANTHEON LEAGUE — Grudges & Glory

**Persistent arena drama for the Grok Party Pack.**

Your gods and a handful of extremely opinionated US Presidents now maintain official ELO rankings, nurse long-standing grudges, and generate beautiful propaganda posters after every bout.

---

## Why This Fits the Party Pack (Maximum Chaos)

The arenas were always the crown jewel — the part you show your friends.

This companion makes the characters *live* between sessions:

- Zeus doesn't just narrate one fight and disappear. He carries a season-long record and a simmering feud with Hades.
- Andrew Jackson can (and will) brawl a god and then brag about it to the Council next door.
- Every match updates a living **Grudge Codex**. Close fights or roast battles create lasting mechanical consequences.
- The "propaganda poster" output is designed to be screenshotted and fed directly into the Chronicler or posted in the group chat.

This is the "what happens next?" engine. It turns one-off arena spectacles into an ongoing soap opera with consequences.

---

## How to Run

```bash
python pantheon-league/web_app.py
```

http://localhost:5004

**Related relics:**
- `chaos-broadcast/` (port 5005) — currently stealing your grudges for the news.
- `relic-museum/` (port 5006) — the official museum now putting your propaganda in glass cases.
- `relic-radio/` (port 5007) — the station that has started reading your match reports as late-night drama.
- `grudgewatch/` (port 5007) — the official Grudgewatch Desk now calling your matches live with god + president commentators. Your ELO swings are now broadcast drama. The League has never felt more alive.

- Pick any two fighters (gods or presidential guests)
- Simulate the match
- Watch ELO move, grudges mutate, and a glorious ASCII propaganda poster appear
- The state is persisted in `~/.pantheon-league/league.json` — delete the file to reset the season

Environment: `PANTHEON_LEAGUE_PORT=5004`

Completely standalone. The simulator is pure Python + flavorful text. No API keys, no LLM calls inside the toy.

---

## Canon Status

Everything that happens here is canon.

If the Chronicler later references "the time Jackson humbled Hermes in straight sets", this is where that came from.

Future expansions that would be glorious (not in v1):
- Integration points so real Arena runs can feed the league
- "Council Dispatch" that actually calls the Presidential Council with the result as context
- Illustrated posters via the image tools
- Season finales with massive multi-combatant royal rumbles

---

## Roster (Season I)

**Gods**: Zeus, Athena, Hephaestus, Hermes, Ares, Hades  
**Presidential Guests**: Jackson, Lincoln, TR, Reagan

The mortals are slightly underpowered but carry the populist fire that makes upsets extremely funny.

---

Ship it. Book the matches. Let the grudges fester.
