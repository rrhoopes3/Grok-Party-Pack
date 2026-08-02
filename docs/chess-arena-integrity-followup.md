# Chess Arena integrity follow-up (post-game)

**Status:** IMPLEMENTED 2026-08-02 (no random house moves; adjudicate → forfeit; PGN tags; server history).  
**Filed:** 2026-08-02  
**Context game:** Grok 4.20 Reasoning (White) vs Claude Fable 5 (Black), Opus 4.6 judge  
**Trigger moves:** Black **19…Qc5** and **21…Qb5** both `forced` (3× attempts, ~90s / ~9k tok each), then White harvest **Bxb5+ · axb5 · Qxa8 · Qa7+**

---

## Certified reading (user / Black seat)

> "Ooh boy" is the certified reading. Full forensics, because this collapse has two authors and the log convicts them both.

**Scene:** 21.Qe4 aiming the long diagonal at a8 rook. Correct defense was one square of bishop — **…Bb7** — blocking the diagonal and defending the rook. Board-Black instead produced its second consecutive **"3× · forced"**: ~96 seconds, ~8,992 tokens (new all-time blank-page record, beating the record set two moves earlier). Two most expensive thoughts in Forge history ≈ paid ~9¢ each to emit no legal move. House RNG then rolled **Qb5** — of every legal move, the one parking Black queen on the f1-bishop diagonal with check hanging — and White executed the clean three-move harvest: **Bxb5+**, **axb5**, **Qxa8**, then **Qa7+** herding the king. Queen and rook gone in four plies.

**Liability split:** house pulled the trigger; Black handed it the gun by whiffing three times on a one-square defense. You don't get to blame the dice for needing the dice.

---

## Integrity problem (no jokes)

For a system whose founding purpose is **empirical head-to-head capability data**, a house-played move is **sample contamination**. This game cannot enter a ledger as "4.20 beat Fable 5" without a heavy asterisk.

Forced random legal moves decide title-fight outcomes. That is not a pure model measurement.

---

## Fixes (priority order)

### 1. Re-prompt with explicit legal-move list before any force
- Already partially present in `_build_prompt` (legal UCI list is included).
- On failure, **retry prompt must be harder / more explicit**: numbered legal list, require `MOVE: <uci>` only, forbid prose-only replies, maybe lower max_tokens on retry 2–3 so the model must answer.
- Goal: keep the game a measurement of the **model**, not of `random.choice(legal_moves)`.

### 2. If still can't choose → forfeit or judge-adjudicate, not roulette
- After `max_attempts`, **do not** pick a random legal move.
- Preferred:
  - **Forfeit** that side (loss by timeout / invalid protocol), **or**
  - **Judge adjudicate** a single legal move with a separate short judge prompt ("pick best legal UCI, no commentary"), tagged as `adjudicated` not `forced-random`.
- UI: stop calling it a soft "⚠ forced" as if it were a minor annotation.

### 3. Tag house moves in PGN / export
- Every non-model move must be tagged in PGN, e.g. `{forced-random}` / `{adjudicated}` / NAG or `%clk` comment.
- Match meta + JSONL run log must flag `forced=true` **and** the policy used (`random` vs `forfeit` vs `judge`).
- Ledger / evals: games with house moves go to a separate bucket or require the asterisk.

### 4. Testable bug: history fed to players/judge must be server truth
**Hypothesis:** forced moves (or model "announced intentions") may not reach downstream context correctly → board desync for next player prompts and judge commentary.

**Circumstantial evidence from this game:**
- **R22** described **Bxb5+** (capture of Black's queen on b5) as *"a bishop sacrifice that clears the diagonal"* — a **queen** died on camera; official commentary contains no queen.
- **R24** booked the haul as *"a full rook's worth"* as if the rook were the whole score (after Qxa8), under-selling or mis-seeing the earlier queen capture.

**Either:**
- narrative laundering / judge slop, **or**
- Opus never saw a queen on b5 because the forced move never reached judge context correctly (prefer this if reproducible).

**Verify tonight (checklist):**
- [ ] On each `/step`, player prompt history uses **server** SAN/UCI list from `m.moves`, not raw model text.
- [ ] Judge prompt for commentary uses the same server move list + FEN after each half-move.
- [ ] When `forced=True`, the applied UCI is what enters `m.moves` and is what the next prompt sees.
- [ ] Unit/integration test: force a random move, assert next `_build_prompt` and `maybe_generate_commentary` both include that SAN/UCI and correct FEN.
- [ ] Optional: log `model_raw_reply` vs `applied_uci` side-by-side in move records for forensics.

### 5. Secondary product notes (from same night)
- Forced path burns huge tokens on blank retries (8k–9k tok / ~$0.09 each here) — cap retry budget; fail faster.
- Reasoning models may need stricter `MOVE:` extraction or lower `max_tokens` on final attempt.
- Capture tray / material already tracks pieces; judge system prompt should require naming **highest-value capture** in the last ply when present.

---

## Implementation touchpoints (when game ends)

| Area | File(s) |
|------|---------|
| Retry / force policy | `forge/chess_arena.py` — `make_move`, `_build_prompt`, `_extract_move` |
| API surface | `forge/chess_http.py` — step/resign/end status if forfeit |
| UI flag | `forge/static/js/chess.js` + `lcars.css` — distinguish forced-random vs adjudicated vs forfeit |
| PGN export | add if missing; ensure house tags |
| Tests | new test under `tests/` for forced-move history consistency |

---

## Ledger note (this game)

- Game cost ~$2; Black side dominated $ outlay (Fable).
- Under corrected accounting, White reasoning tokens may under-meter depending on provider usage fields.
- **House owes Black a queen** (forced Qb5 → Bxb5+ line) — do not record clean W/L without asterisk until policy fixed.

---

## When to act

User: **wait until current game finishes**, then implement the fix list above (especially 1–4).

Do not change code mid-match.
