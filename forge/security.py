"""Shared LAN-hardening helpers for The Forge and satellite demo apps.

Bind defaults to loopback. Mutating APIs require a session or admin secret
unless Flask TESTING is on. Demo mode (loopback bind) will generate missing
secrets; any other mode fails closed if env secrets are absent.
"""
from __future__ import annotations

import functools
import hmac
import ipaddress
import logging
import os
import re
import secrets
import socket
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("forge.security")

_ADMIN_TOKEN_FILE = ".admin_token"
_SECRET_KEY_FILE = ".secret_key"
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

# Marketplace routes that already enforce their own API keys.
_OWN_AUTH_PREFIXES = (
    "/api/v1/agents",
    "/api/v1/wallet",
    "/api/v1/tasks",
    "/api/v1/toll",
)

_PUBLIC_PATHS = {
    "/",
    "/login",
    "/api/login",
    "/api/logout",
    "/favicon.ico",
    "/api/health",
}

_PUBLIC_PREFIXES = (
    "/static/",
)


def bind_host() -> str:
    """Return the Flask/uvicorn bind host.

    127.0.0.1 by default. All-interfaces only when FORGE_BIND=0.0.0.0
    (or ::) is set explicitly.
    """
    raw = (os.getenv("FORGE_BIND") or "127.0.0.1").strip()
    if raw in ("0.0.0.0", "::"):
        return raw
    return "127.0.0.1"


def is_demo_mode() -> bool:
    """Local gag/demo: loopback bind, not public-BYOK, not FORGE_DEMO=false."""
    flag = (os.getenv("FORGE_DEMO") or "").strip().lower()
    if flag in ("0", "false", "no"):
        return False
    if flag in ("1", "true", "yes"):
        return True
    try:
        from forge.config import PUBLIC_MODE
        if PUBLIC_MODE:
            return False
    except Exception:
        pass
    return bind_host() == "127.0.0.1"


def _under_pytest() -> bool:
    return "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


def _data_dir() -> Path:
    try:
        from forge.config import DATA_DIR
        return Path(DATA_DIR)
    except Exception:
        return Path(__file__).resolve().parent / "data"


def _persist_secret(filename: str, value: str) -> None:
    path = _data_dir() / filename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as e:
        log.warning("Could not persist %s: %s", filename, e)


def _read_secret(filename: str) -> str:
    path = _data_dir() / filename
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def require_env_secrets() -> None:
    """Fail closed outside demo if signing key + admin secret are missing."""
    if is_demo_mode() or _under_pytest():
        return
    if not (os.getenv("FORGE_SECRET_KEY") or "").strip():
        raise SystemExit(
            "FORGE_SECRET_KEY is required when not in demo mode "
            "(bind 127.0.0.1 or set FORGE_DEMO=true to generate one)"
        )
    if not (
        (os.getenv("FORGE_ADMIN_PASSWORD") or "").strip()
        or (os.getenv("FORGE_ADMIN_TOKEN") or "").strip()
        or (os.getenv("FORGE_MCP_API_KEY") or "").strip()
    ):
        raise SystemExit(
            "FORGE_ADMIN_PASSWORD (or FORGE_ADMIN_TOKEN / FORGE_MCP_API_KEY) "
            "is required when not in demo mode"
        )


def get_secret_key() -> str:
    """Flask signing key. Env, else demo-generated, else fail closed."""
    env = (os.getenv("FORGE_SECRET_KEY") or "").strip()
    if env:
        return env
    require_env_secrets()
    if _under_pytest():
        return "pytest-forge-secret-key"
    existing = _read_secret(_SECRET_KEY_FILE)
    if existing:
        return existing
    key = secrets.token_urlsafe(32)
    _persist_secret(_SECRET_KEY_FILE, key)
    return key


def get_admin_password() -> str:
    """Admin password for first-run user create. Env, else demo-generated."""
    env = (os.getenv("FORGE_ADMIN_PASSWORD") or "").strip()
    if env:
        return env
    require_env_secrets()
    return secrets.token_urlsafe(16)


def get_admin_token() -> str:
    """Shared bearer for satellite apps and MCP SSE. Never raises at request time."""
    env = (
        (os.getenv("FORGE_ADMIN_TOKEN") or "").strip()
        or (os.getenv("FORGE_MCP_API_KEY") or "").strip()
        or (os.getenv("FORGE_ADMIN_PASSWORD") or "").strip()
    )
    if env:
        return env
    if not is_demo_mode():
        return ""
    if _under_pytest():
        return "pytest-forge-admin-token"
    existing = _read_secret(_ADMIN_TOKEN_FILE)
    if existing:
        return existing
    token = secrets.token_urlsafe(24)
    _persist_secret(_ADMIN_TOKEN_FILE, token)
    log.warning("Demo admin token written to %s — send as Bearer or X-Forge-Admin", _ADMIN_TOKEN_FILE)
    return token


def new_id() -> str:
    """Full UUIDv4 hex — do not truncate."""
    return uuid.uuid4().hex


def safe_id(value: str, *, max_len: int = 80) -> str:
    """Reject ids that could be used as path segments for traversal."""
    if not isinstance(value, str) or not _ID_RE.fullmatch(value) or len(value) > max_len:
        raise ValueError("invalid id")
    return value


def resolve_in_root(root: Path, *parts: str) -> Path:
    """Resolve parts under root; raise ValueError on traversal."""
    base = Path(root).resolve()
    if not parts:
        raise ValueError("empty path")
    for part in parts:
        if not part or part in (".", "..") or "\x00" in part:
            raise ValueError("invalid path")
    candidate = base.joinpath(*parts).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as e:
        raise ValueError("path escapes allowed root") from e
    return candidate


def is_loopback_addr(addr: str | None) -> bool:
    if not addr:
        return False
    if addr in ("localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return True
    try:
        return ipaddress.ip_address(addr.split("%")[0]).is_loopback
    except ValueError:
        return False


def _flask_testing() -> bool:
    try:
        from flask import current_app, has_app_context
        if has_app_context() and current_app.config.get("TESTING"):
            return True
    except Exception:
        pass
    return False


def _is_public_path(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    if path == "/battlechess" or path.startswith("/battlechess/"):
        return True
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _has_own_auth(path: str) -> bool:
    return any(path.startswith(p) for p in _OWN_AUTH_PREFIXES)


def _header_token_ok() -> bool:
    from flask import request
    expected = get_admin_token()
    if not expected:
        return False
    auth_header = request.headers.get("Authorization", "")
    supplied = ""
    if auth_header.startswith("Bearer "):
        supplied = auth_header[7:].strip()
    if not supplied:
        supplied = (request.headers.get("X-Forge-Admin") or "").strip()
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def _session_ok() -> bool:
    try:
        from flask import g, session
        token = session.get("forge_token")
        if not token:
            return False
        auth = getattr(g, "forge_auth_manager", None)
        if auth is None:
            return bool(token)
        sess = auth.validate_session(token)
        if not sess:
            return False
        g.user = sess["user"]
        g.role = sess["role"]
        return True
    except Exception:
        return False


def require_request_auth():
    """before_request / decorator helper. None = allowed, else Flask (resp, code)."""
    from flask import jsonify, request

    path = request.path or "/"
    if request.method == "OPTIONS":
        return None
    if _is_public_path(path) or _has_own_auth(path):
        return None

    try:
        from forge.config import PUBLIC_MODE
        if PUBLIC_MODE:
            return None
    except Exception:
        pass

    if _flask_testing():
        return None

    if _session_ok() or _header_token_ok():
        return None

    try:
        from flask import current_app, has_app_context, g
        if has_app_context() and current_app.config.get("FORGE_ALLOW_LOOPBACK_DEMO"):
            if is_loopback_addr(request.remote_addr):
                g.user = g.user if hasattr(g, "user") else "local-demo"
                return None
    except Exception:
        pass

    if request.is_json or path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    from flask import redirect
    return redirect("/login")


def require_auth(view):
    """Flask route decorator — default-deny except public/testing/valid creds."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        denied = require_request_auth()
        if denied is not None:
            return denied
        return view(*args, **kwargs)
    return wrapped


def install_auth_gate(app, *, with_login_ui: bool = False, allow_loopback_demo: bool = False):
    """Attach secret key + before_request auth. Optional login UI for Forge."""
    require_env_secrets()
    app.secret_key = get_secret_key()
    app.config["FORGE_ALLOW_LOOPBACK_DEMO"] = bool(allow_loopback_demo)

    if with_login_ui:
        from forge.auth import AuthManager
        mgr = AuthManager()
        mgr.init_app(app)
        return mgr

    @app.before_request
    def _forge_auth_gate():
        return require_request_auth()

    return None


def private_urls_allowed() -> bool:
    flag = (os.getenv("FORGE_ALLOW_PRIVATE_URLS") or "").strip().lower()
    return flag in ("1", "true", "yes")


def _host_allowlisted(host: str) -> bool:
    raw = (os.getenv("FORGE_URL_ALLOWLIST") or "").strip()
    if not raw:
        return False
    allowed = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return host.lower() in allowed


_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",
    "metadata",
}


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_public_url(url: str) -> str:
    """Validate a URL for HTTP/browser tools. Returns the URL or raises ValueError.

    Blocks non-http(s), credentials-in-URL, metadata hosts, and resolved
    private/link-local/loopback addresses unless FORGE_ALLOW_PRIVATE_URLS=true
    or the host is in FORGE_URL_ALLOWLIST.
    """
    if not url or not isinstance(url, str):
        raise ValueError("url required")
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError("only http/https URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs with credentials are not allowed")
    host = (parsed.hostname or "").strip().rstrip(".")
    if not host:
        raise ValueError("url host required")
    if host.lower() in _METADATA_HOSTS:
        raise ValueError("metadata endpoints are blocked")

    if private_urls_allowed() or _host_allowlisted(host):
        return url

    try:
        addr = ipaddress.ip_address(host)
        if _ip_is_blocked(addr):
            raise ValueError("private/link-local/loopback targets are blocked")
        return url
    except ValueError as e:
        if "blocked" in str(e):
            raise

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"could not resolve host: {e}") from e
    for info in infos:
        sockaddr = info[4]
        ip_s = sockaddr[0]
        if ip_s.startswith("::ffff:"):
            ip_s = ip_s[7:]
        try:
            ip = ipaddress.ip_address(ip_s)
        except ValueError:
            continue
        if _ip_is_blocked(ip):
            raise ValueError("resolved to a private/link-local/loopback address")
    return url


class SafeRedirectHandler:
    """urllib redirect handler that re-validates each hop. Instantiated per opener."""

    @staticmethod
    def build():
        import urllib.request

        class _Handler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                check_public_url(newurl)
                return urllib.request.HTTPRedirectHandler.redirect_request(
                    self, req, fp, code, msg, headers, newurl
                )

        return _Handler()
