"""Flask Blueprint for API keys vault."""
from __future__ import annotations
import logging
from flask import Blueprint, jsonify, request
from forge import secrets_vault as _vault

log = logging.getLogger("forge.keys_api")
keys_bp = Blueprint("keys", __name__)

@keys_bp.route("/api/keys")
def keys_list():
    """Return the full provider list with masked status."""
    return jsonify({"providers": _vault.list_keys()})


@keys_bp.route("/api/keys/<provider_id>", methods=["POST"])
def keys_set(provider_id: str):
    """Set a provider's key. Body: {value: "..."}. Empty value clears it."""
    if _vault.get_provider(provider_id) is None:
        return jsonify({"error": f"Unknown provider: {provider_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    value = body.get("value", "")
    if not isinstance(value, str):
        return jsonify({"error": "value must be a string"}), 400
    try:
        record = _vault.set_key(provider_id, value)
    except Exception as e:
        log.exception("keys_set failed for %s", provider_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    return jsonify(record)


@keys_bp.route("/api/keys/<provider_id>", methods=["DELETE"])
def keys_clear(provider_id: str):
    """Clear a provider's key."""
    if _vault.get_provider(provider_id) is None:
        return jsonify({"error": f"Unknown provider: {provider_id!r}"}), 404
    try:
        record = _vault.clear_key(provider_id)
    except Exception as e:
        log.exception("keys_clear failed for %s", provider_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    return jsonify(record)
