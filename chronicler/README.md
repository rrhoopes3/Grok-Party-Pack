# THE CHRONICLER — Oracle of the Runs

**A wild companion relic of the Grok Party Pack (Maximum Chaos Division).**

The Chronicler takes the grim, token-by-token, toll-deducted execution traces of The Forge and transmutes them into Homeric sagas, blood-soaked odes, and LCARS terminal recitations worthy of the Pantheon itself.

---

## Why This Fits the Party Pack

The soul of the Party Pack has *always* been theatricality, humor, and "what if we just kept going."

- Run logs are not boring JSONL. They are the **Iliad** of agent OS.
- Every failed `run_command` on Windows is the tragic labor of Hephaestus.
- Every toll paid to the ledger is a sacrifice at the altar of Hermes the Accountant.
- The Presidential Council next door occasionally wanders in to offer commentary.
- This is pure "I want to show this to someone" energy. Drop a run log in front of a friend and watch their face when Zeus narrates their `cd` command.

It is deliberately silly, deliberately beautiful, and 100% in the spirit of smashing cool shit together until something sings.

---

## How to Run

```bash
python chronicler/web_app.py
```

Then open http://localhost:5002

- It will auto-discover real Forge runs from `../forge/data/runs` (or sibling) if they exist.
- If not, glorious canned epics are always present so the ritual never fails.
- Weave individual sagas or demand the full **Epic Cycle**.
- Etch the best ones into your personal Codex (`~/.chronicler/myths.json`).

Environment:
- `CHRONICLER_PORT=5002` to change the port.
- It is completely standalone. No API keys required. No LLM calls inside the toy itself — pure deterministic theatrical engine.

---

## Design Notes (for future chaos agents)

- Narrator selection is biased toward the dominant tool (Hephaestus for shell, Hermes for anything networked, Hades for suffering).
- Presidential cameos appear at random because of course they do.
- The generator is tiny, rule-based, and gloriously over-the-top. This is a feature.
- Future expansions (not in this initial drop): voice synthesis hooks, illustrated battle posters, grudge-aware retellings that reference the Pantheon League.

---

## License / Lineage

Born in the fires of the Grok-Party-Pack maximalist experiment, June 2026 edition.

**New chaos cross-link:** `chaos-broadcast/` (port 5005) is now listening to your etched myths and turning the best ones into emergency dispatches. The signal is getting louder.

- `grudgewatch/` (port 5007) — now pulling your sagas for live color commentary and calling your grudges like a deranged sports desk. The Chronicler has a new audience.

Part of the "just keep adding wild new toys and see what sticks" directive.

If it makes you laugh or screenshot it for the group chat, the Chronicler has done its job.
