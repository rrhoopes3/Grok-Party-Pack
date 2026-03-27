"""
Web UI Authentication — session-based auth for the Forge web interface.

Simple username/password authentication with session cookies.
Passwords are hashed with bcrypt (falls back to hashlib if bcrypt unavailable).
Users are stored in a JSON file.

Usage:
    from forge.auth import AuthManager, require_login

    auth = AuthManager()
    auth.create_user("admin", "changeme")

    # In Flask app:
    auth.init_app(app)

    @app.route("/protected")
    @require_login
    def protected():
        return "secret"

First-run creates a default admin user with password from FORGE_ADMIN_PASSWORD
env var (or "changeme" if not set — change it!).
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger("forge.auth")


def _hash_password(password: str, salt: str = "") -> tuple[str, str]:
    """Hash a password. Returns (hash, salt)."""
    try:
        import bcrypt
        if not salt:
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
            return hashed.decode(), ""
        return bcrypt.hashpw(password.encode(), salt.encode()).decode(), salt
    except ImportError:
        # Fallback to sha256 + salt
        if not salt:
            salt = secrets.token_hex(16)
        hashed = hashlib.sha256((salt + password).encode()).hexdigest()
        return hashed, salt


def _verify_password(password: str, stored_hash: str, salt: str = "") -> bool:
    """Verify a password against a stored hash."""
    try:
        import bcrypt
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    except ImportError:
        hashed = hashlib.sha256((salt + password).encode()).hexdigest()
        return hashed == stored_hash


class AuthManager:
    """Manages user authentication and sessions."""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            from forge.config import DATA_DIR
            data_dir = DATA_DIR
        self._users_file = data_dir / "auth_users.json"
        self._sessions: dict[str, dict] = {}  # token → {user, created_at, expires_at}
        self._users: dict[str, dict] = {}  # username → {hash, salt, role, created_at}
        self._session_ttl = 86400 * 7  # 7 days
        self._load()

    def _load(self) -> None:
        """Load users from disk."""
        if self._users_file.exists():
            try:
                with open(self._users_file, "r") as f:
                    self._users = json.load(f)
            except Exception as e:
                log.warning("Failed to load auth users: %s", e)

    def _save(self) -> None:
        """Persist users to disk."""
        with open(self._users_file, "w") as f:
            json.dump(self._users, f, indent=2)

    def create_user(self, username: str, password: str, role: str = "admin") -> bool:
        """Create a new user. Returns False if user already exists."""
        if username in self._users:
            return False
        hashed, salt = _hash_password(password)
        self._users[username] = {
            "hash": hashed,
            "salt": salt,
            "role": role,
            "created_at": time.time(),
        }
        self._save()
        log.info("Created user: %s (role=%s)", username, role)
        return True

    def change_password(self, username: str, new_password: str) -> bool:
        """Change a user's password."""
        if username not in self._users:
            return False
        hashed, salt = _hash_password(new_password)
        self._users[username]["hash"] = hashed
        self._users[username]["salt"] = salt
        self._save()
        return True

    def delete_user(self, username: str) -> bool:
        """Delete a user."""
        if username in self._users:
            del self._users[username]
            self._save()
            return True
        return False

    def authenticate(self, username: str, password: str) -> str | None:
        """Authenticate a user. Returns session token or None."""
        user = self._users.get(username)
        if not user:
            return None
        if not _verify_password(password, user["hash"], user.get("salt", "")):
            return None

        # Create session
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {
            "user": username,
            "role": user["role"],
            "created_at": time.time(),
            "expires_at": time.time() + self._session_ttl,
        }
        return token

    def validate_session(self, token: str) -> dict | None:
        """Validate a session token. Returns session info or None."""
        session = self._sessions.get(token)
        if not session:
            return None
        if time.time() > session["expires_at"]:
            del self._sessions[token]
            return None
        return session

    def logout(self, token: str) -> None:
        """Invalidate a session."""
        self._sessions.pop(token, None)

    def ensure_default_user(self) -> None:
        """Create default admin user if no users exist."""
        if not self._users:
            password = os.getenv("FORGE_ADMIN_PASSWORD", "changeme")
            self.create_user("admin", password, role="admin")
            if password == "changeme":
                log.warning(
                    "Default admin created with password 'changeme' — "
                    "set FORGE_ADMIN_PASSWORD to change it!"
                )

    def init_app(self, app) -> None:
        """Initialize Flask app with auth routes and middleware."""
        from flask import request, jsonify, redirect, session, g

        app.secret_key = os.getenv(
            "FORGE_SECRET_KEY",
            secrets.token_urlsafe(32),
        )

        self.ensure_default_user()

        # Public paths that don't require auth
        public_paths = {
            "/api/v1/agents/register",  # marketplace registration
            "/login",
            "/api/login",
            "/api/logout",
            "/favicon.ico",
        }
        # Public prefixes
        public_prefixes = (
            "/static/",
            "/api/v1/",  # marketplace API uses its own auth
        )

        @app.before_request
        def check_auth():
            path = request.path

            # Skip auth for public paths
            if path in public_paths:
                return
            for prefix in public_prefixes:
                if path.startswith(prefix):
                    return

            # Check for auth if enabled
            from forge.config import AUTH_ENABLED
            if not AUTH_ENABLED:
                return

            # Check session cookie
            token = session.get("forge_token")
            if token:
                sess = self.validate_session(token)
                if sess:
                    g.user = sess["user"]
                    g.role = sess["role"]
                    return

            # Check Authorization header (for API clients)
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                sess = self.validate_session(token)
                if sess:
                    g.user = sess["user"]
                    g.role = sess["role"]
                    return

            # Not authenticated
            if request.is_json or path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect("/login")

        @app.route("/login", methods=["GET"])
        def login_page():
            return """<!DOCTYPE html>
<html><head><title>Forge Login</title>
<style>
body { background: #0a0a0a; color: #e0e0e0; font-family: monospace;
       display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
.login-box { background: #1a1a1a; padding: 2rem; border-radius: 8px; border: 1px solid #333; width: 320px; }
h1 { color: #00d4ff; text-align: center; margin-bottom: 1.5rem; }
input { width: 100%; padding: 0.6rem; margin: 0.3rem 0 1rem; background: #0a0a0a;
        border: 1px solid #333; color: #e0e0e0; font-family: monospace; box-sizing: border-box; }
button { width: 100%; padding: 0.7rem; background: #00d4ff; color: #0a0a0a;
         border: none; cursor: pointer; font-family: monospace; font-weight: bold; font-size: 1rem; }
button:hover { background: #00b8e6; }
.error { color: #ff4444; text-align: center; margin-top: 0.5rem; }
</style></head><body>
<div class="login-box">
<h1>THE FORGE</h1>
<form id="loginForm">
<input type="text" name="username" placeholder="Username" required autofocus>
<input type="password" name="password" placeholder="Password" required>
<button type="submit">ENTER</button>
<div class="error" id="error"></div>
</form>
<script>
document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await fetch('/api/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: fd.get('username'), password: fd.get('password')})
    });
    if (r.ok) { window.location.href = '/'; }
    else { document.getElementById('error').textContent = 'Invalid credentials'; }
});
</script>
</div></body></html>"""

        @app.route("/api/login", methods=["POST"])
        def api_login():
            data = request.get_json()
            username = data.get("username", "")
            password = data.get("password", "")
            token = self.authenticate(username, password)
            if token:
                session["forge_token"] = token
                return jsonify({"status": "ok", "user": username})
            return jsonify({"error": "Invalid credentials"}), 401

        @app.route("/api/logout", methods=["POST"])
        def api_logout():
            token = session.pop("forge_token", None)
            if token:
                self.logout(token)
            return jsonify({"status": "ok"})


# ── Decorator ────────────────────────────────────────────────────────────

def require_login(f):
    """Flask route decorator that requires authentication."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        from flask import g
        if not hasattr(g, "user") or not g.user:
            from flask import jsonify
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated
