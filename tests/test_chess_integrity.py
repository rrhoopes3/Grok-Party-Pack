"""Chess arena integrity: server history, PGN house tags, no RNG house moves."""
from __future__ import annotations

import chess
import pytest

from forge import chess_arena as ca


@pytest.fixture
def match():
    m = ca.new_match(
        white_model="test-white",
        black_model="test-black",
        judge_model="test-judge",
        commentary_interval=99,
    )
    yield m
    ca.delete_match(m.id)


def _push_server_move(m: ca.ChessMatch, uci: str, *, source: str = ca.MOVE_SOURCE_MODEL):
    """Apply a move as the server would after a legal model reply."""
    mv = chess.Move.from_uci(uci)
    assert mv in m.board.legal_moves
    san = m.board.san(mv)
    side = m.turn
    m.board.push(mv)
    m.moves.append(ca.MoveRecord(
        n=len(m.moves) + 1,
        side=side,
        san=san,
        uci=uci,
        thinking="",
        forced=(source != ca.MOVE_SOURCE_MODEL),
        attempts=1,
        ms=1,
        source=source,
    ))


def test_server_history_uses_applied_san_not_intentions(match):
    _push_server_move(match, "e2e4")
    _push_server_move(match, "e7e5")
    hist = ca._server_history_san(match)
    assert hist == ["1. e4", "1...e5"]
    prompt = ca._build_prompt(match)
    assert "1. e4" in prompt
    assert "1...e5" in prompt
    assert "server-applied SAN only" in prompt
    # FEN must match board after those two plies
    assert match.board.fen().startswith("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR")


def test_adjudicated_move_appears_in_history_and_pgn(match):
    _push_server_move(match, "e2e4")
    _push_server_move(match, "c7c5")
    _push_server_move(match, "g1f3", source=ca.MOVE_SOURCE_ADJUDICATED)
    hist = ca._server_history_san(match)
    assert any("Nf3" in h for h in hist)
    prompt = ca._build_prompt(match)
    assert "Nf3" in prompt or "n" in match.board.fen()
    pgn = ca.export_pgn(match)
    assert "{adjudicated}" in pgn
    assert match.has_house_moves is True
    ser = ca.serialize_match(match)
    assert ser["has_house_moves"] is True
    assert ser["moves"][-1]["source"] == ca.MOVE_SOURCE_ADJUDICATED
    assert ser["moves"][-1]["forced"] is True


def test_judge_prompt_lists_captures_by_name(match):
    # Scholar-ish: e4 e5 Qh5 Nc6 Qxf7# path partial — just capture a pawn with Q
    _push_server_move(match, "e2e4")
    _push_server_move(match, "e7e5")
    _push_server_move(match, "d1h5")
    # Black knight
    _push_server_move(match, "b8c6")
    # Capture e5 pawn with queen
    victim = ca._detect_capture(match.board, chess.Move.from_uci("h5e5"))
    assert victim is not None
    san = match.board.san(chess.Move.from_uci("h5e5"))
    side = match.turn
    match.board.push_uci("h5e5")
    match.captures.append(ca.CapturedPiece(
        by=side, piece_symbol=victim.symbol(), move_n=len(match.moves) + 1, move_san=san,
    ))
    match.moves.append(ca.MoveRecord(
        n=len(match.moves) + 1, side=side, san=san, uci="h5e5",
        thinking="", forced=False, attempts=1, ms=1,
    ))
    jp = ca._build_judge_prompt(match)
    assert "captured" in jp.lower()
    assert "pawn" in jp.lower()
    assert "SERVER-APPLIED" in jp or "server" in jp.lower()


def test_export_pgn_protocol_forfeit_tag(match):
    match.protocol_loss_by = "black"
    match.moves.append(ca.MoveRecord(
        n=1, side="black", san="--", uci="", thinking="fail",
        forced=True, attempts=3, ms=100, source=ca.MOVE_SOURCE_FORFEIT,
    ))
    pgn = ca.export_pgn(match)
    assert "protocol forfeit" in pgn
    assert match.result == "1-0"
    assert match.end_reason == "protocol forfeit (black)"


def test_numbered_legal_list_nonempty_start():
    board = chess.Board()
    legal, block = ca._numbered_legal_list(board)
    assert len(legal) == 20
    assert "1. e2e4" in block or "e2e4" in block


def test_list_player_models_party_pack_catalog(monkeypatch):
    monkeypatch.setattr(ca, "_lmstudio_available_models", lambda _url: [])
    cat = ca.list_player_models()
    ids = {m["id"] for m in cat["models"]}
    assert "auto" not in ids
    assert "grok-4.20-0309-reasoning" in ids
    assert "grok-4-1-fast-non-reasoning" in ids
    assert cat["defaults"]["white_model"]
    assert cat["defaults"]["black_model"]
    assert cat["defaults"]["judge_model"]
    judge = next(m for m in cat["models"] if m["id"] == cat["defaults"]["judge_model"])
    assert judge["judge_ok"] is True
    for m in cat["models"]:
        if "multi-agent" in m["id"] or "planner only" in (m.get("label") or "").lower():
            assert m["judge_ok"] is False


def test_list_player_models_swaps_live_lmstudio(monkeypatch):
    monkeypatch.setattr(ca, "_lmstudio_available_models", lambda _url: ["qwen/qwen3.5-9b"])
    cat = ca.list_player_models()
    ids = {m["id"] for m in cat["models"]}
    assert "lmstudio:default" not in ids
    assert "lmstudio:qwen/qwen3.5-9b" in ids
    assert cat["lmstudio_live"] is True


def test_catalog_keeps_old_ids_and_adds_current_chat_models(monkeypatch):
    monkeypatch.setattr(ca, "_lmstudio_available_models", lambda _url: [])
    cat = ca.list_player_models()
    by_id = {m["id"]: m for m in cat["models"]}
    for mid in (
        "grok-4.5", "claude-opus-5", "claude-opus-4-8", "o3", "gpt-5.5-pro",
        "grok-4.7", "grok-4.6", "claude-opus-5-5", "claude-haiku-5-5",
        "gpt-6-astra", "gemini-4-argon", "muse-wonder-1.0",
    ):
        assert mid in by_id, mid
    assert by_id["gemini-4-argon"]["provider"] == "Google"
    assert by_id["muse-wonder-1.0"]["provider"] == "Meta"
    assert by_id["gpt-6-astra"]["provider"] == "OpenAI"
    assert by_id["grok-4.20-multi-agent-0309"]["judge_ok"] is False


def test_chess_routes_and_effort_by_provider():
    assert ca._provider_for("gemini-4-argon") == "google"
    assert ca._provider_for("muse-wonder-1.0") == "meta"
    assert ca._provider_for("gpt-6-astra") == "openai"
    assert ca._provider_for("o3") == "openai"
    assert ca._provider_for("grok-4.7") == "xai"

    opus = ca._anthropic_thinking_kwargs("claude-opus-5-5")
    assert opus["output_config"]["effort"] == "high"
    assert opus["thinking"]["display"] == "summarized"
    haiku = ca._anthropic_thinking_kwargs("claude-haiku-5-5")
    assert haiku == {"output_config": {"effort": "high"}}
    assert ca._anthropic_thinking_kwargs("claude-sonnet-4-5-20250929") == {}
    assert ca._anthropic_thinking_kwargs("claude-haiku-4-5-20251001") == {}

    assert ca._chat_supports_reasoning_effort("gpt-6-astra")
    assert ca._chat_supports_reasoning_effort("o3")
    assert ca._chat_supports_reasoning_effort("gemini-4-argon")
    assert ca._chat_supports_reasoning_effort("muse-wonder-1.0")
    assert ca._chat_supports_reasoning_effort("grok-4.3")
    assert not ca._chat_supports_reasoning_effort("grok-4.20-0309-reasoning")
    assert not ca._chat_supports_reasoning_effort("grok-4.20-multi-agent-0309")
    assert not ca._chat_supports_reasoning_effort("gpt-4o")
    assert not ca._chat_supports_reasoning_effort("grok-4-1-fast-reasoning")

    assert ca._model_uses_max_completion_tokens("o3")
    assert ca._model_uses_max_completion_tokens("gpt-6-astra")
    assert not ca._model_uses_max_completion_tokens("gpt-4o")
    assert not ca._model_uses_max_completion_tokens("gemini-4-argon")
    assert ca._model_rejects_temperature("gpt-6-astra")
    assert ca._model_rejects_temperature("claude-haiku-5-5")
    assert ca._reasoning_tier("gemini-4-argon")
    assert ca._reasoning_tier("grok-4.7")
    assert not ca._reasoning_tier("gpt-4o")
    assert not ca._reasoning_tier("grok-code-fast-1")
