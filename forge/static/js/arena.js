/**
 * Forge UI — Arena combat/collab + TTS commentary
 */

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
    if (els.ttsToggle) {
        els.ttsToggle.checked = saved === "true";
        state.ttsEnabled = els.ttsToggle.checked;
        els.ttsToggle.addEventListener("change", () => {
            state.ttsEnabled = els.ttsToggle.checked;
            localStorage.setItem("forge_arena_tts", String(state.ttsEnabled));
            if (!state.ttsEnabled) stopTTS();
        });
    }
    // Chess tab has its own TTS checkbox; if it's on, keep speech enabled
    // even when arena TTS was saved off (bindChessUi may have run first).
    const chessTts = document.getElementById("chess-tts-toggle");
    if (chessTts?.checked) state.ttsEnabled = true;

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
