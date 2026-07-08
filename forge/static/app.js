// ═══════════════════════════════════════════════════════════════════════════
// Forge UI — Console glue (config, models, costs, settings)
// Domain UI lives in static/js/*.js modules loaded before this file.
// ═══════════════════════════════════════════════════════════════════════════

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

// ─────────────────────────────────────────────────────────────────────────────
// Pack functionality has been extracted to static/js/packs.js (Quality Refactor)
// The following functions now live in the dedicated module:
//   - loadPacks()
//   - populatePackSelect()
//   - renderPackReadiness()
//
// This keeps the original call sites (init, restoreSettings, bindEvents) working
// without any further changes during the transition.
// ─────────────────────────────────────────────────────────────────────────────

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
    renderPackReadiness(els.packSelect.value);  // show detailed readiness on restore

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

// updateControlState(), applyWorkspaceMode(), and toneMoneyElement()
// have been moved to core.js (single source of truth)

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

function shortModelName(modelId) {
    if (!modelId) return "-";
    const match = state.models.find((model) => model.id === modelId);
    return match ? match.label : modelId;
}

function incrementCompositeCount(value) {
    if (typeof value === "string" && value.includes("/")) {
        const [blocked, total] = value.split("/").map((part) => Number.parseInt(part, 10) || 0);
        return `${blocked + 1}/${total + 1}`;
    }

    const numeric = Number.parseInt(value, 10) || 0;
    return String(numeric + 1);
}
function capitalize(value) {
    if (!value) return "";
    return value.charAt(0).toUpperCase() + value.slice(1);
}

