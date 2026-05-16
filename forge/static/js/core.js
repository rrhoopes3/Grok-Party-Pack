/**
 * Forge UI — Core Module (The Foundation)
 *
 * This is the beating heart of the LCARS Command Nexus.
 * Everything else builds on top of this.
 *
 * Responsibilities:
 *   - els: The One True Map of every important DOM node (cached at startup)
 *   - state: The single source of truth for application state
 *   - fetchJson: Robust shared HTTP helper with good error messages
 *   - All small pure utilities (money, escaping, formatting, time)
 *   - Run state machine helpers (defaultRunState, reset, apply)
 *   - updateControlState / updateStatus — the central UI state sync
 *   - The main init() orchestrator (will grow beautiful as modules are added)
 *
 * LCARS Design Note:
 *   We treat the browser like a starship bridge. These objects are the
 *   "Master Systems Display". Everything else is a subsystem.
 */

const TECHNICAL_TYPES = new Set([
    "tool-call", "tool-result", "toll", "toll-summary",
    "guardrail", "guardrail-summary", "firewall", "escalation", "token-usage",
]);

// ─────────────────────────────────────────────────────────────────────────────
// THE SACRED DOM CACHE
// Every querySelector in the old code eventually became an entry here.
// This is the single place that must be kept in sync with index.html ids.
// ─────────────────────────────────────────────────────────────────────────────
const els = {
    // Console / Main task area
    messages: document.getElementById("messages"),
    messagesTechnical: document.getElementById("messages-technical"),
    taskInput: document.getElementById("task-input"),
    submitBtn: document.getElementById("submit-btn"),
    killBtn: document.getElementById("kill-btn"),
    status: document.getElementById("status"),

    // Control deck (left rail)
    sandboxToggle: document.getElementById("sandbox-toggle"),
    sandboxPath: document.getElementById("sandbox-path"),
    directToggle: document.getElementById("direct-toggle"),
    agentSlider: document.getElementById("agent-slider"),
    agentCount: document.getElementById("agent-count"),
    agentControl: document.getElementById("agent-control"),
    modelSelect: document.getElementById("model-select"),
    packSelect: document.getElementById("pack-select"),

    // Cost & session
    sessionCost: document.getElementById("session-cost"),
    sessionToll: document.getElementById("session-toll"),
    taskCost: document.getElementById("task-cost"),
    costLimits: document.getElementById("cost-limits"),
    resetCostBtn: document.getElementById("reset-cost-btn"),

    // Feature badges + meta
    featureBadges: document.getElementById("feature-badges"),
    plannerModel: document.getElementById("planner-model"),
    defaultModel: document.getElementById("default-model"),
    maxIterations: document.getElementById("max-iterations"),
    workingDir: document.getElementById("working-dir"),
    workspaceTitle: document.getElementById("workspace-title"),
    workspaceSubtitle: document.getElementById("workspace-subtitle"),
    chatArea: document.getElementById("chat-area"),

    // History + Inspector
    historyList: document.getElementById("history-list"),
    historyDetail: document.getElementById("history-detail"),
    inspectorFilters: document.getElementById("inspector-filters"),
    refreshHistoryBtn: document.getElementById("refresh-history-btn"),

    // Memory / Vault
    memoryList: document.getElementById("memory-list"),
    refreshMemoryBtn: document.getElementById("refresh-memory-btn"),
    clearMemoryBtn: document.getElementById("clear-memory-btn"),

    // Arena
    arenaBtn: document.getElementById("arena-btn"),
    backToForgeBtn: document.getElementById("back-to-forge-btn"),
    arenaSetup: document.getElementById("arena-setup"),
    arenaView: document.getElementById("arena-view"),
    redModel: document.getElementById("red-model"),
    blueModel: document.getElementById("blue-model"),
    ttsToggle: document.getElementById("tts-toggle"),
    arenaGoBtn: document.getElementById("arena-go-btn"),
    arenaCancelBtn: document.getElementById("arena-cancel-btn"),
    commentaryText: document.getElementById("commentary-text"),
    roundLabel: document.getElementById("round-label"),
    redLog: document.getElementById("red-log"),
    blueLog: document.getElementById("blue-log"),
    scoreRed: document.getElementById("score-red"),
    scoreBlue: document.getElementById("score-blue"),
    scoreRedNum: document.getElementById("score-red-num"),
    scoreBlueNum: document.getElementById("score-blue-num"),

    // Runtime / Meta
    runtimeEvents: document.getElementById("runtime-events"),
    accountabilityList: document.getElementById("accountability-list"),
    verificationList: document.getElementById("verification-list"),
    metaTaskId: document.getElementById("meta-task-id"),
    metaMode: document.getElementById("meta-mode"),
    metaStep: document.getElementById("meta-step"),
    metaDelegatee: document.getElementById("meta-delegatee"),
    metaModel: document.getElementById("meta-model"),
    metaLatency: document.getElementById("meta-latency"),
    metaTrust: document.getElementById("meta-trust"),
    metaGuardrails: document.getElementById("meta-guardrails"),
    metaFirewall: document.getElementById("meta-firewall"),
    metaTokens: document.getElementById("meta-tokens"),
    metaHops: document.getElementById("meta-hops"),
};

// ─────────────────────────────────────────────────────────────────────────────
// THE CENTRAL STATE BAG
// Not reactive (yet). Everything reads and writes here.
// Modules are expected to keep their own sub-state when it makes sense.
// ─────────────────────────────────────────────────────────────────────────────
const state = {
    config: null,
    models: [],
    packs: [],
    history: [],
    memories: [],
    selectedHistoryId: null,
    currentTaskId: null,
    isRunning: false,
    isArenaMode: false,
    isCollabMode: false,
    currentTaskCostUsd: 0,
    sessionCostUsd: 0,
    sessionTollUsd: 0,
    runtimeEvents: [],
    run: {},
    ttsEnabled: false,
    ttsBuffer: "",
    ttsVoice: null,
};

// ─────────────────────────────────────────────────────────────────────────────
// RUN STATE HELPERS
// These power the beautiful "Runtime" tab and the meta readouts.
// ─────────────────────────────────────────────────────────────────────────────
function defaultRunState(mode = "Planner") {
    return {
        taskId: "-",
        mode,
        step: "-",
        delegatee: "-",
        model: "-",
        latency: "-",
        trust: "-",
        guardrails: "0",
        firewall: "0",
        tokens: 0,
        hops: "-",
        verification: ["No active step."],
        accountability: null,
    };
}

function resetRunState(mode = modeFromControls()) {
    state.run = defaultRunState(mode);
    applyRunState();
}

function applyRunState() {
    const r = state.run || {};
    if (els.metaTaskId) els.metaTaskId.textContent = r.taskId || "-";
    if (els.metaMode) els.metaMode.textContent = r.mode || "-";
    if (els.metaStep) els.metaStep.textContent = r.step || "-";
    if (els.metaDelegatee) els.metaDelegatee.textContent = r.delegatee || "-";
    if (els.metaModel) els.metaModel.textContent = r.model || "-";
    if (els.metaLatency) els.metaLatency.textContent = r.latency || "-";
    if (els.metaTrust) els.metaTrust.textContent = r.trust || "-";
    if (els.metaGuardrails) els.metaGuardrails.textContent = r.guardrails || "0";
    if (els.metaFirewall) els.metaFirewall.textContent = r.firewall || "0";
    if (els.metaTokens) els.metaTokens.textContent = r.tokens || "0";
    if (els.metaHops) els.metaHops.textContent = r.hops || "-";

    if (els.verificationList) {
        els.verificationList.innerHTML = "";
        (r.verification || []).forEach(v => {
            const li = document.createElement("li");
            li.textContent = v;
            els.verificationList.appendChild(li);
        });
    }
    if (els.accountabilityList) {
        els.accountabilityList.innerHTML = "";
        if (r.accountability) {
            const li = document.createElement("li");
            li.textContent = r.accountability;
            els.accountabilityList.appendChild(li);
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// CONTROL SURFACE + STATUS
// These keep the left rail and top status bar in sync with reality.
// ─────────────────────────────────────────────────────────────────────────────
function updateControlState() {
    const controlsDisabled = state.isRunning;
    els.sandboxToggle.disabled = controlsDisabled;
    els.sandboxPath.disabled = controlsDisabled;
    els.directToggle.disabled = controlsDisabled;
    els.agentSlider.disabled = controlsDisabled || els.directToggle.checked;
    els.modelSelect.disabled = controlsDisabled;
    els.packSelect.disabled = controlsDisabled;
    els.arenaBtn.disabled = controlsDisabled;
    els.submitBtn.disabled = controlsDisabled;

    els.killBtn.classList.toggle("hidden", !state.isRunning);
    els.killBtn.disabled = !state.isRunning;
}

function updateStatus(text, running = false) {
    if (els.status) {
        els.status.textContent = text;
        els.status.classList.toggle("running", running);
    }
}

function modeFromControls() {
    if (els.directToggle?.checked) return "Direct";
    const agents = els.agentSlider ? els.agentSlider.value : "16";
    return `Planner (${agents})`;
}

function applyWorkspaceMode() {
    const mode = els.directToggle?.checked ? "Direct" : "Planner";
    if (els.workspaceTitle) els.workspaceTitle.textContent = mode + " Mode";
    if (els.workspaceSubtitle) {
        els.workspaceSubtitle.textContent = els.directToggle?.checked
            ? "Executor receives task immediately"
            : "16-agent research council plans, then executor acts";
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// MONEY & FORMATTING UTILITIES
// Used everywhere (cost ticker, trading, history, etc.)
// ─────────────────────────────────────────────────────────────────────────────
function formatMoney(value, decimals = 2) {
    const n = Number(value) || 0;
    return "$" + n.toFixed(decimals);
}

function formatCompactMoney(value) {
    const n = Number(value) || 0;
    if (n >= 1000) return "$" + (n / 1000).toFixed(1) + "k";
    return "$" + n.toFixed(2);
}

function toneMoneyElement(element, value, limit) {
    if (!element) return;
    const v = Number(value) || 0;
    const l = Number(limit) || 50;
    element.classList.remove("warn", "danger");
    if (v > l * 0.8) element.classList.add("danger");
    else if (v > l * 0.5) element.classList.add("warn");
}

function formatTimestamp(value) {
    if (!value) return "—";
    const d = new Date(value * 1000);
    return d.toLocaleString();
}

function formatCompact(n) {
    n = Number(n) || 0;
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 10_000) return Math.round(n / 1000) + "k";
    if (n >= 1000) return (n / 1000).toFixed(1) + "k";
    return n.toString();
}

function escapeAttr(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// ─────────────────────────────────────────────────────────────────────────────
// SHARED HTTP LAYER
// All modules should use this instead of raw fetch when possible.
// Small quality improvement: better error surfacing.
// ─────────────────────────────────────────────────────────────────────────────
async function fetchJson(url, options = {}) {
    const res = await fetch(url, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
    });

    if (!res.ok) {
        let detail = "";
        try {
            const j = await res.json();
            detail = j.error || j.detail || JSON.stringify(j);
        } catch (_) {
            detail = await res.text();
        }
        const err = new Error(`HTTP ${res.status} ${res.statusText} — ${detail}`);
        err.status = res.status;
        throw err;
    }

    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
        return res.json();
    }
    return res.text();
}

// ─────────────────────────────────────────────────────────────────────────────
// BASE EVENT BINDING (the parts that are truly core)
// The giant tab-switching listener and many feature-specific binds
// will be gradually moved into their modules.
// ─────────────────────────────────────────────────────────────────────────────
function bindBaseEvents() {
    // Core control deck
    els.sandboxToggle?.addEventListener("change", () => {
        localStorage.setItem("forge_sandbox_mode", String(els.sandboxToggle.checked));
        updateControlState();
    });

    els.sandboxPath?.addEventListener("input", () => {
        localStorage.setItem("forge_sandbox_path", els.sandboxPath.value);
    });

    els.directToggle?.addEventListener("change", () => {
        localStorage.setItem("forge_direct_mode", String(els.directToggle.checked));
        state.run.mode = modeFromControls();
        applyRunState();
        updateControlState();
        applyWorkspaceMode();
    });

    els.agentSlider?.addEventListener("input", () => {
        els.agentCount.textContent = els.agentSlider.value;
        localStorage.setItem("forge_agent_count", els.agentSlider.value);
    });

    els.modelSelect?.addEventListener("change", () => {
        localStorage.setItem("forge_executor_model", els.modelSelect.value);
    });

    // Pack change is handled in packs.js module (renderPackReadiness)
    els.packSelect?.addEventListener("change", () => {
        localStorage.setItem("forge_pack", els.packSelect.value);
        // renderPackReadiness is defined in packs.js and attached to window
        if (typeof renderPackReadiness === "function") {
            renderPackReadiness(els.packSelect.value);
        }
    });

    els.submitBtn?.addEventListener("click", () => {
        if (typeof submitTask === "function") submitTask();
    });
    els.killBtn?.addEventListener("click", () => {
        if (typeof killTask === "function") killTask();
    });

    els.taskInput?.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            if (typeof submitTask === "function") submitTask();
        }
    });

    els.resetCostBtn?.addEventListener("click", () => {
        if (typeof resetCosts === "function") resetCosts();
    });

    els.refreshHistoryBtn?.addEventListener("click", () => {
        if (typeof loadHistory === "function") loadHistory();
    });
    els.refreshMemoryBtn?.addEventListener("click", () => {
        if (typeof loadMemory === "function") loadMemory();
    });
    els.clearMemoryBtn?.addEventListener("click", () => {
        if (typeof clearMemory === "function") clearMemory();
    });

    // Arena basic
    els.arenaBtn?.addEventListener("click", () => {
        if (typeof openArenaSetup === "function") openArenaSetup();
    });
    els.backToForgeBtn?.addEventListener("click", () => {
        if (typeof switchToConsole === "function") switchToConsole();
    });
    els.arenaCancelBtn?.addEventListener("click", () => {
        els.arenaSetup?.classList.add("hidden");
    });
    els.arenaGoBtn?.addEventListener("click", () => {
        if (typeof startArena === "function") startArena();
    });

    const scenarioDropdown = document.getElementById("arena-scenario");
    if (scenarioDropdown && typeof updateArenaSetupCopy === "function") {
        scenarioDropdown.addEventListener("change", updateArenaSetupCopy);
    }

    // Global tab bar (this one is intentionally kept in core for now
    // because it is the central router for almost every module)
    const tabBar = document.getElementById("tab-bar");
    if (tabBar) {
        tabBar.addEventListener("click", (e) => {
            const btn = e.target.closest(".tab-btn");
            if (!btn) return;
            const tab = btn.dataset.tab;

            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-content").forEach(p => p.classList.remove("active"));
            btn.classList.add("active");

            const panel = document.getElementById(`tab-${tab}`);
            if (panel) panel.classList.add("active");

            // Auto-refresh / lazy init for each tab — modules register their functions globally
            if (tab === "history" && typeof loadHistory === "function") loadHistory();
            if (tab === "memory" && typeof loadMemory === "function") loadMemory();
            if (tab === "trading" && typeof initTrading === "function") initTrading();
            if (tab === "keys" && typeof loadKeys === "function") loadKeys();
            if (tab === "chess" && typeof chessPopulateModelSelects === "function") {
                chessPopulateModelSelects();
                if (typeof renderChess === "function") renderChess();
            }
            if (tab === "nes" && typeof nesLoadRomList === "function") {
                nesLoadRomList();
                if (typeof nesPopulateCoachModels === "function") nesPopulateCoachModels();
                if (typeof nesRefreshEvents === "function") nesRefreshEvents();
            }
            // prophecy + surgeon are still in the monolith for now
            if (tab === "prophecy" && typeof loadProphecyList === "function") loadProphecyList();
            if (tab === "surgeon" && typeof loadSurgeonOps === "function") loadSurgeonOps();
        });
    }

    // Many more specific binds (prophecy, surgeon, chess, nes, keys)
    // are still attached inside bindEvents() in the shrinking app.js.
    // They will be migrated module-by-module.
}

// ─────────────────────────────────────────────────────────────────────────────
// THE MAIN INIT ORCHESTRATOR
// This will become increasingly elegant as more modules are extracted.
// Right now it still leans on global functions that live in app.js or other modules.
// ─────────────────────────────────────────────────────────────────────────────
async function init() {
    // Base wiring that must happen first
    bindBaseEvents();
    resetRunState();
    if (typeof initTTS === "function") initTTS();

    // Core data
    await (typeof loadConfig === "function" ? loadConfig() : Promise.resolve());
    await (typeof loadModels === "function" ? loadModels() : Promise.resolve());
    await (typeof loadPacks === "function" ? loadPacks() : Promise.resolve());

    if (typeof restoreSettings === "function") restoreSettings();
    updateControlState();
    applyWorkspaceMode();

    // Parallel nice-to-haves
    const parallel = [];
    if (typeof loadSessionCost === "function") parallel.push(loadSessionCost());
    if (typeof loadHistory === "function") parallel.push(loadHistory());
    if (typeof loadMemory === "function") parallel.push(loadMemory());
    if (typeof loadTradingConfig === "function") parallel.push(loadTradingConfig());
    await Promise.all(parallel);

    // Final status
    updateStatus("Ready");
}

// Make some things easy to inspect from the console (fun + debug)
window.ForgeUI = window.ForgeUI || {};
window.ForgeUI.els = els;
window.ForgeUI.state = state;
window.ForgeUI.init = init;

// Auto-start when the script has loaded and DOM is ready
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    // In case the script is loaded after DOMContentLoaded
    setTimeout(init, 0);
}
