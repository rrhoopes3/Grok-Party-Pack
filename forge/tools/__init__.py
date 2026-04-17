from .registry import ToolRegistry
from . import (
    filesystem, shell, browser, http, python_repl,
    git_ops, search, clipboard, image, database, archive,
    email, escalation, prophecy, surgeon, salesforce, blender, mcp,
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
    # Salesforce — personal productivity via sf CLI
    salesforce.register(reg)
    # Blender — 3D scene manipulation via blender-mcp (MCP client)
    blender.register(reg)
    # MCP namespaces — unified store/recall across forge:vault, forge:graph, external
    mcp.register(reg)
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
    # GitHub integration — PRs, issues, CI status
    from forge.config import GITHUB_ENABLED
    if GITHUB_ENABLED:
        from . import github as github_tools
        github_tools.register(reg)
    # Image / audio generation — DALL-E 3, TTS, Whisper
    from forge.config import IMAGE_GEN_ENABLED
    if IMAGE_GEN_ENABLED:
        from . import image_gen as image_gen_tools
        image_gen_tools.register(reg)
    # RAG pipeline — vector search over ingested documents
    from forge.config import RAG_ENABLED
    if RAG_ENABLED:
        from . import rag as rag_tools
        rag_tools.register(reg)
    # Fake audio detection — spectral heuristics + AASIST/SSL neural backends
    from . import fake_audio as fake_audio_tools
    fake_audio_tools.register(reg)
    # Deception detection — veracity pipeline (prosodic + cortical + swarm)
    from . import deception as deception_tools
    deception_tools.register(reg)
    return reg
