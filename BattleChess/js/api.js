/**
 * api.js — client for the Forge LLM Chess Arena endpoints, with a transparent
 * offline demo mode.
 *
 * CONTRACT (section 7). Base URL is same-origin by default and overridable
 * with `?api=http://127.0.0.1:5000`.
 *
 *   GET  /api/chess              -> { matches: [...] }   newest first
 *   GET  /api/chess/<id>         -> full match state
 *   POST /api/chess              -> create match
 *   POST /api/chess/<id>/step    -> advance one ply (may add `new_commentary`)
 *   POST /api/chess/<id>/resign  -> { side }
 *   GET  /api/chess/<id>/pgn     -> PGN text
 *
 * Error discipline: no method ever throws. Every call resolves to either the
 * server payload or a structured `{ error, status }` object (status 0 means
 * the network never answered). Server error bodies are merged in, so a 502
 * from /step still carries its match fields alongside `error`.
 *
 *   const api = createAPI();
 *   const res = await api.getMatch(id);
 *   if (res.error) { ...show it... } else { ...render res... }
 *
 * Offline demo mode: the first network-level failure (DNS, refused, timeout,
 * file:// origin) flips the client into demo mode, loads
 * `./assets/demo-game.json` — the Opera Game, Paris 1858, with a precomputed,
 * verified FEN after every ply — and serves synthesized match payloads shaped
 * exactly like `chess_arena.serialize_match`. `step()` advances one ply, and
 * `pollMatch()` auto-advances so the scene plays itself with no server at all.
 */

import { parseFEN } from './gamestate.js';

const DEFAULT_TIMEOUT_MS = 12000;
// A /step call blocks on two LLM round-trips (player + judge). Reasoning-tier
// models routinely take a minute; anything shorter aborts good moves.
const DEFAULT_STEP_TIMEOUT_MS = 180000;
const DEFAULT_POLL_MS = 1500;
const RETRY_BACKOFF_MS = 400;
const DEMO_ASSET_URL = './assets/demo-game.json';
const DEMO_MATCH_ID = 'demo-opera-1858';
const DEMO_JUDGE = 'the-arena-booth';

const PIECE_NAMES = { p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen', k: 'king' };

/**
 * Canned booth commentary for the demo game, keyed by half-move count. These
 * are authored beats about a 167-year-old master game — no model produced
 * them, and the demo payload reports zero tokens and zero cost accordingly.
 */
const DEMO_COMMENTARY = [
  { after_move_n: 4, text: "Morphy opens with the king's pawn and the allies answer in kind, then tuck a pawn onto d6 — the Philidor. Solid, a little passive, and Morphy has never in his life punished passive play gently. Watch the centre." },
  { after_move_n: 8, text: "There it is — d4 rips the centre open, and when the bishop pins the knight on f3 Morphy simply trades it off. The allies hand over the bishop pair on move four. Every piece Morphy owns is about to have a job." },
  { after_move_n: 12, text: "Queen to f3, bishop to c4, and White's pieces are already staring at f7. Black develops the knight to f6 to plug the hole. It plugs one hole. There are others." },
  { after_move_n: 16, text: "Qb3 doubles on the b-file diagonal and now BOTH white pieces hit f7 — the allies are forced into Qe7, burying their own bishop. Nc3, c6, and White has four pieces out to Black's two. This is the part where it stops being an opening and starts being a hunt." },
  { after_move_n: 20, text: "Bg5 pins the knight, and the allies lash out with b5 — and Morphy just takes it. Knight takes b5! A whole piece, thrown onto the fire to blow open the c-file and keep the king in the middle. cxb5 accepts. Now White needs every single tempo." },
  { after_move_n: 24, text: "Bxb5 with check, Nbd7 to block, and Morphy castles LONG — the rook lands on d1 pointed straight down the d-file at that pinned knight. Rd8 tries to hold it together. Two white pieces are already hanging by the rules of arithmetic and none of it matters." },
  { after_move_n: 28, text: "Rxd7! The rook goes for the pinned knight, Black recaptures, and the second rook swings to d1 to pin it all over again. Qe6 defends. The allies have found the only move — and it still loses." },
  { after_move_n: 32, text: "Bxd7 with check, Nxd7, and now the queen sacrifice: Qb8 CHECK — Morphy gives up his queen to drag the knight off d7. Nxb8 is forced. Black is up a queen and a bishop and has been dead for three moves." },
  { after_move_n: 33, text: "Rd8. Checkmate. Two rooks and a bishop developed, everything else given away, and the king is mated in the centre of the board on move seventeen. Paul Morphy, in an opera box, between arias. Nobody has ever finished a game more politely." },
];

// ────────────────────────────────────────────────────────────────────────
// Small helpers
// ────────────────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function isPlainObject(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function nowISO() {
  return new Date().toISOString();
}

/** Read `?api=` from the page URL, if there is a page. */
function baseUrlFromQuery() {
  try {
    const search = globalThis.location && globalThis.location.search;
    if (!search) return null;
    const value = new URLSearchParams(search).get('api');
    return value ? value.trim() : null;
  } catch {
    return null;
  }
}

function normalizeBase(base) {
  if (!base) return '';
  return String(base).trim().replace(/\/+$/, '');
}

function describeNetworkError(err, url) {
  if (typeof fetch !== 'function') return 'fetch() is unavailable in this environment';
  if (!err) return `No response from ${url}`;
  if (err.name === 'AbortError') return `Request to ${url} timed out`;
  return `Cannot reach ${url} (${err.message || err.name || 'network error'})`;
}

// ────────────────────────────────────────────────────────────────────────
// Demo game -> serialize_match-shaped payloads
// ────────────────────────────────────────────────────────────────────────

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

function symbolCounts(fen) {
  const counts = {};
  const { board } = parseFEN(fen);
  for (const sq of Object.keys(board)) {
    const p = board[sq];
    const sym = p.color === 'white' ? p.type.toUpperCase() : p.type;
    counts[sym] = (counts[sym] || 0) + 1;
  }
  return counts;
}

/**
 * Validate the shipped demo file and precompute the per-ply capture ledger by
 * diffing consecutive FENs. Throws if the asset is malformed — a broken demo
 * should be loud, not silently half-rendered.
 */
function prepareDemo(raw) {
  if (!isPlainObject(raw) || !Array.isArray(raw.moves) || raw.moves.length === 0) {
    throw new Error('demo-game.json: expected { meta, moves: [...] }');
  }
  const meta = isPlainObject(raw.meta) ? raw.meta : {};
  const moves = raw.moves.map((mv, i) => {
    if (!isPlainObject(mv) || typeof mv.fen_after !== 'string' || typeof mv.uci !== 'string') {
      throw new Error(`demo-game.json: ply ${i + 1} is missing uci/fen_after`);
    }
    parseFEN(mv.fen_after); // throws on a corrupt FEN
    return {
      n: Number.isFinite(mv.n) ? mv.n : i + 1,
      side: mv.side === 'black' ? 'black' : 'white',
      san: String(mv.san || ''),
      uci: mv.uci,
      fen_after: mv.fen_after,
    };
  });

  // Capture ledger: exactly one symbol count drops on a capturing ply.
  const captures = [];
  let prevCounts = symbolCounts(START_FEN);
  for (const mv of moves) {
    const counts = symbolCounts(mv.fen_after);
    for (const sym of Object.keys(prevCounts)) {
      const before = prevCounts[sym];
      const after = counts[sym] || 0;
      if (after < before) {
        captures.push({
          by: mv.side,
          piece_symbol: sym,
          move_n: mv.n,
          move_san: mv.san,
        });
        break;
      }
    }
    prevCounts = counts;
  }

  return {
    meta: {
      white: String(meta.white || 'White'),
      black: String(meta.black || 'Black'),
      event: String(meta.event || 'Demo game'),
      note: String(meta.note || ''),
    },
    moves,
    captures,
  };
}

function demoPGN(demo, plies, resultToken) {
  const headers = [
    `[Event "${demo.meta.event}"]`,
    '[Site "BattleChess offline demo"]',
    '[Round "1"]',
    `[White "${demo.meta.white}"]`,
    `[Black "${demo.meta.black}"]`,
    `[Result "${resultToken}"]`,
  ];
  const body = [];
  for (let i = 0; i < plies; i++) {
    const mv = demo.moves[i];
    if (mv.side === 'white') body.push(`${Math.floor(i / 2) + 1}. ${mv.san}`);
    else body.push(mv.san);
  }
  body.push(resultToken);
  return `${headers.join('\n')}\n\n${body.join(' ')}\n`;
}

/**
 * Build a payload matching `chess_arena.serialize_match` for the demo game
 * truncated at `plies` half-moves. Terminal state is read off the SAN suffix
 * ('#' = mate, '+' = check) — exact, and it needs no engine in the browser.
 */
function buildDemoMatch(demo, plies, resignedBy) {
  const applied = demo.moves.slice(0, plies);
  const last = applied.length ? applied[applied.length - 1] : null;
  const fen = last ? last.fen_after : START_FEN;
  const parsed = parseFEN(fen);

  const mated = !!(last && last.san.endsWith('#'));
  const inCheck = !!(last && (last.san.endsWith('+') || mated));
  const winner = mated ? last.side : null;

  let status = 'active';
  let result = null;
  let reason = null;
  if (resignedBy) {
    status = resignedBy === 'white' ? 'black_wins' : 'white_wins';
    result = resignedBy === 'white' ? '0-1' : '1-0';
    reason = `resigned (${resignedBy})`;
  } else if (mated) {
    status = winner === 'white' ? 'white_wins' : 'black_wins';
    result = winner === 'white' ? '1-0' : '0-1';
    reason = 'checkmate';
  }
  const over = status !== 'active';
  const resultToken = result || '*';

  const models = { white: demo.meta.white, black: demo.meta.black };
  const turn = parsed.turn;

  return {
    id: DEMO_MATCH_ID,
    fen,
    turn: over ? null : turn,
    status,
    result,
    reason,
    white_model: models.white,
    black_model: models.black,
    human_side: null,
    current_model: over ? null : models[turn],
    in_check: over ? mated : inCheck,
    halfmove_count: applied.length,
    moves: applied.map((mv) => ({
      n: mv.n,
      side: mv.side,
      san: mv.san,
      uci: mv.uci,
      thinking: '',
      forced: false,
      attempts: 1,
      ms: 0,
      source: 'model',
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
    })),
    has_house_moves: false,
    protocol_loss_by: null,
    pgn: demoPGN(demo, applied.length, resultToken),
    judge_model: DEMO_JUDGE,
    commentary_interval: 2,
    commentary_window_plies: 0,
    commentary: DEMO_COMMENTARY
      .filter((c) => c.after_move_n <= applied.length)
      .map((c) => ({
        after_move_n: c.after_move_n,
        round_num: Math.floor((c.after_move_n + 1) / 2),
        text: c.text,
        model: DEMO_JUDGE,
        ms: 0,
        emitted_at: nowISO(),
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
      })),
    captures: demo.captures.filter((c) => c.move_n <= applied.length),
    tokens: {
      white: { in: 0, out: 0, cost_usd: 0 },
      black: { in: 0, out: 0, cost_usd: 0 },
      judge: { in: 0, out: 0, cost_usd: 0 },
      total_in: 0,
      total_out: 0,
      total_cost_usd: 0,
    },
    created_at: nowISO(),
    last_move_at: last ? nowISO() : null,
    // BattleChess-only annotations so the HUD can label the demo honestly.
    demo: true,
    demo_meta: demo.meta,
    demo_total_plies: demo.moves.length,
  };
}

// ────────────────────────────────────────────────────────────────────────
// createAPI
// ────────────────────────────────────────────────────────────────────────

/**
 * @param {object}  [options]
 * @param {string}  [options.baseUrl]        server root; defaults to `?api=` then same-origin
 * @param {number}  [options.timeoutMs]      per-request timeout for cheap calls
 * @param {number}  [options.stepTimeoutMs]  per-request timeout for /step
 * @param {string}  [options.demoUrl]        override the demo asset path
 * @param {boolean} [options.offline]        start in demo mode without probing
 */
export function createAPI(options = {}) {
  const opts = isPlainObject(options) ? options : {};
  const base = normalizeBase(opts.baseUrl != null ? opts.baseUrl : baseUrlFromQuery());
  const timeoutMs = Number.isFinite(opts.timeoutMs) ? opts.timeoutMs : DEFAULT_TIMEOUT_MS;
  const stepTimeoutMs = Number.isFinite(opts.stepTimeoutMs)
    ? opts.stepTimeoutMs
    : DEFAULT_STEP_TIMEOUT_MS;
  const demoUrl = typeof opts.demoUrl === 'string' ? opts.demoUrl : DEMO_ASSET_URL;

  let offline = !!opts.offline;
  let offlineReason = offline ? 'demo mode requested at construction' : null;
  let demo = null;
  let demoLoading = null;
  let demoError = null;
  let demoPly = 0;
  let demoResignedBy = null;

  let pollTimer = null;
  let pollToken = 0;

  // ── transport ────────────────────────────────────────────────────────

  /**
   * One HTTP call with an AbortController timeout and a single backoff retry
   * on network-level failure. Never throws.
   * @returns {{ ok: boolean, network: boolean, result: any }}
   */
  async function request(path, config = {}) {
    // `absolute` requests (the demo asset) are resolved against the *page*,
    // never against the API base — `?api=` can point at another host entirely.
    const url = config.absolute ? path : `${base}${path}`;
    const limit = Number.isFinite(config.timeoutMs) ? config.timeoutMs : timeoutMs;
    const attempts = (Number.isFinite(config.retries) ? config.retries : 1) + 1;

    if (typeof fetch !== 'function') {
      return { ok: false, network: true, result: { error: describeNetworkError(null, url), status: 0 } };
    }

    let lastError = null;
    for (let attempt = 0; attempt < attempts; attempt++) {
      if (attempt > 0) await sleep(RETRY_BACKOFF_MS * attempt);

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), limit);
      try {
        const init = {
          method: config.method || 'GET',
          signal: controller.signal,
          cache: 'no-store',
          headers: { Accept: config.parse === 'text' ? 'text/plain, */*' : 'application/json' },
        };
        if (config.body !== undefined) {
          init.headers['Content-Type'] = 'application/json';
          init.body = JSON.stringify(config.body);
        }
        const response = await fetch(url, init);
        clearTimeout(timer);

        const text = await response.text();
        let data;
        if (config.parse === 'text') {
          data = text;
        } else {
          try {
            data = text ? JSON.parse(text) : {};
          } catch {
            data = null;
          }
        }

        if (!response.ok) {
          const merged = isPlainObject(data) ? { ...data } : {};
          merged.error = (isPlainObject(data) && data.error)
            || `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`;
          merged.status = response.status;
          return { ok: false, network: false, result: merged };
        }
        if (config.parse !== 'text' && data === null) {
          return {
            ok: false,
            network: false,
            result: { error: `Malformed JSON from ${url}`, status: response.status },
          };
        }
        return { ok: true, network: false, result: data };
      } catch (err) {
        clearTimeout(timer);
        lastError = err;
      }
    }
    return {
      ok: false,
      network: true,
      result: { error: describeNetworkError(lastError, url), status: 0 },
    };
  }

  // ── demo mode ────────────────────────────────────────────────────────

  async function loadDemo() {
    if (demo) return demo;
    if (demoLoading) return demoLoading;
    demoLoading = (async () => {
      try {
        const res = await request(demoUrl, { retries: 1, timeoutMs, absolute: true });
        if (!res.ok) {
          demoError = res.result.error || 'demo asset unavailable';
          return null;
        }
        demo = prepareDemo(res.result);
        demoError = null;
        return demo;
      } catch (err) {
        demoError = `demo-game.json is invalid: ${err && err.message ? err.message : err}`;
        return null;
      } finally {
        // Always clear, so a transient failure can be retried on the next call.
        demoLoading = null;
      }
    })();
    return demoLoading;
  }

  /** Flip into demo mode. Returns true once the canned game is ready. */
  async function enterOffline(reason) {
    if (!offline) {
      offline = true;
      offlineReason = reason || 'API unreachable';
      console.warn(`[battlechess/api] offline demo mode: ${offlineReason}`);
    }
    return !!(await loadDemo());
  }

  function demoPayload() {
    return buildDemoMatch(demo, demoPly, demoResignedBy);
  }

  function demoUnavailable() {
    return {
      error: demoError || 'Offline demo game is unavailable',
      status: 0,
      offline: true,
    };
  }

  function demoAdvance() {
    if (!demo) return demoUnavailable();
    if (demoResignedBy || demoPly >= demo.moves.length) return demoPayload();
    demoPly++;
    const payload = demoPayload();
    const beat = DEMO_COMMENTARY.find((c) => c.after_move_n === demoPly);
    if (beat) {
      payload.new_commentary = {
        after_move_n: beat.after_move_n,
        round_num: Math.floor((beat.after_move_n + 1) / 2),
        text: beat.text,
        model: DEMO_JUDGE,
        ms: 0,
        emitted_at: nowISO(),
      };
    }
    return payload;
  }

  /**
   * Run a network call, falling back to the demo equivalent when — and only
   * when — the failure was network-level. HTTP errors (404, 400, 502) are
   * genuine server answers and are surfaced as-is.
   */
  async function withFallback(netCall, demoCall) {
    if (offline) {
      const ready = demo || (await loadDemo());
      return ready ? demoCall() : demoUnavailable();
    }
    const res = await netCall();
    if (res.ok) return res.result;
    if (res.network && (await enterOffline(res.result.error))) return demoCall();
    return res.result;
  }

  // ── public surface ───────────────────────────────────────────────────

  /** GET /api/chess -> { matches: [...] } (newest first). */
  async function listMatches() {
    return withFallback(
      () => request('/api/chess'),
      () => ({ matches: [demoPayload()], offline: true }),
    );
  }

  /** GET /api/chess/<id> -> match payload. In demo mode `id` is ignored. */
  async function getMatch(id) {
    if (!id) return { error: 'getMatch: a match id is required', status: 400 };
    return withFallback(
      () => request(`/api/chess/${encodeURIComponent(id)}`),
      () => demoPayload(),
    );
  }

  /**
   * POST /api/chess -> new match payload.
   * `opts` is passed through verbatim: { white_model, black_model, judge_model,
   * commentary_interval, commentary_window_plies, starting_fen } or
   * { human_side, ai_model, ... }.
   */
  async function createMatch(opts2 = {}) {
    return withFallback(
      () => request('/api/chess', { method: 'POST', body: isPlainObject(opts2) ? opts2 : {} }),
      () => {
        demoPly = 0;
        demoResignedBy = null;
        return demoPayload();
      },
    );
  }

  /** POST /api/chess/<id>/step -> match payload, possibly with new_commentary. */
  async function step(id) {
    if (!id) return { error: 'step: a match id is required', status: 400 };
    return withFallback(
      () => request(`/api/chess/${encodeURIComponent(id)}/step`, {
        method: 'POST',
        body: {},
        timeoutMs: stepTimeoutMs,
        // A step mutates server state; retrying a timeout could double-move.
        retries: 0,
      }),
      () => demoAdvance(),
    );
  }

  /** POST /api/chess/<id>/resign -> match payload. */
  async function resign(id, side) {
    if (!id) return { error: 'resign: a match id is required', status: 400 };
    if (side !== 'white' && side !== 'black') {
      return { error: `resign: side must be "white" or "black", got ${JSON.stringify(side)}`, status: 400 };
    }
    return withFallback(
      () => request(`/api/chess/${encodeURIComponent(id)}/resign`, { method: 'POST', body: { side } }),
      () => {
        demoResignedBy = side;
        return demoPayload();
      },
    );
  }

  /** GET /api/chess/<id>/pgn -> `{ pgn }` (or `{ error, status }`). */
  async function getPGN(id) {
    if (!id) return { error: 'getPGN: a match id is required', status: 400 };
    const result = await withFallback(
      () => request(`/api/chess/${encodeURIComponent(id)}/pgn`, { parse: 'text' }),
      () => demoPayload().pgn,
    );
    if (typeof result === 'string') return { pgn: result };
    if (isPlainObject(result) && typeof result.pgn === 'string') return { pgn: result.pgn };
    return isPlainObject(result) ? result : { error: 'getPGN: unexpected response', status: 0 };
  }

  /** Force demo mode on and rewind the canned game to move zero. */
  async function useDemo() {
    offline = true;
    offlineReason = offlineReason || 'demo mode requested';
    const ready = await loadDemo();
    if (!ready) return demoUnavailable();
    demoPly = 0;
    demoResignedBy = null;
    return demoPayload();
  }

  /**
   * Poll a match and hand every *changed* payload to `onUpdate`.
   *
   * @param {string} id
   * @param {object} [config]
   * @param {number}   [config.intervalMs=1500]
   * @param {function} [config.onUpdate]  (payload) => void, on every change
   * @param {function} [config.onError]   ({error,status}) => void
   * @param {function} [config.onEnd]     (payload) => void, once the match is over
   * @param {boolean}  [config.stopWhenFinished=true]
   * @param {boolean}  [config.autoAdvance=true]  in demo mode, step each tick
   *                                              (there is no server to move for us)
   * @returns {function} stop function for this poller
   */
  function pollMatch(id, config = {}) {
    stopPolling();
    const cfg = isPlainObject(config) ? config : {};
    const interval = Number.isFinite(cfg.intervalMs) ? Math.max(120, cfg.intervalMs) : DEFAULT_POLL_MS;
    const onUpdate = typeof cfg.onUpdate === 'function' ? cfg.onUpdate : () => {};
    const onError = typeof cfg.onError === 'function' ? cfg.onError : () => {};
    const onEnd = typeof cfg.onEnd === 'function' ? cfg.onEnd : () => {};
    const stopWhenFinished = cfg.stopWhenFinished !== false;
    const autoAdvance = cfg.autoAdvance !== false;

    const token = ++pollToken;
    let signature = null;

    const alive = () => token === pollToken;

    const schedule = () => {
      if (!alive()) return;
      pollTimer = setTimeout(tick, interval);
    };

    async function tick() {
      if (!alive()) return;
      let payload;
      try {
        if (offline && autoAdvance) {
          // No server to move for us — drive the canned game ourselves.
          const ready = demo || (await loadDemo());
          payload = ready ? demoAdvance() : demoUnavailable();
        } else {
          payload = await getMatch(id);
        }
      } catch (err) {
        // getMatch never throws, but a caller-supplied clock could.
        payload = { error: `poll failed: ${err && err.message ? err.message : err}`, status: 0 };
      }
      if (!alive()) return;

      if (!payload || (payload.error && typeof payload.fen !== 'string')) {
        try {
          onError(payload || { error: 'poll: empty response', status: 0 });
        } catch (err) {
          console.error('[battlechess/api] onError threw:', err);
        }
        schedule();
        return;
      }

      const sig = [
        payload.fen,
        payload.halfmove_count,
        payload.status,
        Array.isArray(payload.moves) ? payload.moves.length : 0,
        Array.isArray(payload.commentary) ? payload.commentary.length : 0,
      ].join('|');

      if (sig !== signature) {
        signature = sig;
        try {
          onUpdate(payload);
        } catch (err) {
          console.error('[battlechess/api] onUpdate threw:', err);
        }
      }
      if (payload.error) {
        try {
          onError(payload);
        } catch (err) {
          console.error('[battlechess/api] onError threw:', err);
        }
      }

      const finished = payload.status && payload.status !== 'active';
      const demoExhausted = offline && autoAdvance && demo && demoPly >= demo.moves.length;
      if (finished || demoExhausted) {
        try {
          onEnd(payload);
        } catch (err) {
          console.error('[battlechess/api] onEnd threw:', err);
        }
        if (stopWhenFinished) {
          stopPolling();
          return;
        }
      }
      schedule();
    }

    // Kick immediately so the first frame does not wait a full interval.
    tick();
    return () => {
      if (token === pollToken) stopPolling();
    };
  }

  /** Stop the active poller (safe to call when nothing is polling). */
  function stopPolling() {
    pollToken++;
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  const api = {
    listMatches,
    getMatch,
    createMatch,
    step,
    resign,
    getPGN,
    useDemo,
    pollMatch,
    stopPolling,
  };

  Object.defineProperties(api, {
    isOffline: { get: () => offline, enumerable: true },
    offlineReason: { get: () => offlineReason, enumerable: true },
    demoError: { get: () => demoError, enumerable: true },
    demoPly: { get: () => demoPly, enumerable: true },
    demoTotalPlies: { get: () => (demo ? demo.moves.length : 0), enumerable: true },
    demoMeta: { get: () => (demo ? demo.meta : null), enumerable: true },
    isPolling: { get: () => pollTimer !== null, enumerable: true },
    baseUrl: { get: () => base, enumerable: true },
  });
  return api;
}

/** Human-readable piece name for a python-chess symbol ('N' -> 'knight'). */
export function pieceNameFromSymbol(symbol) {
  return PIECE_NAMES[String(symbol || '').toLowerCase()] || String(symbol || '');
}

/** 'P' -> { type:'p', color:'white' }; used for the captured-piece rack. */
export function pieceFromSymbol(symbol) {
  const s = String(symbol || '');
  const lower = s.toLowerCase();
  if (!PIECE_NAMES[lower]) return null;
  return { type: lower, color: s === lower ? 'black' : 'white' };
}
