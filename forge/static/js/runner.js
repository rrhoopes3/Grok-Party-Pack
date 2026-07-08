/**
 * Forge UI — Task runner: submit, stream, messages, widgets
 * Relies on core.js for resetRunState / applyRunState / updateStatus / modeFromControls
 */

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

