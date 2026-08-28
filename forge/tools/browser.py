"""
Playwright browser tools for The Forge executor.

Manages a single headless Chromium instance that persists across tool calls
within a task execution. The browser is launched lazily on first use and
closed when the module-level cleanup is called.
"""
from __future__ import annotations
import base64
import json
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright, Browser, Page, Playwright

from .registry import ToolRegistry
from forge.config import DATA_DIR

log = logging.getLogger("forge.tools.browser")

# ── Browser Singleton ───────────────────────────────────────────────────────

_playwright: Playwright | None = None
_browser: Browser | None = None
_page: Page | None = None

SCREENSHOTS_DIR = DATA_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def _get_page() -> Page:
    """Get or create the browser page (lazy init)."""
    global _playwright, _browser, _page
    if _page and not _page.is_closed():
        return _page
    if not _playwright:
        _playwright = sync_playwright().start()
    if not _browser or not _browser.is_connected():
        _browser = _playwright.chromium.launch(headless=True)
    _page = _browser.new_page(viewport={"width": 1280, "height": 800})
    _page.set_default_timeout(15_000)  # 15s timeout for actions
    return _page


def close_browser():
    """Clean up browser resources."""
    global _playwright, _browser, _page
    try:
        if _page and not _page.is_closed():
            _page.close()
        if _browser and _browser.is_connected():
            _browser.close()
        if _playwright:
            _playwright.stop()
    except Exception:
        pass
    _playwright = _browser = _page = None


# ── Tool Implementations ───────────────────────────────────────────────────

def navigate(url: str) -> str:
    """Navigate to a URL and return the page title and URL."""
    try:
        from forge.security import check_public_url
        url = check_public_url(url)
        page = _get_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        status = resp.status if resp else "unknown"
        return json.dumps({
            "status": "ok",
            "url": page.url,
            "title": page.title(),
            "http_status": status,
        })
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def screenshot(filename: str = "") -> str:
    """Take a screenshot of the current page. Returns the file path."""
    try:
        page = _get_page()
        if not filename:
            # Auto-generate filename from page title
            safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in page.title())[:40]
            filename = f"{safe_title or 'screenshot'}.png"
        if not filename.endswith(".png"):
            filename += ".png"
        from forge.security import resolve_in_root
        path = resolve_in_root(SCREENSHOTS_DIR, filename)
        page.screenshot(path=str(path), full_page=False)
        return json.dumps({
            "status": "ok",
            "path": str(path),
            "url": page.url,
            "title": page.title(),
        })
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def click(selector: str) -> str:
    """Click an element matching the CSS selector."""
    try:
        page = _get_page()
        page.click(selector, timeout=10_000)
        page.wait_for_load_state("domcontentloaded", timeout=5_000)
        return json.dumps({
            "status": "ok",
            "selector": selector,
            "url": page.url,
            "title": page.title(),
        })
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def type_text(selector: str, text: str) -> str:
    """Type text into an input element matching the CSS selector."""
    try:
        page = _get_page()
        page.fill(selector, text, timeout=10_000)
        return json.dumps({
            "status": "ok",
            "selector": selector,
            "typed": text[:100],
        })
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def extract_text(selector: str = "body") -> str:
    """Extract visible text content from the page or a specific element."""
    try:
        page = _get_page()
        element = page.query_selector(selector)
        if not element:
            return json.dumps({"error": f"No element found for selector: {selector}"})
        text = element.inner_text()
        # Cap output to avoid blowing context
        if len(text) > 20_000:
            text = text[:20_000] + f"\n... [truncated, {len(text)} chars total]"
        return json.dumps({
            "status": "ok",
            "selector": selector,
            "text": text,
            "url": page.url,
        })
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def get_page_info() -> str:
    """Get current page URL, title, and a summary of visible elements."""
    try:
        page = _get_page()
        # Get counts of interactive elements
        links = page.query_selector_all("a[href]")
        buttons = page.query_selector_all("button")
        inputs = page.query_selector_all("input, textarea, select")
        return json.dumps({
            "url": page.url,
            "title": page.title(),
            "links": len(links),
            "buttons": len(buttons),
            "inputs": len(inputs),
        })
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ── Registration ────────────────────────────────────────────────────────────

def register(registry: ToolRegistry):
    registry.register(
        name="browser_navigate",
        description="Navigate the browser to a URL. Returns the page title, final URL, and HTTP status.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to (include https://)"},
            },
            "required": ["url"],
        },
        handler=navigate,
    )
    registry.register(
        name="browser_screenshot",
        description="Take a screenshot of the current browser page. Returns the saved file path.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Optional filename for the screenshot (default: auto-generated from page title)",
                },
            },
            "required": [],
        },
        handler=screenshot,
    )
    registry.register(
        name="browser_click",
        description="Click an element on the page using a CSS selector (e.g. 'button.submit', '#login', 'a[href=\"/about\"]').",
        parameters={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the element to click"},
            },
            "required": ["selector"],
        },
        handler=click,
    )
    registry.register(
        name="browser_type",
        description="Type text into an input field on the page using a CSS selector.",
        parameters={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the input element"},
                "text": {"type": "string", "description": "Text to type into the field"},
            },
            "required": ["selector", "text"],
        },
        handler=type_text,
    )
    registry.register(
        name="browser_extract_text",
        description="Extract visible text from the current page or a specific element. Use selector 'body' for full page text.",
        parameters={
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to extract text from (default: 'body' for full page)",
                },
            },
            "required": [],
        },
        handler=extract_text,
    )
    registry.register(
        name="browser_info",
        description="Get info about the current browser page: URL, title, and counts of links/buttons/inputs.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=get_page_info,
    )
