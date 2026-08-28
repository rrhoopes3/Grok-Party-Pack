"""Multi-mount host — one process serves every Relic under /relics/<slug>/."""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, jsonify

# Repo root for relic-* package paths
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from relics.bootstrap import register_all_relics
from forge.security import bind_host, install_auth_gate


def create_host_app() -> Flask:
    host = Flask("relic_host")
    install_auth_gate(host, allow_loopback_demo=True)
    mounted = register_all_relics(host)

    @host.route("/")
    def index():
        return jsonify({
            "service": "relic-host",
            "relics": {slug: f"/relics/{slug}/" for slug in sorted(mounted)},
        })

    host.relic_apps = mounted  # type: ignore[attr-defined]
    return host


def main() -> None:
    import os
    app = create_host_app()
    port = int(os.getenv("RELIC_HOST_PORT", "5020"))
    print(f"Relic multi-mount host on http://localhost:{port}")
    print("  index:", f"http://localhost:{port}/")
    for slug in sorted(getattr(app, "relic_apps", {})):
        print(f"  /relics/{slug}/")
    app.run(host=bind_host(), port=port, debug=False)


if __name__ == "__main__":
    main()
