"""Play Conquest with cohort controllers. No model API, no API key.

Examples (from the repo root):

    python -m forge.arena.cohort_match --red subprocess --blue subprocess --rounds 4

    python -m forge.arena.cohort_match --red file:/tmp/conquest-red --blue heuristic

    python -m forge.arena.cohort_match --scenario conquest_ffa \\
        --red heuristic --blue heuristic --gold heuristic --green heuristic

    python -m forge.arena.cohort_match --red heuristic --blue heuristic \\
        --movie /tmp/conquest-reel.md --events /tmp/conquest-events.json

File drop: the engine writes ``<dir>/request.json`` and blocks until
``<dir>/response.json`` contains ``{"id": "<same id>", "body": { ... }}``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from forge.arena.cohort import parse_controller
from forge.arena.conquest import CONQUEST_SCENARIOS, ConquestGame
from forge.arena.recorder import events_to_script


def _run(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(
        description="Conquest match with cohort players (no model API)")
    parser.add_argument("--scenario", default="conquest",
                        choices=sorted(CONQUEST_SCENARIOS))
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--red", default="subprocess")
    parser.add_argument("--blue", default="subprocess")
    parser.add_argument("--gold", default="",
                        help="cohort spec for gold (Four Crowns); default heuristic")
    parser.add_argument("--green", default="",
                        help="cohort spec for green (Four Crowns); default heuristic")
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="seconds to wait for one cohort reply")
    parser.add_argument("--events", default="", help="optional path for the JSON event log")
    parser.add_argument("--movie", default="",
                        help="optional path for a markdown movie script / highlight reel")
    parser.add_argument("--no-theater", action="store_true",
                        help="silence rule-based Zeus commentary")
    args = parser.parse_args(argv)

    scenario = CONQUEST_SCENARIOS[args.scenario]
    factions = list(scenario["factions"])
    specs: dict[str, str] = {"red": args.red, "blue": args.blue}
    if "gold" in factions:
        specs["gold"] = args.gold or "heuristic"
    if "green" in factions:
        specs["green"] = args.green or "heuristic"
    missing = [f for f in factions if f not in specs]
    if missing:
        raise SystemExit(f"scenario {args.scenario} needs controllers for: {', '.join(missing)}")

    root = Path(__file__).resolve().parents[2]
    controllers = {
        faction: parse_controller(spec, timeout=args.timeout, cwd=root)
        for faction, spec in specs.items()
    }
    theater = not args.no_theater
    game = ConquestGame(
        args.scenario,
        models={f: f"cohort:{specs[f]}" for f in controllers},
        seed=args.seed,
        llm=_refuse_model_api,
        commentary=theater,
        theater=theater,
        rounds=args.rounds,
        controllers=controllers,
    )
    events: list[dict] = []
    result: dict = {}
    try:
        gen = game.run()
        while True:
            try:
                event = next(gen)
            except StopIteration as stop:
                result = stop.value or {}
                break
            events.append(event)
            if event.get("type") in {"arena_status", "arena_commentary"} and event.get("content"):
                print(event["content"], flush=True)
            elif event.get("type") == "arena_result":
                print(
                    f"RESULT winner={event.get('winner')} "
                    f"scores={json.dumps(event.get('scores'))}",
                    flush=True,
                )
    finally:
        for ctrl in controllers.values():
            close = getattr(ctrl, "close", None)
            if close:
                close()
    if args.events:
        Path(args.events).write_text(json.dumps(events, indent=2), encoding="utf-8")
        print(f"events: {args.events} ({len(events)})", flush=True)
    if args.movie:
        script = events_to_script(
            events, result, seed=args.seed, scenario=args.scenario,
            title=f"CONQUEST — {scenario['name'].upper()} REEL",
        )
        Path(args.movie).write_text(script, encoding="utf-8")
        print(f"movie: {args.movie}", flush=True)
    violations = {f: len(p.violations) for f, p in game.players.items()}
    print(f"violations: {json.dumps(violations)}", flush=True)
    print(f"finished: {bool(result)} winner={result.get('winner')}", flush=True)
    return result


def _refuse_model_api(*_a, **_k) -> str:
    raise RuntimeError("cohort match must not call a model API")


def main() -> None:
    result = _run()
    if not result or result.get("cancelled"):
        sys.exit(1)


if __name__ == "__main__":
    main()
