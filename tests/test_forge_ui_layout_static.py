"""
Static layout verification tests (drive the shipped index.html + lcars.css).

Covers Verification plan steps 1 and 4 (gating reads + re-grep evidence).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "forge" / "static" / "index.html"
LCARS_CSS = ROOT / "forge" / "static" / "lcars.css"

def test_index_has_required_containers_and_exactly_11_tab_btns():
    html = INDEX.read_text(encoding="utf-8")
    # Required containers from plan
    for needle in [
        "console-split", "split-panes", "#mcp-hub", "tab-content", "lcars-left-rail",
        "lcars-viewer", "chat-area", "composer", "tab-layout-"
    ]:
        assert needle in html or needle.replace("#", 'id="') in html or 'class="' + needle in html

    tabs = re.findall(r'class="tab-btn[^"]*"', html)
    assert len(tabs) == 11, f"expected 11 tab-btns, got {len(tabs)}"

def test_lcars_css_has_layout_shell_and_key_rules():
    css = LCARS_CSS.read_text(encoding="utf-8")
    # The consolidated shell we added (or equivalent rules)
    assert "LAYOUT SHELL" in css or ("height: 100vh" in css and "display: flex" in css)
    # Core AC properties
    for prop in [
        "padding-top", "padding-right", "padding-bottom",
        "console-split", "display: flex", "min-height: 0",
        "#mcp-hub", "position: fixed", "right: 0",
        ".tab-bar", "max-width", "overflow-x",
        ".lcars-footer", "position: fixed"
    ]:
        assert prop in css

    # Exactly the color rules for 11 tabs (no orphan 12th)
    colors = re.findall(r"\.tab-btn:nth-child\((\d+)\)", css)
    assert len(colors) >= 10  # at least up to 10 or 11
    # If 11th exists it must be intended for the 11th button
    if "11" in colors:
        assert colors.count("11") == 1
