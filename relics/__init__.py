"""Shared Relic civilization host + bootstrap."""
from relics.bootstrap import (
    create_relic_app,
    load_json_safe,
    register_all_relics,
    run_relic,
)

__all__ = [
    "create_relic_app",
    "load_json_safe",
    "run_relic",
    "register_all_relics",
]
