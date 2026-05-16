/**
 * Forge UI — History + Run Inspector Module
 *
 * Owns the entire "History" tab experience:
 *   - Loading past tasks from /api/history
 *   - Rendering the task list with status pills
 *   - Rich run inspector (the beautiful event timeline)
 *   - Filtering by event category
 *   - Fallback legacy detail view
 *
 * This is one of the highest-UX modules — people love being able to
 * go back and see exactly what the agent did, what tools it called,
 * guardrail hits, widget renders, and cost breakdowns.
 */

async function loadHistory() {
    try {
        const tasks = await fetchJson("/api/history");
        state.history = Array.isArray(tasks) ? [...tasks].reverse() : [];
        renderHistory();

        if (state.selectedHistoryId) {
            renderHistoryDetail(state.history.find((task) => task.task_id === state.selectedHistoryId) || null);
        }
    } catch (error) {
        if (els.historyList) {
            els.historyList.textContent = `Failed to load history: ${error.message}`;
        }
    }
}

function renderHistory() {
    if (!els.historyList) return;

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
    if (!els.historyDetail) return;

    if (!task) {
        els.historyDetail.className = "history-detail empty-state";
        els.historyDetail.textContent = "Select a past task to inspect the full event stream, tool calls, widgets, costs, and safety data.";
        if (els.inspectorFilters) els.inspectorFilters.style.display = "none";
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
    if (!els.historyDetail || !els.inspectorFilters) return;

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
                    ${trust}${latency}${reassign}
                </li>
            `;
        }).join("")
        : `<li class="empty">No step results recorded.</li>`;

    const toolsHtml = uniqueTools.length
        ? `<div class="history-block"><h4>Tools Used</h4><p>${escapeHtml(uniqueTools.join(", "))}</p></div>`
        : "";

    els.historyDetail.className = "history-detail";
    els.historyDetail.innerHTML = `
        <div class="history-block">
            <div class="history-kicker">${escapeHtml(formatTimestamp(task.timestamp))} | ${escapeHtml(task.task_id || "-")}</div>
            <h4>${escapeHtml(task.task || "Untitled task")}</h4>
            <p>${escapeHtml(task.final_summary || "No summary stored.")}</p>
        </div>
        ${toolsHtml}
        <div class="history-block">
            <h4>Steps</h4>
            <ul class="step-list">${stepsHtml}</ul>
        </div>
    `;
}

function renderRunInspector(task, events, meta) {
    if (!els.historyDetail || !els.inspectorFilters) return;

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

    bindInspectorFilters();
}

const EVENT_CATEGORIES = {
    status:              "status",
    content:             "content",
    plan_content:        "plan",
    step_start:          "step",
    step_done:           "step",
    tool_call:           "tool",
    tool_result:         "tool",
    guardrail_violation: "safety",
    guardrail_summary:   "safety",
    firewall_block:      "safety",
    escalation:          "safety",
    widget_render:       "widget",
    judge_scores:        "scores",
    token_usage:         "usage",
    toll_deducted:       "toll",
    toll_summary:        "toll",
    done:                "done",
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

function bindInspectorFilters() {
    if (!els.inspectorFilters) return;
    const filterBtns = els.inspectorFilters.querySelectorAll(".filter-btn");
    filterBtns.forEach(btn => {
        const fresh = btn.cloneNode(true);
        btn.parentNode.replaceChild(fresh, btn);
        fresh.addEventListener("click", () => {
            filterBtns.forEach(b => b.classList.remove("active"));
            els.inspectorFilters.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
            fresh.classList.add("active");
            applyInspectorFilter(fresh.dataset.filter);
        });
    });
}

function applyInspectorFilter(filter) {
    const timeline = els.historyDetail?.querySelector(".inspector-timeline");
    if (!timeline) return;

    const events = timeline.querySelectorAll(".inspector-event");
    if (!filter || filter === "all") {
        events.forEach(el => el.style.display = "");
        return;
    }

    events.forEach(el => {
        el.style.display = (el.dataset.cat === filter) ? "" : "none";
    });
}

// Small helpers this module depends on (can be moved to core later)
function isPlannedTask(task) {
    return !!(task && (task.plan || task.results?.some(r => r.plan_id)));
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
