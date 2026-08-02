# BattleChess — Build Contract

**Every module codes against this document.** It is the integration surface: if a
module needs something not specified here, it must adapt to this doc, not change it.

---

## 1. Concept

A Three.js "battle chess" viewer for the Forge LLM Chess Arena. It renders live
matches from the existing `/api/chess` endpoints in 3D, with animated moves and
capture combat.

**Factions** (this is the piece-distinguishability solution — the two sides are
different *silhouettes*, not just different colors):

| Side  | Faction               | Aesthetic |
|-------|-----------------------|-----------|
| white | **Starfleet / Federation** | Star Trek futurism: polished white duranium + brushed silver, cyan/azure emissive, swept curves, saucer + nacelle + deflector-dish motifs, LCARS accent bands. Optimistic, clean, aerodynamic. |
| black | **Imperium of Man**   | Warhammer 40k gothic: blackened iron + tarnished brass filigree, crimson/amber plasma emissive, flying buttresses, spikes, skulls, aquila wings. Heavy, ornate, brutal, vertical. |

Both sets must read clearly at a glance from the default camera, and each piece
type must be identifiable by silhouette alone.

---

## 2. Directory layout

```
BattleChess/
  CONTRACT.md            <- this file
  README.md
  index.html             <- entry point (ES modules, importmap)
  css/battlechess.css
  js/
    main.js              <- bootstrap: wires everything, owns the frame loop
    scene.js             <- renderer, camera, lights, post-processing, controls
    board.js             <- board mesh, squares, coordinate math, highlights
    pieces.js            <- GLB loading, faction materials, piece instances
    animation.js         <- move/capture/promotion animation queue
    fx.js                <- particles, beams, shields, impact VFX
    api.js               <- Forge /api/chess client (+ offline demo mode)
    gamestate.js         <- FEN parse, diffing prev->next board, move events
    hud.js               <- overlay UI: rosters, move log, commentary, controls
  blender/
    build_pieces.py      <- procedural generator, run inside Blender
  assets/models/
    federation.glb
    imperium.glb
  vendor/                <- three.js r184 + addons (already fetched)
```

---

## 3. Coordinate system (authoritative)

Three.js right-handed, **Y up**. One board square = **1.0 world unit**.

- File `a..h` -> index `f = 0..7`; rank `1..8` -> index `r = 0..7`.
- Square center: **`x = f - 3.5`**, **`y = 0`**, **`z = 3.5 - r`**.
- So **a1 = (-3.5, 0, +3.5)** and **h8 = (+3.5, 0, -3.5)**.
- White home ranks (1,2) are at **positive Z**; Black home ranks (7,8) at negative Z.
- Board top surface sits at **y = 0**; pieces stand on it and extend +Y.
- Default camera is behind White (positive Z), looking toward -Z.

`board.js` exports the canonical helpers — everything else must use them:

```js
export function squareToWorld(square)   // "e4" -> THREE.Vector3
export function worldToSquare(vec3)     // THREE.Vector3 -> "e4" | null
export function squareToIndices(square) // "e4" -> {file:4, rank:3}
export const SQUARE_SIZE = 1.0
export const BOARD_HALF  = 4.0
```

---

## 4. Model contract (Blender -> Three.js)

Two GLB files, one per faction: `assets/models/federation.glb`, `assets/models/imperium.glb`.

**Each GLB contains exactly 6 root-level meshes, named lowercase:**
`pawn`, `knight`, `bishop`, `rook`, `queen`, `king`

Rules every mesh must satisfy:

- **Origin at base center**: local origin `(0,0,0)` is the center of the footprint;
  geometry extends upward in **+Y** (GLTF export converts Blender's +Z up).
- **Facing**: piece "front" faces **-Z** (i.e. toward the enemy for White). The
  loader rotates Black 180 degrees about Y, so asymmetric pieces (knight) must be modeled
  facing -Z.
- **Footprint radius <= 0.40** so pieces never overlap adjacent squares.
- **Heights** (world units, tip of model):

  | piece  | height |
  |--------|--------|
  | pawn   | 0.85   |
  | knight | 1.05   |
  | rook   | 1.00   |
  | bishop | 1.15   |
  | queen  | 1.35   |
  | king   | 1.50   |

- **Two material slots, named exactly**:
  - `body` — the metal/stone shell (PBR, non-emissive)
  - `glow` — the emissive accent (windows, plasma, energy rings, eye slits)

  `pieces.js` overrides both at runtime per faction, so Blender only has to get
  the *slot assignment* right — which polygons are body vs glow.
- **Budget**: aim <= 8k triangles per piece; apply all modifiers before export;
  no n-gons with >6 sides; smooth shading with sharp-edge preservation.
- Meshes must be **separate objects**, not joined, and have no parent transforms
  (apply all transforms: location/rotation/scale) before export.

---

## 5. Faction material palette (Three.js runtime override)

```js
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
```

Board palette: dark void-metal deck, squares as inlaid panels — light squares
`#cfd6de` brushed steel, dark squares `#2b3038` graphite, with a thin emissive
seam grid (`#2a9df4` at 12% intensity) and an engraved coordinate ring.

---

## 6. Game state contract

`gamestate.js` owns board diffing and emits move events. It must work from a
**FEN string alone** (the API always returns `fen`), and enrich with SAN/UCI when
the API supplies moves.

```js
// Parse FEN piece-placement into a 64-entry map.
export function parseFEN(fen)  // -> { board: {e4: {type:'p', color:'white'}, ...}, turn, ... }

// Diff two parsed positions into renderable events.
export function diffPositions(prev, next, moveMeta)
// -> [{ kind:'move'|'capture'|'castle'|'enpassant'|'promotion',
//        from:'e2', to:'e4', piece:{type,color},
//        captured:{type,color}|null, extra:{rookFrom,rookTo}|null }]
```

Diffing must handle: normal moves, captures, **castling** (king + rook in one
event), **en passant** (captured pawn is NOT on the destination square), and
**promotion** (piece type changes).

---

## 7. API contract (existing Forge endpoints — do not modify the server)

Base URL is same-origin by default, overridable via `?api=http://127.0.0.1:5000`.

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/api/chess` | `{matches:[...]}` newest first |
| GET    | `/api/chess/<id>` | full match state |
| POST   | `/api/chess` | create match |
| POST   | `/api/chess/<id>/step` | advance one ply (may include `new_commentary`) |
| POST   | `/api/chess/<id>/resign` | `{side}` |
| GET    | `/api/chess/<id>/pgn` | PGN text |

Match payload fields BattleChess consumes:
`id, fen, turn, status, result, reason, white_model, black_model, in_check,
halfmove_count, moves[], captures[], commentary[], tokens{}, has_house_moves,
protocol_loss_by, judge_model`

Each `moves[]` entry: `{n, side, san, uci, thinking, forced, attempts, ms,
source, input_tokens, output_tokens, cost_usd}`.
`source` is `model` | `adjudicated` | `forfeit` — **the HUD must visually flag
non-`model` plies** (house moves are an integrity signal, per the arena's ledger).

**Offline demo mode**: if the API is unreachable, `api.js` falls back to a canned
game (the shipped `demo-game.json` PGN-derived move list) so the scene is always
demonstrable without a server.

---

## 8. Animation contract

`animation.js` exposes a promise-based queue; `main.js` never mutates transforms
directly.

```js
export class AnimationQueue {
  enqueue(event)        // event from diffPositions()
  update(deltaSeconds)  // called each frame
  get isBusy()
  setSpeed(multiplier)
}
```

Choreography (durations at 1x):

- **move** (0.85s): piece rises 0.35u, arcs along a quadratic bezier to target,
  settles with a small overshoot ease. Trail VFX in faction glow color.
- **capture** (1.6s): attacker advances to contact; defender takes an impact
  flash, shield-bubble crack, then disintegrates (dissolve shader + ember
  particles rising); attacker lands on the square. Captured piece flies to the
  side rack.
- **castle** (1.1s): king and rook animate simultaneously, rook passing under/behind.
- **promotion** (1.4s): pawn dissolves upward into a column of light, the new
  piece materializes downward out of it.
- **check**: the checked king pulses a red warning halo, camera adds a subtle shake.
- **checkmate**: slow orbital camera push-in on the losing king, desaturate.

All animations must be **skippable** (`queue.flush()` snaps to final state) and
must never leave the board in a state that disagrees with the authoritative FEN.

---

## 9. Rendering / quality bar

- `WebGLRenderer` with `antialias:true`, `ACESFilmicToneMapping`,
  `toneMappingExposure = 0.82`, `outputColorSpace = SRGBColorSpace`, shadows on
  (`PCFSoftShadowMap`).
- **EffectComposer**: RenderPass -> UnrealBloomPass (strength 0.40, radius 0.5,
  threshold 0.96) -> OutputPass. Bloom is what sells the emissive faction glow.

> **These numbers were measured, not chosen.** The first draft of this contract
> specified exposure 1.1 / bloom 0.65 / threshold 0.85, with a 620cd key spot and
> metalness ~0.9 board panels. Rendered frames showed that combination clipping
> the light squares to pure white on the `top` and `white` camera presets — those
> cameras sit near the key light's reflection axis. The committed values
> (exposure 0.82, bloom 0.40/0.96, key spot 150cd, board panels metalness 0.35 /
> roughness 0.55) were verified against captured frames at every preset. If you
> re-tune, re-shoot all four presets — a change that looks good side-on can be
> unusable from above.
- Lighting: low-key/dramatic. Key spot above the board, cool cyan fill from the
  White side, warm crimson rim from the Black side, subtle hemisphere ambient,
  plus an environment map generated procedurally (`RoomEnvironment`-style or a
  hand-built gradient cube) so metals actually read as metal.
- Contact shadows under pieces; a subtle reflective floor plane is welcome.
- **Must hold 60fps** at 1080p on integrated graphics: keep draw calls low
  (reuse geometry/materials across identical pieces), cap particles, and
  degrade gracefully (`?quality=low` disables bloom + shadows).
- Handle resize, devicePixelRatio (cap at 2), and WebGL context loss.

---

## 10. HUD

Framed in the Forge LCARS visual language (see `forge/static/lcars.css` for the
existing palette) but *not* dependent on it — BattleChess ships its own CSS.

Required elements:
- Faction rosters (top-left White/Starfleet, top-right Black/Imperium) with model
  name, captured-piece rack, material advantage, token spend + cost.
- Move log (scrolling, SAN, with `source != 'model'` plies flagged).
- Judge commentary panel (latest beat, with speaker styling).
- Controls: play/pause auto-step, step-once, animation speed, camera presets
  (side / white POV / black POV / top-down / cinematic orbit), quality toggle.
- Status line: turn, check/checkmate, result, halfmove count.
- Everything must degrade to "no match loaded" gracefully.

---

## 11. Non-negotiables

1. **Do not modify anything outside `BattleChess/`.** The server API is fixed.
2. **No external network calls at runtime.** Three.js is vendored in `vendor/`;
   models are local GLBs. No CDN, no remote fonts.
3. ES modules only, loaded via an `<script type="importmap">` mapping
   `three` -> `./vendor/three.module.js` and `three/addons/` -> `./vendor/addons/`.
4. No build step. It must run by opening the page from the Flask static server.
5. Every module is a single-responsibility ES module with named exports.
6. Guard against the authoritative-state trap: the **FEN is truth**. Animations
   are cosmetic; a desync must self-heal by snapping to the FEN.
