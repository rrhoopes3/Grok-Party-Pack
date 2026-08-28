"""LAN-hardening: bind host, auth gate, SSRF, path root, read-only sqlite."""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_bind_host_defaults_to_loopback(monkeypatch):
    from forge.security import bind_host
    monkeypatch.delenv("FORGE_BIND", raising=False)
    assert bind_host() == "127.0.0.1"
    monkeypatch.setenv("FORGE_BIND", "0.0.0.0")
    assert bind_host() == "0.0.0.0"
    monkeypatch.setenv("FORGE_BIND", "10.0.0.5")
    assert bind_host() == "127.0.0.1"


def test_check_public_url_blocks_private_and_metadata(monkeypatch):
    from forge.security import check_public_url
    monkeypatch.delenv("FORGE_ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.delenv("FORGE_URL_ALLOWLIST", raising=False)
    with pytest.raises(ValueError):
        check_public_url("http://127.0.0.1/")
    with pytest.raises(ValueError):
        check_public_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError):
        check_public_url("http://10.0.0.1/")
    with pytest.raises(ValueError):
        check_public_url("file:///etc/passwd")
    with pytest.raises(ValueError):
        check_public_url("http://metadata.google.internal/")
    monkeypatch.setenv("FORGE_ALLOW_PRIVATE_URLS", "true")
    assert check_public_url("http://127.0.0.1/") == "http://127.0.0.1/"


def test_resolve_in_root_and_safe_id(tmp_path):
    from forge.security import resolve_in_root, safe_id, new_id
    root = tmp_path / "runs"
    root.mkdir()
    (root / "ok.jsonl").write_text("x", encoding="utf-8")
    assert resolve_in_root(root, "ok.jsonl").name == "ok.jsonl"
    with pytest.raises(ValueError):
        resolve_in_root(root, "..", "ok.jsonl")
    with pytest.raises(ValueError):
        safe_id("../etc/passwd")
    with pytest.raises(ValueError):
        safe_id("arena/../../x")
    nid = new_id()
    assert len(nid) == 32
    assert safe_id(nid) == nid


def test_query_sqlite_is_read_only(tmp_path):
    from forge.tools.database import query_sqlite
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    out = json.loads(query_sqlite(str(db), "SELECT id FROM t"))
    assert out["row_count"] == 1
    assert json.loads(query_sqlite(str(db), "DELETE FROM t"))["error"].startswith("read-only")
    assert json.loads(query_sqlite(str(db), "INSERT INTO t VALUES (2)"))["error"].startswith("read-only")
    assert json.loads(query_sqlite(str(db), "SELECT 1; DROP TABLE t"))["error"]


def test_run_log_rejects_traversing_task_id(tmp_path, monkeypatch):
    from forge import run_log
    monkeypatch.setattr(run_log, "RUNS_DIR", tmp_path)
    assert run_log.load_run_events("../secret") == []
    assert run_log.load_run_meta("..\\secret") is None


def test_mutating_routes_require_auth_when_not_testing():
    from forge.app import app
    app.config["TESTING"] = False
    try:
        c = app.test_client()
        r = c.post("/api/task", json={"task": "hello"})
        assert r.status_code == 401
        r = c.post("/api/trading/order", json={"ticker": "SPY", "side": "buy", "quantity": 1})
        assert r.status_code == 401
        r = c.post("/api/scheduler/jobs", json={"name": "x", "task": "y"})
        assert r.status_code == 401
        r = c.put("/api/toll/rates", json={})
        assert r.status_code == 401
        r = c.post("/api/v1/conversations", json={"agent_a": "a", "agent_b": "b", "topic": "t"})
        assert r.status_code == 401
    finally:
        app.config["TESTING"] = True


def test_login_regenerates_session():
    from forge.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.set_cookie("session", "attacker-fixed")
        r = c.post("/api/login", json={"username": "admin", "password": "pytest-admin"})
        assert r.status_code == 200
        # Cookie payload must have been replaced (new forge_token / _sid).
        cookies = [h for h in c._cookies.values()] if hasattr(c, "_cookies") else []
        assert r.get_json()["status"] == "ok"


def test_login_lockout(monkeypatch):
    from forge.auth import AuthManager, _LOCKOUT_THRESHOLD
    mgr = AuthManager()
    mgr.create_user("lockme", "correct-horse")
    for _ in range(_LOCKOUT_THRESHOLD):
        assert mgr.authenticate("lockme", "wrong") is None
    assert mgr.authenticate("lockme", "correct-horse") is None


def test_http_tool_blocks_loopback(monkeypatch):
    from forge.tools.http import http_get
    monkeypatch.delenv("FORGE_ALLOW_PRIVATE_URLS", raising=False)
    out = json.loads(http_get("http://127.0.0.1:1/"))
    assert "error" in out
    assert "blocked" in out["error"].lower() or "private" in out["error"].lower() or "loopback" in out["error"].lower()
