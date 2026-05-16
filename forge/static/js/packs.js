/**
 * Forge UI — Packs Module (Quality Refactor Phase 1)
 *
 * Extracted from the original monolithic app.js as part of the "make it a quality app" goal.
 * This module owns:
 *   - Loading capability packs from the backend (/api/packs)
 *   - Populating the Pack <select> with readiness icons
 *   - Rendering the detailed Pack Readiness Panel (the new quality UI feature)
 *
 * All functions are intentionally left in the global scope during the transition
 * so the rest of the legacy app.js continues to work without changes.
 * Future steps will introduce a Forge namespace + proper init() methods.
 */

async function loadPacks() {
    try {
        state.packs = await fetchJson("/api/packs");
        populatePackSelect();
    } catch (error) {
        // Packs are optional — degrade gracefully
        state.packs = [];
    }
}

function populatePackSelect() {
    if (!els.packSelect || !state.packs) return;

    // Keep the "Auto" option, clear the rest
    els.packSelect.innerHTML = '<option value="">Auto (all tools)</option>';

    const READINESS_ICONS = { ready: "\u2705", degraded: "\u26A0\uFE0F", unavailable: "\u274C" };

    for (const pack of state.packs) {
        const option = document.createElement("option");
        option.value = pack.name;
        const icon = READINESS_ICONS[pack.readiness?.state] || "";
        const label = pack.name.charAt(0).toUpperCase() + pack.name.slice(1);
        option.textContent = `${icon} ${label} — ${pack.description || ""}`.trim();
        if (pack.readiness?.state === "unavailable") {
            option.disabled = true;
        }
        els.packSelect.appendChild(option);
    }

    // Show detailed readiness for the currently selected pack (if any)
    renderPackReadiness(els.packSelect.value);
}

function renderPackReadiness(packName) {
    const panel = document.getElementById("pack-readiness-panel");
    if (!panel) return;

    if (!packName || !state.packs || state.packs.length === 0) {
        panel.style.display = "none";
        panel.innerHTML = "";
        return;
    }

    const pack = state.packs.find(p => p.name === packName);
    if (!pack || !pack.readiness) {
        panel.style.display = "none";
        return;
    }

    const r = pack.readiness;
    const icon = r.state === "ready" ? "✅" : r.state === "degraded" ? "⚠️" : "❌";

    let html = `<div class="readiness-summary">${icon} ${r.state.toUpperCase()} — ${escapeAttr(r.summary || "All systems nominal")}</div>`;

    if (r.checks && r.checks.length > 0) {
        for (const c of r.checks) {
            const cIcon = c.status === "ready" ? "✅" : c.status === "degraded" ? "⚠️" : "❌";
            html += `<div class="check ${c.status}"><span>${cIcon}</span><span class="msg">${escapeAttr(c.name)}: ${escapeAttr(c.message || "")}</span></div>`;
        }
    }

    panel.innerHTML = html;
    panel.style.display = "block";
}

// Future: expose a clean API
// window.Forge = window.Forge || {};
// window.Forge.Packs = { loadPacks, populatePackSelect, renderPackReadiness };
