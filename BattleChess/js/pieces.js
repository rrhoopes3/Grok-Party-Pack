/**
 * BattleChess — pieces.js
 *
 * GLB loading, faction materials, piece instances.
 *
 * Responsibilities (CONTRACT.md sections 4, 5, 11.6):
 *   - Load ./assets/models/federation.glb and ./assets/models/imperium.glb and
 *     extract the six named meshes (pawn/knight/bishop/rook/queen/king).
 *   - Fall back to procedurally generated silhouettes when a GLB (or a single
 *     named mesh inside one) is missing, so the viewer NEVER hard-fails.
 *   - Override the 'body' / 'trim' / 'glow' material slots per faction.
 *   - Share one geometry + one material set per (faction, piece type): eight
 *     white pawns are eight Meshes over one geometry and one material array.
 *   - Maintain the render-side square -> piece mirror of the game state, with
 *     snapToPosition() as the authoritative-FEN self-heal.
 *
 * Named exports: FACTIONS, createPieceManager, PIECE_TYPES, PIECE_HEIGHTS,
 *                RACK_SCALE, normalizePieceType, normalizePieceColor
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import * as BoardModule from './board.js';

const LOG = '[BattleChess/pieces]';

/* ------------------------------------------------------------------ *
 * Faction palette (CONTRACT.md section 5 — verbatim)
 * ------------------------------------------------------------------ */

export const FACTIONS = {
  white: {                       // Starfleet
    name: 'Starfleet',
    body:  { color: 0xe8eef5, metalness: 0.85, roughness: 0.22 },
    trim:  { color: 0xb9c6d4 },
    glow:  { color: 0x39d6ff, intensity: 2.4 },   // cyan
    rimLight: 0x2a9df4,
  },
  black: {                       // Imperium
    name: 'Imperium',
    body:  { color: 0x1a1a1f, metalness: 0.95, roughness: 0.38 },
    trim:  { color: 0xa8801f },                    // tarnished brass
    glow:  { color: 0xff3b1f, intensity: 2.2 },   // crimson plasma
    rimLight: 0xff6a3d,
  },
};

export const PIECE_TYPES = ['pawn', 'knight', 'bishop', 'rook', 'queen', 'king'];

/** Contract section 4 — tip-of-model height in world units. */
export const PIECE_HEIGHTS = {
  pawn: 0.85, knight: 1.05, rook: 1.00, bishop: 1.15, queen: 1.35, king: 1.50,
};

/** Captured pieces are parked at this scale in the side racks. */
export const RACK_SCALE = 0.55;

const TYPE_BY_LETTER = { p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen', k: 'king' };
const LETTER_BY_TYPE = { pawn: 'p', knight: 'n', bishop: 'b', rook: 'r', queen: 'q', king: 'k' };
const MODEL_FILES = { white: 'federation.glb', black: 'imperium.glb' };
const SLOT_ORDER = ['body', 'trim', 'glow'];

/** Accepts 'p' | 'P' | 'pawn' | 'Pawn'. Returns a canonical long name. */
export function normalizePieceType(type) {
  if (!type) return 'pawn';
  const s = String(type).toLowerCase();
  if (TYPE_BY_LETTER[s]) return TYPE_BY_LETTER[s];
  if (PIECE_TYPES.includes(s)) return s;
  return 'pawn';
}

/** Accepts 'w' | 'white' | 'b' | 'black' (and uppercase FEN letters). */
export function normalizePieceColor(color) {
  if (!color) return 'white';
  const s = String(color).toLowerCase();
  if (s === 'b' || s === 'black' || s === 'dark') return 'black';
  return 'white';
}

/* ------------------------------------------------------------------ *
 * Board helpers — contract says board.js owns the coordinate math, but we
 * keep a spec-identical local fallback so a late/partial board module can
 * never leave pieces stranded at the origin.
 * ------------------------------------------------------------------ */

const FILES = 'abcdefgh';

function localSquareToWorld(square, target = new THREE.Vector3()) {
  const s = String(square || '').toLowerCase();
  const f = FILES.indexOf(s[0]);
  const r = Number.parseInt(s[1], 10) - 1;
  if (f < 0 || !Number.isFinite(r) || r < 0 || r > 7) return target.set(0, 0, 0);
  return target.set(f - 3.5, 0, 3.5 - r);
}

function squareToWorld(square) {
  const fn = BoardModule && typeof BoardModule.squareToWorld === 'function'
    ? BoardModule.squareToWorld
    : null;
  if (fn) {
    try {
      const v = fn(square);
      if (v && v.isVector3) return v.clone();
      if (v && Number.isFinite(v.x)) return new THREE.Vector3(v.x, v.y || 0, v.z);
    } catch (err) {
      console.warn(`${LOG} board.squareToWorld failed for "${square}"`, err);
    }
  }
  return localSquareToWorld(square);
}

const BOARD_HALF = (BoardModule && Number.isFinite(BoardModule.BOARD_HALF))
  ? BoardModule.BOARD_HALF
  : 4.0;

/* ------------------------------------------------------------------ *
 * Geometry plumbing
 * ------------------------------------------------------------------ */

/** Strip a geometry down to position/normal/uv, non-indexed, morph-free. */
function normalizeAttributes(source) {
  let g = source.index ? source.toNonIndexed() : source.clone();
  g.morphAttributes = {};
  g.clearGroups();

  for (const name of Object.keys(g.attributes)) {
    if (name !== 'position' && name !== 'normal' && name !== 'uv') g.deleteAttribute(name);
  }
  if (!g.getAttribute('position')) { g.dispose(); return null; }
  if (!g.getAttribute('normal')) g.computeVertexNormals();
  if (!g.getAttribute('uv')) {
    const count = g.getAttribute('position').count;
    g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(count * 2), 2));
  }
  return g;
}

/** Copy an attribute range into a fresh, non-interleaved BufferAttribute. */
function copyAttributeRange(attr, start, count) {
  const item = attr.itemSize;
  const out = new Float32Array(count * item);
  for (let i = 0; i < count; i++) {
    const src = start + i;
    out[i * item] = attr.getX(src);
    if (item > 1) out[i * item + 1] = attr.getY(src);
    if (item > 2) out[i * item + 2] = attr.getZ(src);
    if (item > 3) out[i * item + 3] = attr.getW(src);
  }
  return new THREE.BufferAttribute(out, item);
}

/**
 * Slice one draw-group out of an already de-indexed geometry.
 * (toNonIndexed() emits one vertex per index in order, so group start/count
 * stay valid as vertex ranges.)
 */
function sliceGroup(nonIndexed, start, count) {
  const pos = nonIndexed.getAttribute('position');
  if (!pos) return null;
  const total = pos.count;
  const s = Math.max(0, Math.min(total, Math.floor(start || 0)));
  const c = (!Number.isFinite(count)) ? total - s : Math.max(0, Math.min(Math.floor(count), total - s));
  if (c <= 0) return null;

  const g = new THREE.BufferGeometry();
  for (const name of ['position', 'normal', 'uv']) {
    const attr = nonIndexed.getAttribute(name);
    if (!attr) continue;
    g.setAttribute(name, copyAttributeRange(attr, s, c));
  }
  if (!g.getAttribute('normal')) g.computeVertexNormals();
  if (!g.getAttribute('uv')) {
    g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(c * 2), 2));
  }
  return g;
}

/**
 * Merge per-slot geometry lists into a single geometry whose draw-groups map
 * 1:1 onto a material array. Returns { geometry, slots } or null.
 * Consumes (disposes) every geometry passed in.
 */
function combineSlots(slots) {
  const parts = [];
  const used = [];

  for (const slot of SLOT_ORDER) {
    const list = (slots[slot] || []).filter(Boolean);
    if (!list.length) continue;

    const normalized = [];
    for (const geo of list) {
      const n = normalizeAttributes(geo);
      geo.dispose();
      if (n) normalized.push(n);
    }
    if (!normalized.length) continue;

    let merged;
    if (normalized.length === 1) {
      merged = normalized[0];
    } else {
      merged = mergeGeometries(normalized, false);
      for (const n of normalized) n.dispose();
      if (!merged) {
        console.warn(`${LOG} mergeGeometries failed for slot "${slot}" — slot dropped`);
        continue;
      }
    }
    parts.push(merged);
    used.push(slot);
  }

  if (!parts.length) return null;

  const geometry = mergeGeometries(parts, true);
  for (const p of parts) p.dispose();
  if (!geometry) {
    console.warn(`${LOG} mergeGeometries failed to group slots — using fallback box`);
    const box = new THREE.BoxGeometry(0.4, 0.8, 0.4);
    box.translate(0, 0.4, 0);
    const n = normalizeAttributes(box);
    box.dispose();
    if (!n) return null;
    const boxed = mergeGeometries([n], true);
    n.dispose();
    return boxed ? { geometry: boxed, slots: ['body'] } : null;
  }

  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return { geometry, slots: used };
}

/**
 * Enforce the contract's origin/height/footprint rules.
 * exact=true always rescales to the tabled height (procedural pieces);
 * exact=false only corrects gross deviations and warns (authored GLBs).
 */
function normalizeToContract(geometry, type, { exact = false, label = '' } = {}) {
  const target = PIECE_HEIGHTS[type] || 1.0;

  geometry.computeBoundingBox();
  let bb = geometry.boundingBox;
  if (!bb) return geometry;

  const cx = (bb.max.x + bb.min.x) * 0.5;
  const cz = (bb.max.z + bb.min.z) * 0.5;
  const baseY = bb.min.y;
  if (Math.abs(cx) > 1e-4 || Math.abs(cz) > 1e-4 || Math.abs(baseY) > 1e-4) {
    if (!exact && Math.abs(baseY) > 0.02) {
      console.warn(`${LOG} ${label}${type}: base sat at y=${baseY.toFixed(3)} — snapped to y=0.`);
    }
    geometry.translate(-cx, -baseY, -cz);
  }

  geometry.computeBoundingBox();
  bb = geometry.boundingBox;
  const height = bb.max.y - bb.min.y;
  if (height > 1e-4) {
    const drift = Math.abs(height - target) / target;
    if (exact || drift > 0.15) {
      if (!exact) {
        console.warn(`${LOG} ${label}${type}: height ${height.toFixed(3)} != ${target} — rescaled.`);
      }
      const k = target / height;
      geometry.scale(k, k, k);
    }
  }

  geometry.computeBoundingBox();
  bb = geometry.boundingBox;
  const radius = Math.max(
    Math.abs(bb.min.x), Math.abs(bb.max.x),
    Math.abs(bb.min.z), Math.abs(bb.max.z),
  );
  if (radius > 0.42) {
    console.warn(`${LOG} ${label}${type}: footprint radius ${radius.toFixed(3)} > 0.40.`);
    if (radius > 0.55) geometry.scale(0.40 / radius, 0.40 / radius, 0.40 / radius);
  }

  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}

/* ------------------------------------------------------------------ *
 * Procedural fallback silhouettes
 * ------------------------------------------------------------------ */

function transformGeo(geo, { x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0, s = 1, sx, sy, sz } = {}) {
  const m = new THREE.Matrix4().compose(
    new THREE.Vector3(x, y, z),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(rx, ry, rz)),
    new THREE.Vector3(sx ?? s, sy ?? s, sz ?? s),
  );
  geo.applyMatrix4(m);
  return geo;
}

function lathe(profile, segments = 28) {
  const points = profile.map(([x, y]) => new THREE.Vector2(Math.max(x, 1e-4), y));
  return new THREE.LatheGeometry(points, segments);
}

function glowRing(radius, tube, y, segments = 24) {
  const g = new THREE.TorusGeometry(radius, tube, 6, segments);
  return transformGeo(g, { y, rx: Math.PI / 2 });
}

function ringOf(count, radius, factory, yaw0 = 0) {
  const out = [];
  for (let i = 0; i < count; i++) {
    const a = yaw0 + (i / count) * Math.PI * 2;
    const geo = factory(i);
    if (!geo) continue;
    out.push(transformGeo(geo, { x: Math.cos(a) * radius, z: Math.sin(a) * radius, ry: -a }));
  }
  return out;
}

/** Lathe silhouettes. Radius <= 0.40, base at y=0, tip at the tabled height. */
const PROFILES = {
  white: {
    pawn: [[0, 0], [0.30, 0], [0.32, 0.035], [0.30, 0.08], [0.20, 0.115], [0.16, 0.17],
      [0.135, 0.34], [0.152, 0.40], [0.126, 0.445], [0.115, 0.50],
      [0.205, 0.545], [0.208, 0.578], [0.148, 0.605],
      [0.145, 0.635], [0.187, 0.70], [0.172, 0.78], [0.112, 0.836], [0, 0.85]],
    knight: [[0, 0], [0.32, 0], [0.335, 0.04], [0.31, 0.085], [0.21, 0.12], [0.175, 0.18],
      [0.155, 0.30], [0.185, 0.345], [0.175, 0.385], [0.14, 0.42], [0.13, 0.50], [0, 0.52]],
    rook: [[0, 0], [0.35, 0], [0.365, 0.045], [0.335, 0.095], [0.255, 0.135], [0.235, 0.20],
      [0.225, 0.50], [0.245, 0.545], [0.235, 0.60], [0.285, 0.66], [0.315, 0.73],
      [0.318, 0.80], [0.26, 0.845], [0, 0.86]],
    bishop: [[0, 0], [0.33, 0], [0.345, 0.04], [0.32, 0.085], [0.215, 0.12], [0.175, 0.175],
      [0.145, 0.36], [0.168, 0.42], [0.138, 0.465], [0.125, 0.53],
      [0.212, 0.585], [0.215, 0.62], [0.152, 0.65],
      [0.16, 0.74], [0.185, 0.83], [0.155, 0.94], [0.095, 1.03], [0.052, 1.075],
      [0.062, 1.10], [0.042, 1.135], [0, 1.15]],
    queen: [[0, 0], [0.37, 0], [0.385, 0.045], [0.355, 0.095], [0.245, 0.135], [0.20, 0.19],
      [0.165, 0.40], [0.19, 0.46], [0.155, 0.51], [0.142, 0.60],
      [0.235, 0.665], [0.238, 0.705], [0.168, 0.735],
      [0.175, 0.86], [0.215, 0.96], [0.30, 1.07], [0.305, 1.115], [0.235, 1.145],
      [0.12, 1.17], [0.10, 1.235], [0.135, 1.275], [0.10, 1.315], [0, 1.35]],
    king: [[0, 0], [0.375, 0], [0.39, 0.045], [0.36, 0.10], [0.25, 0.14], [0.205, 0.20],
      [0.17, 0.42], [0.195, 0.485], [0.16, 0.535], [0.148, 0.64],
      [0.245, 0.71], [0.248, 0.752], [0.175, 0.785],
      [0.182, 0.93], [0.222, 1.04], [0.305, 1.15], [0.31, 1.195], [0.245, 1.228],
      [0.15, 1.25], [0.115, 1.30], [0.075, 1.34], [0, 1.36]],
  },
  black: {
    pawn: [[0, 0], [0.325, 0], [0.335, 0.055], [0.285, 0.062], [0.265, 0.105], [0.19, 0.135],
      [0.152, 0.30], [0.178, 0.36], [0.135, 0.40], [0.125, 0.465],
      [0.215, 0.515], [0.208, 0.585], [0.142, 0.615],
      [0.165, 0.685], [0.192, 0.745], [0.118, 0.815], [0.05, 0.845], [0, 0.85]],
    knight: [[0, 0], [0.34, 0], [0.35, 0.055], [0.30, 0.062], [0.28, 0.11], [0.20, 0.145],
      [0.168, 0.30], [0.198, 0.35], [0.15, 0.395], [0.145, 0.46], [0.16, 0.50], [0, 0.53]],
    rook: [[0, 0], [0.365, 0], [0.375, 0.06], [0.325, 0.068], [0.30, 0.12], [0.245, 0.155],
      [0.235, 0.44], [0.265, 0.49], [0.235, 0.54], [0.245, 0.62], [0.30, 0.70],
      [0.325, 0.79], [0.33, 0.845], [0.27, 0.86], [0, 0.87]],
    bishop: [[0, 0], [0.345, 0], [0.355, 0.055], [0.305, 0.062], [0.285, 0.11], [0.20, 0.145],
      [0.16, 0.34], [0.19, 0.40], [0.145, 0.445], [0.135, 0.52],
      [0.225, 0.575], [0.218, 0.635], [0.155, 0.665],
      [0.175, 0.76], [0.198, 0.85], [0.16, 0.95], [0.10, 1.045], [0.055, 1.09],
      [0.07, 1.115], [0.038, 1.14], [0, 1.15]],
    queen: [[0, 0], [0.385, 0], [0.395, 0.06], [0.34, 0.068], [0.315, 0.125], [0.225, 0.165],
      [0.178, 0.40], [0.208, 0.465], [0.16, 0.515], [0.15, 0.61],
      [0.245, 0.675], [0.238, 0.735], [0.172, 0.765],
      [0.19, 0.885], [0.228, 0.985], [0.315, 1.085], [0.318, 1.135], [0.245, 1.16],
      [0.125, 1.18], [0.105, 1.245], [0.145, 1.285], [0.095, 1.32], [0, 1.35]],
    king: [[0, 0], [0.39, 0], [0.40, 0.06], [0.345, 0.068], [0.32, 0.13], [0.23, 0.17],
      [0.182, 0.42], [0.212, 0.49], [0.165, 0.54], [0.155, 0.655],
      [0.255, 0.725], [0.248, 0.785], [0.18, 0.815],
      [0.198, 0.955], [0.235, 1.06], [0.32, 1.165], [0.322, 1.215], [0.25, 1.245],
      [0.155, 1.265], [0.12, 1.315], [0.078, 1.35], [0, 1.37]],
  },
};

/**
 * Build a faction-flavoured placeholder for one piece type.
 * Starfleet: swept curves, saucer flanges, deflector rings.
 * Imperium:  bladed buttresses, spikes, plasma slits.
 */
function buildProcedural(type, color) {
  const fed = color === 'white';
  const seg = (type === 'queen' || type === 'king') ? 32 : 26;
  const slots = { body: [lathe(PROFILES[color][type], seg)], trim: [], glow: [] };

  const spike = (h, r) => new THREE.ConeGeometry(r, h, 5);
  const blade = (w, h, d) => new THREE.BoxGeometry(w, h, d);

  switch (type) {
    case 'pawn': {
      if (fed) {
        slots.glow.push(glowRing(0.20, 0.017, 0.562));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.036, 10, 8), { y: 0.755, z: -0.145 }));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.235, 0.235, 0.022, 24), { y: 0.126 }));
      } else {
        slots.trim.push(...ringOf(4, 0.20, () => transformGeo(spike(0.14, 0.045), { rz: -Math.PI / 2.4 }), 0.4)
          .map((g) => transformGeo(g, { y: 0.40 })));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.032, 8, 8), { y: 0.735, z: -0.115 }));
        slots.glow.push(glowRing(0.155, 0.014, 0.475, 18));
      }
      break;
    }

    case 'knight': {
      // Head assembly faces -Z (contract section 4); Black is rotated 180 at spawn.
      if (fed) {
        slots.body.push(transformGeo(blade(0.20, 0.46, 0.17), { y: 0.70, z: -0.045, rx: -0.22 }));
        slots.body.push(transformGeo(blade(0.175, 0.20, 0.36), { y: 0.925, z: -0.135, rx: 0.30 }));
        slots.body.push(transformGeo(new THREE.CylinderGeometry(0.105, 0.13, 0.09, 18), { y: 0.90, z: -0.245, rx: Math.PI / 2 }));
        slots.trim.push(transformGeo(blade(0.055, 0.34, 0.20), { y: 0.905, z: 0.075, rx: -0.35 }));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.20, 0.20, 0.03, 22), { y: 0.52 }));
        slots.glow.push(transformGeo(new THREE.CylinderGeometry(0.07, 0.07, 0.028, 18), { y: 0.90, z: -0.29, rx: Math.PI / 2 }));
        slots.glow.push(transformGeo(blade(0.022, 0.30, 0.02), { y: 0.72, z: -0.135, rx: -0.22 }));
      } else {
        slots.body.push(transformGeo(blade(0.215, 0.44, 0.19), { y: 0.70, z: -0.03, rx: -0.26 }));
        slots.body.push(transformGeo(blade(0.185, 0.185, 0.38), { y: 0.905, z: -0.145, rx: 0.34 }));
        slots.trim.push(transformGeo(spike(0.30, 0.05), { y: 1.00, z: 0.10, rx: 0.42 }));
        slots.trim.push(transformGeo(spike(0.19, 0.042), { x: 0.10, y: 0.99, z: -0.02, rx: 0.15, rz: 0.32 }));
        slots.trim.push(transformGeo(spike(0.19, 0.042), { x: -0.10, y: 0.99, z: -0.02, rx: 0.15, rz: -0.32 }));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.21, 0.21, 0.035, 20), { y: 0.53 }));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.032, 8, 8), { x: 0.065, y: 0.945, z: -0.255 }));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.032, 8, 8), { x: -0.065, y: 0.945, z: -0.255 }));
      }
      break;
    }

    case 'rook': {
      if (fed) {
        // Deflector tower + four swept nacelle fins + crenellated command deck.
        slots.body.push(...ringOf(4, 0.245, () => blade(0.075, 0.40, 0.10), Math.PI / 4)
          .map((g) => transformGeo(g, { y: 0.36 })));
        slots.body.push(...ringOf(6, 0.255, () => blade(0.10, 0.145, 0.085))
          .map((g) => transformGeo(g, { y: 0.925 })));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.30, 0.30, 0.028, 26), { y: 0.868 }));
        slots.glow.push(glowRing(0.243, 0.019, 0.60, 26));
        slots.glow.push(transformGeo(new THREE.CylinderGeometry(0.15, 0.15, 0.03, 20), { y: 0.998 }));
      } else {
        // Gothic bastion: buttresses, battlements, plasma vents.
        slots.body.push(...ringOf(4, 0.255, () => blade(0.085, 0.52, 0.115), Math.PI / 4)
          .map((g) => transformGeo(g, { y: 0.36 })));
        slots.body.push(...ringOf(6, 0.268, () => blade(0.115, 0.17, 0.095))
          .map((g) => transformGeo(g, { y: 0.935 })));
        slots.trim.push(...ringOf(4, 0.245, () => spike(0.20, 0.045), Math.PI / 4)
          .map((g) => transformGeo(g, { y: 0.955 })));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.31, 0.31, 0.032, 24), { y: 0.872 }));
        slots.glow.push(...ringOf(4, 0.24, () => blade(0.038, 0.24, 0.03))
          .map((g) => transformGeo(g, { y: 0.44 })));
      }
      break;
    }

    case 'bishop': {
      if (fed) {
        slots.body.push(transformGeo(new THREE.SphereGeometry(0.058, 14, 10), { y: 1.135 }));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.205, 0.205, 0.026, 24), { y: 0.60 }));
        slots.glow.push(transformGeo(blade(0.028, 0.30, 0.028), { y: 0.86, z: -0.132, rx: -0.08 }));
        slots.glow.push(glowRing(0.132, 0.014, 0.70, 20));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.042, 12, 10), { y: 1.135 }));
      } else {
        slots.body.push(transformGeo(new THREE.SphereGeometry(0.055, 12, 10), { y: 1.128 }));
        slots.trim.push(transformGeo(spike(0.16, 0.042), { x: 0.135, y: 0.845, rz: 0.55 }));
        slots.trim.push(transformGeo(spike(0.16, 0.042), { x: -0.135, y: 0.845, rz: -0.55 }));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.215, 0.215, 0.03, 22), { y: 0.60 }));
        slots.glow.push(transformGeo(blade(0.03, 0.26, 0.03), { y: 0.90, z: -0.135, rx: -0.10 }));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.04, 10, 8), { y: 1.128 }));
      }
      break;
    }

    case 'queen': {
      if (fed) {
        slots.body.push(...ringOf(8, 0.275, () => transformGeo(spike(0.13, 0.038), { rx: -0.18 }))
          .map((g) => transformGeo(g, { y: 1.185 })));
        slots.body.push(transformGeo(new THREE.SphereGeometry(0.062, 14, 12), { y: 1.315 }));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.30, 0.30, 0.026, 30), { y: 1.09 }));
        slots.glow.push(glowRing(0.232, 0.018, 0.685, 26));
        slots.glow.push(glowRing(0.283, 0.016, 1.128, 30));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.045, 12, 10), { y: 1.315 }));
      } else {
        slots.body.push(...ringOf(8, 0.285, () => transformGeo(spike(0.17, 0.04), { rx: -0.10 }))
          .map((g) => transformGeo(g, { y: 1.20 })));
        slots.body.push(transformGeo(new THREE.SphereGeometry(0.06, 12, 10), { y: 1.312 }));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.315, 0.315, 0.03, 28), { y: 1.095 }));
        slots.trim.push(...ringOf(4, 0.20, () => blade(0.05, 0.30, 0.055), Math.PI / 4)
          .map((g) => transformGeo(g, { y: 0.90, rx: 0.10 })));
        slots.glow.push(glowRing(0.24, 0.018, 0.70, 24));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.045, 10, 8), { y: 1.312 }));
      }
      break;
    }

    case 'king': {
      if (fed) {
        slots.body.push(...ringOf(8, 0.285, () => transformGeo(spike(0.115, 0.036), { rx: -0.14 }))
          .map((g) => transformGeo(g, { y: 1.26 })));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.305, 0.305, 0.026, 30), { y: 1.17 }));
        // Delta / chevron crest.
        slots.glow.push(transformGeo(blade(0.035, 0.22, 0.035), { y: 1.44 }));
        slots.glow.push(transformGeo(blade(0.20, 0.034, 0.034), { y: 1.415 }));
        slots.glow.push(glowRing(0.24, 0.018, 0.735, 26));
        slots.glow.push(glowRing(0.29, 0.016, 1.205, 30));
        slots.body.push(transformGeo(new THREE.SphereGeometry(0.05, 12, 10), { y: 1.355 }));
      } else {
        slots.body.push(...ringOf(8, 0.295, () => transformGeo(spike(0.165, 0.04), { rx: -0.06 }))
          .map((g) => transformGeo(g, { y: 1.275 })));
        slots.trim.push(transformGeo(new THREE.CylinderGeometry(0.325, 0.325, 0.032, 28), { y: 1.175 }));
        // Aquila: crossbar wings + central spike.
        slots.trim.push(transformGeo(blade(0.30, 0.038, 0.045), { y: 1.415 }));
        slots.trim.push(transformGeo(blade(0.115, 0.035, 0.042), { x: 0.175, y: 1.452, rz: 0.55 }));
        slots.trim.push(transformGeo(blade(0.115, 0.035, 0.042), { x: -0.175, y: 1.452, rz: -0.55 }));
        slots.body.push(transformGeo(spike(0.16, 0.05), { y: 1.42 }));
        slots.glow.push(transformGeo(new THREE.SphereGeometry(0.05, 10, 8), { y: 1.40 }));
        slots.glow.push(glowRing(0.25, 0.018, 0.755, 24));
        slots.glow.push(...ringOf(4, 0.245, () => blade(0.035, 0.26, 0.03), Math.PI / 4)
          .map((g) => transformGeo(g, { y: 0.50 })));
      }
      break;
    }

    default:
      break;
  }

  const combined = combineSlots(slots);
  if (!combined) {
    const box = new THREE.BoxGeometry(0.35, PIECE_HEIGHTS[type], 0.35);
    box.translate(0, PIECE_HEIGHTS[type] / 2, 0);
    const n = normalizeAttributes(box);
    box.dispose();
    const fallback = mergeGeometries([n], true);
    n.dispose();
    return { geometry: normalizeToContract(fallback, type, { exact: true }), slots: ['body'] };
  }
  combined.geometry = normalizeToContract(combined.geometry, type, { exact: true });
  return combined;
}

/* ------------------------------------------------------------------ *
 * GLB extraction
 * ------------------------------------------------------------------ */

function findNodeByName(root, name) {
  let exact = null;
  let loose = null;
  root.traverse((child) => {
    if (exact) return;
    const n = String(child.name || '').toLowerCase();
    if (n === name) exact = child;
    else if (!loose && (n.startsWith(`${name}_`) || n.startsWith(`${name}.`) || n === `${name}s`)) loose = child;
  });
  return exact || loose;
}

function slotForMaterialName(name) {
  const n = String(name || '').toLowerCase();
  if (n.includes('glow') || n.includes('emissive') || n.includes('plasma')) return 'glow';
  if (n.includes('trim') || n.includes('brass') || n.includes('accent')) return 'trim';
  return 'body';
}

/** Bake a GLB node (and its mesh descendants) into one grouped geometry. */
function geometryFromNode(node) {
  node.updateWorldMatrix(true, true);
  const inverse = new THREE.Matrix4().copy(node.matrixWorld).invert();
  const slots = { body: [], trim: [], glow: [] };
  let found = 0;

  node.traverse((child) => {
    if (!child.isMesh || !child.geometry) return;
    found++;
    const source = child.geometry;
    const nonIndexed = source.index ? source.toNonIndexed() : source.clone();
    const local = new THREE.Matrix4().multiplyMatrices(inverse, child.matrixWorld);
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    const groups = (source.groups && source.groups.length)
      ? source.groups
      : [{ start: 0, count: Infinity, materialIndex: 0 }];

    for (const group of groups) {
      const sub = sliceGroup(nonIndexed, group.start, group.count);
      if (!sub) continue;
      sub.applyMatrix4(local);
      const mat = materials[group.materialIndex ?? 0];
      slots[slotForMaterialName(mat && mat.name)].push(sub);
    }
    nonIndexed.dispose();
  });

  if (!found) return null;
  return combineSlots(slots);
}

/* ------------------------------------------------------------------ *
 * Piece handle
 * ------------------------------------------------------------------ */

/**
 * A live piece on (or beside) the board.
 * Exposes Object3D-ish accessors so callers can treat it as a transform, plus
 * .object3D (outer transform), .pivot (faction facing) and .mesh (drawable).
 */
class PieceHandle {
  constructor(manager, type, color, square, object3D, pivot, mesh) {
    this.manager = manager;
    this.type = type;                 // 'pawn' | 'knight' | ...
    this.letter = LETTER_BY_TYPE[type];
    this.color = color;               // 'white' | 'black'
    this.faction = FACTIONS[color].name;
    this.square = square;             // null once captured
    this.object3D = object3D;
    this.pivot = pivot;
    this.mesh = mesh;
    this.captured = false;
    this.rackIndex = -1;
    this.disposed = false;
    this._isolated = null;
    this._sharedMaterial = null;
  }

  get position() { return this.object3D.position; }
  get rotation() { return this.object3D.rotation; }
  get scale() { return this.object3D.scale; }
  get visible() { return this.object3D.visible; }
  set visible(v) { this.object3D.visible = !!v; }
  get isPiece() { return true; }

  /** Faction facing: Black is turned 180 degrees so it looks toward +Z. */
  get facing() { return this.color === 'black' ? Math.PI : 0; }

  /** Clone materials so per-piece VFX (dissolve/fade) can't touch siblings. */
  isolateMaterial() {
    if (this.disposed) return null;
    if (this._isolated) return this.mesh.material;
    const current = this.mesh.material;
    this._sharedMaterial = current;
    const list = Array.isArray(current) ? current : [current];
    this._isolated = list.map((m) => (m && m.clone ? m.clone() : m));
    this.mesh.material = Array.isArray(current) ? this._isolated : this._isolated[0];
    return this.mesh.material;
  }

  /** Put the shared materials back and free the clones. */
  restoreMaterial() {
    if (!this._isolated) return;
    if (!this.disposed && this._sharedMaterial) this.mesh.material = this._sharedMaterial;
    for (const m of this._isolated) { if (m && m.dispose) m.dispose(); }
    this._isolated = null;
    this._sharedMaterial = null;
  }

  setOpacity(alpha) {
    if (this.disposed) return;
    const a = THREE.MathUtils.clamp(alpha, 0, 1);
    if (a >= 0.999 && !this._isolated) return;
    const mats = this.isolateMaterial();
    const list = Array.isArray(mats) ? mats : [mats];
    for (const m of list) {
      if (!m) continue;
      m.transparent = a < 0.999;
      m.opacity = a;
      m.depthWrite = a >= 0.999;
      m.needsUpdate = true;
    }
  }

  /** Undo everything an animation may have done to this piece's visuals. */
  resetVisualState() {
    if (this.disposed) return;
    this.restoreMaterial();
    const s = this.captured ? RACK_SCALE : 1;
    this.object3D.scale.set(s, s, s);
    this.object3D.rotation.set(0, 0, 0);
    this.pivot.rotation.set(0, this.facing, 0);
    this.pivot.position.set(0, 0, 0);
    this.object3D.visible = true;
  }

  destroy() {
    if (this.manager && typeof this.manager.destroyPiece === 'function') {
      this.manager.destroyPiece(this);
    }
  }
}

/* ------------------------------------------------------------------ *
 * Piece manager
 * ------------------------------------------------------------------ */

export function createPieceManager({ scene } = {}) {
  const root = new THREE.Group();
  root.name = 'battlechess-pieces';
  if (scene && typeof scene.add === 'function') scene.add(root);

  /** key `${color}:${type}` -> { geometry, slots } */
  const geometryCache = new Map();
  /** color -> { body, trim, glow } */
  const materialCache = new Map();
  /** square -> PieceHandle */
  const bySquare = new Map();
  const allPieces = new Set();
  const captured = { white: [], black: [] };

  const sourceOf = { white: 'pending', black: 'pending' };
  let loadPromise = null;
  let disposed = false;
  let idCounter = 0;

  /* ---------------- materials ---------------- */

  function materialsFor(color) {
    let mats = materialCache.get(color);
    if (mats) return mats;
    const f = FACTIONS[color];

    const body = new THREE.MeshStandardMaterial({
      name: `${color}-body`,
      color: f.body.color,
      metalness: f.body.metalness,
      roughness: f.body.roughness,
      envMapIntensity: 1.25,
    });

    const trim = new THREE.MeshStandardMaterial({
      name: `${color}-trim`,
      color: f.trim.color,
      metalness: 0.95,
      roughness: color === 'white' ? 0.30 : 0.44,
      envMapIntensity: 1.35,
    });

    // Emissive accent: unlit-bright so UnrealBloom (threshold 0.85) catches it.
    const glowColor = new THREE.Color(f.glow.color);
    const glow = new THREE.MeshStandardMaterial({
      name: `${color}-glow`,
      color: glowColor.clone().multiplyScalar(0.22),
      emissive: glowColor,
      emissiveIntensity: f.glow.intensity,
      metalness: 0.0,
      roughness: 0.35,
      toneMapped: false,
    });

    mats = { body, trim, glow };
    materialCache.set(color, mats);
    return mats;
  }

  /* ---------------- geometry ---------------- */

  function cacheKey(color, type) { return `${color}:${type}`; }

  function ensureGeometry(color, type) {
    const key = cacheKey(color, type);
    let rec = geometryCache.get(key);
    if (rec) return rec;
    rec = buildProcedural(type, color);
    geometryCache.set(key, rec);
    return rec;
  }

  function modelUrl(file) {
    try {
      return new URL(`../assets/models/${file}`, import.meta.url).href;
    } catch (err) {
      return `./assets/models/${file}`;
    }
  }

  function loadGLB(url) {
    return new Promise((resolve, reject) => {
      const loader = new GLTFLoader();
      loader.load(url, resolve, undefined, reject);
    });
  }

  async function loadFaction(color) {
    const file = MODEL_FILES[color];
    const url = modelUrl(file);
    let gltf = null;

    try {
      gltf = await loadGLB(url);
    } catch (err) {
      console.warn(`${LOG} could not load ${file} (${err && err.message ? err.message : err}) — ` +
        `using procedural ${FACTIONS[color].name} placeholders.`);
      sourceOf[color] = 'procedural';
      for (const type of PIECE_TYPES) ensureGeometry(color, type);
      return;
    }

    const glbRoot = (gltf && (gltf.scene || (gltf.scenes && gltf.scenes[0]))) || null;
    if (!glbRoot) {
      console.warn(`${LOG} ${file} contains no scene — using procedural placeholders.`);
      sourceOf[color] = 'procedural';
      for (const type of PIECE_TYPES) ensureGeometry(color, type);
      return;
    }
    glbRoot.updateMatrixWorld(true);

    const missing = [];
    for (const type of PIECE_TYPES) {
      const key = cacheKey(color, type);
      let rec = null;
      try {
        const node = findNodeByName(glbRoot, type);
        if (node) rec = geometryFromNode(node);
      } catch (err) {
        console.warn(`${LOG} failed to extract "${type}" from ${file}`, err);
        rec = null;
      }
      if (rec && rec.geometry) {
        rec.geometry = normalizeToContract(rec.geometry, type, { exact: false, label: `${file} ` });
        geometryCache.set(key, rec);
      } else {
        missing.push(type);
        ensureGeometry(color, type);
      }
    }

    if (missing.length === PIECE_TYPES.length) {
      console.warn(`${LOG} ${file} had none of the six named meshes — fully procedural.`);
      sourceOf[color] = 'procedural';
    } else if (missing.length) {
      console.warn(`${LOG} ${file} is missing mesh(es): ${missing.join(', ')} — ` +
        'those use procedural placeholders.');
      sourceOf[color] = 'partial';
    } else {
      sourceOf[color] = 'glb';
    }
  }

  /**
   * Load both faction GLBs. Never rejects: any failure degrades to procedural
   * geometry so the scene is always renderable.
   */
  function load() {
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
      materialsFor('white');
      materialsFor('black');
      await Promise.all([loadFaction('white'), loadFaction('black')]);
      return { white: sourceOf.white, black: sourceOf.black };
    })().catch((err) => {
      console.warn(`${LOG} load() failed unexpectedly — falling back to procedural`, err);
      for (const color of ['white', 'black']) {
        sourceOf[color] = 'procedural';
        for (const type of PIECE_TYPES) ensureGeometry(color, type);
      }
      return { white: sourceOf.white, black: sourceOf.black };
    });
    return loadPromise;
  }

  /* ---------------- captured rack ---------------- */

  const RACK = { gap: 1.15, colStep: 0.62, rowStep: 0.60, rows: 8 };

  /**
   * Two neat rows flanking the board. `side` is the *capturing* colour:
   * White's trophies sit on -X, Black's on +X.
   */
  function rackSlotPosition(side, index) {
    const sign = side === 'white' ? -1 : 1;
    const i = Math.max(0, index);
    const col = Math.floor(i / RACK.rows);
    const row = i % RACK.rows;
    return new THREE.Vector3(
      sign * (BOARD_HALF + RACK.gap + col * RACK.colStep),
      0,
      3.5 - row * RACK.rowStep,
    );
  }

  function rackSideFor(piece) {
    // A captured black piece is a White trophy, and vice versa.
    return piece.color === 'black' ? 'white' : 'black';
  }

  const capturedRack = {
    scale: RACK_SCALE,

    /** Slot position for the Nth trophy of `side` ('white' | 'black'). */
    slotFor(side, index) { return rackSlotPosition(normalizePieceColor(side), index); },

    /** Reserve (or re-read) this piece's slot without moving it. */
    reserve(piece) {
      if (!piece || piece.disposed) return new THREE.Vector3();
      const side = rackSideFor(piece);
      const list = captured[side];
      if (piece.rackIndex < 0 || list[piece.rackIndex] !== piece) {
        piece.rackIndex = list.length;
        list.push(piece);
      }
      piece.captured = true;
      return rackSlotPosition(side, piece.rackIndex);
    },

    /** Snap the piece into its slot at rack scale. */
    place(piece) {
      if (!piece || piece.disposed) return;
      const target = capturedRack.reserve(piece);
      piece.restoreMaterial();
      piece.object3D.position.copy(target);
      piece.object3D.rotation.set(0, 0, 0);
      piece.object3D.scale.setScalar(RACK_SCALE);
      piece.pivot.rotation.set(0, piece.facing, 0);
      piece.pivot.position.set(0, 0, 0);
      piece.object3D.visible = true;
    },

    add(piece) { capturedRack.place(piece); return piece; },

    remove(piece) {
      if (!piece) return;
      const side = rackSideFor(piece);
      const list = captured[side];
      const i = list.indexOf(piece);
      if (i >= 0) list.splice(i, 1);
      piece.rackIndex = -1;
      piece.captured = false;
      capturedRack.layout(side);
    },

    /** Trophies held by `side` ('white' returns the black pieces White took). */
    list(side) { return captured[normalizePieceColor(side)].slice(); },

    count(side) { return captured[normalizePieceColor(side)].length; },

    /** Re-flow one (or both) racks after a removal. */
    layout(side) {
      const sides = side ? [normalizePieceColor(side)] : ['white', 'black'];
      for (const s of sides) {
        captured[s].forEach((piece, i) => {
          piece.rackIndex = i;
          if (piece.disposed) return;
          piece.object3D.position.copy(rackSlotPosition(s, i));
          piece.object3D.scale.setScalar(RACK_SCALE);
        });
      }
    },

    clear() {
      for (const s of ['white', 'black']) {
        for (const piece of captured[s].slice()) destroyPiece(piece);
        captured[s].length = 0;
      }
    },
  };

  /* ---------------- piece lifecycle ---------------- */

  function setPieceTransform(piece) {
    const p = squareToWorld(piece.square);
    piece.object3D.position.set(p.x, 0, p.z);
    piece.object3D.rotation.set(0, 0, 0);
    piece.object3D.scale.setScalar(1);
    piece.pivot.rotation.set(0, piece.facing, 0);
    piece.pivot.position.set(0, 0, 0);
  }

  function addPiece(type, color, square) {
    if (disposed) return null;
    const t = normalizePieceType(type);
    const c = normalizePieceColor(color);
    const sq = String(square || '').toLowerCase();

    const rec = ensureGeometry(c, t);
    const mats = materialsFor(c);
    const materialArray = rec.slots.map((slot) => mats[slot] || mats.body);

    const mesh = new THREE.Mesh(rec.geometry, materialArray);
    mesh.name = `${c}-${t}`;
    mesh.castShadow = true;
    mesh.receiveShadow = true;

    const pivot = new THREE.Group();
    pivot.name = `${c}-${t}-pivot`;
    pivot.add(mesh);

    const object3D = new THREE.Group();
    object3D.name = `${c}-${t}-${sq || 'rack'}-${++idCounter}`;
    object3D.add(pivot);

    const piece = new PieceHandle(manager, t, c, sq || null, object3D, pivot, mesh);
    piece.id = idCounter;
    object3D.userData.piece = piece;
    mesh.userData.piece = piece;

    setPieceTransform(piece);
    root.add(object3D);
    allPieces.add(piece);

    if (sq) {
      const existing = bySquare.get(sq);
      if (existing && existing !== piece) destroyPiece(existing);
      bySquare.set(sq, piece);
    }
    return piece;
  }

  function getPiece(square) {
    if (!square) return null;
    return bySquare.get(String(square).toLowerCase()) || null;
  }

  /**
   * Move the render mirror from `from` to `to`.
   * `snap:false` updates only the bookkeeping — the AnimationQueue owns the
   * transform for the duration of the animation.
   */
  function movePiece(from, to, { snap = true } = {}) {
    const a = String(from || '').toLowerCase();
    const b = String(to || '').toLowerCase();
    const piece = bySquare.get(a);
    if (!piece) return null;

    const occupant = bySquare.get(b);
    if (occupant && occupant !== piece) removePiece(b, { toRack: true, snap: true });

    bySquare.delete(a);
    bySquare.set(b, piece);
    piece.square = b;
    if (snap) setPieceTransform(piece);
    return piece;
  }

  /**
   * Take a piece off the board.
   * toRack:false leaves it out of the trophy rack (used mid-animation by the
   * promotion choreography, which destroys the pawn instead).
   * snap:false leaves the transform alone so it can be animated away.
   */
  function removePiece(square, { toRack = true, snap = true } = {}) {
    const sq = String(square || '').toLowerCase();
    const piece = bySquare.get(sq);
    if (!piece) return null;
    bySquare.delete(sq);
    piece.square = null;
    piece.captured = true;
    if (toRack) {
      const target = capturedRack.reserve(piece);
      if (snap) capturedRack.place(piece);
      else piece._rackTarget = target;
    } else if (snap) {
      piece.object3D.visible = false;
    }
    return piece;
  }

  function destroyPiece(piece) {
    if (!piece || piece.disposed) return;
    piece.restoreMaterial();
    piece.disposed = true;
    if (piece.square && bySquare.get(piece.square) === piece) bySquare.delete(piece.square);
    piece.square = null;
    for (const s of ['white', 'black']) {
      const i = captured[s].indexOf(piece);
      if (i >= 0) captured[s].splice(i, 1);
    }
    if (piece.object3D.parent) piece.object3D.parent.remove(piece.object3D);
    allPieces.delete(piece);
  }

  function clear() {
    for (const piece of Array.from(allPieces)) destroyPiece(piece);
    bySquare.clear();
    allPieces.clear();
    captured.white.length = 0;
    captured.black.length = 0;
  }

  function boardOf(parsed) {
    if (!parsed) return {};
    if (parsed.board && typeof parsed.board === 'object') return parsed.board;
    if (parsed.pieces && typeof parsed.pieces === 'object') return parsed.pieces;
    return parsed;
  }

  /** Wipe the board and rebuild it from a parsed FEN. */
  function spawnFromPosition(parsed) {
    clear();
    const board = boardOf(parsed);
    for (const [square, entry] of Object.entries(board)) {
      if (!entry || !entry.type) continue;
      addPiece(entry.type, entry.color, square);
    }
    return allPieces.size;
  }

  /**
   * Force-reconcile the whole board to a parsed FEN (CONTRACT.md 11.6).
   * Reuses existing pieces where possible so the scene doesn't flicker, and
   * clears any leftover animation state (scale/rotation/opacity).
   */
  function snapToPosition(parsed) {
    const board = boardOf(parsed);
    const desired = new Map();
    for (const [square, entry] of Object.entries(board)) {
      if (!entry || !entry.type) continue;
      desired.set(String(square).toLowerCase(), {
        type: normalizePieceType(entry.type),
        color: normalizePieceColor(entry.color),
      });
    }

    const satisfied = new Set();
    const pool = [];

    for (const piece of Array.from(allPieces)) {
      const want = piece.square ? desired.get(piece.square) : null;
      if (!piece.captured && want && want.type === piece.type && want.color === piece.color
        && !satisfied.has(piece.square)) {
        satisfied.add(piece.square);
        piece.resetVisualState();
        setPieceTransform(piece);
      } else {
        if (piece.square && bySquare.get(piece.square) === piece) bySquare.delete(piece.square);
        piece.square = null;
        pool.push(piece);
      }
    }

    let changed = 0;
    for (const [square, want] of desired) {
      if (satisfied.has(square)) continue;
      const idx = pool.findIndex((p) => p.type === want.type && p.color === want.color);
      if (idx >= 0) {
        const piece = pool.splice(idx, 1)[0];
        if (piece.captured) capturedRack.remove(piece);
        piece.captured = false;
        piece.square = square;
        bySquare.set(square, piece);
        piece.resetVisualState();
        setPieceTransform(piece);
      } else {
        addPiece(want.type, want.color, square);
      }
      satisfied.add(square);
      changed++;
    }

    for (const piece of pool) {
      if (!piece.captured) {
        piece.captured = true;
        capturedRack.reserve(piece);
      }
      piece.resetVisualState();
      capturedRack.place(piece);
      changed++;
    }
    capturedRack.layout();

    return changed;
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    clear();
    if (root.parent) root.parent.remove(root);
    for (const rec of geometryCache.values()) {
      if (rec && rec.geometry) rec.geometry.dispose();
    }
    geometryCache.clear();
    for (const mats of materialCache.values()) {
      for (const m of Object.values(mats)) { if (m && m.dispose) m.dispose(); }
    }
    materialCache.clear();
  }

  const manager = {
    // contract surface
    load,
    spawnFromPosition,
    getPiece,
    movePiece,
    addPiece,
    removePiece,
    snapToPosition,
    capturedRack,
    dispose,

    // useful extras (additive, never required by other modules)
    root,
    clear,
    getAllPieces: () => Array.from(allPieces),
    getPieces: (color) => Array.from(allPieces).filter((p) => !p.captured
      && (!color || p.color === normalizePieceColor(color))),
    findKing: (color) => Array.from(allPieces).find((p) => !p.captured
      && p.type === 'king' && p.color === normalizePieceColor(color)) || null,
    destroyPiece,
    materialsFor,
    squareToWorld,
    get modelSource() { return { white: sourceOf.white, black: sourceOf.black }; },
    get count() { return allPieces.size; },
  };

  return manager;
}
