"""
Public Demo Mode — safe BYOK deployment for The Forge.

Enables public-facing deployment where users provide their own API keys.
Keys are sent per-request via headers, held in memory only for the
duration of the API call, and never written to disk or logged.

Security:
    - Dangerous tools (shell, python, filesystem write/delete) are disabled
    - Per-session rate limiting
    - HTTPS enforcement (configurable)
    - Keys extracted from request headers, injected into thread-local config
    - No persistent user data between sessions

Usage:
    FORGE_PUBLIC_MODE=true python forge/app.py

    # Client sends keys in headers:
    X-Forge-XAI-Key: xai-xxx
    X-Forge-OpenAI-Key: sk-xxx
    X-Forge-Anthropic-Key: sk-ant-xxx
    X-Forge-GitHub-Token: ghp_xxx
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from functools import wraps

log = logging.getLogger("forge.public_mode")

# ── Thread-local key storage ─────────────────────────────────────────────

_thread_keys = threading.local()


def get_request_key(provider: str) -> str:
    """Get an API key for the current request (thread-local).

    Falls back to the global config if no per-request key is set.
    """
    keys = getattr(_thread_keys, "keys", {})
    key = keys.get(provider, "")
    if key:
        return key
    # Fall back to global config
    from forge import config
    fallback_map = {
        "xai": config.XAI_API_KEY or "",
        "openai": config.OPENAI_API_KEY or "",
        "anthropic": config.ANTHROPIC_API_KEY or "",
        "github": config.GITHUB_TOKEN or "",
    }
    return fallback_map.get(provider, "")


def set_request_keys(keys: dict[str, str]) -> None:
    """Set API keys for the current request thread."""
    _thread_keys.keys = keys


def clear_request_keys() -> None:
    """Clear keys for the current thread."""
    _thread_keys.keys = {}


# ── Safe Tool Allowlist ──────────────────────────────────────────────────

# Tools that are safe for public use (no code execution, no filesystem writes)
SAFE_TOOLS = {
    # Read-only filesystem
    "read_file", "list_directory", "find_files", "grep_files",

    # HTTP (read-only, capped body)
    "http_get",

    # Browser (read-only)
    "browser_navigate", "browser_screenshot", "browser_extract_text", "browser_info",

    # Database (enforced read-only: SELECT/PRAGMA/EXPLAIN/WITH, no commit)
    "query_sqlite",

    # Image ops (safe transforms)
    "resize_image", "convert_image",

    # Image/audio generation (uses user's own OpenAI key)
    "generate_image", "generate_speech", "transcribe_audio",

    # GitHub (uses user's own token)
    "github_list_issues", "github_get_issue", "github_create_issue",
    "github_create_pr", "github_pr_review", "github_ci_status",
    "github_list_repos", "github_search_code",

    # RAG (query only — no ingest in public mode)
    "rag_query", "rag_status",

    # Prophecy (uses user's API key for LLM calls)
    "prophecy_create", "prophecy_run", "prophecy_report", "prophecy_full",
    "prophecy_status", "prophecy_interview", "prophecy_list",

    # TRIBE v2 (local model, no API key needed)
    "tribe_neuro_score", "tribe_compare", "tribe_roi_breakdown",

    # Fake audio detection (read-only analysis)
    "fake_audio_detect", "fake_audio_scan", "fake_audio_neuro_compare",

    # Deception / veracity detection (read-only analysis)
    "veracity_analyze", "veracity_baseline", "veracity_compare", "veracity_quick",

    # Generative UI (renders in user's browser, sandboxed iframe)
    "render_widget",

    # Escalation
    "escalate_to_human",
}

# Tools explicitly blocked in public mode
BLOCKED_TOOLS = {
    "run_command", "run_python",                    # arbitrary code execution
    "write_file", "append_file", "delete_file",     # filesystem writes
    "zip_files", "extract_archive",                 # archive ops (write)
    "copy_to_clipboard", "read_clipboard",          # host clipboard
    "git_commit",                                   # git writes
    "rag_ingest", "rag_clear",                      # vector store writes
    "execute_trade", "start_trading_agent",          # real money
    "stop_trading_agent",
    "surgeon_operate",                              # model modification
    "http_post",                                    # arbitrary POST requests
    "email_create_alias", "email_add_domain",       # email writes
    "email_block_sender",
}


# ── Rate Limiting ────────────────────────────────────────────────────────

class RateLimiter:
    """Simple per-IP rate limiter."""

    def __init__(
        self,
        max_requests_per_minute: int = 10,
        max_requests_per_hour: int = 60,
        max_tasks_per_day: int = 200,
    ):
        self.max_rpm = max_requests_per_minute
        self.max_rph = max_requests_per_hour
        self.max_rpd = max_tasks_per_day
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, client_ip: str) -> tuple[bool, str]:
        """Check if a request is allowed. Returns (allowed, reason)."""
        now = time.time()
        with self._lock:
            timestamps = self._requests[client_ip]
            # Prune old entries
            timestamps[:] = [t for t in timestamps if now - t < 86400]

            minute_count = sum(1 for t in timestamps if now - t < 60)
            hour_count = sum(1 for t in timestamps if now - t < 3600)
            day_count = len(timestamps)

            if minute_count >= self.max_rpm:
                return False, f"Rate limit: max {self.max_rpm} requests/minute"
            if hour_count >= self.max_rph:
                return False, f"Rate limit: max {self.max_rph} requests/hour"
            if day_count >= self.max_rpd:
                return False, f"Rate limit: max {self.max_rpd} requests/day"

            timestamps.append(now)
            return True, ""

    def get_usage(self, client_ip: str) -> dict:
        """Get current usage stats for an IP."""
        now = time.time()
        with self._lock:
            timestamps = self._requests.get(client_ip, [])
            return {
                "minute": sum(1 for t in timestamps if now - t < 60),
                "hour": sum(1 for t in timestamps if now - t < 3600),
                "day": sum(1 for t in timestamps if now - t < 86400),
                "limits": {
                    "per_minute": self.max_rpm,
                    "per_hour": self.max_rph,
                    "per_day": self.max_rpd,
                },
            }


# ── Singleton ────────────────────────────────────────────────────────────

_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


# ── Flask Integration ────────────────────────────────────────────────────

def init_public_mode(app) -> None:
    """Configure Flask app for public demo mode."""
    from flask import request, jsonify, g, Response

    limiter = get_rate_limiter()

    @app.before_request
    def public_mode_middleware():
        # Skip for static files
        if request.path.startswith("/static/"):
            return
        if request.path == "/favicon.ico":
            return

        # HTTPS enforcement (check X-Forwarded-Proto for reverse proxies)
        from forge.config import PUBLIC_REQUIRE_HTTPS
        if PUBLIC_REQUIRE_HTTPS:
            proto = request.headers.get("X-Forwarded-Proto", request.scheme)
            if proto != "https":
                return jsonify({"error": "HTTPS required"}), 403

        # Serve setup page
        if request.path == "/setup":
            from flask import Response
            return Response(_render_setup_page(), content_type="text/html")

        # Extract API keys from headers (never logged, never persisted)
        keys = {}
        key_headers = {
            "X-Forge-XAI-Key": "xai",
            "X-Forge-OpenAI-Key": "openai",
            "X-Forge-Anthropic-Key": "anthropic",
            "X-Forge-GitHub-Token": "github",
        }
        for header, provider in key_headers.items():
            val = request.headers.get(header, "").strip()
            if val:
                keys[provider] = val

        set_request_keys(keys)
        g.byok_keys = keys
        g.has_keys = bool(keys)

        # Rate limiting for task submission
        if request.path in ("/api/task", "/api/arena") and request.method == "POST":
            client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            allowed, reason = limiter.check(client_ip)
            if not allowed:
                return jsonify({"error": reason}), 429

    @app.after_request
    def cleanup_keys(response):
        clear_request_keys()
        return response

    # Public-mode info endpoint
    @app.route("/api/public-info")
    def public_info():
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        keys = getattr(g, "byok_keys", {})
        return jsonify({
            "public_mode": True,
            "safe_tools": sorted(SAFE_TOOLS),
            "blocked_tools": sorted(BLOCKED_TOOLS),
            "keys_configured": {k: bool(v) for k, v in keys.items()},
            "rate_limit": limiter.get_usage(client_ip),
        })

    # Override the landing page in public mode
    @app.route("/setup")
    def setup_page():
        return _render_setup_page()

    log.info(
        "Public demo mode enabled — %d safe tools, %d blocked",
        len(SAFE_TOOLS), len(BLOCKED_TOOLS),
    )


def _render_setup_page() -> str:
    """Render the BYOK key setup page."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Forge | Setup</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: #080808; color: #c8c8c8; font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
    display: flex; flex-direction: column; align-items: center; min-height: 100vh;
    padding: 2rem 1rem;
}
.setup-container { max-width: 640px; width: 100%; }
.logo { font-size: 2.4rem; font-weight: 900; letter-spacing: 4px; color: #00d4ff;
         text-align: center; margin-bottom: 0.5rem; }
.tagline { text-align: center; color: #666; margin-bottom: 2rem; font-size: 0.85rem; }

.card {
    background: #111; border: 1px solid #222; border-radius: 10px;
    padding: 1.5rem; margin-bottom: 1.2rem;
}
.card h2 { font-size: 1rem; color: #00d4ff; margin-bottom: 0.8rem; letter-spacing: 1px; }
.card p { font-size: 0.82rem; color: #888; line-height: 1.6; margin-bottom: 1rem; }

.key-field { margin-bottom: 0.8rem; }
.key-field label {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 0.75rem; color: #888; margin-bottom: 0.3rem;
}
.key-field .provider-badge {
    font-size: 0.65rem; padding: 2px 6px; border-radius: 4px;
    background: #1a1a1a; color: #666;
}
.key-field .provider-badge.required { background: #1a0a0a; color: #ff6b6b; }
.key-field input {
    width: 100%; padding: 0.6rem 0.8rem; background: #0a0a0a; border: 1px solid #333;
    border-radius: 6px; color: #e0e0e0; font-family: inherit; font-size: 0.8rem;
    transition: border-color 0.2s;
}
.key-field input:focus { outline: none; border-color: #00d4ff; }
.key-field input.has-value { border-color: #0a4a0a; }
.key-field .hint { font-size: 0.68rem; color: #555; margin-top: 0.2rem; }

.trust-box {
    background: #0a1a0a; border: 1px solid #1a3a1a; border-radius: 8px;
    padding: 1rem; margin-bottom: 1rem;
}
.trust-box h3 { color: #4ade80; font-size: 0.85rem; margin-bottom: 0.5rem; }
.trust-box ul { list-style: none; padding: 0; }
.trust-box li {
    font-size: 0.78rem; color: #6b8; padding: 0.25rem 0;
    display: flex; align-items: center; gap: 0.5rem;
}
.trust-box li::before { content: "\\2713"; color: #4ade80; font-weight: bold; }

.blocked-box {
    background: #1a0a0a; border: 1px solid #3a1a1a; border-radius: 8px;
    padding: 1rem; margin-bottom: 1rem;
}
.blocked-box h3 { color: #f87171; font-size: 0.85rem; margin-bottom: 0.5rem; }
.blocked-box p { font-size: 0.75rem; color: #a66; line-height: 1.5; }
.blocked-list { font-size: 0.72rem; color: #855; margin-top: 0.5rem; }

.btn-enter {
    display: block; width: 100%; padding: 0.9rem; background: #00d4ff; color: #080808;
    border: none; border-radius: 8px; font-family: inherit; font-weight: 800;
    font-size: 1.1rem; letter-spacing: 2px; cursor: pointer;
    transition: background 0.2s, transform 0.1s;
}
.btn-enter:hover { background: #00b8e6; }
.btn-enter:active { transform: scale(0.98); }
.btn-enter:disabled { background: #333; color: #666; cursor: not-allowed; }

.status-bar {
    text-align: center; margin-top: 0.8rem; font-size: 0.75rem; color: #555;
}
.status-bar .connected { color: #4ade80; }

.footer { text-align: center; margin-top: 2rem; font-size: 0.7rem; color: #444; }
.footer a { color: #00d4ff; text-decoration: none; }
</style>
</head>
<body>

<div class="setup-container">
    <div class="logo">THE FORGE</div>
    <div class="tagline">Autonomous Agent OS &mdash; Bring Your Own Keys</div>

    <div class="card">
        <h2>API KEYS</h2>
        <p>
            Your keys are sent directly from your browser to our server via encrypted HTTPS headers,
            used for a single request, then discarded. They are never stored, logged, cached, or
            transmitted anywhere else.
        </p>

        <div class="key-field">
            <label>
                xAI API Key
                <span class="provider-badge required">recommended</span>
            </label>
            <input type="password" id="key-xai" placeholder="xai-..." autocomplete="off" spellcheck="false">
            <div class="hint">Powers the 16-agent planner and default executor. <a href="https://console.x.ai" target="_blank" style="color:#00d4ff">Get one &rarr;</a></div>
        </div>

        <div class="key-field">
            <label>
                OpenAI API Key
                <span class="provider-badge">optional</span>
            </label>
            <input type="password" id="key-openai" placeholder="sk-..." autocomplete="off" spellcheck="false">
            <div class="hint">Enables GPT-4o, DALL-E 3 image gen, TTS, and Whisper.</div>
        </div>

        <div class="key-field">
            <label>
                Anthropic API Key
                <span class="provider-badge">optional</span>
            </label>
            <input type="password" id="key-anthropic" placeholder="sk-ant-..." autocomplete="off" spellcheck="false">
            <div class="hint">Enables Claude Opus 4, Sonnet 4, and Haiku 4.</div>
        </div>

        <div class="key-field">
            <label>
                GitHub Token
                <span class="provider-badge">optional</span>
            </label>
            <input type="password" id="key-github" placeholder="ghp_..." autocomplete="off" spellcheck="false">
            <div class="hint">Enables GitHub tools (issues, PRs, CI, code search).</div>
        </div>
    </div>

    <div class="trust-box">
        <h3>How We Handle Your Keys</h3>
        <ul>
            <li>Keys stored in your browser only (localStorage)</li>
            <li>Sent per-request via encrypted HTTPS headers</li>
            <li>Used for one API call, then discarded from memory</li>
            <li>Never written to disk, database, or log files</li>
            <li>Never sent to any third party (only to the provider you chose)</li>
            <li>Clear your keys anytime &mdash; they're in your browser</li>
            <li>Source code is public &mdash; verify yourself</li>
        </ul>
    </div>

    <div class="blocked-box">
        <h3>Disabled for Safety</h3>
        <p>
            Public mode disables shell commands, Python execution, file writes, and other tools
            that could affect the server. You get the full research, analysis, and generation
            capabilities &mdash; without the footgun.
        </p>
        <div class="blocked-list">
            Blocked: run_command, run_python, write_file, delete_file, append_file,
            execute_trade, surgeon_operate, http_post, git_commit
        </div>
    </div>

    <button class="btn-enter" id="btn-enter" onclick="enterForge()">ENTER THE FORGE</button>

    <div class="status-bar" id="status-bar">
        No keys configured yet &mdash; add at least one to get started
    </div>

    <div class="footer">
        <a href="https://github.com/rrhoopes3/Grok-Party-Pack" target="_blank">Source Code</a>
        &nbsp;&middot;&nbsp; Built with Grok 4.20 &nbsp;&middot;&nbsp; Your keys, your control
    </div>
</div>

<script>
const KEY_FIELDS = [
    { id: 'key-xai', header: 'X-Forge-XAI-Key', storage: 'forge_key_xai', provider: 'xAI' },
    { id: 'key-openai', header: 'X-Forge-OpenAI-Key', storage: 'forge_key_openai', provider: 'OpenAI' },
    { id: 'key-anthropic', header: 'X-Forge-Anthropic-Key', storage: 'forge_key_anthropic', provider: 'Anthropic' },
    { id: 'key-github', header: 'X-Forge-GitHub-Token', storage: 'forge_key_github', provider: 'GitHub' },
];

// Restore saved keys
KEY_FIELDS.forEach(f => {
    const el = document.getElementById(f.id);
    const saved = localStorage.getItem(f.storage) || '';
    el.value = saved;
    if (saved) el.classList.add('has-value');

    el.addEventListener('input', () => {
        const val = el.value.trim();
        if (val) {
            localStorage.setItem(f.storage, val);
            el.classList.add('has-value');
        } else {
            localStorage.removeItem(f.storage);
            el.classList.remove('has-value');
        }
        updateStatus();
    });
});

function updateStatus() {
    const configured = KEY_FIELDS.filter(f => localStorage.getItem(f.storage));
    const bar = document.getElementById('status-bar');
    const btn = document.getElementById('btn-enter');

    if (configured.length === 0) {
        bar.innerHTML = 'No keys configured yet &mdash; add at least one to get started';
        btn.disabled = true;
    } else {
        const names = configured.map(f => f.provider).join(', ');
        bar.innerHTML = '<span class="connected">' + configured.length + ' provider(s) configured: ' + names + '</span>';
        btn.disabled = false;
    }
}

function enterForge() {
    // Save keys and redirect to main app
    // The main app.js will read keys from localStorage and send them as headers
    window.location.href = '/';
}

updateStatus();
</script>

</body>
</html>"""
