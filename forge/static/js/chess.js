/**
 * Forge UI — Chess Arena tab
 */

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
    const mode = document.getElementById("chess-mode")?.value || "ai_vs_ai";
    const judge = document.getElementById("chess-judge-model")?.value
               || "grok-4.20-0309-reasoning";
    const ciRaw = document.getElementById("chess-commentary-interval")?.value || "2:0";
    const [ciPart, winPart] = ciRaw.split(":");
    const interval = parseInt(ciPart, 10) || 2;
    const windowPlies = parseInt(winPart || "0", 10) || 0;

    let payload;
    if (mode === "human_vs_ai") {
        // Human vs AI mode
        const aiModel = document.getElementById("chess-black-model").value; // for now default human=white, ai=black
        const humanSide = "white"; // TODO: make choosable
        if (!aiModel) {
            chessSetStatus("Select an AI opponent model.", "err");
            return;
        }
        payload = {
            human_side: humanSide,
            ai_model: aiModel,
            judge_model: judge,
            commentary_interval: interval,
            commentary_window_plies: windowPlies,
        };
    } else {
        const white = document.getElementById("chess-white-model").value;
        const black = document.getElementById("chess-black-model").value;
        if (!white || !black) {
            chessSetStatus("Select both models first.", "err");
            return;
        }
        payload = {
            white_model: white,
            black_model: black,
            judge_model: judge,
            commentary_interval: interval,
            commentary_window_plies: windowPlies,
        };
    }

    chessSetStatus("Starting match…", "working");
    try {
        const resp = await fetchJson("/api/chess", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
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
