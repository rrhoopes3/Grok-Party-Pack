"""
Chess Arena — two-model LLM vs LLM chess matches.

Each match wraps `python-chess` for ground-truth move legality / end-state
detection, and asks each side's LLM for a UCI move per turn. Matches live
in memory (no persistence yet — a tab refresh keeps them via their id,
a server restart drops them).

Prompt design:
  * Give the model the FEN, the ASCII board, the SAN move history, and the
    explicit list of legal UCI moves. Listing the legals matters because
    LLMs routinely suggest phantom moves (bishop through pawn, castling
    through check) — pre-constraining the decision set keeps the match
    moving rather than burning retries.
  * Ask for "MOVE: <uci>" as the last line. Parse is permissive: we look
    for MOVE: <uci>, then any bare UCI token, then any SAN token.

Illegal-move policy:
  * Up to 3 retries per turn, each with a "your last attempt {x} was
    illegal" correction appended. After that, pick a random legal move and
    mark `forced=True` so the UI can annotate it.

The LLM caller is provider-agnostic — it routes by model-name prefix the
same way prophecy does, so any model registered in config.EXECUTOR_MODELS
can play.
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import chess

from forge.config import (
    XAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY,
    LMSTUDIO_BASE_URL, OLLAMA_BASE_URL,
)

log = logging.getLogger("forge.chess_arena")


# ────────────────────────────────────────────────────────────────────────
# LLM call — mirrors prophecy._llm_call; routes by model-name prefix.
# ────────────────────────────────────────────────────────────────────────

def _provider_for(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
        return "openai"
    if model.startswith("lmstudio:"):
        return "lmstudio"
    if model.startswith("ollama:"):
        return "ollama"
    return "xai"


def _llm_oneshot(prompt: str, system: str, model: str,
                 temperature: float = 0.3, max_tokens: int = 600) -> str:
    provider = _provider_for(model)
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        kwargs: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        kwargs["temperature"] = min(temperature, 1.0)
        resp = client.messages.create(**kwargs)
        return resp.content[0].text

    from openai import OpenAI
    base_url: Optional[str] = None
    api_key = "none"
    if provider == "openai":
        api_key = OPENAI_API_KEY or ""
    elif provider == "lmstudio":
        base_url = LMSTUDIO_BASE_URL
        api_key = "lm-studio"
        model = model.removeprefix("lmstudio:") or "default"
    elif provider == "ollama":
        base_url = OLLAMA_BASE_URL
        api_key = "ollama"
        model = model.removeprefix("ollama:") or "default"
    else:  # xai
        base_url = "https://api.x.ai/v1"
        api_key = XAI_API_KEY or ""

    client = OpenAI(api_key=api_key or "none", base_url=base_url)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=model, messages=messages,
        temperature=temperature, max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


# ────────────────────────────────────────────────────────────────────────
# Match state
# ────────────────────────────────────────────────────────────────────────

@dataclass
class MoveRecord:
    n: int                 # 1-indexed half-move count
    side: str              # "white" | "black"
    san: str               # standard algebraic notation
    uci: str               # long algebraic (e2e4, e1g1 for O-O)
    thinking: str          # trimmed model output (minus trailing MOVE: line)
    forced: bool           # true if we had to pick a random legal move
    attempts: int          # how many LLM calls it took
    ms: int                # wall-clock for the move


@dataclass
class ChessMatch:
    id: str
    white_model: str
    black_model: str
    board: chess.Board = field(default_factory=chess.Board)
    moves: list[MoveRecord] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_move_at: Optional[str] = None
    resigned_by: Optional[str] = None   # "white" | "black" | None
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ── Derived ────────────────────────────────────────────────────────

    @property
    def turn(self) -> str:
        return "white" if self.board.turn == chess.WHITE else "black"

    @property
    def current_model(self) -> str:
        return self.white_model if self.board.turn == chess.WHITE else self.black_model

    @property
    def is_over(self) -> bool:
        return self.resigned_by is not None or self.board.is_game_over()

    @property
    def result(self) -> Optional[str]:
        if self.resigned_by == "white":
            return "0-1"
        if self.resigned_by == "black":
            return "1-0"
        if self.board.is_game_over():
            return self.board.result()
        return None

    @property
    def end_reason(self) -> Optional[str]:
        if self.resigned_by:
            return f"resigned ({self.resigned_by})"
        if not self.board.is_game_over():
            return None
        if self.board.is_checkmate():
            return "checkmate"
        if self.board.is_stalemate():
            return "stalemate"
        if self.board.is_insufficient_material():
            return "insufficient material"
        if self.board.is_fifty_moves():
            return "fifty-move rule"
        if self.board.is_repetition():
            return "threefold repetition"
        return "draw"

    @property
    def status(self) -> str:
        if not self.is_over:
            return "active"
        res = self.result
        if res == "1-0":
            return "white_wins"
        if res == "0-1":
            return "black_wins"
        return "draw"


# ────────────────────────────────────────────────────────────────────────
# Serialization for /api/chess
# ────────────────────────────────────────────────────────────────────────

def _board_ascii(board: chess.Board) -> str:
    """Unicode-free ASCII board; rows labeled 8..1, columns a..h."""
    return str(board)


def _board_unicode(board: chess.Board) -> str:
    """Unicode chess figures — nicer for LLM prompts."""
    return board.unicode(borders=False, empty_square="·")


def serialize_match(m: ChessMatch) -> dict:
    """JSON-safe view returned by the API."""
    return {
        "id": m.id,
        "fen": m.board.fen(),
        "ascii": _board_ascii(m.board),
        "turn": m.turn if not m.is_over else None,
        "status": m.status,
        "result": m.result,
        "reason": m.end_reason,
        "white_model": m.white_model,
        "black_model": m.black_model,
        "current_model": m.current_model if not m.is_over else None,
        "in_check": m.board.is_check(),
        "halfmove_count": len(m.moves),
        "moves": [
            {
                "n": mv.n, "side": mv.side, "san": mv.san, "uci": mv.uci,
                "thinking": mv.thinking[:1200],
                "forced": mv.forced, "attempts": mv.attempts, "ms": mv.ms,
            }
            for mv in m.moves
        ],
        "created_at": m.created_at,
        "last_move_at": m.last_move_at,
    }


# ────────────────────────────────────────────────────────────────────────
# Move parsing — robust to models that wrap UCI in backticks, prose, etc.
# ────────────────────────────────────────────────────────────────────────

_UCI_RE = re.compile(r"\b([a-h][1-8][a-h][1-8][qrbn]?)\b", re.IGNORECASE)
_MOVE_PREFIX_RE = re.compile(r"MOVE\s*[:=]\s*`?([^\s`]+)`?", re.IGNORECASE)


def _extract_move(text: str, board: chess.Board) -> Optional[chess.Move]:
    """Return a legal chess.Move parsed from `text`, or None."""
    # Prefer an explicit "MOVE: ..." directive (whatever format)
    m = _MOVE_PREFIX_RE.search(text)
    candidates: list[str] = []
    if m:
        candidates.append(m.group(1))

    # Any UCI-shaped token in the body
    candidates.extend(_UCI_RE.findall(text))

    # Try UCI first
    for cand in candidates:
        cand = cand.strip().lower()
        try:
            mv = chess.Move.from_uci(cand)
            if mv in board.legal_moves:
                return mv
        except Exception:
            pass

    # Fall back to SAN parsing against a stripped version of the text
    # Scan tokens with likely SAN glyphs (ignore "." for move numbers).
    san_tokens = re.findall(r"[NBRQK]?[a-h1-8x=+#\-O0]+[+#]?", text)
    for tok in san_tokens:
        tok = tok.strip().rstrip(".,;:")
        if not tok or tok == "O":
            continue
        # Allow "O-O" / "O-O-O" (castling)
        try:
            mv = board.parse_san(tok)
            if mv in board.legal_moves:
                return mv
        except Exception:
            continue
    return None


# ────────────────────────────────────────────────────────────────────────
# Match registry
# ────────────────────────────────────────────────────────────────────────

_MATCHES: dict[str, ChessMatch] = {}
_REGISTRY_LOCK = threading.Lock()
_MAX_MATCHES = 32  # evict oldest finished when exceeded


def _evict_if_needed() -> None:
    if len(_MATCHES) <= _MAX_MATCHES:
        return
    finished = sorted(
        (m for m in _MATCHES.values() if m.is_over),
        key=lambda m: m.last_move_at or m.created_at,
    )
    for m in finished[: len(_MATCHES) - _MAX_MATCHES]:
        _MATCHES.pop(m.id, None)


def new_match(white_model: str, black_model: str, starting_fen: str = "") -> ChessMatch:
    with _REGISTRY_LOCK:
        board = chess.Board(starting_fen) if starting_fen else chess.Board()
        m = ChessMatch(
            id=uuid.uuid4().hex[:12],
            white_model=white_model,
            black_model=black_model,
            board=board,
        )
        _MATCHES[m.id] = m
        _evict_if_needed()
        log.info("chess match created: %s (%s vs %s)", m.id, white_model, black_model)
        return m


def get_match(match_id: str) -> Optional[ChessMatch]:
    return _MATCHES.get(match_id)


def list_matches() -> list[dict]:
    with _REGISTRY_LOCK:
        items = sorted(_MATCHES.values(), key=lambda m: m.created_at, reverse=True)
    return [serialize_match(m) for m in items]


def resign(match_id: str, side: str) -> Optional[ChessMatch]:
    if side not in ("white", "black"):
        raise ValueError(f"side must be white|black, got {side!r}")
    m = _MATCHES.get(match_id)
    if m is None:
        return None
    with m.lock:
        if m.is_over:
            return m
        m.resigned_by = side
        m.last_move_at = datetime.now(timezone.utc).isoformat()
    log.info("chess match %s resigned by %s", match_id, side)
    return m


def delete_match(match_id: str) -> bool:
    with _REGISTRY_LOCK:
        return _MATCHES.pop(match_id, None) is not None


# ────────────────────────────────────────────────────────────────────────
# Move generation
# ────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a chess-playing AI. You will be given the current board position "
    "and a list of legal moves. Analyze briefly and pick a move. "
    "Always respond with your chosen move as the FINAL line in this exact "
    "format: MOVE: <uci>  — e.g. MOVE: e2e4 (or MOVE: e1g1 for kingside castle, "
    "MOVE: e7e8q for promotion). Only pick from the provided legal moves."
)


def _build_prompt(m: ChessMatch, last_error: str = "") -> str:
    legal = [mv.uci() for mv in m.board.legal_moves]
    history_san: list[str] = []
    temp = chess.Board()
    for rec in m.moves:
        history_san.append(f"{(rec.n + 1) // 2}.{'' if rec.side == 'white' else '..'} {rec.san}")
        temp.push_uci(rec.uci)

    lines = [
        f"You are playing as {m.turn.upper()} in an ongoing game.",
        f"Opponent model: {m.black_model if m.turn == 'white' else m.white_model}",
        "",
        f"FEN: {m.board.fen()}",
        "",
        "Board (white uppercase, black lowercase, · = empty):",
        "```",
        _board_unicode(m.board),
        "```",
        "",
        f"Move history ({len(m.moves)} half-moves):",
        " ".join(history_san) if history_san else "(none — this is the first move)",
        "",
        f"You are in check: {'YES' if m.board.is_check() else 'no'}",
        f"Halfmove clock: {m.board.halfmove_clock}  Fullmove: {m.board.fullmove_number}",
        "",
        f"Legal moves ({len(legal)}): {', '.join(legal)}",
        "",
    ]
    if last_error:
        lines.append(f"Your previous reply was rejected: {last_error}")
        lines.append("Pick a DIFFERENT move from the legal list above.")
        lines.append("")
    lines.append("Think in 2–3 short sentences, then end with a single line:")
    lines.append("MOVE: <uci>")
    return "\n".join(lines)


def make_move(match_id: str, max_attempts: int = 3) -> Optional[ChessMatch]:
    """
    Ask the current side's model for a move, apply it.
    Returns the updated match (None if match_id unknown).
    Raises RuntimeError if the LLM call fails outright (no API key etc.).
    """
    m = _MATCHES.get(match_id)
    if m is None:
        return None

    with m.lock:
        if m.is_over:
            return m

        started = time.monotonic()
        model = m.current_model
        chosen: Optional[chess.Move] = None
        thinking = ""
        last_error = ""
        attempts = 0
        forced = False

        for attempts in range(1, max_attempts + 1):
            try:
                reply = _llm_oneshot(
                    prompt=_build_prompt(m, last_error=last_error),
                    system=_SYSTEM_PROMPT,
                    model=model,
                )
            except Exception as e:
                raise RuntimeError(f"LLM call failed for {model}: {type(e).__name__}: {e}")

            thinking = reply.strip()
            chosen = _extract_move(reply, m.board)
            if chosen is not None:
                break
            last_error = (
                f"No legal move found in your reply. "
                f"You said: {reply.strip()[:180]!r}"
            )
            log.warning("chess %s attempt %d: %s returned unparseable/illegal", m.id, attempts, model)

        if chosen is None:
            # Forced random legal move as graceful degradation
            legal = list(m.board.legal_moves)
            if not legal:
                # Board ended between our legality check and here — shouldn't happen
                return m
            chosen = random.choice(legal)
            forced = True
            log.warning("chess %s: %s could not produce legal move after %d tries — forced %s",
                        m.id, model, max_attempts, chosen.uci())

        san = m.board.san(chosen)
        uci = chosen.uci()
        m.board.push(chosen)
        ms = int((time.monotonic() - started) * 1000)
        m.moves.append(MoveRecord(
            n=len(m.moves) + 1,
            side="white" if (len(m.moves) % 2 == 0) else "black",
            san=san,
            uci=uci,
            thinking=_trim_thinking(thinking),
            forced=forced,
            attempts=attempts,
            ms=ms,
        ))
        m.last_move_at = datetime.now(timezone.utc).isoformat()
        return m


def _trim_thinking(text: str, max_len: int = 1200) -> str:
    """
    Strip the trailing 'MOVE: ...' line so the move-history UI shows only the
    reasoning. Cap length so bigger chatter doesn't bloat the response.
    """
    lines = [ln for ln in text.splitlines() if not _MOVE_PREFIX_RE.match(ln.strip())]
    cleaned = "\n".join(lines).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned
