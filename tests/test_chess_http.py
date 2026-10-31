"""Chess HTTP: model catalog, CORS for standalone BattleChess, match create."""
from __future__ import annotations

import pytest


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "forge.chess_arena._lmstudio_available_models", lambda _url: []
    )
    from forge.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_chess_models_catalog(client):
    r = client.get("/api/chess/models")
    assert r.status_code == 200
    data = r.get_json()
    assert "models" in data and "defaults" in data
    ids = {m["id"] for m in data["models"]}
    assert "auto" not in ids
    assert "grok-4.20-0309-reasoning" in ids
    assert data["defaults"]["white_model"] in ids
    assert data["defaults"]["judge_model"] in ids


def test_chess_models_cors_for_standalone_origin(client):
    origin = "http://127.0.0.1:8791"
    r = client.get("/api/chess/models", headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Origin") == origin

    pre = client.open(
        "/api/chess",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert pre.status_code in (200, 204)
    assert pre.headers.get("Access-Control-Allow-Origin") == origin
    allow = pre.headers.get("Access-Control-Allow-Methods", "")
    assert "POST" in allow


def test_chess_create_requires_models(client):
    r = client.post("/api/chess", json={})
    assert r.status_code == 400
    assert "white_model" in (r.get_json() or {}).get("error", "")


def test_chess_create_ai_vs_ai(client):
    r = client.post("/api/chess", json={
        "white_model": "grok-4-1-fast-non-reasoning",
        "black_model": "grok-4-1-fast-non-reasoning",
        "judge_model": "grok-4-1-fast-non-reasoning",
        "commentary_interval": 99,
    })
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "active"
    assert data["white_model"] == "grok-4-1-fast-non-reasoning"
    assert data["black_model"] == "grok-4-1-fast-non-reasoning"
    assert data["fen"]
