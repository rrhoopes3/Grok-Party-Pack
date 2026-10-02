"""Rule-based Zeus theater. No model API.

Turns news lines and scores into round commentary and a closing monologue
so cohort matches stay watchable without XAI_API_KEY.
"""
from __future__ import annotations

import re
from typing import Any


_CAPTURE = re.compile(r"^(\w+) CAPTURES (.+)$")
_ELIM = re.compile(r"^(\w+) HAS BEEN ELIMINATED by (\w+)$")
_REPEL = re.compile(r"^(\w+) repels (\w+) at (.+) \(attacker -(\d+)\)$")
_VIOL = re.compile(r"^(\w+) was caught breaking the rules: (.+)$")
_WIN_HOLD = re.compile(
    r"^(\w+) wins the duel; (\w+) holds (.+) with (\d+)$"
)


def narrate_round(round_num: int, rounds: int, news: list[str], scores: dict[str, int]) -> str:
    """One dramatic paragraph for the end of a campaign round."""
    captures: list[str] = []
    elim: list[str] = []
    repels: list[str] = []
    viols: list[str] = []
    holds: list[str] = []
    for line in news:
        if m := _CAPTURE.match(line.split(" [")[0].strip()):
            captures.append(f"{m.group(1)} storms {m.group(2)}")
        elif m := _ELIM.match(line.strip()):
            elim.append(f"{m.group(1)} falls to {m.group(2)}")
        elif m := _REPEL.match(line.split(" [")[0].strip()):
            repels.append(f"{m.group(1)} holds {m.group(3)} against {m.group(2)}")
        elif m := _VIOL.match(line.strip()):
            viols.append(f"{m.group(1)} cheats ({m.group(2)[:60]})")
        elif m := _WIN_HOLD.match(line.split(" [")[0].strip()):
            holds.append(f"{m.group(1)} bruises {m.group(3)} but {m.group(2)} still stands")

    bits: list[str] = []
    if captures:
        bits.append("Capture reel: " + "; ".join(captures[:4]) + ".")
    if holds:
        bits.append("Blood without land: " + "; ".join(holds[:3]) + ".")
    if repels:
        bits.append("Shield wall: " + "; ".join(repels[:3]) + ".")
    if elim:
        bits.append("Elimination: " + "; ".join(elim) + ".")
    if viols:
        bits.append("Cheat Watch: " + "; ".join(viols[:2]) + ".")
    if not bits:
        bits.append("The front went quiet. Fortunes shifted in the ledger alone.")

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    board = " · ".join(f"{f.upper()} {s}" for f, s in ranked)
    lead = ""
    if ranked:
        top_f, top_s = ranked[0]
        if len(ranked) > 1 and top_s > ranked[1][1]:
            lead = f" {top_f.upper()} leads by {top_s - ranked[1][1]}."
        elif len(ranked) > 1 and top_s == ranked[1][1]:
            lead = " Dead heat at the top."
    return (
        f"[ZEUS · ROUND {round_num}/{rounds}] "
        + " ".join(bits)
        + f" Scoreboard: {board}.{lead}"
    )


def narrate_finish(
    winner: str,
    how: str,
    scores: dict[str, int],
    cheat_count: int,
    land: dict[str, int] | None = None,
) -> str:
    """Closing monologue after THE RECKONING."""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    board = ", ".join(f"{f.upper()} {s}" for f, s in ranked)
    land_bit = ""
    if land:
        land_bit = " Land: " + ", ".join(f"{f.upper()} {n}" for f, n in land.items()) + "."
    cheat = (
        " Cheat Watch stayed quiet — a clean war."
        if cheat_count == 0
        else f" Cheat Watch logged {cheat_count} violation(s)."
    )
    if winner == "tie":
        crown = "No crown. The map refuses a single master."
    else:
        crown = f"Crown to {winner.upper()} {how}."
    return f"[ZEUS · FINAL] {crown} Final tallies: {board}.{land_bit}{cheat}"


def ascii_map_frame(title: str, map_text: str) -> str:
    """Box a render_map dump for the spectator stream."""
    width = max((len(line) for line in map_text.splitlines()), default=40)
    width = max(width, len(title) + 4, 40)
    bar = "═" * (width + 2)
    body = "\n".join(f"║ {line.ljust(width)} ║" for line in map_text.splitlines())
    return f"╔{bar}╗\n║ {title.ljust(width)} ║\n╠{bar}╣\n{body}\n╚{bar}╝"


def theater_from_round_prompt(prompt: str) -> str:
    """Fallback parser when only the Zeus prompt string is available."""
    round_m = re.search(r"Round (\d+) of (\d+)", prompt)
    scores: dict[str, int] = {}
    sm = re.search(r"Scores:\s*(\{.*\})", prompt)
    if sm:
        try:
            import json
            raw = json.loads(sm.group(1))
            if isinstance(raw, dict):
                scores = {str(k): int(v) for k, v in raw.items()}
        except Exception:
            scores = {}
    news_lines = [
        line[2:].strip()
        for line in prompt.splitlines()
        if line.startswith("- ")
    ]
    if "war is over" in prompt.lower() or "THE RECKONING" in prompt:
        winner = "tie"
        wm = re.search(r"VICTOR:\s*(\w+)", prompt)
        if wm:
            winner = wm.group(1).lower()
        how = "on points"
        if "total conquest" in prompt.lower():
            how = "by total conquest"
        cheat_count = len(re.findall(r"round \d+:", prompt))
        if "Clean war" in prompt:
            cheat_count = 0
        return narrate_finish(winner, how, scores or _scores_from_report(prompt), cheat_count)
    rnd = int(round_m.group(1)) if round_m else 0
    total = int(round_m.group(2)) if round_m else 0
    return narrate_round(rnd, total, news_lines, scores)


def _scores_from_report(prompt: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in re.finditer(r"^(RED|BLUE|GOLD|GREEN)\s+score\s+(\d+)", prompt, re.M | re.I):
        out[m.group(1).lower()] = int(m.group(2))
    return out


def public_board(state: dict[str, Any]) -> str:
    """One-line scoreboard from public_state()."""
    factions = state.get("factions") or {}
    parts = []
    for f, info in factions.items():
        regions = ",".join(info.get("regions") or []) or "-"
        parts.append(f"{f.upper()} score={info.get('score', 0)} regions={regions}")
    return " | ".join(parts)
