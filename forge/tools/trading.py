"""
Trading tools for the Forge agent.

Enables the agent to query PCR data, analyze sentiment, execute trades,
and manage portfolio through natural language.
"""
from __future__ import annotations

import json
import logging

from .registry import ToolRegistry

log = logging.getLogger("forge.tools.trading")


def fetch_pcr(ticker: str, expiry: str = "", provider: str = "") -> str:
    """Fetch Put/Call Ratio for a ticker."""
    try:
        from forge.trading.engine import get_engine
        result = get_engine().get_pcr(ticker, expiry=expiry, provider=provider)
        return json.dumps(result.to_dict())
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def analyze_sentiment(tickers: str, provider: str = "") -> str:
    """Analyze PCR sentiment across multiple tickers.

    tickers: comma-separated ticker symbols (e.g. "SPY,QQQ,IWM")
    """
    try:
        from forge.trading.engine import get_engine
        ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
        if not ticker_list:
            return json.dumps({"error": "No tickers provided"})
        result = get_engine().analyze_sentiment(ticker_list, provider=provider)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def get_options_chain(ticker: str, expiry: str = "", provider: str = "",
                      min_volume: str = "0") -> str:
    """Get raw options chain data for a ticker, capped at 50 rows per side."""
    try:
        from forge.trading.engine import get_engine
        chain = get_engine().get_options_chain(
            ticker, expiry, provider, int(min_volume),
        )
        # Cap output for token efficiency
        data = chain.to_dict()
        data["calls"] = data["calls"][:50]
        data["puts"] = data["puts"][:50]
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def set_alert(ticker: str, metric: str = "vol_ratio", threshold: str = "1.0",
              direction: str = "above") -> str:
    """Set a PCR alert. Triggers when the metric crosses the threshold."""
    try:
        from forge.trading.engine import get_engine
        alert = get_engine().set_alert(ticker, metric, float(threshold), direction)
        return json.dumps({"status": "alert_set", **alert.to_dict()})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def get_portfolio(provider: str = "") -> str:
    """Get current portfolio positions, P&L, and summary."""
    try:
        from forge.trading.portfolio_view import build_portfolio_summary

        summary = build_portfolio_summary(provider_name=provider)
        return json.dumps(summary)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def execute_trade(ticker: str, side: str, quantity: str,
                  order_type: str = "market", price: str = "",
                  provider: str = "") -> str:
    """Execute a trade order. Paper mode by default.

    ticker: stock/crypto symbol
    side: "buy" or "sell"
    quantity: number of shares/units
    order_type: "market" or "limit"
    price: limit price (required for limit orders)
    """
    try:
        from forge.trading.brokers import get_broker
        broker = get_broker(provider)
        result = broker.place_order(
            ticker=ticker,
            side=side,
            quantity=float(quantity),
            order_type=order_type,
            price=float(price) if price else None,
        )
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def get_market_quote(ticker: str, provider: str = "") -> str:
    """Get current price quote for a ticker."""
    try:
        from forge.trading.engine import get_engine
        q = get_engine().get_quote(ticker, provider=provider)
        return json.dumps({
            "ticker": q.ticker, "price": q.price,
            "change": q.change, "change_pct": q.change_pct,
            "volume": q.volume,
        })
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def start_trading_agent(ticker: str, strategy: str = "momentum",
                        max_position_usd: str = "50",
                        interval_minutes: str = "15",
                        model: str = "grok-4.20-0309-reasoning") -> str:
    """Start the autonomous crypto trading agent."""
    try:
        from forge.trading.crypto_agent import AgentConfig, start
        config = AgentConfig(
            model=model,
            strategy=strategy,
            ticker=ticker.upper(),
            max_position_usd=float(max_position_usd),
            interval_minutes=int(interval_minutes),
        )
        result = start(config)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def stop_trading_agent() -> str:
    """Stop the autonomous crypto trading agent."""
    try:
        from forge.trading.crypto_agent import stop
        result = stop()
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def get_trading_agent_status() -> str:
    """Get the current status of the trading agent."""
    try:
        from forge.trading.crypto_agent import get_state
        return json.dumps(get_state())
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# -- Registration ------------------------------------------------------------

def register(registry: ToolRegistry):
    registry.register(
        name="fetch_pcr",
        description=(
            "Fetch Put/Call Ratio (PCR) for a stock ticker. Returns vol_ratio, oi_ratio, "
            "sentiment (bullish/bearish/neutral), and raw put/call volumes. "
            "PCR > 1.2 = bearish, < 0.7 = bullish."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol (e.g. SPY, QQQ, ^SPX)"},
                "expiry": {"type": "string", "description": "Option expiration date (ISO format). Defaults to nearest."},
                "provider": {"type": "string", "description": "Data provider: yfinance (free) or tradier (real-time)"},
            },
            "required": ["ticker"],
        },
        handler=fetch_pcr,
    )

    registry.register(
        name="analyze_sentiment",
        description=(
            "Analyze PCR sentiment across multiple tickers at once. Returns per-ticker "
            "sentiment and a market-wide summary (bullish/bearish/mixed)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tickers": {"type": "string", "description": "Comma-separated ticker symbols (e.g. 'SPY,QQQ,IWM')"},
                "provider": {"type": "string", "description": "Data provider: yfinance or tradier"},
            },
            "required": ["tickers"],
        },
        handler=analyze_sentiment,
    )

    registry.register(
        name="get_options_chain",
        description=(
            "Get raw options chain data (strikes, volumes, OI) for a ticker. "
            "Capped at 50 rows per side. Use min_volume to filter low-activity strikes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"},
                "expiry": {"type": "string", "description": "Expiration date. Defaults to nearest."},
                "provider": {"type": "string", "description": "Data provider"},
                "min_volume": {"type": "string", "description": "Minimum volume filter (default: 0)"},
            },
            "required": ["ticker"],
        },
        handler=get_options_chain,
    )

    registry.register(
        name="set_alert",
        description=(
            "Set a PCR threshold alert. Triggers when vol_ratio or oi_ratio crosses "
            "the threshold in the specified direction."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"},
                "metric": {"type": "string", "description": "Metric to watch: vol_ratio or oi_ratio"},
                "threshold": {"type": "string", "description": "Threshold value (e.g. '1.2')"},
                "direction": {"type": "string", "description": "Trigger direction: above or below"},
            },
            "required": ["ticker"],
        },
        handler=set_alert,
    )

    registry.register(
        name="get_portfolio",
        description=(
            "Get locally tracked portfolio: positions, unrealized/realized P&L, "
            "and market values with live price updates. When provider is set to a "
            "live Robinhood crypto provider, prefer brokerage-backed holdings."
        ),
        parameters={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Optional provider override (e.g. robinhood-crypto)",
                },
            },
        },
        handler=get_portfolio,
    )

    registry.register(
        name="execute_trade",
        description=(
            "Execute a LIVE buy or sell order through the connected brokerage. "
            "THIS SPENDS REAL MONEY if paper mode is off. "
            "ALWAYS confirm with the user and quote the current price before calling this. "
            "Supports market and limit orders. Returns fill confirmation or error."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock/crypto ticker symbol"},
                "side": {"type": "string", "description": "Order side: buy or sell"},
                "quantity": {"type": "string", "description": "Number of shares/units to trade"},
                "order_type": {"type": "string", "description": "Order type: market (default) or limit"},
                "price": {"type": "string", "description": "Limit price (required for limit orders)"},
                "provider": {"type": "string", "description": "Optional broker/provider override"},
            },
            "required": ["ticker", "side", "quantity"],
        },
        handler=execute_trade,
    )

    registry.register(
        name="get_market_quote",
        description="Get current price, change, and volume for a stock or crypto ticker.",
        parameters={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker symbol (e.g. SPY, BTC)"},
                "provider": {"type": "string", "description": "Data provider: yfinance, tradier, or robinhood"},
            },
            "required": ["ticker"],
        },
        handler=get_market_quote,
    )

    registry.register(
        name="start_trading_agent",
        description=(
            "Start the autonomous crypto trading agent. It runs on a timer, analyzing "
            "the market and executing trades based on the chosen strategy. "
            "Strategies: 'manual' (recommend only), 'dca' (dollar cost average), "
            "'momentum' (trend follow), 'grid' (range bound). "
            "The agent runs until stopped with stop_trading_agent."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Crypto ticker (e.g. BTC, DOGE, XRP)"},
                "strategy": {"type": "string", "description": "Strategy: manual, dca, momentum, or grid"},
                "max_position_usd": {"type": "string", "description": "Max position size in USD (default: 50)"},
                "interval_minutes": {"type": "string", "description": "Minutes between cycles (default: 15)"},
                "model": {"type": "string", "description": "AI model to use (default: grok-4.20-0309-reasoning)"},
            },
            "required": ["ticker"],
        },
        handler=start_trading_agent,
    )

    registry.register(
        name="stop_trading_agent",
        description="Stop the autonomous crypto trading agent immediately.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=stop_trading_agent,
    )

    registry.register(
        name="get_trading_agent_status",
        description="Check if the trading agent is running, its config, last decision, and cycle count.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=get_trading_agent_status,
    )
