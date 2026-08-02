/**
 * @file main.js — BattleChess bootstrap, frame loop and match driver.
 *
 * This is the only module that knows about every other module. It:
 *   1. imports the subsystems (dynamically, so a missing vendor file produces a
 *      readable diagnosis instead of a blank page),
 *   2. builds the stage, board, pieces, fx, animation queue, api, gamestate and
 *      HUD, and wires the seams between them,
 *   3. owns the single requestAnimationFrame loop — one `dt`, computed once,
 *      driving `queue.update` -> `fx.update` -> `stage.render`,
 *   4. owns the match driver: poll / auto-step / animate, gated so a step can
 *      never overlap an animation and a failing step can never spin,
 *   5. enforces CONTRACT.md 11.6 — the FEN is truth. After every batch of
 *      animated events the render state is checked against the authoritative
 *      position and snapped back if it disagrees.
 *
 * Nothing in here mutates a piece transform directly; all motion goes through
 * the AnimationQueue, per CONTRACT.md section 8.
 *
 * Named exports: `boot`, `getApp`.
 */

const LOG = '[BattleChess]';

/* ══════════════════════════════════════════════════════════════════════════
 * Boot bridge
 * index.html installs `window.BattleChessBoot` in a classic script, so it is
 * always available before this module parses. The fallback keeps main.js
 * runnable if the page shell is ever replaced.
 * ══════════════════════════════════════════════════════════════════════════ */

const Boot = (() => {
  const real = typeof window !== 'undefined' ? window.BattleChessBoot : null;
  if (real && typeof real.setProgress === 'function') return real;
  return {
    hasWebGL: true,
    setProgress() {},
    done() {},
    fail(message, detail) {
      console.error(`${LOG} ${message}`, detail || '');
    },
  };
})();

/* ══════════════════════════════════════════════════════════════════════════
 * Configuration (query string)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Read the runtime switches off the page URL.
 *
 * | param      | effect                                                  |
 * |------------|---------------------------------------------------------|
 * | `api`      | API base URL (consumed by api.js itself)                 |
 * | `quality`  | `low` \| `medium` \| `high` — CONTRACT.md section 9       |
 * | `match`    | join a specific match id instead of the newest           |
 * | `demo`     | `1` forces offline demo mode, never touching the server   |
 * | `autoplay` | `0` starts paused                                        |
 * | `speed`    | initial animation speed multiplier                       |
 * | `selftest` | `1` runs gamestate.selfTest() and logs the result         |
 *
 * @returns {{quality:string, matchId:string|null, demo:boolean,
 *            autoplay:boolean, speed:number, selftest:boolean}}
 */
function readConfig() {
  let params;
  try {
    params = new URLSearchParams(window.location.search);
  } catch {
    params = new URLSearchParams('');
  }
  const quality = String(params.get('quality') || '').toLowerCase();
  const speed = Number(params.get('speed'));
  return {
    quality: ['low', 'medium', 'high'].includes(quality) ? quality : 'high',
    matchId: params.get('match') || null,
    demo: params.get('demo') === '1',
    autoplay: params.get('autoplay') !== '0',
    speed: Number.isFinite(speed) && speed > 0 ? speed : 1,
    selftest: params.get('selftest') === '1',
  };
}

/* ══════════════════════════════════════════════════════════════════════════
 * Module loading + vendor diagnosis
 *
 * Every sibling module statically imports `three`. If the vendored three.js is
 * incomplete, a *static* import here would kill main.js too and the operator
 * would get a blank screen plus an opaque console error. Importing dynamically
 * keeps main.js alive so it can say exactly which file is missing.
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Files the importmap resolves to that must physically exist. `three.module.js`
 * imports `./three.core.js` and every postprocessing pass imports `./Pass.js`,
 * so a missing sibling breaks the whole module graph, not just one feature.
 * @type {string[]}
 */
const VENDOR_FILES = [
  './vendor/three.module.js',
  './vendor/three.core.js',
  './vendor/addons/controls/OrbitControls.js',
  './vendor/addons/loaders/GLTFLoader.js',
  './vendor/addons/utils/BufferGeometryUtils.js',
  './vendor/addons/postprocessing/Pass.js',
  './vendor/addons/postprocessing/EffectComposer.js',
  './vendor/addons/postprocessing/RenderPass.js',
  './vendor/addons/postprocessing/UnrealBloomPass.js',
  './vendor/addons/postprocessing/OutputPass.js',
];

/**
 * Probe the vendor tree for missing files. Same-origin static assets only —
 * this is the identical fetch the module loader would perform, so it does not
 * violate the "no external network calls" rule in CONTRACT.md section 11.2.
 * Only ever called on the failure path.
 *
 * @returns {Promise<string[]>} Paths that could not be fetched.
 */
async function findMissingVendorFiles() {
  const missing = [];
  await Promise.all(VENDOR_FILES.map(async (path) => {
    try {
      const url = new URL(path, document.baseURI).href;
      const res = await fetch(url, { method: 'GET', cache: 'no-store' });
      if (!res.ok) missing.push(path);
    } catch {
      missing.push(path);
    }
  }));
  return missing;
}

/**
 * Dynamically import every subsystem.
 * @returns {Promise<object>} Namespace bag keyed by module name.
 */
async function importModules() {
  const [scene, board, pieces, animation, fx, api, gamestate, hud] = await Promise.all([
    import('./scene.js'),
    import('./board.js'),
    import('./pieces.js'),
    import('./animation.js'),
    import('./fx.js'),
    import('./api.js'),
    import('./gamestate.js'),
    import('./hud.js'),
  ]);
  return { scene, board, pieces, animation, fx, api, gamestate, hud };
}

/**
 * Turn an import failure into an operator-actionable message.
 * @param {Error} err
 * @returns {Promise<{message:string, detail:string}>}
 */
async function explainImportFailure(err) {
  const missing = await findMissingVendorFiles();
  const raw = err && err.message ? err.message : String(err);

  if (missing.length) {
    return {
      message: 'The vendored three.js build is incomplete, so no module that imports '
        + '"three" could be loaded.',
      detail: [
        'Missing file(s) under BattleChess/vendor/:',
        ...missing.map((m) => `  - ${m.replace('./vendor/', '')}`),
        '',
        'three.js r184 ships its build split across two files: three.module.js',
        'holds the WebGL renderer and re-exports the rest from three.core.js.',
        'Both must be vendored together, from the same r184 distribution.',
        '',
        `Underlying error: ${raw}`,
      ].join('\n'),
    };
  }

  return {
    message: 'A BattleChess module failed to load.',
    detail: [
      raw,
      '',
      'All vendor files resolved, so this is most likely a syntax error inside',
      'one of the modules in BattleChess/js/ — check the browser console, which',
      'names the offending file and line.',
    ].join('\n'),
  };
}

/* ══════════════════════════════════════════════════════════════════════════
 * Seam adapters
 * Small translations between modules whose vocabularies drifted.
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * hud.js offers a camera button labelled `orbit`; scene.js names that pose
 * `cinematic`. Unknown preset names are silently ignored by scene.js, so
 * without this map the ORB button would be a dead control.
 * @type {Object<string,string>}
 */
const CAMERA_ALIASES = {
  orbit: 'cinematic',
  cinematic: 'cinematic',
  side: 'side',
  white: 'white',
  black: 'black',
  top: 'top',
};

/** Poll/step pacing, in milliseconds. */
const TIMING = {
  stepGap: 220,        // between auto-steps once the queue has drained
  idlePoll: 2500,      // passive refresh while paused, so external moves show up
  animGate: 60,        // re-check interval while an animation owns the board
  backoffMin: 1000,
  backoffMax: 30000,
};

/* ══════════════════════════════════════════════════════════════════════════
 * Application
 * ══════════════════════════════════════════════════════════════════════════ */

/** @type {object|null} The live app, exposed for debugging via getApp(). */
let APP = null;

/**
 * Build and start the whole viewer.
 * Resolves once the first match is on the board; rejects only on a fault that
 * makes rendering impossible (the error panel is shown either way).
 *
 * @returns {Promise<object|null>} the app handle, or null if boot failed.
 */
export async function boot() {
  const config = readConfig();

  if (!Boot.hasWebGL) {
    // index.html has already raised the no-WebGL panel; don't fight it.
    return null;
  }

  const canvas = document.getElementById('bc-canvas');
  if (!canvas) {
    Boot.fail('The page shell is missing its <canvas id="bc-canvas">.',
      'index.html must provide the canvas element before js/main.js runs.');
    return null;
  }

  // ── modules ─────────────────────────────────────────────────────────────
  Boot.setProgress(6, 'LOADING RENDER CORE');
  let mods;
  try {
    mods = await importModules();
  } catch (err) {
    const { message, detail } = await explainImportFailure(err);
    console.error(`${LOG} ${message}\n${detail}`);
    Boot.fail(message, detail);
    return null;
  }

  const { createStage } = mods.scene;
  const { createBoard } = mods.board;
  const { createPieceManager, normalizePieceType, normalizePieceColor } = mods.pieces;
  const { AnimationQueue } = mods.animation;
  const { createFX } = mods.fx;
  const { createAPI } = mods.api;
  const { createGameState, ALL_SQUARES, parseUCI } = mods.gamestate;
  const { createHUD } = mods.hud;

  if (config.selftest && typeof mods.gamestate.selfTest === 'function') {
    try {
      const result = mods.gamestate.selfTest({ log: true });
      console.info(`${LOG} gamestate selfTest:`, result);
    } catch (err) {
      console.warn(`${LOG} gamestate selfTest threw`, err);
    }
  }

  // ── stage ───────────────────────────────────────────────────────────────
  Boot.setProgress(22, 'IGNITING RENDER CORE');
  let stage;
  try {
    stage = createStage({ canvas, quality: config.quality });
  } catch (err) {
    const detail = err && err.stack ? err.stack : String(err);
    console.error(`${LOG} createStage failed`, err);
    Boot.fail('The WebGL renderer could not be initialised.', detail);
    return null;
  }

  // ── board ───────────────────────────────────────────────────────────────
  Boot.setProgress(34, 'LAYING THE DECK');
  const board = createBoard();
  stage.scene.add(board.group);

  // Start drawing immediately: the empty board is visible while models load.
  const clock = { last: performance.now(), raf: 0, running: true };

  // ── pieces ──────────────────────────────────────────────────────────────
  Boot.setProgress(46, 'MATERIALISING COMBATANTS');
  const pieces = createPieceManager({ scene: stage.scene });
  let modelSource = { white: 'pending', black: 'pending' };
  try {
    modelSource = await pieces.load();
  } catch (err) {
    // pieces.load() is documented never to reject; belt and braces.
    console.warn(`${LOG} piece load fell back to procedural geometry`, err);
  }

  // ── fx + animation ──────────────────────────────────────────────────────
  Boot.setProgress(66, 'CHARGING WEAPONS');
  // NOTE the deliberate asymmetry, it is not a typo:
  //   createFX wants the THREE.Scene   (it does `scene.add(root)` / isObject3D)
  //   AnimationQueue wants the *stage* (it calls `scene.shake(amount, ms)`)
  const fx = createFX({ scene: stage.scene });
  const queue = new AnimationQueue({ pieces, fx, scene: stage, board });
  queue.setSpeed(config.speed);

  // ── api + gamestate ─────────────────────────────────────────────────────
  Boot.setProgress(78, 'OPENING SUBSPACE CHANNEL');
  const api = createAPI({ offline: config.demo });
  const gameState = createGameState();

  /* ────────────────────────────────────────────────────────────────────────
   * Driver state
   * ──────────────────────────────────────────────────────────────────────── */

  const driver = {
    matchId: null,
    playing: false,
    /** Whether the match can still advance. */
    live: false,
    /** A request is in flight; never overlap them. */
    inFlight: false,
    /** performance.now() timestamp of the next allowed pump. Infinity = parked. */
    nextAt: Infinity,
    /** Consecutive failures, drives exponential backoff. */
    failures: 0,
    /** Match id whose pieces are currently spawned. */
    renderedMatchId: null,
    /** Mesh currently wearing a check halo, so we can remove it later. */
    haloMesh: null,
    /** Set once the game-over cinematic has fired for this match. */
    finaleShown: false,
    disposed: false,
  };

  /* ────────────────────────────────────────────────────────────────────────
   * HUD
   * ──────────────────────────────────────────────────────────────────────── */

  const hud = createHUD({
    root: document.getElementById('bc-hud'),
    api,
    onCommand: (name, payload) => handleCommand(name, payload || {}),
  });

  /* ────────────────────────────────────────────────────────────────────────
   * FEN-is-truth reconciliation (CONTRACT.md 11.6)
   * ──────────────────────────────────────────────────────────────────────── */

  /**
   * Compare the rendered board against the authoritative position and snap if
   * they disagree. Animations are cosmetic; the FEN always wins.
   *
   * @returns {boolean} true if the render state already matched.
   */
  function verifyAgainstFEN() {
    const position = gameState.position;
    if (!position || !position.board) return true;

    let drift = null;
    for (const square of ALL_SQUARES) {
      const want = position.board[square] || null;
      const have = pieces.getPiece(square);
      const haveLive = have && !have.captured ? have : null;

      if (!want && !haveLive) continue;
      if (!want || !haveLive) {
        drift = `${square}: ${want ? 'expected' : 'unexpected'} piece`;
        break;
      }
      if (normalizePieceType(want.type) !== haveLive.type
        || normalizePieceColor(want.color) !== haveLive.color) {
        drift = `${square}: expected ${want.color} ${want.type}, `
          + `rendered ${haveLive.color} ${haveLive.type}`;
        break;
      }
    }

    if (!drift) return true;
    console.warn(`${LOG} render/FEN desync (${drift}) — snapping to the FEN.`);
    try {
      pieces.snapToPosition(position);
    } catch (err) {
      console.error(`${LOG} snapToPosition failed`, err);
    }
    return false;
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Board decoration: last-move highlights + check halo
   * ──────────────────────────────────────────────────────────────────────── */

  /** board.highlight() throws on an unknown square; never let that reach the loop. */
  function safeHighlight(square, kind) {
    if (!square) return;
    try {
      board.highlight(square, kind);
    } catch {
      /* off-board or malformed square — nothing to draw */
    }
  }

  /** Squares of the most recent ply, read from the match's move list. */
  function lastMoveSquares() {
    const match = gameState.match;
    const moves = match && Array.isArray(match.moves) ? match.moves : null;
    if (!moves || !moves.length) return null;
    for (let i = moves.length - 1; i >= 0; i--) {
      const uci = moves[i] && moves[i].uci;
      if (typeof uci !== 'string' || !uci) continue;
      const parsed = parseUCI(uci);
      if (parsed) return parsed;
    }
    return null;
  }

  /**
   * Drop any halo we put on a king, and forget it. Called before re-evaluating
   * check and on teardown — animation.js raises the halo but never clears it,
   * so ownership of the removal sits here.
   */
  function clearCheckHalo() {
    if (!driver.haloMesh) return;
    try {
      fx.removeHalo(driver.haloMesh);
    } catch (err) {
      console.warn(`${LOG} removeHalo failed`, err);
    }
    driver.haloMesh = null;
  }

  /** The square the side-to-move's king stands on, per the FEN. */
  function checkedKingSquare() {
    const position = gameState.position;
    if (!position || !position.board) return null;
    // The FEN's side-to-move is the side that is in check.
    const side = position.turn;
    for (const square of ALL_SQUARES) {
      const entry = position.board[square];
      if (entry && normalizePieceType(entry.type) === 'king'
        && normalizePieceColor(entry.color) === side) {
        return square;
      }
    }
    return null;
  }

  /** Repaint from/to/check highlights from the current authoritative state. */
  function refreshDecor() {
    try {
      board.clearHighlights();
    } catch (err) {
      console.warn(`${LOG} clearHighlights failed`, err);
    }

    const move = lastMoveSquares();
    if (move) {
      safeHighlight(move.from, 'from');
      safeHighlight(move.to, 'to');
    }

    const match = gameState.match;
    const inCheck = !!(match && match.in_check);
    clearCheckHalo();

    if (!inCheck) return;
    const square = checkedKingSquare();
    if (!square) return;
    safeHighlight(square, 'check');

    const king = pieces.getPiece(square);
    const mesh = king && king.mesh ? king.mesh : null;
    if (mesh) {
      try {
        fx.warningHalo(mesh, 0xff3040);
        driver.haloMesh = mesh;
      } catch (err) {
        console.warn(`${LOG} warningHalo failed`, err);
      }
    }
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Batch lifecycle
   * ──────────────────────────────────────────────────────────────────────── */

  /**
   * Runs when the animation queue drains. Reconciles against the FEN, repaints
   * the board decoration and releases the HUD's busy gate.
   */
  function onBatchComplete() {
    verifyAgainstFEN();
    refreshDecor();
    hud.setBusy(false);
    if (!driver.live) {
      showFinale();
      return;
    }
    // Ready for the next ply the moment the board is at rest. Always re-arm,
    // including when paused, so moves made elsewhere still surface.
    armPump(driver.playing ? TIMING.stepGap : TIMING.idlePoll);
  }

  /**
   * Coalesce batch completion to at most once per turn. `queue.flush()` fires
   * onIdle synchronously, so an ingest that flushes would otherwise reconcile
   * twice — harmless but wasteful, and it double-fires the check halo.
   */
  let batchPending = false;
  function requestBatchComplete() {
    if (batchPending || driver.disposed) return;
    batchPending = true;
    queueMicrotask(() => {
      batchPending = false;
      if (!driver.disposed) onBatchComplete();
    });
  }

  queue.onIdle(requestBatchComplete);

  /**
   * Game-over flourish: CONTRACT.md section 8 asks for a slow orbital push-in on
   * the losing king. `cinematic` is the stage's low, slow auto-orbit pose.
   */
  function showFinale() {
    if (driver.finaleShown) return;
    const match = gameState.match;
    if (!match || (!match.result && match.status === 'active')) return;
    driver.finaleShown = true;
    clearCheckHalo();
    try {
      stage.setCameraPreset('cinematic', { duration: 3.0 });
    } catch (err) {
      console.warn(`${LOG} finale camera move failed`, err);
    }
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Payload ingestion
   * ──────────────────────────────────────────────────────────────────────── */

  /**
   * True when a payload is an API failure rather than a match.
   * api.js returns `{error, status}` with no `fen` in that case.
   */
  function isFailure(payload) {
    return !payload || typeof payload !== 'object' || typeof payload.fen !== 'string';
  }

  /** Is this match still able to advance? */
  function isLive(match) {
    if (!match) return false;
    if (match.result) return false;
    return match.status === 'active' || match.status === undefined;
  }

  /**
   * Adopt a match payload: diff it, animate the difference, update the HUD.
   *
   * @param {object} payload A match payload from api.js.
   * @param {{animate?:boolean}} [opts] `animate:false` snaps with no choreography.
   * @returns {boolean} whether the payload was accepted.
   */
  function ingest(payload, opts = {}) {
    if (isFailure(payload)) return false;
    const animate = opts.animate !== false;

    let events = [];
    try {
      events = gameState.applyMatch(payload) || [];
    } catch (err) {
      console.error(`${LOG} applyMatch threw`, err);
      return false;
    }

    const match = gameState.match || payload;
    const switched = gameState.matchId !== driver.renderedMatchId;

    driver.matchId = gameState.matchId != null ? gameState.matchId : driver.matchId;
    driver.live = isLive(match);
    if (switched) driver.finaleShown = false;

    // A new match (or the very first payload) spawns wholesale — there is no
    // previous position to animate away from.
    if (switched) {
      queue.clear();
      clearCheckHalo();
      const position = gameState.position;
      if (position) {
        try {
          pieces.spawnFromPosition(position);
        } catch (err) {
          console.error(`${LOG} spawnFromPosition failed`, err);
        }
      }
      driver.renderedMatchId = gameState.matchId;
      events = [];
    }

    if (events.length && animate) {
      hud.setBusy(true);
      for (const event of events) queue.enqueue(event);
      // Check is choreographed as its own beat after the move that caused it.
      if (match.in_check && driver.live) {
        const square = checkedKingSquare();
        if (square) queue.enqueue({ kind: 'check', square });
      }
    } else if (events.length) {
      // Snapping: apply the events instantly rather than dropping them, so the
      // rack bookkeeping inside animation.js still runs.
      for (const event of events) queue.enqueue(event);
      queue.flush();
    }

    hud.update(match);

    if (payload.new_commentary) {
      try {
        hud.pushCommentary(payload.new_commentary);
      } catch (err) {
        console.warn(`${LOG} pushCommentary failed`, err);
      }
    }

    // Nothing to animate: reconcile straight away rather than waiting on onIdle,
    // which only fires on a busy -> idle transition.
    if (!queue.isBusy) requestBatchComplete();

    return true;
  }

  // A desync reported by gamestate.js is authoritative: the replay disagreed
  // with the server FEN, so drop the cosmetic queue and rebuild from the FEN.
  gameState.on('desync', (info) => {
    console.warn(`${LOG} gamestate desync — replay disagreed with the server FEN`, info);
    queue.flush();
    verifyAgainstFEN();
    refreshDecor();
  });

  gameState.on('error', (info) => {
    console.warn(`${LOG} gamestate error`, info);
  });

  /* ────────────────────────────────────────────────────────────────────────
   * Match driver
   * ──────────────────────────────────────────────────────────────────────── */

  /** Schedule the next pump in `ms`. */
  function armPump(ms) {
    if (driver.disposed) return;
    const delay = Number.isFinite(ms) ? Math.max(0, ms) : 0;
    driver.nextAt = performance.now() + delay;
  }

  /** Park the driver; only an explicit command restarts it. */
  function parkPump() {
    driver.nextAt = Infinity;
  }

  /** Exponential backoff so a broken endpoint can never spin a hot loop. */
  function backoff(reason) {
    driver.failures += 1;
    const delay = Math.min(
      TIMING.backoffMax,
      TIMING.backoffMin * Math.pow(2, Math.min(driver.failures - 1, 5)),
    );
    console.warn(`${LOG} ${reason} — retrying in ${Math.round(delay / 1000)}s `
      + `(attempt ${driver.failures})`);
    hud.setStatus(`LINK FAULT · RETRY ${Math.round(delay / 1000)}s`);
    armPump(delay);
  }

  /**
   * One driver beat. Steps the match when playing, otherwise refreshes it
   * passively so moves made elsewhere still appear.
   */
  async function pump() {
    if (driver.disposed || driver.inFlight || !driver.matchId) return;

    // Never step while the board is animating (CONTRACT.md section 8).
    if (queue.isBusy) {
      armPump(TIMING.animGate);
      return;
    }

    if (!driver.live) {
      parkPump();
      showFinale();
      return;
    }

    const stepping = driver.playing;
    driver.inFlight = true;
    if (stepping) hud.setBusy(true);

    let payload = null;
    try {
      payload = stepping
        ? await api.step(driver.matchId)
        : await api.getMatch(driver.matchId);
    } catch (err) {
      // api.js is documented never to throw; treat a surprise as a failure.
      payload = { error: err && err.message ? err.message : String(err), status: 0 };
    } finally {
      driver.inFlight = false;
    }

    if (driver.disposed) return;

    if (isFailure(payload)) {
      hud.setBusy(false);
      backoff(payload && payload.error ? payload.error : 'step failed');
      return;
    }

    driver.failures = 0;
    ingest(payload);

    if (!driver.live) {
      parkPump();
      if (!queue.isBusy) showFinale();
      return;
    }
    if (driver.playing) {
      // If the batch is animating, onBatchComplete re-arms us; otherwise pace
      // the next step ourselves.
      if (!queue.isBusy) armPump(TIMING.stepGap);
    } else {
      armPump(TIMING.idlePoll);
    }
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Match selection / creation
   * ──────────────────────────────────────────────────────────────────────── */

  /**
   * Attach to a match by id.
   * @param {string} id
   * @returns {Promise<boolean>}
   */
  async function loadMatch(id) {
    if (!id) return false;
    queue.clear();
    clearCheckHalo();
    driver.matchId = String(id);
    driver.failures = 0;
    parkPump();

    const payload = await api.getMatch(driver.matchId);
    if (isFailure(payload)) {
      hud.setStatus('MATCH UNAVAILABLE');
      backoff(payload && payload.error ? payload.error : 'match unavailable');
      return false;
    }
    ingest(payload, { animate: false });
    annotateSource();
    armPump(driver.playing ? TIMING.stepGap : TIMING.idlePoll);
    return true;
  }

  /** Create a fresh match on the server (or rewind the demo when offline). */
  async function newMatch() {
    queue.clear();
    clearCheckHalo();
    driver.renderedMatchId = null;
    driver.failures = 0;
    parkPump();
    hud.setStatus('CREATING MATCH…');

    const payload = await api.createMatch({});
    if (isFailure(payload)) {
      hud.setStatus('COULD NOT CREATE MATCH');
      backoff(payload && payload.error ? payload.error : 'createMatch failed');
      return false;
    }
    ingest(payload, { animate: false });
    annotateSource();
    armPump(driver.playing ? TIMING.stepGap : TIMING.idlePoll);
    return true;
  }

  /** Tell the operator when they are looking at canned data or fallback art. */
  function annotateSource() {
    const notes = [];
    if (api.isOffline) notes.push('OFFLINE DEMO');
    if (modelSource.white !== 'glb' || modelSource.black !== 'glb') {
      notes.push('PLACEHOLDER MODELS');
    }
    hud.setStatus(notes.join(' · '));
  }

  /**
   * Pick something to show: the requested match, else the newest on the server,
   * else a freshly created one, else the offline demo.
   * @returns {Promise<boolean>}
   */
  async function selectInitialMatch() {
    if (config.matchId) {
      if (await loadMatch(config.matchId)) return true;
    }

    const listing = await api.listMatches();
    const matches = listing && Array.isArray(listing.matches) ? listing.matches : [];
    // The API returns newest first; prefer a match still in progress.
    const candidate = matches.find((m) => m && m.id && isLive(m)) || matches[0];
    if (candidate && candidate.id) {
      if (await loadMatch(candidate.id)) return true;
    }

    if (await newMatch()) return true;

    // Last resort: force the canned game so the scene is never empty.
    const demo = await api.useDemo();
    if (!isFailure(demo)) {
      driver.matchId = demo.id;
      ingest(demo, { animate: false });
      annotateSource();
      armPump(TIMING.stepGap);
      return true;
    }

    hud.setStatus('NO MATCH AVAILABLE');
    return false;
  }

  /* ────────────────────────────────────────────────────────────────────────
   * HUD command routing
   * ──────────────────────────────────────────────────────────────────────── */

  /** One-shot manual advance, honouring the same animation gate as autoplay. */
  function manualStep() {
    if (!driver.matchId || !driver.live || driver.inFlight || queue.isBusy) return;
    const wasPlaying = driver.playing;
    driver.playing = true;
    pump().finally(() => { driver.playing = wasPlaying; });
  }

  /**
   * Resign on behalf of a side. Only ever reached from the HUD's own two-click
   * confirm control, i.e. a deliberate operator action.
   * @param {'white'|'black'} side
   */
  async function resign(side) {
    if (!driver.matchId || (side !== 'white' && side !== 'black')) return;
    parkPump();
    const payload = await api.resign(driver.matchId, side);
    if (isFailure(payload)) {
      backoff(payload && payload.error ? payload.error : 'resign failed');
      return;
    }
    ingest(payload, { animate: false });
  }

  /**
   * Route a HUD control to the subsystem that owns it.
   * @param {string} name
   * @param {object} payload
   */
  function handleCommand(name, payload) {
    switch (name) {
      case 'play':
        driver.playing = true;
        driver.failures = 0;
        if (driver.live) armPump(0);
        break;

      case 'pause':
        driver.playing = false;
        if (driver.live) armPump(TIMING.idlePoll);
        break;

      case 'step':
        manualStep();
        break;

      case 'skip':
        queue.flush();          // snaps to final state; onIdle -> onBatchComplete
        break;

      case 'speed':
        queue.setSpeed(payload.value);
        break;

      case 'camera': {
        const preset = CAMERA_ALIASES[String(payload.preset || '')] || payload.preset;
        stage.setCameraPreset(preset);
        break;
      }

      case 'quality':
        stage.setQuality(payload.value);
        break;

      case 'load-match':
        loadMatch(payload.id);
        break;

      case 'new-match':
        newMatch();
        break;

      case 'resign':
        resign(payload.side);
        break;

      default:
        console.warn(`${LOG} unhandled HUD command "${name}"`, payload);
    }
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Frame loop — the single rAF in the application
   * ──────────────────────────────────────────────────────────────────────── */

  function frame(now) {
    if (!clock.running) return;
    clock.raf = requestAnimationFrame(frame);

    // One dt for everybody. Clamped so an alt-tab stall cannot teleport the
    // animation queue or fling the camera on the first frame back.
    const raw = (now - clock.last) / 1000;
    clock.last = now;
    const dt = Number.isFinite(raw) ? Math.min(Math.max(raw, 0), 0.1) : 1 / 60;

    // Simulate, then draw — so the frame shows the state we just computed.
    try {
      queue.update(dt);
    } catch (err) {
      console.error(`${LOG} animation queue threw`, err);
    }
    try {
      fx.update(dt);
    } catch (err) {
      console.error(`${LOG} fx threw`, err);
    }
    try {
      stage.render(dt);
    } catch (err) {
      console.error(`${LOG} render threw`, err);
    }

    // The match driver rides the same clock; pump() guards its own re-entry.
    if (now >= driver.nextAt) {
      driver.nextAt = Infinity;      // pump() re-arms
      pump();
    }
  }

  clock.last = performance.now();
  clock.raf = requestAnimationFrame(frame);

  /* ────────────────────────────────────────────────────────────────────────
   * Teardown
   * ──────────────────────────────────────────────────────────────────────── */

  function dispose() {
    if (driver.disposed) return;
    driver.disposed = true;
    clock.running = false;
    if (clock.raf) cancelAnimationFrame(clock.raf);
    parkPump();
    try { api.stopPolling(); } catch { /* ignore */ }
    clearCheckHalo();
    queue.dispose();
    hud.destroy();
    fx.dispose();
    pieces.dispose();
    board.dispose();
    stage.dispose();
  }

  window.addEventListener('pagehide', dispose, { once: true });

  /* ────────────────────────────────────────────────────────────────────────
   * Go live
   * ──────────────────────────────────────────────────────────────────────── */

  Boot.setProgress(90, 'ACQUIRING MATCH');
  driver.playing = config.autoplay;

  const ok = await selectInitialMatch();

  Boot.setProgress(100, ok ? 'ENGAGED' : 'STANDING BY');
  Boot.done();

  if (!ok) {
    hud.setStatus('NO MATCH LOADED');
  }

  console.info(`${LOG} ready — models: white=${modelSource.white} black=${modelSource.black}, `
    + `api=${api.isOffline ? 'offline demo' : api.baseUrl || 'same-origin'}, `
    + `quality=${stage.quality}`);

  APP = {
    stage, board, pieces, fx, queue, api, gameState, hud,
    driver, config, dispose,
    /** Force a reconciliation against the authoritative FEN. */
    verify: verifyAgainstFEN,
    loadMatch,
    newMatch,
  };
  return APP;
}

/**
 * The running application, once {@link boot} has resolved.
 * @returns {object|null}
 */
export function getApp() {
  return APP;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Entry point
 * ══════════════════════════════════════════════════════════════════════════ */

boot()
  .then((app) => {
    if (app && typeof window !== 'undefined') {
      // A single, clearly-named debugging handle. Nothing depends on it.
      window.BattleChess = app;
    }
  })
  .catch((err) => {
    const detail = err && err.stack ? err.stack : String(err);
    console.error(`${LOG} fatal during boot`, err);
    Boot.fail('BattleChess failed to start.', detail);
  });
