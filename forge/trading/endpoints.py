"""Flask Blueprint for trading API endpoints."""

from __future__ import annotations

import json
import logging

from flask import Blueprint, Response, jsonify, request

from forge.trading import check_trading_readiness
from forge.trading.engine import get_engine
from forge.trading_deps import get_provider_dependency_status

log = logging.getLogger("forge.trading.endpoints")

trading_bp = Blueprint("trading", __name__, url_prefix="/api/trading")


@trading_bp.route("/pcr/<ticker>")
def get_pcr(ticker: str):
    """Get PCR ratios for a ticker."""
    expiry = request.args.get("expiry", "").strip()
    provider = request.args.get("provider", "").strip()
    try:
        result = get_engine().get_pcr(ticker, expiry=expiry, provider=provider)
        return jsonify(result.to_dict())
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@trading_bp.route("/tickers")
def get_tickers():
    """Return preset tickers grouped by category."""
    return jsonify(get_engine().get_preset_tickers())


@trading_bp.route("/expirations/<ticker>")
def get_expirations(ticker: str):
    """Get available option expiration dates."""
    provider = request.args.get("provider", "").strip()
    exps = get_engine().get_expirations(ticker, provider=provider)
    return jsonify({"ticker": ticker, "expirations": exps})


@trading_bp.route("/chain/<ticker>")
def get_chain(ticker: str):
    """Get raw options chain data."""
    expiry = request.args.get("expiry", "").strip()
    provider = request.args.get("provider", "").strip()
    min_volume = int(request.args.get("min_volume", "0"))
    chain = get_engine().get_options_chain(ticker, expiry, provider, min_volume)
    return jsonify(chain.to_dict())


@trading_bp.route("/quote/<ticker>")
def get_quote(ticker: str):
    """Get current price quote."""
    provider = request.args.get("provider", "").strip()
    q = get_engine().get_quote(ticker, provider=provider)
    return jsonify(
        {
            "ticker": q.ticker,
            "price": q.price,
            "change": q.change,
            "change_pct": q.change_pct,
            "volume": q.volume,
            "timestamp": q.timestamp,
        }
    )


@trading_bp.route("/sentiment", methods=["POST"])
def analyze_sentiment():
    """Analyze PCR sentiment across multiple tickers."""
    data = request.get_json() or {}
    tickers = data.get("tickers", [])
    provider = data.get("provider", "")
    if not tickers:
        return jsonify({"error": "No tickers provided"}), 400
    result = get_engine().analyze_sentiment(tickers, provider=provider)
    return jsonify(result)


@trading_bp.route("/alerts")
def get_alerts():
    """List active alerts."""
    alerts = get_engine().get_alerts()
    return jsonify([a.to_dict() for a in alerts])


@trading_bp.route("/alerts", methods=["POST"])
def set_alert():
    """Create a new alert."""
    data = request.get_json() or {}
    ticker = data.get("ticker", "").strip()
    metric = data.get("metric", "vol_ratio").strip()
    threshold = float(data.get("threshold", 1.0))
    direction = data.get("direction", "above").strip()

    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    if metric not in ("vol_ratio", "oi_ratio"):
        return jsonify({"error": "metric must be vol_ratio or oi_ratio"}), 400
    if direction not in ("above", "below"):
        return jsonify({"error": "direction must be above or below"}), 400

    alert = get_engine().set_alert(ticker, metric, threshold, direction)
    return jsonify(alert.to_dict()), 201


@trading_bp.route("/alerts/<alert_id>", methods=["DELETE"])
def remove_alert(alert_id: str):
    """Remove an alert."""
    removed = get_engine().remove_alert(alert_id)
    if not removed:
        return jsonify({"error": "Alert not found"}), 404
    return jsonify({"status": "removed", "alert_id": alert_id})


@trading_bp.route("/history/<ticker>")
def get_history(ticker: str):
    """Get PCR history for a ticker."""
    expiry = request.args.get("expiry", "").strip()
    records = get_engine().get_history(ticker, expiry)
    return jsonify({"ticker": ticker, "records": records})


@trading_bp.route("/stream")
def trading_stream():
    """SSE stream for live PCR updates and alert triggers."""
    engine = get_engine()
    q = engine.subscribe()

    def generate():
        try:
            while True:
                msg = q.get()
                if msg is None:
                    break
                yield f"data: {json.dumps(msg)}\n\n"
        finally:
            engine.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@trading_bp.route("/config")
def get_config():
    """Return trading config, readiness, and per-provider capability state."""
    from forge.config import (
        TRADING_DEFAULT_PROVIDER,
        TRADING_ENABLED,
        TRADING_PAPER_MODE,
        TRADING_ROBINHOOD_API_KEY,
        TRADING_ROBINHOOD_API_SECRET,
        TRADING_ROBINHOOD_PASS,
        TRADING_ROBINHOOD_USER,
        TRADING_TRADIER_ACCOUNT_ID,
        TRADING_TRADIER_API_KEY,
    )

    readiness = check_trading_readiness()
    rh_legacy = bool(TRADING_ROBINHOOD_USER and TRADING_ROBINHOOD_PASS)
    rh_crypto_api = bool(TRADING_ROBINHOOD_API_KEY and TRADING_ROBINHOOD_API_SECRET)
    tradier_configured = bool(TRADING_TRADIER_API_KEY)
    tradier_can_trade = bool(TRADING_TRADIER_API_KEY and TRADING_TRADIER_ACCOUNT_ID)
    rh_legacy_deps = get_provider_dependency_status("robinhood")
    rh_crypto_deps = get_provider_dependency_status("robinhood-crypto")
    rh_legacy_ready = rh_legacy and rh_legacy_deps["available"]
    rh_crypto_ready = rh_crypto_api and rh_crypto_deps["available"]

    return jsonify(
        {
            "enabled": TRADING_ENABLED,
            "default_provider": TRADING_DEFAULT_PROVIDER,
            "paper_mode": TRADING_PAPER_MODE,
            "readiness": readiness,
            "providers": {
                "yfinance": {
                    "configured": True,
                    "available": True,
                    "missing_dependencies": [],
                    "issues": [],
                    "mode": "free",
                    "label": "Yahoo Finance (Free, Delayed)",
                    "capabilities": {
                        "stocks": {"quotes": True, "trade": False},
                        "options": {"chains": True, "trade": False},
                        "crypto": {"quotes": False, "trade": False},
                    },
                    "data_quality": "delayed ~15min",
                    "auth": "none",
                },
                "tradier": {
                    "configured": tradier_configured,
                    "available": True,
                    "missing_dependencies": [],
                    "issues": (
                        []
                        if tradier_can_trade
                        else (
                            ["Set FORGE_TRADIER_API_KEY to enable Tradier market data"]
                            if not tradier_configured
                            else [
                                "Set FORGE_TRADIER_ACCOUNT_ID to enable live Tradier order routing"
                            ]
                        )
                    ),
                    "mode": "sandbox" if not tradier_can_trade else "live",
                    "label": "Tradier" + (" (Sandbox)" if not tradier_can_trade else " (Live)"),
                    "capabilities": {
                        "stocks": {"quotes": tradier_configured, "trade": tradier_can_trade},
                        "options": {"chains": tradier_configured, "trade": tradier_can_trade},
                        "crypto": {"quotes": False, "trade": False},
                    },
                    "data_quality": "real-time",
                    "auth": "api_key",
                    "env_vars": ["FORGE_TRADIER_API_KEY", "FORGE_TRADIER_ACCOUNT_ID"],
                },
                "robinhood": {
                    "configured": rh_legacy,
                    "available": rh_legacy_deps["available"],
                    "missing_dependencies": rh_legacy_deps["missing_dependencies"],
                    "issues": (
                        ([] if rh_legacy else [
                            "Set FORGE_ROBINHOOD_USER and FORGE_ROBINHOOD_PASS to enable Robinhood"
                        ])
                        + ([rh_legacy_deps["issue"]] if rh_legacy_deps["issue"] else [])
                    ),
                    "mode": "legacy",
                    "label": "Robinhood (Full Access)",
                    "capabilities": {
                        "stocks": {"quotes": rh_legacy_ready, "trade": rh_legacy_ready},
                        "options": {"chains": rh_legacy_ready, "trade": rh_legacy_ready},
                        "crypto": {"quotes": rh_legacy_ready, "trade": rh_legacy_ready},
                    },
                    "data_quality": "real-time",
                    "auth": "username/password",
                    "env_vars": ["FORGE_ROBINHOOD_USER", "FORGE_ROBINHOOD_PASS"],
                },
                "robinhood-crypto": {
                    "configured": rh_crypto_api,
                    "available": rh_crypto_deps["available"],
                    "missing_dependencies": rh_crypto_deps["missing_dependencies"],
                    "issues": (
                        ([] if rh_crypto_api else [
                            "Set FORGE_ROBINHOOD_API_KEY and FORGE_ROBINHOOD_API_SECRET to enable Robinhood Crypto API"
                        ])
                        + ([rh_crypto_deps["issue"]] if rh_crypto_deps["issue"] else [])
                    ),
                    "mode": "api-key",
                    "label": "Robinhood Crypto API",
                    "capabilities": {
                        "stocks": {"quotes": False, "trade": False},
                        "options": {"chains": False, "trade": False},
                        "crypto": {"quotes": rh_crypto_ready, "trade": rh_crypto_ready},
                    },
                    "data_quality": "real-time",
                    "auth": "api_key",
                    "env_vars": [
                        "FORGE_ROBINHOOD_API_KEY",
                        "FORGE_ROBINHOOD_API_SECRET",
                    ],
                },
            },
        }
    )


@trading_bp.route("/provider", methods=["POST"])
def switch_provider():
    """Switch the active trading provider at runtime."""
    import forge.config as cfg
    from forge.trading import brokers as broker_mod

    data = request.get_json() or {}
    provider = data.get("provider", "").strip()
    valid = {"yfinance", "tradier", "robinhood", "robinhood-crypto"}
    if provider not in valid:
        return jsonify({"error": f"Unknown provider: {provider}. Valid: {sorted(valid)}"}), 400

    if provider == "tradier" and not cfg.TRADING_TRADIER_API_KEY:
        return jsonify({"error": "Cannot switch to Tradier; FORGE_TRADIER_API_KEY not set"}), 400
    if provider == "robinhood" and not (
        cfg.TRADING_ROBINHOOD_USER and cfg.TRADING_ROBINHOOD_PASS
    ):
        return jsonify({"error": "Cannot switch to Robinhood; credentials not set"}), 400
    if provider == "robinhood-crypto" and not (
        cfg.TRADING_ROBINHOOD_API_KEY and cfg.TRADING_ROBINHOOD_API_SECRET
    ):
        return jsonify({"error": "Cannot switch to Robinhood Crypto API; API key not set"}), 400

    dep_status = get_provider_dependency_status(provider)
    if not dep_status["available"]:
        return jsonify({"error": dep_status["issue"]}), 400

    cfg.TRADING_DEFAULT_PROVIDER = provider
    with broker_mod._broker_lock:
        broker_mod._broker = None

    log.info("Trading provider switched to: %s", provider)
    return jsonify({"status": "ok", "provider": provider})


@trading_bp.route("/portfolio")
def get_portfolio():
    """Get current portfolio positions and P&L.

    When a live crypto provider is active, prefer brokerage holdings so the
    UI reflects the actual account state instead of stale local fills.
    """
    try:
        from forge.trading.portfolio_view import build_portfolio_summary

        provider_name = request.args.get("provider", "").strip()
        summary = build_portfolio_summary(provider_name=provider_name)
        log.info(
            "Portfolio summary (%s): %d positions, pnl=%s",
            summary.get("source", "local"),
            summary.get("position_count", 0),
            summary.get("total_pnl", 0),
        )
        return jsonify(summary)
    except ImportError:
        return jsonify({"positions": [], "total_pnl": 0, "message": "Portfolio module available"})


@trading_bp.route("/order", methods=["POST"])
def place_order():
    """Place a trade order."""
    try:
        from forge.config import TRADING_DEFAULT_PROVIDER, TRADING_PAPER_MODE
        from forge.trading.brokers import get_broker

        data = request.get_json() or {}
        asset_type = data.get("asset_type", "stock").strip()
        provider_name = data.get("provider", "").strip()
        ticker = data.get("ticker", "").strip()
        side = data.get("side", "").strip()
        quantity = float(data.get("quantity", 0))
        order_type = data.get("order_type", "market").strip()
        price = float(data.get("price", 0)) if data.get("price") else None

        if not ticker or not side or quantity <= 0:
            return jsonify({"error": "ticker, side, and positive quantity required"}), 400

        broker = get_broker(provider_name)
        effective_provider = provider_name or TRADING_DEFAULT_PROVIDER
        log.info(
            "Order: asset=%s ticker=%s side=%s qty=%s broker=%s provider=%s paper=%s",
            asset_type,
            ticker,
            side,
            quantity,
            broker.name,
            effective_provider,
            TRADING_PAPER_MODE,
        )

        if asset_type == "option":
            expiry = data.get("expiry", "").strip()
            strike = float(data.get("strike", 0))
            option_type = data.get("option_type", "call").strip()
            if not expiry or strike <= 0:
                return jsonify({"error": "expiry and strike required for options"}), 400
            if not hasattr(broker, "place_option_order"):
                return jsonify({"error": f"Broker '{broker.name}' does not support options trading"}), 400
            result = broker.place_option_order(
                ticker, expiry, strike, option_type, side, int(quantity), order_type, price
            )
        elif asset_type == "crypto":
            if hasattr(broker, "place_crypto_order"):
                result = broker.place_crypto_order(ticker, side, quantity, order_type, price)
            else:
                result = broker.place_order(ticker, side, quantity, order_type, price)
        else:
            result = broker.place_order(ticker, side, quantity, order_type, price)

        if "error" in result:
            log.warning("Order failed: %s", result)
            lower_error = str(result.get("error", "")).lower()
            status = 400 if (
                "requires the optional package" in lower_error or "not configured" in lower_error
            ) else 502
            return (
                jsonify(
                    {
                        **result,
                        "_debug": {
                            "broker": broker.name,
                            "provider": effective_provider,
                            "paper_mode": TRADING_PAPER_MODE,
                            "asset_type": asset_type,
                            "ticker": ticker,
                        },
                    }
                ),
                status,
            )

        # Record successful live orders in local portfolio DB
        if not TRADING_PAPER_MODE and not result.get("paper_mode"):
            try:
                from forge.trading.portfolio import get_portfolio_manager
                from forge.trading.providers import get_provider_from_config

                pm = get_portfolio_manager()
                live_status = str(result.get("status", "submitted") or "submitted").lower()
                fill_price = price if live_status == "filled" else None
                if live_status == "filled" and not fill_price:
                    try:
                        provider = get_provider_from_config(provider_name)
                        q = provider.get_quote(ticker)
                        fill_price = q.price
                    except Exception:
                        fill_price = 0
                pm.record_order(
                    order_id=str(result.get("order_id") or ""),
                    ticker=ticker, side=side, quantity=quantity,
                    order_type=order_type, price=price,
                    fill_price=fill_price if fill_price else None,
                    status=live_status, broker=broker.name,
                )
                if live_status == "filled" and fill_price:
                    pm.update_position(ticker, quantity, fill_price, side)
                    log.info("Portfolio updated: %s %s %s @ %s", side, quantity, ticker, fill_price)
            except Exception as e:
                log.warning("Failed to record order in portfolio: %s", e)

        return jsonify(result)
    except Exception as e:
        log.exception("Order endpoint exception")
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@trading_bp.route("/orders")
def get_orders():
    """Get order history."""
    try:
        from forge.trading.portfolio import get_portfolio_manager

        pm = get_portfolio_manager()
        return jsonify(pm.get_orders())
    except ImportError:
        return jsonify([])


@trading_bp.route("/recap")
def daily_recap():
    """Get a daily trading recap — orders, P&L, agent decisions, win rate."""
    from forge.trading.portfolio import get_portfolio_manager

    date_str = request.args.get("date", "").strip() or None
    try:
        pm = get_portfolio_manager()
        recap = pm.get_daily_recap(date_str)
        return jsonify(recap)
    except ValueError as e:
        return jsonify({"error": f"Invalid date: {e}"}), 400
    except Exception as e:
        log.exception("Recap failed")
        return jsonify({"error": str(e)}), 500


@trading_bp.route("/price-history/<symbol>")
def get_price_history(symbol: str):
    """Get price history + technical indicators for any ticker via yfinance."""
    timeframe = request.args.get("timeframe", "1D").strip()
    asset_type = request.args.get("type", "stock")  # "stock" or "crypto"
    try:
        import yfinance as yf
        import numpy as np

        tf_map = {
            "1D": ("1d", "5m"),
            "1W": ("5d", "30m"),
            "1M": ("1mo", "1h"),
            "3M": ("3mo", "1d"),
            "6M": ("6mo", "1d"),
            "1Y": ("1y", "1d"),
        }
        period, interval = tf_map.get(timeframe, ("1d", "5m"))

        yf_symbol = f"{symbol.upper()}-USD" if asset_type == "crypto" else symbol.upper()
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period=period, interval=interval)

        if hist.empty:
            return jsonify({"symbol": symbol, "candles": [], "timeframe": timeframe})

        closes = hist["Close"].values.astype(float)
        highs = hist["High"].values.astype(float)
        lows = hist["Low"].values.astype(float)
        volumes = hist["Volume"].values.astype(float)

        # ── Bollinger Bands (20-period, 2 std) ──
        bb_period = 20
        bb_upper, bb_lower, bb_mid = [], [], []
        for i in range(len(closes)):
            if i < bb_period - 1:
                bb_upper.append(None)
                bb_lower.append(None)
                bb_mid.append(None)
            else:
                window = closes[i - bb_period + 1:i + 1]
                mean = float(np.mean(window))
                std = float(np.std(window))
                bb_mid.append(round(mean, 4))
                bb_upper.append(round(mean + 2 * std, 4))
                bb_lower.append(round(mean - 2 * std, 4))

        # ── VWAP ──
        cumvol = np.cumsum(volumes)
        cum_tp_vol = np.cumsum(((highs + lows + closes) / 3) * volumes)
        vwap = []
        for i in range(len(closes)):
            if cumvol[i] > 0:
                vwap.append(round(float(cum_tp_vol[i] / cumvol[i]), 4))
            else:
                vwap.append(None)

        # ── RSI (14-period) ──
        rsi_period = 14
        rsi = [None] * len(closes)
        if len(closes) > rsi_period:
            deltas = np.diff(closes)
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            avg_gain = float(np.mean(gains[:rsi_period]))
            avg_loss = float(np.mean(losses[:rsi_period]))
            for i in range(rsi_period, len(closes)):
                if i > rsi_period:
                    avg_gain = (avg_gain * (rsi_period - 1) + gains[i - 1]) / rsi_period
                    avg_loss = (avg_loss * (rsi_period - 1) + losses[i - 1]) / rsi_period
                if avg_loss == 0:
                    rsi[i] = 100.0
                else:
                    rs = avg_gain / avg_loss
                    rsi[i] = round(100 - (100 / (1 + rs)), 2)

        # ── MACD (12, 26, 9) ──
        def ema(data, span):
            result = [None] * len(data)
            if len(data) < span:
                return result
            k = 2 / (span + 1)
            result[span - 1] = float(np.mean(data[:span]))
            for i in range(span, len(data)):
                result[i] = data[i] * k + result[i - 1] * (1 - k)
            return result

        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        macd_line = [None] * len(closes)
        for i in range(len(closes)):
            if ema12[i] is not None and ema26[i] is not None:
                macd_line[i] = round(ema12[i] - ema26[i], 4)

        macd_valid = [v for v in macd_line if v is not None]
        signal_line = [None] * len(closes)
        if len(macd_valid) >= 9:
            sig = ema(macd_valid, 9)
            offset = len(closes) - len(macd_valid)
            for i, v in enumerate(sig):
                if v is not None:
                    signal_line[i + offset] = round(v, 4)

        macd_hist = [None] * len(closes)
        for i in range(len(closes)):
            if macd_line[i] is not None and signal_line[i] is not None:
                macd_hist[i] = round(macd_line[i] - signal_line[i], 4)

        # ── Build candles with indicators ──
        candles = []
        for j, (idx, row) in enumerate(hist.iterrows()):
            candles.append({
                "time": idx.isoformat(),
                "open": round(float(row.get("Open", 0)), 4),
                "high": round(float(row.get("High", 0)), 4),
                "low": round(float(row.get("Low", 0)), 4),
                "close": round(float(row.get("Close", 0)), 4),
                "volume": int(row.get("Volume", 0) or 0),
                "bb_upper": bb_upper[j],
                "bb_mid": bb_mid[j],
                "bb_lower": bb_lower[j],
                "vwap": vwap[j],
                "rsi": rsi[j],
                "macd": macd_line[j],
                "macd_signal": signal_line[j],
                "macd_hist": macd_hist[j],
            })

        return jsonify({"symbol": symbol, "candles": candles, "timeframe": timeframe})
    except Exception as e:
        log.warning("Price history failed for %s: %s", symbol, e)
        return jsonify({"symbol": symbol, "candles": [], "error": str(e)})


# Keep old crypto endpoint as alias
@trading_bp.route("/crypto/history/<symbol>")
def get_crypto_history(symbol: str):
    """Alias — redirects to unified price-history."""
    from flask import redirect, url_for
    return redirect(url_for("trading.get_price_history", symbol=symbol,
                            timeframe=request.args.get("timeframe", "1D"), type="crypto"))


# ── Crypto Agent endpoints ───────────────────────────────────────────────────

@trading_bp.route("/agent/status")
def agent_status():
    """Get current agent state."""
    from forge.trading.crypto_agent import get_state
    return jsonify(get_state())


@trading_bp.route("/agent/logs")
def agent_logs():
    """Get recent agent log entries."""
    from forge.trading.crypto_agent import get_logs
    limit = int(request.args.get("limit", 50))
    return jsonify(get_logs(limit))


@trading_bp.route("/agent/start", methods=["POST"])
def agent_start():
    """Start the autonomous trading agent."""
    from forge.trading.crypto_agent import AgentConfig, start
    data = request.get_json() or {}
    config = AgentConfig(
        model=data.get("model", "grok-4.20-0309-reasoning"),
        strategy=data.get("strategy", "manual"),
        ticker=data.get("ticker", "BTC"),
        max_position_usd=float(data.get("max_position_usd", 50)),
        interval_minutes=int(data.get("interval_minutes", 15)),
    )
    result = start(config)
    if "error" in result:
        return jsonify(result), 409
    return jsonify(result)


@trading_bp.route("/agent/stop", methods=["POST"])
def agent_stop():
    """Stop the trading agent."""
    from forge.trading.crypto_agent import stop
    result = stop()
    if "error" in result:
        return jsonify(result), 409
    return jsonify(result)


@trading_bp.route("/agent/decisions")
def agent_decisions():
    """Get persisted agent decision history for analysis."""
    from forge.trading.portfolio import get_portfolio_manager
    ticker = request.args.get("ticker", "").strip() or None
    limit = int(request.args.get("limit", 100))
    pm = get_portfolio_manager()
    rows = pm.get_decisions(ticker=ticker, limit=limit)
    return jsonify(rows)


# ── Polymarket Proxy ──────────────────────────────────────────────────────

import requests as _req

GAMMA_BASE = "https://gamma-api.polymarket.com"


@trading_bp.route("/polymarket/markets")
def poly_markets():
    """Proxy Polymarket Gamma API — fetch active markets."""
    params = {
        "active": "true",
        "closed": "false",
        "limit": request.args.get("limit", "30"),
        "offset": request.args.get("offset", "0"),
        "order": request.args.get("order", "volume24hr"),
        "ascending": "false",
    }
    tag = request.args.get("tag", "").strip()
    if tag:
        params["tag_slug"] = tag
    q = request.args.get("q", "").strip()
    if q:
        params["_q"] = q
    try:
        resp = _req.get(f"{GAMMA_BASE}/markets", params=params, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        log.warning("Polymarket fetch failed: %s", e)
        return jsonify({"error": str(e)}), 502


@trading_bp.route("/polymarket/events")
def poly_events():
    """Proxy Polymarket Gamma API — fetch events (grouped markets)."""
    params = {
        "active": "true",
        "closed": "false",
        "limit": request.args.get("limit", "20"),
        "offset": request.args.get("offset", "0"),
        "order": request.args.get("order", "volume24hr"),
        "ascending": "false",
    }
    tag = request.args.get("tag", "").strip()
    if tag:
        params["tag_slug"] = tag
    try:
        resp = _req.get(f"{GAMMA_BASE}/events", params=params, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        log.warning("Polymarket events fetch failed: %s", e)
        return jsonify({"error": str(e)}), 502


@trading_bp.route("/polymarket/market/<slug>")
def poly_market_detail(slug: str):
    """Proxy Polymarket Gamma API — fetch single market by slug."""
    try:
        resp = _req.get(f"{GAMMA_BASE}/markets", params={"slug": slug}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return jsonify(data[0] if data else {})
    except Exception as e:
        log.warning("Polymarket detail fetch failed: %s", e)
        return jsonify({"error": str(e)}), 502


@trading_bp.route("/polymarket/event/<slug>")
def poly_event_detail(slug: str):
    """Fetch a Polymarket event by slug (with its markets)."""
    try:
        resp = _req.get(f"{GAMMA_BASE}/events", params={"slug": slug}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        event = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else {}
        # Also fetch markets for this event
        mresp = _req.get(
            f"{GAMMA_BASE}/markets",
            params={"event_slug": slug, "active": "true", "closed": "false"},
            timeout=10,
        )
        mresp.raise_for_status()
        markets = mresp.json() if isinstance(mresp.json(), list) else []
        return jsonify({"event": event, "markets": markets})
    except Exception as e:
        log.warning("Polymarket event fetch failed: %s", e)
        return jsonify({"error": str(e)}), 502


# ── Polymarket Agent endpoints ────────────────────────────────────────────────

@trading_bp.route("/polymarket/agent/status")
def poly_agent_status():
    from forge.trading.polymarket_agent import get_state
    return jsonify(get_state())


@trading_bp.route("/polymarket/agent/logs")
def poly_agent_logs():
    from forge.trading.polymarket_agent import get_logs
    limit = int(request.args.get("limit", 50))
    return jsonify(get_logs(limit))


@trading_bp.route("/polymarket/agent/start", methods=["POST"])
def poly_agent_start():
    from forge.trading.polymarket_agent import PolyAgentConfig, start
    data = request.get_json() or {}
    config = PolyAgentConfig(
        model=data.get("model", "grok-4.20-0309-reasoning"),
        strategy=data.get("strategy", "analyst"),
        event_slug=data.get("event_slug", ""),
        event_url=data.get("event_url", ""),
        max_position_usd=float(data.get("max_position_usd", 50)),
        interval_minutes=int(data.get("interval_minutes", 15)),
        live_trading=bool(data.get("live_trading", False)),
        dry_run=bool(data.get("dry_run", True)),
    )
    if not config.event_slug:
        return jsonify({"error": "event_slug is required"}), 400
    result = start(config)
    if "error" in result:
        return jsonify(result), 409
    return jsonify(result)


@trading_bp.route("/polymarket/agent/stop", methods=["POST"])
def poly_agent_stop():
    from forge.trading.polymarket_agent import stop
    result = stop()
    if "error" in result:
        return jsonify(result), 409
    return jsonify(result)
