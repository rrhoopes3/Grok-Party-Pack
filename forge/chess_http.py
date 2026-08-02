"""Flask Blueprint for Chess Arena HTTP API."""
from __future__ import annotations
import logging
from flask import Blueprint, jsonify, request
from forge import chess_arena as _chess

log = logging.getLogger("forge.chess_http")
chess_bp = Blueprint("chess", __name__)

@chess_bp.route("/api/chess", methods=["GET"])
def chess_list():
    """List known matches (active + finished, newest first, capped)."""
    return jsonify({"matches": _chess.list_matches()})


@chess_bp.route("/api/chess", methods=["POST"])
def chess_new():
    """Create a match.
    AI vs AI: {white_model, black_model, ...}
    Human vs AI: {human_side: "white"|"black", ai_model: "...", judge_model?, ...}
    """
    body = request.get_json(silent=True) or {}

    human_side = body.get("human_side")
    if human_side:
        ai_model = (body.get("ai_model") or "").strip()
        if not ai_model:
            return jsonify({"error": "ai_model is required when human_side is set"}), 400
        if human_side == "white":
            white, black = "human", ai_model
        else:
            white, black = ai_model, "human"
    else:
        white = (body.get("white_model") or "").strip()
        black = (body.get("black_model") or "").strip()
        if not white or not black:
            return jsonify({"error": "white_model and black_model are required"}), 400

    try:
        match = _chess.new_match(
            white_model=white,
            black_model=black,
            starting_fen=body.get("starting_fen", "") or "",
            judge_model=(body.get("judge_model") or "grok-4.20-0309-reasoning").strip(),
            commentary_interval=int(body.get("commentary_interval") or 2),
            commentary_window_plies=int(body.get("commentary_window_plies") or 0),
            human_side=human_side,
        )
    except Exception as e:
        log.exception("chess_new failed")
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 400
    return jsonify(_chess.serialize_match(match))


@chess_bp.route("/api/chess/<match_id>", methods=["GET"])
def chess_get(match_id: str):
    m = _chess.get_match(match_id)
    if m is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    return jsonify(_chess.serialize_match(m))


@chess_bp.route("/api/chess/<match_id>/step", methods=["POST"])
def chess_step(match_id: str):
    """Ask the current side's LLM for a move and apply it. If this step
    lands on a commentary beat (every Nth full-move pair, or game-over),
    the judge model is also called and its output included as
    `new_commentary` in the response so the UI can render + TTS it."""
    m = _chess.get_match(match_id)
    if m is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    try:
        m = _chess.make_move(match_id)
    except RuntimeError as e:
        # Missing API key / upstream failure — surface as 502 so UI can show it
        return jsonify({"error": str(e), **_chess.serialize_match(_chess.get_match(match_id))}), 502
    except Exception as e:
        log.exception("chess_step failed for %s", match_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    # Fire the judge if this beat calls for it. A judge failure never
    # blocks the move response — we surface it as `commentary_error` so
    # the UI can render the actual cause (missing key, multi-agent model,
    # upstream 4xx, etc.) rather than silently staring at "no commentary".
    new_commentary = None
    commentary_error = None
    try:
        rec = _chess.maybe_generate_commentary(match_id)
        if rec is not None:
            new_commentary = {
                "after_move_n": rec.after_move_n,
                "round_num": rec.round_num,
                "text": rec.text,
                "model": rec.model,
                "ms": rec.ms,
                "emitted_at": rec.emitted_at,
            }
    except RuntimeError as e:
        commentary_error = str(e)
        log.warning("chess %s commentary: %s", match_id, e)
    except Exception as e:
        commentary_error = f"{type(e).__name__}: {e}"
        log.exception("chess commentary failed for %s (non-fatal)", match_id)

    payload = _chess.serialize_match(m)
    if new_commentary:
        payload["new_commentary"] = new_commentary
    if commentary_error:
        payload["commentary_error"] = commentary_error
    return jsonify(payload)


@chess_bp.route("/api/chess/<match_id>/commentary", methods=["POST"])
def chess_commentary(match_id: str):
    """Manually fire a commentary beat (ignoring the interval). Useful
    for the UI "Call it" button. Returns 502 with the actual upstream
    error on judge-call failure so the UI can show a useful message."""
    if _chess.get_match(match_id) is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    try:
        rec = _chess.generate_commentary(match_id)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        log.exception("chess_commentary failed for %s", match_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    if rec is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    return jsonify({
        "after_move_n": rec.after_move_n,
        "round_num": rec.round_num,
        "text": rec.text,
        "model": rec.model,
        "ms": rec.ms,
        "emitted_at": rec.emitted_at,
    })


@chess_bp.route("/api/chess/<match_id>/resign", methods=["POST"])
def chess_resign(match_id: str):
    """Resign for a side. Body: {side: 'white'|'black'}."""
    body = request.get_json(silent=True) or {}
    side = body.get("side", "")
    try:
        m = _chess.resign(match_id, side)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if m is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    return jsonify(_chess.serialize_match(m))


@chess_bp.route("/api/chess/<match_id>", methods=["DELETE"])
def chess_delete(match_id: str):
    """Delete a match from the in-memory registry."""
    if not _chess.delete_match(match_id):
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    return jsonify({"deleted": match_id})


@chess_bp.route("/api/chess/<match_id>/pgn", methods=["GET"])
def chess_pgn(match_id: str):
    """Return PGN text with house-move tags ({adjudicated}, protocol forfeit)."""
    m = _chess.get_match(match_id)
    if m is None:
        return jsonify({"error": f"Unknown match: {match_id!r}"}), 404
    pgn = _chess.export_pgn(m)
    return pgn, 200, {
        "Content-Type": "application/x-chess-pgn; charset=utf-8",
        "Content-Disposition": f'attachment; filename="forge-{match_id}.pgn"',
    }

