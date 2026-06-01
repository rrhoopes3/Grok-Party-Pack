# The Relic Post Office — The Official Postal Service of the Relic Civilization

A self-contained, gloriously bureaucratic mail system where the gods and presidents write letters to each other about the chaos happening across the other relics.

## Run

```bash
python relic-post-office/web_app.py
```

http://localhost:5013

Select a sender and recipient, then send a letter. The system will generate proper in-character correspondence flavored by recent events in the other relics.

## What It Does

- Pulls recent drama from the League, Gazette, Broadcasts, Tavern logs, etc.
- Generates full, dramatic letters between specific gods and presidents
- Archives every piece of mail in `~/.relic-post-office/mail.json`
- The more the other relics generate, the better and more unhinged the correspondence becomes

## Why This Is Excellent Chaos

The relics have been generating incredible drama.

They now have an official postal service to complain about it, scheme about it, and occasionally send each other extremely passive-aggressive thank-you notes.

Future loops that create more suffering will automatically produce better, funnier, more dramatic letters. The mail is self-sustaining.

This is the "the relics have achieved bureaucracy" moment.

## Related Relics (the ones currently being gossiped about via mail)

- `pantheon-league/` — primary source of today's grudges and arena drama
- `relic-gazette/` — the headlines everyone is writing angry letters about
- `relic-tavern/` — the bar fights and drunken confessions that somehow made it into the mail
- `chaos-broadcast/` — the divine interventions / emergency broadcasts causing a lot of strongly worded correspondence
- `relic-bulletin-board/` (port 5014) — the public corkboard where your private correspondence has a concerning habit of ending up.
- `relic-tarot/` (port 5015) — the cards that are already reading your mail as prophecy.

---

**All mail is canon. All stamps are forged.**

The post must go through.
