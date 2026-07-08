/**
 * Forge UI — Memory / Session Vault tab
 */

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

