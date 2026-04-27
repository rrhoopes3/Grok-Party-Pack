const TECHNICAL_TYPES = new Set([
    "tool-call", "tool-result", "toll", "toll-summary",
    "guardrail", "guardrail-summary", "firewall", "escalation", "token-usage",
]);

const els = {
    messages: document.getElementById("messages"),
    messagesTechnical: document.getElementById("messages-technical"),
    taskInput: document.getElementById("task-input"),
    submitBtn: document.getElementById("submit-btn"),
    killBtn: document.getElementById("kill-btn"),
    status: document.getElementById("status"),
    sandboxToggle: document.getElementById("sandbox-toggle"),
    sandboxPath: document.getElementById("sandbox-path"),
    directToggle: document.getElementById("direct-toggle"),
    agentSlider: document.getElementById("agent-slider"),
    agentCount: document.getElementById("agent-count"),
    agentControl: document.getElementById("agent-control"),
    modelSelect: document.getElementById("model-select"),
    packSelect: document.getElementById("pack-select"),
    arenaBtn: document.getElementById("arena-btn"),
    backToForgeBtn: document.getElementById("back-to-forge-btn"),
    resetCostBtn: document.getElementById("reset-cost-btn"),
    refreshHistoryBtn: document.getElementById("refresh-history-btn"),
    refreshMemoryBtn: document.getElementById("refresh-memory-btn"),
    clearMemoryBtn: document.getElementById("clear-memory-btn"),
    featureBadges: document.getElementById("feature-badges"),
    historyList: document.getElementById("history-list"),
    historyDetail: document.getElementById("history-detail"),
    inspectorFilters: document.getElementById("inspector-filters"),
    memoryList: document.getElementById("memory-list"),
    sessionCost: document.getElementById("session-cost"),
    sessionToll: document.getElementById("session-toll"),
    taskCost: document.getElementById("task-cost"),
    costLimits: document.getElementById("cost-limits"),
    plannerModel: document.getElementById("planner-model"),
    defaultModel: document.getElementById("default-model"),
    maxIterations: document.getElementById("max-iterations"),
    workingDir: document.getElementById("working-dir"),
    workspaceTitle: document.getElementById("workspace-title"),
    workspaceSubtitle: document.getElementById("workspace-subtitle"),
    chatArea: document.getElementById("chat-area"),
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

function bindEvents() {
    els.sandboxToggle.addEventListener("change", () => {
        localStorage.setItem("forge_sandbox_mode", String(els.sandboxToggle.checked));
        updateControlState();
    });

    els.sandboxPath.addEventListener("input", () => {
        localStorage.setItem("forge_sandbox_path", els.sandboxPath.value);
    });

    els.directToggle.addEventListener("change", () => {
        localStorage.setItem("forge_direct_mode", String(els.directToggle.checked));
        state.run.mode = modeFromControls();
        applyRunState();
        updateControlState();
    });

    els.agentSlider.addEventListener("input", () => {
        els.agentCount.textContent = els.agentSlider.value;
        localStorage.setItem("forge_agent_count", els.agentSlider.value);
    });

    els.modelSelect.addEventListener("change", () => {
        localStorage.setItem("forge_executor_model", els.modelSelect.value);
    });

    els.packSelect.addEventListener("change", () => {
        localStorage.setItem("forge_pack", els.packSelect.value);
    });

    els.submitBtn.addEventListener("click", submitTask);
    els.killBtn.addEventListener("click", killTask);

    els.taskInput.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            submitTask();
        }
    });

    els.resetCostBtn.addEventListener("click", resetCosts);
    els.refreshHistoryBtn.addEventListener("click", () => loadHistory());
    els.refreshMemoryBtn.addEventListener("click", () => loadMemory());
    els.clearMemoryBtn.addEventListener("click", clearMemory);

    els.arenaBtn.addEventListener("click", openArenaSetup);
    els.backToForgeBtn.addEventListener("click", switchToConsole);
    els.arenaCancelBtn.addEventListener("click", () => {
        els.arenaSetup.classList.add("hidden");
    });
    els.arenaGoBtn.addEventListener("click", startArena);
    const scenarioDropdown = document.getElementById("arena-scenario");
    if (scenarioDropdown) {
        scenarioDropdown.addEventListener("change", updateArenaSetupCopy);
    }

    // Tab switching
    document.getElementById("tab-bar").addEventListener("click", (e) => {
        const btn = e.target.closest(".tab-btn");
        if (!btn) return;
        const tab = btn.dataset.tab;
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach(p => p.classList.remove("active"));
        btn.classList.add("active");
        const panel = document.getElementById(`tab-${tab}`);
        if (panel) panel.classList.add("active");
        // Auto-refresh data when switching to a tab
        if (tab === "history") loadHistory();
        if (tab === "memory") loadMemory();
        if (tab === "trading") initTrading();
        if (tab === "prophecy") loadProphecyList();
        if (tab === "surgeon") loadSurgeonOps();
        if (tab === "keys") loadKeys();
        if (tab === "chess") { chessPopulateModelSelects(); renderChess(); }
        if (tab === "nes")   { nesLoadRomList(); nesPopulateCoachModels(); nesRefreshEvents(); }
    });

    // Prophecy events
    const prophecyCreateBtn = document.getElementById("prophecy-create-btn");
    const prophecyFullBtn = document.getElementById("prophecy-full-btn");
    const prophecyRefreshBtn = document.getElementById("prophecy-refresh-btn");
    const prophecyRunBtn = document.getElementById("prophecy-run-btn");
    const prophecyReportBtn = document.getElementById("prophecy-report-btn");
    const prophecyInterviewBtn = document.getElementById("prophecy-interview-btn");
    if (prophecyCreateBtn) prophecyCreateBtn.addEventListener("click", prophecyCreate);
    if (prophecyFullBtn) prophecyFullBtn.addEventListener("click", prophecyRunFull);
    const deliberationSelect = document.getElementById("prophecy-deliberation-mode");
    if (deliberationSelect) deliberationSelect.addEventListener("change", () => {
        const hint = document.getElementById("deliberation-mode-hint");
        if (hint) hint.textContent = deliberationSelect.value === "independent"
            ? "Each prophet gets its own LLM call in parallel. Richer output, higher cost."
            : "One LLM call simulates all prophets. Fast and cheap.";
    });
    if (prophecyRefreshBtn) prophecyRefreshBtn.addEventListener("click", loadProphecyList);
    if (prophecyRunBtn) prophecyRunBtn.addEventListener("click", prophecyRunRounds);
    if (prophecyReportBtn) prophecyReportBtn.addEventListener("click", prophecyGetReport);
    if (prophecyInterviewBtn) prophecyInterviewBtn.addEventListener("click", prophecyInterview);

    // Surgeon events
    const surgeonCheckBtn = document.getElementById("surgeon-check-btn");
    const surgeonScanBtn = document.getElementById("surgeon-scan-btn");
    const surgeonOperateBtn = document.getElementById("surgeon-operate-btn");
    const surgeonMethodsBtn = document.getElementById("surgeon-methods-btn");
    const surgeonOpsRefreshBtn = document.getElementById("surgeon-ops-refresh-btn");
    if (surgeonCheckBtn) surgeonCheckBtn.addEventListener("click", surgeonCheckDeps);
    if (surgeonScanBtn) surgeonScanBtn.addEventListener("click", surgeonScan);
    if (surgeonOperateBtn) surgeonOperateBtn.addEventListener("click", surgeonOperate);
    if (surgeonMethodsBtn) surgeonMethodsBtn.addEventListener("click", surgeonLoadMethods);
    if (surgeonOpsRefreshBtn) surgeonOpsRefreshBtn.addEventListener("click", loadSurgeonOps);

    // Keys vault events (refresh button + category filter pills)
    bindKeysUi();

    // Chess arena events (new / step / auto / resign)
    bindChessUi();

    // NES Arena events (rom select / boot / pause / reset / coach / note)
    bindNesUi();
}

async function init() {
    bindEvents();
    resetRunState();
    initTTS();

    await loadConfig();
    await loadModels();
    await loadPacks();
    restoreSettings();
    updateControlState();
    applyWorkspaceMode();

    await Promise.all([loadSessionCost(), loadHistory(), loadMemory(), loadTradingConfig()]);
}

async function fetchJson(url, options = {}) {
    // Inject BYOK API keys from localStorage (public demo mode)
    const byokMap = {
        forge_key_xai:       'X-Forge-XAI-Key',
        forge_key_openai:    'X-Forge-OpenAI-Key',
        forge_key_anthropic: 'X-Forge-Anthropic-Key',
        forge_key_github:    'X-Forge-GitHub-Token',
    };
    const extra = {};
    for (const [storageKey, headerName] of Object.entries(byokMap)) {
        const val = localStorage.getItem(storageKey);
        if (val) extra[headerName] = val;
    }
    options.headers = Object.assign({}, extra, options.headers || {});

    const response = await fetch(url, options);
    return response.json();
}

async function loadConfig() {
    try {
        state.config = await fetchJson("/api/config");
        applyConfig();
    } catch (error) {
        addMessage("error", `Failed to load config: ${error.message}`);
    }
}

function applyConfig() {
    if (!state.config) return;

    const savedSandboxPath = localStorage.getItem("forge_sandbox_path");
    els.sandboxPath.value = savedSandboxPath || state.config.default_sandbox_path || "";

    els.plannerModel.textContent = shortModelName(state.config.defaults?.planner_model || "-");
    els.defaultModel.textContent = shortModelName(state.config.defaults?.executor_model || "-");
    els.maxIterations.textContent = String(state.config.defaults?.max_iterations || "-");
    els.workingDir.textContent = truncateMiddle(state.config.runtime?.working_dir || "-", 36);

    const taskLimit = state.config.limits?.task_cost_usd || 0;
    const sessionLimit = state.config.limits?.session_cost_usd || 0;
    els.costLimits.textContent = `${formatMoney(taskLimit)} / ${formatMoney(sessionLimit)}`;

    renderFeatureBadges();
}

function renderFeatureBadges() {
    if (!state.config) return;

    const features = state.config.features || {};
    const badges = [
        { enabled: true, label: "Planner" },
        { enabled: features.memory, label: "Memory" },
        { enabled: features.arena, label: "Arena" },
        { enabled: features.toll, label: "Toll" },
        { enabled: features.marketplace, label: "Marketplace" },
        { enabled: features.email_agent, label: "Email Agent" },
        { enabled: features.solana_watcher, label: "Solana Watcher" },
        { enabled: features.generative_ui, label: "Generative UI" },
        { enabled: features.trading, label: "Trading" },
    ].filter((item) => item.enabled);

    if (features.email_agent && state.config.runtime?.email_agent_model) {
        badges.push({
            enabled: true,
            label: `Email:${shortModelName(state.config.runtime.email_agent_model)}`,
        });
    }

    els.featureBadges.innerHTML = badges
        .map((badge) => `<span class="feature-badge">${escapeHtml(badge.label)}</span>`)
        .join("");

    // Public mode: show key management link
    if (features.public_mode) {
        const keyCount = ['forge_key_xai','forge_key_openai','forge_key_anthropic','forge_key_github']
            .filter(k => localStorage.getItem(k)).length;
        els.featureBadges.innerHTML +=
            `<a href="/setup" class="feature-badge" style="background:#1a3a1a;color:#4ade80;text-decoration:none;cursor:pointer">`
            + `BYOK (${keyCount} key${keyCount !== 1 ? 's' : ''})</a>`;
    }
}

// ── Trading Config Panel ─────────────────────────────────────────────────

async function loadTradingConfig() {
    try {
        const data = await fetchJson("/api/trading/config");
        state.tradingConfig = data;
        renderTradingConfig(data);
    } catch (error) {
        const panel = document.getElementById("trading-config-panel");
        if (panel) panel.innerHTML = `<div class="trading-loading">Trading module not available</div>`;
    }
}

async function switchTradingProvider(provider) {
    try {
        const result = await fetchJson("/api/trading/provider", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider }),
        });
        if (result.error) {
            addMessage("error", `Provider switch failed: ${result.error}`);
            return;
        }
        addMessage("status", `Trading provider switched to: ${provider}`);
        await loadTradingConfig();  // refresh the panel
    } catch (error) {
        addMessage("error", `Provider switch failed: ${error.message}`);
    }
}

function renderTradingConfig(cfg) {
    const panel = document.getElementById("trading-config-panel");
    const badge = document.getElementById("trading-active-badge");
    const switcherRow = document.getElementById("trading-switcher-row");
    if (!panel || !badge) return;

    const active = cfg.default_provider || "yfinance";
    const paperMode = cfg.paper_mode;

    // Update header badge
    badge.className = `trading-active-badge active-${active}`;
    const activeProvider = cfg.providers?.[active];
    badge.textContent = activeProvider
        ? `${activeProvider.label}${paperMode ? " (Paper)" : ""}`
        : `${active}${paperMode ? " (Paper)" : ""}`;

    // ── Provider switcher buttons ──
    if (switcherRow) {
        const switcherDefs = [
            { key: "robinhood",       short: "RH Legacy",    sub: "Stocks + Options + Crypto" },
            { key: "robinhood-crypto", short: "RH Crypto API", sub: "Crypto Only" },
            { key: "tradier",         short: "Tradier",      sub: "Stocks + Options" },
            { key: "yfinance",        short: "Yahoo",        sub: "Free / Delayed" },
        ];
        switcherRow.innerHTML = switcherDefs.map((s) => {
            const p = cfg.providers?.[s.key];
            const isActive = s.key === active;
            const isConfigured = p?.configured;
            const cls = isActive ? "sw-active" : !isConfigured ? "sw-disabled" : "";
            return `<button class="trading-switch-btn ${cls}"
                data-provider="${s.key}" ${!isConfigured && !isActive ? "disabled" : ""}>
                ${escapeHtml(s.short)}
                <span class="sw-sub">${escapeHtml(s.sub)}</span>
            </button>`;
        }).join("");

        // Bind click handlers
        switcherRow.querySelectorAll(".trading-switch-btn:not(.sw-disabled)").forEach((btn) => {
            btn.addEventListener("click", () => {
                const prov = btn.dataset.provider;
                if (prov && prov !== active) switchTradingProvider(prov);
            });
        });
    }

    // ── Provider detail cards ──
    const providerOrder = ["yfinance", "tradier", "robinhood", "robinhood-crypto"];
    const html = providerOrder.map((key) => {
        const p = cfg.providers?.[key];
        if (!p) return "";

        const isActive = key === active;
        const isConfigured = p.configured;
        const cardClass = isActive ? "is-active" : isConfigured ? "is-configured" : "is-unconfigured";

        const statusClass = isActive ? "status-active" : isConfigured ? "status-ready" : "status-missing";
        const statusText = isActive ? "ACTIVE" : isConfigured ? "Ready" : "Not Configured";

        const caps = p.capabilities || {};
        const capHtml = ["stocks", "options", "crypto"].map((asset) => {
            const c = caps[asset] || {};
            const quoteIcon = c.quotes
                ? `<span class="trading-cap-icon cap-yes">Quotes</span>`
                : `<span class="trading-cap-icon cap-no">Quotes</span>`;
            const tradeIcon = c.trade
                ? `<span class="trading-cap-icon cap-yes">Trade</span>`
                : `<span class="trading-cap-icon cap-no">Trade</span>`;
            return `<div class="trading-cap">
                <span class="trading-cap-label">${asset}</span>
                <div class="trading-cap-icons">${quoteIcon}${tradeIcon}</div>
            </div>`;
        }).join("");

        const envVars = (p.env_vars || []).join(", ");
        const authLine = p.auth !== "none"
            ? `<div class="trading-provider-auth">
                Auth: ${escapeHtml(p.auth)}
                ${envVars ? `<span class="trading-provider-envvars">${escapeHtml(envVars)}</span>` : ""}
               </div>`
            : "";

        return `<div class="trading-provider-card ${cardClass}">
            <div class="trading-provider-header">
                <span class="trading-provider-name">${escapeHtml(p.label)}</span>
                <span class="trading-provider-mode">${escapeHtml(p.data_quality || "")}</span>
                <span class="trading-provider-status ${statusClass}">${statusText}</span>
            </div>
            <div class="trading-caps-grid">${capHtml}</div>
            ${authLine}
        </div>`;
    }).join("");

    panel.innerHTML = html;
}

async function loadModels() {
    try {
        state.models = await fetchJson("/api/models");
        populateModelSelect(els.modelSelect, true);
        populateModelSelect(els.redModel, false);
        populateModelSelect(els.blueModel, false);
        applyConfig();
    } catch (error) {
        addMessage("error", `Failed to load models: ${error.message}`);
    }
}

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

    // state.packs is an array of pack dicts from /api/packs
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
}

function populateModelSelect(selectEl, includeAuto) {
    if (!selectEl) return;

    const grouped = {};
    for (const model of state.models) {
        if (!includeAuto && model.id === "auto") continue;
        const provider = model.provider || "Other";
        if (!grouped[provider]) grouped[provider] = [];
        grouped[provider].push(model);
    }

    selectEl.innerHTML = "";
    for (const [provider, models] of Object.entries(grouped)) {
        const optgroup = document.createElement("optgroup");
        optgroup.label = provider;
        models.forEach((model) => {
            const option = document.createElement("option");
            option.value = model.id;
            const priced = model.cost_in > 0 || model.cost_out > 0;
            option.textContent = priced
                ? `${model.label} (${formatCompactMoney(model.cost_in)}/${formatCompactMoney(model.cost_out)})`
                : model.label;
            optgroup.appendChild(option);
        });
        selectEl.appendChild(optgroup);
    }
}

function restoreSettings() {
    const defaults = state.config?.defaults || {};

    const savedSandboxMode = localStorage.getItem("forge_sandbox_mode");
    els.sandboxToggle.checked = savedSandboxMode !== null ? savedSandboxMode === "true" : true;

    const savedDirectMode = localStorage.getItem("forge_direct_mode");
    els.directToggle.checked = savedDirectMode === "true";

    const agentCount = localStorage.getItem("forge_agent_count") || String(defaults.agent_count || 16);
    els.agentSlider.value = agentCount;
    els.agentCount.textContent = agentCount;

    const savedModel = localStorage.getItem("forge_executor_model") || defaults.executor_model || "";
    if (hasOption(els.modelSelect, savedModel)) {
        els.modelSelect.value = savedModel;
    }

    const savedPack = localStorage.getItem("forge_pack") || "";
    if (hasOption(els.packSelect, savedPack)) {
        els.packSelect.value = savedPack;
    }

    if (!els.redModel.value && els.redModel.options.length > 0) {
        els.redModel.value = pickArenaDefaultModel();
    }
    if (!els.blueModel.value && els.blueModel.options.length > 0) {
        els.blueModel.value = pickArenaDefaultModel();
    }
}

function pickArenaDefaultModel() {
    const preferred = [
        "grok-4.20-0309-reasoning", "grok-code-fast-1",
        "gpt-5.4-mini", "gpt-4o-mini",
        "claude-haiku-4-5-20251001",
    ];
    for (const modelId of preferred) {
        if (hasOption(els.redModel, modelId)) return modelId;
    }
    return els.redModel.options[0]?.value || "";
}

function hasOption(selectEl, value) {
    return Array.from(selectEl.options).some((option) => option.value === value);
}

function updateControlState() {
    const sandboxEnabled = els.sandboxToggle.checked && !state.isRunning;
    const controlsDisabled = state.isRunning;

    els.sandboxPath.disabled = !sandboxEnabled;
    els.agentControl.classList.toggle("disabled", els.directToggle.checked || controlsDisabled);

    els.sandboxToggle.disabled = controlsDisabled;
    els.directToggle.disabled = controlsDisabled;
    els.agentSlider.disabled = controlsDisabled || els.directToggle.checked;
    els.modelSelect.disabled = controlsDisabled;
    els.packSelect.disabled = controlsDisabled;
    els.arenaBtn.disabled = controlsDisabled;
    els.submitBtn.disabled = controlsDisabled;

    els.killBtn.classList.toggle("hidden", !state.isRunning);
    els.killBtn.disabled = !state.isRunning;

    updateStatus(state.isRunning ? (state.isArenaMode ? "Arena Running" : "Running") : "Ready", state.isRunning);
}

function applyWorkspaceMode() {
    const arenaVisible = state.isArenaMode;
    els.arenaView.classList.toggle("hidden", !arenaVisible);
    els.chatArea.classList.toggle("hidden", arenaVisible);
    els.backToForgeBtn.classList.toggle("hidden", !(arenaVisible && !state.isRunning));

    if (arenaVisible) {
        els.workspaceTitle.textContent = "Arena Console";
        els.workspaceSubtitle.textContent = "Live commentary, scores, and team logs stream here.";
    } else {
        els.workspaceTitle.textContent = "Forge Console";
        els.workspaceSubtitle.textContent = "Planner, executor, guardrails, and task output stream here.";
    }
}

async function loadSessionCost() {
    try {
        const cost = await fetchJson("/api/cost");
        state.sessionCostUsd = cost.session_cost || 0;
        state.sessionTollUsd = cost.session_toll || 0;
        renderCostMetrics();
    } catch (error) {
        addMessage("error", `Failed to load cost data: ${error.message}`);
    }
}

function renderCostMetrics() {
    els.sessionCost.textContent = formatMoney(state.sessionCostUsd, 6);
    els.sessionToll.textContent = formatMoney(state.sessionTollUsd, 6);
    els.taskCost.textContent = formatMoney(state.currentTaskCostUsd, 6);

    toneMoneyElement(els.sessionCost, state.sessionCostUsd, state.config?.limits?.session_cost_usd || 0);
    toneMoneyElement(els.sessionToll, state.sessionTollUsd, 0);
    toneMoneyElement(els.taskCost, state.currentTaskCostUsd, state.config?.limits?.task_cost_usd || 0);
}

function toneMoneyElement(element, value, limit) {
    element.classList.remove("metric-low", "metric-mid", "metric-high");

    if (!limit) {
        if (value >= 5) element.classList.add("metric-high");
        else if (value >= 1) element.classList.add("metric-mid");
        else element.classList.add("metric-low");
        return;
    }

    const ratio = value / limit;
    if (ratio >= 1) element.classList.add("metric-high");
    else if (ratio >= 0.5) element.classList.add("metric-mid");
    else element.classList.add("metric-low");
}

async function resetCosts() {
    if (state.isRunning) return;
    if (!confirm("Reset session cost and toll counters?")) return;

    try {
        const result = await fetchJson("/api/cost/reset", { method: "POST" });
        state.sessionCostUsd = result.session_cost || 0;
        state.sessionTollUsd = result.session_toll || 0;
        state.currentTaskCostUsd = 0;
        renderCostMetrics();
    } catch (error) {
        addMessage("error", `Failed to reset costs: ${error.message}`);
    }
}

async function loadHistory() {
    try {
        const tasks = await fetchJson("/api/history");
        state.history = Array.isArray(tasks) ? [...tasks].reverse() : [];
        renderHistory();

        if (state.selectedHistoryId) {
            renderHistoryDetail(state.history.find((task) => task.task_id === state.selectedHistoryId) || null);
        }
    } catch (error) {
        els.historyList.textContent = `Failed to load history: ${error.message}`;
    }
}

function renderHistory() {
    if (!state.history.length) {
        els.historyList.className = "stack-list empty-state";
        els.historyList.textContent = "No completed tasks yet.";
        return;
    }

    els.historyList.className = "stack-list";
    els.historyList.innerHTML = "";

    state.history.forEach((task) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "stack-item task-item";
        if (task.task_id === state.selectedHistoryId) {
            button.classList.add("selected");
        }

        const results = Array.isArray(task.results) ? task.results : [];
        const successCount = results.filter((result) => result.status === "success").length;
        const planned = isPlannedTask(task) ? "Planned" : "Direct";

        button.innerHTML = `
            <div class="stack-item-head">
                <strong>${escapeHtml(truncate(task.task || "Untitled task", 68))}</strong>
                <span class="pill">${planned}</span>
            </div>
            <div class="stack-item-sub">${escapeHtml(formatTimestamp(task.timestamp))}</div>
            <div class="stack-item-meta">${escapeHtml(task.final_summary || "No summary")} | ${successCount}/${results.length || 0} steps</div>
        `;

        button.addEventListener("click", () => {
            state.selectedHistoryId = task.task_id;
            renderHistory();
            renderHistoryDetail(task);
        });

        els.historyList.appendChild(button);
    });
}

async function renderHistoryDetail(task) {
    if (!task) {
        els.historyDetail.className = "history-detail empty-state";
        els.historyDetail.textContent = "Select a past task to inspect the full event stream, tool calls, widgets, costs, and safety data.";
        els.inspectorFilters.style.display = "none";
        return;
    }

    // Try to load the full run log for rich inspector view
    try {
        const run = await fetchJson(`/api/runs/${task.task_id}`);
        if (run && !run.error && Array.isArray(run.events) && run.events.length) {
            renderRunInspector(task, run.events, run.meta);
            return;
        }
    } catch (_) {
        // No run log — fall back to legacy view
    }
    renderHistoryDetailLegacy(task);
}

function renderHistoryDetailLegacy(task) {
    els.inspectorFilters.style.display = "none";
    const results = Array.isArray(task.results) ? task.results : [];
    const uniqueTools = Array.from(new Set(results.flatMap((result) => result.tools_used || [])));
    const stepsHtml = results.length
        ? results.map((result) => {
            const trust = result.trust_score_after != null ? ` | trust ${Number(result.trust_score_after).toFixed(2)}` : "";
            const latency = result.latency_seconds ? ` | ${result.latency_seconds}s` : "";
            const reassign = result.was_reassigned ? ` | from ${escapeHtml(result.reassigned_from || "previous")}` : "";
            return `
                <li>
                    <strong>Step ${result.step_number}</strong>
                    <span>${escapeHtml(result.status || "unknown")}</span>
                    <div>${escapeHtml(result.delegatee_model || "-")}${trust}${latency}${reassign}</div>
                </li>
            `;
        }).join("")
        : "<li>No step results stored.</li>";

    const chainHtml = task.accountability_chain?.chain?.length
        ? `
            <div class="history-block">
                <h4>Accountability</h4>
                <ul class="mini-list">
                    ${task.accountability_chain.chain.map((hop) => `
                        <li>${escapeHtml(`${hop.hop}. ${hop.delegator} -> ${hop.delegatee} [${hop.status}]`)}</li>
                    `).join("")}
                </ul>
            </div>
        `
        : "";

    els.historyDetail.className = "history-detail";
    els.historyDetail.innerHTML = `
        <div class="history-block">
            <div class="history-kicker">${escapeHtml(formatTimestamp(task.timestamp))}</div>
            <h4>${escapeHtml(task.task || "Untitled task")}</h4>
            <p>${escapeHtml(task.final_summary || "No summary stored.")}</p>
        </div>
        <div class="history-block">
            <h4>Run Shape</h4>
            <p>${isPlannedTask(task) ? "Planned task" : "Direct task"} | ${results.length} steps | ${escapeHtml(task.task_id || "-")}</p>
            <p>${uniqueTools.length ? `Tools: ${escapeHtml(uniqueTools.join(", "))}` : "No tool data recorded."}</p>
        </div>
        <div class="history-block">
            <h4>Step Results</h4>
            <ul class="mini-list">${stepsHtml}</ul>
        </div>
        ${chainHtml}
    `;
}

// ── Run Inspector — Full Event Timeline ─────────────────────────────────────

const EVENT_CATEGORIES = {
    status:              "status",
    plan_content:        "step",
    step_start:          "step",
    step_done:           "step",
    content:             "content",
    tool_call:           "tools",
    tool_result:         "tools",
    guardrail_violation: "safety",
    guardrail_summary:   "safety",
    firewall_block:      "safety",
    escalation:          "safety",
    widget_render:       "widgets",
    judge_scores:        "scores",
    token_usage:         "cost",
    toll_deducted:       "cost",
    toll_summary:        "cost",
    done:                "status",
    cancelled:           "status",
    error:               "safety",
    arena_status:        "arena",
    arena_round_start:   "arena",
    arena_team_action:   "arena",
    arena_commentary:    "arena",
    arena_scores:        "scores",
    arena_result:        "arena",
};

function categorizeEvent(evt) {
    return EVENT_CATEGORIES[evt.type] || "status";
}

function renderRunInspector(task, events, meta) {
    els.inspectorFilters.style.display = "";

    // Compute summary stats
    const stats = {
        events: events.length,
        steps: 0,
        tools: 0,
        guardrails: 0,
        firewalls: 0,
        widgets: 0,
        scores: 0,
        costUsd: 0,
        tokens: 0,
    };
    const toolNames = new Set();

    for (const evt of events) {
        switch (evt.type) {
            case "step_start": stats.steps++; break;
            case "tool_call":
                stats.tools++;
                if (evt.name) toolNames.add(evt.name);
                break;
            case "guardrail_violation": stats.guardrails++; break;
            case "firewall_block": stats.firewalls++; break;
            case "widget_render": stats.widgets++; break;
            case "judge_scores": case "arena_scores": stats.scores++; break;
            case "token_usage":
                stats.costUsd += evt.cost_usd || 0;
                stats.tokens += (evt.input_tokens || 0) + (evt.output_tokens || 0);
                break;
        }
    }

    const durationSec = events.length >= 2
        ? ((events[events.length - 1].t || 0) - (events[0].t || 0)).toFixed(1)
        : "-";

    // Build HTML
    const summaryHtml = `
        <div class="inspector-summary">
            <div class="inspector-stat"><em>Events</em><strong>${stats.events}</strong></div>
            <div class="inspector-stat"><em>Steps</em><strong>${stats.steps}</strong></div>
            <div class="inspector-stat"><em>Tool Calls</em><strong>${stats.tools}</strong></div>
            <div class="inspector-stat"><em>Guardrails</em><strong>${stats.guardrails}</strong></div>
            <div class="inspector-stat"><em>Firewalls</em><strong>${stats.firewalls}</strong></div>
            <div class="inspector-stat"><em>Widgets</em><strong>${stats.widgets}</strong></div>
            <div class="inspector-stat"><em>Scores</em><strong>${stats.scores}</strong></div>
            <div class="inspector-stat"><em>Tokens</em><strong>${stats.tokens.toLocaleString()}</strong></div>
            <div class="inspector-stat"><em>Cost</em><strong>${formatMoney(stats.costUsd, 4)}</strong></div>
            <div class="inspector-stat"><em>Duration</em><strong>${durationSec}s</strong></div>
        </div>
    `;

    const toolsLine = toolNames.size
        ? `<div class="history-block"><h4>Tools Used</h4><p>${escapeHtml([...toolNames].join(", "))}</p></div>`
        : "";

    const t0 = events[0]?.t || 0;
    const timelineHtml = events.map((evt, i) => {
        const cat = categorizeEvent(evt);
        const relTime = t0 ? `+${((evt.t || 0) - t0).toFixed(1)}s` : `#${i}`;
        const body = formatEventBody(evt);
        return `
            <div class="inspector-event" data-cat="${cat}" data-seq="${evt.seq ?? i}">
                <div class="ev-head">
                    <span class="ev-type">${escapeHtml(evt.type || "unknown")}</span>
                    <span class="ev-time">${escapeHtml(relTime)}</span>
                </div>
                <div class="ev-body">${body}</div>
            </div>
        `;
    }).join("");

    els.historyDetail.className = "history-detail";
    els.historyDetail.innerHTML = `
        <div class="history-block">
            <div class="history-kicker">${escapeHtml(formatTimestamp(task.timestamp))} | ${escapeHtml(task.task_id || "-")}</div>
            <h4>${escapeHtml(task.task || "Untitled task")}</h4>
            <p>${escapeHtml(task.final_summary || "No summary stored.")}</p>
        </div>
        ${summaryHtml}
        ${toolsLine}
        <div class="inspector-timeline">${timelineHtml}</div>
    `;

    // Wire filter buttons
    bindInspectorFilters();
}

function formatEventBody(evt) {
    switch (evt.type) {
        case "status":
        case "arena_status":
        case "cancelled":
            return escapeHtml(evt.content || "");

        case "plan_content":
        case "content":
            return `<pre>${escapeHtml(truncate(evt.content || "", 300))}</pre>`;

        case "step_start":
            return `<strong>Step ${evt.step}: ${escapeHtml(evt.title || "")}</strong>`
                + (evt.description ? `<br>${escapeHtml(evt.description)}` : "")
                + (evt.delegatee ? `<br><em>Delegatee:</em> ${escapeHtml(evt.delegatee)}` : "");

        case "step_done": {
            const parts = [`Step ${evt.step} ${evt.status || "done"}`];
            if (evt.delegatee) parts.push(evt.delegatee);
            if (evt.latency_s) parts.push(`${evt.latency_s}s`);
            if (evt.trust_score != null) parts.push(`trust ${Number(evt.trust_score).toFixed(2)}`);
            if (evt.was_reassigned) parts.push("reassigned");
            return escapeHtml(parts.join(" | "));
        }

        case "tool_call":
            return `<strong>${escapeHtml(evt.name || "tool")}</strong>`
                + `<pre>${escapeHtml(truncate(JSON.stringify(evt.args || {}, null, 2), 200))}</pre>`;

        case "tool_result":
            return `<pre>${escapeHtml(truncate(evt.result || "", 300))}</pre>`;

        case "guardrail_violation":
            return `<strong>${escapeHtml(evt.severity || "warning")}</strong> ${escapeHtml(evt.guardrail || "")} — ${escapeHtml(evt.message || "")}`;

        case "guardrail_summary":
            return `${evt.total_violations || 0} violations | ${evt.blocks || 0} blocks | ${evt.warnings || 0} warnings`;

        case "firewall_block":
            return `<strong>${escapeHtml(evt.tool || "tool")}</strong> blocked — ${escapeHtml(evt.reason || "")}`;

        case "escalation":
            return `<strong>${escapeHtml(evt.category || "general")}</strong> — ${escapeHtml(evt.reason || "")}`;

        case "widget_render":
            return `<strong>${escapeHtml(evt.title || "Widget")}</strong> (${escapeHtml(evt.widget_type || "custom")})`
                + `<div class="inspector-widget-preview"><iframe sandbox="allow-scripts" srcdoc="${escapeAttr(evt.html || "<p>Empty</p>")}" title="${escapeAttr(evt.title || "Widget")}"></iframe></div>`;

        case "judge_scores":
            if (Array.isArray(evt.scores)) {
                return evt.scores.map(s =>
                    `Step ${s.step ?? "?"}: ${s.score ?? "-"}/10 — ${escapeHtml(truncate(s.reasoning || "", 100))}`
                ).join("<br>");
            }
            return escapeHtml(JSON.stringify(evt.scores || evt));

        case "token_usage":
            return `${escapeHtml(evt.model || "-")} | in: ${(evt.input_tokens || 0).toLocaleString()} out: ${(evt.output_tokens || 0).toLocaleString()} | ${formatMoney(evt.cost_usd || 0, 6)}`;

        case "toll_deducted":
            return `${formatMoney(evt.toll_usd || 0, 6)} | ${escapeHtml(evt.sender || "-")} → ${escapeHtml(evt.receiver || "-")}`;

        case "toll_summary":
            return `${evt.total_messages || 0} messages | ${formatMoney(evt.total_tolls_usd || 0, 6)} tolls`;

        case "done":
            return escapeHtml(evt.summary || "Task complete");

        case "arena_round_start":
            return `<strong>Round ${evt.round}: ${escapeHtml(evt.name || "")}</strong>`;

        case "arena_team_action":
            return `<strong class="team-${escapeHtml(evt.team || "red")}">${escapeHtml((evt.team || "").toUpperCase())}</strong> [${escapeHtml(evt.action_type || "action")}] ${escapeHtml(truncate(evt.content || "", 200))}`;

        case "arena_commentary":
            return escapeHtml(truncate(evt.content || "", 300));

        case "arena_scores":
            return `Red +${evt.red_score || 0} (${evt.red_total || 0}) | Blue +${evt.blue_score || 0} (${evt.blue_total || 0})`;

        case "arena_result":
            return `<strong>${escapeHtml(evt.winner || "?")}</strong> | Red ${evt.red_total || 0} | Blue ${evt.blue_total || 0}`;

        default:
            return `<pre>${escapeHtml(truncate(JSON.stringify(evt, null, 2), 200))}</pre>`;
    }
}

function escapeAttr(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

function bindInspectorFilters() {
    const filterBtns = els.inspectorFilters.querySelectorAll(".filter-btn");
    filterBtns.forEach(btn => {
        // Replace with fresh clone to remove old listeners
        const fresh = btn.cloneNode(true);
        btn.parentNode.replaceChild(fresh, btn);
        fresh.addEventListener("click", () => {
            filterBtns.forEach(b => b.classList.remove("active"));
            // Re-query since we replaced nodes
            els.inspectorFilters.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
            fresh.classList.add("active");
            applyInspectorFilter(fresh.dataset.filter);
        });
    });
}

function applyInspectorFilter(filter) {
    const timeline = els.historyDetail.querySelector(".inspector-timeline");
    if (!timeline) return;

    const events = timeline.querySelectorAll(".inspector-event");
    events.forEach(el => {
        if (filter === "all") {
            el.classList.remove("filtered-out");
        } else {
            const cat = el.dataset.cat;
            // Map filter names to categories
            const match = (filter === "steps" && (cat === "step" || cat === "content" || cat === "status"))
                || (filter === "tools" && cat === "tools")
                || (filter === "safety" && cat === "safety")
                || (filter === "widgets" && cat === "widgets")
                || (filter === "scores" && cat === "scores");
            el.classList.toggle("filtered-out", !match);
        }
    });
}

async function loadMemory() {
    try {
        const memories = await fetchJson("/api/memory");
        state.memories = Array.isArray(memories) ? [...memories].reverse() : [];
        renderMemory();
    } catch (error) {
        els.memoryList.className = "stack-list compact empty-state";
        els.memoryList.textContent = `Failed to load memory: ${error.message}`;
    }
}

function renderMemory() {
    if (!state.memories.length) {
        els.memoryList.className = "stack-list compact empty-state";
        els.memoryList.textContent = "No session memory stored yet.";
        return;
    }

    els.memoryList.className = "stack-list compact";
    els.memoryList.innerHTML = "";

    state.memories.forEach((memory) => {
        const item = document.createElement("div");
        item.className = "stack-item";
        item.innerHTML = `
            <div class="stack-item-head">
                <strong>${escapeHtml(truncate(memory.task || "Memory", 58))}</strong>
            </div>
            <div class="stack-item-meta">${escapeHtml((memory.tools_effective || []).join(", ") || "No tools recorded")}</div>
            <div class="stack-item-sub">${escapeHtml(truncate((memory.key_paths || []).join(" | ") || memory.outcome || "No details", 120))}</div>
        `;
        els.memoryList.appendChild(item);
    });
}

async function clearMemory() {
    if (!confirm("Clear all session memory for this server?")) return;

    try {
        await fetchJson("/api/memory/clear", { method: "POST" });
        state.memories = [];
        renderMemory();
    } catch (error) {
        addMessage("error", `Failed to clear memory: ${error.message}`);
    }
}

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

// ══════════════════════════════════════════════════════════════════════
// Chess Arena tab — LLM vs LLM match
// ══════════════════════════════════════════════════════════════════════
// The server is the source of truth for board state and legality. The UI
// holds just the latest snapshot + a small autoplay loop that calls
// /step repeatedly until the match ends or the user stops it.

const chessState = {
    match: null,
    autoPlaying: false,
    stepInFlight: false,
    // Highest capture.move_n seen so far — anything higher gets the
    // .just-taken pop animation on next render. Reset on new match.
    lastCaptureN: 0,
};

// NES NTSC is 60.0988 frames per second. On monitors above 60Hz, a naive
// `requestAnimationFrame(() => nes.frame())` loop emulates at the display
// rate — so 144Hz panels run SMB at 2.4× speed. We decouple emulation
// from render: accumulate wall-clock time and fire `nes.frame()` once
// per NTSC-frame of elapsed time, skipping RAFs when we've already
// advanced far enough.
const NES_FRAME_MS = 1000 / 60.0988;

// Unicode figurines, keyed by python-chess's single-letter piece symbols.
const CHESS_GLYPHS = {
    "K": "\u2654", "Q": "\u2655", "R": "\u2656",
    "B": "\u2657", "N": "\u2658", "P": "\u2659",
    "k": "\u265A", "q": "\u265B", "r": "\u265C",
    "b": "\u265D", "n": "\u265E", "p": "\u265F",
};

function chessSetStatus(msg, kind = "") {
    const el = document.getElementById("chess-status-msg");
    if (!el) return;
    el.textContent = msg || "";
    el.className = "chess-status-msg" + (kind ? " " + kind : "");
}

async function chessPopulateModelSelects(force = false) {
    // Reuse state.models from loadModels() (already fetched during init).
    const whiteSel = document.getElementById("chess-white-model");
    const blackSel = document.getElementById("chess-black-model");
    const judgeSel = document.getElementById("chess-judge-model");
    if (!whiteSel || !blackSel || !judgeSel) return;
    const baseModels = (state.models || []).filter(m => m.id !== "auto");
    if (baseModels.length === 0) return;
    // On explicit refresh (force=true), re-fetch /api/lmstudio/models
    // even if the selects were already populated. Otherwise skip to
    // avoid clobbering the user's current selection on tab switches.
    if (!force && whiteSel.options.length > 0) return;

    // Ask LM Studio what's actually loaded right now — replaces the
    // static "lmstudio:default" entry (which 400s on modern LM Studio)
    // with real model ids like "lmstudio:qwen/qwen3.5-9b". Fetch is
    // best-effort; if LM Studio isn't running we just fall back to the
    // config-declared "LM Studio (Local)" placeholder.
    let lmStudioModels = [];
    try {
        const resp = await fetch("/api/lmstudio/models");
        const data = await resp.json().catch(() => ({}));
        if (Array.isArray(data.models)) {
            lmStudioModels = data.models.map(m => ({
                id: "lmstudio:" + m.id,
                label: m.id + " (local)",
                provider: "Local (LM Studio)",
            }));
        }
    } catch (_) { /* LM Studio down — skip live models */ }

    // Replace the generic "Local" placeholder with the live list when
    // we have one; otherwise keep whatever's in the base registry.
    const models = lmStudioModels.length > 0
        ? [...baseModels.filter(m => !m.id.startsWith("lmstudio:") && !m.id.startsWith("ollama:")),
           ...lmStudioModels]
        : baseModels;

    // Group by provider the same way populateModelSelect does
    const grouped = {};
    for (const m of models) {
        const provider = m.provider || "Other";
        (grouped[provider] ||= []).push(m);
    }

    // Models that can't serve as judge: multi-agent grok needs xai_sdk's
    // native `agent_count` path, which our judge caller (OpenAI-compat)
    // can't drive. Filter them out of the judge dropdown so the user
    // doesn't pick a choice that silently 400s.
    const isJudgeCompatible = (m) => {
        const id = (m.id || "").toLowerCase();
        const label = (m.label || "").toLowerCase();
        if (id.includes("multi-agent") || label.includes("multi-agent")) return false;
        if (label.includes("planner only")) return false;
        return true;
    };

    const fillSelect = (sel, pickIdx, filter = null) => {
        // Remember the current selection so a refresh (force=true)
        // doesn't silently reset it to the default. If the prior value
        // isn't in the new option list (e.g. model was un-loaded) we
        // fall back to the pickIdx default so the user isn't stuck on
        // a phantom selection.
        const prior = sel.value;
        sel.innerHTML = "";
        for (const [provider, list] of Object.entries(grouped)) {
            const filtered = filter ? list.filter(filter) : list;
            if (!filtered.length) continue;
            const group = document.createElement("optgroup");
            group.label = provider;
            filtered.forEach(m => {
                const opt = document.createElement("option");
                opt.value = m.id;
                opt.textContent = m.label || m.id;
                group.appendChild(opt);
            });
            sel.appendChild(group);
        }
        if (sel.options.length > 0) {
            const priorStillExists = prior
                && Array.from(sel.options).some(o => o.value === prior);
            sel.value = priorStillExists
                ? prior
                : sel.options[Math.min(pickIdx, sel.options.length - 1)].value;
        }
    };

    fillSelect(whiteSel, 0);
    fillSelect(blackSel, 1);
    fillSelect(judgeSel, 0, isJudgeCompatible);

    // Judge default — prefer Grok 4.20 reasoning if present (matches what the
    // user asked for: "Grok 4.20 judge"). Falls back through the newer
    // Claude/OpenAI flagships before giving up on the first option.
    // Note: Grok multi-agent is filtered out by isJudgeCompatible.
    const preferredJudges = [
        "grok-4.20-0309-reasoning",
        "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
        "gpt-5.4", "gpt-4o",
    ];
    for (const preferred of preferredJudges) {
        for (let i = 0; i < judgeSel.options.length; i++) {
            if (judgeSel.options[i].value === preferred) {
                judgeSel.selectedIndex = i;
                return;
            }
        }
    }
}

// FEN → 8x8 array of piece chars. First rank in FEN is rank 8 (top).
function fenToGrid(fen) {
    const placement = (fen || "").split(" ")[0] || "";
    const ranks = placement.split("/");
    const grid = [];
    ranks.forEach(rank => {
        const row = [];
        for (const ch of rank) {
            if (/[1-8]/.test(ch)) {
                const n = parseInt(ch, 10);
                for (let i = 0; i < n; i++) row.push(null);
            } else {
                row.push(ch);
            }
        }
        while (row.length < 8) row.push(null);
        grid.push(row);
    });
    while (grid.length < 8) grid.push(Array(8).fill(null));
    return grid;
}

// Converts a UCI token (e.g. "e2e4") to [fromSquare, toSquare] algebraic.
function uciSquares(uci) {
    if (!uci || uci.length < 4) return [null, null];
    return [uci.slice(0, 2), uci.slice(2, 4)];
}

function renderChess() {
    const board = document.getElementById("chess-board");
    if (!board) return;

    const m = chessState.match;
    if (!m) {
        board.innerHTML = `<div class="chess-empty">No active match. Configure players above and press "Start match".</div>`;
        document.getElementById("chess-turn").textContent = "—";
        document.getElementById("chess-status").textContent = "—";
        document.getElementById("chess-movenum").textContent = "0";
        document.getElementById("chess-white-badge").textContent = "— White";
        document.getElementById("chess-black-badge").textContent = "— Black";
        document.getElementById("chess-moves").innerHTML =
            `<div class="chess-moves-empty">No moves yet.</div>`;
        ["chess-step-btn","chess-auto-btn","chess-resign-white-btn","chess-resign-black-btn"]
            .forEach(id => { const b = document.getElementById(id); if (b) b.disabled = true; });
        return;
    }

    // Board
    const grid = fenToGrid(m.fen);
    const lastMove = m.moves && m.moves.length ? m.moves[m.moves.length - 1] : null;
    const [lastFrom, lastTo] = lastMove ? uciSquares(lastMove.uci) : [null, null];
    const checkSquare = m.in_check ? findKingSquare(grid, m.turn === "white" ? "K" : "k") : null;

    const cells = [];
    for (let r = 0; r < 8; r++) {       // r=0 → rank 8 (top)
        for (let f = 0; f < 8; f++) {   // f=0 → file a (left)
            const rankChar = (8 - r).toString();
            const fileChar = "abcdefgh"[f];
            const square = fileChar + rankChar;
            const piece = grid[r][f];
            const isLight = ((r + f) % 2 === 0);
            const classes = ["chess-square", isLight ? "light" : "dark"];
            if (square === lastFrom) classes.push("last-from");
            if (square === lastTo)   classes.push("last-to");
            if (square === checkSquare) classes.push("in-check");

            const coordFile = (r === 7) ? `<span class="coord file">${fileChar}</span>` : "";
            const coordRank = (f === 0) ? `<span class="coord rank">${rankChar}</span>` : "";
            const pieceHtml = piece
                ? `<span class="chess-piece ${piece === piece.toUpperCase() ? "white" : "black"}">${CHESS_GLYPHS[piece] || piece}</span>`
                : "";
            cells.push(`<div class="${classes.join(" ")}" data-sq="${square}">${coordFile}${coordRank}${pieceHtml}</div>`);
        }
    }
    board.innerHTML = cells.join("");

    // Status block
    const turnEl = document.getElementById("chess-turn");
    const statusEl = document.getElementById("chess-status");
    const moveNumEl = document.getElementById("chess-movenum");
    if (m.status === "active") {
        turnEl.textContent = m.turn.toUpperCase();
        turnEl.className = m.turn === "white" ? "white-turn" : "black-turn";
        statusEl.textContent = m.in_check ? "CHECK" : "IN PLAY";
        statusEl.className = m.in_check ? "black-turn" : "";
    } else {
        turnEl.textContent = "—";
        turnEl.className = "ended";
        const label = {
            white_wins: "WHITE WINS",
            black_wins: "BLACK WINS",
            draw: "DRAW",
        }[m.status] || m.status.toUpperCase();
        statusEl.textContent = m.reason ? `${label} (${m.reason})` : label;
        statusEl.className = "ended";
    }
    moveNumEl.textContent = Math.ceil(m.halfmove_count / 2).toString();

    // Player badges
    const whiteBadge = document.getElementById("chess-white-badge");
    const blackBadge = document.getElementById("chess-black-badge");
    whiteBadge.textContent = `${m.white_model} · White`;
    blackBadge.textContent = `${m.black_model} · Black`;
    whiteBadge.classList.toggle("active", m.status === "active" && m.turn === "white");
    blackBadge.classList.toggle("active", m.status === "active" && m.turn === "black");

    // Controls
    const active = m.status === "active";
    const busy = chessState.stepInFlight || chessState.autoPlaying;
    document.getElementById("chess-step-btn").disabled = !active || busy;
    document.getElementById("chess-auto-btn").disabled = !active || chessState.stepInFlight;
    document.getElementById("chess-resign-white-btn").disabled = !active || busy;
    document.getElementById("chess-resign-black-btn").disabled = !active || busy;
    const callBtn = document.getElementById("chess-commentary-now-btn");
    if (callBtn) callBtn.disabled = !active || busy;

    const autoBtn = document.getElementById("chess-auto-btn");
    autoBtn.textContent = chessState.autoPlaying ? "Stop auto-play" : "Auto-play";
    autoBtn.classList.toggle("is-running", chessState.autoPlaying);

    // Move log
    renderChessMoves(m.moves);

    // Captured-pieces side-board + token counter
    renderChessCaptures(m.captures || []);
    renderChessTokens(m.tokens || null);
}

function findKingSquare(grid, kingChar) {
    for (let r = 0; r < 8; r++) {
        for (let f = 0; f < 8; f++) {
            if (grid[r][f] === kingChar) {
                return "abcdefgh"[f] + (8 - r).toString();
            }
        }
    }
    return null;
}

function renderChessMoves(moves) {
    const el = document.getElementById("chess-moves");
    if (!el) return;
    if (!moves || !moves.length) {
        el.innerHTML = `<div class="chess-moves-empty">No moves yet.</div>`;
        return;
    }
    el.innerHTML = "";
    moves.forEach(mv => {
        const row = document.createElement("div");
        row.className = "chess-move-row " + (mv.side === "white" ? "white-move" : "black-move") + (mv.forced ? " forced" : "");
        const moveNum = Math.floor((mv.n - 1) / 2) + 1;
        const dots = mv.side === "white" ? "." : "…";
        const tokBit = (mv.input_tokens || mv.output_tokens)
            ? ` · ${(mv.input_tokens || 0) + (mv.output_tokens || 0)}tok`
            : "";
        const meta = `${mv.ms}ms${mv.attempts > 1 ? ` · ${mv.attempts}×` : ""}${mv.forced ? " · forced" : ""}${tokBit}`;
        row.innerHTML = `
            <span class="move-n">${moveNum}${dots}</span>
            <span class="move-san" title="${escapeHtml(mv.uci)}">${escapeHtml(mv.san)}</span>
            <span class="move-meta">${escapeHtml(meta)}</span>
        `;
        if (mv.thinking) row.title = mv.thinking;
        el.appendChild(row);
    });
    // Scroll to bottom
    el.scrollTop = el.scrollHeight;
}

// ── Captured-pieces side-board ─────────────────────────────────────────
// Standard chess material values — used to compute the net advantage
// shown next to each captured row. Kings don't get a value (uncapturable).
const CHESS_PIECE_VALUES = { p: 1, n: 3, b: 3, r: 5, q: 9 };
const CHESS_PIECE_GLYPH = {
    "K": "\u2654", "Q": "\u2655", "R": "\u2656",
    "B": "\u2657", "N": "\u2658", "P": "\u2659",
    "k": "\u265A", "q": "\u265B", "r": "\u265C",
    "b": "\u265D", "n": "\u265E", "p": "\u265F",
};

function renderChessCaptures(captures) {
    const whitePiecesEl = document.getElementById("chess-capture-white-pieces");
    const blackPiecesEl = document.getElementById("chess-capture-black-pieces");
    const whiteMatEl = document.getElementById("chess-capture-white-material");
    const blackMatEl = document.getElementById("chess-capture-black-material");
    if (!whitePiecesEl || !blackPiecesEl) return;

    // Group by captor side. White captures black pieces (lowercase symbols);
    // Black captures white pieces (uppercase symbols).
    const byWhite = captures.filter(c => c.by === "white");
    const byBlack = captures.filter(c => c.by === "black");

    const lastSeenN = chessState.lastCaptureN || 0;
    const newestN = captures.length ? captures[captures.length - 1].move_n : 0;

    const renderRow = (el, records) => {
        el.innerHTML = "";
        // Order captures by piece value (heaviest first) for visual parity
        // with Chess.com / Lichess side-boards.
        const sorted = [...records].sort((a, b) => {
            const va = CHESS_PIECE_VALUES[a.piece_symbol.toLowerCase()] || 0;
            const vb = CHESS_PIECE_VALUES[b.piece_symbol.toLowerCase()] || 0;
            return vb - va;
        });
        sorted.forEach(cap => {
            const span = document.createElement("span");
            const isWhitePiece = cap.piece_symbol === cap.piece_symbol.toUpperCase();
            span.className = isWhitePiece ? "piece-w" : "piece-b";
            if (cap.move_n > lastSeenN) span.classList.add("just-taken");
            span.textContent = CHESS_PIECE_GLYPH[cap.piece_symbol] || cap.piece_symbol;
            span.title = `${cap.move_san} (move ${cap.move_n})`;
            el.appendChild(span);
        });
    };
    renderRow(whitePiecesEl, byWhite);
    renderRow(blackPiecesEl, byBlack);

    // Material balance: positive number means "this side is up X points".
    const sumMat = (records) => records.reduce((acc, c) =>
        acc + (CHESS_PIECE_VALUES[c.piece_symbol.toLowerCase()] || 0), 0);
    const whiteCaptured = sumMat(byWhite);  // points white took from black
    const blackCaptured = sumMat(byBlack);  // points black took from white
    const whiteAdv = whiteCaptured - blackCaptured;
    const blackAdv = blackCaptured - whiteCaptured;
    whiteMatEl.textContent = whiteAdv > 0 ? `+${whiteAdv}` : "";
    blackMatEl.textContent = blackAdv > 0 ? `+${blackAdv}` : "";
    whiteMatEl.classList.toggle("neg", whiteAdv < 0);
    blackMatEl.classList.toggle("neg", blackAdv < 0);

    chessState.lastCaptureN = newestN;
}

// ── Token counter ──────────────────────────────────────────────────────
function renderChessTokens(tokens) {
    if (!tokens) tokens = {
        white: {in: 0, out: 0, cost_usd: 0},
        black: {in: 0, out: 0, cost_usd: 0},
        judge: {in: 0, out: 0, cost_usd: 0},
        total_in: 0, total_out: 0, total_cost_usd: 0,
    };
    const fmt = (n) => n >= 1000 ? (n/1000).toFixed(1) + "k" : String(n);
    const row = (id, t) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = `${fmt(t.in || 0)} / ${fmt(t.out || 0)} · $${(t.cost_usd || 0).toFixed(4)}`;
    };
    row("chess-tokens-white", tokens.white);
    row("chess-tokens-black", tokens.black);
    row("chess-tokens-judge", tokens.judge);
    const total = document.getElementById("chess-tokens-total");
    if (total) {
        const totalTok = (tokens.total_in || 0) + (tokens.total_out || 0);
        total.textContent = `${fmt(totalTok)} tokens · $${(tokens.total_cost_usd || 0).toFixed(4)}`;
    }
}

async function chessNewMatch() {
    const white = document.getElementById("chess-white-model").value;
    const black = document.getElementById("chess-black-model").value;
    const judge = document.getElementById("chess-judge-model")?.value
               || "grok-4.20-0309-reasoning";
    // The commentary dropdown encodes "interval:window_plies". A window
    // of 0 means running commentary (judge sees the whole game); >0
    // means recap mode (judge only sees the last N plies + is told to
    // not reference earlier moves). Default 2:0 = every 2 rounds, full
    // history — same as before.
    const ciRaw = document.getElementById("chess-commentary-interval")?.value || "2:0";
    const [ciPart, winPart] = ciRaw.split(":");
    const interval = parseInt(ciPart, 10) || 2;
    const windowPlies = parseInt(winPart || "0", 10) || 0;
    if (!white || !black) {
        chessSetStatus("Select both models first.", "err");
        return;
    }
    chessSetStatus("Starting match…", "working");
    try {
        const resp = await fetchJson("/api/chess", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                white_model: white,
                black_model: black,
                judge_model: judge,
                commentary_interval: interval,
                commentary_window_plies: windowPlies,
            }),
        });
        if (resp.error) { chessSetStatus(resp.error, "err"); return; }
        chessState.match = resp;
        chessState.autoPlaying = false;
        chessState.lastCaptureN = 0;
        // Fresh commentary state — clear the body + history from previous matches
        chessResetCommentary();
        renderChess();
        const modeTag = windowPlies > 0
            ? `recap-last-${windowPlies/2}-rounds`
            : "full-history";
        chessSetStatus(`Match started — ${white} vs ${black} · Judge ${judge} every ${interval} round${interval>1?"s":""} (${modeTag}). Auto-playing…`, "ok");
        // Kick off auto-play automatically. Users expected "start match" to
        // mean "begin the game" — making them hunt for a separate Step /
        // Auto-play button was a UX footgun. They can still pause anytime.
        chessToggleAuto();
    } catch (err) {
        chessSetStatus(`Start failed: ${err.message}`, "err");
    }
}

async function chessStep() {
    if (!chessState.match || chessState.stepInFlight) return;
    if (chessState.match.status !== "active") return;
    const mid = chessState.match.id;
    const turn = chessState.match.turn;
    const model = chessState.match.current_model;
    chessState.stepInFlight = true;
    chessSetStatus(`${turn.toUpperCase()} (${model}) is thinking…`, "working");
    renderChess();
    try {
        const resp = await fetchJson(`/api/chess/${encodeURIComponent(mid)}/step`, {
            method: "POST",
        });
        if (resp.id) chessState.match = resp;
        if (resp.error) {
            chessSetStatus(resp.error, "err");
            chessState.autoPlaying = false;
        } else if (chessState.match) {
            const last = chessState.match.moves[chessState.match.moves.length - 1];
            if (last) {
                const flag = last.forced ? " ⚠ forced random" : "";
                chessSetStatus(`${last.side} played ${last.san}${flag}`, last.forced ? "err" : "ok");
            }
            // Judge commentary lands here when it's the right beat.
            if (resp.new_commentary) {
                chessApplyCommentary(resp.new_commentary);
            } else if (resp.commentary_error) {
                // Judge failed (bad model choice, missing key, etc.).
                // Surface it in the commentary panel so the user knows why
                // nothing is appearing. Kill auto-play so they can fix it.
                chessShowCommentaryError(resp.commentary_error);
                chessState.autoPlaying = false;
            }
        }
    } catch (err) {
        chessSetStatus(`Step failed: ${err.message}`, "err");
        chessState.autoPlaying = false;
    } finally {
        chessState.stepInFlight = false;
        renderChess();
    }
}

// ── Chess commentary (TTS judge) ────────────────────────────────────────
// The judge fires server-side every N full moves; the /step response
// includes `new_commentary` when it's a commentary beat. We also let the
// user hit "Call it" to trigger an out-of-band narration via POST
// /api/chess/<id>/commentary.

function chessResetCommentary() {
    const body = document.getElementById("chess-commentary-body");
    const meta = document.getElementById("chess-commentary-meta");
    const hist = document.getElementById("chess-commentary-history");
    if (body) {
        body.textContent = "Commentary will appear here after the first full round.";
        body.className = "chess-commentary-body";
    }
    if (meta) meta.textContent = "—";
    if (hist) hist.innerHTML = "";
}

function chessShowCommentaryError(msg) {
    const body = document.getElementById("chess-commentary-body");
    const meta = document.getElementById("chess-commentary-meta");
    if (body) {
        body.textContent = msg;
        body.className = "chess-commentary-body error";
    }
    if (meta) meta.textContent = "judge failed";
    // Kill any in-flight TTS so a stale earlier line doesn't keep speaking
    try { stopTTS(); } catch (_) {}
}

function chessApplyCommentary(rec) {
    if (!rec || !rec.text) return;
    const body = document.getElementById("chess-commentary-body");
    const meta = document.getElementById("chess-commentary-meta");
    const hist = document.getElementById("chess-commentary-history");
    if (!body) return;

    // Slide the previous live line into the history before writing the new one.
    const prevText = body.textContent || "";
    const prevMeta = meta?.dataset?.prevHeader;
    if (prevText
        && !body.classList.contains("placeholder")
        && prevMeta
        && hist) {
        chessAddCommentaryHistoryRow(prevMeta, prevText);
    }

    body.textContent = rec.text;
    body.className = "chess-commentary-body speaking";
    const header = `Round ${rec.round_num} · ${rec.model} · ${rec.ms}ms`;
    if (meta) {
        meta.textContent = header;
        meta.dataset.prevHeader = header;
    }
    // Re-enable the "Call it" button after any in-flight judge call.
    const callBtn = document.getElementById("chess-commentary-now-btn");
    if (callBtn) callBtn.disabled = false;

    // TTS it if enabled
    const ttsOn = document.getElementById("chess-tts-toggle")?.checked;
    if (ttsOn) {
        flushSpeechBuffer();
        speakText(rec.text);
    }
    // Drop the "speaking" glow after a beat so it doesn't stay lit all match
    setTimeout(() => {
        if (body.textContent === rec.text) body.classList.remove("speaking");
    }, 4500);
}

function chessAddCommentaryHistoryRow(header, text) {
    const hist = document.getElementById("chess-commentary-history");
    if (!hist) return;
    const row = document.createElement("div");
    row.className = "chess-history-row";
    const roundMatch = header.match(/Round (\d+)/);
    const roundLabel = roundMatch ? `R${roundMatch[1]}` : "—";
    row.innerHTML = `
        <span class="history-round">${escapeHtml(roundLabel)}</span>
        <span class="history-text">${escapeHtml(text)}</span>
    `;
    // Newest on top
    hist.insertBefore(row, hist.firstChild);
    // Cap the history — anything beyond 12 rows is just noise.
    while (hist.children.length > 12) hist.removeChild(hist.lastChild);
}

async function chessCommentaryNow() {
    if (!chessState.match || chessState.match.status !== "active") return;
    const btn = document.getElementById("chess-commentary-now-btn");
    const body = document.getElementById("chess-commentary-body");
    if (btn) btn.disabled = true;
    if (body) {
        body.textContent = "Judge is thinking…";
        body.className = "chess-commentary-body thinking";
    }
    try {
        const resp = await fetchJson(
            `/api/chess/${encodeURIComponent(chessState.match.id)}/commentary`,
            { method: "POST" }
        );
        if (resp.error) {
            chessShowCommentaryError(resp.error);
            return;
        }
        chessApplyCommentary(resp);
    } catch (err) {
        chessShowCommentaryError(`Judge call failed: ${err.message}`);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function chessToggleAuto() {
    if (!chessState.match) return;
    if (chessState.autoPlaying) {
        chessState.autoPlaying = false;
        chessSetStatus("Auto-play stopped.", "");
        renderChess();
        return;
    }
    chessState.autoPlaying = true;
    renderChess();
    // Sequential loop — one /step at a time, stop on non-active or user toggle.
    while (chessState.autoPlaying
           && chessState.match
           && chessState.match.status === "active") {
        await chessStep();
        // Safety: tiny yield so UI can update and the abort button is responsive.
        await new Promise(r => setTimeout(r, 150));
    }
    chessState.autoPlaying = false;
    renderChess();
}

async function chessResign(side) {
    if (!chessState.match) return;
    if (!confirm(`Resign for ${side.toUpperCase()}?`)) return;
    try {
        const resp = await fetchJson(`/api/chess/${encodeURIComponent(chessState.match.id)}/resign`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ side }),
        });
        if (resp.error) { chessSetStatus(resp.error, "err"); return; }
        chessState.match = resp;
        chessState.autoPlaying = false;
        renderChess();
        chessSetStatus(`${side} resigned. ${resp.status === "white_wins" ? "White wins" : "Black wins"}.`, "ok");
    } catch (err) {
        chessSetStatus(`Resign failed: ${err.message}`, "err");
    }
}

function bindChessUi() {
    const newBtn    = document.getElementById("chess-new-btn");
    const stepBtn   = document.getElementById("chess-step-btn");
    const autoBtn   = document.getElementById("chess-auto-btn");
    const resignW   = document.getElementById("chess-resign-white-btn");
    const resignB   = document.getElementById("chess-resign-black-btn");
    const callBtn   = document.getElementById("chess-commentary-now-btn");
    const ttsBtn    = document.getElementById("chess-tts-toggle");
    if (newBtn)  newBtn .addEventListener("click", chessNewMatch);
    if (stepBtn) stepBtn.addEventListener("click", chessStep);
    if (autoBtn) autoBtn.addEventListener("click", chessToggleAuto);
    if (resignW) resignW.addEventListener("click", () => chessResign("white"));
    if (resignB) resignB.addEventListener("click", () => chessResign("black"));
    if (callBtn) callBtn.addEventListener("click", chessCommentaryNow);

    // Click-to-refresh on the three chess model dropdowns. Re-probes
    // /api/lmstudio/models and rebuilds the Local (LM Studio) optgroup
    // with whatever's actually loaded right now. Covers the case where
    // a model got un-loaded / deleted between page load and match
    // start and the dropdown still shows the phantom id.
    //
    // Debounced to at most one refresh per 30s so rapid clicks don't
    // hammer the endpoint. Preserves the user's selection across
    // rebuilds via fillSelect's prior-value lookup.
    let lastSelectRefreshAt = 0;
    const refreshSelects = () => {
        const chessTab = document.getElementById("tab-chess");
        if (!chessTab?.classList.contains("active")) return;
        const now = Date.now();
        if (now - lastSelectRefreshAt < 30000) return;
        lastSelectRefreshAt = now;
        chessPopulateModelSelects(true).catch(() => {});
    };
    ["chess-white-model", "chess-black-model", "chess-judge-model"].forEach(id => {
        const sel = document.getElementById(id);
        if (sel) sel.addEventListener("mousedown", refreshSelects);
    });

    // Chess TTS piggy-backs the arena TTS engine — flipping this toggle
    // also flips the shared state.ttsEnabled flag so speakText() actually
    // synthesizes. Persist the preference so tab reloads respect it.
    if (ttsBtn) {
        const savedTts = localStorage.getItem("forge_chess_tts");
        if (savedTts !== null) ttsBtn.checked = savedTts === "true";
        state.ttsEnabled = ttsBtn.checked || state.ttsEnabled;
        ttsBtn.addEventListener("change", () => {
            state.ttsEnabled = ttsBtn.checked;
            localStorage.setItem("forge_chess_tts", String(ttsBtn.checked));
            if (!ttsBtn.checked) stopTTS();
        });
    }
}

// ══════════════════════════════════════════════════════════════════════
// NES Arena — jsnes in browser + Grok coach loop
// ══════════════════════════════════════════════════════════════════════
// jsnes emulates the NES on the main thread. We drive it at ~60 fps via
// requestAnimationFrame, drawing the 256×240 framebuffer into a canvas.
// Every N seconds we POST a downscaled PNG of the canvas to /api/nes/.../coach
// and render the returned plan. Human input is captured through keyboard
// events; the controller in grok-mode maps the current plan → buttons.
//
// jsnes exposes:
//   new jsnes.NES({ onFrame(buf), onAudioSample(l,r) })
//   nes.loadROM(binaryString)
//   nes.frame()
//   nes.buttonDown(player, button)   player ∈ {1,2}, button ∈ Controller.BUTTON_*
//   nes.buttonUp(player, button)

const nesState = {
    nes: null,                  // jsnes.NES instance
    romSlug: "",
    romTitle: "",
    session: null,              // server session summary
    mode: "hybrid-coach",
    rafHandle: 0,
    running: false,
    paused: false,
    lastTickSentAt: 0,
    lastCoachAt: 0,
    coachInFlight: false,
    coachInterval: 2500,
    imageData: null,            // ImageData backing the canvas
    frameBuf: null,             // Uint32Array wrapping imageData.data
    frameN: 0,
    fpsEma: 0,
    fpsLastTs: 0,
    redAlertUntil: 0,
    roms: [],

    // ── Fast controller loop (Mode B) ──────────────────────────────
    // Separate cadence from coach — this is the tight-loop button
    // pusher. Calls LM Studio directly (bypasses Forge backend) for
    // lowest latency. `lastAction` is kept to de-duplicate identical
    // plans in the controller prompt so the model varies its moves.
    controllerInFlight: false,
    tickInFlight: false,          // guard for /tick heartbeat (was unguarded)
    lastControllerAt: 0,
    controllerInterval: 1000,
    controllerUrl: "http://localhost:1234/v1",
    controllerModel: "grok-4-1-fast-non-reasoning",
    controllerLatencyMs: 0,
    controllerCalls: 0,             // # of successful controller replies this boot
    controllerCostUsd: 0,           // running $ spend for this boot
    controllerConsecutiveEmpty: 0,  // ticks in a row where buttons=[]
    controllerPaused: false,        // auto-pause flag when stuck
    lastActions: [],            // ring of last N {buttons, hold_ms} for prompt
    activeHolds: new Map(),     // button name → scheduled release timestamp

    // ── Audio pipeline ─────────────────────────────────────────────
    // jsnes produces ~44100Hz samples (two floats per tick) via the
    // onAudioSample callback. We buffer them in a ring and pump into a
    // WebAudio ScriptProcessorNode that WebAudio pulls at its own
    // sample-rate. Ring is oversized (~4× bufferSize) so the emulator
    // can be ahead or behind without glitching.
    audioCtx: null,
    audioNode: null,
    audioRingL: null,
    audioRingR: null,
    audioWritePos: 0,
    audioReadPos: 0,
    audioMuted: false,
};

function nesSetOverlay(msg, visible = true) {
    const el = document.getElementById("nes-overlay");
    const msgEl = document.getElementById("nes-overlay-msg");
    if (!el || !msgEl) return;
    if (msg) msgEl.innerHTML = msg;
    el.classList.toggle("hidden", !visible);
}

function nesSetCoachText(text, kind = "") {
    const body = document.getElementById("nes-coach-body");
    if (!body) return;
    body.textContent = text || "—";
    body.className = "nes-coach-body" + (kind ? " " + kind : "");
}

function nesSetCoachMeta(text) {
    const el = document.getElementById("nes-coach-meta");
    if (el) el.textContent = text || "—";
}

async function nesLoadRomList() {
    const sel = document.getElementById("nes-rom-select");
    if (!sel) return;
    try {
        const resp = await fetchJson("/api/nes/roms");
        nesState.roms = resp.roms || [];
        sel.innerHTML = "";
        if (!nesState.roms.length) {
            const opt = document.createElement("option");
            opt.value = "";
            opt.textContent = "No ROMs found in FORGE_NES_ROMS_DIR";
            sel.appendChild(opt);
            sel.disabled = true;
            return;
        }
        // Pick a handful of famous titles to the top if present
        const priorityTitles = /super mario|zelda|metroid|castlevania|contra|mega man|punch-?out|tetris/i;
        const prio = nesState.roms.filter(r => priorityTitles.test(r.title));
        const rest = nesState.roms.filter(r => !priorityTitles.test(r.title));
        if (prio.length) {
            const gHot = document.createElement("optgroup");
            gHot.label = "★ Classics";
            prio.slice(0, 40).forEach(r => gHot.appendChild(nesRomOption(r)));
            sel.appendChild(gHot);
        }
        const gAll = document.createElement("optgroup");
        gAll.label = `All ROMs (${rest.length})`;
        // Cap the list — 1700+ ROMs blows the <select> to uselessness.
        rest.slice(0, 400).forEach(r => gAll.appendChild(nesRomOption(r)));
        sel.appendChild(gAll);
        sel.disabled = false;
    } catch (e) {
        sel.innerHTML = `<option value="">Failed to load ROM library: ${escapeHtml(e.message)}</option>`;
        sel.disabled = true;
    }
}

function nesRomOption(r) {
    const opt = document.createElement("option");
    opt.value = r.slug;
    opt.textContent = `${r.title}  (${(r.size_bytes / 1024).toFixed(0)}KB)`;
    return opt;
}

function nesPopulateCoachModels() {
    const sel = document.getElementById("nes-coach-model");
    if (!sel || sel.options.length) return;
    const models = state.models || [];
    const grouped = {};
    for (const m of models) {
        if (m.id === "auto") continue;
        (grouped[m.provider || "Other"] ||= []).push(m);
    }
    sel.innerHTML = "";
    // Default preference order: newest vision Claude → Grok reasoning → others
    const preferred = [
        "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
        "grok-4.20-0309-reasoning",
        "gpt-5.4", "gpt-4o",
        "claude-haiku-4-5-20251001",
    ];
    let defaultIdx = -1;
    let flatIdx = 0;
    for (const [provider, list] of Object.entries(grouped)) {
        const g = document.createElement("optgroup");
        g.label = provider;
        list.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m.id;
            opt.textContent = m.label || m.id;
            g.appendChild(opt);
            if (defaultIdx < 0 && preferred.includes(m.id)) defaultIdx = flatIdx;
            flatIdx++;
        });
        sel.appendChild(g);
    }
    if (defaultIdx >= 0) sel.selectedIndex = defaultIdx;
}

// jsnes rendering — onFrame receives a 256×240 Uint32 buffer where each
// pixel is packed as 0x00BBGGRR (little-endian RGBA with A=0). Canvas
// ImageData wants 0xFFBBGGRR so we OR the alpha byte on per pixel.
// Without this every pixel renders fully transparent → canvas appears
// pure black while emulation proceeds invisibly in the background.
function nesOnFrame(framebuffer) {
    if (!nesState.imageData || !nesState.frameBuf) return;
    const fb = nesState.frameBuf;
    for (let i = 0; i < fb.length; i++) {
        fb[i] = framebuffer[i] | 0xFF000000;
    }
    const canvas = document.getElementById("nes-canvas");
    if (canvas) {
        canvas.getContext("2d").putImageData(nesState.imageData, 0, 0);
    }
    nesState.frameN++;

    // FPS EMA
    const now = performance.now();
    if (nesState.fpsLastTs) {
        const dt = now - nesState.fpsLastTs;
        if (dt > 0) {
            const instant = 1000 / dt;
            nesState.fpsEma = nesState.fpsEma ? nesState.fpsEma * 0.9 + instant * 0.1 : instant;
        }
    }
    nesState.fpsLastTs = now;
}

function nesFpsTick() {
    const el = document.getElementById("nes-fps");
    if (el) el.textContent = nesState.fpsEma.toFixed(0);
    const fn = document.getElementById("nes-frame-n");
    if (fn) fn.textContent = nesState.frameN.toString();
    const ct = document.getElementById("nes-ctrl-ms");
    if (ct) {
        if (nesState.controllerPaused) ct.textContent = "PAUSED";
        else ct.textContent = nesState.controllerLatencyMs > 0
            ? `${nesState.controllerLatencyMs}ms` : "—";
    }
    const calls = document.getElementById("nes-ctrl-calls");
    if (calls) calls.textContent = nesState.controllerCalls.toString();
    const cost = document.getElementById("nes-ctrl-cost");
    if (cost) cost.textContent = `$${nesState.controllerCostUsd.toFixed(4)}`;
}

async function nesBoot() {
    if (nesState.nes) { nesStop(); }
    const slug = document.getElementById("nes-rom-select").value;
    if (!slug) { nesSetOverlay("Pick a ROM first.", true); return; }
    const mode = document.getElementById("nes-mode-select").value;
    const coachModel = document.getElementById("nes-coach-model").value;
    const interval = parseInt(document.getElementById("nes-coach-interval").value, 10) || 2500;
    // Controller (LM Studio VLM) config — read once at boot so the user
    // doesn't have to re-boot to change it (actually, the loop reads the
    // fields live on every tick, but keep boot values as fallback).
    nesState.controllerUrl = (document.getElementById("nes-controller-url")?.value
                              || "http://localhost:1234/v1").replace(/\/+$/, "");
    nesState.controllerModel = (document.getElementById("nes-controller-model")?.value
                                || "grok-4-1-fast-non-reasoning").trim();
    nesState.controllerInterval = parseInt(
        document.getElementById("nes-controller-interval")?.value || "1000", 10) || 1000;

    nesSetOverlay("Loading ROM…", true);

    // 1. Fetch ROM bytes
    let romData;
    try {
        const resp = await fetchJson(`/api/nes/rom/${encodeURIComponent(slug)}`);
        if (resp.error) throw new Error(resp.error);
        // jsnes.loadROM wants a binary string (legacy JS idiom).
        const bytes = atob(resp.data_b64);
        romData = bytes;
        nesState.romSlug = resp.slug;
        nesState.romTitle = resp.title;
    } catch (e) {
        nesSetOverlay(`ROM fetch failed: ${escapeHtml(e.message)}`, true);
        return;
    }

    // 2. Create server-side session
    try {
        const sess = await fetchJson("/api/nes/sessions", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                rom_slug: slug, mode,
                coach_model: coachModel || undefined,
                coach_interval_ms: interval,
            }),
        });
        if (sess.error) throw new Error(sess.error);
        nesState.session = sess;
        nesState.mode = mode;
        nesState.coachInterval = interval;
    } catch (e) {
        nesSetOverlay(`Session create failed: ${escapeHtml(e.message)}`, true);
        return;
    }

    // 3. Set up canvas + jsnes
    if (typeof jsnes === "undefined") {
        nesSetOverlay("jsnes library didn't load — check /static/vendor/jsnes.min.js", true);
        return;
    }
    const canvas = document.getElementById("nes-canvas");
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(256, 240);
    nesState.imageData = img;
    nesState.frameBuf = new Uint32Array(img.data.buffer);

    // Initialise WebAudio lazily — browsers require a user gesture to
    // unlock, and clicking "Boot ROM" IS a user gesture, so here is the
    // right spot. Keep the AudioContext across boots so we don't spam
    // "context created" warnings, but rebuild the ring + node each
    // time so stale samples don't leak between games.
    nesSetupAudio();

    nesState.nes = new jsnes.NES({
        onFrame: nesOnFrame,
        onAudioSample: nesOnAudioSample,
        sampleRate: nesState.audioCtx ? nesState.audioCtx.sampleRate : 44100,
    });
    try {
        nesState.nes.loadROM(romData);
    } catch (e) {
        nesSetOverlay(`jsnes loadROM failed: ${escapeHtml(e.message)}`, true);
        nesState.nes = null;
        return;
    }

    nesState.running = true;
    nesState.paused = false;
    nesState.frameN = 0;
    nesState.fpsEma = 0;
    nesState.fpsLastTs = 0;
    nesState.lastTickSentAt = 0;
    nesState.lastCoachAt = 0;
    // Fixed-timestep accumulator state — reset at boot so the emulator
    // doesn't try to "catch up" on stale wall-clock time from an earlier
    // session.
    nesState.emulationAccumulatorMs = 0;
    nesState.lastEmulationTs = 0;
    // Controller state reset
    nesState.controllerInFlight = false;
    nesState.lastControllerAt = 0;
    nesState.controllerLatencyMs = 0;
    nesState.controllerCalls = 0;
    nesState.controllerCostUsd = 0;
    nesState.controllerConsecutiveEmpty = 0;
    nesState.controllerPaused = false;
    nesState.lastActions = [];
    nesState.activeHolds.clear();

    nesSetOverlay("", false);
    nesSetCoachText(`Ready. Coach fires every ${(interval/1000).toFixed(1)}s on ${mode}.`, "");
    nesSetCoachMeta(`${coachModel || "default coach"} · 0ms`);

    // Enable controls
    ["nes-pause-btn","nes-reset-btn","nes-stop-btn","nes-coach-now-btn","nes-note-btn","nes-mute-btn"]
        .forEach(id => { const b = document.getElementById(id); if (b) b.disabled = false; });

    // Auto-tap START 1.5s after boot — covers the near-universal NES
    // pattern where a title screen waits for START to begin gameplay.
    // If the game's already past the title (e.g. save state) this just
    // pauses/unpauses once briefly, which is harmless.
    setTimeout(() => {
        if (!nesState.nes || !nesState.running || nesState.paused) return;
        try {
            nesState.nes.buttonDown(1, NES_BUTTON_CODES.START);
            setTimeout(() => {
                if (nesState.nes) {
                    try { nesState.nes.buttonUp(1, NES_BUTTON_CODES.START); } catch (_) {}
                }
            }, 150);
            // Record this as a synthetic action so the client-side filter
            // doesn't stall Grok from pressing START again in the next tick
            // if a second menu screen appears.
            nesState.lastActions.push({ buttons: ["START"], hold_ms: 150 });
        } catch (_) {}
    }, 1500);

    // Start RAF
    nesRafLoop();
    // Kick the first coach call immediately so we don't stare at a blank panel.
    if (mode !== "human") {
        setTimeout(() => nesAskCoach(true), 800);
    }
}

// ── Audio pipeline ─────────────────────────────────────────────────────
// jsnes fires onAudioSample(l, r) once per audio sample (at whatever
// sampleRate we configured the NES with). We push into a ring buffer
// and let the WebAudio ScriptProcessorNode pull at its own pace.

function nesSetupAudio() {
    if (!nesState.audioCtx) {
        try {
            const AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return;  // no WebAudio — silent playback
            nesState.audioCtx = new AC({ sampleRate: 44100 });
        } catch (e) {
            console.warn("[nes audio] AudioContext failed:", e);
            return;
        }
    }
    // Browser may have auto-suspended the context; resume now that we
    // have a user gesture (the Boot click).
    if (nesState.audioCtx.state === "suspended") {
        nesState.audioCtx.resume().catch(() => {});
    }
    // Tear down the old node so we don't leak handlers between boots
    if (nesState.audioNode) {
        try { nesState.audioNode.disconnect(); } catch (_) {}
        nesState.audioNode.onaudioprocess = null;
        nesState.audioNode = null;
    }
    // Ring sized for ~4× one audio-processor buffer so the emulator can
    // drift ahead/behind without underflowing. bufferSize 1024 = ~23ms
    // per pull on a 44.1kHz context.
    const bufferSize = 1024;
    const ringLen = bufferSize * 4;
    nesState.audioRingL = new Float32Array(ringLen);
    nesState.audioRingR = new Float32Array(ringLen);
    nesState.audioWritePos = 0;
    nesState.audioReadPos = 0;

    // ScriptProcessorNode is deprecated but universally supported and
    // simpler than AudioWorklet for a one-off demo. 0 input channels,
    // 2 output channels (stereo).
    try {
        nesState.audioNode = nesState.audioCtx.createScriptProcessor(bufferSize, 0, 2);
    } catch (e) {
        console.warn("[nes audio] createScriptProcessor failed:", e);
        return;
    }
    nesState.audioNode.onaudioprocess = (ev) => {
        const outL = ev.outputBuffer.getChannelData(0);
        const outR = ev.outputBuffer.getChannelData(1);
        const ringL = nesState.audioRingL;
        const ringR = nesState.audioRingR;
        const len = ringL.length;
        // If the emulator hasn't produced enough samples (underflow),
        // read the last written value to avoid clicks.
        for (let i = 0; i < outL.length; i++) {
            if (nesState.audioMuted || !nesState.running || nesState.paused) {
                outL[i] = 0; outR[i] = 0;
                continue;
            }
            if (nesState.audioReadPos === nesState.audioWritePos) {
                outL[i] = 0; outR[i] = 0;
                continue;
            }
            outL[i] = ringL[nesState.audioReadPos];
            outR[i] = ringR[nesState.audioReadPos];
            nesState.audioReadPos = (nesState.audioReadPos + 1) % len;
        }
    };
    nesState.audioNode.connect(nesState.audioCtx.destination);
}

function nesOnAudioSample(l, r) {
    const ringL = nesState.audioRingL;
    if (!ringL) return;
    const ringR = nesState.audioRingR;
    const len = ringL.length;
    ringL[nesState.audioWritePos] = l;
    ringR[nesState.audioWritePos] = r;
    nesState.audioWritePos = (nesState.audioWritePos + 1) % len;
    // Overflow safety: if the ring is full (write caught up to read),
    // bump the read pointer to drop the oldest sample rather than
    // stalling. Keeps latency bounded.
    if (nesState.audioWritePos === nesState.audioReadPos) {
        nesState.audioReadPos = (nesState.audioReadPos + 1) % len;
    }
}

function nesTeardownAudio() {
    if (nesState.audioNode) {
        try { nesState.audioNode.disconnect(); } catch (_) {}
        nesState.audioNode.onaudioprocess = null;
        nesState.audioNode = null;
    }
    nesState.audioRingL = null;
    nesState.audioRingR = null;
    nesState.audioWritePos = 0;
    nesState.audioReadPos = 0;
    // Keep the AudioContext alive across boots — creating a new one on
    // every Boot ROM click risks exhausting browser context quotas.
}

function nesRafLoop() {
    if (!nesState.running) return;
    const now = performance.now();

    // ── Fixed-timestep emulation tick ──────────────────────────────
    // Advance exactly one NTSC frame per NES_FRAME_MS of real elapsed
    // time. On a 60Hz display that's one tick per RAF; on 144Hz we tick
    // every ~2.4 RAFs. A 100ms safety cap prevents "spiral of death"
    // when the tab was backgrounded and accumulated 60s of unprocessed
    // time — we cap catch-up to ~6 frames instead.
    if (!nesState.paused && nesState.nes) {
        if (nesState.lastEmulationTs) {
            const dt = now - nesState.lastEmulationTs;
            nesState.emulationAccumulatorMs += Math.min(dt, 100);
        }
        nesState.lastEmulationTs = now;
        let framesRun = 0;
        while (nesState.emulationAccumulatorMs >= NES_FRAME_MS && framesRun < 6) {
            try { nesState.nes.frame(); }
            catch (e) { console.warn("[nes] frame error", e); break; }
            nesState.emulationAccumulatorMs -= NES_FRAME_MS;
            framesRun++;
        }
    } else {
        // Paused / not ready — reset the accumulator clock so resuming
        // doesn't unleash a backlog.
        nesState.lastEmulationTs = now;
    }

    nesFpsTick();

    // Heartbeat tick to server: ~0.7 Hz (was 2 Hz — too aggressive on
    // Windows localhost where the Flask dev server is single-threaded
    // and slow coach calls block the whole socket pool. Saw
    // ERR_NO_BUFFER_SPACE when ticks piled up behind a stalled coach.)
    // tickInFlight guard prevents a backed-up queue from growing even
    // when a single tick takes unexpectedly long.
    if (!nesState.tickInFlight && now - nesState.lastTickSentAt > 1500) {
        nesState.lastTickSentAt = now;
        nesState.tickInFlight = true;
        nesSendTick()
            .catch(() => {})
            .finally(() => { nesState.tickInFlight = false; });
    }
    // Coach at the chosen interval (human-only mode skips)
    if (nesState.mode !== "human"
        && !nesState.coachInFlight
        && now - nesState.lastCoachAt > nesState.coachInterval) {
        nesState.lastCoachAt = now;
        nesAskCoach(false).catch(() => {});
    }
    // Fast controller loop — fires only when Grok is actually driving
    // ("grok" mode). Skipped in human / hybrid-coach modes, when the
    // auto-pause flag is set (too many empty replies in a row), and
    // when the provider select is "off".
    if (nesState.mode === "grok"
        && nesState.nes
        && !nesState.controllerInFlight
        && !nesState.controllerPaused
        && now - nesState.lastControllerAt > nesState.controllerInterval) {
        nesState.lastControllerAt = now;
        nesRunControllerTick().catch(() => {});
    }
    // Release any button holds whose duration has elapsed.
    nesReleaseExpiredHolds(now);
    // Clear Red Alert when its window elapses
    const ra = document.getElementById("nes-red-alert");
    if (ra && now > nesState.redAlertUntil) ra.classList.remove("active");

    nesState.rafHandle = requestAnimationFrame(nesRafLoop);
}

async function nesSendTick() {
    const s = nesState.session;
    if (!s) return;
    const canvas = document.getElementById("nes-canvas");
    if (!canvas) return;
    // Downscale the preview to keep POST body small — the coach later
    // asks for a fresh frame so this is just a session heartbeat.
    // Further: skip the frame entirely now that the heartbeat is only
    // every ~1.5s. The coach POSTs its own fresh frame when it fires,
    // so shipping one here is pure redundancy that bloats socket use.
    try {
        // Abort if the tick hangs more than 4s — prevents Chrome's
        // per-origin connection pool from clogging during a Flask stall.
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 4000);
        await fetch(`/api/nes/sessions/${s.id}/tick`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                frame_b64: "",
                frame_n: nesState.frameN,
                state: nesReadGameState(),
            }),
            signal: controller.signal,
        });
        clearTimeout(timeoutId);
    } catch (_) { /* aborts + network errors are silently ignored */ }
}

// Very generic hook — later we can plug per-ROM RAM inspectors here
// (e.g. SMB: lives at $075A). For now just return the frame number.
function nesReadGameState() {
    return { frame: nesState.frameN };
}

async function nesAskCoach(first = false) {
    const s = nesState.session;
    if (!s || nesState.coachInFlight) return;
    nesState.coachInFlight = true;

    const canvas = document.getElementById("nes-canvas");
    let frameB64 = "";
    try { frameB64 = canvas.toDataURL("image/png"); } catch (_) {}

    nesSetCoachText(first ? "Coach booting…" : "Thinking…", "thinking");

    try {
        const resp = await fetchJson(`/api/nes/sessions/${s.id}/coach`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ frame_b64: frameB64 }),
        });
        if (resp.error) {
            nesSetCoachText(resp.error, "danger");
            return;
        }
        const danger = /^danger\b/i.test(resp.plan || "");
        nesSetCoachText(resp.plan || "(no plan)", danger ? "danger" : "");
        nesSetCoachMeta(`${resp.model} · ${resp.ms}ms${resp.used_vision ? " · vision" : " · text"}`);
        if (danger) {
            const ra = document.getElementById("nes-red-alert");
            if (ra) ra.classList.add("active");
            nesState.redAlertUntil = performance.now() + 6000;
        }
    } catch (e) {
        nesSetCoachText(`Coach call failed: ${e.message}`, "danger");
    } finally {
        nesState.coachInFlight = false;
    }
}

// ══════════════════════════════════════════════════════════════════════
// Fast controller loop — LM Studio VLM translates frame + coach plan
// into button presses. Fires at nesState.controllerInterval (default
// 250ms ≈ 4Hz). Direct browser → LM Studio POST; no backend roundtrip.
// ══════════════════════════════════════════════════════════════════════

// jsnes button constants — buttonDown(player, button), player 1=1.
const NES_BUTTON_CODES = {
    A: 0, B: 1, SELECT: 2, START: 3, UP: 4, DOWN: 5, LEFT: 6, RIGHT: 7,
};
const NES_VALID_BUTTONS = new Set(Object.keys(NES_BUTTON_CODES));

// Scale the 256×240 canvas down to a thumbnail the VLM can actually use.
// 128×120 is 4× smaller (16× bytes) and keeps all the pixel-art detail.
function nesDownscaleFrame(sourceCanvas, targetW = 128, targetH = 120) {
    try {
        const off = document.createElement("canvas");
        off.width = targetW; off.height = targetH;
        const ctx = off.getContext("2d");
        ctx.imageSmoothingEnabled = false;  // keep crisp pixel art
        ctx.drawImage(sourceCanvas, 0, 0, targetW, targetH);
        return off.toDataURL("image/png");
    } catch (e) {
        return "";
    }
}

// Very permissive JSON extraction — the model is small and sometimes
// wraps the JSON in markdown, prose, or explanatory text. Grab the
// first {...} we can parse; fall back to "nothing pressed" on failure.
function nesParseControllerReply(text) {
    if (!text) return { buttons: [], hold_ms: 120 };
    // Strip markdown fences if present
    const fence = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
    const body = fence ? fence[1] : text;
    // Try to find the first balanced JSON object
    const objMatch = body.match(/\{[\s\S]*?\}/);
    if (!objMatch) return { buttons: [], hold_ms: 120 };
    try {
        const parsed = JSON.parse(objMatch[0]);
        const rawButtons = Array.isArray(parsed.buttons) ? parsed.buttons : [];
        const buttons = rawButtons
            .map(b => String(b).toUpperCase().trim())
            .filter(b => NES_VALID_BUTTONS.has(b));
        const hold_ms = Math.min(400, Math.max(40,
            parseInt(parsed.hold_ms, 10) || 120));
        return { buttons, hold_ms };
    } catch (_) {
        return { buttons: [], hold_ms: 120 };
    }
}

function nesReleaseExpiredHolds(now) {
    if (!nesState.nes || nesState.activeHolds.size === 0) return;
    for (const [button, releaseAt] of nesState.activeHolds) {
        if (now >= releaseAt) {
            const code = NES_BUTTON_CODES[button];
            if (code !== undefined) {
                try { nesState.nes.buttonUp(1, code); } catch (_) {}
            }
            nesState.activeHolds.delete(button);
        }
    }
}

// Guard against the "game pauses every second" symptom: if the model
// keeps asking for START, we strip it because in ~every NES game START
// is the pause button and repeated presses toggle pause ⇄ unpause.
// The model CAN still press START — just not two ticks in a row, and
// not more than a handful of times per session.
function nesFilterButtons(buttons) {
    if (!buttons.includes("START")) return buttons;
    // Last-press recency: was START in either of the last 2 actions?
    const recentStart = nesState.lastActions.slice(-2)
        .some(a => a.buttons.includes("START"));
    // Session cap: how many total START presses so far?
    const totalStart = nesState.lastActions
        .filter(a => a.buttons.includes("START")).length;
    const SESSION_START_CAP = 4;
    if (recentStart || totalStart >= SESSION_START_CAP) {
        // Keep any other buttons the model wanted — just drop START.
        return buttons.filter(b => b !== "START");
    }
    return buttons;
}

function nesApplyButtons(buttons, hold_ms) {
    if (!nesState.nes) return;
    buttons = nesFilterButtons(buttons);
    if (buttons.length === 0) return;   // all-filtered → no-op tick
    // Release any holds first so each tick starts clean. This matches
    // how a human plays — a new "press" fully replaces the prior one.
    for (const [button, _] of nesState.activeHolds) {
        const code = NES_BUTTON_CODES[button];
        if (code !== undefined) {
            try { nesState.nes.buttonUp(1, code); } catch (_) {}
        }
    }
    nesState.activeHolds.clear();

    const releaseAt = performance.now() + hold_ms;
    for (const button of buttons) {
        const code = NES_BUTTON_CODES[button];
        if (code === undefined) continue;
        try {
            nesState.nes.buttonDown(1, code);
            nesState.activeHolds.set(button, releaseAt);
        } catch (_) {}
    }
}

const NES_CONTROLLER_SYSTEM = (
    "You are the fast controller for an NES player character. On every " +
    "call you see one screenshot plus a coach plan. Output ONE JSON " +
    "object, no prose, no markdown:\n" +
    '  {"buttons":["LEFT"|"RIGHT"|"UP"|"DOWN"|"A"|"B"|"START"|"SELECT"], ' +
    '"hold_ms":INTEGER_40_TO_400}\n\n' +
    "Empty buttons = do nothing this tick. Running right is B+RIGHT. " +
    "Jump = A (hold ~150ms for big jump, ~80ms for short). Avoid enemies.\n\n" +
    "CRITICAL — START BUTTON:\n" +
    "• In gameplay, START *pauses the game*. NEVER press START if you " +
    "see score/lives/character on screen.\n" +
    "• Only press START on an obvious title screen (logo + PRESS START " +
    "text + no HUD).\n" +
    "• If you just pressed START last tick, do NOT press it again.\n\n" +
    "React to what you SEE — don't just echo the coach plan."
);

async function nesRunControllerTick() {
    const canvas = document.getElementById("nes-canvas");
    if (!canvas || !nesState.nes) return;
    const provider = document.getElementById("nes-controller-provider")?.value
                     || "grok";
    if (provider === "off") return;

    nesState.controllerInFlight = true;
    const frameB64 = nesDownscaleFrame(canvas);
    if (!frameB64) { nesState.controllerInFlight = false; return; }

    // Current coach plan lives in the DOM already — read it back so we
    // don't have to plumb it through state.
    const coachPlanEl = document.getElementById("nes-coach-body");
    const coachPlan = (coachPlanEl?.textContent || "").trim().slice(0, 300);
    const recentActions = nesState.lastActions
        .slice(-3)
        .map(a => `[${a.buttons.join("+") || "NONE"}]@${a.hold_ms}ms`)
        .join(" ");
    const model = (document.getElementById("nes-controller-model")?.value
                   || nesState.controllerModel).trim();

    const started = performance.now();
    try {
        let action;
        if (provider === "grok") {
            action = await nesControllerViaGrok({
                model, frameB64, coachPlan, recentActions,
            });
        } else {
            action = await nesControllerViaLMStudio({
                model, frameB64, coachPlan, recentActions,
            });
        }
        nesState.controllerLatencyMs = Math.round(performance.now() - started);
        if (action === null) return;   // error already surfaced
        nesState._controllerWarned = false;

        if (action.buttons.length > 0) {
            nesApplyButtons(action.buttons, action.hold_ms);
        }
        nesState.lastActions.push(action);
        if (nesState.lastActions.length > 6) nesState.lastActions.shift();
    } catch (e) {
        console.warn("[nes controller]", e);
        if (!nesState._controllerWarned) {
            nesState._controllerWarned = true;
            nesSetCoachText(`Controller error: ${e.message}`, "danger");
        }
    } finally {
        nesState.controllerInFlight = false;
    }
}

// Provider: Grok cloud via Forge backend. Fast (~300-800ms), supports
// vision if the model does; the backend route falls back to text-only.
// Tracks cost + call count + "stuck on menu" streaks so we can auto-
// pause when the model keeps returning empty buttons (cheaper than
// letting the user discover the $ drip in their billing dashboard).
async function nesControllerViaGrok({ model, frameB64, coachPlan, recentActions }) {
    const sid = nesState.session?.id;
    if (!sid) return null;
    const resp = await fetch(`/api/nes/sessions/${encodeURIComponent(sid)}/controller`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            model, frame_b64: frameB64, coach_plan: coachPlan,
            recent_actions: recentActions,
        }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.error) {
        if (!nesState._controllerWarned) {
            nesState._controllerWarned = true;
            nesSetCoachText(`Grok controller error (${model}): ${data.error || resp.status}`, "danger");
        }
        return null;
    }
    // Update cost + call counters from the usage block the backend now
    // returns. `session_total_cost_usd` is the authoritative source (it
    // rolls up on the server side), but we also keep a client-side
    // running sum so the HUD updates immediately without waiting for
    // a GET /sessions/<id> round-trip.
    if (data.usage) {
        nesState.controllerCalls += 1;
        if (typeof data.usage.cost_usd === "number") {
            nesState.controllerCostUsd += data.usage.cost_usd;
        }
    }
    const buttons = Array.isArray(data.buttons) ? data.buttons : [];
    // Auto-pause when the model returns empty buttons many ticks in a
    // row — stopping is cheaper than paying round-trips to confirm the
    // screen hasn't changed. Two knobs to keep false positives low:
    //   GRACE_CALLS: first N ticks don't count (warm-up, title screen)
    //   STUCK_THRESHOLD: consecutive empties after the grace period
    const GRACE_CALLS = 2;
    const STUCK_THRESHOLD = 8;
    const pastGrace = nesState.controllerCalls > GRACE_CALLS;
    if (buttons.length === 0 && pastGrace) {
        nesState.controllerConsecutiveEmpty += 1;
        if (nesState.controllerConsecutiveEmpty >= STUCK_THRESHOLD
            && !nesState.controllerPaused) {
            nesState.controllerPaused = true;
            nesSetCoachText(
                `Controller idle for ${STUCK_THRESHOLD} ticks — paused to save tokens ` +
                `(so far: ${nesState.controllerCalls} calls, $${nesState.controllerCostUsd.toFixed(4)}). ` +
                `If the game's on a menu, press ENTER to tap START manually, ` +
                `then click ▶ Resume Ctrl.`,
                ""
            );
            nesSyncControllerPauseUi();
        }
    } else if (buttons.length > 0) {
        nesState.controllerConsecutiveEmpty = 0;
    }
    return { buttons, hold_ms: data.hold_ms || 120 };
}

function nesSyncControllerPauseUi() {
    const btn = document.getElementById("nes-ctrl-resume-btn");
    if (btn) {
        btn.style.display = nesState.controllerPaused ? "" : "none";
    }
}

function nesResumeController() {
    nesState.controllerPaused = false;
    nesState.controllerConsecutiveEmpty = 0;
    nesState._controllerWarned = false;
    nesSetCoachText("Controller resumed.", "");
    nesSyncControllerPauseUi();
}

// Provider: LM Studio via our /api/nes/controller same-origin proxy.
// Gemma 4 and similar reasoning models consume the token budget on
// reasoning before producing output — we bump max_tokens and dig JSON
// out of reasoning_content as a fallback when content is empty.
async function nesControllerViaLMStudio({ model, frameB64, coachPlan, recentActions }) {
    const url = (document.getElementById("nes-controller-url")?.value
                 || nesState.controllerUrl).replace(/\/+$/, "");
    const userText =
        `Coach plan: ${coachPlan || "(none yet — act on screen cues)"}\n` +
        `Your last actions: ${recentActions || "(none)"}\n` +
        `Output ONLY the JSON object now. No thinking, no prose.`;

    const body = {
        model: model,
        messages: [
            { role: "system", content: NES_CONTROLLER_SYSTEM },
            {
                role: "user",
                content: [
                    { type: "image_url", image_url: { url: frameB64 } },
                    { type: "text", text: userText },
                ],
            },
        ],
        // Bumped from 80 → 400 to survive reasoning-model chain-of-
        // thought. At 47 tok/s this is ~8s/tick on Gemma 4 E4B — slow
        // but at least produces output. Users who want speed should
        // pick the Grok provider instead.
        max_tokens: 400,
        temperature: 0.4,
        stream: false,
    };

    const resp = await fetch(`/api/nes/controller`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_url: url, body: body }),
    });
    const data = await resp.json().catch(() => ({}));

    if (!resp.ok || data.error) {
        const msg = data.error?.message || data.error || `HTTP ${resp.status}`;
        if (!nesState._controllerWarned) {
            nesState._controllerWarned = true;
            nesSetCoachText(`LM Studio controller error (${model}): ${msg}. `
                + `Load the model first or switch Controller via = Grok.`, "danger");
        }
        return null;
    }

    const choice = data.choices?.[0]?.message || {};
    // Reasoning-model recovery: some LM Studio models (Gemma 4, Qwen 3,
    // Ministral reasoning) put chain-of-thought in `reasoning_content`
    // and leave `content` empty when they hit max_tokens mid-think. If
    // we see that, scan reasoning_content for a JSON object as a last
    // resort — models often drop "{"buttons":[...]}" near the end of
    // their reasoning even when they never formally finish.
    const reply = (choice.content && choice.content.length > 0)
        ? choice.content
        : (choice.reasoning_content || "");
    const action = nesParseControllerReply(reply);

    // Cost bookkeeping for LM Studio — model is running locally so
    // cost_usd is effectively 0, but we still track call count so the
    // HUD shows "CALLS 47 · COST $0.0000" and the stuck-detection fires
    // on empty outputs the same way Grok does.
    nesState.controllerCalls += 1;
    const GRACE_CALLS = 2;
    const STUCK_THRESHOLD = 8;
    const pastGrace = nesState.controllerCalls > GRACE_CALLS;
    if (action.buttons.length === 0 && pastGrace) {
        nesState.controllerConsecutiveEmpty += 1;
        if (nesState.controllerConsecutiveEmpty >= STUCK_THRESHOLD
            && !nesState.controllerPaused) {
            nesState.controllerPaused = true;
            nesSetCoachText(
                `Controller idle for ${STUCK_THRESHOLD} ticks — paused to save time. ` +
                `Gemma 4 / Qwen / Ministral reasoning variants tend to ` +
                `burn their token budget on chain-of-thought and emit nothing. ` +
                `Switch Controller via = Grok for a faster path, or click ▶ Resume Ctrl.`,
                ""
            );
            nesSyncControllerPauseUi();
        }
    } else if (action.buttons.length > 0) {
        nesState.controllerConsecutiveEmpty = 0;
    }
    return action;
}

function nesPauseToggle() {
    if (!nesState.running) return;
    nesState.paused = !nesState.paused;
    const btn = document.getElementById("nes-pause-btn");
    if (btn) btn.textContent = nesState.paused ? "▶ Resume" : "⏸ Pause";
}

function nesReset() {
    if (!nesState.nes) return;
    try {
        // jsnes exposes .reset on the CPU
        nesState.nes.reset();
        nesState.frameN = 0;
    } catch (e) {
        console.warn("[nes] reset failed", e);
    }
}

function nesStop() {
    // Release any controller-held buttons BEFORE nulling out nes so the
    // buttonUp calls still land on the real instance.
    if (nesState.nes && nesState.activeHolds.size > 0) {
        for (const [button] of nesState.activeHolds) {
            const code = NES_BUTTON_CODES[button];
            if (code !== undefined) {
                try { nesState.nes.buttonUp(1, code); } catch (_) {}
            }
        }
    }
    nesState.activeHolds.clear();
    nesState.running = false;
    nesState.paused = false;
    if (nesState.rafHandle) cancelAnimationFrame(nesState.rafHandle);
    nesState.rafHandle = 0;
    nesTeardownAudio();
    if (nesState.session) {
        fetchJson(`/api/nes/sessions/${nesState.session.id}`, {method:"DELETE"}).catch(()=>{});
    }
    nesState.nes = null;
    nesState.session = null;
    nesSetOverlay("Stopped. Pick a ROM to boot again.", true);
    nesSetCoachText("", "");
    nesSetCoachMeta("—");
    document.getElementById("nes-score").textContent = "-";
    document.getElementById("nes-lives").textContent = "-";
    const ctrl = document.getElementById("nes-ctrl-ms");
    if (ctrl) ctrl.textContent = "—";
    nesState.controllerLatencyMs = 0;
    ["nes-pause-btn","nes-reset-btn","nes-stop-btn","nes-coach-now-btn","nes-note-btn","nes-mute-btn"]
        .forEach(id => { const b = document.getElementById(id); if (b) b.disabled = true; });
}

function nesToggleMute() {
    nesState.audioMuted = !nesState.audioMuted;
    const btn = document.getElementById("nes-mute-btn");
    if (btn) btn.textContent = nesState.audioMuted ? "🔇 Muted" : "🔊 Sound";
}

// ── Theater Mode ──────────────────────────────────────────────────────
// Full-screen zoom on the canvas + coach panel. Hides topbar / footer /
// MCP hub / setup panel. Tries to request browser fullscreen too so the
// address bar gets out of the way on Brave/Chrome. ESC exits. Clicking
// 📺 again (or its renamed sibling ⛶) also exits.
function nesToggleTheater() {
    const isOn = !document.body.classList.contains("nes-theater-mode");
    document.body.classList.toggle("nes-theater-mode", isOn);
    const btn = document.getElementById("nes-theater-btn");
    if (btn) btn.textContent = isOn ? "⛶ Exit Theater" : "📺 Theater";
    try {
        if (isOn && document.documentElement.requestFullscreen) {
            document.documentElement.requestFullscreen().catch(() => {});
        } else if (!isOn && document.fullscreenElement && document.exitFullscreen) {
            document.exitFullscreen().catch(() => {});
        }
    } catch (_) { /* fullscreen is optional, theater class is the real work */ }
}

// If the user hits ESC (which exits browser fullscreen automatically)
// or uses the fullscreen UI to leave, keep our theater class in sync.
document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement
        && document.body.classList.contains("nes-theater-mode")) {
        // Browser dropped fullscreen — sync theater mode off too
        document.body.classList.remove("nes-theater-mode");
        const btn = document.getElementById("nes-theater-btn");
        if (btn) btn.textContent = "📺 Theater";
    }
});

async function nesAddNote() {
    const s = nesState.session;
    if (!s) return;
    const text = prompt("Note to deposit to forge:vault (nes:" + s.rom_slug + "):");
    if (!text) return;
    try {
        const resp = await fetchJson(`/api/nes/sessions/${s.id}/event`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ kind: "note", summary: text, frame_n: nesState.frameN }),
        });
        nesRefreshEvents();
        if (!resp.vault_ok) {
            console.info("[nes] vault deposit skipped:", resp.vault_msg);
        }
    } catch (_) {}
}

async function nesRefreshEvents() {
    const s = nesState.session;
    const el = document.getElementById("nes-events");
    if (!s || !el) return;
    try {
        const summary = await fetchJson(`/api/nes/sessions/${s.id}`);
        const events = summary.events || [];
        if (!events.length) {
            el.innerHTML = `<div class="nes-events-empty">No events yet.</div>`;
            return;
        }
        el.innerHTML = "";
        events.slice().reverse().forEach(ev => {
            const row = document.createElement("div");
            row.className = "nes-event-row " + (ev.kind || "note");
            row.innerHTML = `
                <span class="ev-kind">${escapeHtml(ev.kind || "note")}</span>
                <span class="ev-summary">${escapeHtml(ev.summary)}</span>
                <span class="ev-frame">#${ev.frame_n}</span>
            `;
            el.appendChild(row);
        });
    } catch (_) {}
}

// Keyboard → jsnes controller (Player 1)
const NES_KEYMAP = {
    "ArrowUp":    ["jsnes.Controller.BUTTON_UP", 4],
    "ArrowDown":  ["jsnes.Controller.BUTTON_DOWN", 5],
    "ArrowLeft":  ["jsnes.Controller.BUTTON_LEFT", 6],
    "ArrowRight": ["jsnes.Controller.BUTTON_RIGHT", 7],
    "KeyZ":       ["jsnes.Controller.BUTTON_B", 1],
    "KeyX":       ["jsnes.Controller.BUTTON_A", 0],
    "Enter":      ["jsnes.Controller.BUTTON_START", 3],
    "ShiftLeft":  ["jsnes.Controller.BUTTON_SELECT", 2],
    "ShiftRight": ["jsnes.Controller.BUTTON_SELECT", 2],
};

function nesKeyHandler(evt, down) {
    if (!nesState.nes) return;
    const entry = NES_KEYMAP[evt.code];
    if (!entry) return;
    // Only capture when the NES tab is active — avoid hijacking arrows
    // on other tabs.
    const nesTab = document.getElementById("tab-nes");
    if (!nesTab || !nesTab.classList.contains("active")) return;
    evt.preventDefault();
    const button = entry[1];
    if (down) nesState.nes.buttonDown(1, button);
    else      nesState.nes.buttonUp(1, button);
}

function bindNesUi() {
    const romSel = document.getElementById("nes-rom-select");
    const modeSel = document.getElementById("nes-mode-select");
    const coachSel = document.getElementById("nes-coach-model");
    const intervalSlider = document.getElementById("nes-coach-interval");
    const intervalLabel = document.getElementById("nes-coach-interval-label");
    const startBtn = document.getElementById("nes-start-btn");
    const pauseBtn = document.getElementById("nes-pause-btn");
    const resetBtn = document.getElementById("nes-reset-btn");
    const stopBtn = document.getElementById("nes-stop-btn");
    const coachNowBtn = document.getElementById("nes-coach-now-btn");
    const noteBtn = document.getElementById("nes-note-btn");

    if (startBtn) startBtn.addEventListener("click", nesBoot);
    if (pauseBtn) pauseBtn.addEventListener("click", nesPauseToggle);
    if (resetBtn) resetBtn.addEventListener("click", nesReset);
    if (stopBtn)  stopBtn .addEventListener("click", nesStop);
    if (coachNowBtn) coachNowBtn.addEventListener("click", () => nesAskCoach(false));
    if (noteBtn)  noteBtn .addEventListener("click", nesAddNote);
    const muteBtn = document.getElementById("nes-mute-btn");
    if (muteBtn) muteBtn.addEventListener("click", nesToggleMute);
    const theaterBtn = document.getElementById("nes-theater-btn");
    if (theaterBtn) theaterBtn.addEventListener("click", nesToggleTheater);
    const resumeBtn = document.getElementById("nes-ctrl-resume-btn");
    if (resumeBtn) resumeBtn.addEventListener("click", nesResumeController);

    // On-screen player controller — mousedown = buttonDown, mouseup /
    // mouseleave = buttonUp. Touch events for mobile / tablet parity.
    // contextmenu suppressed so right-click-drag doesn't strand a
    // phantom press. Everything no-ops when the emulator isn't running.
    document.querySelectorAll(".nes-pc-btn").forEach(btn => {
        const name = btn.dataset.btn;
        const code = NES_BUTTON_CODES[name];
        if (code === undefined) return;

        const press = (e) => {
            e.preventDefault();
            if (!nesState.nes) return;
            try { nesState.nes.buttonDown(1, code); } catch (_) {}
            btn.classList.add("pressed");
        };
        const release = (e) => {
            if (e) e.preventDefault?.();
            if (!nesState.nes) {
                btn.classList.remove("pressed");
                return;
            }
            try { nesState.nes.buttonUp(1, code); } catch (_) {}
            btn.classList.remove("pressed");
        };

        btn.addEventListener("mousedown", press);
        btn.addEventListener("mouseup", release);
        btn.addEventListener("mouseleave", release);
        // Touch support — use passive: false so preventDefault stops
        // the browser from firing synthetic mousedown + scrolling.
        btn.addEventListener("touchstart", press, { passive: false });
        btn.addEventListener("touchend", release, { passive: false });
        btn.addEventListener("touchcancel", release);
        btn.addEventListener("contextmenu", (e) => e.preventDefault());
    });

    // Also release every on-screen button if the user drags out of the
    // panel entirely (e.g. alt-tabs while holding RIGHT) — otherwise
    // Link keeps walking forever.
    window.addEventListener("blur", () => {
        document.querySelectorAll(".nes-pc-btn.pressed").forEach(b => {
            const code = NES_BUTTON_CODES[b.dataset.btn];
            if (code !== undefined && nesState.nes) {
                try { nesState.nes.buttonUp(1, code); } catch (_) {}
            }
            b.classList.remove("pressed");
        });
    });

    // ROM dropdown self-heal — if the first fetch failed (server was
    // restarting, network blip, whatever), retry when the user clicks
    // or focuses the select. Avoids the "stuck in error state" case
    // where switching tabs doesn't force a re-fetch.
    if (romSel) {
        const maybeRetry = () => {
            if (!nesState.roms.length) nesLoadRomList();
        };
        romSel.addEventListener("mousedown", maybeRetry);
        romSel.addEventListener("focus", maybeRetry);
    }
    if (intervalSlider) {
        intervalSlider.addEventListener("input", () => {
            const v = parseInt(intervalSlider.value, 10);
            nesState.coachInterval = v;
            if (intervalLabel) intervalLabel.textContent = (v / 1000).toFixed(1) + " s";
        });
    }

    // Controller interval slider — lives separately from the coach slider
    // so the user can tune them independently (coach 2.5s, controller 250ms).
    const ctrlSlider = document.getElementById("nes-controller-interval");
    const ctrlLabel  = document.getElementById("nes-controller-interval-label");
    if (ctrlSlider) {
        ctrlSlider.addEventListener("input", () => {
            const v = parseInt(ctrlSlider.value, 10);
            nesState.controllerInterval = v;
            if (ctrlLabel) {
                ctrlLabel.textContent = v >= 1000
                    ? (v / 1000).toFixed(1) + " s"
                    : v + " ms";
            }
        });
    }

    window.addEventListener("keydown", e => nesKeyHandler(e, true));
    window.addEventListener("keyup",   e => nesKeyHandler(e, false));
}

function resetRunState(mode = modeFromControls()) {
    state.currentTaskCostUsd = 0;
    state.runtimeEvents = [];
    state.run = defaultRunState(mode);
    renderCostMetrics();
    applyRunState();
    renderRuntimeEvents();
    renderAccountabilityChain(null);
}

function applyRunState() {
    els.metaTaskId.textContent = state.run.taskId;
    els.metaMode.textContent = state.run.mode;
    els.metaStep.textContent = state.run.step;
    els.metaDelegatee.textContent = state.run.delegatee;
    els.metaModel.textContent = state.run.model;
    els.metaLatency.textContent = state.run.latency;
    els.metaTrust.textContent = state.run.trust;
    els.metaGuardrails.textContent = String(state.run.guardrails);
    els.metaFirewall.textContent = String(state.run.firewall);
    els.metaTokens.textContent = typeof state.run.tokens === "number"
        ? state.run.tokens.toLocaleString()
        : String(state.run.tokens);
    els.metaHops.textContent = state.run.hops;

    renderVerificationList(state.run.verification || []);
}

function renderVerificationList(items) {
    els.verificationList.innerHTML = "";

    if (!items.length) {
        els.verificationList.innerHTML = "<li>No active step.</li>";
        return;
    }

    items.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = item;
        els.verificationList.appendChild(li);
    });
}

function recordRuntimeEvent(kind, message, detail = "") {
    state.runtimeEvents.unshift({ kind, message, detail });
    state.runtimeEvents = state.runtimeEvents.slice(0, 8);
    renderRuntimeEvents();
}

function renderRuntimeEvents() {
    if (!state.runtimeEvents.length) {
        els.runtimeEvents.className = "event-feed empty-state";
        els.runtimeEvents.textContent = "No guardrail, firewall, or escalation events yet.";
        return;
    }

    els.runtimeEvents.className = "event-feed";
    els.runtimeEvents.innerHTML = state.runtimeEvents.map((event) => `
        <div class="event-row ${escapeHtml(event.kind)}">
            <strong>${escapeHtml(event.message)}</strong>
            ${event.detail ? `<span>${escapeHtml(event.detail)}</span>` : ""}
        </div>
    `).join("");
}

function renderAccountabilityChain(chain) {
    if (!chain || !Array.isArray(chain.chain) || !chain.chain.length) {
        els.accountabilityList.className = "stack-list compact empty-state";
        els.accountabilityList.textContent = "Waiting for a completed task.";
        return;
    }

    els.accountabilityList.className = "stack-list compact";
    els.accountabilityList.innerHTML = chain.chain.map((hop) => `
        <div class="stack-item hop-item">
            <div class="stack-item-head">
                <strong>Hop ${hop.hop}</strong>
                <span class="pill">${escapeHtml(hop.status || "unknown")}</span>
            </div>
            <div class="stack-item-meta">${escapeHtml(`${hop.delegator} -> ${hop.delegatee}`)}</div>
            <div class="stack-item-sub">${escapeHtml(hop.duration_s != null ? `${hop.duration_s}s` : "in progress")}${hop.error ? ` | ${escapeHtml(hop.error)}` : ""}</div>
        </div>
    `).join("");
}

async function submitTask() {
    const task = els.taskInput.value.trim();
    if (!task || state.isRunning) return;

    state.isArenaMode = false;
    applyWorkspaceMode();
    els.arenaSetup.classList.add("hidden");
    resetRunState(modeFromControls());
    state.run.model = shortModelName(els.modelSelect.value || state.config?.defaults?.executor_model || "-");
    applyRunState();

    setRunning(true);
    updateStatus("Submitting Task", true);
    addMessage("user", task);
    scrollToBottom(true);
    els.taskInput.value = "";

    try {
        const payload = {
            task,
            sandbox_mode: els.sandboxToggle.checked,
            sandbox_path: els.sandboxPath.value.trim(),
            direct_mode: els.directToggle.checked,
            agent_count: Number.parseInt(els.agentSlider.value, 10),
            executor_model: els.modelSelect.value,
            pack: els.packSelect.value,
        };

        const response = await fetchJson("/api/task", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        if (response.error) {
            addMessage("error", response.error);
            setRunning(false);
            return;
        }

        state.currentTaskId = response.task_id;
        state.run.taskId = response.task_id;
        applyRunState();
        streamTask(response.task_id);
    } catch (error) {
        addMessage("error", `Connection failed: ${error.message}`);
        setRunning(false);
    }
}

async function killTask() {
    if (!state.currentTaskId) return;

    els.killBtn.disabled = true;
    els.killBtn.textContent = "Killing...";

    try {
        await fetch(`/api/kill/${state.currentTaskId}`, { method: "POST" });
    } catch (error) {
        addMessage("error", `Failed to send kill signal: ${error.message}`);
    }
}

function streamTask(taskId) {
    const source = new EventSource(`/api/stream/${taskId}`);
    let activeBuffer = "";
    let activeElement = null;
    let streamFinished = false;

    source.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.final) {
            streamFinished = true;
            source.close();
            finishRun();
            return;
        }

        switch (msg.type) {
            case "status":
                updateStatus(msg.content || "Running", true);
                addMessage("status", msg.content || "Status update", { extraClass: msg.phase || "" });
                break;

            case "plan_content":
                if (!activeElement || !activeElement.classList.contains("plan")) {
                    activeBuffer = "";
                    activeElement = addMessage("plan", "", { markdown: true });
                }
                activeBuffer += msg.content || "";
                activeElement.innerHTML = renderMarkdown(activeBuffer);
                scrollToBottom();
                break;

            case "step_start":
                activeBuffer = "";
                activeElement = null;
                state.run.step = `Step ${msg.step}`;
                state.run.delegatee = msg.delegatee || "default";
                state.run.verification = Array.isArray(msg.verification_criteria) ? msg.verification_criteria : [];
                applyRunState();
                addStepStartMessage(msg);
                break;

            case "tool_call":
                addMessage("tool-call", `<div class="message-title">${escapeHtml(msg.name)}</div><pre>${escapeHtml(JSON.stringify(msg.args || {}, null, 2))}</pre>`, { html: true });
                break;

            case "tool_result":
                addMessage("tool-result", `<pre>${escapeHtml(msg.result || "")}</pre>`, { html: true });
                break;

            case "content":
                if (!activeElement || activeElement.classList.contains("plan") || activeElement.classList.contains("step-card")) {
                    activeBuffer = "";
                    activeElement = addMessage("response", "", { markdown: true });
                }
                activeBuffer += msg.content || "";
                activeElement.innerHTML = renderMarkdown(activeBuffer);
                scrollToBottom();
                break;

            case "step_done":
                handleStepDone(msg);
                break;

            case "guardrail_violation":
                state.run.guardrails = String(Number.parseInt(state.run.guardrails, 10) + 1);
                applyRunState();
                recordRuntimeEvent(`guardrail-${msg.severity || "warning"}`, `Guardrail ${msg.guardrail || "violation"}`, msg.message || "");
                addMessage("guardrail", `${msg.severity || "warning"} | ${msg.guardrail || "guardrail"} | ${msg.message || ""}`);
                break;

            case "guardrail_summary":
                state.run.guardrails = String(msg.total_violations || 0);
                applyRunState();
                addMessage("guardrail-summary", `Guardrails: ${msg.total_violations || 0} total | ${msg.blocks || 0} blocks | ${msg.warnings || 0} warnings`);
                break;

            case "firewall_block":
                state.run.firewall = incrementCompositeCount(state.run.firewall);
                applyRunState();
                recordRuntimeEvent("firewall", `Firewall blocked ${msg.tool || "tool"}`, msg.reason || "");
                addMessage("firewall", `${msg.tool || "Tool"} blocked by firewall: ${msg.reason || "unknown reason"}`);
                break;

            case "escalation":
                recordRuntimeEvent("escalation", `Escalated: ${msg.category || "general"}`, msg.reason || "");
                addMessage("escalation", `Escalated to human (${msg.category || "general"}): ${msg.reason || "no reason"}${msg.context ? `\n\n${msg.context}` : ""}`);
                break;

            case "cancelled":
                addMessage("cancelled", msg.content || "Task cancelled");
                break;

            case "error":
                addMessage("error", msg.content || "Unknown error");
                break;

            case "token_usage":
                handleTokenUsage(msg);
                break;

            case "toll_deducted":
                state.sessionTollUsd += msg.toll_usd || 0;
                renderCostMetrics();
                addMessage("toll", `[TOLL] ${formatMoney(msg.toll_usd || 0, 6)} | ${msg.sender || "-"} -> ${msg.receiver || "-"} (${msg.message_type || "message"})`);
                break;

            case "toll_summary":
                addMessage("toll-summary", `Toll summary: ${msg.total_messages || 0} messages | ${formatMoney(msg.total_tolls_usd || 0, 6)} tolls | ${formatMoney(msg.total_creator_revenue_usd || 0, 6)} creator revenue`);
                break;

            case "widget_render":
                renderWidget(msg);
                break;

            case "done":
                handleTaskDone(msg);
                break;
        }
    };

    source.onerror = () => {
        source.close();
        if (!streamFinished) {
            addMessage("error", "Stream disconnected before task completion.");
            finishRun();
        }
    };
}

function addStepStartMessage(msg) {
    const criteria = Array.isArray(msg.verification_criteria) && msg.verification_criteria.length
        ? `<ul>${msg.verification_criteria.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
        : "<p>No verification criteria provided.</p>";

    addMessage(
        "step-card",
        `
            <div class="message-title">Step ${msg.step}: ${escapeHtml(msg.title || "Untitled step")}</div>
            <p>${escapeHtml(msg.description || "")}</p>
            <div class="message-meta">
                <span>${escapeHtml(msg.delegatee || "default")}</span>
                <span>${msg.tools_filtered || 0} tools</span>
                <span>${escapeHtml(msg.contract_id || "no contract")}</span>
            </div>
            ${criteria}
        `,
        { html: true }
    );
}

function handleStepDone(msg) {
    state.run.step = `Step ${msg.step} ${msg.status || ""}`.trim();
    state.run.delegatee = msg.delegatee || state.run.delegatee;
    state.run.latency = msg.latency_s ? `${msg.latency_s}s` : "-";
    state.run.trust = msg.trust_score != null ? Number(msg.trust_score).toFixed(2) : "-";
    applyRunState();

    if (msg.was_reassigned) {
        recordRuntimeEvent("reassigned", `Step ${msg.step} reassigned`, `Executor: ${msg.delegatee || "-"}`);
    }

    addMessage(
        "step-done",
        `Step ${msg.step} ${msg.status || "done"} | ${msg.delegatee || "-"}${msg.latency_s ? ` | ${msg.latency_s}s` : ""}${msg.trust_score != null ? ` | trust ${Number(msg.trust_score).toFixed(2)}` : ""}${msg.was_reassigned ? " | reassigned" : ""}`
    );
}

function handleTokenUsage(msg) {
    state.currentTaskCostUsd += msg.cost_usd || 0;
    state.sessionCostUsd += msg.cost_usd || 0;
    state.run.tokens += (msg.input_tokens || 0) + (msg.output_tokens || 0);
    state.run.model = shortModelName(msg.model || state.run.model);
    renderCostMetrics();
    applyRunState();
}

function handleTaskDone(msg) {
    if (msg.summary) {
        addMessage("done", `${msg.summary}${state.currentTaskCostUsd > 0 ? ` | ${formatMoney(state.currentTaskCostUsd, 4)}` : ""}`);
    }

    if (msg.accountability_chain) {
        state.run.accountability = msg.accountability_chain;
        state.run.hops = String(msg.accountability_chain.total_hops || 0);
        renderAccountabilityChain(msg.accountability_chain);
    }

    if (msg.firewall) {
        state.run.firewall = `${msg.firewall.blocked || 0}/${msg.firewall.total_checks || 0}`;
    }

    if (msg.kernel?.session_tokens) {
        state.run.tokens = msg.kernel.session_tokens.total || state.run.tokens;
    }

    applyRunState();
}

function finishRun() {
    state.currentTaskId = null;
    setRunning(false);
    loadSessionCost();
    loadHistory();
    loadMemory();
}

function setRunning(running) {
    state.isRunning = running;
    updateControlState();
    applyWorkspaceMode();

    if (running) {
        els.killBtn.textContent = "Kill";
        els.killBtn.disabled = false;
    }
}

function updateStatus(text, running = false) {
    els.status.textContent = text;
    els.status.className = `status-pill${running ? " running" : ""}`;
}

function addMessage(type, content, options = {}) {
    const div = document.createElement("div");
    const extraClass = options.extraClass ? ` ${options.extraClass}` : "";
    div.className = `msg ${type}${extraClass}`;

    if (options.markdown) {
        div.innerHTML = renderMarkdown(content);
    } else if (options.html) {
        div.innerHTML = content;
    } else {
        div.textContent = content;
    }

    // Route to correct pane
    const target = (TECHNICAL_TYPES.has(type) && els.messagesTechnical)
        ? els.messagesTechnical : els.messages;
    target.appendChild(div);
    scrollToBottom(false, target);
    return div;
}

// ── Generative UI — Widget Rendering ─────────────────────────────────────────

function renderWidget(msg) {
    const container = document.createElement("div");
    container.className = "msg widget-msg";
    container.dataset.widgetId = msg.widget_id || "";
    container.dataset.widgetType = msg.widget_type || "custom";

    // Widget header with type badge
    const header = document.createElement("div");
    header.className = "widget-header";
    header.innerHTML = `
        <span class="widget-badge">${escapeHtml(msg.widget_type || "widget")}</span>
        <span class="widget-title-text">${escapeHtml(msg.title || "Widget")}</span>
        <div class="widget-actions">
            <button class="widget-action-btn widget-expand-btn" title="Expand widget">&#x26F6;</button>
            <button class="widget-action-btn widget-reload-btn" title="Reload widget">&#x21BB;</button>
        </div>
    `;
    container.appendChild(header);

    // Description if present
    if (msg.description) {
        const desc = document.createElement("div");
        desc.className = "widget-description";
        desc.textContent = msg.description;
        container.appendChild(desc);
    }

    // Sandboxed iframe
    const iframe = document.createElement("iframe");
    iframe.className = "widget-iframe";
    iframe.sandbox = "allow-scripts allow-popups";
    iframe.style.width = msg.width || "100%";
    iframe.style.height = msg.height || "400px";
    iframe.srcdoc = msg.html || "<p>Empty widget</p>";
    iframe.title = msg.title || "Forge Widget";
    container.appendChild(iframe);

    // Wire up expand/reload buttons
    const expandBtn = header.querySelector(".widget-expand-btn");
    const reloadBtn = header.querySelector(".widget-reload-btn");

    expandBtn.addEventListener("click", () => {
        container.classList.toggle("widget-expanded");
        if (container.classList.contains("widget-expanded")) {
            iframe.style.height = "80vh";
            expandBtn.innerHTML = "&#x2716;";  // × close
        } else {
            iframe.style.height = msg.height || "400px";
            expandBtn.innerHTML = "&#x26F6;";  // expand
        }
    });

    reloadBtn.addEventListener("click", () => {
        iframe.srcdoc = msg.html || "<p>Empty widget</p>";
    });

    // Listen for widget → agent messages
    const widgetId = msg.widget_id;
    window.addEventListener("message", (e) => {
        if (e.data && e.data.source === "forge-widget" && e.data.widgetId === widgetId) {
            console.log("[Forge Widget Event]", e.data.event, e.data.data);
            // Future: relay to backend for agent processing
        }
    });

    els.messages.appendChild(container);
    scrollToBottom(true);
}

function renderMarkdown(text) {
    if (typeof marked !== "undefined") {
        marked.setOptions({ breaks: true, mangle: false, headerIds: false });
        return marked.parse(text || "");
    }
    return escapeHtml(text || "").replace(/\n/g, "<br>");
}

function isNearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

function scrollToBottom(force = false, target = null) {
    if (target) {
        if (force || isNearBottom(target)) target.scrollTop = target.scrollHeight;
    } else {
        [els.messages, els.messagesTechnical].forEach(el => {
            if (el && (force || isNearBottom(el))) el.scrollTop = el.scrollHeight;
        });
    }
}

function modeFromControls() {
    return els.directToggle.checked ? "Direct" : "Planner";
}

const COLLAB_SCENARIOS = new Set(["pair_prog", "story_time", "startup", "world_build", "hackathon"]);
const SWARM_SCENARIOS = new Set(["swarm_wars", "influence_ops", "market_crash", "civilization", "memetic_war"]);

function openArenaSetup() {
    if (state.isRunning) return;
    els.arenaSetup.classList.toggle("hidden");
}

function updateArenaSetupCopy() {
    const scenarioSelect = document.getElementById("arena-scenario");
    const val = scenarioSelect?.value;
    const isCollab = COLLAB_SCENARIOS.has(val);
    const isSwarm = SWARM_SCENARIOS.has(val);
    const copy = els.arenaSetup.querySelector(".arena-copy");
    const goBtn = document.getElementById("arena-go-btn");
    if (copy) {
        copy.querySelector("strong").textContent = isSwarm ? "CASS — SWARM WARS"
            : isCollab ? "THE FORGE STUDIO" : "THE FORGE ARENA";
        copy.querySelector("span").textContent = isSwarm
            ? "Two AI societies clash. Agents spy, sabotage, recruit, and wage war."
            : isCollab
            ? "Two AI collaborators enter. Something beautiful (maybe) leaves."
            : "Two AI gladiators enter. One leaves victorious. Zeus judges all.";
    }
    if (goBtn) goBtn.textContent = isSwarm ? "WAR" : isCollab ? "BUILD" : "FIGHT";
}

async function startArena() {
    if (state.isRunning) return;

    els.arenaSetup.classList.add("hidden");
    state.isArenaMode = true;
    const scenarioSelect = document.getElementById("arena-scenario");
    state.isCollabMode = COLLAB_SCENARIOS.has(scenarioSelect?.value);
    state.isSwarmMode = SWARM_SCENARIOS.has(scenarioSelect?.value);
    applyWorkspaceMode();
    resetArenaUI();
    const modeName = state.isSwarmMode ? "CASS" : state.isCollabMode ? "Studio" : "Arena";
    resetRunState(modeName);
    state.run.model = state.isSwarmMode
        ? `Red Swarm vs Blue Swarm`
        : state.isCollabMode
        ? `${shortModelName(els.redModel.value)} + ${shortModelName(els.blueModel.value)}`
        : `${shortModelName(els.redModel.value)} vs ${shortModelName(els.blueModel.value)}`;
    applyRunState();
    setRunning(true);
    updateStatus(state.isSwarmMode ? "Deploying Swarms" : state.isCollabMode ? "Launching Studio" : "Launching Arena", true);

    try {
        const scenarioSelect = document.getElementById("arena-scenario");
        const response = await fetchJson("/api/arena", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                red_model: els.redModel.value,
                blue_model: els.blueModel.value,
                scenario: scenarioSelect ? scenarioSelect.value : "classic",
            }),
        });

        if (response.error) {
            addArenaCommentary(`ERROR: ${response.error}`);
            setRunning(false);
            return;
        }

        state.currentTaskId = response.task_id;
        state.run.taskId = response.task_id;
        applyRunState();
        streamArena(response.task_id);
    } catch (error) {
        addArenaCommentary(`Connection failed: ${error.message}`);
        setRunning(false);
    }
}

function switchToConsole() {
    if (state.isRunning) return;
    state.isArenaMode = false;
    applyWorkspaceMode();
    stopTTS();
}

function resetArenaUI() {
    const scenarioSelect = document.getElementById("arena-scenario");
    const isCollab = COLLAB_SCENARIOS.has(scenarioSelect?.value);
    const isSwarm = SWARM_SCENARIOS.has(scenarioSelect?.value);
    els.commentaryText.textContent = isSwarm
        ? "CASS initializing. Two societies will clash."
        : isCollab
        ? "The Muses gather. Choose your collaborators."
        : "The gods grow restless. Choose your fighters.";
    els.roundLabel.textContent = "Ready";
    els.redLog.textContent = "";
    els.blueLog.textContent = "";
    els.scoreRed.style.width = "0%";
    els.scoreBlue.style.width = "0%";
    els.scoreRedNum.textContent = "0";
    els.scoreBlueNum.textContent = "0";
    stopTTS();
}

function streamArena(taskId) {
    const source = new EventSource(`/api/stream/${taskId}`);
    let commentaryBuffer = "";
    let streamFinished = false;

    source.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.final) {
            streamFinished = true;
            source.close();
            state.currentTaskId = null;
            setRunning(false);
            applyWorkspaceMode();
            updateStatus(state.isCollabMode ? "Studio Complete" : "Arena Complete", false);
            return;
        }

        switch (msg.type) {
            case "arena_status":
                addArenaCommentary(msg.content || "");
                break;

            case "arena_round_start":
                commentaryBuffer = "";
                els.roundLabel.textContent = `Round ${msg.round}: ${msg.name}`;
                addArenaCommentary(`--- Round ${msg.round}: ${msg.name} ---`);
                flushSpeechBuffer();
                speakText(`Round ${msg.round}. ${msg.name}`);
                break;

            case "arena_team_action": {
                const target = msg.team === "red" ? els.redLog : els.blueLog;
                const line = msg.action_type === "content"
                    ? (msg.content || "")
                    : `[${msg.action_type || "event"}] ${msg.content || ""}\n`;
                target.textContent += line;
                target.scrollTop = target.scrollHeight;
                break;
            }

            case "arena_commentary":
                commentaryBuffer += msg.content || "";
                els.commentaryText.textContent = commentaryBuffer;
                els.commentaryText.parentElement.scrollTop = els.commentaryText.parentElement.scrollHeight;
                bufferAndSpeak(msg.content || "");
                break;

            case "arena_scores":
                els.scoreRedNum.textContent = String(msg.red_total || 0);
                els.scoreBlueNum.textContent = String(msg.blue_total || 0);
                els.scoreRed.style.width = `${Math.min(100, ((msg.red_total || 0) / 160) * 100)}%`;
                els.scoreBlue.style.width = `${Math.min(100, ((msg.blue_total || 0) / 160) * 100)}%`;
                addArenaCommentary(`Score update: Red +${msg.red_score || 0} (${msg.red_total || 0}) | Blue +${msg.blue_score || 0} (${msg.blue_total || 0})`);
                break;

            case "arena_result":
                flushSpeechBuffer();
                if (state.isCollabMode) {
                    const combined = (msg.red_total || 0) + (msg.blue_total || 0);
                    els.roundLabel.textContent = "Project Complete";
                    addArenaCommentary(`Final result: Team score ${combined} | Alpha ${msg.red_total || 0} | Beta ${msg.blue_total || 0}`);
                } else {
                    els.roundLabel.textContent = msg.winner === "tie"
                        ? "Tie"
                        : `${capitalize(msg.winner || "winner")} Wins`;
                    addArenaCommentary(`Final result: ${msg.winner || "unknown"} | Red ${msg.red_total || 0} | Blue ${msg.blue_total || 0}`);
                }
                break;

            case "error":
                addArenaCommentary(`ERROR: ${msg.content || "Unknown error"}`);
                break;
        }
    };

    source.onerror = () => {
        source.close();
        if (!streamFinished) {
            addArenaCommentary("Arena stream disconnected before completion.");
            setRunning(false);
            applyWorkspaceMode();
        }
    };
}

function addArenaCommentary(text) {
    els.commentaryText.textContent += `${text}\n`;
    els.commentaryText.parentElement.scrollTop = els.commentaryText.parentElement.scrollHeight;
}

function initTTS() {
    if (!("speechSynthesis" in window)) return;

    const saved = localStorage.getItem("forge_arena_tts");
    els.ttsToggle.checked = saved === "true";
    state.ttsEnabled = els.ttsToggle.checked;

    els.ttsToggle.addEventListener("change", () => {
        state.ttsEnabled = els.ttsToggle.checked;
        localStorage.setItem("forge_arena_tts", String(state.ttsEnabled));
        if (!state.ttsEnabled) stopTTS();
    });

    const pickVoice = () => {
        const voices = speechSynthesis.getVoices();
        if (!voices.length) return;

        state.ttsVoice = voices.find((voice) => voice.lang.startsWith("en") && voice.name.includes("Google"))
            || voices.find((voice) => voice.lang.startsWith("en") && voice.name.includes("Microsoft"))
            || voices.find((voice) => voice.lang.startsWith("en"))
            || voices[0];
    };

    speechSynthesis.onvoiceschanged = pickVoice;
    pickVoice();
}

function speakText(text) {
    if (!state.ttsEnabled || !("speechSynthesis" in window) || !text.trim()) return;

    // Chrome bug: speechSynthesis silently dies after ~15s of continuous speech.
    // Split long text into short chunks and re-poke the synth to keep it alive.
    const MAX_CHARS = 200;
    const chunks = [];
    let remaining = text.trim();
    while (remaining.length > MAX_CHARS) {
        let cut = remaining.lastIndexOf(" ", MAX_CHARS);
        if (cut <= 0) cut = MAX_CHARS;
        chunks.push(remaining.slice(0, cut));
        remaining = remaining.slice(cut).trimStart();
    }
    if (remaining) chunks.push(remaining);

    for (const chunk of chunks) {
        const utterance = new SpeechSynthesisUtterance(chunk);
        if (state.ttsVoice) utterance.voice = state.ttsVoice;
        utterance.rate = 1.05;
        utterance.pitch = 1.0;
        speechSynthesis.speak(utterance);
    }
}

// Chrome workaround: periodically resume speechSynthesis to prevent silent stall
setInterval(() => {
    if (speechSynthesis.speaking && !speechSynthesis.paused) {
        speechSynthesis.pause();
        speechSynthesis.resume();
    }
}, 10000);

function bufferAndSpeak(chunk) {
    if (!state.ttsEnabled) return;

    state.ttsBuffer += chunk;
    // Split on sentence boundaries for natural pauses
    const sentences = state.ttsBuffer.split(/(?<=[.!?\n])\s+/);
    if (sentences.length > 1) {
        const speakable = sentences.slice(0, -1).join(" ").trim();
        state.ttsBuffer = sentences[sentences.length - 1];
        if (speakable) speakText(speakable);
    }
}

function flushSpeechBuffer() {
    if (state.ttsBuffer.trim()) {
        speakText(state.ttsBuffer.trim());
    }
    state.ttsBuffer = "";
}

function stopTTS() {
    state.ttsBuffer = "";
    if ("speechSynthesis" in window) {
        speechSynthesis.cancel();
    }
}

function shortModelName(modelId) {
    if (!modelId) return "-";
    const match = state.models.find((model) => model.id === modelId);
    return match ? match.label : modelId;
}

function isPlannedTask(task) {
    return task?.plan_raw && !String(task.plan_raw).startsWith("(direct mode");
}

function incrementCompositeCount(value) {
    if (typeof value === "string" && value.includes("/")) {
        const [blocked, total] = value.split("/").map((part) => Number.parseInt(part, 10) || 0);
        return `${blocked + 1}/${total + 1}`;
    }

    const numeric = Number.parseInt(value, 10) || 0;
    return String(numeric + 1);
}

function formatMoney(value, decimals = 2) {
    return `$${Number(value || 0).toFixed(decimals)}`;
}

function formatCompactMoney(value) {
    return value ? `$${Number(value).toFixed(2)}` : "$0";
}

function formatTimestamp(value) {
    if (!value) return "Unknown time";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString([], {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

function truncate(value, maxLength) {
    if (!value || value.length <= maxLength) return value || "";
    return `${value.slice(0, maxLength - 3)}...`;
}

function truncateMiddle(value, maxLength) {
    if (!value || value.length <= maxLength) return value || "";
    const side = Math.floor((maxLength - 3) / 2);
    return `${value.slice(0, side)}...${value.slice(-side)}`;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function capitalize(value) {
    if (!value) return "";
    return value.charAt(0).toUpperCase() + value.slice(1);
}

// ══════════════════════════════════════════════════════════════════════════
// TRADING TAB
// ══════════════════════════════════════════════════════════════════════════

const tradingState = {
    initialized: false,
    autoRefreshTimer: null,
    currentTicker: "SPY",
    currentExpiry: "",
    chartMode: "line",
    chartTimeframe: "1D",
    priceData: [],
    tradeSide: "buy",
    assetType: "stock",
    providerCaps: null,
    tradingConfig: null,
    pcrHistory: [],
    alerts: [],
    sseSource: null,
    // Crypto sub-tab state
    cryptoInitialized: false,
    activeSubtab: "stocks",
    cryptoTicker: "BTC",
    cryptoSide: "buy",
    cryptoAutoRefreshTimer: null,
    agentRunning: false,
    agentPollTimer: null,
};

function initTrading() {
    if (tradingState.initialized) return;
    tradingState.initialized = true;

    // Sub-tab switching
    document.querySelectorAll(".trading-subtab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".trading-subtab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".trading-pane").forEach(p => p.classList.remove("active"));
            btn.classList.add("active");
            const pane = document.getElementById(`trading-pane-${btn.dataset.subtab}`);
            if (pane) pane.classList.add("active");
            tradingState.activeSubtab = btn.dataset.subtab;
            if (btn.dataset.subtab === "crypto") initCryptoPane();
            if (btn.dataset.subtab === "polymarket") initPolymarketPane();
        });
    });

    // Bind trading events
    const refreshBtn = document.getElementById("trading-refresh-btn");
    const autoRefresh = document.getElementById("trading-auto-refresh");
    const tickerSelect = document.getElementById("trading-ticker");
    const expirySelect = document.getElementById("trading-expiry");
    const providerSelect = document.getElementById("trading-provider");
    const customTicker = document.getElementById("trading-custom-ticker");
    const alertSetBtn = document.getElementById("alert-set-btn");
    const tradeSubmitBtn = document.getElementById("trade-submit-btn");
    const buyBtn = document.getElementById("trade-buy-btn");
    const sellBtn = document.getElementById("trade-sell-btn");
    const orderType = document.getElementById("trade-order-type");
    const priceField = document.getElementById("trade-price-field");

    refreshBtn.addEventListener("click", () => loadPCRData());

    // Market-data provider switch only.
    providerSelect.addEventListener("change", () => {
        fetchTradingCaps();
        loadExpirations();
        loadPCRData();
        // Reset option expirations for new provider
        const optExp = document.getElementById("trade-option-expiry");
        if (optExp) optExp.innerHTML = '<option value="">Select expiry...</option>';
    });

    // Auto-select active provider from backend config
    fetch("/api/trading/config").then(r => r.json()).then(cfg => {
        tradingState.tradingConfig = cfg;
        const defaultInfo = cfg.providers?.[cfg.default_provider];
        if (
            cfg.default_provider &&
            defaultInfo?.configured &&
            defaultInfo?.available !== false &&
            providerSelect.querySelector(`option[value="${cfg.default_provider}"]`)
        ) {
            providerSelect.value = cfg.default_provider;
        }

        for (const [key, info] of Object.entries(cfg.providers || {})) {
            const opt = providerSelect.querySelector(`option[value="${key}"]`);
            if (!opt) continue;
            const usable = info.configured && info.available !== false;
            if (!usable) {
                opt.disabled = true;
                const reason = info.available === false ? "missing dependency" : "not configured";
                if (!opt.textContent.includes(`(${reason})`)) {
                    opt.textContent += ` (${reason})`;
                }
            }
        }

        if (providerSelect.selectedOptions[0]?.disabled) {
            const firstUsable = Array.from(providerSelect.options).find(option => !option.disabled);
            if (firstUsable) providerSelect.value = firstUsable.value;
        }
        fetchTradingCaps(cfg);
        loadExpirations();
        loadPCRData();
        loadTradingAlerts();
        loadPortfolio();
    }).catch(() => {
        fetchTradingCaps();
        loadExpirations();
        loadPCRData();
        loadTradingAlerts();
        loadPortfolio();
    });

    tickerSelect.addEventListener("change", () => {
        tradingState.currentTicker = tickerSelect.value;
        loadExpirations();
        loadPCRData();
    });
    expirySelect.addEventListener("change", () => {
        tradingState.currentExpiry = expirySelect.value;
        loadPCRData();
    });
    customTicker.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            tradingState.currentTicker = customTicker.value.trim().toUpperCase();
            loadExpirations();
            loadPCRData();
        }
    });

    autoRefresh.addEventListener("change", () => {
        if (autoRefresh.checked) {
            tradingState.autoRefreshTimer = setInterval(() => loadPCRData(), 30000);
        } else {
            clearInterval(tradingState.autoRefreshTimer);
        }
    });

    alertSetBtn.addEventListener("click", setTradingAlert);
    tradeSubmitBtn.addEventListener("click", executeTrade);

    buyBtn.addEventListener("click", () => {
        tradingState.tradeSide = "buy";
        buyBtn.classList.add("active");
        sellBtn.classList.remove("active");
    });
    sellBtn.addEventListener("click", () => {
        tradingState.tradeSide = "sell";
        sellBtn.classList.add("active");
        buyBtn.classList.remove("active");
    });

    orderType.addEventListener("change", () => {
        const val = orderType.value;
        const show = val === "limit" || val === "stop" || val === "stop_limit";
        priceField.style.display = show ? "" : "none";
        const priceLabel = document.getElementById("trade-price-label");
        if (priceLabel) {
            priceLabel.textContent = val === "stop" ? "Stop Price" : val === "stop_limit" ? "Stop/Limit" : "Limit Price";
        }
    });

    // Asset type tab switching
    document.querySelectorAll(".trade-asset-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            if (tab.classList.contains("disabled")) return;
            document.querySelectorAll(".trade-asset-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            tradingState.assetType = tab.dataset.asset;
            updateTradeFieldsVisibility();
        });
    });

    // Load option expirations when option expiry dropdown is opened
    const optionExpiry = document.getElementById("trade-option-expiry");
    if (optionExpiry) {
        optionExpiry.addEventListener("focus", () => loadOptionExpirations());
    }

    // Timeframe switching
    document.querySelectorAll(".chart-tf-btns .filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".chart-tf-btns .filter-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            tradingState.chartTimeframe = btn.dataset.tf;
            loadPriceChart();
        });
    });

    // Indicator toggles — re-render chart on change
    document.querySelectorAll(".chart-indicator-toggles input").forEach(cb => {
        cb.addEventListener("change", () => renderTradingChart());
    });

    // Connect SSE stream
    connectTradingStream();
}

async function loadExpirations() {
    const provider = document.getElementById("trading-provider").value;
    try {
        const data = await fetchJson(`/api/trading/expirations/${encodeURIComponent(tradingState.currentTicker)}?provider=${provider}`);
        const select = document.getElementById("trading-expiry");
        select.innerHTML = '<option value="">Nearest</option>';
        (data.expirations || []).forEach(exp => {
            select.innerHTML += `<option value="${escapeHtml(exp)}">${escapeHtml(exp)}</option>`;
        });
    } catch (e) {
        console.warn("Failed to load expirations:", e);
    }
}

async function loadPCRData() {
    const ticker = tradingState.currentTicker;
    const expiry = tradingState.currentExpiry;
    const provider = document.getElementById("trading-provider").value;

    try {
        const params = new URLSearchParams({ provider });
        if (expiry) params.set("expiry", expiry);

        const [pcr, quote] = await Promise.all([
            fetchJson(`/api/trading/pcr/${encodeURIComponent(ticker)}?${params}`),
            fetchJson(`/api/trading/quote/${encodeURIComponent(ticker)}?provider=${provider}`),
        ]);

        // Update metric cards
        document.getElementById("pcr-vol-ratio").textContent = pcr.vol_ratio != null ? pcr.vol_ratio.toFixed(4) : "—";
        document.getElementById("pcr-oi-ratio").textContent = pcr.oi_ratio != null ? pcr.oi_ratio.toFixed(4) : "—";
        document.getElementById("pcr-put-vol").textContent = (pcr.put_vol || 0).toLocaleString();
        document.getElementById("pcr-call-vol").textContent = (pcr.call_vol || 0).toLocaleString();

        const badge = document.getElementById("pcr-sentiment");
        badge.textContent = pcr.sentiment || "—";
        badge.className = `sentiment-badge ${pcr.sentiment || "neutral"}`;

        const priceEl = document.getElementById("pcr-price");
        priceEl.textContent = quote.price ? `$${quote.price.toFixed(2)}` : "—";

        // Update chart title
        const priceStr = quote.price ? `$${quote.price.toFixed(2)}` : '';
        document.getElementById("chart-title").textContent = `${ticker} ${priceStr}`;

        // Add to history for charting
        tradingState.pcrHistory.push({
            timestamp: new Date().toLocaleTimeString(),
            vol_ratio: pcr.vol_ratio,
            oi_ratio: pcr.oi_ratio,
            ticker,
            expiry: pcr.expiry,
        });
        if (tradingState.pcrHistory.length > 100) {
            tradingState.pcrHistory = tradingState.pcrHistory.slice(-100);
        }

        loadPriceChart();
    } catch (e) {
        console.error("Failed to load PCR data:", e);
    }
}

async function loadPriceChart() {
    const ticker = tradingState.currentTicker;
    const tf = tradingState.chartTimeframe || "1D";
    const provider = document.getElementById("trading-provider").value;
    // Detect asset type from ticker, not provider — crypto tickers are short symbols
    const cryptoTickers = ["BTC","ETH","SOL","DOGE","AVAX","LINK","XRP","ADA","SHIB","MATIC","DOT","UNI"];
    const assetType = cryptoTickers.includes(ticker.toUpperCase()) ? "crypto" : "stock";

    try {
        const resp = await fetchJson(`/api/trading/price-history/${encodeURIComponent(ticker)}?timeframe=${tf}&type=${assetType}`);
        tradingState.priceData = resp.candles || [];
        renderTradingChart();
    } catch (e) {
        console.error("Failed to load price history:", e);
    }
}

function renderTradingChart() {
    const container = document.getElementById("trading-chart-container");
    const data = tradingState.priceData || [];

    if (data.length === 0) {
        container.innerHTML = '<div class="empty-state">No data yet. Click Refresh to load.</div>';
        return;
    }

    const showBB = document.getElementById("ind-bb")?.checked ?? true;
    const showVWAP = document.getElementById("ind-vwap")?.checked ?? true;
    const showRSI = document.getElementById("ind-rsi")?.checked ?? true;
    const showMACD = document.getElementById("ind-macd")?.checked ?? false;

    // How many sub-charts do we need?
    const subCharts = [];
    if (showRSI) subCharts.push("rsi");
    if (showMACD) subCharts.push("macd");

    // Domain splits: price chart gets most space, sub-indicators get small strips below
    const subHeight = 0.12;  // each sub-indicator
    const gap = 0.03;
    const subTotal = subCharts.length * (subHeight + gap);
    const priceTop = 1.0;
    const priceBottom = subTotal + (subCharts.length > 0 ? 0.02 : 0);

    const plotlyCode = `
        const data = ${JSON.stringify(data)};
        const times = data.map(d => d.time);
        const traces = [];

        // ── Candlestick ──
        traces.push({
            x: times,
            open: data.map(d => d.open),
            high: data.map(d => d.high),
            low: data.map(d => d.low),
            close: data.map(d => d.close),
            type: 'candlestick',
            name: 'Price',
            increasing: { line: { color: '#26a69a' } },
            decreasing: { line: { color: '#ef5350' } },
            yaxis: 'y',
            showlegend: false,
        });

        ${showBB ? `
        // ── Bollinger Bands ──
        traces.push({
            x: times, y: data.map(d => d.bb_upper),
            type: 'scatter', mode: 'lines', name: 'BB Upper',
            line: { color: 'rgba(156,172,191,0.4)', width: 1 },
            yaxis: 'y', showlegend: false,
        });
        traces.push({
            x: times, y: data.map(d => d.bb_lower),
            type: 'scatter', mode: 'lines', name: 'BB Lower',
            line: { color: 'rgba(156,172,191,0.4)', width: 1 },
            fill: 'tonexty', fillcolor: 'rgba(156,172,191,0.06)',
            yaxis: 'y', showlegend: false,
        });
        traces.push({
            x: times, y: data.map(d => d.bb_mid),
            type: 'scatter', mode: 'lines', name: 'BB Mid',
            line: { color: 'rgba(156,172,191,0.3)', width: 1, dash: 'dot' },
            yaxis: 'y', showlegend: false,
        });
        ` : ''}

        ${showVWAP ? `
        // ── VWAP ──
        traces.push({
            x: times, y: data.map(d => d.vwap),
            type: 'scatter', mode: 'lines', name: 'VWAP',
            line: { color: '#f2a74b', width: 1.5 },
            yaxis: 'y', showlegend: true,
        });
        ` : ''}

        const layout = {
            template: 'plotly_dark',
            paper_bgcolor: '#171f2a',
            plot_bgcolor: '#171f2a',
            margin: { t: 8, r: 50, b: 30, l: 60 },
            showlegend: true,
            legend: { x: 0, y: ${priceTop}, orientation: 'h', font: { size: 10 } },
            xaxis: {
                gridcolor: 'rgba(255,255,255,0.06)',
                rangeslider: { visible: false },
                type: 'date',
            },
            yaxis: {
                domain: [${priceBottom}, ${priceTop}],
                gridcolor: 'rgba(255,255,255,0.06)',
                side: 'right',
            },
        };

        ${showRSI ? `
        // ── RSI subplot ──
        const rsiIdx = ${subCharts.indexOf("rsi")};
        const rsiBottom = ${subCharts.indexOf("rsi")} * ${subHeight + gap};
        const rsiTop = rsiBottom + ${subHeight};
        traces.push({
            x: times, y: data.map(d => d.rsi),
            type: 'scatter', mode: 'lines', name: 'RSI',
            line: { color: '#ab47bc', width: 1.5 },
            yaxis: 'y2', showlegend: true,
        });
        // RSI overbought/oversold
        traces.push({
            x: [times[0], times[times.length-1]],
            y: [70, 70],
            type: 'scatter', mode: 'lines',
            line: { color: 'rgba(239,83,80,0.4)', width: 1, dash: 'dot' },
            yaxis: 'y2', showlegend: false,
        });
        traces.push({
            x: [times[0], times[times.length-1]],
            y: [30, 30],
            type: 'scatter', mode: 'lines',
            line: { color: 'rgba(38,166,154,0.4)', width: 1, dash: 'dot' },
            yaxis: 'y2', showlegend: false,
        });
        layout.yaxis2 = {
            domain: [rsiBottom, rsiTop],
            gridcolor: 'rgba(255,255,255,0.06)',
            range: [0, 100],
            dtick: 30,
            side: 'right',
            title: { text: 'RSI', font: { size: 9 } },
        };
        ` : ''}

        ${showMACD ? `
        // ── MACD subplot ──
        const macdIdx = ${subCharts.indexOf("macd")};
        const macdBottom = macdIdx * ${subHeight + gap};
        const macdTop = macdBottom + ${subHeight};
        traces.push({
            x: times, y: data.map(d => d.macd),
            type: 'scatter', mode: 'lines', name: 'MACD',
            line: { color: '#42a5f5', width: 1.5 },
            yaxis: 'y3', showlegend: true,
        });
        traces.push({
            x: times, y: data.map(d => d.macd_signal),
            type: 'scatter', mode: 'lines', name: 'Signal',
            line: { color: '#ff7043', width: 1.5 },
            yaxis: 'y3', showlegend: true,
        });
        traces.push({
            x: times,
            y: data.map(d => d.macd_hist),
            type: 'bar', name: 'Histogram',
            marker: { color: data.map(d => (d.macd_hist || 0) >= 0 ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)') },
            yaxis: 'y3', showlegend: false,
        });
        layout.yaxis3 = {
            domain: [macdBottom, macdTop],
            gridcolor: 'rgba(255,255,255,0.06)',
            side: 'right',
            title: { text: 'MACD', font: { size: 9 } },
        };
        ` : ''}

        Plotly.newPlot('chart', traces, layout, { responsive: true, displayModeBar: false });
    `;

    const html = `<!DOCTYPE html>
<html><head>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"><\/script>
<style>body{margin:0;background:#171f2a;overflow:hidden}#chart{width:100%;height:100vh}</style>
</head><body>
<div id="chart"></div>
<script>${plotlyCode}<\/script>
</body></html>`;

    container.innerHTML = "";
    const iframe = document.createElement("iframe");
    iframe.srcdoc = html;
    iframe.style.cssText = "width:100%;height:100%;border:none;border-radius:6px;position:absolute;top:0;left:0";
    container.appendChild(iframe);
}

async function setTradingAlert() {
    const ticker = tradingState.currentTicker;
    const metric = document.getElementById("alert-metric").value;
    const threshold = document.getElementById("alert-threshold").value;
    const direction = document.getElementById("alert-direction").value;

    try {
        const result = await fetchJson("/api/trading/alerts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker, metric, threshold: parseFloat(threshold), direction }),
        });
        loadTradingAlerts();
    } catch (e) {
        console.error("Failed to set alert:", e);
    }
}

async function loadTradingAlerts() {
    try {
        const alerts = await fetchJson("/api/trading/alerts");
        tradingState.alerts = alerts;
        const list = document.getElementById("alert-list");
        if (alerts.length === 0) {
            list.innerHTML = '<div class="empty-state" style="font-size:0.75rem;padding:6px">No alerts set.</div>';
            return;
        }
        list.innerHTML = alerts.map(a => `
            <div class="alert-item ${a.triggered ? 'triggered' : ''}">
                <span>${escapeHtml(a.ticker)} ${a.metric} ${a.direction} ${a.threshold}${a.last_value != null ? ` (now: ${a.last_value.toFixed(4)})` : ''}</span>
                <button class="alert-remove" onclick="removeTradingAlert('${a.alert_id}')">&times;</button>
            </div>
        `).join("");
    } catch (e) {
        console.warn("Failed to load alerts:", e);
    }
}

async function removeTradingAlert(alertId) {
    try {
        await fetch(`/api/trading/alerts/${alertId}`, { method: "DELETE" });
        loadTradingAlerts();
    } catch (e) {
        console.error("Failed to remove alert:", e);
    }
}

function updateTradeFieldsVisibility() {
    const asset = tradingState.assetType;
    // Option fields
    document.querySelectorAll(".trade-field-option").forEach(el => {
        el.style.display = asset === "option" ? "" : "none";
    });
    // Crypto fields
    document.querySelectorAll(".trade-field-crypto").forEach(el => {
        el.style.display = asset === "crypto" ? "" : "none";
    });
    // Update qty label
    const qtyLabel = document.getElementById("trade-qty-label");
    if (qtyLabel) {
        qtyLabel.textContent = asset === "option" ? "Contracts" : "Qty";
    }
    // Update quantity step
    const qtyInput = document.getElementById("trade-quantity");
    if (qtyInput) {
        qtyInput.step = asset === "crypto" ? "0.001" : "1";
        qtyInput.min = asset === "crypto" ? "0.0001" : "1";
        if (asset !== "crypto" && parseFloat(qtyInput.value) < 1) qtyInput.value = "1";
    }
}

async function fetchTradingCaps(cfgOverride = null) {
    try {
        const cfg = cfgOverride || await fetchJson("/api/trading/config");
        tradingState.tradingConfig = cfg;
        const provider = cfg.default_provider;
        const executionInfo = cfg.providers?.[provider] || {};
        const caps = executionInfo.capabilities || {};
        tradingState.providerCaps = caps;

        // Enable/disable asset tabs based on provider capabilities
        document.querySelectorAll(".trade-asset-tab").forEach(tab => {
            const asset = tab.dataset.asset;
            let enabled = true;
            if (asset === "stock" && !caps.stocks?.quotes) enabled = false;
            if (asset === "option" && !caps.options?.chains) enabled = false;
            if (asset === "crypto" && !caps.crypto?.quotes) enabled = false;
            tab.classList.toggle("disabled", !enabled);

            // If active tab became disabled, switch to first enabled
            if (!enabled && tab.classList.contains("active")) {
                tab.classList.remove("active");
                const first = document.querySelector(".trade-asset-tab:not(.disabled)");
                if (first) {
                    first.classList.add("active");
                    tradingState.assetType = first.dataset.asset;
                }
            }
        });
        updateTradeFieldsVisibility();

        // Update paper badge
        const badge = document.getElementById("paper-badge");
        if (badge) {
            const isTrade = caps.stocks?.trade || caps.options?.trade || caps.crypto?.trade;
            const readiness = cfg.readiness || {};
            if (cfg.paper_mode) {
                badge.textContent = "PAPER";
                badge.className = "paper-badge";
            } else if (readiness.state === "unavailable") {
                badge.textContent = "UNAVAILABLE";
                badge.className = "paper-badge unavailable";
            } else {
                badge.textContent = isTrade ? "LIVE" : "PAPER";
                badge.className = "paper-badge" + (isTrade ? " live" : "");
            }
        }

        const note = document.getElementById("trade-broker-note");
        const submitBtn = document.getElementById("trade-submit-btn");
        if (note && submitBtn) {
            const selectedDataProvider = document.getElementById("trading-provider")?.value || "";
            const selectedDataInfo = cfg.providers?.[selectedDataProvider] || {};
            const readiness = cfg.readiness || {};
            const executionLabel = executionInfo.label || provider || "Primary broker";
            const dataLabel = selectedDataInfo.label || selectedDataProvider || "Current data provider";
            const readinessIssue = readiness.issues?.[0] || "";

            if (cfg.paper_mode) {
                note.textContent = `Orders are in paper mode. Market data is using ${dataLabel}.`;
                note.className = "trade-broker-note";
                submitBtn.disabled = false;
                submitBtn.title = "";
            } else if (readiness.state === "unavailable") {
                note.textContent = `Orders route via ${executionLabel}, but it is currently unavailable. ${readinessIssue}`;
                note.className = "trade-broker-note error";
                submitBtn.disabled = true;
                submitBtn.title = readinessIssue || `${executionLabel} is unavailable`;
            } else {
                note.textContent = `Orders route via ${executionLabel}. Market data is using ${dataLabel}.`;
                note.className = "trade-broker-note";
                submitBtn.disabled = false;
                submitBtn.title = "";
            }
        }
    } catch (e) {
        console.warn("Failed to fetch trading caps:", e);
    }
}

async function loadOptionExpirations() {
    const provider = document.getElementById("trading-provider").value;
    const ticker = tradingState.currentTicker;
    const select = document.getElementById("trade-option-expiry");
    if (!select || select.options.length > 1) return; // already loaded
    try {
        const data = await fetchJson(`/api/trading/expirations/${encodeURIComponent(ticker)}?provider=${provider}`);
        select.innerHTML = '<option value="">Select expiry...</option>';
        (data.expirations || []).forEach(exp => {
            select.innerHTML += `<option value="${escapeHtml(exp)}">${escapeHtml(exp)}</option>`;
        });
    } catch (e) {
        console.warn("Failed to load option expirations:", e);
    }
}

async function executeTrade() {
    const asset = tradingState.assetType;
    const side = tradingState.tradeSide;
    const quantity = document.getElementById("trade-quantity").value;
    const orderType = document.getElementById("trade-order-type").value;
    const price = document.getElementById("trade-price").value;
    const duration = document.getElementById("trade-duration").value;
    const resultEl = document.getElementById("trade-result");

    // Determine ticker based on asset type
    let ticker;
    if (asset === "crypto") {
        ticker = document.getElementById("trade-crypto-symbol").value;
    } else {
        ticker = tradingState.currentTicker;
    }

    // Build order payload
    const payload = {
        asset_type: asset,
        ticker,
        side,
        quantity: parseFloat(quantity),
        order_type: orderType,
        duration,
    };
    if (price) payload.price = parseFloat(price);

    // Add options fields
    if (asset === "option") {
        payload.expiry = document.getElementById("trade-option-expiry").value;
        payload.strike = parseFloat(document.getElementById("trade-option-strike").value || "0");
        payload.option_type = document.getElementById("trade-option-type").value;

        if (!payload.expiry || !payload.strike) {
            resultEl.textContent = "Select expiry and strike for options order";
            resultEl.className = "trade-result error";
            return;
        }
    }

    // Build confirmation summary
    let summary;
    if (asset === "option") {
        summary = `${side.toUpperCase()} ${quantity} ${ticker} ${payload.expiry} $${payload.strike} ${payload.option_type.toUpperCase()} — ${orderType}`;
    } else if (asset === "crypto") {
        summary = `${side.toUpperCase()} ${quantity} ${ticker} — ${orderType}`;
    } else {
        summary = `${side.toUpperCase()} ${quantity} ${ticker} — ${orderType}`;
    }
    if (price) summary += ` @ $${price}`;

    try {
        const result = await fetchJson("/api/trading/order", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        if (result.error) {
            resultEl.textContent = result.error;
            resultEl.className = "trade-result error";
        } else {
            const msg = result.message || `${summary} — ${result.status || "submitted"}`;
            resultEl.textContent = msg;
            resultEl.className = "trade-result success";
            loadPortfolio();
        }
    } catch (e) {
        resultEl.textContent = `Error: ${e.message}`;
        resultEl.className = "trade-result error";
    }
}

async function loadPortfolio() {
    try {
        const provider = document.getElementById("trading-provider")?.value || "";
        const qs = provider ? `?provider=${encodeURIComponent(provider)}` : "";
        const data = await fetchJson(`/api/trading/portfolio${qs}`);
        document.getElementById("port-count").textContent = data.position_count || 0;
        document.getElementById("port-realized").textContent = formatMoney(data.realized_pnl || 0);
        document.getElementById("port-unrealized").textContent = formatMoney(data.unrealized_pnl || 0);

        const realizedEl = document.getElementById("port-realized");
        realizedEl.className = `pcr-value ${(data.realized_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
        const unrealizedEl = document.getElementById("port-unrealized");
        unrealizedEl.className = `pcr-value ${(data.unrealized_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

        const positionsEl = document.getElementById("portfolio-positions");
        const positions = data.positions || [];
        if (positions.length === 0) {
            positionsEl.innerHTML = '<div class="empty-state" style="font-size:0.75rem;padding:6px">No positions.</div>';
            return;
        }
        positionsEl.innerHTML = `
            <table>
                <thead><tr><th>Ticker</th><th>Qty</th><th>Avg</th><th>Current</th><th>P&L</th></tr></thead>
                <tbody>
                    ${positions.map(p => `
                        <tr>
                            <td>${escapeHtml(p.ticker)}</td>
                            <td>${p.quantity}</td>
                            <td>$${p.avg_price.toFixed(2)}</td>
                            <td>$${p.current_price.toFixed(2)}</td>
                            <td class="${p.unrealized_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">
                                $${p.unrealized_pnl.toFixed(2)} (${p.unrealized_pnl_pct.toFixed(1)}%)
                            </td>
                        </tr>
                    `).join("")}
                </tbody>
            </table>
        `;
    } catch (e) {
        console.warn("Failed to load portfolio:", e);
    }
}

function connectTradingStream() {
    if (tradingState.sseSource) return;
    try {
        tradingState.sseSource = new EventSource("/api/trading/stream");
        tradingState.sseSource.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === "pcr_update") {
                // Auto-update if it's the current ticker
                const d = msg.data;
                if (d.ticker === tradingState.currentTicker) {
                    document.getElementById("pcr-vol-ratio").textContent = d.vol_ratio != null ? d.vol_ratio.toFixed(4) : "—";
                    document.getElementById("pcr-oi-ratio").textContent = d.oi_ratio != null ? d.oi_ratio.toFixed(4) : "—";
                    const badge = document.getElementById("pcr-sentiment");
                    badge.textContent = d.sentiment;
                    badge.className = `sentiment-badge ${d.sentiment}`;
                }
            } else if (msg.type === "alert_triggered") {
                loadTradingAlerts();
                addMessage("info", `Trading Alert: ${msg.data.ticker} ${msg.data.metric} is ${msg.data.current_value?.toFixed(4)} (${msg.data.direction} ${msg.data.threshold})`);
            }
        };
        tradingState.sseSource.onerror = () => {
            tradingState.sseSource.close();
            tradingState.sseSource = null;
            // Reconnect after 5 seconds
            setTimeout(connectTradingStream, 5000);
        };
    } catch (e) {
        console.warn("Failed to connect trading stream:", e);
    }
}

// ══════════════════════════════════════════════════════════════════════════
// CRYPTO PANE
// ══════════════════════════════════════════════════════════════════════════

function initCryptoPane() {
    if (tradingState.cryptoInitialized) return;
    tradingState.cryptoInitialized = true;

    const tickerSel = document.getElementById("crypto-ticker");
    const buyBtn = document.getElementById("crypto-buy-btn");
    const sellBtn = document.getElementById("crypto-sell-btn");
    const submitBtn = document.getElementById("crypto-submit-btn");
    const orderType = document.getElementById("crypto-order-type");
    const priceField = document.getElementById("crypto-price-field");
    const refreshBtn = document.getElementById("crypto-refresh-btn");

    tickerSel.addEventListener("change", () => {
        tradingState.cryptoTicker = tickerSel.value;
        loadCryptoQuote();
        loadCryptoPortfolio();
        const activeTf = document.querySelector("[data-crypto-tf].active");
        loadCryptoChart(activeTf ? activeTf.dataset.cryptoTf : "1D");
    });

    buyBtn.addEventListener("click", () => {
        tradingState.cryptoSide = "buy";
        buyBtn.classList.add("active");
        sellBtn.classList.remove("active");
    });
    sellBtn.addEventListener("click", () => {
        tradingState.cryptoSide = "sell";
        sellBtn.classList.add("active");
        buyBtn.classList.remove("active");
    });

    orderType.addEventListener("change", () => {
        const needsPrice = ["limit", "stop", "stop_limit"].includes(orderType.value);
        priceField.style.display = needsPrice ? "" : "none";
    });

    submitBtn.addEventListener("click", executeCryptoTrade);
    refreshBtn.addEventListener("click", () => {
        loadCryptoQuote();
        loadCryptoPortfolio();
    });

    // Timeframe buttons for crypto chart
    document.querySelectorAll("[data-crypto-tf]").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("[data-crypto-tf]").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            loadCryptoChart(btn.dataset.cryptoTf);
        });
    });

    // Agent button
    const agentBtn = document.getElementById("crypto-agent-btn");
    agentBtn.addEventListener("click", toggleCryptoAgent);

    // Initial load
    loadCryptoQuote();
    loadCryptoPortfolio();
    loadCryptoChart("1D");
    pollAgentStatus();

    // Auto-refresh quotes every 15 seconds, agent status every 5s
    tradingState.cryptoAutoRefreshTimer = setInterval(() => {
        if (tradingState.activeSubtab === "crypto") {
            loadCryptoQuote();
            pollAgentStatus();
        }
    }, 15000);
    tradingState.agentPollTimer = setInterval(() => {
        if (tradingState.activeSubtab === "crypto" && tradingState.agentRunning) {
            pollAgentStatus();
            pollAgentLogs();
        }
    }, 5000);
}

async function loadCryptoQuote() {
    const ticker = tradingState.cryptoTicker;
    try {
        const data = await fetchJson(`/api/trading/quote/${ticker}?provider=robinhood-crypto`);
        document.getElementById("crypto-quote-symbol").textContent = ticker;
        document.getElementById("crypto-chart-title").textContent = `${ticker} — Live`;

        if (data.price && data.price > 0) {
            const priceStr = data.price < 1 ? data.price.toFixed(6) : data.price.toFixed(2);
            document.getElementById("crypto-quote-price").textContent = `$${priceStr}`;

            const changeEl = document.getElementById("crypto-quote-change");
            if (data.change != null && data.change !== 0) {
                const sign = data.change >= 0 ? "+" : "";
                changeEl.textContent = `${sign}$${data.change.toFixed(2)} (${sign}${data.change_pct.toFixed(2)}%)`;
                changeEl.className = `crypto-change ${data.change >= 0 ? "" : "negative"}`;
            } else {
                changeEl.textContent = "—";
            }

            document.getElementById("crypto-quote-volume").textContent =
                data.volume ? `Vol: ${Number(data.volume).toLocaleString()}` : "Vol: —";
        } else {
            document.getElementById("crypto-quote-price").textContent = "—";
        }
    } catch (e) {
        console.warn("Crypto quote failed:", e);
        document.getElementById("crypto-quote-price").textContent = "Error";
    }
}

async function loadCryptoPortfolio() {
    try {
        const data = await fetchJson("/api/trading/portfolio?provider=robinhood-crypto");
        document.getElementById("crypto-port-count").textContent = data.position_count || 0;
        document.getElementById("crypto-port-realized").textContent = formatMoney(data.realized_pnl || 0);
        document.getElementById("crypto-port-unrealized").textContent = formatMoney(data.unrealized_pnl || 0);

        const realizedEl = document.getElementById("crypto-port-realized");
        realizedEl.className = `pcr-value ${(data.realized_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
        const unrealizedEl = document.getElementById("crypto-port-unrealized");
        unrealizedEl.className = `pcr-value ${(data.unrealized_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

        const posEl = document.getElementById("crypto-portfolio-positions");
        const positions = data.positions || [];
        if (positions.length === 0) {
            posEl.innerHTML = '<div class="empty-state" style="font-size:0.75rem;padding:6px">No holdings.</div>';
            return;
        }
        posEl.innerHTML = `
            <table>
                <thead><tr><th>Ticker</th><th>Qty</th><th>Avg</th><th>Current</th><th>P&L</th></tr></thead>
                <tbody>
                    ${positions.map(p => `
                        <tr>
                            <td>${escapeHtml(p.ticker)}</td>
                            <td>${p.quantity}</td>
                            <td>$${p.avg_price < 1 ? p.avg_price.toFixed(6) : p.avg_price.toFixed(2)}</td>
                            <td>$${p.current_price < 1 ? p.current_price.toFixed(6) : p.current_price.toFixed(2)}</td>
                            <td class="${p.unrealized_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">
                                $${p.unrealized_pnl.toFixed(2)} (${p.unrealized_pnl_pct.toFixed(1)}%)
                            </td>
                        </tr>
                    `).join("")}
                </tbody>
            </table>
        `;
    } catch (e) {
        console.warn("Crypto portfolio failed:", e);
    }
}

async function loadCryptoChart(timeframe = "1D") {
    const ticker = tradingState.cryptoTicker;
    const container = document.getElementById("crypto-chart-container");
    container.innerHTML = '<div class="empty-state">Loading chart...</div>';
    try {
        const data = await fetchJson(`/api/trading/crypto/history/${ticker}?timeframe=${timeframe}`);
        if (data.error) {
            container.innerHTML = `<div class="empty-state">Chart error: ${escapeHtml(data.error)}</div>`;
            return;
        }
        if (!data.candles || data.candles.length === 0) {
            container.innerHTML = '<div class="empty-state">No price data available.</div>';
            return;
        }
        renderCryptoChart(container, data.candles, ticker, timeframe);
    } catch (e) {
        console.warn("Crypto chart failed:", e);
        container.innerHTML = `<div class="empty-state">Failed to load chart.</div>`;
    }
}

function renderCryptoChart(container, candles, ticker, timeframe) {
    const times = candles.map(c => c.time);
    const opens = candles.map(c => c.open);
    const highs = candles.map(c => c.high);
    const lows = candles.map(c => c.low);
    const closes = candles.map(c => c.close);
    const volumes = candles.map(c => c.volume);

    const plotlyCode = `
        const times = ${JSON.stringify(times)};
        const opens = ${JSON.stringify(opens)};
        const highs = ${JSON.stringify(highs)};
        const lows = ${JSON.stringify(lows)};
        const closes = ${JSON.stringify(closes)};
        const volumes = ${JSON.stringify(volumes)};

        const candlestick = {
            x: times,
            open: opens, high: highs, low: lows, close: closes,
            type: 'candlestick',
            increasing: { line: { color: '#26a69a' }, fillcolor: '#26a69a' },
            decreasing: { line: { color: '#ef5350' }, fillcolor: '#ef5350' },
            name: '${ticker}',
            xaxis: 'x', yaxis: 'y',
        };

        const volumeBars = {
            x: times,
            y: volumes,
            type: 'bar',
            marker: {
                color: closes.map((c, i) => c >= opens[i] ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.35)'),
            },
            name: 'Volume',
            xaxis: 'x', yaxis: 'y2',
            hoverinfo: 'x+y',
        };

        Plotly.newPlot('chart', [candlestick, volumeBars], {
            template: 'plotly_dark',
            paper_bgcolor: '#171f2a',
            plot_bgcolor: '#171f2a',
            margin: { t: 10, r: 50, b: 40, l: 60 },
            xaxis: {
                rangeslider: { visible: false },
                gridcolor: 'rgba(255,255,255,0.06)',
                type: 'date',
            },
            yaxis: {
                title: 'Price ($)',
                gridcolor: 'rgba(255,255,255,0.06)',
                domain: [0.22, 1],
                side: 'right',
            },
            yaxis2: {
                title: 'Vol',
                gridcolor: 'rgba(255,255,255,0.04)',
                domain: [0, 0.18],
                side: 'right',
            },
            legend: { x: 0, y: 1.05, orientation: 'h' },
            showlegend: false,
        }, { responsive: true });
    `;

    const html = `<!DOCTYPE html>
<html><head>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"><\/script>
<style>body{margin:0;background:#171f2a;overflow:hidden}#chart{width:100%;height:100vh}</style>
</head><body>
<div id="chart"></div>
<script>${plotlyCode}<\/script>
</body></html>`;

    container.innerHTML = "";
    const iframe = document.createElement("iframe");
    iframe.srcdoc = html;
    iframe.style.cssText = "width:100%;height:100%;border:none;border-radius:6px;position:absolute;top:0;left:0";
    container.appendChild(iframe);
}

async function toggleCryptoAgent() {
    const btn = document.getElementById("crypto-agent-btn");
    btn.disabled = true;

    if (tradingState.agentRunning) {
        // Stop
        try {
            await fetch("/api/trading/agent/stop", { method: "POST" });
            tradingState.agentRunning = false;
            updateAgentUI(false);
        } catch (e) {
            console.warn("Failed to stop agent:", e);
        }
    } else {
        // Start
        const config = {
            model: document.getElementById("crypto-agent-model").value,
            strategy: document.getElementById("crypto-agent-strategy").value,
            ticker: tradingState.cryptoTicker,
            max_position_usd: parseFloat(document.getElementById("crypto-agent-max-pos").value) || 50,
            interval_minutes: parseInt(document.getElementById("crypto-agent-interval").value) || 15,
        };
        try {
            const resp = await fetch("/api/trading/agent/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(config),
            });
            const data = await resp.json();
            if (data.error) {
                alert(`Agent error: ${data.error}`);
            } else {
                tradingState.agentRunning = true;
                updateAgentUI(true);
                showAgentLog(true);
            }
        } catch (e) {
            console.warn("Failed to start agent:", e);
        }
    }
    btn.disabled = false;
}

function showAgentLog(visible) {
    const section = document.getElementById("crypto-agent-log-section");
    const layout = section?.closest(".trading-layout");
    if (visible) {
        section.style.display = "";
        layout?.classList.add("has-agent-log");
    } else {
        section.style.display = "none";
        layout?.classList.remove("has-agent-log");
    }
}

function updateAgentUI(running) {
    const btn = document.getElementById("crypto-agent-btn");
    const statusEl = document.getElementById("crypto-agent-status");
    const dot = statusEl.querySelector(".status-dot");

    if (running) {
        btn.textContent = "Stop Agent";
        btn.classList.add("danger-btn");
        dot.className = "status-dot running";
        statusEl.childNodes[statusEl.childNodes.length - 1].textContent = " Agent running";
    } else {
        btn.textContent = "Start Agent";
        btn.classList.remove("danger-btn");
        dot.className = "status-dot idle";
        statusEl.childNodes[statusEl.childNodes.length - 1].textContent = " Agent idle";
    }
}

async function pollAgentStatus() {
    try {
        const data = await fetchJson("/api/trading/agent/status");
        tradingState.agentRunning = data.running;
        updateAgentUI(data.running);

        if (data.running) {
            showAgentLog(true);
        }

        // Update cycle info in log header
        const cycleInfo = document.getElementById("crypto-agent-cycle-info");
        if (cycleInfo && data.cycle_count > 0) {
            const lastRun = data.last_run ? new Date(data.last_run * 1000).toLocaleTimeString() : "—";
            cycleInfo.textContent = `Cycle #${data.cycle_count} · Last: ${lastRun}`;
        }

        if (data.last_decision) {
            const statusEl = document.getElementById("crypto-agent-status");
            const dot = statusEl.querySelector(".status-dot");
            const text = data.running ? " Agent running" : " Agent idle";
            statusEl.innerHTML = "";
            statusEl.appendChild(dot);
            statusEl.appendChild(document.createTextNode(text));
        }
    } catch (e) {
        // silent
    }
}

async function pollAgentLogs() {
    try {
        const logs = await fetchJson("/api/trading/agent/logs?limit=50");
        if (!logs || logs.length === 0) return;

        const container = document.getElementById("crypto-agent-log");
        for (const entry of logs) {
            const div = document.createElement("div");
            div.className = `agent-log-entry log-${entry.type}`;
            const timeStr = entry.time ? new Date(entry.time).toLocaleTimeString() : "";
            div.innerHTML = `<span class="log-time">${escapeHtml(timeStr)}</span>${escapeHtml(entry.message)}`;
            container.appendChild(div);
        }
        // Auto-scroll
        container.scrollTop = container.scrollHeight;

        // If we got a decision, refresh portfolio
        if (logs.some(l => l.type === "decision")) {
            loadCryptoPortfolio();
            loadCryptoQuote();
        }
    } catch (e) {
        // silent
    }
}

async function executeCryptoTrade() {
    const ticker = tradingState.cryptoTicker;
    const side = tradingState.cryptoSide;
    const qty = parseFloat(document.getElementById("crypto-quantity").value);
    const orderType = document.getElementById("crypto-order-type").value;
    const limitPrice = document.getElementById("crypto-limit-price").value;

    const resultEl = document.getElementById("crypto-trade-result");
    resultEl.textContent = "Submitting...";
    resultEl.className = "trade-result";

    try {
        const body = {
            asset_type: "crypto",
            ticker: ticker,
            side: side,
            quantity: qty,
            order_type: orderType,
            provider: "robinhood-crypto",
        };
        if (limitPrice && orderType !== "market") body.price = parseFloat(limitPrice);

        const resp = await fetch("/api/trading/order", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (data.error) {
            resultEl.textContent = `Error: ${data.error}`;
            resultEl.className = "trade-result trade-error";
        } else {
            const msg = data.message || `${side.toUpperCase()} ${qty} ${ticker} — ${data.status || "submitted"}`;
            resultEl.textContent = msg;
            resultEl.className = "trade-result trade-success";
            // Refresh portfolio after trade
            setTimeout(loadCryptoPortfolio, 1000);
        }
    } catch (e) {
        resultEl.textContent = `Network error: ${e.message}`;
        resultEl.className = "trade-result trade-error";
    }
}

// ── Polymarket ───────────────────────────────────────────────────────────

const polyState = {
    loaded: false,
    markets: [],
    selectedSlug: null,
    eventSlug: "btc-updown-5m-1773464700",
    eventData: null,
    agentRunning: false,
    agentPollTimer: null,
    autoRefreshTimer: null,
};

function initPolymarketPane() {
    if (polyState.loaded) return;
    polyState.loaded = true;

    document.getElementById("poly-refresh-btn")?.addEventListener("click", () => {
        loadPolymarkets();
        if (polyState.eventSlug) loadPolyEvent(polyState.eventSlug);
    });
    document.getElementById("poly-sort")?.addEventListener("change", loadPolymarkets);

    let searchTimer = null;
    document.getElementById("poly-search")?.addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => loadPolymarkets(), 400);
    });

    // Load event button
    document.getElementById("poly-load-event-btn")?.addEventListener("click", () => {
        const raw = document.getElementById("poly-event-url")?.value?.trim() || "";
        const slug = parsePolymarketSlug(raw);
        if (slug) {
            polyState.eventSlug = slug;
            loadPolyEvent(slug);
        } else {
            alert("Could not parse event slug from input. Paste a Polymarket event URL or just the slug.");
        }
    });

    // Agent button
    document.getElementById("poly-agent-btn")?.addEventListener("click", togglePolyAgent);

    // Load default event + markets
    loadPolymarkets();
    if (polyState.eventSlug) loadPolyEvent(polyState.eventSlug);
    pollPolyAgentStatus();

    // Auto-refresh: event data every 30s, agent status every 5s when running
    polyState.autoRefreshTimer = setInterval(() => {
        if (tradingState.activeSubtab === "polymarket") {
            if (polyState.eventSlug) loadPolyEvent(polyState.eventSlug);
            pollPolyAgentStatus();
        }
    }, 30000);
    polyState.agentPollTimer = setInterval(() => {
        if (tradingState.activeSubtab === "polymarket" && polyState.agentRunning) {
            pollPolyAgentStatus();
            pollPolyAgentLogs();
        }
    }, 5000);
}

function parsePolymarketSlug(input) {
    // Handle full URL: https://polymarket.com/event/btc-updown-5m-1773464700
    const urlMatch = input.match(/polymarket\.com\/event\/([a-zA-Z0-9_-]+)/);
    if (urlMatch) return urlMatch[1];
    // Handle bare slug
    if (/^[a-zA-Z0-9_-]+$/.test(input) && input.length > 3) return input;
    return null;
}

async function loadPolyEvent(slug) {
    try {
        const data = await fetchJson(`/api/trading/polymarket/event/${slug}`);
        if (data.error) {
            document.getElementById("poly-event-card").style.display = "";
            document.getElementById("poly-event-title").textContent = `Error: ${data.error}`;
            return;
        }

        polyState.eventData = data;
        const event = data.event || {};
        const markets = data.markets || [];

        // Show event card
        const card = document.getElementById("poly-event-card");
        card.style.display = "";
        document.getElementById("poly-event-title").textContent = event.title || slug;

        const metaLines = [];
        if (event.endDate) metaLines.push(`Closes: ${new Date(event.endDate).toLocaleDateString()}`);
        if (event.volume) metaLines.push(`Volume: $${formatCompact(event.volume)}`);
        if (event.liquidity) metaLines.push(`Liquidity: $${formatCompact(event.liquidity)}`);
        document.getElementById("poly-event-meta").innerHTML = metaLines.join(" · ");

        // Show markets
        const marketsSection = document.getElementById("poly-event-markets");
        const marketList = document.getElementById("poly-market-list");
        const marketCount = document.getElementById("poly-market-count");

        if (markets.length > 0) {
            marketsSection.style.display = "";
            marketCount.textContent = `(${markets.length})`;

            marketList.innerHTML = markets.map(m => {
                let outcomes = [];
                try {
                    const names = typeof m.outcomes === "string" ? JSON.parse(m.outcomes) : (m.outcomes || []);
                    const prices = typeof m.outcomePrices === "string" ? JSON.parse(m.outcomePrices) : (m.outcomePrices || []);
                    outcomes = names.map((name, i) => ({ name, price: prices[i] ? parseFloat(prices[i]) : null }));
                } catch {}

                const priceHtml = outcomes.map(o => {
                    if (o.price === null) return "";
                    const pct = (o.price * 100).toFixed(0);
                    const cls = o.name.toLowerCase() === "yes" ? "poly-price-yes" :
                                o.name.toLowerCase() === "no" ? "poly-price-no" : "poly-price-yes";
                    return `<span class="poly-price-pill ${cls}">${o.name} ${pct}%</span>`;
                }).join(" ");

                const vol = m.volume24hr ? `Vol: $${formatCompact(m.volume24hr)}` : "";

                return `<div class="poly-event-market-row">
                    <div style="font-size:0.8rem;margin-bottom:2px;">${escapeHtml(m.question || "?")}</div>
                    <div>${priceHtml} <span style="font-size:0.7rem;color:var(--muted);margin-left:6px">${vol}</span></div>
                </div>`;
            }).join("");
        } else {
            marketsSection.style.display = "none";
        }
    } catch (e) {
        console.warn("Failed to load poly event:", e);
    }
}

async function togglePolyAgent() {
    const btn = document.getElementById("poly-agent-btn");
    btn.disabled = true;

    if (polyState.agentRunning) {
        try {
            await fetch("/api/trading/polymarket/agent/stop", { method: "POST" });
            polyState.agentRunning = false;
            updatePolyAgentUI(false);
        } catch (e) {
            console.warn("Failed to stop poly agent:", e);
        }
    } else {
        const slug = polyState.eventSlug;
        if (!slug) {
            alert("Load an event first before starting the agent.");
            btn.disabled = false;
            return;
        }
        const config = {
            model: document.getElementById("poly-agent-model").value,
            strategy: document.getElementById("poly-agent-strategy").value,
            event_slug: slug,
            event_url: document.getElementById("poly-event-url")?.value || "",
            max_position_usd: parseFloat(document.getElementById("poly-agent-max-pos").value) || 50,
            interval_minutes: parseInt(document.getElementById("poly-agent-interval").value) || 15,
            live_trading: document.getElementById("poly-agent-live-trading")?.checked || false,
            dry_run: document.getElementById("poly-agent-dry-run")?.checked ?? true,
        };
        try {
            const resp = await fetch("/api/trading/polymarket/agent/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(config),
            });
            const data = await resp.json();
            if (data.error) {
                alert(`Agent error: ${data.error}`);
            } else {
                polyState.agentRunning = true;
                updatePolyAgentUI(true);
                showPolyAgentLog(true);
            }
        } catch (e) {
            console.warn("Failed to start poly agent:", e);
        }
    }
    btn.disabled = false;
}

function showPolyAgentLog(visible) {
    const section = document.getElementById("poly-agent-log-section");
    const layout = section?.closest(".trading-layout");
    if (visible) {
        section.style.display = "";
        layout?.classList.add("has-agent-log");
    } else {
        section.style.display = "none";
        layout?.classList.remove("has-agent-log");
    }
}

function updatePolyAgentUI(running) {
    const btn = document.getElementById("poly-agent-btn");
    const statusEl = document.getElementById("poly-agent-status");
    const dot = statusEl.querySelector(".status-dot");

    if (running) {
        btn.textContent = "Stop Agent";
        btn.classList.add("danger-btn");
        dot.className = "status-dot running";
        statusEl.childNodes[statusEl.childNodes.length - 1].textContent = " Agent running";
    } else {
        btn.textContent = "Start Agent";
        btn.classList.remove("danger-btn");
        dot.className = "status-dot idle";
        statusEl.childNodes[statusEl.childNodes.length - 1].textContent = " Agent idle";
    }
}

async function pollPolyAgentStatus() {
    try {
        const data = await fetchJson("/api/trading/polymarket/agent/status");
        polyState.agentRunning = data.running;
        updatePolyAgentUI(data.running);

        if (data.running) showPolyAgentLog(true);

        const cycleInfo = document.getElementById("poly-agent-cycle-info");
        if (cycleInfo && data.cycle_count > 0) {
            const lastRun = data.last_run ? new Date(data.last_run * 1000).toLocaleTimeString() : "—";
            cycleInfo.textContent = `Cycle #${data.cycle_count} · Last: ${lastRun}`;
        }

        // Update active slug display & event URL when rotating
        if (data.active_slug && data.running) {
            const urlInput = document.getElementById("poly-event-url");
            if (urlInput && data.rotating) {
                urlInput.value = `https://polymarket.com/event/${data.active_slug}`;
            }

            // Show rotation info in status line
            const statusEl = document.getElementById("poly-agent-status");
            if (statusEl && data.rotating) {
                const dot = statusEl.querySelector(".status-dot");
                const upcoming = (data.upcoming_slugs || []).slice(1, 3)
                    .map(s => { const ts = parseInt(s.split("-").pop()); return ts ? new Date(ts*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : s; })
                    .join(", ");
                statusEl.innerHTML = "";
                statusEl.appendChild(dot);
                const activeTs = parseInt(data.active_slug.split("-").pop());
                const activeTime = activeTs ? new Date(activeTs*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : "?";
                statusEl.appendChild(document.createTextNode(
                    ` Rotating · ${activeTime}` + (upcoming ? ` → ${upcoming}` : "")
                ));
            }

            // Refresh event card when slot rolls over
            if (data.active_slug !== polyState.eventSlug) {
                polyState.eventSlug = data.active_slug;
                loadPolyEvent(data.active_slug);
            }
        }
    } catch (e) {
        // silent
    }
}

async function pollPolyAgentLogs() {
    try {
        const logs = await fetchJson("/api/trading/polymarket/agent/logs?limit=50");
        if (!logs || logs.length === 0) return;

        const container = document.getElementById("poly-agent-log");
        for (const entry of logs) {
            const div = document.createElement("div");
            div.className = `agent-log-entry log-${entry.type}`;
            const timeStr = entry.time ? new Date(entry.time).toLocaleTimeString() : "";
            div.innerHTML = `<span class="log-time">${escapeHtml(timeStr)}</span>${escapeHtml(entry.message)}`;
            container.appendChild(div);
        }
        container.scrollTop = container.scrollHeight;

        // Refresh event data after decisions
        if (logs.some(l => l.type === "decision") && polyState.eventSlug) {
            loadPolyEvent(polyState.eventSlug);
        }
    } catch (e) {
        // silent
    }
}

async function loadPolymarkets() {
    const container = document.getElementById("poly-markets");
    const countEl = document.getElementById("poly-count");
    const order = document.getElementById("poly-sort")?.value || "volume24hr";
    const q = document.getElementById("poly-search")?.value?.trim() || "";

    container.innerHTML = '<div class="empty-state">Loading markets...</div>';

    const params = new URLSearchParams({ limit: "40", order });
    if (q) params.set("q", q);

    try {
        const resp = await fetch(`/api/trading/polymarket/markets?${params}`);
        const data = await resp.json();

        if (data.error) {
            container.innerHTML = `<div class="empty-state">Error: ${data.error}</div>`;
            return;
        }

        const markets = Array.isArray(data) ? data : [];
        polyState.markets = markets;

        if (!markets.length) {
            container.innerHTML = '<div class="empty-state">No markets found.</div>';
            countEl.textContent = "";
            return;
        }

        countEl.textContent = `${markets.length} markets`;
        container.innerHTML = "";

        markets.forEach(m => {
            const card = document.createElement("div");
            card.className = "poly-card";
            card.dataset.slug = m.slug || "";

            // Parse outcomes & prices
            let outcomes = [];
            try {
                const names = typeof m.outcomes === "string" ? JSON.parse(m.outcomes) : (m.outcomes || []);
                const prices = typeof m.outcomePrices === "string" ? JSON.parse(m.outcomePrices) : (m.outcomePrices || []);
                outcomes = names.map((name, i) => ({
                    name,
                    price: prices[i] ? parseFloat(prices[i]) : null,
                }));
            } catch {}

            const priceHtml = outcomes.map(o => {
                if (o.price === null) return "";
                const pct = (o.price * 100).toFixed(0);
                const cls = o.name.toLowerCase() === "yes" ? "poly-price-yes" :
                            o.name.toLowerCase() === "no" ? "poly-price-no" : "poly-price-yes";
                return `<span class="poly-price-pill ${cls}">${o.name} ${pct}%</span>`;
            }).join("");

            const vol24 = m.volume24hr ? `$${formatCompact(m.volume24hr)}` : "";
            const liq = m.liquidity ? `$${formatCompact(m.liquidity)}` : "";

            card.innerHTML = `
                <div class="poly-card-question">${escapeHtml(m.question || "Untitled")}</div>
                <div class="poly-card-prices">${priceHtml}</div>
                <div class="poly-card-meta">
                    ${vol24 ? `<span>Vol: ${vol24}</span>` : ""}
                    ${liq ? `<span>Liq: ${liq}</span>` : ""}
                </div>
            `;

            card.addEventListener("click", () => selectPolyMarket(m));
            container.appendChild(card);
        });
    } catch (e) {
        container.innerHTML = `<div class="empty-state">Failed to load: ${e.message}</div>`;
    }
}

function selectPolyMarket(m) {
    // Highlight card
    document.querySelectorAll(".poly-card").forEach(c => c.classList.remove("selected"));
    const card = document.querySelector(`.poly-card[data-slug="${m.slug}"]`);
    if (card) card.classList.add("selected");

    // If the market has an event_slug, load it as the target event
    const eventSlug = m.event_slug || m.slug;
    if (eventSlug) {
        const urlInput = document.getElementById("poly-event-url");
        if (urlInput) urlInput.value = `https://polymarket.com/event/${eventSlug}`;
        polyState.eventSlug = eventSlug;
        loadPolyEvent(eventSlug);
    }
}

function formatCompact(n) {
    const num = parseFloat(n);
    if (isNaN(num)) return "0";
    if (num >= 1_000_000) return (num / 1_000_000).toFixed(1) + "M";
    if (num >= 1_000) return (num / 1_000).toFixed(1) + "K";
    return num.toFixed(0);
}

function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
}

// ═══════════════════════════════════════════════════════════════════════════
// PROPHECY ENGINE MODULE
// ═══════════════════════════════════════════════════════════════════════════

let prophecyState = { selectedSim: null, simulations: [] };

async function loadProphecyList() {
    try {
        const data = await fetchJson("/api/prophecy/simulations");
        if (data.error) return;
        prophecyState.simulations = data.simulations || [];
        renderProphecyList();
    } catch (e) {
        console.error("Failed to load prophecy list:", e);
    }
}

function renderProphecyList() {
    const container = document.getElementById("prophecy-list");
    if (!container) return;
    if (!prophecyState.simulations.length) {
        container.innerHTML = '<div class="empty-state">No simulations yet. Create one above.</div>';
        return;
    }
    container.innerHTML = prophecyState.simulations.map(s => `
        <div class="prophecy-sim-card ${prophecyState.selectedSim === s.id ? 'selected' : ''}"
             data-sim-id="${s.id}" onclick="selectProphecySim('${s.id}')">
            <div class="sim-card-header">
                <span class="sim-status badge badge-${s.status === 'completed' ? 'success' : s.status === 'running' ? 'warning' : 'info'}">${s.status}</span>
                <span class="sim-type">${s.sim_type}</span>
            </div>
            <div class="sim-topic">${escapeHtml(s.topic)}</div>
            <div class="sim-meta">${s.num_prophets} prophets &middot; ${s.rounds_completed}/${s.rounds_total} rounds</div>
        </div>
    `).join("");
}

async function selectProphecySim(simId) {
    prophecyState.selectedSim = simId;
    renderProphecyList();
    const runBtn = document.getElementById("prophecy-run-btn");
    const reportBtn = document.getElementById("prophecy-report-btn");
    if (runBtn) runBtn.disabled = false;
    if (reportBtn) reportBtn.disabled = false;

    try {
        const sim = await fetchJson(`/api/prophecy/simulations/${simId}`);
        if (sim.error) return;
        renderProphecyDetail(sim);
    } catch (e) {
        console.error("Failed to load simulation:", e);
    }
}

function renderProphecyDetail(sim) {
    const title = document.getElementById("prophecy-detail-title");
    const detail = document.getElementById("prophecy-detail");
    const interviewPanel = document.getElementById("prophecy-interview-panel");
    if (title) title.textContent = sim.topic || "Simulation";
    if (!detail) return;

    let html = `<div class="sim-detail-grid">`;
    html += `<div class="sim-detail-section">
        <h4>World</h4>
        <p>${sim.world ? escapeHtml(sim.world.description || sim.world.name) : "Not seeded yet"}</p>
    </div>`;

    if (sim.prophets && sim.prophets.length) {
        html += `<div class="sim-detail-section"><h4>Prophets (${sim.prophets.length})</h4><div class="prophet-grid">`;
        for (const p of sim.prophets) {
            const conf = Math.round((p.confidence || 0.5) * 100);
            html += `<div class="prophet-card">
                <strong>${escapeHtml(p.name)}</strong>
                <span class="prophet-archetype">${escapeHtml(p.archetype)}</span>
                <div class="prophet-bar"><div class="prophet-bar-fill" style="width:${conf}%"></div></div>
                <span class="prophet-position">${escapeHtml(p.position || "Undecided")}</span>
            </div>`;
        }
        html += `</div></div>`;
    }

    if (sim.rounds && sim.rounds.length) {
        html += `<div class="sim-detail-section"><h4>Rounds</h4>`;
        for (const r of sim.rounds) {
            html += `<div class="round-entry"><strong>Round ${r.round_number}</strong>: ${escapeHtml(r.summary || "")}</div>`;
        }
        html += `</div>`;
    }

    if (sim.report) {
        html += `<div class="sim-detail-section report-section">
            <h4>Prediction Report</h4>
            <div class="report-prediction">${escapeHtml(sim.report.prediction || "")}</div>
            <div class="report-confidence">Confidence: ${Math.round((sim.report.confidence || 0) * 100)}%</div>
            <div class="report-reasoning">${escapeHtml(sim.report.reasoning || "")}</div>
        </div>`;
    }
    html += `</div>`;
    detail.innerHTML = html;

    // Show interview panel
    if (interviewPanel && sim.prophets && sim.prophets.length) {
        interviewPanel.classList.remove("hidden");
        const select = document.getElementById("prophecy-interview-prophet");
        if (select) {
            select.innerHTML = sim.prophets.map(p => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)} (${escapeHtml(p.archetype)})</option>`).join("");
        }
    }
}

async function prophecyCreate() {
    const topic = document.getElementById("prophecy-topic")?.value?.trim();
    if (!topic) return;
    const btn = document.getElementById("prophecy-create-btn");
    if (btn) btn.disabled = true;
    try {
        const data = await fetchJson("/api/prophecy/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                topic,
                seed_material: document.getElementById("prophecy-seed")?.value || "",
                num_prophets: parseInt(document.getElementById("prophecy-prophets")?.value || "12"),
                num_rounds: parseInt(document.getElementById("prophecy-rounds")?.value || "8"),
                deliberation_mode: document.getElementById("prophecy-deliberation-mode")?.value || "hivemind",
            }),
        });
        if (data.error) { alert(data.error); return; }
        await loadProphecyList();
        selectProphecySim(data.id);
    } catch (e) {
        alert("Failed: " + e.message);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function prophecyRunFull() {
    const topic = document.getElementById("prophecy-topic")?.value?.trim();
    if (!topic) return;
    const btn = document.getElementById("prophecy-full-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Running..."; }
    try {
        const data = await fetchJson("/api/prophecy/full", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                topic,
                seed_material: document.getElementById("prophecy-seed")?.value || "",
                num_prophets: parseInt(document.getElementById("prophecy-prophets")?.value || "12"),
                num_rounds: parseInt(document.getElementById("prophecy-rounds")?.value || "8"),
                deliberation_mode: document.getElementById("prophecy-deliberation-mode")?.value || "hivemind",
            }),
        });
        if (data.error) { alert(data.error); return; }
        await loadProphecyList();
        selectProphecySim(data.id);
    } catch (e) {
        alert("Failed: " + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Full Pipeline"; }
    }
}

async function prophecyRunRounds() {
    if (!prophecyState.selectedSim) return;
    const btn = document.getElementById("prophecy-run-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Running..."; }
    try {
        await fetchJson(`/api/prophecy/run/${prophecyState.selectedSim}`, { method: "POST" });
        // Poll for completion
        const poll = setInterval(async () => {
            const status = await fetchJson(`/api/prophecy/status/${prophecyState.selectedSim}`);
            if (status.run_status === "done" || status.run_status === "error" || status.status === "completed") {
                clearInterval(poll);
                if (btn) { btn.disabled = false; btn.textContent = "Run Rounds"; }
                selectProphecySim(prophecyState.selectedSim);
            }
        }, 3000);
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = "Run Rounds"; }
    }
}

async function prophecyGetReport() {
    if (!prophecyState.selectedSim) return;
    const btn = document.getElementById("prophecy-report-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Generating..."; }
    try {
        await fetchJson(`/api/prophecy/report/${prophecyState.selectedSim}`);
        selectProphecySim(prophecyState.selectedSim);
    } catch (e) {
        alert("Failed: " + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Generate Report"; }
    }
}

async function prophecyInterview() {
    if (!prophecyState.selectedSim) return;
    const prophet = document.getElementById("prophecy-interview-prophet")?.value;
    const question = document.getElementById("prophecy-interview-question")?.value?.trim();
    if (!prophet || !question) return;
    const responseDiv = document.getElementById("prophecy-interview-response");
    if (responseDiv) responseDiv.innerHTML = '<div class="loading">Asking...</div>';
    try {
        const data = await fetchJson(`/api/prophecy/interview/${prophecyState.selectedSim}/${encodeURIComponent(prophet)}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question }),
        });
        if (responseDiv) {
            responseDiv.innerHTML = data.error
                ? `<div class="error">${escapeHtml(data.error)}</div>`
                : `<div class="interview-reply"><strong>${escapeHtml(data.prophet)}:</strong> ${escapeHtml(data.response)}</div>`;
        }
    } catch (e) {
        if (responseDiv) responseDiv.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`;
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// SURGEON (OBLITERATUS) MODULE
// ═══════════════════════════════════════════════════════════════════════════

let surgeonState = { depsReady: null, operations: [], activeOpId: null };

async function surgeonCheckDeps() {
    const btn = document.getElementById("surgeon-check-btn");
    const result = document.getElementById("surgeon-deps-result");
    if (btn) btn.disabled = true;
    try {
        const data = await fetchJson("/api/surgeon/check");
        surgeonState.depsReady = data.ready;
        if (result) {
            if (data.ready) {
                result.innerHTML = `<div class="deps-ok">All dependencies ready. GPU: ${data.gpu ? "Available" : "CPU only"}</div>`;
            } else {
                result.innerHTML = `<div class="deps-missing">
                    <strong>Missing:</strong> ${(data.missing || []).join(", ")}<br>
                    <code>${escapeHtml(data.install_command || "")}</code>
                </div>`;
            }
        }
    } catch (e) {
        if (result) result.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function surgeonLoadMethods() {
    const list = document.getElementById("surgeon-methods-list");
    if (!list) return;
    try {
        const data = await fetchJson("/api/surgeon/methods");
        if (data.error || !data.methods) return;
        list.innerHTML = Object.entries(data.methods).map(([key, m]) => `
            <div class="method-card">
                <strong>${escapeHtml(m.label || key)}</strong>
                <span class="method-difficulty">${escapeHtml(m.difficulty || "")}</span>
                <p>${escapeHtml(m.description || "")}</p>
                <span class="method-vram">VRAM: ${escapeHtml(m.vram || "?")}</span>
            </div>
        `).join("");
    } catch (e) {
        list.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`;
    }
}

async function loadSurgeonOps() {
    try {
        const data = await fetchJson("/api/surgeon/operations");
        if (data.error) return;
        surgeonState.operations = data.operations || [];
        renderSurgeonOps();
    } catch (e) {
        console.error("Failed to load surgeon ops:", e);
    }
}

function renderSurgeonOps() {
    const list = document.getElementById("surgeon-ops-list");
    if (!list) return;
    if (!surgeonState.operations.length) {
        list.innerHTML = '<div class="empty-state">No operations yet. Scan or operate on a model to begin.</div>';
        return;
    }
    list.innerHTML = surgeonState.operations.map(op => `
        <div class="surgeon-op-card" onclick="viewSurgeonOp('${escapeHtml(op.id)}')">
            <div class="op-header">
                <span class="badge badge-${op.status === 'completed' ? 'success' : op.status === 'failed' ? 'error' : 'warning'}">${op.status}</span>
                <span class="op-method">${escapeHtml(op.method)}</span>
            </div>
            <div class="op-model">${escapeHtml(op.model_name)}</div>
            <div class="op-meta">${escapeHtml(op.created_at || "")}</div>
        </div>
    `).join("");
}

async function viewSurgeonOp(opId) {
    const resultDiv = document.getElementById("surgeon-result");
    const content = document.getElementById("surgeon-result-content");
    const title = document.getElementById("surgeon-result-title");
    if (!resultDiv || !content) return;
    resultDiv.classList.remove("hidden");
    try {
        const data = await fetchJson(`/api/surgeon/operations/${opId}`);
        if (data.error) { content.innerHTML = `<div class="error">${escapeHtml(data.error)}</div>`; return; }
        if (title) title.textContent = `Operation: ${data.model_name || opId}`;
        let html = `<div class="op-detail-grid">`;
        html += `<div class="metric"><span>Method</span><strong>${escapeHtml(data.method)}</strong></div>`;
        html += `<div class="metric"><span>Status</span><strong>${escapeHtml(data.status)}</strong></div>`;
        html += `<div class="metric"><span>Device</span><strong>${escapeHtml(data.device)}</strong></div>`;
        if (data.output_path) html += `<div class="metric"><span>Output</span><strong>${escapeHtml(data.output_path)}</strong></div>`;
        if (data.quality_metrics) {
            const qm = data.quality_metrics;
            html += `<div class="metric"><span>Refusal Rate</span><strong>${(qm.refusal_rate * 100).toFixed(1)}%</strong></div>`;
            html += `<div class="metric"><span>Perplexity</span><strong>${qm.perplexity.toFixed(2)}</strong></div>`;
            html += `<div class="metric"><span>Coherence</span><strong>${qm.coherence.toFixed(2)}</strong></div>`;
        }
        if (data.stages) {
            html += `<div class="op-stages"><h4>Pipeline Stages</h4>`;
            for (const s of data.stages) {
                html += `<div class="stage-row"><span class="stage-name">${escapeHtml(s.name)}</span><span class="badge badge-${s.status === 'done' ? 'success' : s.status === 'failed' ? 'error' : 'info'}">${s.status}</span><span>${s.duration_seconds.toFixed(1)}s</span></div>`;
            }
            html += `</div>`;
        }
        html += `</div>`;
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`;
    }
}

async function surgeonScan() {
    const model = document.getElementById("surgeon-model")?.value?.trim();
    if (!model) { alert("Enter a model name"); return; }
    const resultDiv = document.getElementById("surgeon-result");
    const content = document.getElementById("surgeon-result-content");
    const title = document.getElementById("surgeon-result-title");
    if (resultDiv) resultDiv.classList.remove("hidden");
    if (title) title.textContent = "Scanning...";
    if (content) content.innerHTML = '<div class="loading">Scanning model refusal geometry...</div>';
    try {
        const data = await fetchJson("/api/surgeon/scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model_name: model,
                device: document.getElementById("surgeon-device")?.value || "auto",
                dtype: document.getElementById("surgeon-dtype")?.value || "float16",
            }),
        });
        if (data.error) { content.innerHTML = `<div class="error">${escapeHtml(data.error)}</div>`; return; }
        if (title) title.textContent = `Scan: ${data.model_name || model}`;
        let html = `<div class="scan-results">`;
        html += `<div class="metric"><span>Architecture</span><strong>${escapeHtml(data.architecture || "?")}</strong></div>`;
        html += `<div class="metric"><span>Layers</span><strong>${data.num_layers}</strong></div>`;
        html += `<div class="metric"><span>Strong Refusal Layers</span><strong>${(data.strong_layers || []).join(", ") || "None"}</strong></div>`;
        html += `<div class="metric"><span>Recommended Method</span><strong>${escapeHtml(data.recommended_method || "?")}</strong></div>`;
        html += `</div>`;
        content.innerHTML = html;
    } catch (e) {
        if (content) content.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`;
    }
}

async function surgeonOperate() {
    const model = document.getElementById("surgeon-model")?.value?.trim();
    if (!model) { alert("Enter a model name"); return; }
    const logsDiv = document.getElementById("surgeon-live-logs");
    const logOutput = document.getElementById("surgeon-log-output");
    const statusBadge = document.getElementById("surgeon-live-status");
    if (logsDiv) logsDiv.classList.remove("hidden");
    if (logOutput) logOutput.textContent = "Starting operation...\n";
    if (statusBadge) { statusBadge.textContent = "Running"; statusBadge.className = "badge badge-warning"; }
    try {
        const data = await fetchJson("/api/surgeon/operate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model_name: model,
                method: document.getElementById("surgeon-method")?.value || "advanced",
                device: document.getElementById("surgeon-device")?.value || "auto",
                dtype: document.getElementById("surgeon-dtype")?.value || "float16",
            }),
        });
        if (data.error) {
            if (logOutput) logOutput.textContent += `ERROR: ${data.error}\n`;
            if (data.install_command) logOutput.textContent += `Install: ${data.install_command}\n`;
            if (statusBadge) { statusBadge.textContent = "Error"; statusBadge.className = "badge badge-error"; }
            return;
        }
        surgeonState.activeOpId = data.op_id;
        // Poll for progress
        const poll = setInterval(async () => {
            const status = await fetchJson(`/api/surgeon/operate/${data.op_id}/status`);
            if (logOutput && status.recent_logs) {
                logOutput.textContent = status.recent_logs.join("\n") + "\n";
                logOutput.scrollTop = logOutput.scrollHeight;
            }
            if (status.status === "done") {
                clearInterval(poll);
                if (statusBadge) { statusBadge.textContent = "Complete"; statusBadge.className = "badge badge-success"; }
                loadSurgeonOps();
                if (status.record) viewSurgeonOp(status.record.id);
            } else if (status.status === "error") {
                clearInterval(poll);
                if (statusBadge) { statusBadge.textContent = "Failed"; statusBadge.className = "badge badge-error"; }
                if (logOutput) logOutput.textContent += `\nERROR: ${status.error || "Unknown"}\n`;
            }
        }, 3000);
    } catch (e) {
        if (logOutput) logOutput.textContent += `ERROR: ${e.message}\n`;
        if (statusBadge) { statusBadge.textContent = "Error"; statusBadge.className = "badge badge-error"; }
    }
}


init();
