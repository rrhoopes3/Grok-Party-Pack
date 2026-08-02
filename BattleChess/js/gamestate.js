/**
 * gamestate.js — FEN parsing, position diffing, and the observable game-state
 * holder for BattleChess.
 *
 * CONTRACT (section 6): the FEN is truth. Everything here works from the FEN
 * placement field alone; SAN/UCI from the server's `moves[]` are only ever used
 * to *disambiguate*, never as the source of truth. A desync self-heals by
 * snapping to the authoritative FEN.
 *
 * Public API
 * ----------
 *   parseFEN(fen)                    -> Position
 *   positionToFEN(position)          -> string
 *   applyUCI(position, uci)          -> Position           (pure; no engine, no legality check)
 *   diffPositions(prev, next, meta)  -> MoveEvent[]
 *   createGameState()                -> GameState
 *   selfTest({ demoGame } = {})      -> { ok, passed, failed, failures }
 *
 * Types
 * -----
 *   Piece    = { type: 'p'|'n'|'b'|'r'|'q'|'k', color: 'white'|'black' }
 *   Position = {
 *     board: { [square: string]: Piece },   // only occupied squares
 *     turn, castling, enPassant, halfmove, fullmove, placement, fen
 *   }
 *   MoveEvent = {
 *     kind: 'move'|'capture'|'castle'|'enpassant'|'promotion',
 *     from, to,                       // king squares for a castle
 *     piece: Piece,                   // the piece that *departed* (pre-promotion)
 *     captured: Piece|null,
 *     capturedSquare: string|null,    // != `to` for en passant
 *     extra: { rookFrom, rookTo }|null,
 *     promotedTo: 'q'|'r'|'b'|'n'|null,
 *     side, san, uci, n, source,      // enrichment from the server move record
 *     ambiguous: boolean              // true when the diff spanned >1 ply
 *   }
 *
 * A promotion that also captures is reported as kind 'promotion' with a
 * non-null `captured` — the kind names the *most specific* transform, and the
 * capture ride-along tells the animator to still play the impact beat.
 */

export const FILE_LETTERS = 'abcdefgh';
export const PIECE_TYPES = ['p', 'n', 'b', 'r', 'q', 'k'];

/** All 64 square names, a1 .. h8. */
export const ALL_SQUARES = (() => {
  const out = [];
  for (let r = 0; r < 8; r++) {
    for (let f = 0; f < 8; f++) out.push(FILE_LETTERS[f] + (r + 1));
  }
  return out;
})();

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

// ────────────────────────────────────────────────────────────────────────
// Square helpers (algebraic only — world-space math lives in board.js)
// ────────────────────────────────────────────────────────────────────────

/** 0-based file/rank -> 'e4'. */
export function squareFromFileRank(file, rank) {
  return FILE_LETTERS[file] + (rank + 1);
}

/** 'e4' -> { file: 4, rank: 3 } (0-based), or null if malformed. */
export function fileRankFromSquare(square) {
  if (typeof square !== 'string' || square.length !== 2) return null;
  const file = FILE_LETTERS.indexOf(square[0]);
  const rank = square.charCodeAt(1) - 49; // '1' -> 0
  if (file < 0 || rank < 0 || rank > 7) return null;
  return { file, rank };
}

function normalizeSide(side) {
  if (typeof side !== 'string') return null;
  const s = side.trim().toLowerCase();
  if (s === 'white' || s === 'w') return 'white';
  if (s === 'black' || s === 'b') return 'black';
  return null;
}

function otherSide(side) {
  return side === 'white' ? 'black' : 'white';
}

// ────────────────────────────────────────────────────────────────────────
// FEN
// ────────────────────────────────────────────────────────────────────────

/**
 * Parse a FEN into a Position. Throws a SyntaxError on malformed input —
 * callers that receive FENs from the network should guard (createGameState
 * does).
 */
export function parseFEN(fen) {
  if (typeof fen !== 'string' || !fen.trim()) {
    throw new SyntaxError(`parseFEN: expected a FEN string, got ${JSON.stringify(fen)}`);
  }
  const parts = fen.trim().split(/\s+/);
  const placement = parts[0];
  const ranks = placement.split('/');
  if (ranks.length !== 8) {
    throw new SyntaxError(`parseFEN: expected 8 ranks, got ${ranks.length} in "${fen}"`);
  }

  const board = {};
  for (let i = 0; i < 8; i++) {
    const rankIdx = 7 - i; // FEN lists rank 8 first
    let file = 0;
    for (const ch of ranks[i]) {
      if (ch >= '1' && ch <= '8') {
        file += ch.charCodeAt(0) - 48;
        continue;
      }
      const lower = ch.toLowerCase();
      if (!PIECE_TYPES.includes(lower)) {
        throw new SyntaxError(`parseFEN: bad piece "${ch}" in "${fen}"`);
      }
      if (file > 7) {
        throw new SyntaxError(`parseFEN: rank ${8 - i} overflows in "${fen}"`);
      }
      board[squareFromFileRank(file, rankIdx)] = {
        type: lower,
        color: ch === lower ? 'black' : 'white',
      };
      file++;
    }
    if (file !== 8) {
      throw new SyntaxError(`parseFEN: rank ${8 - i} covers ${file} files in "${fen}"`);
    }
  }

  const halfmove = Number.parseInt(parts[4], 10);
  const fullmove = Number.parseInt(parts[5], 10);

  return {
    board,
    turn: parts[1] === 'b' ? 'black' : 'white',
    castling: parts[2] || '-',
    enPassant: parts[3] && parts[3] !== '-' ? parts[3] : null,
    halfmove: Number.isFinite(halfmove) ? halfmove : 0,
    fullmove: Number.isFinite(fullmove) ? fullmove : 1,
    placement,
    fen: fen.trim(),
  };
}

/** Board map -> canonical FEN piece-placement field. */
export function boardToPlacement(board) {
  const rows = [];
  for (let rank = 7; rank >= 0; rank--) {
    let row = '';
    let empty = 0;
    for (let file = 0; file < 8; file++) {
      const piece = board[squareFromFileRank(file, rank)];
      if (!piece) {
        empty++;
        continue;
      }
      if (empty) {
        row += String(empty);
        empty = 0;
      }
      row += piece.color === 'white' ? piece.type.toUpperCase() : piece.type;
    }
    if (empty) row += String(empty);
    rows.push(row);
  }
  return rows.join('/');
}

/** Position -> full FEN string. */
export function positionToFEN(position) {
  const placement = boardToPlacement(position.board);
  return [
    placement,
    position.turn === 'black' ? 'b' : 'w',
    position.castling || '-',
    position.enPassant || '-',
    String(position.halfmove ?? 0),
    String(position.fullmove ?? 1),
  ].join(' ');
}

/** Accept a Position or a raw FEN string; always return a Position. */
function coerce(input, label) {
  if (typeof input === 'string') return parseFEN(input);
  if (input && typeof input === 'object' && input.board) {
    if (typeof input.placement !== 'string') {
      return { ...input, placement: boardToPlacement(input.board) };
    }
    return input;
  }
  throw new TypeError(`${label || 'gamestate'}: expected a FEN string or parsed position`);
}

/** Fresh starting position. */
export function startPosition() {
  return parseFEN(START_FEN);
}

// ────────────────────────────────────────────────────────────────────────
// Move notation helpers
// ────────────────────────────────────────────────────────────────────────

const UCI_RE = /^([a-h][1-8])([a-h][1-8])([qrbnQRBN])?$/;

/** 'e7e8q' -> { from:'e7', to:'e8', promotion:'q' }, or null. */
export function parseUCI(uci) {
  if (typeof uci !== 'string') return null;
  const m = UCI_RE.exec(uci.trim().toLowerCase());
  if (!m) return null;
  return { from: m[1], to: m[2], promotion: m[3] ? m[3].toLowerCase() : null };
}

const SAN_RE = /^([NBRQK])?([a-h])?([1-8])?(x)?([a-h][1-8])(?:=([NBRQK]))?/;

/**
 * Parse SAN into disambiguation hints. Returns null for castling (structural
 * detection handles that) and for anything unparseable.
 */
export function parseSAN(san) {
  if (typeof san !== 'string') return null;
  const clean = san.trim().replace(/[+#!?]+$/, '');
  if (!clean || /^0-0|^O-O/i.test(clean)) return null;
  const m = SAN_RE.exec(clean);
  if (!m) return null;
  return {
    pieceType: m[1] ? m[1].toLowerCase() : 'p',
    fromFile: m[2] || null,
    fromRank: m[3] || null,
    capture: !!m[4],
    to: m[5],
    promotion: m[6] ? m[6].toLowerCase() : null,
  };
}

// ────────────────────────────────────────────────────────────────────────
// Pure move application (no legality checking — the server already did that)
// ────────────────────────────────────────────────────────────────────────

const ROOK_RIGHTS = { a1: 'Q', h1: 'K', a8: 'q', h8: 'k' };

/**
 * Apply a UCI move to a Position and return a NEW Position. Handles castling
 * (king moves 2 files -> rook teleports), en passant (victim behind the
 * destination) and promotion. Used to walk forward ply-by-ply when a poll
 * skipped moves; the result is always verified against the server FEN.
 */
export function applyUCI(position, uci) {
  const pos = coerce(position, 'applyUCI');
  const mv = parseUCI(uci);
  if (!mv) throw new SyntaxError(`applyUCI: bad UCI ${JSON.stringify(uci)}`);

  const piece = pos.board[mv.from];
  if (!piece) throw new Error(`applyUCI: no piece on ${mv.from} in "${pos.placement}"`);

  const mover = piece.color;
  const from = fileRankFromSquare(mv.from);
  const to = fileRankFromSquare(mv.to);
  const board = { ...pos.board };

  // Capture resolution (en passant victim is not on the destination square).
  let capturedSquare = null;
  const occupant = board[mv.to];
  if (occupant && occupant.color !== mover) {
    capturedSquare = mv.to;
  } else if (piece.type === 'p' && from.file !== to.file && !occupant) {
    capturedSquare = squareFromFileRank(to.file, from.rank);
  }
  if (capturedSquare) delete board[capturedSquare];

  delete board[mv.from];
  board[mv.to] = { type: mv.promotion || piece.type, color: mover };

  // Castling: relocate the rook.
  if (piece.type === 'k' && Math.abs(to.file - from.file) === 2) {
    const kingside = to.file > from.file;
    const rookFrom = squareFromFileRank(kingside ? 7 : 0, from.rank);
    const rookTo = squareFromFileRank(kingside ? 5 : 3, from.rank);
    const rook = board[rookFrom] || { type: 'r', color: mover };
    delete board[rookFrom];
    board[rookTo] = rook;
  }

  // Castling rights.
  let rights = pos.castling && pos.castling !== '-' ? pos.castling : '';
  const strip = (chars) => {
    for (const c of chars) rights = rights.split(c).join('');
  };
  if (piece.type === 'k') strip(mover === 'white' ? 'KQ' : 'kq');
  if (piece.type === 'r' && ROOK_RIGHTS[mv.from]) strip(ROOK_RIGHTS[mv.from]);
  if (capturedSquare && ROOK_RIGHTS[capturedSquare]) strip(ROOK_RIGHTS[capturedSquare]);

  // En-passant target after a double pawn push. Advisory only: python-chess
  // omits it unless a capture is actually legal, so we never compare this
  // field against the server FEN — the diff detects en passant structurally.
  let enPassant = null;
  if (piece.type === 'p' && Math.abs(to.rank - from.rank) === 2) {
    enPassant = squareFromFileRank(from.file, (from.rank + to.rank) / 2);
  }

  const next = {
    board,
    turn: otherSide(mover),
    castling: rights || '-',
    enPassant,
    halfmove: piece.type === 'p' || capturedSquare ? 0 : (pos.halfmove ?? 0) + 1,
    fullmove: mover === 'black' ? (pos.fullmove ?? 1) + 1 : (pos.fullmove ?? 1),
    placement: '',
    fen: '',
  };
  next.placement = boardToPlacement(board);
  next.fen = positionToFEN(next);
  return next;
}

// ────────────────────────────────────────────────────────────────────────
// Diffing
// ────────────────────────────────────────────────────────────────────────

function makeEvent(kind, o) {
  return {
    kind,
    from: o.from,
    to: o.to,
    piece: o.piece,
    captured: o.captured || null,
    capturedSquare: o.capturedSquare || null,
    extra: o.extra || null,
    promotedTo: o.promotedTo || null,
    side: o.piece.color,
    san: o.san || null,
    uci: o.uci || null,
    n: Number.isFinite(o.n) ? o.n : null,
    source: o.source || null,
    ambiguous: !!o.ambiguous,
  };
}

function metaFields(meta) {
  if (!meta || typeof meta !== 'object') return { san: null, uci: null, n: null, source: null };
  return {
    san: typeof meta.san === 'string' ? meta.san : null,
    uci: typeof meta.uci === 'string' ? meta.uci : null,
    n: Number.isFinite(meta.n) ? meta.n : null,
    source: typeof meta.source === 'string' ? meta.source : null,
  };
}

/** Chebyshev distance between two squares — used only as a last-resort tiebreak. */
function squareDistance(a, b) {
  const A = fileRankFromSquare(a);
  const B = fileRankFromSquare(b);
  if (!A || !B) return 99;
  return Math.max(Math.abs(A.file - B.file), Math.abs(A.rank - B.rank));
}

/**
 * Diff two positions into renderable move events.
 *
 * `prev` / `next` may be Positions or raw FEN strings. `moveMeta` is an
 * optional server move record ({ san, uci, side, n, source }) used purely to
 * disambiguate; the diff is correct without it.
 *
 * Returns [] when nothing changed or the change is not representable as a
 * single ply — it never invents a capture and never drops a captured piece.
 */
export function diffPositions(prev, next, moveMeta = null) {
  const a = coerce(prev, 'diffPositions(prev)');
  const b = coerce(next, 'diffPositions(next)');
  if (a.placement === b.placement) return [];

  const removed = [];
  const added = [];
  for (const sq of ALL_SQUARES) {
    const pa = a.board[sq];
    const pb = b.board[sq];
    const same = pa && pb && pa.type === pb.type && pa.color === pb.color;
    if (pa && !same) removed.push({ square: sq, piece: pa });
    if (pb && !same) added.push({ square: sq, piece: pb });
  }
  if (added.length === 0 || removed.length === 0) return [];

  const meta = metaFields(moveMeta);
  const uci = parseUCI(meta.uci);
  const san = parseSAN(meta.san);

  // Who moved? meta wins, then the FEN's own side-to-move, then inference.
  let mover = normalizeSide(moveMeta && moveMeta.side) || a.turn || added[0].piece.color;
  if (!added.some((x) => x.piece.color === mover)) mover = otherSide(mover);
  if (!added.some((x) => x.piece.color === mover)) return [];

  const addedMover = added.filter((x) => x.piece.color === mover);
  const removedMover = removed.filter((x) => x.piece.color === mover);
  const removedEnemy = removed.filter((x) => x.piece.color !== mover);
  if (removedMover.length === 0) return [];

  let ambiguous = addedMover.length > (addedMover.some((x) => x.piece.type === 'k') ? 2 : 1)
    || removedEnemy.length > 1;

  // ── Castling: ONE event carrying both king and rook squares ────────────
  const kingArrived = addedMover.find((x) => x.piece.type === 'k');
  const kingLeft = removedMover.find((x) => x.piece.type === 'k');
  if (kingArrived && kingLeft) {
    const kf = fileRankFromSquare(kingLeft.square);
    const kt = fileRankFromSquare(kingArrived.square);
    if (kf && kt && kf.rank === kt.rank && Math.abs(kt.file - kf.file) === 2) {
      const kingside = kt.file > kf.file;
      const rookLeft = removedMover.find((x) => x.piece.type === 'r');
      const rookArrived = addedMover.find((x) => x.piece.type === 'r');
      const rookFrom = rookLeft ? rookLeft.square : squareFromFileRank(kingside ? 7 : 0, kf.rank);
      const rookTo = rookArrived ? rookArrived.square : squareFromFileRank(kingside ? 5 : 3, kf.rank);
      return [makeEvent('castle', {
        from: kingLeft.square,
        to: kingArrived.square,
        piece: kingLeft.piece,
        captured: null,
        capturedSquare: null,
        extra: { rookFrom, rookTo, side: kingside ? 'kingside' : 'queenside' },
        ...meta,
        ambiguous: removedEnemy.length > 0 || ambiguous,
      })];
    }
  }

  // ── Destination ────────────────────────────────────────────────────────
  let to = null;
  if (uci && b.board[uci.to] && b.board[uci.to].color === mover
      && addedMover.some((x) => x.square === uci.to)) {
    to = uci.to;
  }
  if (!to && san && addedMover.some((x) => x.square === san.to)) to = san.to;
  if (!to) {
    to = addedMover[0].square;
    if (addedMover.length > 1) ambiguous = true;
  }
  const arriving = b.board[to];
  if (!arriving) return [];

  // ── Origin ─────────────────────────────────────────────────────────────
  let fromEntry = null;
  if (uci) fromEntry = removedMover.find((x) => x.square === uci.from) || null;
  if (!fromEntry && san) {
    let cands = removedMover.filter((x) => x.piece.type === san.pieceType);
    if (san.fromFile) cands = cands.filter((x) => x.square[0] === san.fromFile);
    if (san.fromRank) cands = cands.filter((x) => x.square[1] === san.fromRank);
    if (cands.length === 1) fromEntry = cands[0];
  }
  if (!fromEntry) {
    // Same piece type as the piece that arrived — the common case.
    const sameType = removedMover.filter((x) => x.piece.type === arriving.type);
    if (sameType.length === 1) {
      fromEntry = sameType[0];
    } else if (sameType.length > 1) {
      ambiguous = true;
      fromEntry = sameType
        .slice()
        .sort((x, y) => squareDistance(x.square, to) - squareDistance(y.square, to))[0];
    } else {
      // Type changed => promotion. The origin is a pawn one rank behind `to`,
      // on the same file (quiet) or an adjacent file (capture-promotion).
      const t = fileRankFromSquare(to);
      const dir = mover === 'white' ? -1 : 1;
      const pawns = removedMover.filter((x) => x.piece.type === 'p');
      const plausible = pawns.filter((x) => {
        const p = fileRankFromSquare(x.square);
        return p && p.rank === t.rank + dir && Math.abs(p.file - t.file) <= 1;
      });
      if (plausible.length >= 1) {
        if (plausible.length > 1) ambiguous = true;
        fromEntry = plausible.find((x) => fileRankFromSquare(x.square).file === t.file)
          || plausible[0];
      } else if (pawns.length) {
        ambiguous = true;
        fromEntry = pawns[0];
      }
    }
  }
  if (!fromEntry) {
    if (removedMover.length !== 1) return [];
    fromEntry = removedMover[0];
  }
  const from = fromEntry.square;

  // ── Capture resolution (never phantom, never lost) ─────────────────────
  let captured = null;
  let capturedSquare = null;
  if (removedEnemy.length) {
    const atTo = removedEnemy.find((x) => x.square === to);
    if (atTo) {
      captured = atTo.piece;
      capturedSquare = atTo.square;
    } else {
      // En passant: the victim sits on the destination's FILE and the
      // origin's RANK.
      const f = fileRankFromSquare(from);
      const t = fileRankFromSquare(to);
      const epSquare = f && t ? squareFromFileRank(t.file, f.rank) : null;
      const epHit = epSquare ? removedEnemy.find((x) => x.square === epSquare) : null;
      if (epHit) {
        captured = epHit.piece;
        capturedSquare = epHit.square;
      } else {
        // Multi-ply gap or an unexpected board edit — report the removal
        // rather than silently losing the piece, and flag it.
        captured = removedEnemy[0].piece;
        capturedSquare = removedEnemy[0].square;
        ambiguous = true;
      }
    }
  }

  // ── Kind ───────────────────────────────────────────────────────────────
  const promoted = fromEntry.piece.type === 'p' && arriving.type !== 'p';
  let kind = 'move';
  if (promoted) kind = 'promotion';
  else if (captured && capturedSquare !== to) kind = 'enpassant';
  else if (captured) kind = 'capture';

  return [makeEvent(kind, {
    from,
    to,
    piece: fromEntry.piece,
    captured,
    capturedSquare,
    extra: null,
    promotedTo: promoted ? arriving.type : null,
    ...meta,
    ambiguous,
  })];
}

// ────────────────────────────────────────────────────────────────────────
// Observable game state
// ────────────────────────────────────────────────────────────────────────

/**
 * Events emitted by createGameState():
 *   'reset'    ()                       — state cleared / new match adopted
 *   'match'    (payload)                — a payload was ingested (always)
 *   'position' (position, payload)      — the board changed
 *   'events'   (events[], payload)      — the diffed ply events (may be empty)
 *   'move'     (event, payload)         — once per diffed event
 *   'desync'   ({ expected, actual, payload }) — replay disagreed with the FEN
 *   'error'    ({ error, payload })     — bad payload / unparseable FEN
 */
export function createGameState() {
  const listeners = new Map();
  let position = null;
  let lastEvents = [];
  let match = null;
  let matchId = null;
  let pliesSeen = 0;

  function emit(evt, ...args) {
    const set = listeners.get(evt);
    if (!set) return;
    for (const cb of Array.from(set)) {
      try {
        cb(...args);
      } catch (err) {
        // A listener blowing up must never take the frame loop with it.
        console.error(`[gamestate] listener for "${evt}" threw:`, err);
      }
    }
  }

  function on(evt, cb) {
    if (typeof cb !== 'function') return () => {};
    if (!listeners.has(evt)) listeners.set(evt, new Set());
    listeners.get(evt).add(cb);
    return () => off(evt, cb);
  }

  function off(evt, cb) {
    const set = listeners.get(evt);
    if (set) set.delete(cb);
  }

  function reset() {
    position = null;
    lastEvents = [];
    match = null;
    matchId = null;
    pliesSeen = 0;
    emit('reset');
  }

  /** Adopt `next` wholesale with no animation (first payload, match switch, desync). */
  function snap(next, payload, events) {
    position = next;
    lastEvents = events || [];
    match = payload;
    matchId = payload && payload.id != null ? payload.id : matchId;
    pliesSeen = Number.isFinite(payload && payload.halfmove_count)
      ? payload.halfmove_count
      : pliesSeen;
    emit('match', payload);
    emit('position', position, payload);
    emit('events', lastEvents, payload);
    for (const ev of lastEvents) emit('move', ev, payload);
    return lastEvents;
  }

  /**
   * Ingest a match payload from api.js. Returns the events to animate.
   *
   * Walks forward one ply at a time using `moves[].uci` when the payload
   * jumped more than a single ply (a slow poll, or a page that joined mid
   * game), then verifies the replay against the payload's FEN. The FEN always
   * wins: on any disagreement we snap and emit 'desync'.
   */
  function applyMatch(payload) {
    if (!payload || typeof payload !== 'object' || typeof payload.fen !== 'string') {
      emit('error', { error: 'applyMatch: payload has no fen', payload });
      return [];
    }

    let next;
    try {
      next = parseFEN(payload.fen);
    } catch (err) {
      emit('error', { error: `applyMatch: ${err.message}`, payload });
      return [];
    }

    const incomingId = payload.id != null ? payload.id : matchId;
    const isNewMatch = position === null || incomingId !== matchId;
    if (isNewMatch) {
      if (position !== null) reset();
      return snap(next, payload, []);
    }

    const targetPlies = Number.isFinite(payload.halfmove_count)
      ? payload.halfmove_count
      : pliesSeen + 1;
    const delta = targetPlies - pliesSeen;

    if (next.placement === position.placement && delta <= 0) {
      // Nothing moved, but commentary / tokens / status may have changed.
      match = payload;
      pliesSeen = targetPlies > 0 ? targetPlies : pliesSeen;
      emit('match', payload);
      return [];
    }

    if (delta <= 0) {
      // Board changed but the ply counter went backwards (takeback / rebuild).
      return snap(next, payload, []);
    }

    const moves = Array.isArray(payload.moves) ? payload.moves : null;
    const metas = moves ? moves.slice(Math.max(0, moves.length - delta)) : null;

    let cur = position;
    let events = [];
    let replayed = false;

    if (metas && metas.length) {
      replayed = true;
      for (const mv of metas) {
        // Protocol-forfeit plies carry no UCI and change nothing on the board.
        if (!mv || typeof mv.uci !== 'string' || !parseUCI(mv.uci)) continue;
        let after;
        try {
          after = applyUCI(cur, mv.uci);
        } catch (err) {
          replayed = false;
          break;
        }
        events = events.concat(diffPositions(cur, after, mv));
        cur = after;
      }
    }

    if (!replayed || cur.placement !== next.placement) {
      if (replayed && cur.placement !== next.placement) {
        emit('desync', { expected: next.placement, actual: cur.placement, payload });
      }
      // Fall back to a straight two-position diff for a single ply; for a
      // multi-ply gap we cannot narrate it honestly, so we just snap.
      const fallbackMeta = moves && moves.length ? moves[moves.length - 1] : null;
      events = delta === 1 ? diffPositions(position, next, fallbackMeta) : [];
      return snap(next, payload, events);
    }

    // Replay agreed with the FEN — adopt the authoritative parse anyway so
    // castling rights / clocks come from the server, not from our simulation.
    return snap(next, payload, events);
  }

  const api = {
    applyMatch,
    on,
    off,
    reset,
  };
  Object.defineProperties(api, {
    position: { get: () => position, enumerable: true },
    events: { get: () => lastEvents, enumerable: true },
    match: { get: () => match, enumerable: true },
    matchId: { get: () => matchId, enumerable: true },
    pliesSeen: { get: () => pliesSeen, enumerable: true },
  });
  return api;
}

// ────────────────────────────────────────────────────────────────────────
// Self test
// ────────────────────────────────────────────────────────────────────────

/**
 * Runs the diffing edge cases that break naive implementations against known
 * FEN pairs (generated with python-chess). Pass `{ demoGame }` — the parsed
 * assets/demo-game.json — to also replay the full Opera Game through
 * createGameState().
 *
 * Returns { ok, passed, failed, failures }.
 */
export function selfTest({ demoGame = null, log = true } = {}) {
  const failures = [];
  let passed = 0;

  const out = (msg) => {
    if (log) console.log(msg);
  };

  function check(name, cond, detail) {
    if (cond) {
      passed++;
      out(`  ok   ${name}`);
    } else {
      failures.push({ name, detail });
      out(`  FAIL ${name}${detail ? ` — ${detail}` : ''}`);
    }
  }

  function one(prev, nextFen, meta) {
    const evs = diffPositions(prev, nextFen, meta);
    return evs.length === 1 ? evs[0] : null;
  }

  // Each case is run twice: with the server move record, and with NO metadata
  // at all (FEN-only diffing must stand on its own).
  const cases = [
    {
      name: 'simple move',
      prev: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      next: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
      meta: { n: 1, side: 'white', san: 'e4', uci: 'e2e4' },
      expect: { kind: 'move', from: 'e2', to: 'e4', pieceType: 'p', captured: null },
    },
    {
      name: 'capture',
      prev: 'rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2',
      next: 'rnbqkbnr/ppp1pppp/8/3P4/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2',
      meta: { n: 3, side: 'white', san: 'exd5', uci: 'e4d5' },
      expect: {
        kind: 'capture', from: 'e4', to: 'd5', pieceType: 'p',
        captured: 'p', capturedColor: 'black', capturedSquare: 'd5',
      },
    },
    {
      name: 'white kingside castle',
      prev: 'r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 0 1',
      next: 'r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQ1RK1 b kq - 1 1',
      meta: { n: 9, side: 'white', san: 'O-O', uci: 'e1g1' },
      expect: {
        kind: 'castle', from: 'e1', to: 'g1', pieceType: 'k', captured: null,
        rookFrom: 'h1', rookTo: 'f1',
      },
    },
    {
      name: 'black queenside castle',
      prev: 'r3kbnr/pppqpppp/2npb3/8/8/2NPB3/PPPQPPPP/R3KBNR b KQkq - 0 1',
      next: '2kr1bnr/pppqpppp/2npb3/8/8/2NPB3/PPPQPPPP/R3KBNR w KQ - 1 2',
      meta: { n: 10, side: 'black', san: 'O-O-O', uci: 'e8c8' },
      expect: {
        kind: 'castle', from: 'e8', to: 'c8', pieceType: 'k', captured: null,
        rookFrom: 'a8', rookTo: 'd8',
      },
    },
    {
      name: 'en passant',
      prev: 'rnbqkbnr/ppp2ppp/4p3/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3',
      next: 'rnbqkbnr/ppp2ppp/3Pp3/8/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 3',
      meta: { n: 5, side: 'white', san: 'exd6', uci: 'e5d6' },
      expect: {
        kind: 'enpassant', from: 'e5', to: 'd6', pieceType: 'p',
        captured: 'p', capturedColor: 'black', capturedSquare: 'd5',
      },
    },
    {
      name: 'quiet promotion',
      prev: '8/3P4/8/8/8/8/4k3/K7 w - - 0 1',
      next: '3Q4/8/8/8/8/8/4k3/K7 b - - 0 1',
      meta: { n: 61, side: 'white', san: 'd8=Q', uci: 'd7d8q' },
      expect: {
        kind: 'promotion', from: 'd7', to: 'd8', pieceType: 'p',
        captured: null, promotedTo: 'q',
      },
    },
    {
      name: 'capture promotion',
      prev: 'r6k/1P6/8/8/8/8/4K3/8 w - - 0 1',
      next: 'Q6k/8/8/8/8/8/4K3/8 b - - 0 1',
      meta: { n: 71, side: 'white', san: 'bxa8=Q+', uci: 'b7a8q' },
      expect: {
        kind: 'promotion', from: 'b7', to: 'a8', pieceType: 'p',
        captured: 'r', capturedColor: 'black', capturedSquare: 'a8',
        promotedTo: 'q',
      },
    },
  ];

  out('gamestate.selfTest — diffPositions edge cases');
  for (const c of cases) {
    for (const withMeta of [true, false]) {
      const label = `${c.name}${withMeta ? '' : ' [FEN only]'}`;
      const ev = one(c.prev, c.next, withMeta ? c.meta : null);
      if (!ev) {
        check(label, false, 'expected exactly one event');
        continue;
      }
      const e = c.expect;
      const problems = [];
      if (ev.kind !== e.kind) problems.push(`kind=${ev.kind} want ${e.kind}`);
      if (ev.from !== e.from) problems.push(`from=${ev.from} want ${e.from}`);
      if (ev.to !== e.to) problems.push(`to=${ev.to} want ${e.to}`);
      if (ev.piece.type !== e.pieceType) problems.push(`piece=${ev.piece.type} want ${e.pieceType}`);
      if (e.captured === null && ev.captured !== null) problems.push('phantom capture');
      if (e.captured && (!ev.captured || ev.captured.type !== e.captured)) {
        problems.push(`captured=${ev.captured && ev.captured.type} want ${e.captured}`);
      }
      if (e.capturedColor && ev.captured && ev.captured.color !== e.capturedColor) {
        problems.push(`capturedColor=${ev.captured.color} want ${e.capturedColor}`);
      }
      if (e.capturedSquare && ev.capturedSquare !== e.capturedSquare) {
        problems.push(`capturedSquare=${ev.capturedSquare} want ${e.capturedSquare}`);
      }
      if (e.promotedTo && ev.promotedTo !== e.promotedTo) {
        problems.push(`promotedTo=${ev.promotedTo} want ${e.promotedTo}`);
      }
      if (e.rookFrom && (!ev.extra || ev.extra.rookFrom !== e.rookFrom)) {
        problems.push(`rookFrom=${ev.extra && ev.extra.rookFrom} want ${e.rookFrom}`);
      }
      if (e.rookTo && (!ev.extra || ev.extra.rookTo !== e.rookTo)) {
        problems.push(`rookTo=${ev.extra && ev.extra.rookTo} want ${e.rookTo}`);
      }
      if (ev.ambiguous) problems.push('flagged ambiguous');
      check(label, problems.length === 0, problems.join(', '));
    }
  }

  // Castling must be ONE event, not two moves.
  const castleEvents = diffPositions(cases[2].prev, cases[2].next, cases[2].meta);
  check('castle emits exactly one event', castleEvents.length === 1,
    `got ${castleEvents.length}`);

  // Identical positions -> no events at all.
  check('identical positions diff to nothing',
    diffPositions(cases[0].prev, cases[0].prev).length === 0);

  // applyUCI must reproduce every case's FEN placement exactly.
  out('gamestate.selfTest — applyUCI reproduces server FENs');
  for (const c of cases) {
    const got = applyUCI(c.prev, c.meta.uci);
    check(`applyUCI ${c.name}`, got.placement === parseFEN(c.next).placement,
      `${got.placement} != ${parseFEN(c.next).placement}`);
  }

  // ── createGameState: multi-ply catch-up, desync healing ────────────────
  out('gamestate.selfTest — createGameState');
  {
    const gs = createGameState();
    const seen = [];
    gs.on('move', (ev) => seen.push(ev));
    gs.applyMatch({
      id: 'T1', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      halfmove_count: 0, moves: [],
    });
    check('first payload emits no events', seen.length === 0);

    const three = [
      { n: 1, side: 'white', san: 'e4', uci: 'e2e4' },
      { n: 2, side: 'black', san: 'e5', uci: 'e7e5' },
      { n: 3, side: 'white', san: 'Nf3', uci: 'g1f3' },
    ];
    const evs = gs.applyMatch({
      id: 'T1',
      fen: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2',
      halfmove_count: 3, moves: three,
    });
    check('3-ply catch-up yields 3 events', evs.length === 3, `got ${evs.length}`);
    check('catch-up events are in play order',
      evs.map((e) => e.uci).join(',') === 'e2e4,e7e5,g1f3');
    check('catch-up leaves no phantom captures', evs.every((e) => e.captured === null));
    check('position matches authoritative FEN',
      gs.position.fen === 'rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2');
  }
  {
    // Desync: claim one ply, hand back a FEN that ply cannot produce.
    const gs = createGameState();
    let desynced = false;
    gs.on('desync', () => { desynced = true; });
    gs.applyMatch({
      id: 'T2', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      halfmove_count: 0, moves: [],
    });
    gs.applyMatch({
      id: 'T2', fen: '8/8/8/4k3/8/8/8/4K3 b - - 0 1', halfmove_count: 1,
      moves: [{ n: 1, side: 'white', san: 'e4', uci: 'e2e4' }],
    });
    check('desync is detected', desynced);
    check('desync self-heals to the authoritative FEN',
      gs.position.placement === '8/8/8/4k3/8/8/8/4K3'.split(' ')[0]);
  }

  // ── Full Opera Game replay ─────────────────────────────────────────────
  if (demoGame && Array.isArray(demoGame.moves)) {
    out('gamestate.selfTest — full demo game replay');
    const gs = createGameState();
    const all = [];
    gs.on('move', (ev) => all.push(ev));
    gs.applyMatch({
      id: 'DEMO', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      halfmove_count: 0, moves: [],
    });
    let bad = null;
    for (let i = 0; i < demoGame.moves.length; i++) {
      const mv = demoGame.moves[i];
      gs.applyMatch({
        id: 'DEMO', fen: mv.fen_after, halfmove_count: i + 1,
        moves: demoGame.moves.slice(0, i + 1),
      });
      if (gs.position.fen !== mv.fen_after) {
        bad = `ply ${i + 1}: ${gs.position.fen} != ${mv.fen_after}`;
        break;
      }
    }
    check('every demo ply lands on its exact FEN', bad === null, bad || '');
    check('one event per demo ply', all.length === demoGame.moves.length,
      `${all.length} events for ${demoGame.moves.length} plies`);
    check('no demo event is ambiguous', all.every((e) => !e.ambiguous));

    // Material bookkeeping: captures must exactly account for missing pieces.
    const startCount = Object.keys(parseFEN(START_FEN).board).length;
    const endCount = Object.keys(
      parseFEN(demoGame.moves[demoGame.moves.length - 1].fen_after).board,
    ).length;
    const captureEvents = all.filter((e) => e.captured);
    check('capture events account for every missing piece',
      startCount - endCount === captureEvents.length,
      `${startCount - endCount} missing vs ${captureEvents.length} captures`);
    check('no capture takes a friendly piece',
      captureEvents.every((e) => e.captured.color !== e.side));
    check('demo contains a castle',
      all.some((e) => e.kind === 'castle' && e.extra && e.extra.rookFrom));
    check('demo SAN survives onto events',
      all.every((e, i) => e.san === demoGame.moves[i].san));
  }

  const ok = failures.length === 0;
  out(`\n${ok ? 'PASS' : 'FAIL'} — ${passed} passed, ${failures.length} failed`);
  return { ok, passed, failed: failures.length, failures };
}
