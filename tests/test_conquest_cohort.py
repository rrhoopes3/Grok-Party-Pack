"""Cohort players: external JSON orders, no model API."""
from __future__ import annotations

import json
import random
import threading
import time

import pytest

from forge.arena.cohort import FileCohort, SubprocessCohort
from forge.arena.cohort_bot import HeuristicCohort, solve_duel
from forge.arena.conquest import NEUTRAL, TERRITORIES, ConquestGame, generate_task


def _refuse(*_a, **_k):
    raise AssertionError("model API must not be called")


def _play(controllers, rounds=2, seed=3):
    game = ConquestGame(
        "conquest",
        models={f: "cohort" for f in controllers},
        seed=seed,
        llm=_refuse,
        commentary=False,
        rounds=rounds,
        controllers=controllers,
    )
    events = []
    gen = game.run()
    result = {}
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            result = stop.value or {}
            break
    return game, events, result


def test_heuristic_match_finishes_without_model_or_violations():
    game, events, result = _play({"red": HeuristicCohort(), "blue": HeuristicCohort()})
    assert result.get("winner") in {"red", "blue", "tie"}
    assert any(e.get("type") == "arena_result" for e in events)
    assert game.players["red"].violations == []
    assert game.players["blue"].violations == []
    assert sum(p.battles_won + p.battles_lost for p in game.players.values()) > 0


def test_illegal_cohort_order_is_still_a_violation():
    def cheat(_req):
        return {"attacks": [{"from": "Axiom Ridge", "to": "Haiku Mesa", "troops": 1}],
                "reinforce": {}, "fortify": None, "spy": None, "messages": [], "memory": "nope"}

    game = ConquestGame(
        "conquest", seed=1, llm=_refuse, commentary=False, rounds=1,
        controllers={"red": cheat, "blue": HeuristicCohort()},
    )
    # Pin a known illegal pair so the cheat is definitely not adjacent.
    for t in TERRITORIES:
        game.map[t].owner, game.map[t].troops = (NEUTRAL, 2)
    game.map["Axiom Ridge"].owner, game.map["Axiom Ridge"].troops = "red", 4
    game.map["Rot Fen"].owner, game.map["Rot Fen"].troops = "blue", 4
    game.turn = 1
    list(game.play_turn("red"))
    assert game.players["red"].violations
    assert "not adjacent" in game.players["red"].violations[0]


def test_file_drop_controller_round_trip(tmp_path):
    ctrl = FileCohort(tmp_path, timeout=5)

    def clerk():
        req_path = tmp_path / "request.json"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if req_path.exists():
                data = json.loads(req_path.read_text())
                body = {"reinforce": {}, "attacks": [], "fortify": None,
                        "spy": None, "messages": [], "memory": "file drop"}
                payload = json.dumps({"id": data["id"], "body": body})
                (tmp_path / "response.json").write_text(payload)
                return
            time.sleep(0.02)
    threading.Thread(target=clerk, daemon=True).start()
    game = ConquestGame(
        "conquest", seed=1, llm=_refuse, commentary=False, rounds=1,
        controllers={"red": ctrl, "blue": HeuristicCohort()},
    )
    game.turn = 1
    events = list(game.play_turn("red"))
    assert game.players["red"].memory == "file drop"
    assert game.players["red"].violations == []
    assert any(e.get("action_type") == "reinforce" for e in events)


def test_subprocess_bot_answers_one_request():
    import sys
    bot = SubprocessCohort([sys.executable, "-m", "forge.arena.cohort_bot"], timeout=10)
    try:
        body = bot({
            "kind": "duel",
            "faction": "red",
            "turn": 1,
            "prompt": "TASK (logic):\nWhat is (3 ^ 5) mod 7? Answer with a single integer.\n",
            "system": "",
            "view": {},
        })
        assert body["body"]["answer"] == pow(3, 5, 7)
    finally:
        bot.close()


@pytest.mark.parametrize("domain", ["logic", "code", "cipher", "words"])
def test_script_duelist_beats_neutral(domain):
    bot = HeuristicCohort()
    for seed in range(8):
        task = generate_task(domain, random.Random(seed))
        reply = bot({"kind": "duel", "prompt": f"TASK ({task.domain}):\n{task.prompt}\n"})
        answer = reply.get("code") if domain == "code" else reply.get("answer")
        from forge.arena.conquest import score_answer
        score, detail = score_answer(task, answer, random.Random(seed))
        assert score >= 1.0, (domain, task.kind, detail, reply)


def test_solve_duel_shape():
    reply = solve_duel("TASK (logic):\nWhat is (2 ^ 8) mod 5? Answer with a single integer.\n")
    assert reply == {"answer": pow(2, 8, 5)}
