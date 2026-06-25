"""
Live layout verification.

Ports the working playwright probe logic into committed test code.
Drive the shipped server + real clicks on all data-tab buttons from HTML.
Covers Verification plan step 2 for the live probe + tab switching.

"""
import re
import time
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "forge" / "static" / "index.html"

def _get_tab_names():
    html = INDEX.read_text(encoding="utf-8")
    return re.findall(r'data-tab="([^"]+)"', html)

def test_live_tab_bar_renders_11_and_all_tabs_activate_panel_and_no_crowding():
    """Launch real app, load in playwright, assert 11 tabs, click each, active panel usable, header not crowded."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    # Launch server in background (same as ui-probe)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "forge" / "app.py")],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("http://127.0.0.1:5000", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(800)

            tabs = page.query_selector_all(".tab-btn")
            assert len(tabs) == 11, f"expected 11, got {len(tabs)}"

            tab_names = _get_tab_names()
            for name in tab_names:
                btn = page.query_selector(f'button.tab-btn[data-tab="{name}"]')
                assert btn is not None
                btn.click()
                page.wait_for_timeout(200)
                active = page.query_selector(".tab-content.active")
                assert active is not None
                h = active.evaluate("el => el.clientHeight")
                w = active.evaluate("el => el.clientWidth")
                assert h >= 30 and w >= 100, f"tab {name} active panel too small"

            # header crowding
            header = page.evaluate("""() => {
                const bar = document.querySelector('.tab-bar');
                const btns = document.querySelectorAll('.tab-btn');
                if (!bar || btns.length !== 11) return false;
                let ok = true;
                btns.forEach(b => { const r = b.getBoundingClientRect(); if (r.width < 5 || r.height < 5) ok = false; });
                const contained = bar.scrollWidth <= (bar.clientWidth + 50);
                return ok && contained;
            }""")
            assert header, "header tab bar crowding or overflow"

            browser.close()
    finally:
        try:
            if sys.platform == "win32":
                subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
        except Exception:
            pass
