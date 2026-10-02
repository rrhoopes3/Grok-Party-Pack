"""Theater, recorder, smarter cohort bot, Four Crowns — no model API."""
from __future__ import annotations

from forge.arena.cohort_bot import HeuristicCohort, plan_orders
from forge.arena.conquest import ADJACENCY, REGIONS, ConquestGame
from forge.arena.recorder import events_to_script
from forge.arena.theater import ascii_map_frame, narrate_finish, narrate_round


def _refuse(*_a, **_k):
    raise AssertionError("model API must not be called")


def _play(controllers, scenario="conquest", rounds=3, seed=7, theater=True):
    game = ConquestGame(
        scenario,
        models={f: "cohort" for f in controllers},
        seed=seed,
        llm=_refuse,
        commentary=theater,
        theater=theater,
        rounds=rounds,
        controllers=controllers,
    )
    events = []
    result = {}
    gen = game.run()
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            result = stop.value or {}
            break
    return game, events, result


def test_theater_round_and_finish_templates():
    line = narrate_round(
        2, 4,
        ["RED CAPTURES Paradox Pass [x]", "BLUE was caught breaking the rules: not adjacent"],
        {"red": 20, "blue": 18},
    )
    assert "ZEUS" in line and "CAPTURE" in line.upper() or "storms" in line
    assert "Cheat Watch" in line
    assert "RED 20" in line
    fin = narrate_finish("red", "on points", {"red": 40, "blue": 33}, 0, {"red": 7, "blue": 5})
    assert "Crown to RED" in fin
    assert "clean war" in fin.lower()
    framed = ascii_map_frame("MAP", "Logic Peaks\n  Axiom Ridge   RED      3")
    assert "╔" in framed and "MAP" in framed


def test_theater_match_emits_commentary_without_llm():
    ctrls = {"red": HeuristicCohort(), "blue": HeuristicCohort()}
    game, events, result = _play(ctrls, rounds=2, seed=11, theater=True)
    assert result.get("winner") in {"red", "blue", "tie"}
    comments = [e for e in events if e.get("type") == "arena_commentary"]
    assert comments, "expected rule-based Zeus lines"
    assert all("ZEUS" in (c.get("content") or "") for c in comments)
    assert game.players["red"].violations == []
    assert game.players["blue"].violations == []


def test_recorder_builds_movie_script():
    ctrls = {"red": HeuristicCohort(), "blue": HeuristicCohort()}
    _, events, result = _play(ctrls, rounds=2, seed=5, theater=True)
    script = events_to_script(events, result, seed=5, scenario="conquest")
    assert "# CONQUEST" in script
    assert "Highlight reel" in script or "Act 1" in script
    assert "Box score" in script
    assert f"winner=`{result.get('winner')}`" in script


def test_smarter_bot_prefers_adjacent_and_may_fortify_or_spy():
    # Build a fog-safe view where red owns a triangle edge and can fortify.
    view = {
        "turn": 3,
        "rounds": 8,
        "faction": "red",
        "alive": ["red", "blue"],
        "reinforcements": 3,
        "score": 12,
        "perks": [],
        "memory": "",
        "inbox": [],
        "intel": [],
        "max_attacks": 3,
        "territories": [],
    }
    # Minimal map: red owns Axiom+Proof, blue owns Paradox, rest hidden/neutral visible.
    for name, region in [
        ("Axiom Ridge", "Logic Peaks"),
        ("Proof Spire", "Logic Peaks"),
        ("Paradox Pass", "Logic Peaks"),
        ("Compiler Cove", "Code Coast"),
    ]:
        owner = "red" if name in ("Axiom Ridge", "Proof Spire") else (
            "blue" if name == "Paradox Pass" else "neutral"
        )
        troops = 5 if name == "Axiom Ridge" else (2 if name == "Proof Spire" else 2)
        view["territories"].append({
            "name": name,
            "region": region,
            "domain": REGIONS[region]["domain"],
            "adjacent": sorted(ADJACENCY[name]),
            "owner": owner,
            "troops": troops,
        })
    # Hide everything else.
    from forge.arena.conquest import TERRITORIES, TERRITORY_REGION
    known = {t["name"] for t in view["territories"]}
    for name in TERRITORIES:
        if name in known:
            continue
        region = TERRITORY_REGION[name]
        view["territories"].append({
            "name": name,
            "region": region,
            "domain": REGIONS[region]["domain"],
            "adjacent": sorted(ADJACENCY[name]),
            "owner": None,
            "troops": None,
        })

    orders = plan_orders({"kind": "orders", "faction": "red", "view": view})
    assert orders["reinforce"]
    for a in orders["attacks"]:
        assert a["to"] in ADJACENCY[a["from"]]
        assert a["troops"] >= 1
    # Goal region is Logic Peaks — expect an attack into Paradox Pass when affordable.
    targets = {a["to"] for a in orders["attacks"]}
    assert "Paradox Pass" in targets
    # With an interior stack and a frontier, fortify or spy should show up.
    assert orders["fortify"] is not None or orders["spy"] is not None


def test_four_crowns_cohort_match():
    ctrls = {f: HeuristicCohort() for f in ("red", "blue", "gold", "green")}
    game, events, result = _play(ctrls, scenario="conquest_ffa", rounds=2, seed=9, theater=True)
    assert set(game.players) == {"red", "blue", "gold", "green"}
    assert result.get("winner") in {"red", "blue", "gold", "green", "tie"}
    assert any(e.get("type") == "arena_result" for e in events)
    for p in game.players.values():
        assert p.violations == []
