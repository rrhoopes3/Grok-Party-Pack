/**
 * hud.js — BattleChess heads-up display.
 * ---------------------------------------------------------------------------
 * Presentation only. This module NEVER imports scene / pieces / animation /
 * board / gamestate, and never touches WebGL. Everything the operator can do
 * leaves through `onCommand(name, payload)`; everything the operator can see
 * arrives through `update(matchPayload)` and friends.
 *
 *   import { createHUD } from './hud.js';
 *
 *   const hud = createHUD({
 *     root: document.getElementById('bc-hud'),
 *     api,                                  // optional; only duck-typed
 *     onCommand(name, payload) { ... }
 *   });
 *
 * Returned surface
 * ---------------------------------------------------------------------------
 *   update(matchPayload)     full re-sync from an /api/chess/<id> payload
 *                            (null / undefined => "no match loaded")
 *   setStatus(text)          sticky note appended to the status line;
 *                            falsy clears it. Never hides the computed status.
 *   pushCommentary(entry)    append one judge beat ({text, model, ...} or a
 *                            bare string). De-duplicated against update().
 *   setLoading(pct, label)   drive the boot veil. pct >= 100 or null => hide.
 *   showError(msg, detail)   raise the fatal error panel.
 *   setBusy(flag)            true while the animation queue is running;
 *                            gates STEP so the board cannot desync.
 *   destroy()                remove every node, listener and timer.
 *
 * Commands emitted through onCommand(name, payload)
 * ---------------------------------------------------------------------------
 *   'play'                {}                        start auto-stepping
 *   'pause'               {}                        stop auto-stepping
 *   'step'                {}                        advance exactly one ply
 *   'skip'                {}                        flush animations to final state
 *   'speed'               { value: 0.5|1|2|4 }      animation speed multiplier
 *   'camera'              { preset }                'side'|'white'|'black'|'top'|'orbit'
 *   'quality'             { value: 'high'|'low' }   bloom + shadows on/off
 *   'new-match'           {}                        create a match
 *   'load-match'          { id }                    switch to an existing match
 *   'resign'              { side: 'white'|'black' } confirmed resignation
 *
 * Unknown commands are safe to ignore — the HUD updates its own visual state
 * optimistically and re-syncs from the next update() payload.
 *
 * Keyboard: Space play/pause · Right-arrow step · 1..4 speed · C cycle camera
 *           · Q quality · Esc collapse expanded ply.
 */

/* ── Constants ───────────────────────────────────────────────────────── */

const PIECE_VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };

/** Outline glyphs read as Starfleet, solid glyphs as Imperium. */
const GLYPH = {
  white: { k: '♔', q: '♕', r: '♖', b: '♗', n: '♘', p: '♙' },
  black: { k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' },
};

const PIECE_NAME = {
  p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen', k: 'king',
};

const FACTION = {
  white: { label: 'Starfleet', tag: 'WHITE // FEDERATION', skin: 'bc-fed' },
  black: { label: 'Imperium',  tag: 'BLACK // IMPERIUM',   skin: 'bc-imp' },
};

const SPEEDS = [
  { value: 0.5, label: '0.5×' },
  { value: 1,   label: '1×' },
  { value: 2,   label: '2×' },
  { value: 4,   label: '4×' },
];

const CAMERAS = [
  { preset: 'side',  label: 'SIDE',  title: 'Cinematic side view' },
  { preset: 'white', label: 'WHT',   title: 'Starfleet point of view' },
  { preset: 'black', label: 'BLK',   title: 'Imperium point of view' },
  { preset: 'top',   label: 'TOP',   title: 'Top-down tactical view' },
  { preset: 'orbit', label: 'ORB',   title: 'Slow cinematic orbit' },
];

const QUALITIES = [
  { value: 'high', label: 'HIGH', title: 'Bloom + soft shadows' },
  { value: 'low',  label: 'LOW',  title: 'No bloom, no shadows — integrated GPUs' },
];

/* ── Small helpers ───────────────────────────────────────────────────── */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clearNode(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
}

function fmtTokens(n) {
  const v = Number(n) || 0;
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'k';
  return String(Math.round(v));
}

function fmtCost(n) {
  const v = Number(n) || 0;
  if (v === 0) return '$0.00';
  if (v < 0.01) return '$' + v.toFixed(4);
  if (v < 1) return '$' + v.toFixed(3);
  return '$' + v.toFixed(2);
}

function fmtMs(n) {
  const v = Number(n) || 0;
  if (v <= 0) return '—';
  if (v >= 10000) return (v / 1000).toFixed(1) + 's';
  if (v >= 1000) return (v / 1000).toFixed(2) + 's';
  return Math.round(v) + 'ms';
}

function shortModel(name) {
  if (!name) return '';
  return String(name).replace(/^(openai|anthropic|xai|google|meta|mistral)\//i, '');
}

/** Material totals straight off the FEN placement field — survives promotions,
 *  which a captures[]-derived count does not. */
function materialFromFEN(fen) {
  if (typeof fen !== 'string' || !fen) return null;
  const placement = fen.split(' ')[0];
  if (!placement) return null;
  let white = 0;
  let black = 0;
  for (let i = 0; i < placement.length; i++) {
    const ch = placement[i];
    const lower = ch.toLowerCase();
    const value = PIECE_VALUE[lower];
    if (!value) continue;
    if (ch === lower) black += value;
    else white += value;
  }
  return { white, black, diff: white - black };
}

/** Fallback when there is no FEN: value of everything each side has taken. */
function materialFromCaptures(captures) {
  let white = 0;
  let black = 0;
  for (const cap of captures) {
    const value = PIECE_VALUE[String(cap.piece_symbol || '').toLowerCase()] || 0;
    if (cap.by === 'white') white += value;
    else if (cap.by === 'black') black += value;
  }
  return { white, black, diff: white - black };
}

function commentaryKey(entry, index) {
  if (!entry) return 'c' + index;
  const text = String(entry.text || '');
  const at = entry.after_move_n != null ? entry.after_move_n : (entry.round_num != null ? 'r' + entry.round_num : index);
  return at + '|' + text.slice(0, 64);
}

function isAtBottom(node, slack = 28) {
  if (!node) return true;
  return node.scrollHeight - node.scrollTop - node.clientHeight <= slack;
}

/* ── Factory ─────────────────────────────────────────────────────────── */

export function createHUD(options = {}) {
  const doc = document;
  const onCommand = typeof options.onCommand === 'function' ? options.onCommand : () => {};
  const api = options.api || null;

  let root = options.root || doc.getElementById('bc-hud');
  if (!root) {
    root = el('div', 'bc-hud');
    root.id = 'bc-hud';
    doc.body.appendChild(root);
  }
  root.classList.add('bc-hud');
  clearNode(root);

  const abort = new AbortController();
  const listen = { signal: abort.signal };
  const timers = new Set();
  let destroyed = false;

  function later(fn, ms) {
    const id = setTimeout(() => { timers.delete(id); if (!destroyed) fn(); }, ms);
    timers.add(id);
    return id;
  }

  const state = {
    match: null,
    matchId: null,
    plyCount: -1,
    note: '',
    playing: false,
    busy: false,
    speed: 1,
    camera: 'side',
    quality: 'high',
    expanded: null,          // ply index currently showing its `thinking`
    beatKeys: new Set(),
    beatCount: 0,
  };

  function emit(name, payload) {
    try { onCommand(name, payload || {}); }
    catch (err) { console.error('[hud] command handler threw for "' + name + '"', err); }
  }

  /* ── Build: status line ───────────────────────────────────────────── */

  const statusBar = el('div', 'bc-status');
  statusBar.id = 'bc-status';
  statusBar.setAttribute('role', 'status');
  statusBar.setAttribute('aria-live', 'polite');

  const statusBrand = el('span', 'bc-status__brand');
  statusBrand.append(doc.createTextNode('BATTLE'), el('em', null, 'CHESS'));
  const statusText = el('span', 'bc-status__text', 'NO MATCH LOADED');
  const statusChips = el('span', 'bc-status__chips');
  statusBar.append(statusBrand, statusText, statusChips);
  root.appendChild(statusBar);

  /* ── Build: rosters ───────────────────────────────────────────────── */

  function buildRoster(side) {
    const meta = FACTION[side];
    const panel = el('section', 'bc-panel ' + meta.skin + ' bc-roster bc-roster--' + side);
    panel.id = 'bc-roster-' + side;

    const head = el('div', 'bc-panel__head');
    head.append(el('h2', 'bc-panel__title', meta.label), el('span', 'bc-panel__tag', meta.tag));

    const body = el('div', 'bc-panel__body');
    const model = el('p', 'bc-roster__model is-empty', 'no model assigned');
    const rack = el('div', 'bc-rack');
    rack.setAttribute('aria-label', meta.label + ' captured pieces');

    const stats = el('div', 'bc-stats');
    const cells = {};
    for (const key of ['material', 'tokens', 'spend']) {
      const cell = el('div', 'bc-stat');
      const k = el('span', 'bc-stat__k', key === 'spend' ? 'SPEND' : key.toUpperCase());
      const v = el('span', 'bc-stat__v', '—');
      cell.append(k, v);
      stats.appendChild(cell);
      cells[key] = v;
    }

    body.append(model, rack, stats);
    panel.append(head, body);
    root.appendChild(panel);
    return { panel, model, rack, cells };
  }

  const rosters = { white: buildRoster('white'), black: buildRoster('black') };

  /* ── Build: move log ──────────────────────────────────────────────── */

  const logPanel = el('section', 'bc-panel bc-fed bc-log');
  logPanel.id = 'bc-log';
  const logHead = el('div', 'bc-panel__head');
  const logCount = el('span', 'bc-panel__tag', '0 PLIES');
  logHead.append(el('h2', 'bc-panel__title', 'Move Log'), logCount);
  const logBody = el('div', 'bc-panel__body bc-scroll');
  logBody.id = 'bc-log-body';
  const logList = el('ol', 'bc-plies');
  const logEmpty = el('div', 'bc-empty', 'no plies recorded');
  logBody.append(logList, logEmpty);
  logPanel.append(logHead, logBody);
  root.appendChild(logPanel);

  let logStick = true;
  logBody.addEventListener('scroll', () => { logStick = isAtBottom(logBody); }, listen);

  /* ── Build: judge commentary ──────────────────────────────────────── */

  const beatPanel = el('section', 'bc-panel bc-imp bc-commentary');
  beatPanel.id = 'bc-commentary';
  const beatHead = el('div', 'bc-panel__head');
  const beatTag = el('span', 'bc-panel__tag', 'JUDGE OFFLINE');
  beatHead.append(el('h2', 'bc-panel__title', 'Commentary'), beatTag);
  const beatBody = el('div', 'bc-panel__body bc-scroll');
  beatBody.id = 'bc-commentary-body';
  beatBody.setAttribute('aria-live', 'polite');
  const beatEmpty = el('div', 'bc-empty', 'the judge has not spoken');
  beatBody.appendChild(beatEmpty);
  beatPanel.append(beatHead, beatBody);
  root.appendChild(beatPanel);

  let beatStick = true;
  beatBody.addEventListener('scroll', () => { beatStick = isAtBottom(beatBody); }, listen);

  /* ── Build: control dock ──────────────────────────────────────────── */

  const dock = el('div', 'bc-dock');
  dock.id = 'bc-dock';
  root.appendChild(dock);

  function group(labelText, extraClass) {
    const g = el('div', 'bc-group' + (extraClass ? ' ' + extraClass : ''));
    if (labelText) g.appendChild(el('span', 'bc-group__label', labelText));
    dock.appendChild(g);
    return g;
  }

  function button(parent, text, className, title) {
    const b = el('button', 'bc-btn' + (className ? ' ' + className : ''), text);
    b.type = 'button';
    if (title) b.title = title;
    parent.appendChild(b);
    return b;
  }

  /* Transport */
  const gTransport = group(null);
  const btnPlay = button(gTransport, 'PLAY', 'bc-btn--primary', 'Auto-step the match (Space)');
  btnPlay.id = 'bc-btn-play';
  const btnStep = button(gTransport, 'STEP', null, 'Advance one ply (Right arrow)');
  btnStep.id = 'bc-btn-step';
  const btnSkip = button(gTransport, 'SKIP', 'bc-btn--icon', 'Snap the animation queue to its final state');
  btnSkip.id = 'bc-btn-skip';

  btnPlay.addEventListener('click', () => {
    setPlaying(!state.playing);
    emit(state.playing ? 'play' : 'pause');
  }, listen);
  btnStep.addEventListener('click', () => { if (!btnStep.disabled) emit('step'); }, listen);
  btnSkip.addEventListener('click', () => emit('skip'), listen);

  /* Speed */
  const gSpeed = group('SPEED', 'bc-group--seg');
  gSpeed.id = 'bc-speed';
  const speedButtons = SPEEDS.map((s) => {
    const b = button(gSpeed, s.label, 'bc-btn--seg', 'Animation speed ' + s.label);
    b.dataset.value = String(s.value);
    b.addEventListener('click', () => { setSpeed(s.value); emit('speed', { value: s.value }); }, listen);
    return b;
  });

  /* Camera */
  const gCamera = group('CAM', 'bc-group--seg');
  gCamera.id = 'bc-camera';
  const cameraButtons = CAMERAS.map((c) => {
    const b = button(gCamera, c.label, 'bc-btn--seg', c.title);
    b.dataset.preset = c.preset;
    b.addEventListener('click', () => { setCamera(c.preset); emit('camera', { preset: c.preset }); }, listen);
    return b;
  });

  /* Quality */
  const gQuality = group('FX', 'bc-group--seg');
  gQuality.id = 'bc-quality';
  const qualityButtons = QUALITIES.map((q) => {
    const b = button(gQuality, q.label, 'bc-btn--seg', q.title);
    b.dataset.value = q.value;
    b.addEventListener('click', () => { setQuality(q.value); emit('quality', { value: q.value }); }, listen);
    return b;
  });

  /* Match management */
  const gMatch = group('MATCH');
  const matchSelect = el('select', 'bc-select');
  matchSelect.id = 'bc-match-select';
  matchSelect.title = 'Switch to another match on the server';
  matchSelect.hidden = true;
  gMatch.appendChild(matchSelect);
  matchSelect.addEventListener('change', () => {
    if (matchSelect.value) emit('load-match', { id: matchSelect.value });
  }, listen);

  const btnNew = button(gMatch, 'NEW', null, 'Create a fresh match');
  btnNew.id = 'bc-btn-new';
  btnNew.addEventListener('click', () => emit('new-match'), listen);

  const gResign = group('RESIGN');
  const resignButtons = {};
  for (const side of ['white', 'black']) {
    const label = side === 'white' ? 'WHT' : 'BLK';
    const b = button(gResign, label, side === 'black' ? 'bc-btn--imp bc-btn--danger' : 'bc-btn--danger',
                     'Resign on behalf of ' + FACTION[side].label + ' (click twice to confirm)');
    b.dataset.side = side;
    b.disabled = true;
    resignButtons[side] = b;
    b.addEventListener('click', () => armResign(side), listen);
  }

  let armedSide = null;
  let armTimer = 0;

  function disarmResign() {
    armedSide = null;
    if (armTimer) { clearTimeout(armTimer); timers.delete(armTimer); armTimer = 0; }
    for (const side of ['white', 'black']) {
      const b = resignButtons[side];
      b.classList.remove('is-armed');
      b.textContent = side === 'white' ? 'WHT' : 'BLK';
    }
  }

  function armResign(side) {
    if (armedSide === side) {
      disarmResign();
      emit('resign', { side });
      return;
    }
    disarmResign();
    armedSide = side;
    const b = resignButtons[side];
    b.classList.add('is-armed');
    b.textContent = 'SURE?';
    armTimer = later(disarmResign, 3200);
  }

  /* ── Optimistic control state ─────────────────────────────────────── */

  function markSegment(buttons, matchFn) {
    for (const b of buttons) b.classList.toggle('is-on', matchFn(b));
  }

  function setPlaying(flag) {
    state.playing = !!flag;
    btnPlay.textContent = state.playing ? 'PAUSE' : 'PLAY';
    btnPlay.classList.toggle('is-on', state.playing);
    btnPlay.setAttribute('aria-pressed', state.playing ? 'true' : 'false');
  }

  function setSpeed(value) {
    state.speed = Number(value) || 1;
    markSegment(speedButtons, (b) => Number(b.dataset.value) === state.speed);
  }

  function setCamera(preset) {
    state.camera = preset;
    markSegment(cameraButtons, (b) => b.dataset.preset === state.camera);
  }

  function setQuality(value) {
    state.quality = value;
    markSegment(qualityButtons, (b) => b.dataset.value === state.quality);
  }

  setPlaying(false);
  setSpeed(1);
  setCamera('side');
  setQuality(new URLSearchParams(location.search).get('quality') === 'low' ? 'low' : 'high');

  /* ── Keyboard ─────────────────────────────────────────────────────── */

  doc.addEventListener('keydown', (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target && target !== doc.body) {
      const tag = target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) return;
    }
    switch (event.key) {
      case ' ':
      case 'Spacebar':
        event.preventDefault();
        setPlaying(!state.playing);
        emit(state.playing ? 'play' : 'pause');
        break;
      case 'ArrowRight':
        event.preventDefault();
        if (!btnStep.disabled) emit('step');
        break;
      case '1': case '2': case '3': case '4': {
        const s = SPEEDS[Number(event.key) - 1];
        if (s) { setSpeed(s.value); emit('speed', { value: s.value }); }
        break;
      }
      case 'c': case 'C': {
        const idx = CAMERAS.findIndex((c) => c.preset === state.camera);
        const next = CAMERAS[(idx + 1) % CAMERAS.length];
        setCamera(next.preset);
        emit('camera', { preset: next.preset });
        break;
      }
      case 'q': case 'Q': {
        const next = state.quality === 'high' ? 'low' : 'high';
        setQuality(next);
        emit('quality', { value: next });
        break;
      }
      case 'Escape':
        if (state.expanded !== null) { state.expanded = null; renderLog(state.match, true); }
        disarmResign();
        break;
      default:
        break;
    }
  }, listen);

  /* ── Boot-veil / error bridge ─────────────────────────────────────── */

  function boot() {
    return (typeof window !== 'undefined' && window.BattleChessBoot) || null;
  }

  function setLoading(pct, label) {
    const b = boot();
    if (b) {
      if (pct === null || pct === undefined || pct === false) {
        if (typeof label === 'string' && label) b.setProgress(undefined, label);
        b.done();
      } else {
        b.setProgress(pct, label);
      }
      return;
    }
    // No boot bridge (hud used standalone): drive the DOM directly.
    const veil = doc.getElementById('bc-loading');
    const bar = doc.getElementById('bc-loading-bar');
    const num = doc.getElementById('bc-loading-pct');
    const lbl = doc.getElementById('bc-loading-label');
    if (typeof label === 'string' && label && lbl) lbl.textContent = label;
    const done = pct === null || pct === undefined || pct === false || Number(pct) >= 100;
    const n = Math.max(0, Math.min(100, Number(pct) || 0));
    if (bar) bar.style.width = n + '%';
    if (num) num.textContent = Math.round(n) + '%';
    if (veil && done) { veil.classList.add('is-gone'); later(() => { veil.hidden = true; }, 700); }
  }

  function showError(message, detail) {
    const text = String(message == null ? 'Unknown fault.' : message);
    const b = boot();
    if (b) { b.fail(text, detail); return; }
    const panel = doc.getElementById('bc-error');
    const msg = doc.getElementById('bc-error-message');
    const det = doc.getElementById('bc-error-detail');
    if (msg) msg.textContent = text;
    if (det) { det.textContent = detail == null ? '' : String(detail); det.hidden = !detail; }
    if (panel) panel.hidden = false;
    doc.body.classList.add('bc-has-error');
  }

  function setBusy(flag) {
    state.busy = !!flag;
    root.classList.toggle('is-busy', state.busy);
    refreshGating();
  }

  /* ── Rendering ────────────────────────────────────────────────────── */

  function refreshGating() {
    const match = state.match;
    const over = !!match && (match.status === 'finished' || match.status === 'over' || !!match.result);
    btnStep.disabled = state.busy || !match || over;
    btnPlay.disabled = !match || over;
    for (const side of ['white', 'black']) {
      resignButtons[side].disabled = !match || over;
    }
    if (over && state.playing) setPlaying(false);
    if ((!match || over) && armedSide) disarmResign();
  }

  function chip(text, kind) {
    return el('span', 'bc-chip bc-chip--' + kind, text);
  }

  function renderStatus(match) {
    clearNode(statusChips);

    if (!match) {
      statusText.textContent = 'NO MATCH LOADED';
      statusBar.removeAttribute('data-turn');
      if (state.note) statusChips.appendChild(el('span', 'bc-chip bc-chip--demo', state.note));
      return;
    }

    const over = match.status === 'finished' || match.status === 'over' || !!match.result;
    const turn = match.turn === 'white' || match.turn === 'black' ? match.turn : null;
    const plies = Number(match.halfmove_count != null
      ? match.halfmove_count
      : (Array.isArray(match.moves) ? match.moves.length : 0)) || 0;
    const full = Math.floor(plies / 2) + 1;   // plies played -> move number in play

    let text;
    if (over) {
      const result = match.result ? String(match.result) : 'game over';
      const reason = match.reason ? ' · ' + match.reason : '';
      text = result.toUpperCase() + reason.toUpperCase();
    } else if (turn) {
      text = 'MOVE ' + full + ' · ' + FACTION[turn].label.toUpperCase() + ' TO PLAY';
    } else {
      text = (match.status ? String(match.status).toUpperCase() : 'STANDING BY');
    }
    statusText.textContent = text + '  ·  ' + plies + ' PLIES';

    if (turn && !over) statusBar.setAttribute('data-turn', turn);
    else statusBar.removeAttribute('data-turn');

    if (over) statusChips.appendChild(chip(match.status === 'finished' ? 'COMPLETE' : 'ENDED', 'done'));
    else statusChips.appendChild(chip('LIVE', 'live'));

    if (match.in_check && !over) statusChips.appendChild(chip('CHECK', 'check'));
    if (over && /#|checkmate/i.test(String(match.reason || ''))) statusChips.appendChild(chip('CHECKMATE', 'mate'));
    if (match.has_house_moves) statusChips.appendChild(chip('HOUSE MOVES', 'house'));
    if (match.protocol_loss_by) {
      statusChips.appendChild(chip('FORFEIT: ' + String(match.protocol_loss_by).toUpperCase(), 'forfeit'));
    }
    if (match.demo || match.offline) statusChips.appendChild(chip('OFFLINE DEMO', 'demo'));
    if (state.note) statusChips.appendChild(chip(state.note, 'demo'));
  }

  function renderRoster(side, match) {
    const view = rosters[side];
    const enemy = side === 'white' ? 'black' : 'white';

    if (!match) {
      view.model.textContent = 'no model assigned';
      view.model.classList.add('is-empty');
      clearNode(view.rack);
      view.rack.appendChild(el('span', 'bc-rack__empty', 'no captures'));
      view.cells.material.textContent = '—';
      view.cells.material.className = 'bc-stat__v';
      view.cells.tokens.textContent = '—';
      view.cells.spend.textContent = '—';
      view.panel.classList.remove('is-active');
      return;
    }

    const modelName = shortModel(match[side + '_model']);
    view.model.textContent = modelName || 'unassigned';
    view.model.classList.toggle('is-empty', !modelName);
    view.model.title = String(match[side + '_model'] || '');

    /* Captured rack: every enemy piece this side has removed. */
    const captures = Array.isArray(match.captures) ? match.captures : [];
    const taken = captures
      .filter((c) => c && c.by === side && c.piece_symbol)
      .map((c) => String(c.piece_symbol))
      .sort((a, b) => (PIECE_VALUE[b.toLowerCase()] || 0) - (PIECE_VALUE[a.toLowerCase()] || 0));

    clearNode(view.rack);
    if (!taken.length) {
      view.rack.appendChild(el('span', 'bc-rack__empty', 'no captures'));
    } else {
      for (const symbol of taken) {
        const lower = symbol.toLowerCase();
        const glyph = GLYPH[enemy][lower] || '?';
        const span = el('span', 'bc-rack__piece bc-rack__piece--' + (enemy === 'white' ? 'fed' : 'imp'), glyph);
        span.title = FACTION[enemy].label + ' ' + (PIECE_NAME[lower] || lower);
        view.rack.appendChild(span);
      }
    }

    /* Material advantage: FEN first (promotion-safe), captures as fallback. */
    const material = materialFromFEN(match.fen) || materialFromCaptures(captures);
    const advantage = side === 'white' ? material.diff : -material.diff;
    const cell = view.cells.material;
    cell.className = 'bc-stat__v ' + (advantage > 0 ? 'is-up' : advantage < 0 ? 'is-down' : 'is-flat');
    cell.textContent = advantage > 0 ? '+' + advantage : (advantage < 0 ? '−' + Math.abs(advantage) : 'EVEN');
    cell.title = 'Material on the board: ' + material.white + ' vs ' + material.black;

    /* Token spend. */
    const tokens = (match.tokens && match.tokens[side]) || null;
    const tin = tokens ? Number(tokens.in) || 0 : 0;
    const tout = tokens ? Number(tokens.out) || 0 : 0;
    view.cells.tokens.textContent = tokens ? fmtTokens(tin + tout) : '—';
    view.cells.tokens.title = 'in ' + tin.toLocaleString() + '  ·  out ' + tout.toLocaleString();
    view.cells.spend.textContent = tokens ? fmtCost(tokens.cost_usd) : '—';
    view.cells.spend.title = tokens ? 'Billed ' + fmtCost(tokens.cost_usd) + ' so far' : '';

    const over = match.status === 'finished' || match.status === 'over' || !!match.result;
    view.panel.classList.toggle('is-active', !over && match.turn === side);
  }

  function plyLabels(moves) {
    const labels = [];
    let full = 1;
    for (const mv of moves) {
      if (mv && mv.side === 'black') { labels.push(full + '…'); full++; }
      else labels.push(full + '.');
    }
    return labels;
  }

  function renderLog(match, force) {
    const moves = match && Array.isArray(match.moves) ? match.moves : [];
    const sameMatch = match && match.id === state.matchId;
    if (!force && sameMatch && moves.length === state.plyCount) return;

    const stick = logStick;
    clearNode(logList);
    logCount.textContent = moves.length + (moves.length === 1 ? ' PLY' : ' PLIES');
    logEmpty.hidden = moves.length > 0;

    const labels = plyLabels(moves);

    moves.forEach((mv, index) => {
      const side = mv.side === 'black' ? 'black' : 'white';
      const source = mv.source || 'model';
      const house = source !== 'model';

      const item = el('li', 'bc-ply-item');

      const row = el('button', 'bc-ply bc-ply--' + side + (house ? ' bc-ply--house' : ''));
      row.type = 'button';
      if (index === moves.length - 1) row.classList.add('is-latest');

      row.appendChild(el('span', 'bc-ply__n', labels[index]));
      row.appendChild(el('span', 'bc-ply__side'));
      row.appendChild(el('span', 'bc-ply__san', mv.san || mv.uci || '??'));

      const flags = el('span', 'bc-ply__flags');
      if (house) {
        const isForfeit = source === 'forfeit';
        const flag = el('span', 'bc-flag ' + (isForfeit ? 'bc-flag--forfeit' : 'bc-flag--house'),
                        isForfeit ? 'FORFEIT' : (source === 'adjudicated' ? 'HOUSE' : String(source).toUpperCase()));
        flag.title = 'This ply did not come from the model — source: ' + source +
                     '. House moves are logged as an integrity signal.';
        flags.appendChild(flag);
      }
      if (mv.forced) {
        const f = el('span', 'bc-flag bc-flag--forced', 'FORCED');
        f.title = 'Only one legal move was available.';
        flags.appendChild(f);
      }
      if (Number(mv.attempts) > 1) {
        const f = el('span', 'bc-flag bc-flag--retry', '×' + mv.attempts);
        f.title = mv.attempts + ' attempts before a legal move was produced.';
        flags.appendChild(f);
      }
      row.appendChild(flags);

      const summary = [
        'ply ' + (mv.n != null ? mv.n : index + 1),
        FACTION[side].label,
        fmtMs(mv.ms),
        'src:' + source,
      ];
      if (mv.input_tokens || mv.output_tokens) {
        summary.push('tok ' + fmtTokens(mv.input_tokens) + '/' + fmtTokens(mv.output_tokens));
      }
      if (mv.cost_usd) summary.push(fmtCost(mv.cost_usd));
      row.title = summary.join('  ·  ') + (mv.thinking ? '  ·  click for reasoning' : '');

      const open = state.expanded === index;
      row.setAttribute('aria-expanded', open ? 'true' : 'false');
      row.addEventListener('click', () => {
        state.expanded = state.expanded === index ? null : index;
        renderLog(state.match, true);
      }, listen);

      item.appendChild(row);

      if (open) {
        item.appendChild(el('div', 'bc-ply__meta', summary.join('  ·  ')));
        const thinking = String(mv.thinking || '').trim();
        item.appendChild(el('div', 'bc-ply__think', thinking || 'No reasoning was recorded for this ply.'));
      }

      logList.appendChild(item);
    });

    if (stick) logBody.scrollTop = logBody.scrollHeight;
  }

  function appendBeat(entry, latest) {
    const text = typeof entry === 'string' ? entry : String((entry && entry.text) || '').trim();
    if (!text) return false;

    const model = (entry && entry.model) || (state.match && state.match.judge_model) || 'judge';
    const after = entry && entry.after_move_n != null ? entry.after_move_n : null;
    const round = entry && entry.round_num != null ? entry.round_num : null;

    const beat = el('article', 'bc-beat' + (latest ? ' is-latest' : ''));
    const who = el('div', 'bc-beat__who');
    who.appendChild(el('span', 'bc-beat__name', shortModel(model).toUpperCase()));
    const at = [];
    if (round != null) at.push('RD ' + round);
    if (after != null) at.push('PLY ' + after);
    if (entry && entry.ms) at.push(fmtMs(entry.ms));
    who.appendChild(el('span', 'bc-beat__at', at.join(' · ')));
    beat.append(who, el('p', 'bc-beat__text', text));

    const stick = beatStick;
    beatEmpty.hidden = true;
    for (const prior of beatBody.querySelectorAll('.bc-beat.is-latest')) prior.classList.remove('is-latest');
    beatBody.appendChild(beat);
    state.beatCount++;
    beatTag.textContent = state.beatCount + (state.beatCount === 1 ? ' BEAT' : ' BEATS');
    if (stick) beatBody.scrollTop = beatBody.scrollHeight;
    return true;
  }

  function renderCommentary(match, reset) {
    if (reset) {
      clearNode(beatBody);
      beatBody.appendChild(beatEmpty);
      beatEmpty.hidden = true;
      state.beatKeys.clear();
      state.beatCount = 0;
      beatTag.textContent = 'JUDGE OFFLINE';
      beatStick = true;
    }

    const list = match && Array.isArray(match.commentary) ? match.commentary : [];
    if (!list.length && !state.beatCount) {
      beatEmpty.hidden = false;
      beatTag.textContent = match && match.judge_model ? shortModel(match.judge_model).toUpperCase() : 'JUDGE OFFLINE';
      return;
    }

    list.forEach((entry, index) => {
      const key = commentaryKey(entry, index);
      if (state.beatKeys.has(key)) return;
      state.beatKeys.add(key);
      appendBeat(entry, index === list.length - 1);
    });

    if (!state.beatCount) beatEmpty.hidden = false;
  }

  /* ── Public: update ───────────────────────────────────────────────── */

  function update(payload) {
    if (destroyed) return;

    const match = payload && payload.match && payload.match.fen ? payload.match : payload;
    const valid = !!(match && typeof match === 'object');
    const next = valid ? match : null;

    const changedMatch = !next || next.id !== state.matchId;
    state.match = next;

    if (changedMatch) {
      state.expanded = null;
      state.plyCount = -1;
      logStick = true;
    }

    renderStatus(next);
    renderRoster('white', next);
    renderRoster('black', next);
    renderLog(next, changedMatch);
    renderCommentary(next, changedMatch);

    state.matchId = next ? next.id : null;
    state.plyCount = next && Array.isArray(next.moves) ? next.moves.length : 0;

    refreshGating();
    syncMatchSelect(next);
  }

  /* ── Public: status note ──────────────────────────────────────────── */

  function setStatus(text) {
    state.note = text ? String(text) : '';
    renderStatus(state.match);
  }

  /* ── Public: commentary push ──────────────────────────────────────── */

  function pushCommentary(entry) {
    if (destroyed || !entry) return;
    const key = commentaryKey(typeof entry === 'string' ? { text: entry } : entry, state.beatCount);
    if (state.beatKeys.has(key)) return;
    state.beatKeys.add(key);
    appendBeat(entry, true);
  }

  /* ── Optional: match picker (duck-typed against api.js) ───────────── */

  let matchListLoaded = false;

  function syncMatchSelect(match) {
    if (match && matchSelect.value !== match.id) {
      const hit = Array.from(matchSelect.options).some((o) => o.value === match.id);
      if (hit) matchSelect.value = match.id;
    }
    if (matchListLoaded) return;
    matchListLoaded = true;
    loadMatchList();
  }

  function loadMatchList() {
    if (!api) return;
    const candidates = ['listMatches', 'list', 'getMatches', 'fetchMatches', 'matches'];
    const name = candidates.find((n) => typeof api[n] === 'function');
    if (!name) return;

    let result;
    try { result = api[name](); }
    catch (err) { return; }

    Promise.resolve(result).then((data) => {
      if (destroyed) return;
      const list = Array.isArray(data) ? data : (data && Array.isArray(data.matches) ? data.matches : null);
      if (!list || !list.length) return;

      clearNode(matchSelect);
      for (const entry of list) {
        if (!entry || !entry.id) continue;
        const opt = el('option', null,
          shortModel(entry.white_model || '?') + ' vs ' + shortModel(entry.black_model || '?'));
        opt.value = entry.id;
        matchSelect.appendChild(opt);
      }
      if (matchSelect.options.length) {
        matchSelect.hidden = false;
        if (state.matchId) {
          const hit = Array.from(matchSelect.options).some((o) => o.value === state.matchId);
          if (hit) matchSelect.value = state.matchId;
        }
      }
    }).catch(() => { /* offline / demo mode: the picker simply stays hidden */ });
  }

  /* ── Public: destroy ──────────────────────────────────────────────── */

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    abort.abort();
    for (const id of timers) clearTimeout(id);
    timers.clear();
    root.classList.remove('is-busy');
    clearNode(root);
    state.match = null;
    state.beatKeys.clear();
  }

  /* ── First paint ─────────────────────────────────────────────────── */

  update(null);

  return {
    update,
    setStatus,
    pushCommentary,
    setLoading,
    showError,
    setBusy,
    destroy,
    /* Non-contract conveniences for the integrator / debugging. */
    root,
    nodes: {
      status: statusBar, rosterWhite: rosters.white.panel, rosterBlack: rosters.black.panel,
      log: logPanel, commentary: beatPanel, dock,
    },
  };
}
