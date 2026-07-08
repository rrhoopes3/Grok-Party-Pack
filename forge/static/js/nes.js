/**
 * Forge UI — NES Arena tab
 */

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
