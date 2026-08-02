/**
 * @file scene.js — BattleChess renderer, stage dressing, camera rig and post FX.
 *
 * Owns the WebGLRenderer, the EffectComposer chain, the lighting rig, the
 * procedurally generated environment map, the void backdrop / starfield, the
 * reflective floor, OrbitControls, camera presets, camera shake, resize handling
 * and WebGL context-loss recovery.
 *
 * Nothing in here knows anything about chess. `main.js` adds the board and piece
 * groups to `stage.scene` and calls `stage.render(dt)` once per frame.
 *
 * The stage is built to the CONTRACT.md section 9 quality bar:
 *   ACESFilmic tone mapping @ exposure 1.1, sRGB output, PCF soft shadows,
 *   RenderPass -> UnrealBloomPass(0.65 / 0.5 / 0.85) -> OutputPass,
 *   devicePixelRatio capped at 2, and `quality:'low'` bypassing bloom + shadows.
 */

import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  BackSide,
  BoxGeometry,
  BufferGeometry,
  CanvasTexture,
  Color,
  DirectionalLight,
  Fog,
  Float32BufferAttribute,
  Group,
  HemisphereLight,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  PCFSoftShadowMap,
  PerspectiveCamera,
  PlaneGeometry,
  PMREMGenerator,
  Points,
  PointsMaterial,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  SpotLight,
  SRGBColorSpace,
  Vector2,
  Vector3,
  WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

// ---------------------------------------------------------------------------
// Tuning
// ---------------------------------------------------------------------------

// Tuned empirically against real rendered frames at every camera preset, not
// picked on paper. The original 0.65/0.85 pair blew the light squares to pure
// white on the 'top' and 'white' presets (the camera looks straight down the
// key light's reflection axis there). Raising the threshold means only genuinely
// emissive surfaces — faction glow, seams — bloom at all.
const BLOOM_STRENGTH = 0.4;
const BLOOM_RADIUS = 0.5;
const BLOOM_THRESHOLD = 0.96;

const BACKDROP_RADIUS = 420;
const CAMERA_FAR = 1200;

/**
 * Per-quality-tier settings. `low` bypasses the composer and shadows entirely.
 * @type {Object<string, {composer:boolean, shadows:boolean, shadowMap:number,
 *   pixelRatioCap:number, bloom:number, stars:number, floorRoughness:number}>}
 */
const QUALITY_TIERS = {
  low: {
    composer: false, shadows: false, shadowMap: 512,
    pixelRatioCap: 1, bloom: 0, stars: 900, floorRoughness: 0.55,
  },
  medium: {
    composer: true, shadows: true, shadowMap: 1024,
    pixelRatioCap: 1.5, bloom: BLOOM_STRENGTH * 0.85, stars: 1800, floorRoughness: 0.28,
  },
  high: {
    composer: true, shadows: true, shadowMap: 2048,
    pixelRatioCap: 2, bloom: BLOOM_STRENGTH, stars: 2800, floorRoughness: 0.16,
  },
};

/**
 * Named camera poses. Each is `{position, target}` in world space.
 * `cinematic` is the starting pose for the slow automatic orbit.
 * @type {Object<string, {position:[number,number,number], target:[number,number,number], orbit?:boolean}>}
 */
const CAMERA_PRESETS = {
  // Default hero shot: three-quarter view from behind White (positive Z).
  side:      { position: [6.4, 7.4, 10.2], target: [0, 0.55, 0] },
  // Straight down the board from White's chair.
  white:     { position: [0, 4.3, 11.6],   target: [0, 0.65, -0.4] },
  // ...and from the Imperium's.
  black:     { position: [0, 4.3, -11.6],  target: [0, 0.65, 0.4] },
  // Near-orthographic tactical view. The small +Z offset keeps the up-vector
  // stable AND keeps the polar angle clear of `controls.minPolarAngle`, so
  // OrbitControls does not nudge the camera when it takes over after the tween.
  top:       { position: [0, 14.0, 0.9],   target: [0, 0, 0] },
  // Low, slow, dramatic auto-orbit.
  cinematic: { position: [9.6, 4.2, 9.6],  target: [0, 0.85, 0], orbit: true },
};

const DEFAULT_PRESET = 'side';
const PRESET_TWEEN_SECONDS = 1.5;

/** Smootherstep — C2 continuous, so preset tweens have no visible velocity pop. */
function smootherstep(t) {
  const x = Math.min(1, Math.max(0, t));
  return x * x * x * (x * (x * 6 - 15) + 10);
}

/** Normalise an unknown quality string onto a known tier name. */
function normalizeQuality(q) {
  const key = String(q || '').toLowerCase();
  return QUALITY_TIERS[key] ? key : 'high';
}

// ---------------------------------------------------------------------------
// Procedural assets
// ---------------------------------------------------------------------------

/**
 * Build the throwaway scene that gets baked into the PMREM environment cube.
 *
 * This is a hand-rolled `RoomEnvironment`: an inverted box shell with emissive
 * panels — a bright softbox overhead, a cool cyan wall on the Starfleet (+Z)
 * side, a warm crimson wall on the Imperium (-Z) side, and dim neutral sides.
 * Baking this gives metals real specular structure without fetching an HDRI.
 *
 * @returns {{scene:Scene, dispose:() => void}}
 */
function buildEnvironmentScene() {
  const scene = new Scene();
  const owned = { geo: [], mat: [] };

  const panel = (w, h, d, color, intensity, pos, rot) => {
    const g = new BoxGeometry(w, h, d);
    const m = new MeshBasicMaterial({ color: new Color(color).multiplyScalar(intensity) });
    const mesh = new Mesh(g, m);
    mesh.position.set(pos[0], pos[1], pos[2]);
    if (rot) mesh.rotation.set(rot[0], rot[1], rot[2]);
    owned.geo.push(g);
    owned.mat.push(m);
    scene.add(mesh);
    return mesh;
  };

  // Room shell (seen from the inside).
  const shellGeo = new BoxGeometry(28, 20, 28);
  const shellMat = new MeshBasicMaterial({ color: 0x0b0f16, side: BackSide });
  owned.geo.push(shellGeo);
  owned.mat.push(shellMat);
  scene.add(new Mesh(shellGeo, shellMat));

  // Overhead softbox — the dominant specular highlight on every metal piece.
  panel(11, 0.4, 11, 0xfff2e2, 3.1, [0, 8.6, 0]);
  // Secondary ceiling strips give elongated, believable highlights.
  panel(2.2, 0.3, 20, 0xdfe9ff, 1.35, [-7.5, 8.2, 0]);
  panel(2.2, 0.3, 20, 0xdfe9ff, 1.35, [7.5, 8.2, 0]);
  // Starfleet cyan wall (+Z) and Imperium crimson wall (-Z).
  panel(22, 11, 0.4, 0x2a9df4, 0.62, [0, 1.5, 12.6]);
  panel(22, 11, 0.4, 0xff4a22, 0.42, [0, 1.5, -12.6]);
  // Dim neutral side walls keep the environment from looking two-toned.
  panel(0.4, 11, 22, 0x1a2230, 0.5, [-12.6, 1.5, 0]);
  panel(0.4, 11, 22, 0x1a2230, 0.5, [12.6, 1.5, 0]);
  // Dark floor so downward reflections read as "orbital dock at night".
  panel(24, 0.4, 24, 0x05070c, 1.0, [0, -8.6, 0]);

  return {
    scene,
    dispose() {
      owned.geo.forEach((g) => g.dispose());
      owned.mat.forEach((m) => m.dispose());
      scene.clear();
    },
  };
}

/**
 * Round soft-edged sprite used for star points, generated on a canvas.
 * @returns {CanvasTexture}
 */
function makeStarSprite() {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0.0, 'rgba(255,255,255,1)');
  g.addColorStop(0.25, 'rgba(255,255,255,0.85)');
  g.addColorStop(0.6, 'rgba(255,255,255,0.18)');
  g.addColorStop(1.0, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 64, 64);
  const tex = new CanvasTexture(canvas);
  tex.colorSpace = SRGBColorSpace;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Radial alpha ramp: opaque at the centre, fully transparent at the rim.
 * Used to fade the reflective floor plane into the void.
 * @param {number} innerStop Normalised radius where the falloff begins.
 * @returns {CanvasTexture}
 */
function makeRadialFalloff(innerStop = 0.16) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#000000';
  ctx.fillRect(0, 0, 512, 512);
  const g = ctx.createRadialGradient(256, 256, 512 * innerStop * 0.5, 256, 256, 250);
  g.addColorStop(0.0, '#ffffff');
  g.addColorStop(0.42, '#c8c8c8');
  g.addColorStop(0.75, '#4b4b4b');
  g.addColorStop(1.0, '#000000');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 512, 512);
  const tex = new CanvasTexture(canvas);
  tex.colorSpace = SRGBColorSpace;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Build a starfield as a single `Points` object on a spherical shell.
 * @param {number} count Number of stars.
 * @param {CanvasTexture} sprite Shared point sprite.
 * @returns {Points}
 */
function buildStarfield(count, sprite) {
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const c = new Color();
  for (let i = 0; i < count; i++) {
    // Uniform-ish distribution over a shell, biased slightly above the horizon.
    const u = Math.random() * 2 - 1;
    const theta = Math.random() * Math.PI * 2;
    const s = Math.sqrt(Math.max(0, 1 - u * u));
    const radius = 150 + Math.random() * 190;
    positions[i * 3 + 0] = Math.cos(theta) * s * radius;
    positions[i * 3 + 1] = (u * 0.62 + 0.22) * radius;
    positions[i * 3 + 2] = Math.sin(theta) * s * radius;

    // Mostly cool white, a scattering of blue giants and amber dwarfs.
    const roll = Math.random();
    if (roll > 0.93) c.setHSL(0.08, 0.55, 0.62);
    else if (roll > 0.76) c.setHSL(0.58, 0.65, 0.68);
    else c.setHSL(0.58, 0.12, 0.62 + Math.random() * 0.35);
    const dim = 0.35 + Math.random() * 0.65;
    colors[i * 3 + 0] = c.r * dim;
    colors[i * 3 + 1] = c.g * dim;
    colors[i * 3 + 2] = c.b * dim;
  }

  const pointsGeo = new BufferGeometry();
  pointsGeo.setAttribute('position', new Float32BufferAttribute(positions, 3));
  pointsGeo.setAttribute('color', new Float32BufferAttribute(colors, 3));
  pointsGeo.computeBoundingSphere();

  const mat = new PointsMaterial({
    size: 2.0,
    sizeAttenuation: false,
    map: sprite,
    transparent: true,
    depthWrite: false,
    fog: false,
    vertexColors: true,
    blending: AdditiveBlending,
    toneMapped: false,
  });

  const points = new Points(pointsGeo, mat);
  points.name = 'starfield';
  points.frustumCulled = false;
  points.renderOrder = -900;
  return points;
}

const BACKDROP_VERT = /* glsl */ `
  varying vec3 vDir;
  void main() {
    vDir = normalize( position );
    gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
  }
`;

const BACKDROP_FRAG = /* glsl */ `
  varying vec3 vDir;
  uniform vec3 uZenith;
  uniform vec3 uHorizon;
  uniform vec3 uNadir;
  uniform vec3 uWarm;
  uniform vec3 uCool;

  void main() {
    float h = clamp( vDir.y * 0.5 + 0.5, 0.0, 1.0 );
    vec3 col = mix( uNadir, uHorizon, smoothstep( 0.0, 0.5, h ) );
    col = mix( col, uZenith, smoothstep( 0.5, 1.0, h ) );

    // Faction wash: cool nebula toward +Z (Starfleet), warm toward -Z (Imperium).
    // NB: written as t*t rather than pow(t,2.0) — pow() with a negative base is
    // undefined in GLSL and t straddles zero.
    float t = ( vDir.y - 0.02 ) * 3.1;
    float band = exp( -( t * t ) );
    float cool = max( 0.0, vDir.z ) * band;
    float warm = max( 0.0, -vDir.z ) * band;
    col += uCool * cool * 0.55 + uWarm * warm * 0.42;

    gl_FragColor = vec4( col, 1.0 );
  }
`;

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} BattleChessStage
 * @property {Scene} scene The root scene. Add board / piece / FX groups to this.
 * @property {PerspectiveCamera} camera
 * @property {WebGLRenderer} renderer
 * @property {EffectComposer|null} composer Null while `quality === 'low'`.
 * @property {OrbitControls} controls
 * @property {() => void} resize Re-read the canvas size and reconfigure everything.
 * @property {(dt:number) => void} render Advance and draw one frame. `dt` in seconds.
 * @property {(q:'low'|'medium'|'high') => void} setQuality Switch quality tier live.
 * @property {(intensity:number) => void} shake Add decaying camera shake (0..1-ish).
 * @property {(name:string, opts?:{instant?:boolean}) => void} setCameraPreset Tween to a named pose.
 * @property {() => string} getCameraPreset Name of the most recently requested preset.
 * @property {() => void} dispose Free every GPU resource and detach listeners.
 */

/**
 * Create the BattleChess stage: renderer, camera rig, lighting, environment,
 * backdrop and post-processing.
 *
 * @param {Object} options
 * @param {HTMLCanvasElement} options.canvas Canvas to render into. Required.
 * @param {'low'|'medium'|'high'} [options.quality='high'] Initial quality tier.
 *   `'low'` bypasses the EffectComposer and shadow maps entirely.
 * @returns {BattleChessStage}
 * @throws {Error} If no canvas is supplied.
 *
 * @example
 * const stage = createStage({ canvas: document.getElementById('view'), quality: 'high' });
 * stage.scene.add(board.group);
 * stage.setCameraPreset('cinematic');
 * (function loop(){ requestAnimationFrame(loop); stage.render(clock.getDelta()); })();
 */
export function createStage({ canvas, quality = 'high' } = {}) {
  if (!canvas) throw new Error('scene.js: createStage requires a { canvas }');

  let tier = normalizeQuality(quality);
  let settings = QUALITY_TIERS[tier];
  let disposed = false;
  let contextLost = false;

  // --- renderer -----------------------------------------------------------
  const renderer = new WebGLRenderer({
    canvas,
    antialias: true,
    alpha: false,
    powerPreference: 'high-performance',
    preserveDrawingBuffer: false,
  });
  renderer.setClearColor(0x03050a, 1);
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.82;
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.shadowMap.type = PCFSoftShadowMap;
  renderer.shadowMap.enabled = settings.shadows;

  // --- scene --------------------------------------------------------------
  const scene = new Scene();
  scene.name = 'BattleChessStage';
  scene.fog = new Fog(0x05070d, 26, 105);

  const camera = new PerspectiveCamera(42, 1, 0.1, CAMERA_FAR);
  camera.position.set(...CAMERA_PRESETS[DEFAULT_PRESET].position);
  camera.lookAt(new Vector3(...CAMERA_PRESETS[DEFAULT_PRESET].target));
  scene.add(camera);

  /** Everything the stage allocated and must free in dispose(). */
  const ownedGeometries = [];
  const ownedMaterials = [];
  const ownedTextures = [];

  // --- environment map (procedural PMREM — no HDRI fetch) ------------------
  /** @type {import('three').WebGLRenderTarget|null} */
  let envRT = null;

  /**
   * Bake the procedural room into a PMREM cube and install it as `scene.environment`.
   * Called at start-up and again after a WebGL context restore, since the render
   * target that backs the map does not survive context loss.
   * @returns {void}
   */
  function buildEnvironment() {
    if (envRT) {
      envRT.dispose();
      envRT = null;
    }
    const pmrem = new PMREMGenerator(renderer);
    const envSource = buildEnvironmentScene();
    envRT = pmrem.fromScene(envSource.scene, 0.035, 0.1, 60);
    scene.environment = envRT.texture;
    scene.environmentIntensity = 0.85;
    envSource.dispose();
    pmrem.dispose();
  }

  buildEnvironment();

  // --- backdrop -----------------------------------------------------------
  const backdropGeo = new SphereGeometry(BACKDROP_RADIUS, 32, 24);
  const backdropMat = new ShaderMaterial({
    side: BackSide,
    depthWrite: false,
    depthTest: false,
    fog: false,
    toneMapped: false,
    uniforms: {
      uZenith: { value: new Color(0x02030a) },
      uHorizon: { value: new Color(0x070c18) },
      uNadir: { value: new Color(0x010206) },
      uWarm: { value: new Color(0x3a1108) },
      uCool: { value: new Color(0x08243f) },
    },
    vertexShader: BACKDROP_VERT,
    fragmentShader: BACKDROP_FRAG,
  });
  const backdrop = new Mesh(backdropGeo, backdropMat);
  backdrop.name = 'voidBackdrop';
  backdrop.frustumCulled = false;
  backdrop.renderOrder = -1000;
  backdrop.matrixAutoUpdate = false;
  scene.add(backdrop);
  ownedGeometries.push(backdropGeo);
  ownedMaterials.push(backdropMat);

  // --- starfield ----------------------------------------------------------
  const starSprite = makeStarSprite();
  ownedTextures.push(starSprite);
  let stars = buildStarfield(settings.stars, starSprite);
  scene.add(stars);

  // --- floor --------------------------------------------------------------
  const floorFalloff = makeRadialFalloff(0.18);
  ownedTextures.push(floorFalloff);
  const floorGeo = new PlaneGeometry(150, 150, 1, 1);
  const floorMat = new MeshStandardMaterial({
    color: 0x05070d,
    metalness: 0.95,
    roughness: settings.floorRoughness,
    envMapIntensity: 0.85,
    alphaMap: floorFalloff,
    transparent: true,
    depthWrite: false,
  });
  const floor = new Mesh(floorGeo, floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -0.34;
  floor.receiveShadow = true;
  floor.name = 'dockFloor';
  scene.add(floor);
  ownedGeometries.push(floorGeo);
  ownedMaterials.push(floorMat);

  // Soft cyan light-pool under the board so the deck does not float in blackness.
  const poolFalloff = makeRadialFalloff(0.04);
  ownedTextures.push(poolFalloff);
  const poolGeo = new PlaneGeometry(26, 26);
  const poolMat = new MeshBasicMaterial({
    color: new Color(0x2a9df4).multiplyScalar(0.16),
    map: poolFalloff,
    alphaMap: poolFalloff,
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
    fog: false,
    toneMapped: false,
  });
  const pool = new Mesh(poolGeo, poolMat);
  pool.rotation.x = -Math.PI / 2;
  pool.position.y = -0.33;
  pool.name = 'dockGlowPool';
  pool.renderOrder = 10;
  scene.add(pool);
  ownedGeometries.push(poolGeo);
  ownedMaterials.push(poolMat);

  // --- lighting rig -------------------------------------------------------
  const lights = new Group();
  lights.name = 'lightRig';
  scene.add(lights);

  // Key: hard spot from high above and slightly to the White-right.
  // 150cd, not 620: with decay 2 at ~14 units the old value clipped the board
  // to white from any camera near the reflection axis. Verified across the
  // side/white/black/top presets before committing.
  const keyLight = new SpotLight(0xfff0dc, 150, 0, Math.PI / 6.2, 0.42, 2.0);
  keyLight.position.set(4.2, 13.5, 5.4);
  keyLight.target.position.set(0, 0, 0);
  keyLight.castShadow = settings.shadows;
  keyLight.shadow.mapSize.set(settings.shadowMap, settings.shadowMap);
  keyLight.shadow.camera.near = 3;
  keyLight.shadow.camera.far = 40;
  keyLight.shadow.bias = -0.0007;
  keyLight.shadow.normalBias = 0.022;
  keyLight.shadow.radius = 3;
  lights.add(keyLight, keyLight.target);

  // Cool cyan fill from the Starfleet side (+Z).
  const coolFill = new DirectionalLight(0x2a9df4, 1.15);
  coolFill.position.set(-2.5, 4.2, 12);
  coolFill.target.position.set(0, 0.2, 0);
  lights.add(coolFill, coolFill.target);

  // Warm crimson rim from the Imperium side (-Z).
  const warmRim = new DirectionalLight(0xff5a2d, 1.45);
  warmRim.position.set(3.0, 3.4, -12);
  warmRim.target.position.set(0, 0.2, 0);
  lights.add(warmRim, warmRim.target);

  // Very dim bounce so shadow interiors are not pure black.
  const hemi = new HemisphereLight(0x2a3a52, 0x04060a, 0.42);
  lights.add(hemi);

  // --- controls -----------------------------------------------------------
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.rotateSpeed = 0.62;
  controls.zoomSpeed = 0.85;
  controls.panSpeed = 0.55;
  controls.screenSpacePanning = false;
  controls.minDistance = 5.5;
  controls.maxDistance = 34;
  // Clamp above the deck: 0.03 rad off vertical, 0.06 rad above the horizon.
  controls.minPolarAngle = 0.03;
  controls.maxPolarAngle = Math.PI * 0.5 - 0.06;
  controls.autoRotateSpeed = 0.42;
  controls.target.set(...CAMERA_PRESETS[DEFAULT_PRESET].target);
  controls.update();

  // Keep panning from dragging the pivot off into the void.
  const PAN_LIMIT = 6;
  const clampTarget = () => {
    controls.target.x = Math.min(PAN_LIMIT, Math.max(-PAN_LIMIT, controls.target.x));
    controls.target.z = Math.min(PAN_LIMIT, Math.max(-PAN_LIMIT, controls.target.z));
    // Never let the pivot drop below the deck: combined with maxPolarAngle this
    // guarantees the camera can never end up under the floor plane.
    controls.target.y = Math.min(4, Math.max(0, controls.target.y));
  };

  // --- post-processing ----------------------------------------------------
  /** @type {EffectComposer|null} */
  let composer = null;
  /** @type {RenderPass|null} */
  let renderPass = null;
  /** @type {UnrealBloomPass|null} */
  let bloomPass = null;
  /** @type {OutputPass|null} */
  let outputPass = null;

  /** Tear the composer chain down (used on quality change, resize-free). */
  function destroyComposer() {
    if (bloomPass) bloomPass.dispose();
    if (outputPass) outputPass.dispose();
    if (renderPass && typeof renderPass.dispose === 'function') renderPass.dispose();
    if (composer) composer.dispose();
    composer = null;
    renderPass = null;
    bloomPass = null;
    outputPass = null;
  }

  /** (Re)build RenderPass -> UnrealBloomPass -> OutputPass. */
  function buildComposer() {
    destroyComposer();
    if (!settings.composer) return;
    composer = new EffectComposer(renderer);
    composer.setPixelRatio(renderer.getPixelRatio());
    renderPass = new RenderPass(scene, camera);
    bloomPass = new UnrealBloomPass(
      new Vector2(size.w, size.h),
      settings.bloom,
      BLOOM_RADIUS,
      BLOOM_THRESHOLD,
    );
    outputPass = new OutputPass();
    composer.addPass(renderPass);
    composer.addPass(bloomPass);
    composer.addPass(outputPass);
    composer.setSize(size.w, size.h);
  }

  // --- sizing -------------------------------------------------------------
  const size = { w: 1, h: 1 };

  /**
   * Re-measure the canvas, then reconfigure camera, renderer and composer.
   * Safe to call at any time; `main.js` should also call it on window resize.
   * @returns {void}
   */
  function resize() {
    if (disposed) return;
    const rect = canvas.getBoundingClientRect();
    let w = Math.floor(rect.width);
    let h = Math.floor(rect.height);
    let updateStyle = false;
    if (w <= 1 || h <= 1) {
      w = window.innerWidth;
      h = window.innerHeight;
      updateStyle = true;
    }
    size.w = Math.max(1, w);
    size.h = Math.max(1, h);

    const dpr = Math.min(window.devicePixelRatio || 1, settings.pixelRatioCap, 2);
    renderer.setPixelRatio(dpr);
    renderer.setSize(size.w, size.h, updateStyle);
    camera.aspect = size.w / size.h;
    camera.updateProjectionMatrix();

    if (composer) {
      // EffectComposer.setPixelRatio() re-applies setSize() to every pass,
      // so this single call resizes the bloom render targets too.
      composer.setPixelRatio(dpr);
      composer.setSize(size.w, size.h);
    }
  }

  resize();
  buildComposer();

  const onWindowResize = () => resize();
  window.addEventListener('resize', onWindowResize, { passive: true });

  // --- context loss / restore ---------------------------------------------
  const onContextLost = (event) => {
    event.preventDefault();
    contextLost = true;
  };
  const onContextRestored = () => {
    contextLost = false;
    // The renderer re-initialises its own GL state; we must rebuild anything
    // that held render targets and force every program to recompile.
    renderer.shadowMap.enabled = settings.shadows;
    renderer.shadowMap.needsUpdate = true;
    scene.traverse((obj) => {
      const mat = obj.material;
      if (!mat) return;
      if (Array.isArray(mat)) mat.forEach((m) => { m.needsUpdate = true; });
      else mat.needsUpdate = true;
    });
    buildEnvironment();
    resize();
    buildComposer();
  };
  canvas.addEventListener('webglcontextlost', onContextLost, false);
  canvas.addEventListener('webglcontextrestored', onContextRestored, false);

  // --- camera presets -----------------------------------------------------
  let currentPreset = DEFAULT_PRESET;
  const tween = {
    active: false,
    t: 0,
    duration: PRESET_TWEEN_SECONDS,
    fromPos: new Vector3(),
    toPos: new Vector3(),
    fromTarget: new Vector3(),
    toTarget: new Vector3(),
    orbitAfter: false,
  };

  /**
   * Move the camera to a named preset. Changes tween smoothly (never snap)
   * unless `opts.instant` is set. Unknown names are ignored.
   *
   * Presets: `'side'` (default three-quarter hero shot from behind White),
   * `'white'`, `'black'`, `'top'`, `'cinematic'` (slow automatic orbit).
   *
   * @param {'side'|'white'|'black'|'top'|'cinematic'} name
   * @param {{instant?:boolean, duration?:number}} [opts]
   * @returns {void}
   */
  function setCameraPreset(name, opts = {}) {
    const preset = CAMERA_PRESETS[name];
    if (!preset) return;
    currentPreset = name;
    controls.autoRotate = false;

    const toPos = new Vector3(...preset.position);
    const toTarget = new Vector3(...preset.target);

    if (opts.instant) {
      camera.position.copy(toPos);
      controls.target.copy(toTarget);
      camera.lookAt(controls.target);
      controls.update();
      controls.autoRotate = !!preset.orbit;
      tween.active = false;
      return;
    }

    tween.fromPos.copy(camera.position);
    tween.toPos.copy(toPos);
    tween.fromTarget.copy(controls.target);
    tween.toTarget.copy(toTarget);
    tween.t = 0;
    tween.duration = Math.max(0.05, opts.duration || PRESET_TWEEN_SECONDS);
    tween.orbitAfter = !!preset.orbit;
    tween.active = true;
    // Controls stay *enabled* so their 'start' event can cancel the flight the
    // moment the user grabs the camera; render() simply skips controls.update()
    // while a tween owns the transform.
  }

  /**
   * @returns {string} Name of the most recently requested camera preset.
   */
  function getCameraPreset() {
    return currentPreset;
  }

  // A user grab cancels any in-flight tween so the camera never fights the mouse.
  const onControlsStart = () => {
    tween.active = false;
    controls.autoRotate = false;
  };
  controls.addEventListener('start', onControlsStart);

  // --- camera shake -------------------------------------------------------
  let shakeAmp = 0;
  let shakeTime = 0;
  const shakeOffset = new Vector3();
  const savedPos = new Vector3();

  /**
   * Add decaying camera shake. Call once per impact; repeated calls accumulate
   * up to a hard ceiling. Decay is driven by {@link render}'s `dt`.
   *
   * @param {number} [intensity=0.35] Roughly the peak positional amplitude in
   *   world units. 0.15 is a light tap, 0.4 a solid hit, 0.8 a capital strike.
   * @returns {void}
   */
  function shake(intensity = 0.35) {
    if (!Number.isFinite(intensity) || intensity <= 0) return;
    shakeAmp = Math.min(1.1, shakeAmp + intensity);
  }

  // --- quality ------------------------------------------------------------
  /**
   * Switch quality tier at runtime. `'low'` bypasses the EffectComposer and
   * shadows entirely and still renders a correct (if flatter) image.
   *
   * @param {'low'|'medium'|'high'} q
   * @returns {void}
   */
  function setQuality(q) {
    const next = normalizeQuality(q);
    if (next === tier) return;
    tier = next;
    settings = QUALITY_TIERS[tier];

    renderer.shadowMap.enabled = settings.shadows;
    renderer.shadowMap.needsUpdate = true;
    keyLight.castShadow = settings.shadows;
    keyLight.shadow.mapSize.set(settings.shadowMap, settings.shadowMap);
    if (keyLight.shadow.map) {
      keyLight.shadow.map.dispose();
      keyLight.shadow.map = null;
    }
    floorMat.roughness = settings.floorRoughness;
    floorMat.needsUpdate = true;

    // Toggling shadowMap.enabled changes every program's defines.
    scene.traverse((obj) => {
      const mat = obj.material;
      if (!mat) return;
      if (Array.isArray(mat)) mat.forEach((m) => { m.needsUpdate = true; });
      else mat.needsUpdate = true;
    });

    // Rebuild the starfield at the new density.
    if (stars) {
      scene.remove(stars);
      stars.geometry.dispose();
      stars.material.dispose();
    }
    stars = buildStarfield(settings.stars, starSprite);
    scene.add(stars);

    resize();
    buildComposer();
  }

  // --- frame --------------------------------------------------------------
  /**
   * Advance the camera rig and draw one frame.
   *
   * @param {number} [dt=1/60] Seconds since the previous frame. Clamped
   *   internally so a background-tab stall cannot fling the camera.
   * @returns {void}
   */
  function render(dt = 1 / 60) {
    if (disposed || contextLost) return;
    const step = Math.min(Math.max(Number.isFinite(dt) ? dt : 1 / 60, 0), 0.1);

    if (tween.active) {
      // Drive the camera directly while tweening. OrbitControls re-derives its
      // spherical state from the camera position every update(), so letting it
      // run mid-tween would re-apply the distance/polar clamps to every
      // intermediate pose and make the flight path lurch. We hand control back
      // (and resync it) the instant the tween lands.
      tween.t += step;
      const k = smootherstep(tween.t / tween.duration);
      camera.position.lerpVectors(tween.fromPos, tween.toPos, k);
      controls.target.lerpVectors(tween.fromTarget, tween.toTarget, k);
      camera.lookAt(controls.target);
      if (tween.t >= tween.duration) {
        tween.active = false;
        camera.position.copy(tween.toPos);
        controls.target.copy(tween.toTarget);
        camera.lookAt(controls.target);
        controls.autoRotate = tween.orbitAfter;
        controls.update();
      }
    } else {
      controls.update(step);
      clampTarget();
    }

    // Decaying shake, applied after controls so it never feeds back into the
    // orbit state. Restored immediately after the draw.
    let shaking = false;
    if (shakeAmp > 0.0008) {
      shaking = true;
      shakeTime += step;
      shakeAmp *= Math.exp(-5.5 * step);
      const a = shakeAmp;
      // Three decorrelated sine stacks read as noise without a noise texture.
      shakeOffset.set(
        (Math.sin(shakeTime * 47.3) + 0.6 * Math.sin(shakeTime * 111.7)) * a * 0.22,
        (Math.sin(shakeTime * 61.1) + 0.6 * Math.sin(shakeTime * 89.3)) * a * 0.19,
        (Math.sin(shakeTime * 53.9) + 0.6 * Math.sin(shakeTime * 127.1)) * a * 0.22,
      );
      savedPos.copy(camera.position);
      camera.position.add(shakeOffset);
      camera.updateMatrixWorld(true);
    } else {
      shakeAmp = 0;
      shakeTime = 0;
    }

    if (composer) composer.render(step);
    else renderer.render(scene, camera);

    if (shaking) {
      camera.position.copy(savedPos);
      camera.updateMatrixWorld(true);
    }
  }

  // --- teardown -----------------------------------------------------------
  /**
   * Free every GPU resource the stage owns and detach all listeners.
   * The scene's children added by other modules are removed but NOT disposed —
   * each module disposes its own assets.
   * @returns {void}
   */
  function dispose() {
    if (disposed) return;
    disposed = true;

    window.removeEventListener('resize', onWindowResize);
    canvas.removeEventListener('webglcontextlost', onContextLost);
    canvas.removeEventListener('webglcontextrestored', onContextRestored);
    controls.removeEventListener('start', onControlsStart);
    controls.dispose();

    destroyComposer();

    if (stars) {
      scene.remove(stars);
      stars.geometry.dispose();
      stars.material.dispose();
      stars = null;
    }

    if (keyLight.shadow && keyLight.shadow.map) {
      keyLight.shadow.map.dispose();
      keyLight.shadow.map = null;
    }

    ownedGeometries.forEach((g) => g.dispose());
    ownedMaterials.forEach((m) => m.dispose());
    ownedTextures.forEach((t) => t.dispose());
    ownedGeometries.length = 0;
    ownedMaterials.length = 0;
    ownedTextures.length = 0;

    if (envRT) {
      envRT.dispose();
      envRT = null;
    }
    scene.environment = null;
    scene.clear();
    renderer.dispose();
  }

  const stage = {
    scene,
    camera,
    renderer,
    controls,
    resize,
    render,
    setQuality,
    shake,
    setCameraPreset,
    getCameraPreset,
    dispose,
    /** Convenience handles for fx.js / hud.js. */
    lights: { key: keyLight, coolFill, warmRim, hemi },
    get quality() { return tier; },
    get composer() { return composer; },
  };

  return stage;
}
