/**
 * Forge UI — Trading, crypto, and polymarket panes
 */

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
            container.textContent = "";
            const err = document.createElement("div");
            err.className = "empty-state";
            err.textContent = `Error: ${data.error}`;
            container.appendChild(err);
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
                return `<span class="poly-price-pill ${cls}">${escapeHtml(o.name)} ${pct}%</span>`;
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
        container.textContent = "";
        const fail = document.createElement("div");
        fail.className = "empty-state";
        fail.textContent = `Failed to load: ${e.message}`;
        container.appendChild(fail);
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

