"""
Flask Blueprint for toll protocol API endpoints.

All routes prefixed with /api/toll/.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from forge.security import require_auth
from forge.config import TOLL_DB_PATH
from forge.toll.ledger import Ledger
from forge.toll.rates import RateEngine

log = logging.getLogger("forge.toll.endpoints")

toll_bp = Blueprint("toll", __name__, url_prefix="/api/toll")

# Shared instances — initialized on first request
_ledger: Ledger | None = None
_rate_engine: RateEngine | None = None


def _get_ledger() -> Ledger:
    global _ledger
    if _ledger is None:
        _ledger = Ledger(TOLL_DB_PATH)
    return _ledger


def _get_rate_engine() -> RateEngine:
    global _rate_engine
    if _rate_engine is None:
        _rate_engine = RateEngine()
    return _rate_engine


# ── Wallet Endpoints ──────────────────────────────────────────────────────

@toll_bp.route("/balance")
def get_all_balances():
    """All wallets."""
    wallets = _get_ledger().get_all_wallets()
    return jsonify([w.model_dump() for w in wallets])


@toll_bp.route("/balance/<agent_id>")
def get_balance(agent_id: str):
    """Single wallet balance."""
    w = _get_ledger().get_wallet(agent_id)
    if not w:
        return jsonify({"error": f"No wallet for agent '{agent_id}'"}), 404
    return jsonify(w.model_dump())


@toll_bp.route("/deposit", methods=["POST"])
def deposit():
    """Deposit funds into an agent's wallet."""
    data = request.get_json() or {}
    agent_id = data.get("agent_id", "").strip()
    amount = data.get("amount_usd", 0)
    if not agent_id or amount <= 0:
        return jsonify({"error": "agent_id and positive amount_usd required"}), 400
    tx = _get_ledger().deposit(agent_id, amount)
    new_balance = _get_ledger().get_balance(agent_id)
    return jsonify({"transaction": tx.model_dump(), "new_balance": new_balance})


# ── Transaction Endpoints ─────────────────────────────────────────────────

@toll_bp.route("/transactions")
def get_transactions():
    """Transaction history, optionally filtered by agent."""
    agent_id = request.args.get("agent_id", "").strip() or None
    limit = int(request.args.get("limit", "50"))
    txs = _get_ledger().get_transactions(agent_id, limit)
    return jsonify([tx.model_dump() for tx in txs])


# ── Rate Endpoints ────────────────────────────────────────────────────────

@toll_bp.route("/rates")
def get_rates():
    """Current toll rate configuration."""
    return jsonify(_get_rate_engine().all_rates())


@toll_bp.route("/rates", methods=["PUT"])
@require_auth
def update_rates():
    """Update toll rates."""
    from forge.toll.models import TollRate
    data = request.get_json() or {}
    engine = _get_rate_engine()
    for msg_type, rate_data in data.items():
        rate_data["message_type"] = msg_type
        engine.set_rate(msg_type, TollRate(**rate_data))
    return jsonify({"status": "updated", "rates": engine.all_rates()})


# ── Revenue Endpoints ─────────────────────────────────────────────────────

@toll_bp.route("/revenue")
def get_revenue():
    """Creator revenue dashboard."""
    ledger = _get_ledger()
    return jsonify({
        "total_revenue_usd": round(ledger.get_creator_revenue(), 8),
        "revenue_by_session": ledger.get_revenue_by_session(),
    })


# ── Session Summary ───────────────────────────────────────────────────────

@toll_bp.route("/summary/<session_id>")
def get_summary(session_id: str):
    """Toll summary for a specific task session."""
    summary = _get_ledger().get_session_summary(session_id)
    return jsonify(summary.model_dump())


# ── Admin ──────────────────────────────────────────────────────────────────

@toll_bp.route("/reset", methods=["POST"])
def reset():
    """Reset all toll data (dev/testing)."""
    _get_ledger().reset()
    return jsonify({"status": "reset"})


# ── Settlement Export (Beat 3 hook) ────────────────────────────────────────

@toll_bp.route("/export")
def export_for_settlement():
    """Export unsettled transactions for blockchain settlement."""
    since = request.args.get("since", "")
    txs = _get_ledger().export_for_settlement(since)
    return jsonify([tx.model_dump() for tx in txs])
