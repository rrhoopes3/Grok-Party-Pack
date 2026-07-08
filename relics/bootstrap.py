"""Shared Flask bootstrap for Relic mini-apps.

Eliminates copy-paste Flask shells across relic-*/web_app.py.
Each relic keeps its own content, routes, and flavor; only the bootstrap is shared.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from flask import Flask


def create_relic_app(import_name: str) -> Flask:
    """Canonical factory — every relic starts here."""
    return Flask(import_name)


def load_json_safe(path: Path) -> list[dict]:
    """Load a JSON file as a list of dicts; never raises."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in (
                "history", "editions", "nights", "mail", "notices",
                "readings", "entries", "exhibits", "broadcasts",
            ):
                if key in data and isinstance(data[key], list):
                    return data[key]
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []


def run_relic(
    app: Flask,
    *,
    default_port: int,
    env_var: str,
    banner: list[str] | None = None,
    debug: bool = True,
) -> None:
    """Shared __main__ runner for standalone relic processes."""
    port = int(os.getenv(env_var, str(default_port)))
    if banner:
        for line in banner:
            print(line.replace("{port}", str(port)))
    else:
        print(f"Relic app on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)


def register_all_relics(host: Flask) -> dict[str, Flask]:
    """Attach every relic app under /relics/<slug>/ via Werkzeug dispatcher map.

    Returns the slug → Flask app mapping for test clients and the host index.
    Standalone python relic-*/web_app.py still works unchanged.
    """
    import importlib

    # slug -> (module path, default port)
    catalog = {
        "bestiary": "relic-bestiary.web_app",
        "bulletin-board": "relic-bulletin-board.web_app",
        "gazette": "relic-gazette.web_app",
        "museum": "relic-museum.web_app",
        "oracle": "relic-oracle.web_app",
        "post-office": "relic-post-office.web_app",
        "radio": "relic-radio.web_app",
        "tarot": "relic-tarot.web_app",
        "tavern": "relic-tavern.web_app",
    }
    mounted: dict[str, Flask] = {}
    for slug, mod_name in catalog.items():
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            # Package names with hyphens need importlib file load
            package = mod_name.split(".")[0]
            path = Path(package) / "web_app.py"
            if not path.exists():
                raise
            import importlib.util
            spec = importlib.util.spec_from_file_location(mod_name.replace("-", "_"), path)
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(mod)
        child: Flask = mod.app
        mounted[slug] = child

        # Mount routes onto host with a path prefix so one process serves all.
        # Child routes are copied under /relics/<slug> + original rule.
        for rule in list(child.url_map.iter_rules()):
            if rule.endpoint == "static":
                continue
            endpoint = f"relic_{slug}_{rule.endpoint}"
            view = child.view_functions[rule.endpoint]
            # Avoid double-registration on reload
            if endpoint in host.view_functions:
                continue
            host.add_url_rule(
                f"/relics/{slug}" + (rule.rule if rule.rule != "/" else "/"),
                endpoint=endpoint,
                view_func=view,
                methods=sorted(rule.methods - {"HEAD", "OPTIONS"}) or None,
            )
            if rule.rule == "/":
                host.add_url_rule(
                    f"/relics/{slug}",
                    endpoint=endpoint + "_noslash",
                    view_func=view,
                    methods=sorted(rule.methods - {"HEAD", "OPTIONS"}) or None,
                )
    return mounted
