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
    EXECUTOR_MODELS,
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


# Models that reject the `temperature` parameter outright. Anthropic
# deprecated it on the 4.5+ reasoning tier (claude-*-4-5-* and newer).
# OpenAI deprecated it on the o-series + GPT-5 family. Prefix-match so
# both the unversioned aliases and the dated pins are caught.
def _model_rejects_temperature(model: str) -> bool:
    m = model.lower()
    if m.startswith((
        # Claude 4.5+ all deprecate temperature
        "claude-opus-4-7",
        "claude-opus-4-6", "claude-sonnet-4-6",
        "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5",
    )):
        return True
    if m.startswith(("o1-", "o3-", "o4-", "gpt-5")):
        return True
    return False


# OpenAI renamed `max_tokens` → `max_completion_tokens` on the o-series
# and GPT-5 family (they 400 with "Unsupported parameter: 'max_tokens'").
# Anthropic kept `max_tokens` on everything, so this check is OpenAI-side
# only. xAI's Grok / LM Studio / Ollama all still accept max_tokens.
def _model_uses_max_completion_tokens(model: str) -> bool:
    m = model.lower()
    return m.startswith(("o1-", "o3-", "o4-", "gpt-5"))


# Qwen3+ supports disabling its hybrid-thinking mode via extra_body —
# without this a Qwen 3/3.5 model burns most of max_tokens on internal
# reasoning_content and emits empty `content`. Saves 5-10× on latency
# and unblocks the "model reasons forever, never outputs move" case we
# saw live with qwen/qwen3.5-9b.
def _qwen3_no_think_kwargs(model: str) -> dict[str, Any]:
    m = model.lower()
    if m.startswith(("qwen3", "qwen/qwen3", "lmstudio:qwen/qwen3",
                     "lmstudio:qwen3", "qwen/qwen3.5", "qwen3.5")):
        return {"extra_body": {
            "chat_template_kwargs": {"enable_thinking": False}
        }}
    return {}


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Dollars spent for a single call, using the per-million pricing in
    forge.config.EXECUTOR_MODELS. Unknown models default to zero cost
    rather than raising — we'd rather undercount than surface errors on
    an experimental model."""
    info = EXECUTOR_MODELS.get(model)
    if not info:
        return 0.0
    ci = float(info.get("cost_in", 0) or 0)
    co = float(info.get("cost_out", 0) or 0)
    return (input_tokens * ci + output_tokens * co) / 1_000_000.0


def _llm_oneshot(prompt: str, system: str, model: str,
                 temperature: float = 0.3, max_tokens: int = 2000
                 ) -> tuple[str, dict[str, Any]]:
    """
    Call an LLM and return (text, usage) where usage is:
      { input_tokens, output_tokens, cost_usd, model }

    Default max_tokens raised from 600 → 2000 to survive reasoning-tier
    output: Qwen3/Ministral/gpt-5 happily burn 1000+ reasoning tokens
    before emitting an actual move. If the model finishes quickly on
    plain output, the unused budget costs nothing (OpenAI-style APIs
    only bill what's actually generated).

    Token counts fall back to 0 if the provider doesn't include usage
    on the response (rare but possible for local endpoints).
    """
    provider = _provider_for(model)
    skip_temp = _model_rejects_temperature(model)
    qwen_kwargs = _qwen3_no_think_kwargs(model)
    usage: dict[str, Any] = {
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model": model,
    }

    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        # Anthropic keeps max_tokens on every model (no rename), so no
        # conditional here — only temperature gets dropped on 4.5+.
        kwargs: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if not skip_temp:
            kwargs["temperature"] = min(temperature, 1.0)
        resp = client.messages.create(**kwargs)
        text = resp.content[0].text
        u = getattr(resp, "usage", None)
        if u is not None:
            usage["input_tokens"] = int(getattr(u, "input_tokens", 0) or 0)
            usage["output_tokens"] = int(getattr(u, "output_tokens", 0) or 0)
        usage["cost_usd"] = _compute_cost(model, usage["input_tokens"], usage["output_tokens"])
        return text, usage

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
    call_kwargs: dict[str, Any] = {
        "model": model, "messages": messages,
    }
    # gpt-5 / o-series require max_completion_tokens; everything else
    # (gpt-4o, xAI Grok, LM Studio, Ollama) still takes max_tokens.
    if _model_uses_max_completion_tokens(model):
        call_kwargs["max_completion_tokens"] = max_tokens
    else:
        call_kwargs["max_tokens"] = max_tokens
    if not skip_temp:
        call_kwargs["temperature"] = temperature
    # Qwen3 + some other hybrid-thinking models accept this knob to
    # force direct output. Unknown-kwarg servers (vanilla Grok / OpenAI)
    # ignore extra_body, so passing it is always safe.
    if qwen_kwargs:
        call_kwargs.update(qwen_kwargs)
    resp = client.chat.completions.create(**call_kwargs)
    # Primary text path. If content is empty (reasoning model hit its
    # token budget before emitting), salvage the move from
    # reasoning_content — players often think "I will play e2e4" out
    # loud and the extractor finds that inside the reasoning.
    msg = resp.choices[0].message
    text = msg.content or ""
    if not text:
        reasoning = getattr(msg, "reasoning_content", "") or ""
        if reasoning:
            text = reasoning
            log.info("chess: content empty, parsed from reasoning_content (%s, %d chars)",
                     model, len(reasoning))
    u = getattr(resp, "usage", None)
    if u is not None:
        usage["input_tokens"] = int(getattr(u, "prompt_tokens", 0) or 0)
        usage["output_tokens"] = int(getattr(u, "completion_tokens", 0) or 0)
    usage["cost_usd"] = _compute_cost(model, usage["input_tokens"], usage["output_tokens"])
    return text, usage


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
    # Token accounting — summed across all retry attempts for this move.
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CommentaryRecord:
    """A single spoken commentary line from the judge. Emitted every Nth
    full-move pair so TTS has something meaty to chew on without
    yammering after every half-ply."""
    after_move_n: int      # half-move count AT time of emission (even = black just moved)
    round_num: int         # 1-indexed full-move pair number
    text: str              # what the judge said
    model: str
    ms: int                # wall-clock for the judge call
    emitted_at: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CapturedPiece:
    """A piece removed from the board. `by` is the side that did the
    capturing, `piece_symbol` is python-chess's one-letter notation
    (uppercase = white piece taken, lowercase = black piece taken)."""
    by: str                # "white" | "black" — who made the capture
    piece_symbol: str      # "P", "p", "N", "n", ... (upper=white, lower=black)
    move_n: int            # which half-move this happened on
    move_san: str          # SAN of the capturing move


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

    # ── Judge (arena-style TTS commentary) ────────────────────────────
    # Fires every `commentary_interval` full-move pairs. The judge sees
    # the full move list (SAN), the current position, and who's winning;
    # returns 2-4 sentences of play-by-play. Default = every other round
    # (every 2 full moves, i.e. every 4 half-moves) to match the pattern
    # the user asked for.
    judge_model: str = "grok-4.20-0309-reasoning"
    commentary_interval: int = 2             # full-move pairs between commentary
    commentary: list[CommentaryRecord] = field(default_factory=list)

    # ── Captured pieces + token accounting ─────────────────────────────
    # Captures are recorded in play order so the UI can animate new takes.
    # Token counters are rolling per-role sums — per-move tokens live on
    # MoveRecord / CommentaryRecord, these are the cheap UI readouts.
    captures: list[CapturedPiece] = field(default_factory=list)
    tokens_white_in: int = 0
    tokens_white_out: int = 0
    tokens_black_in: int = 0
    tokens_black_out: int = 0
    tokens_judge_in: int = 0
    tokens_judge_out: int = 0
    cost_white_usd: float = 0.0
    cost_black_usd: float = 0.0
    cost_judge_usd: float = 0.0

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
                "input_tokens": mv.input_tokens,
                "output_tokens": mv.output_tokens,
                "cost_usd": round(mv.cost_usd, 6),
            }
            for mv in m.moves
        ],
        "judge_model": m.judge_model,
        "commentary_interval": m.commentary_interval,
        "commentary": [
            {
                "after_move_n": c.after_move_n,
                "round_num": c.round_num,
                "text": c.text,
                "model": c.model,
                "ms": c.ms,
                "emitted_at": c.emitted_at,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "cost_usd": round(c.cost_usd, 6),
            }
            for c in m.commentary
        ],
        "captures": [
            {
                "by": cap.by,
                "piece_symbol": cap.piece_symbol,
                "move_n": cap.move_n,
                "move_san": cap.move_san,
            }
            for cap in m.captures
        ],
        "tokens": {
            "white":  {"in": m.tokens_white_in,  "out": m.tokens_white_out,
                       "cost_usd": round(m.cost_white_usd, 6)},
            "black":  {"in": m.tokens_black_in,  "out": m.tokens_black_out,
                       "cost_usd": round(m.cost_black_usd, 6)},
            "judge":  {"in": m.tokens_judge_in,  "out": m.tokens_judge_out,
                       "cost_usd": round(m.cost_judge_usd, 6)},
            "total_in":  m.tokens_white_in + m.tokens_black_in + m.tokens_judge_in,
            "total_out": m.tokens_white_out + m.tokens_black_out + m.tokens_judge_out,
            "total_cost_usd": round(
                m.cost_white_usd + m.cost_black_usd + m.cost_judge_usd, 6),
        },
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


def new_match(
    white_model: str,
    black_model: str,
    starting_fen: str = "",
    judge_model: str = "grok-4.20-0309-reasoning",
    commentary_interval: int = 2,
) -> ChessMatch:
    with _REGISTRY_LOCK:
        board = chess.Board(starting_fen) if starting_fen else chess.Board()
        m = ChessMatch(
            id=uuid.uuid4().hex[:12],
            white_model=white_model,
            black_model=black_model,
            board=board,
            judge_model=judge_model,
            commentary_interval=max(1, int(commentary_interval)),
        )
        _MATCHES[m.id] = m
        _evict_if_needed()
        log.info("chess match created: %s (%s vs %s, judge=%s every %d rounds)",
                 m.id, white_model, black_model, judge_model, m.commentary_interval)
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


def _detect_capture(board: chess.Board, move: chess.Move) -> Optional[chess.Piece]:
    """Return the piece that will be captured by `move`, or None. Handles
    en passant correctly (victim is NOT on the destination square in that
    case — it's on the same file one rank behind)."""
    if not board.is_capture(move):
        return None
    if board.is_en_passant(move):
        # Victim pawn sits on the same file as the destination but on the
        # capturer's starting rank. turn == WHITE means a white pawn is
        # capturing, so the victim is one rank SOUTH of the destination.
        file = chess.square_file(move.to_square)
        rank = chess.square_rank(move.to_square)
        victim_rank = rank - 1 if board.turn == chess.WHITE else rank + 1
        return board.piece_at(chess.square(file, victim_rank))
    return board.piece_at(move.to_square)


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
        mover_side = m.turn  # capture BEFORE the push so we know whose move this is
        chosen: Optional[chess.Move] = None
        thinking = ""
        last_error = ""
        attempts = 0
        forced = False
        # Token accounting is summed across retry attempts — an illegal-
        # move retry is still billable, so we don't throw those numbers
        # away when we fall through to another attempt.
        total_in, total_out, total_cost = 0, 0, 0.0

        for attempts in range(1, max_attempts + 1):
            try:
                reply, usage = _llm_oneshot(
                    prompt=_build_prompt(m, last_error=last_error),
                    system=_SYSTEM_PROMPT,
                    model=model,
                )
            except Exception as e:
                raise RuntimeError(f"LLM call failed for {model}: {type(e).__name__}: {e}")

            total_in += usage["input_tokens"]
            total_out += usage["output_tokens"]
            total_cost += usage["cost_usd"]

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

        # Capture detection has to happen BEFORE pushing — once we push,
        # the victim is gone from the board and we can't identify it.
        victim = _detect_capture(m.board, chosen)
        san = m.board.san(chosen)
        uci = chosen.uci()
        m.board.push(chosen)

        if victim is not None:
            m.captures.append(CapturedPiece(
                by=mover_side,
                piece_symbol=victim.symbol(),
                move_n=len(m.moves) + 1,
                move_san=san,
            ))

        ms = int((time.monotonic() - started) * 1000)
        m.moves.append(MoveRecord(
            n=len(m.moves) + 1,
            side=mover_side,
            san=san,
            uci=uci,
            thinking=_trim_thinking(thinking),
            forced=forced,
            attempts=attempts,
            ms=ms,
            input_tokens=total_in,
            output_tokens=total_out,
            cost_usd=total_cost,
        ))
        # Update rolling per-side totals for the UI.
        if mover_side == "white":
            m.tokens_white_in += total_in
            m.tokens_white_out += total_out
            m.cost_white_usd += total_cost
        else:
            m.tokens_black_in += total_in
            m.tokens_black_out += total_out
            m.cost_black_usd += total_cost

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


# ────────────────────────────────────────────────────────────────────────
# Judge / TTS commentary
# ────────────────────────────────────────────────────────────────────────
# The judge watches the match and narrates every Nth round. Tuned for
# speech: short, punchy, 2-4 sentences. Arena-style play-by-play —
# name the move, its tactical purpose, and build a little tension.

_JUDGE_SYSTEM = (
    "You are a chess commentator for a live audience. Two AI models are "
    "playing each other. Your job: produce crisp, broadcast-ready "
    "commentary on the most recent pair of moves. Rules:\n"
    " • 2–4 sentences. NO more. This is spoken aloud via TTS.\n"
    " • Name the moves by their SAN (e.g. 'Nf3', 'O-O', 'Qxd5'). Briefly "
    "   say what each move does (develop, attack, defend, trade, threaten).\n"
    " • Pick a side to frame as 'ahead' or 'pressing' if the position "
    "   clearly favors one — don't hedge.\n"
    " • No bullet points. No markdown. No emojis. Pure prose, like a radio "
    "   caller. End with a tiny cliffhanger when you can.\n"
    " • NEVER say 'as an AI' or 'I cannot'. You're the voice of the arena."
)


def _should_emit_commentary(m: ChessMatch) -> bool:
    """True when a commentary beat should fire after the just-applied move.

    Fires on black's move (so a full pair has completed) every
    `commentary_interval` full-move pairs. Also fires on the final move
    if the game just ended, regardless of interval.
    """
    n = len(m.moves)
    if n == 0:
        return False
    # Fire on game-over beats always (unless already fired for this n).
    just_ended = m.is_over and (not m.commentary or m.commentary[-1].after_move_n != n)
    if just_ended:
        return True
    # Only after a black move (completed pair)
    if n % 2 != 0:
        return False
    full_pair_n = n // 2
    return full_pair_n > 0 and full_pair_n % m.commentary_interval == 0


def _build_judge_prompt(m: ChessMatch) -> str:
    """Build the judge prompt with the full move history so it can reason
    about momentum, not just the latest ply."""
    # SAN history grouped by full move (e.g. "1. e4 e5  2. Nf3 Nc6")
    history_tokens: list[str] = []
    for rec in m.moves:
        if rec.side == "white":
            full_n = (rec.n + 1) // 2
            history_tokens.append(f"{full_n}. {rec.san}")
        else:
            history_tokens.append(rec.san)
    history_str = " ".join(history_tokens) if history_tokens else "(no moves yet)"

    # Last pair for focus
    last_pair: list[str] = []
    if len(m.moves) >= 2:
        w, b = m.moves[-2], m.moves[-1]
        last_pair = [f"White: {w.san}", f"Black: {b.san}"]
    elif len(m.moves) == 1:
        only = m.moves[-1]
        last_pair = [f"{only.side.capitalize()}: {only.san}"]

    # End-of-game framing
    outcome_line = ""
    if m.is_over:
        if m.status == "white_wins":
            outcome_line = f"GAME OVER — White wins ({m.end_reason or ''})."
        elif m.status == "black_wins":
            outcome_line = f"GAME OVER — Black wins ({m.end_reason or ''})."
        elif m.status == "draw":
            outcome_line = f"GAME OVER — Draw ({m.end_reason or ''})."

    check_line = "Black is in check." if m.board.is_check() and m.turn == "black" else (
        "White is in check." if m.board.is_check() and m.turn == "white" else ""
    )

    lines = [
        f"Match: {m.white_model} (White) vs {m.black_model} (Black).",
        f"After move {len(m.moves)} ({(len(m.moves)+1)//2} full pairs).",
        "",
        "Full move history (SAN):",
        history_str,
    ]
    if last_pair:
        lines.extend(["", "Most recent pair:", *last_pair])
    if check_line:
        lines.extend(["", check_line])
    if outcome_line:
        lines.extend(["", outcome_line])
    lines.extend([
        "",
        f"Position FEN: {m.board.fen()}",
        "",
        "Call the action. 2–4 sentences.",
    ])
    return "\n".join(lines)


def generate_commentary(match_id: str) -> Optional[CommentaryRecord]:
    """
    Ask the judge to narrate the match state. Returns the new
    CommentaryRecord on success; returns None only if the match is
    unknown. On any LLM-side failure (API 400, missing key, empty reply)
    raises RuntimeError — the /step route maps that to a `commentary_error`
    field in its response so the UI surfaces the actual cause rather than
    silently skipping beats.
    """
    m = _MATCHES.get(match_id)
    if m is None:
        return None

    # Multi-agent grok models use a different API shape (`agent_count`
    # kwarg via xai_sdk) — calling them through OpenAI-compat fails in
    # ways that aren't useful to retry. Refuse up front with a clear
    # message rather than eat a cryptic upstream 4xx.
    if "multi-agent" in m.judge_model.lower():
        raise RuntimeError(
            f"{m.judge_model} is a planner-only multi-agent model and "
            f"can't be used as a judge. Pick a single-agent model "
            f"(Grok 4.20 Reasoning, Claude 4.7, GPT-5.4, etc.)."
        )

    started = time.monotonic()
    try:
        reply, usage = _llm_oneshot(
            prompt=_build_judge_prompt(m),
            system=_JUDGE_SYSTEM,
            model=m.judge_model,
            temperature=0.7,
            max_tokens=300,
        )
    except Exception as e:
        log.warning("chess %s: judge call failed: %s", m.id, e)
        raise RuntimeError(
            f"Judge call failed ({m.judge_model}): {type(e).__name__}: {e}"
        ) from e

    text = (reply or "").strip()
    if not text:
        raise RuntimeError(
            f"Judge ({m.judge_model}) returned an empty reply."
        )

    record = CommentaryRecord(
        after_move_n=len(m.moves),
        round_num=(len(m.moves) + 1) // 2,
        text=text,
        model=m.judge_model,
        ms=int((time.monotonic() - started) * 1000),
        emitted_at=datetime.now(timezone.utc).isoformat(),
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cost_usd=usage["cost_usd"],
    )
    with m.lock:
        m.commentary.append(record)
        m.tokens_judge_in += usage["input_tokens"]
        m.tokens_judge_out += usage["output_tokens"]
        m.cost_judge_usd += usage["cost_usd"]
    log.info("chess %s: judge commentary fired (round %d, %dms, %d→%d toks, $%.4f)",
             m.id, record.round_num, record.ms,
             usage["input_tokens"], usage["output_tokens"], usage["cost_usd"])
    return record


def maybe_generate_commentary(match_id: str) -> Optional[CommentaryRecord]:
    """Fire commentary if it's the right beat; otherwise return None.
    Called by the /step route after a move is applied."""
    m = _MATCHES.get(match_id)
    if m is None:
        return None
    if not _should_emit_commentary(m):
        return None
    return generate_commentary(match_id)
