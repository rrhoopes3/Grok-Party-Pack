/**
 * Forge UI — Prophecy Engine tab
 */

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

function bindProphecyUi() {
    const wire = (id, fn) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("click", fn);
    };
    wire("prophecy-create-btn", () => prophecyCreate());
    wire("prophecy-full-btn", () => prophecyRunFull());
    wire("prophecy-run-btn", () => prophecyRunRounds());
    wire("prophecy-report-btn", () => prophecyGetReport());
    wire("prophecy-interview-btn", () => prophecyInterview());
    wire("prophecy-refresh-btn", () => loadProphecyList());
}

