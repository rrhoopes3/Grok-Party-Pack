"""Play Conquest with cohort controllers. No model API, no API key.

Examples (from the repo root):

    python -m forge.arena.cohort_match --red subprocess --blue subprocess --rounds 4

    python -m forge.arena.cohort_match --red file:/tmp/conquest-red --blue heuristic

    python -m forge.arena.cohort_match --red 'cmd:python -m forge.arena.cohort_bot' --blue heuristic

File drop: the engine writes ``<dir>/request.json`` and blocks until
``<dir>/response.json`` contains ``{"id": "<same id>", "body": { ... }}``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from forge.arena.cohort import parse_controller
from forge.arena.conquest import ConquestGame


def _run(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(
        description="Conquest match with cohort players (no model API)")
    parser.add_argument("--scenario", default="conquest")
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--red", default="subprocess")
    parser.add_argument("--blue", default="subprocess")
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="seconds to wait for one cohort reply")
    parser.add_argument("--events", default="", help="optional path for the JSON event log")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    specs = {"red": args.red, "blue": args.blue}
    controllers = {
        faction: parse_controller(spec, timeout=args.timeout, cwd=root)
        for faction, spec in specs.items()
    }
    game = ConquestGame(
        args.scenario,
        models={f: f"cohort:{specs[f]}" for f in controllers},
        seed=args.seed,
        llm=_refuse_model_api,
        commentary=False,
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
            if event.get("type") == "arena_status" and event.get("content"):
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
