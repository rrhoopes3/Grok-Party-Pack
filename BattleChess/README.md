# BattleChess

A Three.js battle-chess viewer for the Forge **LLM Chess Arena**. It takes the
match state the arena already produces — FEN, SAN move list, judge commentary,
token ledger — and renders it as a 3D board where two very different armies
animate their moves and fight over captures.

You have never seen this project before? Start at [Running it](#running-it),
then read [The faction concept](#the-faction-concept). Everything else is
reference material.

---

## Running it

BattleChess is plain ES modules. **There is no build step, no bundler, no
package.json, no TypeScript.** You need exactly two things: the files, and any
web server.

### Option A — through the Forge (the intended path)

The Forge Flask app already serves this directory. Start the Forge as usual and
open the BattleChess page it exposes. Same-origin means the API client talks to
`/api/chess` with no configuration at all.

### Option B — standalone static server

```bash
cd BattleChess
python -m http.server 8791
# then open http://127.0.0.1:8791/
```

Point it at a Forge instance running elsewhere with the `api` query parameter:

```
http://127.0.0.1:8791/?api=http://127.0.0.1:5000
```

If no API answers, `js/api.js` falls back to the canned game in
`assets/demo-game.json`, so the scene is always demonstrable.

### Option C — double-clicking index.html

**This does not work, by design.** Browsers block ES module imports over the
`file://` scheme. The page will detect the failure and show a
`MODULE LOAD FAILURE` panel telling you to serve it over HTTP. Use option A or B.

### Query parameters

| Parameter  | Values        | Effect |
|------------|---------------|--------|
| `api`      | a base URL    | Point the API client at a non-same-origin Forge |
| `quality`  | `low`         | Boot with bloom and shadows off (integrated GPUs) |

### Requirements

A current browser with WebGL. **Nothing is fetched from the network at
runtime** — three.js r184 is vendored under `vendor/`, the models are local
GLBs, and every font is a locally-installed system font. If you are offline,
BattleChess still runs.

---

## The faction concept

The oldest usability problem in 3D chess is that both armies are the same
sixteen shapes in two shades, and from a raked camera you cannot tell a bishop
from a pawn, let alone whose it is. BattleChess solves that by making the two
sides **different silhouettes**, not different colours.

| Side  | Faction                  | Language |
|-------|--------------------------|----------|
| white | **Starfleet / Federation** | Star Trek futurism. Polished white duranium, brushed silver, cyan/azure emissive. Swept curves, saucer sections, nacelles, deflector dishes, LCARS accent bands. Optimistic, clean, aerodynamic. |
| black | **Imperium of Man**      | Warhammer 40k gothic. Blackened iron, tarnished brass filigree, crimson/amber plasma emissive. Flying buttresses, spikes, skulls, aquila wings. Heavy, ornate, brutal, vertical. |

The rule both piece sets must satisfy: **each piece type is identifiable by
silhouette alone**, from the default camera, without reading its colour.

The HUD carries the same split. Starfleet occupies the left of the screen in
cyan, with soft LCARS pill geometry and rounded elbows. The Imperium occupies
the right in crimson and brass, with hard clipped corners, hairline filigree
rules and a gothic serif. You always know which half of the screen you are
reading.

---

## Architecture

```
BattleChess/
  CONTRACT.md          the integration surface every module codes against
  README.md            this file
  index.html           entry point: canvas, HUD root, importmap, boot bridge
  css/battlechess.css  the whole visual identity; depends on nothing
  js/
    main.js            bootstrap — wires everything, owns the frame loop
    scene.js           renderer, camera, lights, post-processing, controls
    board.js           board mesh, squares, coordinate math, highlights
    pieces.js          GLB loading, faction materials, piece instances
    animation.js       move / capture / promotion animation queue
    fx.js              particles, beams, shields, impact VFX
    api.js             Forge /api/chess client + offline demo mode
    gamestate.js       FEN parsing, prev->next diffing, move events
    hud.js             overlay UI — rosters, move log, commentary, controls
  blender/
    build_pieces.py    procedural piece generator, run inside Blender
  assets/
    demo-game.json     canned game for offline mode
    models/
      federation.glb   6 meshes: pawn knight bishop rook queen king
      imperium.glb     same six, gothic
  vendor/              three.js r184 + addons (already fetched, never edited)
```

### How the pieces fit together

```
        ┌──────────┐   poll / step        ┌───────────────┐
        │  api.js  │◄────────────────────►│ Forge server  │
        └────┬─────┘   match payload      │  /api/chess   │
             │                            └───────────────┘
             ▼
        ┌──────────────┐  FEN in, events out
        │ gamestate.js │──────────────┐
        └──────────────┘              │
             │ authoritative FEN      │ [{kind:'capture', from, to, …}]
             ▼                        ▼
   ┌──────────────────┐        ┌────────────────┐
   │ main.js          │───────►│ animation.js   │──► pieces.js / fx.js
   │ frame loop       │        │ promise queue  │
   └────────┬─────────┘        └────────────────┘
            │ matchPayload
            ▼
       ┌──────────┐  onCommand(name, payload)
       │  hud.js  │──────────────► back into main.js
       └──────────┘
```

Two rules make this stable:

1. **The FEN is truth.** Animations are cosmetic. If the animated board ever
   disagrees with the FEN, the renderer snaps to the FEN. A dropped frame, a
   skipped animation or a network hiccup can never corrupt the position.
2. **hud.js is presentation only.** It imports nothing from the 3D stack —
   no scene, no pieces, no animation, no three.js. It receives payloads and
   emits command names. That is the entire coupling.

### The importmap

`index.html` declares the module resolution for every other file:

```html
<script type="importmap">
{"imports": {
  "three": "./vendor/three.module.js",
  "three/addons/": "./vendor/addons/"
}}
</script>
```

Every module therefore imports the **bare specifiers**, never a relative path
into `vendor/`:

```js
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
```

Vendored addons available: `controls/OrbitControls.js`,
`loaders/GLTFLoader.js`, `utils/BufferGeometryUtils.js`,
`postprocessing/{EffectComposer,RenderPass,ShaderPass,UnrealBloomPass,OutputPass,MaskPass}.js`,
`shaders/{CopyShader,LuminosityHighPassShader,OutputShader}.js`.

---

## The page shell

`index.html` owns a small, stable set of ids. They are documented in a comment
block at the top of the file; this is the summary.

| Id | What it is |
|----|-----------|
| `#bc-canvas` | The `<canvas>`. Hand it to `new THREE.WebGLRenderer({ canvas })`. |
| `#bc-hud` | HUD overlay root. Starts empty; pass it to `createHUD()`. |
| `#bc-loading` | Full-screen loading veil, visible at first paint. |
| `#bc-loading-bar` / `-pct` / `-label` | Progress fill, percentage text, stage label. |
| `#bc-error` | Fatal error / no-WebGL panel. `hidden` until something breaks. |

### The boot bridge

`window.BattleChessBoot` exists before any module parses, so `main.js` can
report progress while it is still importing and fetching GLBs:

```js
BattleChessBoot.setProgress(42, 'LOADING IMPERIUM GEOMETRY');
BattleChessBoot.done();                        // fade the veil out
BattleChessBoot.fail('WebGL context lost', stack);
BattleChessBoot.hasWebGL;                      // probed at first paint
```

`hud.setLoading()` and `hud.showError()` delegate to this object when it
exists, so both paths drive the same DOM and never fight over it.

The shell also handles, on its own, with no module loaded: no-WebGL detection,
`<noscript>`, resource-load failures (a missing `main.js` shows a real error
instead of a blank page), and a 20-second soft watchdog that tells you to check
the console rather than staring at a frozen bar.

---

## The HUD

```js
import { createHUD } from './hud.js';

const hud = createHUD({
  root: document.getElementById('bc-hud'),
  api,                                   // optional, only duck-typed
  onCommand(name, payload) { /* … */ }
});
```

### Methods

| Call | Effect |
|------|--------|
| `update(matchPayload)` | Full re-sync from an `/api/chess/<id>` payload. `null` renders the "no match loaded" state. |
| `setStatus(text)` | Sticky note chip on the status line. Falsy clears it. Never hides the computed status. |
| `pushCommentary(entry)` | Append one judge beat. Accepts a commentary object or a bare string. De-duplicated against `update()`. |
| `setLoading(pct, label)` | Drive the loading veil. `pct >= 100`, `null` or `false` fades it out. |
| `showError(msg, detail)` | Raise the fatal error panel. |
| `setBusy(flag)` | `true` while the animation queue runs. Gates STEP so the board cannot desync. |
| `destroy()` | Remove every node, listener and timer. Safe to call twice. |

### Commands

Controls never touch the 3D stack. They call `onCommand(name, payload)`:

| Name | Payload | Meaning |
|------|---------|---------|
| `play` | `{}` | Start auto-stepping |
| `pause` | `{}` | Stop auto-stepping |
| `step` | `{}` | Advance exactly one ply |
| `skip` | `{}` | Flush animations to their final state |
| `speed` | `{ value: 0.5 \| 1 \| 2 \| 4 }` | Animation speed multiplier |
| `camera` | `{ preset }` | `side` · `white` · `black` · `top` · `orbit` |
| `quality` | `{ value: 'high' \| 'low' }` | Bloom + shadows on/off |
| `new-match` | `{}` | Create a match |
| `load-match` | `{ id }` | Switch to an existing match |
| `resign` | `{ side }` | Resign — only fired after the operator confirms |

The HUD updates its own visual state optimistically and re-syncs from the next
`update()`, so ignoring a command it emits is safe.

`resign` is deliberately two-step: the first click arms the button (it reads
`SURE?`), the second fires. It disarms itself after ~3 seconds.

### Keyboard

| Key | Action |
|-----|--------|
| `Space` | Play / pause |
| `→` | Step one ply |
| `1` `2` `3` `4` | Speed 0.5× · 1× · 2× · 4× |
| `C` | Cycle camera presets |
| `Q` | Toggle quality |
| `Esc` | Collapse the expanded ply, disarm resign |

Shortcuts are suppressed while focus is inside an input, select or
contenteditable.

### House moves are flagged, always

Every ply in the log carries its `source`. When `source !== 'model'` — the
arena adjudicated the move, or the model forfeited it — the row gets amber
hazard hatching, an amber left rule, an amber SAN, and a `HOUSE` or `FORFEIT`
badge, and the status line gains a `HOUSE MOVES` chip.

This is not decoration. The arena's ledger treats house moves as an integrity
signal: a game with them in it is not purely a contest between the two models.
The HUD is required to make that impossible to miss. **Do not soften it.**

Clicking any ply expands the model's recorded `thinking` plus a meta line
(latency, attempt count, token split, cost).

### Material advantage

Computed from the **FEN placement field**, not from the captures list —
promotions change material without a capture, so a captures-derived count
drifts. The captures list is still what fills the visual rack, and is used as
a fallback when a payload arrives with no FEN.

### Pointer discipline

The HUD root and every passive panel are `pointer-events: none`, so orbit-drag
works through the rosters and the status line. Only real controls and the two
scrolling feeds take pointer events. Verified: the canvas is hit-testable at
screen centre with the full HUD mounted.

### Responsiveness

Laid out and verified at 1600×900 and at the 1280 contract floor — no panel
overlap, nothing off-screen, no horizontal page scroll. Below 1080px the
two-rail layout stops fitting and the control dock reflows to a stacked column
so the page degrades rather than breaking. `prefers-reduced-motion` disables
every animation.

### Fonts

No `@font-face`, no Google Fonts, no network. The Antonio/Oswald condensed
geometric feel comes from a local stack that resolves to **Bahnschrift** on
Windows 10/11 and falls through Roboto Condensed → Arial Narrow →
Helvetica Neue Condensed → `system-ui`. The Imperium serif resolves through
Trajan/Cinzel → Palatino Linotype → Book Antiqua → Georgia → serif. Mono is
Cascadia → JetBrains Mono → Consolas → `ui-monospace`.

Captured pieces use the Unicode chess glyphs `♔♕♖♗♘♙` / `♚♛♜♝♞♟`, which every
target platform covers.

---

## The coordinate contract

Three.js right-handed, **Y up**. One board square = **1.0 world unit**.

- File `a..h` → `f = 0..7`; rank `1..8` → `r = 0..7`.
- Square centre: **`x = f - 3.5`**, **`y = 0`**, **`z = 3.5 - r`**.
- So **a1 = (-3.5, 0, +3.5)** and **h8 = (+3.5, 0, -3.5)**.
- White home ranks (1, 2) sit at **positive Z**; Black home ranks (7, 8) at
  negative Z.
- The board's top surface is **y = 0**. Pieces stand on it and extend **+Y**.
- The default camera sits behind White (positive Z) looking toward −Z.

`board.js` exports the canonical helpers and **everything else must use them**
rather than recomputing the arithmetic:

```js
squareToWorld('e4')    // -> THREE.Vector3
worldToSquare(vec3)    // -> 'e4' | null
squareToIndices('e4')  // -> { file: 4, rank: 3 }
SQUARE_SIZE            // 1.0
BOARD_HALF             // 4.0
```

---

## The model contract

Two GLB files: `assets/models/federation.glb` and `assets/models/imperium.glb`.
Each contains **exactly six root-level meshes**, named lowercase:

`pawn` · `knight` · `bishop` · `rook` · `queen` · `king`

Every mesh must satisfy:

- **Origin at base centre.** Local `(0,0,0)` is the centre of the footprint;
  geometry extends upward in **+Y**. (GLTF export converts Blender's +Z up.)
- **Front faces −Z**, i.e. toward the enemy for White. `pieces.js` rotates
  Black 180° about Y, so asymmetric pieces — the knight above all — must be
  modelled facing −Z.
- **Footprint radius ≤ 0.40**, so a piece never bleeds into an adjacent square.
- **Heights**, in world units, measured to the tip:

  | piece  | height |
  |--------|--------|
  | pawn   | 0.85   |
  | knight | 1.05   |
  | rook   | 1.00   |
  | bishop | 1.15   |
  | queen  | 1.35   |
  | king   | 1.50   |

- **Two material slots, named exactly `body` and `glow`.** `body` is the
  metal/stone shell (PBR, non-emissive); `glow` is the emissive accent —
  windows, plasma vents, energy rings, eye slits. `pieces.js` overrides both at
  runtime per faction, so Blender only has to get the *slot assignment* right:
  which polygons are body, which are glow.
- **Budget:** aim for ≤ 8k triangles per piece. Apply all modifiers before
  export. No n-gons above 6 sides. Smooth shading with sharp-edge preservation.
- Meshes are **separate objects**, not joined, with **no parent transforms** —
  apply location, rotation and scale before exporting.

### Runtime faction palette

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
    trim:  { color: 0xa8801f },                   // tarnished brass
    glow:  { color: 0xff3b1f, intensity: 2.2 },   // crimson plasma
    rimLight: 0xff6a3d,
  },
};
```

---

## Regenerating the models in Blender

The pieces are **procedural** — `blender/build_pieces.py` generates both
factions from scratch rather than shipping hand-sculpted meshes that nobody can
reproduce. If you change a silhouette, you change the script and re-run it.

### Requirements

Blender 3.6 or newer. Nothing else; the script uses only `bpy` and the standard
mesh operators.

### From the GUI

1. Open Blender with an empty scene.
2. Switch a area to the **Scripting** workspace.
3. **Open** `blender/build_pieces.py`, then **Run Script** (`Alt+P`).
4. The script clears the scene, builds all twelve pieces, assigns the `body`
   and `glow` material slots, applies every modifier and transform, and writes
   `assets/models/federation.glb` and `assets/models/imperium.glb`.

### Headless

```bash
blender --background --python blender/build_pieces.py
```

Run it from the `BattleChess/` directory so the relative output paths resolve.

### After regenerating, check

- Six meshes per file, named exactly `pawn` `knight` `bishop` `rook` `queen`
  `king`, all lowercase, no `.001` suffixes.
- Each piece's origin sits at its base centre, and it stands in +Y.
- Heights match the table above (drop one into the scene and measure).
- The knight faces **−Z**.
- Both material slots exist on every mesh and are named `body` and `glow`.
- Triangle count per piece is under budget.

A mesh that violates any of these will still load, but it will sit wrong, face
wrong, or ignore the faction palette — and the failure will look like a bug in
`pieces.js` when it is not.

---

## Rendering targets

- `WebGLRenderer` with `antialias: true`, `ACESFilmicToneMapping`,
  `toneMappingExposure ≈ 1.1`, `outputColorSpace = SRGBColorSpace`, shadows on
  (`PCFSoftShadowMap`).
- `EffectComposer`: RenderPass → UnrealBloomPass (strength ≈ 0.65, radius 0.5,
  threshold 0.85) → OutputPass. **Bloom is what sells the emissive glow** —
  without it both factions read as flat plastic.
- Low-key, dramatic lighting: a key spot above the board, a cool cyan fill from
  the White side, a warm crimson rim from the Black side, subtle hemisphere
  ambient, and a procedurally generated environment map so the metals actually
  read as metal.
- **60 fps at 1080p on integrated graphics.** Reuse geometry and materials
  across identical pieces to keep draw calls low, cap particles, and degrade
  cleanly: `?quality=low` (or the FX toggle) drops bloom and shadows.
- Handle resize, cap `devicePixelRatio` at 2, and survive WebGL context loss.

---

## Ground rules

1. **Nothing outside `BattleChess/` is modified.** The Forge server API is fixed.
2. **No network calls at runtime.** three.js is vendored, models are local, all
   fonts are system fonts.
3. **ES modules only**, resolved through the importmap in `index.html`.
4. **No build step.** It runs by being served.
5. Every module is a single-responsibility ES module with **named exports**.
6. **The FEN is truth.** A desync self-heals by snapping to it.

`CONTRACT.md` is the authoritative version of all of this. Where this README
and the contract disagree, the contract wins — and the README is the thing
that needs fixing.
