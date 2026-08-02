/**
 * BattleChess — animation.js
 *
 * The move/capture/promotion animation queue (CONTRACT.md section 8).
 *
 * Rules this module lives by:
 *   - Events are processed strictly in order, one at a time.
 *   - update(deltaSeconds) is the ONLY place transforms are touched. Nothing
 *     here uses setTimeout/setInterval/requestAnimationFrame.
 *   - The render-side square map is updated the instant an event starts, so the
 *     book state never lags the FEN; the tween is purely cosmetic.
 *   - flush() snaps every pending event to its final state with no artifacts.
 *   - Every fx.js call is guarded: a missing, late or throwing FX module can
 *     never stop a piece from arriving on its square.
 *
 * Named exports: AnimationQueue, DURATIONS, and the easing functions.
 */

import * as THREE from 'three';
import { FACTIONS } from './pieces.js';
import * as BoardModule from './board.js';

const LOG = '[BattleChess/animation]';

/* ------------------------------------------------------------------ *
 * Tiny easing library (no dependencies)
 * ------------------------------------------------------------------ */

export const clamp01 = (t) => (t < 0 ? 0 : t > 1 ? 1 : t);
export const lerp = (a, b, t) => a + (b - a) * t;

export function easeOutCubic(t) {
  const x = clamp01(t);
  return 1 - Math.pow(1 - x, 3);
}

export function easeInCubic(t) {
  const x = clamp01(t);
  return x * x * x;
}

export function easeInOutQuad(t) {
  const x = clamp01(t);
  return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
}

export function easeOutBack(t, overshoot = 1.70158) {
  const x = clamp01(t);
  const c3 = overshoot + 1;
  const d = x - 1;
  return 1 + c3 * d * d * d + overshoot * d * d;
}

export function easeOutElastic(t) {
  const x = clamp01(t);
  if (x === 0 || x === 1) return x;
  const c4 = (2 * Math.PI) / 3;
  return Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * c4) + 1;
}

/** Smooth 0->1->0 window, used for one-off overshoot/impact pulses. */
export function pulse(t) {
  return Math.sin(Math.PI * clamp01(t));
}

export const Easing = {
  clamp01, lerp, easeOutCubic, easeInCubic, easeInOutQuad, easeOutBack, easeOutElastic, pulse,
};

/** Durations in seconds at 1x (CONTRACT.md section 8). */
export const DURATIONS = {
  move: 0.85,
  capture: 1.60,
  enpassant: 1.60,
  castle: 1.10,
  promotion: 1.40,
  check: 0.55,
};

/* ------------------------------------------------------------------ *
 * Board coordinates — board.js is authoritative, with a spec-identical
 * local fallback so animation can never strand a piece at the origin.
 * ------------------------------------------------------------------ */

const FILES = 'abcdefgh';

function localSquareToWorld(square) {
  const s = String(square || '').toLowerCase();
  const f = FILES.indexOf(s[0]);
  const r = Number.parseInt(s[1], 10) - 1;
  if (f < 0 || !Number.isFinite(r) || r < 0 || r > 7) return new THREE.Vector3();
  return new THREE.Vector3(f - 3.5, 0, 3.5 - r);
}

function worldOf(square) {
  const fn = BoardModule && typeof BoardModule.squareToWorld === 'function'
    ? BoardModule.squareToWorld
    : null;
  if (fn) {
    try {
      const v = fn(square);
      if (v && v.isVector3) return v.clone();
      if (v && Number.isFinite(v.x)) return new THREE.Vector3(v.x, v.y || 0, v.z);
    } catch (err) { /* fall through to local */ }
  }
  return localSquareToWorld(square);
}

/* ------------------------------------------------------------------ *
 * Small helpers
 * ------------------------------------------------------------------ */

function obj3dOf(piece) {
  if (!piece) return null;
  if (piece.isObject3D) return piece;
  return piece.object3D || piece.group || piece.mesh || null;
}

function meshOf(piece) {
  if (!piece) return null;
  if (piece.mesh) return piece.mesh;
  if (piece.isMesh) return piece;
  const root = obj3dOf(piece);
  if (!root || !root.traverse) return null;
  let found = null;
  root.traverse((c) => { if (!found && c.isMesh) found = c; });
  return found;
}

function pivotOf(piece) {
  if (piece && piece.pivot) return piece.pivot;
  return obj3dOf(piece);
}

function glowColorOf(piece, fallback = 0x9fd4ff) {
  const color = piece && piece.color ? piece.color : null;
  const faction = color && FACTIONS[color];
  return faction ? faction.glow.color : fallback;
}

/** Quadratic bezier arc from a to b whose apex sits `rise` above the endpoints. */
function makeArc(a, b, rise) {
  const ctrl = new THREE.Vector3(
    (a.x + b.x) * 0.5,
    Math.max(a.y, b.y) + rise * 2.0,
    (a.z + b.z) * 0.5,
  );
  return new THREE.QuadraticBezierCurve3(a.clone(), ctrl, b.clone());
}

function dirXZ(a, b) {
  const d = new THREE.Vector3(b.x - a.x, 0, b.z - a.z);
  const len = d.length();
  return len > 1e-6 ? d.divideScalar(len) : new THREE.Vector3(0, 0, -1);
}

/** Lean a piece into its direction of travel (outer transform only). */
function applyLean(obj, dir, amount) {
  if (!obj) return;
  obj.rotation.x = dir.z * amount;
  obj.rotation.z = -dir.x * amount;
}

function applySquash(obj, amount) {
  if (!obj) return;
  const a = Math.max(0, amount);
  obj.scale.set(1 + a * 0.6, 1 - a, 1 + a * 0.6);
}

function restPiece(piece, worldPos, scale = 1) {
  const obj = obj3dOf(piece);
  if (!obj) return;
  if (worldPos) obj.position.set(worldPos.x, 0, worldPos.z);
  obj.rotation.set(0, 0, 0);
  obj.scale.setScalar(scale);
  const pivot = pivotOf(piece);
  if (pivot && pivot !== obj) {
    pivot.position.set(0, 0, 0);
    pivot.rotation.set(0, piece && piece.facing != null ? piece.facing : pivot.rotation.y, 0);
  }
  if (piece && typeof piece.restoreMaterial === 'function') piece.restoreMaterial();
  obj.visible = true;
}

/** En passant: the victim pawn sits on the destination file, the origin rank. */
function enPassantSquare(event) {
  const explicit = event.capturedSquare
    || (event.extra && (event.extra.capturedSquare || event.extra.victimSquare));
  if (explicit) return String(explicit).toLowerCase();
  const to = String(event.to || '').toLowerCase();
  const from = String(event.from || '').toLowerCase();
  if (to.length < 2 || from.length < 2) return null;
  return `${to[0]}${from[1]}`;
}

/**
 * The type a promoting pawn turned *into*.
 *
 * gamestate.js is the authority here and puts it on `event.promotedTo`
 * ('q'|'r'|'b'|'n'), so that field is consulted first. `event.piece` is
 * deliberately NOT a fallback: gamestate documents it as the piece that
 * *departed*, which on a promotion is always the pawn — reading it renders a
 * second pawn on the back rank instead of the new officer. UCI carries the same
 * letter in its fifth character ('d7d8q') and is the last real source before we
 * assume a queen, which is what the overwhelming majority of promotions are.
 *
 * @param {object} event promotion event from diffPositions()
 * @returns {string} piece type letter or name; pieces.js normalises both.
 */
function promotedTypeOf(event) {
  const uci = event && typeof event.uci === 'string' ? event.uci : '';
  return event.promotedTo
    || event.promotion
    || (event.extra && (event.extra.promotion || event.extra.promoteTo))
    || (uci.length >= 5 ? uci[4] : null)
    || 'queen';
}

/* ------------------------------------------------------------------ *
 * AnimationQueue
 * ------------------------------------------------------------------ */

export class AnimationQueue {
  /**
   * @param {object} options
   * @param {object} options.pieces  piece manager from createPieceManager()
   * @param {object} [options.fx]    createFX() result (optional / late-bound)
   * @param {object} [options.scene] scene API exposing shake() (optional)
   * @param {object} [options.board] board API (optional; board.js is imported)
   */
  constructor(options = {}) {
    this.pieces = options.pieces || options.pieceManager || options.pieceMgr || null;
    this.fx = options.fx || null;
    this.scene = options.scene || options.sceneApi || null;
    this.board = options.board || options.boardApi || null;

    this._queue = [];
    this._current = null;
    this._speed = 1;
    this._maxStep = 0.25;         // clamp huge frame gaps (tab restore)
    this._completeListeners = new Set();
    this._idleListeners = new Set();
    this._wasBusy = false;
  }

  /** Late-bind collaborators (e.g. fx.js created after the queue). */
  setContext(ctx = {}) {
    if (ctx.pieces) this.pieces = ctx.pieces;
    if (ctx.fx) this.fx = ctx.fx;
    if (ctx.scene) this.scene = ctx.scene;
    if (ctx.board) this.board = ctx.board;
    return this;
  }

  /* -------- public API (contract) -------- */

  /**
   * Queue one event from diffPositions().
   * @returns {Promise<object>} resolves with the event when it has finished.
   */
  enqueue(event) {
    if (!event || typeof event !== 'object') return Promise.resolve(null);
    let resolve;
    const promise = new Promise((res) => { resolve = res; });
    this._queue.push({ event, resolve, anim: null, time: 0 });
    this._wasBusy = true;
    return promise;
  }

  /** Queue a list of events in order. */
  enqueueAll(events) {
    const list = Array.isArray(events) ? events : [];
    return Promise.all(list.map((e) => this.enqueue(e)));
  }

  /** Drive the queue. Called once per frame from the main loop. */
  update(deltaSeconds) {
    const dt = Math.min(Math.max(0, Number(deltaSeconds) || 0), this._maxStep);
    let remaining = dt * this._speed;
    let guard = 0;

    while (guard++ < 24) {
      if (!this._current && this._queue.length) this._beginNext();
      if (!this._current) break;

      const cur = this._current;
      const duration = cur.anim.duration;

      if (duration <= 0) { this._finishCurrent(); continue; }

      const step = Math.min(remaining, duration - cur.time);
      cur.time += step;
      remaining -= step;

      if (cur.time >= duration - 1e-6) {
        this._finishCurrent();
        if (remaining <= 1e-6) break;
      } else {
        this._safe(() => cur.anim.apply(cur.time / duration), 'apply');
        break;
      }
    }

    this._pumpIdle();
  }

  get isBusy() {
    return this._current !== null || this._queue.length > 0;
  }

  get pending() {
    return this._queue.length + (this._current ? 1 : 0);
  }

  get speed() { return this._speed; }

  /** Playback multiplier; 2 = twice as fast. */
  setSpeed(multiplier) {
    const m = Number(multiplier);
    this._speed = Number.isFinite(m) ? THREE.MathUtils.clamp(m, 0.1, 8) : 1;
    return this._speed;
  }

  /**
   * Snap everything — active and pending — to its final state, silently.
   * Leaves no half-scaled, half-faded or half-rotated pieces behind.
   */
  flush() {
    if (this._current) {
      const cur = this._current;
      this._current = null;
      this._safe(() => cur.anim.finish(), 'finish');
      this._notify(cur);
    }
    let guard = 0;
    while (this._queue.length && guard++ < 512) {
      const item = this._queue.shift();
      this._safe(() => { item.anim = this._build(item.event, true); }, 'build');
      if (!item.anim) item.anim = staticAnim(0);
      this._safe(() => item.anim.finish(), 'finish');
      this._notify(item);
    }
    this._pumpIdle();
    return this;
  }

  /** Drop pending events without applying them (use flush() normally). */
  clear() {
    for (const item of this._queue) item.resolve(item.event);
    this._queue.length = 0;
    if (this._current) {
      const cur = this._current;
      this._current = null;
      cur.resolve(cur.event);
    }
    this._pumpIdle();
    return this;
  }

  /**
   * Register a per-event completion callback.
   * @returns {function} unsubscribe
   */
  onComplete(callback) {
    if (typeof callback !== 'function') return () => {};
    this._completeListeners.add(callback);
    return () => this._completeListeners.delete(callback);
  }

  /** Fires when the queue drains from busy to idle. Returns unsubscribe. */
  onIdle(callback) {
    if (typeof callback !== 'function') return () => {};
    this._idleListeners.add(callback);
    return () => this._idleListeners.delete(callback);
  }

  dispose() {
    this.clear();
    this._completeListeners.clear();
    this._idleListeners.clear();
    this.pieces = null;
    this.fx = null;
    this.scene = null;
  }

  /* -------- internals -------- */

  _safe(fn, label) {
    try { return fn(); } catch (err) {
      console.warn(`${LOG} ${label} failed`, err);
      return null;
    }
  }

  _beginNext() {
    const item = this._queue.shift();
    if (!item) return;
    item.time = 0;
    this._safe(() => { item.anim = this._build(item.event, false); }, 'build');
    if (!item.anim) item.anim = staticAnim(0);
    this._current = item;
    this._safe(() => item.anim.apply(0), 'apply');
  }

  _finishCurrent() {
    const cur = this._current;
    this._current = null;
    if (!cur) return;
    this._safe(() => cur.anim.finish(), 'finish');
    this._notify(cur);
  }

  _notify(item) {
    item.resolve(item.event);
    for (const cb of this._completeListeners) {
      try { cb(item.event); } catch (err) { console.warn(`${LOG} onComplete listener threw`, err); }
    }
  }

  _pumpIdle() {
    const busy = this.isBusy;
    if (this._wasBusy && !busy) {
      this._wasBusy = false;
      for (const cb of this._idleListeners) {
        try { cb(); } catch (err) { console.warn(`${LOG} onIdle listener threw`, err); }
      }
    } else if (busy) {
      this._wasBusy = true;
    }
  }

  /* -------- collaborator access (all guarded) -------- */

  _fx(name, ...args) {
    const fx = this.fx;
    if (!fx) return null;
    const fn = fx[name];
    if (typeof fn !== 'function') return null;
    try { return fn.apply(fx, args); } catch (err) {
      console.warn(`${LOG} fx.${name}() failed`, err);
      return null;
    }
  }

  _shake(amount = 0.3, duration = 0.35) {
    const target = this.scene;
    if (!target) return;
    const fn = target.shake;
    if (typeof fn !== 'function') return;
    try { fn.call(target, amount, duration); } catch (err) {
      console.warn(`${LOG} scene.shake() failed`, err);
    }
  }

  _pieceAt(square) {
    const pm = this.pieces;
    if (!pm || typeof pm.getPiece !== 'function' || !square) return null;
    try { return pm.getPiece(square); } catch (err) { return null; }
  }

  /** Self-heal: the FEN says a piece is here but the scene lost it. */
  _ensurePiece(spec, square) {
    const pm = this.pieces;
    if (!pm || typeof pm.addPiece !== 'function' || !spec || !square) return null;
    console.warn(`${LOG} no piece on ${square} — recreating from event data.`);
    try { return pm.addPiece(spec.type, spec.color, square); } catch (err) {
      console.warn(`${LOG} addPiece failed`, err);
      return null;
    }
  }

  _rackTarget(piece) {
    const rack = this.pieces && this.pieces.capturedRack;
    if (!rack || !piece) return new THREE.Vector3(0, 0, 0);
    try {
      const v = typeof rack.reserve === 'function' ? rack.reserve(piece) : null;
      if (v && Number.isFinite(v.x)) return v.clone ? v.clone() : new THREE.Vector3(v.x, v.y, v.z);
    } catch (err) { /* fall through */ }
    return new THREE.Vector3(0, 0, 0);
  }

  _placeInRack(piece) {
    const rack = this.pieces && this.pieces.capturedRack;
    if (!rack || !piece) return;
    try {
      if (typeof rack.place === 'function') rack.place(piece);
      else if (typeof rack.add === 'function') rack.add(piece);
    } catch (err) { console.warn(`${LOG} rack placement failed`, err); }
  }

  _rackScale() {
    const rack = this.pieces && this.pieces.capturedRack;
    const s = rack && Number(rack.scale);
    return Number.isFinite(s) && s > 0 ? s : 0.55;
  }

  /** Detach a captured piece from the board map without snapping it away. */
  _detach(square, { toRack = true } = {}) {
    const pm = this.pieces;
    if (!pm || typeof pm.removePiece !== 'function' || !square) return null;
    try { return pm.removePiece(square, { toRack, snap: false }); } catch (err) {
      console.warn(`${LOG} removePiece(${square}) failed`, err);
      return null;
    }
  }

  _moveMirror(from, to) {
    const pm = this.pieces;
    if (!pm || typeof pm.movePiece !== 'function') return null;
    try { return pm.movePiece(from, to, { snap: false }); } catch (err) {
      console.warn(`${LOG} movePiece(${from}->${to}) failed`, err);
      return null;
    }
  }

  /* -------- choreography -------- */

  _build(event, silent) {
    const kind = String(event && event.kind ? event.kind : 'move').toLowerCase();
    switch (kind) {
      case 'move':      return this._buildMove(event, silent);
      case 'capture':   return this._buildCapture(event, silent, null);
      case 'enpassant':
      case 'en-passant':
      case 'en_passant': return this._buildCapture(event, silent, enPassantSquare(event));
      case 'castle':    return this._buildCastle(event, silent);
      case 'promotion':
      case 'promote':   return this._buildPromotion(event, silent);
      case 'check':     return this._buildCheck(event, silent);
      default:
        // Unknown kinds must never stall the queue: mirror the state and pass.
        if (event && event.from && event.to) return this._buildMove(event, silent);
        return staticAnim(0);
    }
  }

  /* --- move: 0.85s bezier arc, rise + landing overshoot --- */

  _buildMove(event, silent, overrides = {}) {
    const from = event.from;
    const to = event.to;
    const piece = this._pieceAt(from) || this._ensurePiece(event.piece, from);
    this._moveMirror(from, to);

    const a = worldOf(from);
    const b = worldOf(to);
    const obj = obj3dOf(piece);
    const duration = overrides.duration || DURATIONS.move;

    if (!obj) return staticAnim(duration);

    const dist = Math.hypot(b.x - a.x, b.z - a.z);
    const rise = (overrides.rise != null ? overrides.rise : 0.35) + Math.min(dist, 7) * 0.045;
    const curve = makeArc(a, b, rise);
    const dir = dirXZ(a, b);
    const TRAVEL = overrides.travel != null ? overrides.travel : 0.80;
    const lean = overrides.lean != null ? overrides.lean : 0.13;
    const tmp = new THREE.Vector3();
    const started = { trail: false };

    return {
      duration,
      apply: (u) => {
        if (!silent && !started.trail) {
          started.trail = true;
          this._fx('trail', meshOf(piece) || obj, glowColorOf(piece));
        }
        const travel = clamp01(u / TRAVEL);
        curve.getPoint(easeInOutQuad(travel), tmp);

        // Small forward overshoot in the last quarter of the flight, settling
        // back onto the square exactly at travel = 1.
        const over = dist * 0.055 * pulse(clamp01((travel - 0.72) / 0.28));
        obj.position.set(tmp.x + dir.x * over, Math.max(0, tmp.y), tmp.z + dir.z * over);
        applyLean(obj, dir, pulse(travel) * lean);
        obj.scale.setScalar(1);

        if (u > TRAVEL) {
          const s = clamp01((u - TRAVEL) / (1 - TRAVEL));
          obj.position.y = Math.max(0, (1 - easeOutElastic(s)) * 0.06);
          applySquash(obj, pulse(Math.min(s * 1.35, 1)) * 0.11 * (1 - s * 0.55));
          applyLean(obj, dir, pulse(travel) * lean * (1 - s));
        }
      },
      finish: () => { restPiece(piece, b, 1); },
    };
  }

  /* --- capture / en passant: 1.6s contact, flash, crack, dissolve, rack --- */

  _buildCapture(event, silent, victimSquareOverride) {
    const from = event.from;
    const to = event.to;
    const victimSquare = victimSquareOverride || to;
    const isEnPassant = !!victimSquareOverride && victimSquareOverride !== to;

    const attacker = this._pieceAt(from) || this._ensurePiece(event.piece, from);
    const victim = this._pieceAt(victimSquare);

    // Take the victim off the board first so the mover can't displace it.
    if (victim) this._detach(victimSquare, { toRack: true });
    else if (event.captured) {
      console.warn(`${LOG} capture on ${victimSquare} but no piece there — visual only.`);
    }
    this._moveMirror(from, to);

    const duration = DURATIONS.capture;
    const a = worldOf(from);
    const b = worldOf(to);
    const vPos = worldOf(victimSquare);
    const attackerObj = obj3dOf(attacker);
    const victimObj = obj3dOf(victim);

    if (!attackerObj && !victimObj) return staticAnim(duration);

    const dir = dirXZ(a, isEnPassant ? b : vPos);
    const contact = new THREE.Vector3(
      lerp(a.x, b.x, 0.72), 0, lerp(a.z, b.z, 0.72),
    );
    const approach = makeArc(a, contact, 0.30);
    const rackTarget = victim ? this._rackTarget(victim) : new THREE.Vector3();
    const rackScale = this._rackScale();
    const flight = victim
      ? makeArc(vPos, rackTarget, 1.25)
      : null;
    const victimPivot = pivotOf(victim);
    const victimBaseYaw = victimPivot ? victimPivot.rotation.y : 0;

    const P_CONTACT = 0.34;
    const P_LAUNCH = 0.44;
    const P_LANDED = 0.68;
    const fired = new Set();
    const tmp = new THREE.Vector3();

    const once = (key, fn) => {
      if (silent || fired.has(key)) return;
      fired.add(key);
      fn();
    };

    return {
      duration,
      apply: (u) => {
        once('start', () => {
          this._fx('trail', meshOf(attacker) || attackerObj, glowColorOf(attacker));
          if (victimObj) this._fx('warningHalo', meshOf(victim) || victimObj, glowColorOf(victim));
        });

        /* ---- attacker ---- */
        if (attackerObj) {
          if (u <= P_CONTACT) {
            const t = clamp01(u / P_CONTACT);
            approach.getPoint(easeInOutQuad(t), tmp);
            attackerObj.position.set(tmp.x, Math.max(0, tmp.y), tmp.z);
            applyLean(attackerObj, dir, pulse(t) * 0.10 + t * 0.10);
            attackerObj.scale.setScalar(1);
          } else if (u <= P_LANDED) {
            const t = clamp01((u - P_CONTACT) / (P_LANDED - P_CONTACT));
            const e = easeOutBack(t, 1.5);
            const recoil = -0.16 * pulse(clamp01(t / 0.4));
            attackerObj.position.set(
              lerp(contact.x, b.x, e) + dir.x * recoil,
              Math.max(0, 0.10 * pulse(t)),
              lerp(contact.z, b.z, e) + dir.z * recoil,
            );
            applyLean(attackerObj, dir, 0.20 * (1 - easeOutCubic(t)));
            applySquash(attackerObj, pulse(clamp01(t / 0.35)) * 0.10);
          } else {
            const t = clamp01((u - P_LANDED) / (1 - P_LANDED));
            attackerObj.position.set(b.x, 0, b.z);
            applyLean(attackerObj, dir, 0);
            applySquash(attackerObj, (1 - easeOutElastic(t)) * 0.05);
          }
        }

        /* ---- impact ---- */
        if (u >= P_CONTACT) {
          once('impact', () => {
            const flashAt = new THREE.Vector3(
              (contact.x + vPos.x) * 0.5, 0.55, (contact.z + vPos.z) * 0.5,
            );
            this._fx('impactFlash', flashAt, glowColorOf(attacker));
            this._fx('shieldCrack', new THREE.Vector3(vPos.x, 0.52, vPos.z), glowColorOf(victim));
            this._shake(isEnPassant ? 0.22 : 0.38, 0.30);
          });
        }

        /* ---- victim ---- */
        if (victimObj) {
          if (u < P_CONTACT) {
            victimObj.position.set(vPos.x, 0, vPos.z);
            victimObj.scale.setScalar(1);
          } else if (u < P_LAUNCH) {
            const t = clamp01((u - P_CONTACT) / (P_LAUNCH - P_CONTACT));
            const shudder = Math.sin(t * Math.PI * 9) * 0.035 * (1 - t);
            victimObj.position.set(vPos.x + shudder, 0, vPos.z + shudder * 0.6);
            victimObj.scale.setScalar(1 + pulse(t) * 0.08);
            applyLean(victimObj, dir, -0.12 * t);
          } else {
            once('dissolve', () => {
              if (typeof victim.isolateMaterial === 'function') victim.isolateMaterial();
              this._fx('dissolve', meshOf(victim) || victimObj, glowColorOf(victim), 720);
              this._fx('embers', new THREE.Vector3(vPos.x, 0.35, vPos.z), glowColorOf(victim), 46);
            });
            const t = clamp01((u - P_LAUNCH) / (1 - P_LAUNCH));
            const e = easeInOutQuad(t);
            if (flight) {
              flight.getPoint(e, tmp);
              victimObj.position.set(tmp.x, Math.max(0, tmp.y), tmp.z);
            }
            victimObj.scale.setScalar(lerp(1, rackScale, easeOutCubic(t)));
            applyLean(victimObj, dir, -0.12 * (1 - t));
            if (victimPivot) victimPivot.rotation.y = victimBaseYaw + t * Math.PI * 2.2;
            if (!silent && typeof victim.setOpacity === 'function') {
              // disintegrate, then re-form as a trophy in the rack
              const fade = t < 0.6 ? lerp(1, 0.28, t / 0.6) : lerp(0.28, 1, (t - 0.6) / 0.4);
              victim.setOpacity(fade);
            }
          }
        }
      },
      finish: () => {
        restPiece(attacker, b, 1);
        if (victim) {
          const victimMesh = meshOf(victim) || victimObj;
          // Unwind in the reverse order the swaps went on. fx grabbed the
          // isolated clones as its "original" material, and restoreMaterial()
          // disposes those clones — so fx has to let go first, or it reinstalls
          // disposed materials on the rack piece a fraction of a second later.
          // For the same reason setOpacity(1) must land on the clones rather
          // than on a pooled dissolve material. The warning halo is ours to
          // clear too: nothing else tracks a capture victim, so leaving it up
          // strands two additive rings under the trophy rack forever.
          this._fx('endDissolve', victimMesh);
          this._fx('removeHalo', victimMesh);
          if (typeof victim.setOpacity === 'function') victim.setOpacity(1);
          if (typeof victim.restoreMaterial === 'function') victim.restoreMaterial();
          this._placeInRack(victim);
        }
      },
    };
  }

  /* --- castle: 1.1s, king and rook together, rook swinging behind --- */

  _buildCastle(event, silent) {
    const duration = DURATIONS.castle;
    const kingFrom = event.from;
    const kingTo = event.to;
    const extra = event.extra || {};
    const rookFrom = extra.rookFrom || event.rookFrom;
    const rookTo = extra.rookTo || event.rookTo;

    const king = this._pieceAt(kingFrom) || this._ensurePiece(event.piece, kingFrom);
    const rook = rookFrom ? this._pieceAt(rookFrom) : null;

    this._moveMirror(kingFrom, kingTo);
    if (rook && rookTo) this._moveMirror(rookFrom, rookTo);

    const ka = worldOf(kingFrom);
    const kb = worldOf(kingTo);
    const kingObj = obj3dOf(king);
    const kingArc = makeArc(ka, kb, 0.34);
    const kingDir = dirXZ(ka, kb);

    let rookObj = null;
    let rookArc = null;
    let rookDir = null;
    let rookPivot = null;
    let rookBaseYaw = 0;
    let rb = null;
    if (rook && rookTo) {
      const ra = worldOf(rookFrom);
      rb = worldOf(rookTo);
      // Swing the rook toward the middle of the board so it reads as passing
      // behind the king rather than through it.
      const inward = ra.z > 0 ? -1 : 1;
      const arc = makeArc(ra, rb, 0.20);
      arc.v1.z += inward * 0.34;
      rookArc = arc;
      rookDir = dirXZ(ra, rb);
      rookObj = obj3dOf(rook);
      rookPivot = pivotOf(rook);
      rookBaseYaw = rookPivot ? rookPivot.rotation.y : 0;
    }

    if (!kingObj && !rookObj) return staticAnim(duration);
    const tmp = new THREE.Vector3();
    const fired = new Set();
    const once = (key, fn) => { if (!silent && !fired.has(key)) { fired.add(key); fn(); } };

    return {
      duration,
      apply: (u) => {
        once('start', () => {
          this._fx('trail', meshOf(king) || kingObj, glowColorOf(king));
          if (rookObj) this._fx('trail', meshOf(rook) || rookObj, glowColorOf(rook));
        });

        if (kingObj) {
          const t = clamp01((u - 0.05) / 0.95);
          kingArc.getPoint(easeInOutQuad(t), tmp);
          kingObj.position.set(tmp.x, Math.max(0, tmp.y), tmp.z);
          applyLean(kingObj, kingDir, pulse(t) * 0.11);
          if (u > 0.82) {
            const s = clamp01((u - 0.82) / 0.18);
            kingObj.position.y = Math.max(0, (1 - easeOutElastic(s)) * 0.05);
            applySquash(kingObj, pulse(Math.min(s * 1.4, 1)) * 0.09 * (1 - s * 0.5));
          } else {
            kingObj.scale.setScalar(1);
          }
        }

        if (rookObj && rookArc) {
          const t = clamp01(u / 0.90);
          rookArc.getPoint(easeInOutQuad(t), tmp);
          rookObj.position.set(tmp.x, Math.max(0, tmp.y), tmp.z);
          applyLean(rookObj, rookDir, pulse(t) * 0.16);
          if (rookPivot) rookPivot.rotation.y = rookBaseYaw + Math.sin(Math.PI * t) * 0.7;
          if (u > 0.88) {
            const s = clamp01((u - 0.88) / 0.12);
            applySquash(rookObj, pulse(s) * 0.10);
          } else {
            rookObj.scale.setScalar(1);
          }
        }

        if (u >= 0.94) {
          once('land', () => {
            this._fx('impactFlash', new THREE.Vector3(kb.x, 0.30, kb.z), glowColorOf(king));
            this._shake(0.14, 0.22);
          });
        }
      },
      finish: () => {
        restPiece(king, kb, 1);
        if (rook && rb) restPiece(rook, rb, 1);
      },
    };
  }

  /* --- promotion: 1.4s dissolve-up into a light column, new piece descends --- */

  _buildPromotion(event, silent) {
    const duration = DURATIONS.promotion;
    const from = event.from || event.to;
    const to = event.to;
    const color = (event.piece && event.piece.color) || 'white';
    const newType = promotedTypeOf(event);

    // A promotion can also be a capture.
    const victim = event.captured ? this._pieceAt(to) : null;
    if (victim) this._detach(to, { toRack: true });

    const pawn = this._pieceAt(from) || this._ensurePiece(event.piece || { type: 'p', color }, from);
    if (from !== to) this._moveMirror(from, to);

    // Detach the pawn from the board map right away and put the promoted piece
    // there, so the mirror matches the FEN for the whole animation.
    const pm = this.pieces;
    if (pawn && pm && typeof pm.removePiece === 'function') {
      this._detach(to, { toRack: false });
    }
    let promoted = null;
    if (pm && typeof pm.addPiece === 'function') {
      try { promoted = pm.addPiece(newType, color, to); } catch (err) {
        console.warn(`${LOG} could not create promoted piece`, err);
      }
    }

    const a = worldOf(from);
    const b = worldOf(to);
    const pawnObj = obj3dOf(pawn);
    const pawnPivot = pivotOf(pawn);
    const pawnBaseYaw = pawnPivot ? pawnPivot.rotation.y : 0;
    const promotedObj = obj3dOf(promoted);
    const promotedPivot = pivotOf(promoted);
    const promotedBaseYaw = promotedPivot ? promotedPivot.rotation.y : 0;
    const glow = glowColorOf(promoted || pawn);

    const victimObj = obj3dOf(victim);
    const victimRack = victim ? this._rackTarget(victim) : null;
    const victimFlight = victim ? makeArc(worldOf(to), victimRack, 1.1) : null;
    const rackScale = this._rackScale();

    const travelArc = makeArc(a, b, 0.32);
    const dir = dirXZ(a, b);

    const P_ARRIVE = 0.36;
    const P_ASCEND = 0.62;
    const fired = new Set();
    const tmp = new THREE.Vector3();
    const state = { swapped: false };
    const once = (key, fn) => { if (!silent && !fired.has(key)) { fired.add(key); fn(); } };

    if (promotedObj) promotedObj.visible = false;

    const swap = () => {
      if (state.swapped) return;
      state.swapped = true;
      if (pawn && typeof pawn.destroy === 'function') pawn.destroy();
      else if (pawnObj && pawnObj.parent) pawnObj.parent.remove(pawnObj);
      if (promotedObj) promotedObj.visible = true;
    };

    return {
      duration,
      apply: (u) => {
        /* victim (capture-promotion) flies off during the first half */
        if (victimObj && victimFlight) {
          once('victim', () => {
            if (typeof victim.isolateMaterial === 'function') victim.isolateMaterial();
            this._fx('shieldCrack', new THREE.Vector3(b.x, 0.5, b.z), glowColorOf(victim));
            this._fx('embers', new THREE.Vector3(b.x, 0.35, b.z), glowColorOf(victim), 34);
            this._fx('dissolve', meshOf(victim) || victimObj, glowColorOf(victim), 620);
          });
          const t = clamp01(u / 0.72);
          victimFlight.getPoint(easeInOutQuad(t), tmp);
          victimObj.position.set(tmp.x, Math.max(0, tmp.y), tmp.z);
          victimObj.scale.setScalar(lerp(1, rackScale, easeOutCubic(t)));
          if (typeof victim.setOpacity === 'function' && !silent) {
            victim.setOpacity(t < 0.6 ? lerp(1, 0.3, t / 0.6) : lerp(0.3, 1, (t - 0.6) / 0.4));
          }
        }

        /* pawn: advance, then rise and dissolve into the column */
        if (pawnObj && !state.swapped) {
          if (u < P_ARRIVE) {
            const t = clamp01(u / P_ARRIVE);
            travelArc.getPoint(easeInOutQuad(t), tmp);
            pawnObj.position.set(tmp.x, Math.max(0, tmp.y), tmp.z);
            applyLean(pawnObj, dir, pulse(t) * 0.12);
            pawnObj.scale.setScalar(1);
          } else if (u < P_ASCEND) {
            once('column', () => {
              this._fx('lightColumn', new THREE.Vector3(b.x, 0, b.z), glow, 950);
            });
            const t = clamp01((u - P_ARRIVE) / (P_ASCEND - P_ARRIVE));
            const e = easeInCubic(t);
            pawnObj.position.set(b.x, e * 1.05, b.z);
            pawnObj.rotation.set(0, 0, 0);
            pawnObj.scale.setScalar(Math.max(0.02, 1 - e * 0.92));
            if (pawnPivot) pawnPivot.rotation.y = pawnBaseYaw + t * Math.PI * 3.2;
            if (typeof pawn.setOpacity === 'function' && !silent) {
              pawn.setOpacity(Math.max(0, 1 - easeOutCubic(t)));
            }
            if (t > 0.75) {
              once('embers', () => {
                this._fx('embers', new THREE.Vector3(b.x, 0.8, b.z), glow, 40);
              });
            }
          }
        }

        /* the new piece materialises downward out of the column */
        if (u >= P_ASCEND) {
          swap();
          if (promotedObj) {
            const t = clamp01((u - P_ASCEND) / (1 - P_ASCEND));
            const e = easeOutBack(t, 1.25);
            promotedObj.visible = true;
            promotedObj.position.set(b.x, Math.max(0, lerp(1.85, 0, e)), b.z);
            promotedObj.rotation.set(0, 0, 0);
            promotedObj.scale.setScalar(lerp(0.35, 1, easeOutCubic(Math.min(t * 1.5, 1))));
            if (promotedPivot) promotedPivot.rotation.y = promotedBaseYaw + (1 - t) * Math.PI * 1.4;
            if (promoted && typeof promoted.setOpacity === 'function' && !silent) {
              promoted.setOpacity(easeOutCubic(Math.min(t * 2, 1)));
            }
            if (t > 0.82) {
              once('land', () => {
                this._fx('impactFlash', new THREE.Vector3(b.x, 0.25, b.z), glow);
                this._shake(0.24, 0.28);
              });
            }
          }
        }
      },
      finish: () => {
        swap();
        if (promoted) {
          if (typeof promoted.setOpacity === 'function') promoted.setOpacity(1);
          restPiece(promoted, b, 1);
        }
        if (victim) {
          // Same nesting rule as the capture finish(): fx holds the isolated
          // clones, so it releases them before restoreMaterial() disposes them.
          this._fx('endDissolve', meshOf(victim) || victimObj);
          if (typeof victim.setOpacity === 'function') victim.setOpacity(1);
          if (typeof victim.restoreMaterial === 'function') victim.restoreMaterial();
          this._placeInRack(victim);
        }
      },
    };
  }

  /* --- check: warning halo + a nudge of camera shake --- */

  _buildCheck(event, silent) {
    const square = event.square || event.to || event.king;
    const piece = this._pieceAt(square);
    const obj = obj3dOf(piece);
    const home = worldOf(square);
    const duration = DURATIONS.check;
    if (!obj) return staticAnim(duration);

    let started = false;
    return {
      duration,
      apply: (u) => {
        if (!silent && !started) {
          started = true;
          this._fx('warningHalo', meshOf(piece) || obj, 0xff3040);
          this._shake(0.16, 0.30);
        }
        const bob = pulse(clamp01(u * 2)) * 0.06;
        obj.position.set(home.x, Math.max(0, bob), home.z);
        applySquash(obj, pulse(clamp01(u * 1.6)) * 0.05);
      },
      finish: () => { restPiece(piece, home, 1); },
    };
  }
}

/** A no-op animation of the given length (used for unresolvable events). */
function staticAnim(duration) {
  return { duration: Math.max(0, duration || 0), apply: () => {}, finish: () => {} };
}
