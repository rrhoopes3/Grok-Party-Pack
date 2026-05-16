# Forge UI — Modular Architecture (Quality Refactor)

**Status:** In progress — Phase 1 complete (core + packs extracted)

This is the new home for the LCARS Command Nexus frontend.

## Philosophy

The original `app.js` grew to ~260 KB of vanilla JS. It worked extremely well but was becoming a maintenance burden. The goal of the "make it a quality app" initiative is to turn the Forge UI into something delightful *and* a joy to work on.

We are doing a careful, low-risk progressive extraction with **no bundler**, **no breaking changes**, and full backward compatibility during the transition.

## Module Loading Order (in index.html)

```html
<script src="/static/js/core.js"></script>      <!-- Foundation: els, state, utils, init orchestrator -->
<script src="/static/js/packs.js"></script>     <!-- Capability Packs + Readiness Panel -->
<script src="/static/js/history.js"></script>   <!-- Task history + inspector -->
<script src="/static/js/memory.js"></script>
<script src="/static/js/runner.js"></script>    <!-- Task submission + SSE streaming -->
<script src="/static/js/trading.js"></script>
<script src="/static/js/keys.js"></script>
<script src="/static/js/arena.js"></script>
<script src="/static/js/chess.js"></script>
<script src="/static/js/nes.js"></script>
<script src="/static/app.js"></script>          <!-- Thin remaining orchestrator + legacy glue -->
<script src="/static/mcp-hub.js"></script>
```

## Current Modules

### core.js (Foundation)
- `els` — massive cached DOM element map (single source of truth)
- `state` — central application state bag
- `fetchJson()` — improved shared HTTP helper
- All formatting utilities (`formatMoney`, `escapeAttr`, `escapeHtml`, `toneMoneyElement`, etc.)
- `defaultRunState`, `resetRunState`, `applyRunState`, `updateControlState`, `updateStatus`
- Base `init()` that will eventually become the beautiful module coordinator
- Tab switching skeleton (the big one in bindEvents)

### packs.js
- `loadPacks()`
- `populatePackSelect()`
- `renderPackReadiness()` — the new high-quality detailed readiness panel

### history.js
- Complete History tab + rich Run Inspector
- `loadHistory`, `renderHistory`, beautiful event timeline, category filtering
- Full `renderRunInspector` with stats, tool usage, widget previews, cost breakdowns
- Legacy fallback view
- Includes small utilities it depends on (`isPlannedTask`, `truncate`) — candidates to move to core later

### (Future modules)
- history + inspector
- runner (the actual task execution loop)
- trading (PCR, positions, orders)
- arena (the glorious gladiatorial system)
- chess, nes (the fun deep features)
- etc.

## Design Decisions

- **Globals during transition**: Everything stays on `window` / global scope. This is the safest path for a large vanilla SPA refactor. Once the split is stable we can introduce a `window.ForgeUI` namespace.
- **No build step**: The Flask static server just serves the files. Order in `<script>` tags is the only "bundler".
- **LCARS personality**: Module comments lean into the starship console aesthetic because this is the Grok *Party* Pack.
- **Progressive enhancement**: Every extraction must leave the app in a working state.

## Next Quality Moves (after full modularization)

- Introduce a tiny event bus for cross-module communication
- Make `state` changes more observable (very light pub/sub)
- Extract individual tab content into more declarative pieces
- Add proper error boundaries + loading states
- Turn the Arena and NES experiences into absolute showpieces

---

**Remember**: This is a *fun* project. The code should eventually feel as premium and playful as the LCARS UI itself.
