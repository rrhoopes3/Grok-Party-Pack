/**
 * @file board.js — BattleChess board geometry, coordinate math and square highlights.
 *
 * This module owns the CANONICAL coordinate system for the whole project
 * (CONTRACT.md section 3). Every other module must import
 * {@link squareToWorld}, {@link worldToSquare}, {@link squareToIndices},
 * {@link SQUARE_SIZE} and {@link BOARD_HALF} from here rather than
 * re-deriving them.
 *
 *   file 'a'..'h' -> f = 0..7      rank '1'..'8' -> r = 0..7
 *   x = f - 3.5    y = 0    z = 3.5 - r
 *   a1 = (-3.5, 0, +3.5)           h8 = (+3.5, 0, -3.5)
 *
 * White home ranks (1,2) sit at positive Z; Black home ranks (7,8) at negative Z.
 * The playable top surface of the board is exactly y = 0.
 */

import {
  AdditiveBlending,
  BoxGeometry,
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  Color,
  DoubleSide,
  DynamicDrawUsage,
  Group,
  LinearFilter,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  PlaneGeometry,
  ShaderMaterial,
  SRGBColorSpace,
} from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

// ---------------------------------------------------------------------------
// Canonical coordinate constants
// ---------------------------------------------------------------------------

/**
 * Edge length of a single board square in world units.
 * @type {number}
 */
export const SQUARE_SIZE = 1.0;

/**
 * Half the playable board extent in world units (the board spans -4..+4 in X and Z).
 * @type {number}
 */
export const BOARD_HALF = 4.0;

/** Offset from index space to world space: index 0..7 -> world -3.5..+3.5. */
const CENTER_OFFSET = BOARD_HALF - SQUARE_SIZE * 0.5; // 3.5

const FILES = 'abcdefgh';
const SQUARE_RE = /^([a-h])([1-8])$/;

// ---------------------------------------------------------------------------
// Geometry / styling constants
// ---------------------------------------------------------------------------

const PANEL_SIZE = 0.94;    // inlaid panel footprint (leaves a 0.06 seam)
const PANEL_DEPTH = 0.06;   // panel thickness; top face sits at y = 0
const SEAM_Y = -0.004;      // glowing seam plate, visible down inside the gaps
const HIGHLIGHT_Y = 0.007;  // highlight decals float just above the panel tops
const BEZEL_TOP = 0.07;
const BEZEL_BOTTOM = -0.09;
const BEZEL_INNER = BOARD_HALF + 0.03;  // 4.03
const BEZEL_OUTER = BOARD_HALF + 0.85;  // 4.85
const LABEL_BAND = (BEZEL_INNER + BEZEL_OUTER) * 0.5; // centre of the label ring
const LABEL_WIDTH = 0.52;
const LABEL_Y = BEZEL_TOP + 0.0025;
const DECK_TOP = -0.03;
const DECK_DEPTH = 0.26;

const COLOR_LIGHT_SQUARE = 0xcfd6de; // brushed steel
const COLOR_DARK_SQUARE = 0x2b3038;  // graphite
const COLOR_SEAM = 0x2a9df4;         // Starfleet azure seam grid
const COLOR_BEZEL = 0x14171c;        // dark void metal
const COLOR_DECK = 0x0a0c11;
const COLOR_BRASS = 0xa8801f;        // tarnished brass inlay

/**
 * Highlight kinds understood by {@link createBoard}'s `highlight()`.
 * Higher priority wins when several kinds land on the same square.
 * `style` selects a branch in the decal fragment shader.
 * @type {Object<string, {style:number, color:number, alpha:number, priority:number}>}
 */
const HIGHLIGHT_KINDS = {
  legal:  { style: 1, color: 0x39d6ff, alpha: 0.42, priority: 1 },
  from:   { style: 3, color: 0x6fa8ff, alpha: 0.62, priority: 2 },
  to:     { style: 4, color: 0x39d6ff, alpha: 0.85, priority: 3 },
  select: { style: 2, color: 0xffd76a, alpha: 0.95, priority: 4 },
  check:  { style: 5, color: 0xff3b1f, alpha: 1.0,  priority: 5 },
};

// ---------------------------------------------------------------------------
// Coordinate helpers (canonical — see CONTRACT.md section 3)
// ---------------------------------------------------------------------------

/**
 * Convert an algebraic square name to zero-based board indices.
 *
 * @param {string} square Algebraic square name, e.g. `"e4"` (case-insensitive).
 * @returns {{file:number, rank:number}} `file` 0..7 for a..h, `rank` 0..7 for 1..8.
 * @throws {Error} If `square` is not a legal algebraic square name.
 *
 * @example
 * squareToIndices('e4'); // -> { file: 4, rank: 3 }
 */
export function squareToIndices(square) {
  const m = typeof square === 'string' ? SQUARE_RE.exec(square.toLowerCase()) : null;
  if (!m) throw new Error(`board.js: invalid square "${square}"`);
  return { file: FILES.indexOf(m[1]), rank: Number(m[2]) - 1 };
}

/**
 * Convert an algebraic square name to the world-space centre of that square.
 * The returned Y is always 0 — the playable surface of the board.
 *
 * @param {string} square Algebraic square name, e.g. `"e4"`.
 * @param {{x:number,y:number,z:number}} [target] Optional vector-like object to write into
 *   (any object with x/y/z, typically a `THREE.Vector3`), avoiding an allocation.
 * @returns {{x:number,y:number,z:number}} The square centre; a plain `{x,y,z}` object when
 *   no `target` is supplied. Callers that need a real `Vector3` should pass one in, or
 *   spread the result — the shape is identical either way.
 * @throws {Error} If `square` is not a legal algebraic square name.
 *
 * @example
 * squareToWorld('a1'); // -> { x: -3.5, y: 0, z: 3.5 }
 * squareToWorld('e4', new THREE.Vector3());
 */
export function squareToWorld(square, target) {
  const { file, rank } = squareToIndices(square);
  const x = file * SQUARE_SIZE - CENTER_OFFSET;
  const z = CENTER_OFFSET - rank * SQUARE_SIZE;
  if (target) {
    target.x = x;
    target.y = 0;
    target.z = z;
    return target;
  }
  return { x, y: 0, z };
}

/**
 * Convert a world-space position to the algebraic square it sits over.
 * Y is ignored entirely; only the XZ footprint matters.
 *
 * @param {{x:number, z:number}} vec3 A `THREE.Vector3` (or any `{x,z}`) in world space.
 * @returns {string|null} Algebraic square name, or `null` if the point is off the board.
 *
 * @example
 * worldToSquare(new THREE.Vector3(-3.5, 0, 3.5)); // -> 'a1'
 * worldToSquare(new THREE.Vector3(99, 0, 0));     // -> null
 */
export function worldToSquare(vec3) {
  if (!vec3 || typeof vec3.x !== 'number' || typeof vec3.z !== 'number') return null;
  const file = Math.round((vec3.x + CENTER_OFFSET) / SQUARE_SIZE);
  const rank = Math.round((CENTER_OFFSET - vec3.z) / SQUARE_SIZE);
  if (file < 0 || file > 7 || rank < 0 || rank > 7) return null;
  return FILES[file] + (rank + 1);
}

/** Internal: 0..63 flat index used by the highlight decal buffers. */
function squareToFlat(square) {
  const { file, rank } = squareToIndices(square);
  return rank * 8 + file;
}

// ---------------------------------------------------------------------------
// Highlight decal shader
// ---------------------------------------------------------------------------

const DECAL_VERT = /* glsl */ `
  attribute vec4 aColor;
  attribute vec2 aMeta;      // x = style, y = phase
  varying vec2 vUvLocal;
  varying vec4 vColor;
  varying vec2 vMeta;
  void main() {
    vUvLocal = uv;
    vColor = aColor;
    vMeta = aMeta;
    gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
  }
`;

const DECAL_FRAG = /* glsl */ `
  uniform float uTime;
  varying vec2 vUvLocal;
  varying vec4 vColor;
  varying vec2 vMeta;

  // Signed distance to the edge of the unit square, in "inset" units (0 at edge, 0.5 at centre).
  float edgeDist( vec2 uv ) {
    vec2 d = min( uv, 1.0 - uv );
    return min( d.x, d.y );
  }

  void main() {
    if ( vColor.a <= 0.001 ) discard;

    float style = vMeta.x;
    float phase = vMeta.y;
    vec2 p = vUvLocal - 0.5;
    float r = length( p );
    float e = edgeDist( vUvLocal );

    // Frame: bright band hugging the panel border.
    float frame = smoothstep( 0.105, 0.055, e ) * smoothstep( 0.010, 0.028, e );
    // Corner brackets: only near the corners, for the "targeting reticle" look.
    vec2 a = abs( p );
    float corner = smoothstep( 0.26, 0.40, max( a.x, a.y ) ) * smoothstep( 0.30, 0.44, min( a.x, a.y ) );
    // Soft interior wash that falls off toward the edge.
    float wash = smoothstep( 0.52, 0.10, r );
    // Centre disc for 'legal' markers.
    float dot0 = smoothstep( 0.185, 0.120, r );
    float ring = smoothstep( 0.115, 0.085, abs( r - 0.325 ) );

    float pulse = 0.5 + 0.5 * sin( uTime * 3.1 + phase );
    float intensity = 0.0;

    if ( style < 0.5 ) {
      // 0 — flat programmatic fill (setSquareEmissive)
      intensity = 0.75 * wash + 0.55 * frame;
    } else if ( style < 1.5 ) {
      // 1 — legal move: quiet centre dot with a whisper of wash
      intensity = 1.0 * dot0 + 0.16 * wash;
    } else if ( style < 2.5 ) {
      // 2 — selection: pulsing ring plus corner brackets
      intensity = ( 0.85 + 0.55 * pulse ) * ring + 0.9 * corner + 0.10 * wash;
    } else if ( style < 3.5 ) {
      // 3 — move origin: hollow frame, faint interior
      intensity = 0.95 * frame + 0.18 * wash;
    } else if ( style < 4.5 ) {
      // 4 — move destination: solid frame + strong wash + reticle corners
      intensity = 1.15 * frame + 0.62 * wash + 0.45 * corner;
    } else {
      // 5 — check: hard pulsing alarm wash and frame
      float beat = 0.35 + 0.65 * pow( pulse, 1.6 );
      intensity = beat * ( 0.95 * wash + 1.25 * frame );
    }

    float alpha = clamp( intensity, 0.0, 2.2 ) * vColor.a;
    if ( alpha <= 0.002 ) discard;
    gl_FragColor = vec4( vColor.rgb * alpha, alpha );
  }
`;

/**
 * Build the 64-quad decal geometry used for square highlights.
 * One quad per square, laid out flat at {@link HIGHLIGHT_Y}. Per-vertex `aColor`
 * (rgb + alpha) and `aMeta` (style, phase) let all 64 squares be styled
 * independently while still rendering in a single draw call.
 * @returns {BufferGeometry}
 */
function buildDecalGeometry() {
  const half = PANEL_SIZE * 0.5;
  const positions = new Float32Array(64 * 4 * 3);
  const uvs = new Float32Array(64 * 4 * 2);
  const colors = new Float32Array(64 * 4 * 4);
  const meta = new Float32Array(64 * 4 * 2);
  const indices = new Uint16Array(64 * 6);

  for (let rank = 0; rank < 8; rank++) {
    for (let file = 0; file < 8; file++) {
      const i = rank * 8 + file;
      const cx = file * SQUARE_SIZE - CENTER_OFFSET;
      const cz = CENTER_OFFSET - rank * SQUARE_SIZE;
      // vertex order: (-x,-z) (+x,-z) (+x,+z) (-x,+z)
      const corners = [
        [cx - half, cz - half, 0, 0],
        [cx + half, cz - half, 1, 0],
        [cx + half, cz + half, 1, 1],
        [cx - half, cz + half, 0, 1],
      ];
      for (let v = 0; v < 4; v++) {
        const vi = i * 4 + v;
        positions[vi * 3 + 0] = corners[v][0];
        positions[vi * 3 + 1] = HIGHLIGHT_Y;
        positions[vi * 3 + 2] = corners[v][1];
        uvs[vi * 2 + 0] = corners[v][2];
        uvs[vi * 2 + 1] = corners[v][3];
      }
      const o = i * 4;
      indices[i * 6 + 0] = o + 0;
      indices[i * 6 + 1] = o + 2;
      indices[i * 6 + 2] = o + 1;
      indices[i * 6 + 3] = o + 0;
      indices[i * 6 + 4] = o + 3;
      indices[i * 6 + 5] = o + 2;
    }
  }

  const geo = new BufferGeometry();
  geo.setAttribute('position', new BufferAttribute(positions, 3));
  geo.setAttribute('uv', new BufferAttribute(uvs, 2));
  const colorAttr = new BufferAttribute(colors, 4);
  colorAttr.setUsage(DynamicDrawUsage);
  geo.setAttribute('aColor', colorAttr);
  const metaAttr = new BufferAttribute(meta, 2);
  metaAttr.setUsage(DynamicDrawUsage);
  geo.setAttribute('aMeta', metaAttr);
  geo.setIndex(new BufferAttribute(indices, 1));
  geo.computeBoundingSphere();
  return geo;
}

// ---------------------------------------------------------------------------
// Coordinate label textures
// ---------------------------------------------------------------------------

/**
 * Draw eight engraved-looking glyphs across a wide canvas and wrap it in a texture.
 * No font files and no network: only generic CSS font families are used.
 *
 * @param {string[]} glyphs Exactly eight glyphs, laid out left-to-right in texture space.
 * @returns {CanvasTexture}
 */
function makeLabelStripTexture(glyphs) {
  const canvas = document.createElement('canvas');
  canvas.width = 2048;
  canvas.height = 144;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const cell = canvas.width / 8;
  const cy = canvas.height * 0.54;

  for (let i = 0; i < 8; i++) {
    const cx = cell * (i + 0.5);
    // Engraved illusion: a dark bevel above, a light bevel below, glyph on top.
    ctx.font = '600 78px "Eurostile", "Bahnschrift", "Segoe UI", system-ui, sans-serif';
    ctx.fillStyle = 'rgba(0,0,0,0.85)';
    ctx.fillText(glyphs[i], cx, cy - 2.5);
    ctx.fillStyle = 'rgba(190,214,240,0.30)';
    ctx.fillText(glyphs[i], cx, cy + 2.5);
    ctx.fillStyle = 'rgba(158,182,208,0.92)';
    ctx.fillText(glyphs[i], cx, cy);
  }

  const tex = new CanvasTexture(canvas);
  tex.colorSpace = SRGBColorSpace;
  tex.anisotropy = 4;
  tex.minFilter = LinearFilter;
  tex.magFilter = LinearFilter;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Build a radial-falloff alpha ramp used to fade the emissive seam grid toward
 * the board edges so the centre reads brightest.
 * @returns {CanvasTexture}
 */
function makeSeamFalloffTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d');
  const g = ctx.createRadialGradient(128, 128, 10, 128, 128, 150);
  g.addColorStop(0.0, '#ffffff');
  g.addColorStop(0.55, '#bfbfbf');
  g.addColorStop(1.0, '#4a4a4a');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 256, 256);
  const tex = new CanvasTexture(canvas);
  tex.colorSpace = SRGBColorSpace;
  tex.needsUpdate = true;
  return tex;
}

// ---------------------------------------------------------------------------
// Static sub-mesh builders
// ---------------------------------------------------------------------------

/**
 * Merge the 32 light or 32 dark inlaid panels into a single geometry so the whole
 * chequer pattern costs two draw calls instead of sixty-four.
 * @param {boolean} light `true` for the light (brushed steel) squares.
 * @returns {BufferGeometry}
 */
function buildPanelGeometry(light) {
  const parts = [];
  const proto = new BoxGeometry(PANEL_SIZE, PANEL_DEPTH, PANEL_SIZE);
  for (let rank = 0; rank < 8; rank++) {
    for (let file = 0; file < 8; file++) {
      // a1 (file 0, rank 0) is a dark square in standard chess.
      const isLight = (file + rank) % 2 === 1;
      if (isLight !== light) continue;
      const x = file * SQUARE_SIZE - CENTER_OFFSET;
      const z = CENTER_OFFSET - rank * SQUARE_SIZE;
      parts.push(proto.clone().translate(x, -PANEL_DEPTH * 0.5, z));
    }
  }
  const merged = mergeGeometries(parts, false);
  parts.forEach((p) => p.dispose());
  proto.dispose();
  merged.computeBoundingSphere();
  return merged;
}

/**
 * Build a rectangular ring out of four merged boxes.
 * @param {number} inner Half-extent of the inner hole.
 * @param {number} outer Half-extent of the outer edge.
 * @param {number} bottom Y of the ring's underside.
 * @param {number} top Y of the ring's top face.
 * @returns {BufferGeometry}
 */
function buildRingGeometry(inner, outer, bottom, top) {
  const h = top - bottom;
  const cy = (top + bottom) * 0.5;
  const span = outer - inner;
  const mid = (outer + inner) * 0.5;
  const parts = [
    new BoxGeometry(outer * 2, h, span).translate(0, cy, -mid), // far  (-Z)
    new BoxGeometry(outer * 2, h, span).translate(0, cy, mid),  // near (+Z)
    new BoxGeometry(span, h, inner * 2).translate(-mid, cy, 0), // left (-X)
    new BoxGeometry(span, h, inner * 2).translate(mid, cy, 0),  // right(+X)
  ];
  const merged = mergeGeometries(parts, false);
  parts.forEach((p) => p.dispose());
  merged.computeBoundingSphere();
  return merged;
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} BattleChessBoard
 * @property {Group} group Root object to add to the scene. Board top surface is y = 0.
 * @property {(square:string, kind:string) => void} highlight Add a highlight of `kind` to a square.
 * @property {(kind?:string, square?:string) => void} clearHighlights Clear all highlights, or
 *   just one kind, or one kind on one square.
 * @property {(square:string, color?:(number|string|Color|null), intensity?:number) => void} setSquareEmissive
 *   Programmatic per-square emissive override, independent of the highlight kinds.
 * @property {() => void} dispose Free every geometry, material and texture the board owns.
 */

/**
 * Create the BattleChess board: 64 inlaid panels, a glowing seam grid, a raised
 * bezel with brass inlay and engraved coordinate labels, and a single-draw-call
 * highlight decal layer.
 *
 * Total cost is roughly a dozen draw calls regardless of highlight count.
 *
 * @returns {BattleChessBoard}
 *
 * @example
 * const board = createBoard();
 * scene.add(board.group);
 * board.highlight('e2', 'from');
 * board.highlight('e4', 'to');
 * board.clearHighlights();      // wipe everything
 * board.clearHighlights('legal');// wipe just the legal-move dots
 */
export function createBoard() {
  const group = new Group();
  group.name = 'BattleChessBoard';

  /** Everything we own and must dispose(). */
  const geometries = [];
  const materials = [];
  const textures = [];

  const track = (mesh) => {
    if (mesh.geometry && !geometries.includes(mesh.geometry)) geometries.push(mesh.geometry);
    if (mesh.material && !materials.includes(mesh.material)) materials.push(mesh.material);
    return mesh;
  };

  // --- deck ---------------------------------------------------------------
  const deckGeo = new BoxGeometry(BEZEL_OUTER * 2 - 0.06, DECK_DEPTH, BEZEL_OUTER * 2 - 0.06);
  // Board surfaces are satin, not mirror. At metalness ~0.9 / roughness ~0.3
  // the deck and panels mirrored the overhead key straight into the camera on
  // the top/white presets and clipped to white. 0.35/0.55 keeps the brushed
  // look while staying inside range. Verified on rendered frames, all presets.
  const deckMat = new MeshStandardMaterial({
    color: COLOR_DECK,
    metalness: 0.35,
    roughness: 0.55,
    envMapIntensity: 0.6,
  });
  const deck = new Mesh(deckGeo, deckMat);
  deck.position.y = DECK_TOP - DECK_DEPTH * 0.5;
  deck.castShadow = true;
  deck.receiveShadow = true;
  deck.name = 'boardDeck';
  group.add(track(deck));

  // --- emissive seam plate (visible in the 0.06 gaps between panels) ------
  const seamFalloff = makeSeamFalloffTexture();
  textures.push(seamFalloff);
  const seamGeo = new PlaneGeometry(BOARD_HALF * 2, BOARD_HALF * 2);
  const seamMat = new MeshBasicMaterial({
    color: new Color(COLOR_SEAM).multiplyScalar(0.42),
    map: seamFalloff,
    toneMapped: true,
    side: DoubleSide,
  });
  const seam = new Mesh(seamGeo, seamMat);
  seam.rotation.x = -Math.PI / 2;
  seam.position.y = SEAM_Y;
  seam.name = 'boardSeamGrid';
  group.add(track(seam));

  // --- 64 inlaid panels, merged into two meshes ---------------------------
  const lightGeo = buildPanelGeometry(true);
  const lightMat = new MeshStandardMaterial({
    color: COLOR_LIGHT_SQUARE,
    metalness: 0.35,
    roughness: 0.55,
    envMapIntensity: 1.05,
  });
  const lightMesh = new Mesh(lightGeo, lightMat);
  lightMesh.receiveShadow = true;
  lightMesh.castShadow = false;
  lightMesh.name = 'boardSquaresLight';
  group.add(track(lightMesh));

  const darkGeo = buildPanelGeometry(false);
  const darkMat = new MeshStandardMaterial({
    color: COLOR_DARK_SQUARE,
    metalness: 0.35,
    roughness: 0.55,
    envMapIntensity: 0.95,
  });
  const darkMesh = new Mesh(darkGeo, darkMat);
  darkMesh.receiveShadow = true;
  darkMesh.castShadow = false;
  darkMesh.name = 'boardSquaresDark';
  group.add(track(darkMesh));

  // --- raised bezel + brass inlay ring ------------------------------------
  const bezelGeo = buildRingGeometry(BEZEL_INNER, BEZEL_OUTER, BEZEL_BOTTOM, BEZEL_TOP);
  const bezelMat = new MeshStandardMaterial({
    color: COLOR_BEZEL,
    metalness: 0.35,
    roughness: 0.55,
    envMapIntensity: 1.0,
  });
  const bezel = new Mesh(bezelGeo, bezelMat);
  bezel.castShadow = true;
  bezel.receiveShadow = true;
  bezel.name = 'boardBezel';
  group.add(track(bezel));

  const inlayGeo = buildRingGeometry(BEZEL_INNER + 0.055, BEZEL_INNER + 0.105, BEZEL_TOP - 0.012, BEZEL_TOP + 0.004);
  const inlayMat = new MeshStandardMaterial({
    color: COLOR_BRASS,
    metalness: 0.35,
    roughness: 0.55,
    emissive: new Color(COLOR_BRASS).multiplyScalar(0.06),
    envMapIntensity: 1.4,
  });
  const inlay = new Mesh(inlayGeo, inlayMat);
  inlay.name = 'boardInlay';
  group.add(track(inlay));

  const outerInlayGeo = buildRingGeometry(BEZEL_OUTER - 0.11, BEZEL_OUTER - 0.06, BEZEL_TOP - 0.012, BEZEL_TOP + 0.004);
  const outerInlay = new Mesh(outerInlayGeo, inlayMat);
  outerInlay.name = 'boardInlayOuter';
  geometries.push(outerInlayGeo);
  group.add(outerInlay);

  // --- engraved coordinate labels -----------------------------------------
  const fileGlyphs = FILES.split('');
  const rankGlyphs = ['1', '2', '3', '4', '5', '6', '7', '8'];

  // Textures are laid out left-to-right in *texture* space; the mesh rotations
  // below map texture +X onto the world direction noted in each comment.
  const texFilesNear = makeLabelStripTexture(fileGlyphs);                 // tex +X -> world +X
  const texFilesFar = makeLabelStripTexture([...fileGlyphs].reverse());   // tex +X -> world -X
  const texRanksLeft = makeLabelStripTexture(rankGlyphs);                 // tex +X -> world -Z
  const texRanksRight = makeLabelStripTexture([...rankGlyphs].reverse()); // tex +X -> world +Z
  textures.push(texFilesNear, texFilesFar, texRanksLeft, texRanksRight);

  const labelGeo = new PlaneGeometry(BOARD_HALF * 2, LABEL_WIDTH);
  geometries.push(labelGeo);

  /**
   * @param {CanvasTexture} tex
   * @param {[number,number,number]} pos
   * @param {number} rotZ In-plane spin applied before the plane is laid flat.
   * @param {string} name
   */
  const addLabelStrip = (tex, pos, rotZ, name) => {
    const mat = new MeshStandardMaterial({
      map: tex,
      emissive: 0xffffff,
      emissiveMap: tex,
      emissiveIntensity: 0.22,
      transparent: true,
      depthWrite: false,
      metalness: 0.15,
      roughness: 0.65,
      color: 0xffffff,
    });
    materials.push(mat);
    const mesh = new Mesh(labelGeo, mat);
    mesh.position.set(pos[0], pos[1], pos[2]);
    // Euler order 'XYZ' composes as Rx * Ry * Rz, so rotZ spins the quad within
    // its own plane first, then Rx(-90) lays it flat on the bezel.
    mesh.rotation.set(-Math.PI / 2, 0, rotZ);
    mesh.renderOrder = 1;
    mesh.name = name;
    group.add(mesh);
  };

  addLabelStrip(texFilesNear, [0, LABEL_Y, LABEL_BAND], 0, 'boardLabelsFilesNear');
  addLabelStrip(texFilesFar, [0, LABEL_Y, -LABEL_BAND], Math.PI, 'boardLabelsFilesFar');
  addLabelStrip(texRanksLeft, [-LABEL_BAND, LABEL_Y, 0], Math.PI / 2, 'boardLabelsRanksLeft');
  addLabelStrip(texRanksRight, [LABEL_BAND, LABEL_Y, 0], -Math.PI / 2, 'boardLabelsRanksRight');

  // --- highlight decal layer ----------------------------------------------
  const decalGeo = buildDecalGeometry();
  const decalMat = new ShaderMaterial({
    uniforms: { uTime: { value: 0 } },
    vertexShader: DECAL_VERT,
    fragmentShader: DECAL_FRAG,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: AdditiveBlending,
    toneMapped: false,
  });
  const decals = new Mesh(decalGeo, decalMat);
  decals.frustumCulled = false;
  decals.renderOrder = 2;
  decals.name = 'boardHighlights';
  geometries.push(decalGeo);
  materials.push(decalMat);
  group.add(decals);

  const startedAt = performance.now();
  decals.onBeforeRender = () => {
    decalMat.uniforms.uTime.value = (performance.now() - startedAt) / 1000;
  };

  // --- highlight state ----------------------------------------------------

  /** @type {Array<Set<string>>} kinds currently applied to each of the 64 squares. */
  const kindsBySquare = Array.from({ length: 64 }, () => new Set());
  /** @type {Array<{color:Color, intensity:number}|null>} manual overrides. */
  const manualBySquare = new Array(64).fill(null);

  const colorAttr = decalGeo.getAttribute('aColor');
  const metaAttr = decalGeo.getAttribute('aMeta');
  const scratch = new Color();

  /**
   * Rewrite the four vertices of one square's decal quad from its current state.
   * The manual override (setSquareEmissive) wins; otherwise the highest-priority
   * highlight kind wins; otherwise the quad is written fully transparent.
   * @param {number} flat 0..63
   */
  function refreshSquare(flat) {
    let r = 0, g = 0, b = 0, a = 0, style = 0;
    const manual = manualBySquare[flat];
    if (manual && manual.intensity > 0) {
      r = manual.color.r; g = manual.color.g; b = manual.color.b;
      a = manual.intensity;
      style = 0;
    } else {
      let best = null;
      for (const kind of kindsBySquare[flat]) {
        const def = HIGHLIGHT_KINDS[kind];
        if (def && (!best || def.priority > best.priority)) best = def;
      }
      if (best) {
        scratch.setHex(best.color);
        r = scratch.r; g = scratch.g; b = scratch.b;
        a = best.alpha;
        style = best.style;
      }
    }
    // A per-square phase keeps pulses from marching in lockstep.
    const phase = (flat % 8) * 0.37 + Math.floor(flat / 8) * 0.19;
    for (let v = 0; v < 4; v++) {
      const vi = flat * 4 + v;
      colorAttr.setXYZW(vi, r, g, b, a);
      metaAttr.setXY(vi, style, phase);
    }
    colorAttr.needsUpdate = true;
    metaAttr.needsUpdate = true;
  }

  for (let i = 0; i < 64; i++) refreshSquare(i);

  /**
   * Add a highlight to a square. Multiple kinds may coexist on one square; the
   * highest-priority kind is the one rendered (check > select > to > from > legal).
   *
   * @param {string} square Algebraic square, e.g. `"e4"`.
   * @param {'from'|'to'|'check'|'select'|'legal'} kind Highlight treatment.
   * @returns {void}
   * @throws {Error} If the square name or the kind is unknown.
   */
  function highlight(square, kind) {
    if (!HIGHLIGHT_KINDS[kind]) {
      throw new Error(`board.js: unknown highlight kind "${kind}"`);
    }
    const flat = squareToFlat(square);
    kindsBySquare[flat].add(kind);
    refreshSquare(flat);
  }

  /**
   * Clear highlights. With no arguments this wipes every highlight on the board,
   * which is the safe call to make between moves so no state leaks forward.
   *
   * @param {('from'|'to'|'check'|'select'|'legal')} [kind] Restrict the clear to one kind.
   * @param {string} [square] Restrict the clear further to a single square.
   * @returns {void}
   */
  function clearHighlights(kind, square) {
    if (square !== undefined) {
      const flat = squareToFlat(square);
      if (kind === undefined) kindsBySquare[flat].clear();
      else kindsBySquare[flat].delete(kind);
      refreshSquare(flat);
      return;
    }
    for (let i = 0; i < 64; i++) {
      if (kind === undefined) {
        if (kindsBySquare[i].size === 0) continue;
        kindsBySquare[i].clear();
      } else if (!kindsBySquare[i].delete(kind)) {
        continue;
      }
      refreshSquare(i);
    }
  }

  /**
   * Programmatically drive one square's emissive decal, bypassing the named
   * highlight kinds entirely. Useful for bespoke effects (dissolve pools, threat
   * maps, victory sweeps) driven by fx.js or animation.js.
   *
   * While a manual override is active it takes precedence over any highlight on
   * that square; clearing it restores whatever highlights are still applied.
   *
   * @param {string} square Algebraic square, e.g. `"e4"`.
   * @param {number|string|Color|null} [color] Emissive colour. `null` (or omitted) clears
   *   the override.
   * @param {number} [intensity=1] Strength, 0..~2. Zero or less also clears the override.
   * @returns {void}
   */
  function setSquareEmissive(square, color = null, intensity = 1) {
    const flat = squareToFlat(square);
    if (color === null || color === undefined || !(intensity > 0)) {
      manualBySquare[flat] = null;
    } else {
      const c = color instanceof Color ? color.clone() : new Color(color);
      manualBySquare[flat] = { color: c, intensity };
    }
    refreshSquare(flat);
  }

  /**
   * Release every GPU resource this board allocated and empty the group.
   * Safe to call more than once.
   * @returns {void}
   */
  function dispose() {
    decals.onBeforeRender = () => {};
    group.clear();
    for (const g of geometries) g.dispose();
    for (const m of materials) m.dispose();
    for (const t of textures) t.dispose();
    geometries.length = 0;
    materials.length = 0;
    textures.length = 0;
  }

  return {
    group,
    highlight,
    clearHighlights,
    setSquareEmissive,
    dispose,
    // Re-exported on the instance for convenience; the module-level exports
    // remain the canonical import site.
    squareToWorld,
    worldToSquare,
    squareToIndices,
    SQUARE_SIZE,
    BOARD_HALF,
  };
}
