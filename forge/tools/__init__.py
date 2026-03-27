from .registry import ToolRegistry
from . import (
    filesystem, shell, browser, http, python_repl,
    git_ops, search, clipboard, image, database, archive,
    email, escalation, prophecy, surgeon,
)


def create_registry() -> ToolRegistry:
    """Build a ToolRegistry with all available tools registered."""
    reg = ToolRegistry()
    filesystem.register(reg)
    shell.register(reg)
    browser.register(reg)
    http.register(reg)
    python_repl.register(reg)
    git_ops.register(reg)
    search.register(reg)
    clipboard.register(reg)
    image.register(reg)
    database.register(reg)
    archive.register(reg)
    email.register(reg)
    escalation.register(reg)
    # Prophecy Engine — swarm-intelligence prediction simulations
    prophecy.register(reg)
    # Surgeon — model surgery via OBLITERATUS
    surgeon.register(reg)
    # Generative UI — interactive widget rendering
    from forge.generative_ui import register_widget_tools
    register_widget_tools(reg)
    # Trading tools — PCR analysis, trade execution, portfolio
    from forge.config import TRADING_ENABLED
    if TRADING_ENABLED:
        from . import trading as trading_tools
        trading_tools.register(reg)
    # TRIBE v2 — neural engagement scoring via Meta's fMRI foundation model
    from forge.config import TRIBE_ENABLED
    if TRIBE_ENABLED:
        from . import tribe as tribe_tools
        tribe_tools.register(reg)
    return reg
