"""Match recorder: turn a Conquest event log into a markdown movie script."""
from __future__ import annotations

from typing import Any


def events_to_script(
    events: list[dict],
    result: dict | None = None,
    *,
    title: str = "CONQUEST — MATCH REEL",
    seed: int | None = None,
    scenario: str = "conquest",
) -> str:
    """Build a watchable markdown highlight reel from engine events."""
    result = result or {}
    lines: list[str] = [
        f"# {title}",
        "",
        f"_Scenario `{scenario}`"
        + (f", seed `{seed}`" if seed is not None else "")
        + "._",
        "",
    ]

    opener = next((e for e in events if e.get("type") == "arena_status" and "CONQUEST" in (e.get("content") or "")), None)
    if opener and opener.get("content"):
        lines.append("## Cold open")
        lines.append("```")
        lines.append(opener["content"].rstrip())
        lines.append("```")
        lines.append("")

    highlights: list[str] = []
    round_num = 0
    captures = 0
    battles = 0
    violations = 0
    eliminations = 0

    for e in events:
        et = e.get("type")
        content = (e.get("content") or "").strip()
        if et == "arena_round_start":
            round_num = int(e.get("round") or 0)
            name = e.get("name") or f"ROUND {round_num}"
            if round_num == 99:
                lines.append("## THE RECKONING")
            else:
                lines.append(f"## Act {round_num}: {name}")
            lines.append("")
            continue
        if et == "arena_commentary" and content:
            lines.append(f"> {content.replace(chr(10), ' ').strip()}")
            lines.append("")
            continue
        if et == "arena_status" and content.startswith("MAP AFTER"):
            lines.append("<details><summary>Map after the round</summary>")
            lines.append("")
            lines.append("```")
            lines.append(content)
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")
            continue
        if et == "arena_status" and content.startswith("THE MAP"):
            lines.append("<details><summary>Opening map</summary>")
            lines.append("")
            lines.append("```")
            lines.append(content)
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")
            continue
        if et == "arena_team_action":
            action = e.get("action_type")
            team = (e.get("team") or "?").upper()
            if action == "battle" or action == "attack":
                battles += 1
            if action == "battle" and "CAPTURES" in content:
                captures += 1
                clip = content.split("\n", 1)[0]
                highlights.append(f"**R{round_num}** {clip}")
                lines.append(f"- 🎬 **CAPTURE** ({team}): {clip}")
            elif action == "battle":
                clip = content.split("\n", 1)[0]
                lines.append(f"- ⚔️ {clip}")
            elif action == "violation":
                violations += 1
                highlights.append(f"**R{round_num}** CHEAT {team}: {content}")
                lines.append(f"- 🚨 **VIOLATION** ({team}): {content}")
            elif action == "spy":
                lines.append(f"- 🕵️ {team}: {content}")
            elif action == "fortify":
                lines.append(f"- 🧱 {team}: {content}")
            elif action == "diplomacy":
                lines.append(f"- 📜 {team}: {content}")
            elif action == "reinforce" and round_num <= 2:
                lines.append(f"- ➕ {team}: {content}")
            continue
        if et == "arena_status" and "ELIMINATED" in content:
            eliminations += 1
            highlights.append(f"**R{round_num}** {content}")
            lines.append(f"- ☠️ {content}")
            lines.append("")
            continue
        if et == "arena_scores":
            scores = e.get("scores") or {}
            board = " · ".join(f"{f.upper()} {s}" for f, s in scores.items())
            lines.append(f"_Scoreboard: {board}_")
            lines.append("")
            continue
        if et == "arena_result":
            winner = e.get("winner")
            scores = e.get("scores") or {}
            lines.append("## Credits")
            lines.append("")
            lines.append(
                f"**Winner:** `{winner}` · "
                + " · ".join(f"{f}={s}" for f, s in scores.items())
            )
            lines.append("")

    if highlights:
        # Insert highlight reel near the top (after cold open block).
        reel = ["## Highlight reel", ""] + [f"{i+1}. {h}" for i, h in enumerate(highlights[:12])] + [""]
        # Find insertion point after cold open
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("## Act") or line.startswith("## THE RECKONING"):
                insert_at = i
                break
        if insert_at == 0:
            insert_at = min(len(lines), 8)
        lines[insert_at:insert_at] = reel

    lines.append("## Box score")
    lines.append("")
    lines.append(
        f"| Battles logged | Captures | Eliminations | Violations |\n"
        f"| --- | --- | --- | --- |\n"
        f"| {battles} | {captures} | {eliminations} | {violations} |"
    )
    lines.append("")
    if result:
        lines.append(
            f"Engine result: winner=`{result.get('winner')}` "
            f"scores=`{result.get('scores')}`."
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
