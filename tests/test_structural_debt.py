"""Structural guards for the Party Pack maintainability refactor.

These assert the shipped layout (not re-implemented logic): modular JS ownership,
blueprint registration for domain HTTP, shared orchestrator helpers, relic factory,
and CSS asset presence.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "forge" / "static"


def _funcs(text: str) -> set[str]:
    return set(re.findall(r"(?:async\s+)?function\s+(\w+)\s*\(", text))


def test_history_symbols_single_owner():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    history = (STATIC / "js" / "history.js").read_text(encoding="utf-8")
    app_f, hist_f = _funcs(app_js), _funcs(history)
    for name in (
        "renderHistory",
        "renderHistoryDetail",
        "renderHistoryDetailLegacy",
        "renderRunInspector",
    ):
        assert name in hist_f
        assert name not in app_f


def test_app_js_under_half_baseline():
    lines = len((STATIC / "app.js").read_text(encoding="utf-8").splitlines())
    assert lines <= 2817


def test_index_loads_modular_scripts():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for src in (
        "/static/js/core.js",
        "/static/js/history.js",
        "/static/js/runner.js",
        "/static/js/chess.js",
        "/static/js/nes.js",
        "/static/js/keys.js",
        "/static/js/trading.js",
        "/static/js/prophecy.js",
        "/static/app.js",
    ):
        assert src in html
        path = ROOT / src.lstrip("/").replace("static/", "forge/static/", 1)
        # /static/X maps to forge/static/X
        path = ROOT / "forge" / src[len("/static/") :]
        if src.startswith("/static/"):
            path = ROOT / "forge" / "static" / src[len("/static/") :]
        assert path.exists(), path


def test_app_py_under_1000_and_no_domain_routes():
    app_py = (ROOT / "forge" / "app.py").read_text(encoding="utf-8")
    assert len(app_py.splitlines()) < 1000
    for path in ("/api/chess", "/api/nes", "/api/keys"):
        assert not re.search(rf"@app\.route\([\"'].*{re.escape(path)}", app_py)
    assert "keys_bp" in app_py
    assert "chess_bp" in app_py
    assert "nes_bp" in app_py


def test_domain_blueprints_register_routes():
    from forge.app import app

    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/keys" in rules
    assert "/api/chess" in rules
    assert "/api/nes/roms" in rules


def test_relic_factory_and_two_distinct_relics():
    from relics.bootstrap import create_relic_app
    from relics.host import create_host_app
    import importlib.util

    assert create_relic_app("x").name == "x"

    def load(rel: str):
        path = ROOT / rel / "web_app.py"
        spec = importlib.util.spec_from_file_location(rel.replace("-", "_"), path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod

    bestiary = load("relic-bestiary")
    tavern = load("relic-tavern")
    b = bestiary.app.test_client().get("/")
    t = tavern.app.test_client().get("/")
    assert b.status_code == 200 and t.status_code == 200
    assert b.data != t.data

    host = create_host_app()
    hb = host.test_client().get("/relics/bestiary/")
    ht = host.test_client().get("/relics/tavern/")
    assert hb.status_code == 200 and ht.status_code == 200
    assert hb.data != ht.data


def test_css_assets_exist_and_lcars_is_primary():
    style = STATIC / "style.css"
    lcars = STATIC / "lcars.css"
    assert style.exists() and lcars.exists()
    style_text = style.read_text(encoding="utf-8")
    lcars_text = lcars.read_text(encoding="utf-8")
    assert "THIN REMNANT" in style_text or "remnant" in style_text.lower()
    assert "AUTHORITATIVE" in lcars_text or "Primary visual language" in lcars_text
    # Remnant should stay smaller than LCARS
    assert len(style_text.splitlines()) < len(lcars_text.splitlines())
