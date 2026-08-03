/**
 * fx.js — BattleChess combat VFX.
 *
 * Owns every particle, beam, shield and dissolve effect in the scene. All
 * textures are generated procedurally on a <canvas>; nothing here touches the
 * network or the filesystem.
 *
 * Public surface (see CONTRACT.md §8):
 *
 *   const fx = createFX({ scene });
 *   fx.impactFlash(position, color)
 *   fx.dissolve(mesh, color, durationMs)      -> Promise<void>
 *   fx.embers(position, color, count)
 *   fx.trail(mesh, color)                     -> { stop() }
 *   fx.shieldCrack(position, color)
 *   fx.lightColumn(position, color, durationMs) -> Promise<void>
 *   fx.warningHalo(mesh, color)
 *   fx.removeHalo(mesh)
 *   fx.update(dt)                             // dt in SECONDS
 *   fx.dispose()
 *
 * Every entry point is defensive: passing null/undefined/garbage is a no-op,
 * never a throw, because animation.js calls in during fast-forward and flush.
 */

import * as THREE from 'three';

/* ------------------------------------------------------------------------- *
 * Tunables / hard caps
 * ------------------------------------------------------------------------- */

const MAX_PARTICLES = 2400;   // hard ceiling on live points (embers + trails + sparks)
const MAX_EFFECTS = 64;       // hard ceiling on live mesh-based effects
const MAX_EMIT_PER_CALL = 220;

const DISSOLVE_RESTORE_GRACE = 0.24; // s held fully-dissolved before materials restore
const TRAIL_TTL = 2.0;               // s an unrefreshed trail emitter keeps living
const TRAIL_SPACING = 0.035;         // world units between emitted trail points

const WHITE = new THREE.Color(0xffffff);

/**
 * Clears an `onBeforeRender` override on a pooled object.
 *
 * Do NOT assign `null` here. `onBeforeRender` lives on `Object3D.prototype`, and
 * three.js calls `object.onBeforeRender(...)` unconditionally for every rendered
 * object. Assigning `null` creates an *own* property that shadows the prototype
 * method, so the next frame throws
 * `TypeError: object.onBeforeRender is not a function` from inside
 * `renderObject()` — once per frame, forever, because the pooled ring is
 * re-acquired with the poisoned property still on it.
 *
 * A shared no-op keeps the object's hidden class stable across pool reuse
 * (`delete` would force a shape transition on a hot object).
 */
const NO_BEFORE_RENDER = () => {};

/* ------------------------------------------------------------------------- *
 * Small helpers
 * ------------------------------------------------------------------------- */

const _v0 = new THREE.Vector3();
const _v1 = new THREE.Vector3();
const _box = new THREE.Box3();

function isVecLike(p) {
  return !!p && typeof p.x === 'number' && typeof p.y === 'number' &&
    typeof p.z === 'number' && Number.isFinite(p.x) && Number.isFinite(p.y) &&
    Number.isFinite(p.z);
}

function readColor(c, out) {
  const target = out || new THREE.Color();
  try {
    if (c === undefined || c === null) return target.copy(WHITE);
    if (c.isColor) return target.copy(c);
    if (typeof c === 'number' && Number.isFinite(c)) return target.setHex(c);
    if (typeof c === 'string' && c.length) return target.set(c);
  } catch (e) { /* fall through to white */ }
  return target.copy(WHITE);
}

function rand(a, b) { return a + Math.random() * (b - a); }
function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
function easeOutCubic(t) { const u = 1 - t; return 1 - u * u * u; }
function easeOutQuint(t) { const u = 1 - t; return 1 - u * u * u * u * u; }

function makeCanvas(size) {
  const c = document.createElement('canvas');
  c.width = size;
  c.height = size;
  return c;
}

/* ------------------------------------------------------------------------- *
 * Procedural textures
 * ------------------------------------------------------------------------- */

/** Soft glowing dot with a hot core — used for embers, sparks and trails. */
function makeSparkTexture() {
  const S = 64;
  const cv = makeCanvas(S);
  const ctx = cv.getContext('2d');
  const g = ctx.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
  g.addColorStop(0.00, 'rgba(255,255,255,1)');
  g.addColorStop(0.14, 'rgba(255,255,255,0.95)');
  g.addColorStop(0.32, 'rgba(255,255,255,0.42)');
  g.addColorStop(0.60, 'rgba(255,255,255,0.10)');
  g.addColorStop(1.00, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, S, S);
  const t = new THREE.CanvasTexture(cv);
  t.minFilter = THREE.LinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

/** Anamorphic-ish lens flare: hot core, soft halo, four streaks. */
function makeFlareTexture() {
  const S = 256;
  const cv = makeCanvas(S);
  const ctx = cv.getContext('2d');
  const c = S / 2;

  ctx.globalCompositeOperation = 'lighter';

  // soft halo
  let g = ctx.createRadialGradient(c, c, 0, c, c, c);
  g.addColorStop(0.00, 'rgba(255,255,255,1)');
  g.addColorStop(0.06, 'rgba(255,255,255,0.90)');
  g.addColorStop(0.18, 'rgba(255,255,255,0.30)');
  g.addColorStop(0.44, 'rgba(255,255,255,0.07)');
  g.addColorStop(1.00, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, S, S);

  // streaks
  for (let i = 0; i < 4; i++) {
    ctx.save();
    ctx.translate(c, c);
    ctx.rotate((Math.PI / 4) * i);
    const len = i % 2 === 0 ? c * 0.98 : c * 0.55;
    const wid = i % 2 === 0 ? 5 : 3;
    const lg = ctx.createLinearGradient(-len, 0, len, 0);
    lg.addColorStop(0.00, 'rgba(255,255,255,0)');
    lg.addColorStop(0.34, 'rgba(255,255,255,0.16)');
    lg.addColorStop(0.50, 'rgba(255,255,255,0.85)');
    lg.addColorStop(0.66, 'rgba(255,255,255,0.16)');
    lg.addColorStop(1.00, 'rgba(255,255,255,0)');
    ctx.fillStyle = lg;
    ctx.fillRect(-len, -wid / 2, len * 2, wid);
    ctx.restore();
  }

  // tiny white nucleus
  g = ctx.createRadialGradient(c, c, 0, c, c, S * 0.045);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, S, S);

  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  t.minFilter = THREE.LinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

/**
 * Shockwave annulus: sharp leading edge, soft trailing wash, plus a few
 * radial spokes so the ring reads as energy rather than a flat donut.
 */
function makeShockRingTexture() {
  const S = 256;
  const cv = makeCanvas(S);
  const ctx = cv.getContext('2d');
  const c = S / 2;

  const g = ctx.createRadialGradient(c, c, 0, c, c, c);
  g.addColorStop(0.00, 'rgba(255,255,255,0)');
  g.addColorStop(0.52, 'rgba(255,255,255,0)');
  g.addColorStop(0.68, 'rgba(255,255,255,0.16)');
  g.addColorStop(0.86, 'rgba(255,255,255,0.60)');
  g.addColorStop(0.94, 'rgba(255,255,255,1)');
  g.addColorStop(0.985, 'rgba(255,255,255,0.35)');
  g.addColorStop(1.00, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, S, S);

  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < 28; i++) {
    const a = (i / 28) * Math.PI * 2 + (i % 3) * 0.06;
    const r0 = c * 0.62;
    const r1 = c * (0.90 + (i % 4) * 0.024);
    ctx.save();
    ctx.translate(c, c);
    ctx.rotate(a);
    const lg = ctx.createLinearGradient(r0, 0, r1, 0);
    lg.addColorStop(0, 'rgba(255,255,255,0)');
    lg.addColorStop(1, 'rgba(255,255,255,0.5)');
    ctx.strokeStyle = lg;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(r0, 0);
    ctx.lineTo(r1, 0);
    ctx.stroke();
    ctx.restore();
  }

  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  t.minFilter = THREE.LinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

/** Warning halo: crisp double ring with tick marks, for the checked king. */
function makeHaloTexture() {
  const S = 256;
  const cv = makeCanvas(S);
  const ctx = cv.getContext('2d');
  const c = S / 2;

  ctx.globalCompositeOperation = 'lighter';

  // main ring
  ctx.strokeStyle = 'rgba(255,255,255,0.95)';
  ctx.lineWidth = 6;
  ctx.beginPath();
  ctx.arc(c, c, c * 0.80, 0, Math.PI * 2);
  ctx.stroke();

  // inner hairline
  ctx.strokeStyle = 'rgba(255,255,255,0.42)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(c, c, c * 0.64, 0, Math.PI * 2);
  ctx.stroke();

  // tick marks around the outside
  ctx.strokeStyle = 'rgba(255,255,255,0.8)';
  ctx.lineWidth = 3;
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * Math.PI * 2;
    const r0 = c * 0.84;
    const r1 = c * (i % 4 === 0 ? 0.97 : 0.90);
    ctx.beginPath();
    ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0);
    ctx.lineTo(c + Math.cos(a) * r1, c + Math.sin(a) * r1);
    ctx.stroke();
  }

  // soft bloom under the ring
  const g = ctx.createRadialGradient(c, c, c * 0.45, c, c, c);
  g.addColorStop(0.0, 'rgba(255,255,255,0)');
  g.addColorStop(0.72, 'rgba(255,255,255,0.20)');
  g.addColorStop(1.0, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, S, S);

  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  t.minFilter = THREE.LinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

/* ------------------------------------------------------------------------- *
 * GLSL fragments shared by several effects
 * ------------------------------------------------------------------------- */

const GLSL_NOISE3 = /* glsl */`
float bcHash13( vec3 p ) {
  p = fract( p * 0.3183099 + vec3( 0.11, 0.37, 0.71 ) );
  p *= 17.0;
  return fract( p.x * p.y * p.z * ( p.x + p.y + p.z ) );
}
float bcNoise3( vec3 x ) {
  vec3 i = floor( x );
  vec3 f = fract( x );
  f = f * f * ( 3.0 - 2.0 * f );
  return mix(
    mix( mix( bcHash13( i + vec3( 0.0, 0.0, 0.0 ) ), bcHash13( i + vec3( 1.0, 0.0, 0.0 ) ), f.x ),
         mix( bcHash13( i + vec3( 0.0, 1.0, 0.0 ) ), bcHash13( i + vec3( 1.0, 1.0, 0.0 ) ), f.x ), f.y ),
    mix( mix( bcHash13( i + vec3( 0.0, 0.0, 1.0 ) ), bcHash13( i + vec3( 1.0, 0.0, 1.0 ) ), f.x ),
         mix( bcHash13( i + vec3( 0.0, 1.0, 1.0 ) ), bcHash13( i + vec3( 1.0, 1.0, 1.0 ) ), f.x ), f.y ),
    f.z );
}
float bcFbm3( vec3 p ) {
  float a = 0.5;
  float s = 0.0;
  for ( int i = 0; i < 3; i ++ ) {
    s += a * bcNoise3( p );
    p = p * 2.07 + vec3( 13.1, 7.3, 3.9 );
    a *= 0.5;
  }
  return s / 0.875;
}
`;

/* ------------------------------------------------------------------------- *
 * Dissolve material patching
 * ------------------------------------------------------------------------- */

const DISSOLVE_HEADER = /* glsl */`
uniform float uBcDissolve;
uniform float uBcEdge;
uniform float uBcAmp;
uniform float uBcNoiseScale;
uniform float uBcMinY;
uniform float uBcInvH;
uniform float uBcTime;
uniform vec3  uBcBurn;
varying vec3 vBcWorld;
` + GLSL_NOISE3;

const DISSOLVE_BODY = /* glsl */`
  float bcH = clamp( ( vBcWorld.y - uBcMinY ) * uBcInvH, 0.0, 1.0 );
  float bcN = bcFbm3( vBcWorld * uBcNoiseScale );
  float bcFlick = bcNoise3( vBcWorld * 9.0 + vec3( 0.0, uBcTime * 2.6, uBcTime * 0.8 ) );
  float bcSpan = 1.0 + uBcAmp + 2.0 * uBcEdge + 0.10;
  float bcFront = uBcDissolve * bcSpan - ( 0.5 * uBcAmp + uBcEdge );
  float bcD = ( bcH - bcFront ) + ( bcN - 0.5 ) * uBcAmp;
  if ( bcD < 0.0 ) discard;
  float bcW = max( 0.015, uBcEdge * ( 0.55 + 0.9 * bcFlick ) );
  float bcE = ( 1.0 - smoothstep( 0.0, bcW, bcD ) ) * step( 0.0005, uBcDissolve );
  vec3 bcGlow = uBcBurn * ( bcE * bcE ) * 7.0
              + vec3( 1.0, 0.88, 0.66 ) * pow( bcE, 7.0 ) * 6.0;
`;

/** Materials that are built from the standard three.js chunk pipeline. */
function isChunkMaterial(m) {
  return !!m && !m.isShaderMaterial && !m.isRawShaderMaterial &&
    !m.isPointsMaterial && !m.isSpriteMaterial && !m.isLineBasicMaterial &&
    !m.isLineDashedMaterial;
}

function patchDissolveVertex(vs) {
  let out = vs;
  if (out.indexOf('#include <common>') !== -1) {
    out = out.replace('#include <common>', '#include <common>\nvarying vec3 vBcWorld;');
  } else {
    out = 'varying vec3 vBcWorld;\n' + out;
  }
  const write = '\nvBcWorld = ( modelMatrix * vec4( transformed, 1.0 ) ).xyz;';
  if (out.indexOf('#include <project_vertex>') !== -1) {
    out = out.replace('#include <project_vertex>', '#include <project_vertex>' + write);
  } else if (out.indexOf('#include <begin_vertex>') !== -1) {
    out = out.replace('#include <begin_vertex>', '#include <begin_vertex>' + write);
  } else {
    return null;
  }
  return out;
}

function patchDissolveFragment(fs) {
  let out = fs;
  if (out.indexOf('#include <common>') !== -1) {
    out = out.replace('#include <common>', '#include <common>\n' + DISSOLVE_HEADER);
  } else {
    out = DISSOLVE_HEADER + '\n' + out;
  }

  if (out.indexOf('#include <emissivemap_fragment>') !== -1) {
    // Lit materials: feed the burn line into emissive so bloom + tone mapping
    // treat it like real light, and char the surface just behind the front.
    out = out.replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\n{\n' +
      DISSOLVE_BODY +
      '  totalEmissiveRadiance += bcGlow;\n' +
      '  diffuseColor.rgb = mix( diffuseColor.rgb, vec3( 0.035, 0.022, 0.018 ), bcE * 0.8 );\n' +
      '}\n');
    return out;
  }

  if (out.indexOf('#include <dithering_fragment>') !== -1) {
    // Unlit fallback (MeshBasicMaterial): additively bias the final colour.
    out = out.replace('#include <dithering_fragment>', '#include <dithering_fragment>\n{\n' +
      DISSOLVE_BODY +
      '  gl_FragColor.rgb += bcGlow * 0.32;\n' +
      '}\n');
    return out;
  }

  return null;
}

/* ------------------------------------------------------------------------- *
 * createFX
 * ------------------------------------------------------------------------- */

export function createFX({ scene } = {}) {
  const root = new THREE.Group();
  root.name = 'BattleChessFX';
  root.matrixAutoUpdate = false;
  root.frustumCulled = false;
  if (scene && scene.isObject3D) scene.add(root);

  let disposed = false;
  let clock = 0;

  /* --- shared assets --------------------------------------------------- */

  const texSpark = makeSparkTexture();
  const texFlare = makeFlareTexture();
  const texShock = makeShockRingTexture();
  const texHalo = makeHaloTexture();

  const disposables = {
    textures: [texSpark, texFlare, texShock, texHalo],
    geometries: [],
    materials: [],
  };

  function trackGeom(g) { disposables.geometries.push(g); return g; }
  function trackMat(m) { disposables.materials.push(m); return m; }

  /* --- viewport height (for point sizing) ------------------------------ */

  function viewportPixels() {
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    const h = (typeof window !== 'undefined' && window.innerHeight) || 1080;
    return clamp(h * Math.min(dpr, 2), 480, 2400);
  }

  /* ------------------------------------------------------------------- *
   * Particle system (embers, trails, sparks) — one pooled Points object
   * ------------------------------------------------------------------- */

  const pPos = new Float32Array(MAX_PARTICLES * 3);
  const pCol = new Float32Array(MAX_PARTICLES * 3);
  const pSize = new Float32Array(MAX_PARTICLES);
  const pAlpha = new Float32Array(MAX_PARTICLES);

  const pVel = new Float32Array(MAX_PARTICLES * 3);
  const pLife = new Float32Array(MAX_PARTICLES);
  const pMaxLife = new Float32Array(MAX_PARTICLES);
  const pGrav = new Float32Array(MAX_PARTICLES);
  const pDrag = new Float32Array(MAX_PARTICLES);
  const pS0 = new Float32Array(MAX_PARTICLES);
  const pS1 = new Float32Array(MAX_PARTICLES);
  const pSwirl = new Float32Array(MAX_PARTICLES);
  const pPhase = new Float32Array(MAX_PARTICLES);
  const pAlive = new Uint8Array(MAX_PARTICLES);

  const freeList = new Int32Array(MAX_PARTICLES);
  let freeCount = MAX_PARTICLES;
  for (let i = 0; i < MAX_PARTICLES; i++) freeList[i] = MAX_PARTICLES - 1 - i;
  let liveCount = 0;
  let particlesDirty = true;

  const partGeom = trackGeom(new THREE.BufferGeometry());
  const aPos = new THREE.BufferAttribute(pPos, 3);
  const aCol = new THREE.BufferAttribute(pCol, 3);
  const aSize = new THREE.BufferAttribute(pSize, 1);
  const aAlpha = new THREE.BufferAttribute(pAlpha, 1);
  aPos.setUsage(THREE.DynamicDrawUsage);
  aCol.setUsage(THREE.DynamicDrawUsage);
  aSize.setUsage(THREE.DynamicDrawUsage);
  aAlpha.setUsage(THREE.DynamicDrawUsage);
  partGeom.setAttribute('position', aPos);
  partGeom.setAttribute('aColor', aCol);
  partGeom.setAttribute('aSize', aSize);
  partGeom.setAttribute('aAlpha', aAlpha);
  partGeom.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 1, 0), 64);

  const partMat = trackMat(new THREE.ShaderMaterial({
    uniforms: {
      uMap: { value: texSpark },
      uViewH: { value: viewportPixels() },
    },
    vertexShader: /* glsl */`
      attribute vec3 aColor;
      attribute float aSize;
      attribute float aAlpha;
      uniform float uViewH;
      varying vec3 vCol;
      varying float vA;
      void main() {
        vCol = aColor;
        vA = aAlpha;
        vec4 mv = modelViewMatrix * vec4( position, 1.0 );
        gl_Position = projectionMatrix * mv;
        float dist = max( 0.05, -mv.z );
        float px = aSize * projectionMatrix[ 1 ][ 1 ] * uViewH * 0.5 / dist;
        gl_PointSize = clamp( px, 1.0, 140.0 ) * step( 0.0015, aAlpha );
      }
    `,
    fragmentShader: /* glsl */`
      uniform sampler2D uMap;
      varying vec3 vCol;
      varying float vA;
      void main() {
        if ( vA <= 0.0015 ) discard;
        vec4 tex = texture2D( uMap, gl_PointCoord );
        float a = tex.a * vA;
        if ( a < 0.004 ) discard;
        gl_FragColor = vec4( vCol, a );
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
  }));

  const points = new THREE.Points(partGeom, partMat);
  points.frustumCulled = false;
  points.renderOrder = 12;
  points.name = 'fx-particles';
  root.add(points);

  const _spawnColor = new THREE.Color();

  /**
   * Spawn one particle. Silently drops when the pool is exhausted — that is
   * the cap, and it is deliberate.
   */
  function spawnParticle(x, y, z, vx, vy, vz, colr, colg, colb, size0, size1, life, gravity, drag, swirl) {
    if (freeCount === 0) return -1;
    const i = freeList[--freeCount];
    pAlive[i] = 1;
    liveCount++;
    const i3 = i * 3;
    pPos[i3] = x; pPos[i3 + 1] = y; pPos[i3 + 2] = z;
    pVel[i3] = vx; pVel[i3 + 1] = vy; pVel[i3 + 2] = vz;
    pCol[i3] = colr; pCol[i3 + 1] = colg; pCol[i3 + 2] = colb;
    pLife[i] = 0;
    pMaxLife[i] = Math.max(0.05, life);
    pS0[i] = size0;
    pS1[i] = size1;
    pSize[i] = size0;
    pAlpha[i] = 0;
    pGrav[i] = gravity;
    pDrag[i] = drag;
    pSwirl[i] = swirl;
    pPhase[i] = Math.random() * 6.2831853;
    particlesDirty = true;
    return i;
  }

  function killParticle(i) {
    if (!pAlive[i]) return;
    pAlive[i] = 0;
    pAlpha[i] = 0;
    pSize[i] = 0;
    liveCount--;
    freeList[freeCount++] = i;
    particlesDirty = true;
  }

  function updateParticles(dt) {
    if (liveCount === 0) {
      if (particlesDirty) {
        aPos.needsUpdate = true; aCol.needsUpdate = true;
        aSize.needsUpdate = true; aAlpha.needsUpdate = true;
        particlesDirty = false;
      }
      return;
    }
    for (let i = 0; i < MAX_PARTICLES; i++) {
      if (!pAlive[i]) continue;
      const l = pLife[i] + dt;
      const ml = pMaxLife[i];
      if (l >= ml) { killParticle(i); continue; }
      pLife[i] = l;
      const t = l / ml;
      const i3 = i * 3;

      // integrate
      const dragF = Math.max(0, 1 - pDrag[i] * dt);
      pVel[i3] *= dragF;
      pVel[i3 + 1] = (pVel[i3 + 1] + pGrav[i] * dt) * dragF;
      pVel[i3 + 2] *= dragF;

      const sw = pSwirl[i];
      if (sw !== 0) {
        const ph = pPhase[i] + l * 5.1;
        pPos[i3] += Math.sin(ph) * sw * dt;
        pPos[i3 + 2] += Math.cos(ph * 0.83) * sw * dt;
      }

      pPos[i3] += pVel[i3] * dt;
      pPos[i3 + 1] += pVel[i3 + 1] * dt;
      pPos[i3 + 2] += pVel[i3 + 2] * dt;

      // fast attack, long decay
      const attack = t < 0.06 ? t / 0.06 : 1;
      const decay = Math.pow(1 - t, 1.6);
      pAlpha[i] = attack * decay;
      pSize[i] = pS0[i] + (pS1[i] - pS0[i]) * t;
    }
    aPos.needsUpdate = true;
    aCol.needsUpdate = true;
    aSize.needsUpdate = true;
    aAlpha.needsUpdate = true;
    particlesDirty = true;
  }

  function clearParticles() {
    for (let i = 0; i < MAX_PARTICLES; i++) if (pAlive[i]) killParticle(i);
    updateParticles(0);
  }

  /* ------------------------------------------------------------------- *
   * Live effect registry
   * ------------------------------------------------------------------- */

  /** @type {Array<{update:(dt:number)=>boolean, release:()=>void, sticky?:boolean}>} */
  const effects = [];

  /**
   * Effects are capped hard. When we are over budget we evict the oldest
   * *non-sticky* effect first — killing a dissolve or a promotion beam early
   * would pop a piece back to full opacity, which is far more visible than
   * losing one impact flash.
   */
  function addEffect(eff) {
    while (effects.length >= MAX_EFFECTS) {
      let idx = effects.findIndex((e) => !e.sticky);
      if (idx === -1) idx = 0;
      const old = effects.splice(idx, 1)[0];
      try { old.release(); } catch (e) { /* never let cleanup throw */ }
    }
    effects.push(eff);
    return eff;
  }

  /* ------------------------------------------------------------------- *
   * Object pools
   * ------------------------------------------------------------------- */

  function makePool(factory, disposer) {
    const free = [];
    const freeSet = new Set();   // guards against double-release corrupting the pool
    const created = [];
    return {
      acquire() {
        let o = free.pop();
        if (o) freeSet.delete(o);
        else { o = factory(); created.push(o); }
        return o;
      },
      release(o) {
        if (!o || freeSet.has(o)) return;
        o.visible = false;
        if (o.parent) o.parent.remove(o);
        freeSet.add(o);
        free.push(o);
      },
      disposeAll() {
        for (const o of created) {
          if (o.parent) o.parent.remove(o);
          try { disposer && disposer(o); } catch (e) { /* ignore */ }
        }
        created.length = 0;
        free.length = 0;
        freeSet.clear();
      },
    };
  }

  const disposeMeshLike = (o) => {
    if (o.material) {
      if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose());
      else o.material.dispose();
    }
  };

  // --- flash sprites ---------------------------------------------------
  const flashPool = makePool(() => {
    const m = new THREE.SpriteMaterial({
      map: texFlare,
      color: 0xffffff,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      toneMapped: true,
    });
    const s = new THREE.Sprite(m);
    s.renderOrder = 14;
    s.frustumCulled = false;
    return s;
  }, disposeMeshLike);

  // --- ring quads (shockwaves, halos) ----------------------------------
  const quadGeom = trackGeom(new THREE.PlaneGeometry(1, 1));

  function makeRingQuad(texture) {
    const m = new THREE.MeshBasicMaterial({
      map: texture,
      color: 0xffffff,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(quadGeom, m);
    mesh.renderOrder = 13;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    return mesh;
  }

  const shockPool = makePool(() => makeRingQuad(texShock), disposeMeshLike);
  const haloPool = makePool(() => makeRingQuad(texHalo), disposeMeshLike);

  /* ------------------------------------------------------------------- *
   * Shield geometry (faceted hemisphere)
   * ------------------------------------------------------------------- */

  let shieldGeom = null;
  function getShieldGeometry() {
    if (shieldGeom) return shieldGeom;
    const src = new THREE.SphereGeometry(1, 22, 11, 0, Math.PI * 2, 0, Math.PI * 0.5);
    const g = src.toNonIndexed();
    src.dispose();
    const pos = g.getAttribute('position');
    const n = pos.count;
    const cent = new Float32Array(n * 3);
    const rnd = new Float32Array(n);
    for (let i = 0; i < n; i += 3) {
      const cx = (pos.getX(i) + pos.getX(i + 1) + pos.getX(i + 2)) / 3;
      const cy = (pos.getY(i) + pos.getY(i + 1) + pos.getY(i + 2)) / 3;
      const cz = (pos.getZ(i) + pos.getZ(i + 1) + pos.getZ(i + 2)) / 3;
      const r = Math.random();
      for (let k = 0; k < 3; k++) {
        const j = (i + k) * 3;
        cent[j] = cx; cent[j + 1] = cy; cent[j + 2] = cz;
        rnd[i + k] = r;
      }
    }
    g.setAttribute('aCentroid', new THREE.BufferAttribute(cent, 3));
    g.setAttribute('aRand', new THREE.BufferAttribute(rnd, 1));
    g.computeVertexNormals(); // non-indexed => flat facet normals
    g.computeBoundingSphere();
    shieldGeom = trackGeom(g);
    return shieldGeom;
  }

  function makeShieldMaterial() {
    return new THREE.ShaderMaterial({
      uniforms: {
        uColor: { value: new THREE.Color(0x66ccff) },
        uOpacity: { value: 1 },
        uBreak: { value: 0 },
        uScale: { value: 1 },
        uTime: { value: 0 },
      },
      vertexShader: /* glsl */`
        attribute vec3 aCentroid;
        attribute float aRand;
        uniform float uBreak;
        uniform float uScale;
        varying float vRand;
        varying vec3 vN;
        varying vec3 vV;
        varying vec3 vLocal;
        void main() {
          vRand = aRand;
          vec3 dir = normalize( aCentroid + vec3( 0.0, 0.0001, 0.0 ) );
          vec3 spin = vec3( -dir.z, 0.0, dir.x ) * uBreak * ( aRand - 0.5 ) * 0.5;
          vec3 p = ( position + dir * uBreak * ( 0.25 + 1.0 * aRand ) + spin ) * uScale;
          vLocal = p;
          vec4 mv = modelViewMatrix * vec4( p, 1.0 );
          vN = normalize( normalMatrix * normal );
          vV = normalize( -mv.xyz );
          gl_Position = projectionMatrix * mv;
        }
      `,
      fragmentShader: /* glsl */`
        uniform vec3 uColor;
        uniform float uOpacity;
        uniform float uBreak;
        uniform float uTime;
        varying float vRand;
        varying vec3 vN;
        varying vec3 vV;
        varying vec3 vLocal;
        void main() {
          vec3 N = normalize( vN );
          vec3 V = normalize( vV );
          float fres = pow( 1.0 - abs( dot( N, V ) ), 2.3 );

          // hex-ish energy lattice so the shell reads as a constructed field
          vec2 q = vec2( atan( vLocal.z, vLocal.x ) * 2.2, vLocal.y * 7.0 );
          float grid = max(
            smoothstep( 0.86, 1.0, abs( sin( q.x * 3.0 ) ) ),
            smoothstep( 0.86, 1.0, abs( sin( q.y * 2.0 + uTime * 1.5 ) ) )
          );

          float facet = 0.25 + 0.75 * vRand;
          float shatter = step( vRand, 1.0 - uBreak * 0.92 );
          float a = uOpacity * shatter * ( 0.09 + fres * 1.35 + grid * 0.35 ) * mix( 1.0, facet, min( 1.0, uBreak * 2.5 ) );
          if ( a <= 0.003 ) discard;
          vec3 c = uColor * ( 0.55 + fres * 1.9 + grid * 0.8 ) + vec3( 1.0 ) * pow( fres, 6.0 ) * 0.9;
          gl_FragColor = vec4( c, clamp( a, 0.0, 1.0 ) );
        }
      `,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
    });
  }

  const shieldPool = makePool(() => {
    const mesh = new THREE.Mesh(getShieldGeometry(), makeShieldMaterial());
    mesh.renderOrder = 13;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    return mesh;
  }, disposeMeshLike);

  /* ------------------------------------------------------------------- *
   * Light column geometry / material
   * ------------------------------------------------------------------- */

  let beamGeom = null;
  function getBeamGeometry() {
    if (!beamGeom) {
      // unit radius, unit height, origin at the base
      const g = new THREE.CylinderGeometry(1, 1, 1, 40, 1, true);
      g.translate(0, 0.5, 0);
      beamGeom = trackGeom(g);
    }
    return beamGeom;
  }

  function makeBeamMaterial() {
    return new THREE.ShaderMaterial({
      uniforms: {
        uColor: { value: new THREE.Color(0x66ccff) },
        uOpacity: { value: 1 },
        uTime: { value: 0 },
        uPower: { value: 1.4 },
        uScroll: { value: 1.3 },
      },
      vertexShader: /* glsl */`
        varying vec2 vUvB;
        varying vec3 vN;
        varying vec3 vV;
        void main() {
          vUvB = uv;
          vec4 mv = modelViewMatrix * vec4( position, 1.0 );
          vN = normalize( normalMatrix * normal );
          vV = normalize( -mv.xyz );
          gl_Position = projectionMatrix * mv;
        }
      `,
      fragmentShader: /* glsl */`
        uniform vec3 uColor;
        uniform float uOpacity;
        uniform float uTime;
        uniform float uPower;
        uniform float uScroll;
        varying vec2 vUvB;
        varying vec3 vN;
        varying vec3 vV;

        float bcHash12( vec2 p ) {
          vec3 p3 = fract( vec3( p.xyx ) * 0.1031 );
          p3 += dot( p3, p3.yzx + 33.33 );
          return fract( ( p3.x + p3.y ) * p3.z );
        }
        float bcNoise2( vec2 p ) {
          vec2 i = floor( p );
          vec2 f = fract( p );
          f = f * f * ( 3.0 - 2.0 * f );
          return mix(
            mix( bcHash12( i ), bcHash12( i + vec2( 1.0, 0.0 ) ), f.x ),
            mix( bcHash12( i + vec2( 0.0, 1.0 ) ), bcHash12( i + vec2( 1.0, 1.0 ) ), f.x ), f.y );
        }

        void main() {
          float edge = abs( dot( normalize( vN ), normalize( vV ) ) );
          float body = pow( clamp( edge, 0.0, 1.0 ), uPower );
          float up = clamp( vUvB.y, 0.0, 1.0 );
          float grad = pow( 1.0 - up, 1.35 ) * 0.82 + 0.18;

          float s1 = bcNoise2( vec2( vUvB.x * 9.0, vUvB.y * 3.2 - uTime * uScroll ) );
          float s2 = bcNoise2( vec2( vUvB.x * 21.0 + 4.7, vUvB.y * 7.0 - uTime * uScroll * 2.3 ) );
          float streak = 0.42 + 0.72 * s1 + 0.46 * s2;

          float a = uOpacity * body * grad * streak;
          if ( a <= 0.003 ) discard;

          vec3 c = uColor * ( 0.65 + streak * 1.05 )
                 + vec3( 1.0, 0.97, 0.9 ) * pow( 1.0 - up, 7.0 ) * 0.85;
          gl_FragColor = vec4( c, clamp( a, 0.0, 1.0 ) );
        }
      `,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
    });
  }

  const beamPool = makePool(() => {
    const group = new THREE.Group();
    const outer = new THREE.Mesh(getBeamGeometry(), makeBeamMaterial());
    const inner = new THREE.Mesh(getBeamGeometry(), makeBeamMaterial());
    inner.material.uniforms.uPower.value = 0.55;
    inner.material.uniforms.uScroll.value = 2.4;
    outer.renderOrder = 13;
    inner.renderOrder = 14;
    outer.frustumCulled = false;
    inner.frustumCulled = false;
    outer.castShadow = false; inner.castShadow = false;
    group.add(outer, inner);
    group.userData.outer = outer;
    group.userData.inner = inner;
    group.frustumCulled = false;
    return group;
  }, (group) => {
    if (group.userData.outer) group.userData.outer.material.dispose();
    if (group.userData.inner) group.userData.inner.material.dispose();
  });

  /* ------------------------------------------------------------------- *
   * Public: embers
   * ------------------------------------------------------------------- */

  const _emberCol = new THREE.Color();

  function embers(position, color, count) {
    if (disposed || !isVecLike(position)) return;
    let n = Number.isFinite(count) ? Math.floor(count) : 26;
    n = clamp(n, 0, MAX_EMIT_PER_CALL);
    if (n <= 0) return;
    readColor(color, _emberCol);

    for (let i = 0; i < n; i++) {
      // hot core near the source biased toward white, cooling on the way up
      const heat = Math.random();
      const r = _emberCol.r + heat * 0.85;
      const g = _emberCol.g + heat * 0.55;
      const b = _emberCol.b + heat * 0.35;

      const ang = Math.random() * Math.PI * 2;
      const rad = Math.pow(Math.random(), 0.65) * 0.22;
      const speed = rand(0.35, 1.5);

      spawnParticle(
        position.x + Math.cos(ang) * rad,
        position.y + rand(-0.05, 0.16),
        position.z + Math.sin(ang) * rad,
        Math.cos(ang) * rand(0.05, 0.5), speed, Math.sin(ang) * rand(0.05, 0.5),
        r * 1.6, g * 1.6, b * 1.6,
        rand(0.035, 0.085), rand(0.004, 0.02),
        rand(0.75, 1.9),
        rand(0.25, 0.75),   // slight buoyancy, then it fights drag
        rand(0.6, 1.5),
        rand(0.08, 0.3)
      );
    }
  }

  /* ------------------------------------------------------------------- *
   * Public: impactFlash
   * ------------------------------------------------------------------- */

  const _flashCol = new THREE.Color();

  function impactFlash(position, color) {
    if (disposed || !isVecLike(position)) return;
    readColor(color, _flashCol);

    const px = position.x, py = position.y, pz = position.z;

    // --- billboard flare ---
    const sprite = flashPool.acquire();
    sprite.visible = true;
    sprite.position.set(px, py, pz);
    sprite.scale.setScalar(0.35);
    sprite.material.color.copy(_flashCol).lerp(WHITE, 0.65);
    sprite.material.opacity = 1;
    root.add(sprite);

    // --- camera-facing shock ring ---
    const ring = shockPool.acquire();
    ring.visible = true;
    ring.position.set(px, py, pz);
    ring.scale.setScalar(0.3);
    ring.material.color.copy(_flashCol);
    ring.material.opacity = 0.95;
    ring.onBeforeRender = billboardToCamera;
    root.add(ring);

    // --- ground shock ring on the board deck ---
    const ground = shockPool.acquire();
    ground.visible = true;
    ground.position.set(px, 0.028, pz);
    ground.rotation.set(-Math.PI / 2, 0, Math.random() * Math.PI);
    ground.scale.setScalar(0.4);
    ground.material.color.copy(_flashCol).lerp(WHITE, 0.2);
    ground.material.opacity = 0.8;
    ground.onBeforeRender = NO_BEFORE_RENDER;
    root.add(ground);

    // --- spark burst ---
    for (let i = 0; i < 30; i++) {
      const a = Math.random() * Math.PI * 2;
      const el = rand(-0.35, 1.0);
      const sp = rand(1.6, 5.4);
      spawnParticle(
        px, py, pz,
        Math.cos(a) * sp, el * sp * 0.8, Math.sin(a) * sp,
        (_flashCol.r + 0.9) * 1.9, (_flashCol.g + 0.7) * 1.9, (_flashCol.b + 0.5) * 1.9,
        rand(0.03, 0.06), 0.004,
        rand(0.22, 0.55),
        -3.2, 3.4, 0
      );
    }

    const DUR_FLASH = 0.26;
    const DUR_RING = 0.52;
    const DUR_GROUND = 0.62;
    let t = 0;
    // Each borrowed object is handed back exactly once — the pool may have
    // re-issued it to a later flash, so `.parent` is not a safe ownership test.
    let heldFlash = true, heldRing = true, heldGround = true;

    const dropFlash = () => { if (heldFlash) { heldFlash = false; flashPool.release(sprite); } };
    const dropRing = () => { if (heldRing) { heldRing = false; ring.onBeforeRender = NO_BEFORE_RENDER; shockPool.release(ring); } };
    const dropGround = () => { if (heldGround) { heldGround = false; shockPool.release(ground); } };

    addEffect({
      update(dt) {
        t += dt;

        const tf = clamp(t / DUR_FLASH, 0, 1);
        if (tf < 1) {
          sprite.scale.setScalar(0.35 + easeOutQuint(tf) * 2.15);
          sprite.material.opacity = Math.pow(1 - tf, 1.9);
        } else dropFlash();

        const tr = clamp(t / DUR_RING, 0, 1);
        if (tr < 1) {
          ring.scale.setScalar(0.3 + easeOutQuint(tr) * 2.5);
          ring.material.opacity = 0.95 * Math.pow(1 - tr, 1.6);
        } else dropRing();

        const tg = clamp(t / DUR_GROUND, 0, 1);
        if (tg < 1) {
          ground.scale.setScalar(0.4 + easeOutQuint(tg) * 3.4);
          ground.material.opacity = 0.8 * Math.pow(1 - tg, 1.4);
        } else dropGround();

        return t < Math.max(DUR_FLASH, DUR_RING, DUR_GROUND);
      },
      release() { dropFlash(); dropRing(); dropGround(); },
    });
  }

  function billboardToCamera(renderer, sceneRef, camera) {
    if (camera) this.quaternion.copy(camera.quaternion);
  }

  /* ------------------------------------------------------------------- *
   * Public: shieldCrack
   * ------------------------------------------------------------------- */

  const _shieldCol = new THREE.Color();

  function shieldCrack(position, color) {
    if (disposed || !isVecLike(position)) return;
    readColor(color, _shieldCol);

    const mesh = shieldPool.acquire();
    mesh.visible = true;
    mesh.position.set(position.x, position.y - 0.08, position.z);
    mesh.scale.setScalar(0.62);
    mesh.rotation.y = Math.random() * Math.PI * 2;

    const u = mesh.material.uniforms;
    u.uColor.value.copy(_shieldCol).lerp(WHITE, 0.25);
    u.uOpacity.value = 0;
    u.uBreak.value = 0;
    u.uScale.value = 0.55;
    u.uTime.value = 0;
    root.add(mesh);

    const DUR = 0.62;
    let t = 0;
    let held = true;
    const drop = () => { if (held) { held = false; shieldPool.release(mesh); } };

    addEffect({
      update(dt) {
        t += dt;
        const k = clamp(t / DUR, 0, 1);
        u.uTime.value += dt;
        // snap out fast, then shatter and fade
        u.uScale.value = 0.55 + easeOutQuint(Math.min(1, k * 2.6)) * 0.78;
        u.uBreak.value = Math.pow(k, 1.9);
        const rampIn = clamp(k / 0.12, 0, 1);
        u.uOpacity.value = rampIn * Math.pow(1 - k, 1.15);
        if (k >= 1) { drop(); return false; }
        return true;
      },
      release: drop,
    });

    // shell fragments spitting outward
    for (let i = 0; i < 26; i++) {
      const a = Math.random() * Math.PI * 2;
      const el = rand(0.1, 1.0);
      const sp = rand(1.1, 2.9);
      spawnParticle(
        position.x + Math.cos(a) * 0.4,
        position.y + rand(-0.05, 0.5),
        position.z + Math.sin(a) * 0.4,
        Math.cos(a) * sp, el * sp * 0.5, Math.sin(a) * sp,
        _shieldCol.r * 2.0 + 0.4, _shieldCol.g * 2.0 + 0.4, _shieldCol.b * 2.0 + 0.4,
        rand(0.025, 0.05), 0.002,
        rand(0.35, 0.8),
        -2.2, 2.0, 0
      );
    }
  }

  /* ------------------------------------------------------------------- *
   * Public: lightColumn
   * ------------------------------------------------------------------- */

  const _beamCol = new THREE.Color();

  function lightColumn(position, color, durationMs) {
    if (disposed || !isVecLike(position)) return Promise.resolve();
    readColor(color, _beamCol);

    const dur = Math.max(0.15, (Number.isFinite(durationMs) ? durationMs : 1400) / 1000);
    const H = 3.4;

    const group = beamPool.acquire();
    const outer = group.userData.outer;
    const inner = group.userData.inner;
    group.visible = true;
    group.position.set(position.x, position.y, position.z);
    group.rotation.y = Math.random() * Math.PI;

    outer.scale.set(0.44, H, 0.44);
    inner.scale.set(0.17, H * 1.02, 0.17);
    outer.material.uniforms.uColor.value.copy(_beamCol);
    inner.material.uniforms.uColor.value.copy(_beamCol).lerp(WHITE, 0.6);
    outer.material.uniforms.uOpacity.value = 0;
    inner.material.uniforms.uOpacity.value = 0;
    outer.material.uniforms.uTime.value = 0;
    inner.material.uniforms.uTime.value = 0;
    root.add(group);

    // base flare + ground ring
    const base = flashPool.acquire();
    base.visible = true;
    base.position.set(position.x, position.y + 0.06, position.z);
    base.scale.setScalar(1.1);
    base.material.color.copy(_beamCol).lerp(WHITE, 0.5);
    base.material.opacity = 0;
    root.add(base);

    const ring = shockPool.acquire();
    ring.visible = true;
    ring.position.set(position.x, 0.03, position.z);
    ring.rotation.set(-Math.PI / 2, 0, 0);
    ring.scale.setScalar(0.5);
    ring.material.color.copy(_beamCol);
    ring.material.opacity = 0;
    ring.onBeforeRender = NO_BEFORE_RENDER;
    root.add(ring);

    let t = 0;
    let emitAcc = 0;
    let resolveFn = null;
    let held = true;
    const promise = new Promise((res) => { resolveFn = res; });

    const release = () => {
      if (held) {
        held = false;
        beamPool.release(group);
        flashPool.release(base);
        shockPool.release(ring);
      }
      if (resolveFn) { const r = resolveFn; resolveFn = null; r(); }
    };

    addEffect({
      sticky: true,
      update(dt) {
        t += dt;
        const k = clamp(t / dur, 0, 1);

        const grow = easeOutQuint(clamp(t / (dur * 0.18), 0, 1));
        const fade = k > 0.62 ? Math.pow(1 - (k - 0.62) / 0.38, 1.5) : 1;
        const flicker = 0.88 + 0.12 * Math.sin(t * 27.0) * Math.sin(t * 11.3);

        outer.scale.y = H * grow;
        inner.scale.y = H * 1.02 * grow;
        const widen = 1 + (1 - fade) * 0.5;
        outer.scale.x = outer.scale.z = 0.44 * widen;
        inner.scale.x = inner.scale.z = 0.17 * widen;

        outer.material.uniforms.uTime.value = t;
        inner.material.uniforms.uTime.value = t;
        outer.material.uniforms.uOpacity.value = 0.95 * grow * fade * flicker;
        inner.material.uniforms.uOpacity.value = 1.15 * grow * fade * flicker;
        group.rotation.y += dt * 0.55;

        base.material.opacity = 0.95 * grow * fade;
        base.scale.setScalar(0.9 + 0.5 * Math.sin(t * 6.0) * fade + grow * 0.5);

        const tr = clamp(t / (dur * 0.7), 0, 1);
        ring.scale.setScalar(0.5 + easeOutQuint(tr) * 2.6);
        ring.material.opacity = 0.85 * Math.pow(1 - tr, 1.3);

        // rising motes inside the beam
        emitAcc += dt * 70 * fade;
        while (emitAcc >= 1) {
          emitAcc -= 1;
          const a = Math.random() * Math.PI * 2;
          const rr = Math.sqrt(Math.random()) * 0.4;
          spawnParticle(
            position.x + Math.cos(a) * rr,
            position.y + rand(0, 0.25),
            position.z + Math.sin(a) * rr,
            0, rand(1.6, 3.6), 0,
            (_beamCol.r + 0.5) * 1.7, (_beamCol.g + 0.5) * 1.7, (_beamCol.b + 0.5) * 1.7,
            rand(0.025, 0.06), rand(0.002, 0.012),
            rand(0.6, 1.3),
            0.6, 0.2, rand(0.05, 0.22)
          );
        }

        if (k >= 1) { release(); return false; }
        return true;
      },
      release,
    });

    return promise;
  }

  /* ------------------------------------------------------------------- *
   * Public: trail
   * ------------------------------------------------------------------- */

  /** @type {Map<THREE.Object3D, object>} */
  const trails = new Map();

  function trail(mesh, color) {
    const noop = { stop() {} };
    if (disposed || !mesh || !mesh.isObject3D) return noop;

    let em = trails.get(mesh);
    if (em) {
      em.ttl = Math.max(em.ttl, TRAIL_TTL);
      readColor(color, em.color);
      return em.handle;
    }

    em = {
      mesh,
      color: readColor(color),
      last: new THREE.Vector3(),
      ttl: TRAIL_TTL,
      started: false,
      handle: null,
    };
    try { mesh.getWorldPosition(em.last); } catch (e) { em.last.set(0, 0, 0); }
    em.handle = { stop() { em.ttl = 0; } };
    trails.set(mesh, em);
    return em.handle;
  }

  function updateTrails(dt) {
    if (trails.size === 0) return;
    for (const [mesh, em] of trails) {
      em.ttl -= dt;
      const detached = !mesh.parent || mesh.visible === false;
      if (em.ttl <= 0 || detached) { trails.delete(mesh); continue; }

      try { mesh.getWorldPosition(_v0); } catch (e) { trails.delete(mesh); continue; }
      const dist = _v0.distanceTo(em.last);
      if (dist < 1e-4) { em.last.copy(_v0); continue; }

      let n = Math.min(24, Math.floor(dist / TRAIL_SPACING));
      if (n <= 0) { continue; }  // accumulate until we've moved far enough

      const c = em.color;
      for (let i = 0; i < n; i++) {
        const f = (i + Math.random()) / n;
        _v1.lerpVectors(em.last, _v0, f);
        const hgt = rand(0.06, 0.62);
        spawnParticle(
          _v1.x + rand(-0.06, 0.06),
          _v1.y + hgt,
          _v1.z + rand(-0.06, 0.06),
          rand(-0.12, 0.12), rand(0.05, 0.45), rand(-0.12, 0.12),
          (c.r + 0.28) * 1.45, (c.g + 0.28) * 1.45, (c.b + 0.28) * 1.45,
          rand(0.03, 0.075), 0.002,
          rand(0.28, 0.62),
          0.15, 1.9, rand(0.02, 0.12)
        );
      }
      em.last.copy(_v0);
    }
  }

  /* ------------------------------------------------------------------- *
   * Public: warningHalo / removeHalo
   * ------------------------------------------------------------------- */

  /** @type {Map<THREE.Object3D, object>} */
  const halos = new Map();

  /**
   * Hard ceiling on simultaneous halos. Two are expected at a time (a checked
   * king and a capture victim); the cap exists purely so that a caller which
   * forgets removeHalo() cannot grow this map — and the always-visible additive
   * draw calls it implies — without bound for the life of the page.
   */
  const MAX_HALOS = 8;

  /**
   * True once `obj` no longer hangs off a Scene, i.e. whatever owned it was torn
   * down. Checking `obj.parent` is not enough: piece meshes are parented to a
   * facing pivot, and destroying a piece unlinks the pivot's parent, not the
   * mesh's — so `mesh.parent` stays non-null forever.
   */
  function isDetached(obj) {
    let o = obj;
    while (o) {
      if (o.isScene) return false;
      o = o.parent;
    }
    return true;
  }

  function warningHalo(mesh, color) {
    if (disposed || !mesh || !mesh.isObject3D) return;

    let h = halos.get(mesh);
    if (h) {
      h.fading = false;
      h.fade = 1;
      readColor(color, h.color);
      return;
    }

    while (halos.size >= MAX_HALOS) {
      const oldest = halos.entries().next();   // Map iterates in insertion order
      if (oldest.done) break;
      releaseHalo(oldest.value[0], oldest.value[1]);
    }

    const ringA = haloPool.acquire();
    const ringB = haloPool.acquire();
    for (const r of [ringA, ringB]) {
      r.visible = true;
      r.rotation.set(-Math.PI / 2, 0, 0);
      r.material.opacity = 0;
      root.add(r);
    }
    ringA.renderOrder = 11;
    ringB.renderOrder = 11;

    h = {
      mesh,
      ringA,
      ringB,
      color: readColor(color, new THREE.Color()),
      t: Math.random() * 3,
      ping: 0,
      fading: false,
      fade: 1,
    };
    halos.set(mesh, h);
  }

  function removeHalo(mesh) {
    if (!mesh) return;
    const h = halos.get(mesh);
    if (!h) return;
    h.fading = true;
  }

  function releaseHalo(mesh, h) {
    haloPool.release(h.ringA);
    haloPool.release(h.ringB);
    halos.delete(mesh);
  }

  function updateHalos(dt) {
    if (halos.size === 0) return;
    for (const [mesh, h] of halos) {
      if (isDetached(mesh)) { releaseHalo(mesh, h); continue; }

      if (h.fading) {
        h.fade -= dt / 0.28;
        if (h.fade <= 0) { releaseHalo(mesh, h); continue; }
      }

      h.t += dt;
      h.ping += dt;
      if (h.ping > 1.15) h.ping -= 1.15;

      try { mesh.getWorldPosition(_v0); } catch (e) { releaseHalo(mesh, h); continue; }

      const pulse = 0.5 + 0.5 * Math.sin(h.t * 4.6);

      const a = h.ringA;
      a.position.set(_v0.x, 0.022, _v0.z);
      a.scale.setScalar(1.32 + pulse * 0.1);
      a.rotation.z += dt * 0.35;
      a.material.color.copy(h.color);
      a.material.opacity = (0.42 + 0.42 * pulse) * h.fade;

      const pk = h.ping / 1.15;
      const b = h.ringB;
      b.position.set(_v0.x, 0.024, _v0.z);
      b.scale.setScalar(1.1 + easeOutCubic(pk) * 1.5);
      b.rotation.z -= dt * 0.2;
      b.material.color.copy(h.color).lerp(WHITE, 0.25);
      b.material.opacity = 0.7 * Math.pow(1 - pk, 1.7) * h.fade;
    }
  }

  /* ------------------------------------------------------------------- *
   * Public: dissolve
   * ------------------------------------------------------------------- */

  /**
   * Cache of dissolve-variant materials.
   *
   * Keyed on a *stable* identity rather than `src.uuid`: animation.js isolates a
   * victim's materials (pieces.js hands back fresh `Material.clone()`s) in the
   * frame before it calls dissolve(), so the uuid differs on every capture and a
   * uuid key would miss every single time — cloning, tracking and never freeing
   * one more material per capture, forever. Material.clone() does preserve
   * `name`, and pieces.js names the faction materials ('white-body',
   * 'black-glow', ...), so type+name is stable across captures and collapses the
   * pool back to the handful of buckets it was designed for.
   */
  const dissolveMatPool = new Map();

  /** Idle buckets past this many are disposed — backstop for unnamed sources. */
  const MAX_DISSOLVE_KEYS = 12;

  function dissolveKeyFor(src) {
    const name = typeof src.name === 'string' ? src.name : '';
    return name ? `${src.type}|${name}` : `uuid|${src.uuid}`;
  }

  /** Dispose a material and drop it from the teardown list so it is freed once. */
  function disposeTrackedMat(mat) {
    const i = disposables.materials.indexOf(mat);
    if (i >= 0) disposables.materials.splice(i, 1);
    try { mat.dispose(); } catch (e) { /* ignore */ }
  }

  /**
   * Evict idle buckets once the map outgrows the expected key count. Map
   * iteration is insertion-ordered, so the oldest idle bucket goes first. Busy
   * buckets are skipped: their materials are installed on a live mesh.
   */
  function pruneDissolvePool() {
    if (dissolveMatPool.size <= MAX_DISSOLVE_KEYS) return;
    for (const [key, bucket] of dissolveMatPool) {
      if (dissolveMatPool.size <= MAX_DISSOLVE_KEYS) break;
      if (bucket.some((e) => e.busy)) continue;
      for (const e of bucket) disposeTrackedMat(e.material);
      dissolveMatPool.delete(key);
    }
  }

  function acquireDissolveMaterial(src) {
    const key = dissolveKeyFor(src);
    let bucket = dissolveMatPool.get(key);
    if (!bucket) { bucket = []; dissolveMatPool.set(key, bucket); }

    for (const entry of bucket) {
      if (!entry.busy) {
        entry.busy = true;
        // PieceHandle.setOpacity() writes blend state straight onto whatever
        // material is currently installed — which, mid-dissolve, is this one. A
        // reused entry therefore has to be handed back in its as-built state or
        // the next capture inherits the previous victim's fade.
        const m = entry.material;
        const r = entry.reset;
        m.transparent = r.transparent;
        m.opacity = r.opacity;
        m.depthWrite = r.depthWrite;
        m.visible = r.visible;
        m.needsUpdate = true;
        return entry;
      }
    }

    const uniforms = {
      uBcDissolve: { value: 0 },
      uBcEdge: { value: 0.11 },
      uBcAmp: { value: 0.34 },
      uBcNoiseScale: { value: 5.2 },
      uBcMinY: { value: 0 },
      uBcInvH: { value: 1 },
      uBcTime: { value: 0 },
      uBcBurn: { value: new THREE.Color(0xff6a20) },
    };

    const mat = src.clone();
    mat.name = (src.name || 'mat') + '__dissolve';
    mat.side = THREE.DoubleSide;
    mat.shadowSide = THREE.DoubleSide;
    mat.onBeforeCompile = (shader) => {
      const vs = patchDissolveVertex(shader.vertexShader);
      const fs = patchDissolveFragment(shader.fragmentShader);
      if (!vs || !fs) return;               // unknown shader shape: leave it alone
      Object.assign(shader.uniforms, uniforms);
      shader.vertexShader = vs;
      shader.fragmentShader = fs;
    };
    mat.customProgramCacheKey = () => 'battlechess-dissolve-v1';
    mat.needsUpdate = true;
    trackMat(mat);

    const entry = {
      material: mat,
      uniforms,
      busy: true,
      key,
      reset: {
        transparent: mat.transparent,
        opacity: mat.opacity,
        depthWrite: mat.depthWrite,
        visible: mat.visible,
      },
    };
    bucket.push(entry);
    pruneDissolvePool();
    return entry;
  }

  function releaseDissolveMaterial(entry) {
    if (entry) entry.busy = false;
  }

  /** @type {Map<THREE.Object3D, object>} */
  const dissolves = new Map();

  function collectDissolveTargets(rootObj) {
    const out = [];
    rootObj.traverse((o) => {
      if (!o.isMesh && !o.isSkinnedMesh) return;
      if (!o.material) return;
      out.push(o);
    });
    return out;
  }

  /**
   * Put the piece's real materials back. Idempotent: the target list is
   * emptied, so a second call is a no-op. Visibility is never touched —
   * animation.js owns that, and the FEN is truth.
   */
  function restoreDissolve(record) {
    for (const t of record.targets) {
      t.node.material = t.original;
      t.node.castShadow = t.castShadow;
      for (const e of t.entries) releaseDissolveMaterial(e);
    }
    record.targets.length = 0;
  }

  /**
   * Unwind the dissolve on `mesh` right now: the node's real materials go back,
   * the pooled variants are released and the dissolve promise settles. The
   * record is marked cancelled so its still-registered effect retires on its
   * next tick without clobbering a replacement's registry entry.
   *
   * animation.js calls this from a capture's finish(). It isolates the victim's
   * materials *before* starting the dissolve, so fx captured those clones as its
   * "original" — and PieceHandle.restoreMaterial() disposes them. Unwinding here
   * keeps the two material swaps properly nested; without it fx would later
   * reinstall disposed materials on a piece sitting in the trophy rack, and the
   * clones would be unreachable from every disposal path.
   *
   * Safe (and cheap) to call when nothing is dissolving.
   *
   * @param {THREE.Object3D} mesh the object originally passed to dissolve()
   * @returns {boolean} true if a dissolve was active.
   */
  function endDissolve(mesh) {
    if (!mesh) return false;
    const record = dissolves.get(mesh);
    if (!record) return false;
    record.cancelled = true;
    restoreDissolve(record);
    dissolves.delete(mesh);
    if (record.resolve) { const r = record.resolve; record.resolve = null; r(); }
    return true;
  }

  function dissolve(mesh, color, durationMs) {
    if (disposed || !mesh || !mesh.isObject3D) return Promise.resolve();

    // A second dissolve on the same object supersedes the first.
    endDissolve(mesh);

    const dur = Math.max(0.08, (Number.isFinite(durationMs) ? durationMs : 900) / 1000);
    const burn = readColor(color);

    const nodes = collectDissolveTargets(mesh);
    if (nodes.length === 0) return Promise.resolve();

    // World-space extent drives the burn front and the ember emission volume.
    try { mesh.getWorldPosition(_v0); } catch (e) { _v0.set(0, 0, 0); }
    let minY = _v0.y;
    let maxY = _v0.y + 1.2;
    let cx = _v0.x, cz = _v0.z, halfX = 0.3, halfZ = 0.3;
    try {
      _box.setFromObject(mesh);
      const finite = Number.isFinite(_box.min.y) && Number.isFinite(_box.max.y) &&
        Number.isFinite(_box.min.x) && Number.isFinite(_box.min.z);
      if (!_box.isEmpty() && finite) {
        minY = _box.min.y;
        maxY = _box.max.y;
        cx = (_box.min.x + _box.max.x) * 0.5;
        cz = (_box.min.z + _box.max.z) * 0.5;
        halfX = Math.max(0.12, (_box.max.x - _box.min.x) * 0.5);
        halfZ = Math.max(0.12, (_box.max.z - _box.min.z) * 0.5);
      }
    } catch (e) { /* keep the world-position fallback */ }

    const height = Math.max(0.05, maxY - minY);
    const invH = 1 / height;
    const baseWorldY = _v0.y;

    const targets = [];
    for (const node of nodes) {
      const original = node.material;
      const isArray = Array.isArray(original);
      const srcList = isArray ? original : [original];
      const entries = [];
      const swapped = [];
      let ok = false;
      for (const src of srcList) {
        if (isChunkMaterial(src)) {
          const entry = acquireDissolveMaterial(src);
          const u = entry.uniforms;
          u.uBcDissolve.value = 0;
          u.uBcMinY.value = minY;
          u.uBcInvH.value = invH;
          u.uBcTime.value = 0;
          u.uBcBurn.value.copy(burn);
          entries.push(entry);
          swapped.push(entry.material);
          ok = true;
        } else {
          swapped.push(src);
        }
      }
      if (!ok) continue;
      targets.push({
        node,
        original,
        entries,
        castShadow: node.castShadow,
      });
      node.material = isArray ? swapped : swapped[0];
      node.castShadow = false;   // depth material is unpatched; don't leave a ghost shadow
    }

    if (targets.length === 0) {
      // Nothing patchable — fall back to a plain ember burst so the capture
      // still reads, and resolve on the requested schedule.
      embers({ x: cx, y: minY + height * 0.5, z: cz }, burn, 40);
      return new Promise((res) => {
        let t = 0;
        addEffect({
          update(dt) { t += dt; if (t >= dur) { res(); return false; } return true; },
          release() { res(); },
        });
      });
    }

    let resolveFn = null;
    const promise = new Promise((res) => { resolveFn = res; });

    const record = {
      mesh,
      targets,
      resolve: resolveFn,
      t: 0,
      minY,
      height,
      baseWorldY,
      cx, cz, halfX, halfZ,
      burn,
      dur,
      emitAcc: 0,
      done: false,
      cancelled: false,
      grace: 0,
    };
    dissolves.set(mesh, record);

    const finish = () => {
      if (record.resolve) { const r = record.resolve; record.resolve = null; r(); }
    };
    const retire = () => {
      restoreDissolve(record);
      if (dissolves.get(record.mesh) === record) dissolves.delete(record.mesh);
    };

    addEffect({
      sticky: true,
      update(dt) {
        if (record.cancelled) { retire(); finish(); return false; }
        record.t += dt;
        const k = clamp(record.t / record.dur, 0, 1);

        // Track vertical motion of the piece so the front stays glued to it.
        let yShift = 0;
        try {
          record.mesh.getWorldPosition(_v0);
          yShift = _v0.y - record.baseWorldY;
        } catch (e) { yShift = 0; }

        for (const t of record.targets) {
          for (const e of t.entries) {
            e.uniforms.uBcDissolve.value = k;
            e.uniforms.uBcTime.value += dt;
            e.uniforms.uBcMinY.value = record.minY + yShift;
          }
        }

        // Embers pour off the burn line.
        if (!record.done) {
          const frontY = record.minY + yShift + k * record.height;
          record.emitAcc += dt * 130;
          const budget = Math.min(18, Math.floor(record.emitAcc));
          record.emitAcc -= budget;
          for (let i = 0; i < budget; i++) {
            const ang = Math.random() * Math.PI * 2;
            const rr = Math.sqrt(Math.random());
            spawnParticle(
              record.cx + Math.cos(ang) * record.halfX * rr,
              frontY + rand(-0.06, 0.06),
              record.cz + Math.sin(ang) * record.halfZ * rr,
              rand(-0.25, 0.25), rand(0.5, 1.9), rand(-0.25, 0.25),
              (record.burn.r + 0.75) * 1.75,
              (record.burn.g + 0.42) * 1.75,
              (record.burn.b + 0.18) * 1.75,
              rand(0.03, 0.075), rand(0.003, 0.014),
              rand(0.7, 1.7),
              rand(0.15, 0.6), rand(0.55, 1.3), rand(0.06, 0.28)
            );
          }
        }

        if (k >= 1 && !record.done) {
          record.done = true;
          // final puff
          embers({ x: record.cx, y: record.minY + yShift + record.height * 0.75, z: record.cz }, record.burn, 34);
          finish();
        }

        if (record.done) {
          record.grace += dt;
          if (record.grace >= DISSOLVE_RESTORE_GRACE) { retire(); return false; }
        }
        return true;
      },
      release() { retire(); finish(); },
    });

    return promise;
  }

  /* ------------------------------------------------------------------- *
   * Frame update
   * ------------------------------------------------------------------- */

  function step(dt) {
    clock += dt;
    for (let i = effects.length - 1; i >= 0; i--) {
      const eff = effects[i];
      let alive = false;
      try { alive = eff.update(dt) !== false; } catch (e) {
        alive = false;
        try { eff.release(); } catch (e2) { /* ignore */ }
      }
      if (!alive) effects.splice(i, 1);
    }
    updateTrails(dt);
    updateHalos(dt);
    updateParticles(dt);
  }

  function update(dt) {
    if (disposed) return;
    let d = Number(dt);
    if (!Number.isFinite(d) || d <= 0) return;
    if (d > 2) d = 2;             // absurd tab-restore deltas
    // Sub-step so fast-forward still integrates plausibly without stalling.
    const steps = Math.min(6, Math.max(1, Math.ceil(d / 0.05)));
    const sub = d / steps;
    for (let i = 0; i < steps; i++) step(sub);
  }

  /* ------------------------------------------------------------------- *
   * Resize hook (keeps point sizing honest)
   * ------------------------------------------------------------------- */

  const onResize = () => {
    if (disposed) return;
    partMat.uniforms.uViewH.value = viewportPixels();
  };
  if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('resize', onResize);
  }

  /* ------------------------------------------------------------------- *
   * dispose
   * ------------------------------------------------------------------- */

  function dispose() {
    if (disposed) return;
    disposed = true;

    if (typeof window !== 'undefined' && window.removeEventListener) {
      window.removeEventListener('resize', onResize);
    }

    // Effects first — their release() restores dissolves and resolves promises.
    for (const eff of effects.splice(0, effects.length)) {
      try { eff.release(); } catch (e) { /* ignore */ }
    }

    for (const [mesh, record] of dissolves) {
      try {
        restoreDissolve(record);
        if (record.resolve) { const r = record.resolve; record.resolve = null; r(); }
      } catch (e) { /* ignore */ }
      dissolves.delete(mesh);
    }

    for (const [mesh, h] of halos) {
      try { releaseHalo(mesh, h); } catch (e) { /* ignore */ }
    }
    halos.clear();
    trails.clear();

    clearParticles();

    flashPool.disposeAll();
    shockPool.disposeAll();
    haloPool.disposeAll();
    shieldPool.disposeAll();
    beamPool.disposeAll();

    if (points.parent) points.parent.remove(points);
    if (root.parent) root.parent.remove(root);
    root.clear();

    for (const g of disposables.geometries) { try { g.dispose(); } catch (e) { /* ignore */ } }
    for (const m of disposables.materials) { try { m.dispose(); } catch (e) { /* ignore */ } }
    for (const t of disposables.textures) { try { t.dispose(); } catch (e) { /* ignore */ } }
    disposables.geometries.length = 0;
    disposables.materials.length = 0;
    disposables.textures.length = 0;
    dissolveMatPool.clear();
    shieldGeom = null;
    beamGeom = null;
  }

  /* ------------------------------------------------------------------- */

  return {
    impactFlash,
    dissolve,
    endDissolve,
    embers,
    trail,
    shieldCrack,
    lightColumn,
    warningHalo,
    removeHalo,
    update,
    dispose,
  };
}

export default createFX;
