/**
 * Forge UI — API Keys vault tab
 */

// ══════════════════════════════════════════════════════════════════════
// API Keys tab — read/write provider secrets via /api/keys
// ══════════════════════════════════════════════════════════════════════
// The server returns only {set, last4, length, masked} — raw values never
// touch the client on load. When the user types a new value we POST it
// back; the server persists to forge/.env and hot-patches the running
// process.

const keysState = {
    providers: [],
    category: "all",
    editing: null, // provider id currently being edited
};

function keysSetStatus(msg, kind = "") {
    const el = document.getElementById("keys-status");
    if (!el) return;
    el.textContent = msg || "";
    el.className = "keys-status" + (kind ? " " + kind : "");
    if (msg && kind === "ok") {
        setTimeout(() => { if (el.textContent === msg) keysSetStatus(""); }, 3000);
    }
}

async function loadKeys() {
    const listEl = document.getElementById("keys-list");
    if (!listEl) return;
    listEl.innerHTML = `<div class="keys-loading">Loading providers…</div>`;
    try {
        const resp = await fetchJson("/api/keys");
        keysState.providers = Array.isArray(resp?.providers) ? resp.providers : [];
        renderKeys();
    } catch (err) {
        listEl.innerHTML = `<div class="keys-empty">Failed to load keys: ${escapeHtml(err.message)}</div>`;
    }
}

function renderKeys() {
    const listEl = document.getElementById("keys-list");
    if (!listEl) return;

    const cat = keysState.category;
    const rows = keysState.providers.filter(p => cat === "all" || p.category === cat);

    if (!rows.length) {
        listEl.innerHTML = `<div class="keys-empty">No providers in this category.</div>`;
        return;
    }

    listEl.innerHTML = "";
    rows.forEach(p => listEl.appendChild(buildKeyRow(p)));
}

function buildKeyRow(p) {
    const row = document.createElement("div");
    row.className = "key-row";
    row.dataset.provider = p.id;
    row.dataset.set = p.set ? "true" : "false";

    const docs = p.docs_url
        ? ` <a href="${escapeHtml(p.docs_url)}" target="_blank" rel="noopener">docs</a>`
        : "";

    const maskedDisplay = p.set
        ? `<span class="key-masked">${escapeHtml(p.masked)}</span>
           <span class="key-badge set">SET</span>`
        : `<span class="key-masked empty">not configured</span>
           <span class="key-badge empty">EMPTY</span>`;

    row.innerHTML = `
        <div class="key-meta">
            <span class="key-label">${escapeHtml(p.label)}</span>
            <span class="key-envvar">${escapeHtml(p.env_var)}${docs}</span>
        </div>
        <div class="key-value">${maskedDisplay}</div>
        <div class="key-actions">
            <button class="key-btn update" data-action="update">${p.set ? "Replace" : "Add"}</button>
            <button class="key-btn clear"  data-action="clear"${p.set ? "" : " disabled"}>Clear</button>
        </div>
    `;

    row.addEventListener("click", onKeyRowClick);

    // If this row was being edited, re-expand the editor.
    if (keysState.editing === p.id) {
        expandKeyEditor(row, p);
    }

    return row;
}

function onKeyRowClick(e) {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const row = e.currentTarget;
    const providerId = row.dataset.provider;
    const provider = keysState.providers.find(p => p.id === providerId);
    if (!provider) return;

    const action = btn.dataset.action;
    if (action === "update")  toggleKeyEditor(row, provider);
    if (action === "clear")   confirmClearKey(provider);
    if (action === "save")    submitKeySave(row, provider);
    if (action === "cancel")  collapseKeyEditor(row, provider);
}

function toggleKeyEditor(row, provider) {
    const existing = row.querySelector(".key-edit");
    if (existing) {
        collapseKeyEditor(row, provider);
        return;
    }
    expandKeyEditor(row, provider);
}

function expandKeyEditor(row, provider) {
    keysState.editing = provider.id;
    if (row.querySelector(".key-edit")) return;

    const form = document.createElement("div");
    form.className = "key-edit";
    form.innerHTML = `
        <input type="password"
               class="key-input"
               placeholder="${escapeHtml(provider.hint || 'Paste key…')}"
               autocomplete="off"
               spellcheck="false">
        <label class="key-reveal">
            <input type="checkbox" class="key-reveal-toggle"> show
        </label>
        <button class="key-btn save"   data-action="save">Save</button>
        <button class="key-btn cancel" data-action="cancel">Cancel</button>
    `;
    row.appendChild(form);

    const input = form.querySelector(".key-input");
    const reveal = form.querySelector(".key-reveal-toggle");
    reveal.addEventListener("change", () => {
        input.type = reveal.checked ? "text" : "password";
    });
    input.focus();
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); submitKeySave(row, provider); }
        if (e.key === "Escape") { e.preventDefault(); collapseKeyEditor(row, provider); }
    });
}

function collapseKeyEditor(row, provider) {
    const form = row.querySelector(".key-edit");
    if (form) form.remove();
    if (keysState.editing === provider.id) keysState.editing = null;
}

async function submitKeySave(row, provider) {
    const input = row.querySelector(".key-input");
    if (!input) return;
    const value = input.value;
    if (!value) {
        keysSetStatus("Enter a value or use Clear.", "err");
        return;
    }
    keysSetStatus(`Saving ${provider.env_var}…`);
    try {
        const record = await fetchJson(`/api/keys/${encodeURIComponent(provider.id)}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ value }),
        });
        if (record.error) {
            keysSetStatus(record.error, "err");
            return;
        }
        // Merge updated record into state
        const idx = keysState.providers.findIndex(p => p.id === provider.id);
        if (idx >= 0) keysState.providers[idx] = record;
        keysState.editing = null;
        renderKeys();
        keysSetStatus(`${provider.label} updated. Last 4: ${record.last4 || "—"}`, "ok");
    } catch (err) {
        keysSetStatus(`Save failed: ${err.message}`, "err");
    }
}

async function confirmClearKey(provider) {
    if (!confirm(`Clear ${provider.label}? This removes ${provider.env_var} from forge/.env and the running process.`)) return;
    keysSetStatus(`Clearing ${provider.env_var}…`);
    try {
        const record = await fetchJson(`/api/keys/${encodeURIComponent(provider.id)}`, {
            method: "DELETE",
        });
        if (record.error) {
            keysSetStatus(record.error, "err");
            return;
        }
        const idx = keysState.providers.findIndex(p => p.id === provider.id);
        if (idx >= 0) keysState.providers[idx] = record;
        keysState.editing = null;
        renderKeys();
        keysSetStatus(`${provider.label} cleared.`, "ok");
    } catch (err) {
        keysSetStatus(`Clear failed: ${err.message}`, "err");
    }
}

function bindKeysUi() {
    const refreshBtn = document.getElementById("keys-refresh-btn");
    if (refreshBtn) refreshBtn.addEventListener("click", loadKeys);

    document.querySelectorAll(".keys-filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".keys-filter-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            keysState.category = btn.dataset.cat || "all";
            renderKeys();
        });
    });
}

