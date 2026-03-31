"""
Lazy Import Helpers — defer heavy optional dependencies until first use.

Pattern borrowed from CLI agent startup optimization: instead of importing
heavyweight packages (chromadb, sentence-transformers, playwright, solana,
torch) at module load time, wrap them in lazy accessors that only import
on first access. This cuts cold-start time significantly for sessions that
don't use every subsystem.

Usage:
    from forge.lazy_imports import lazy

    # Instead of: import chromadb
    chromadb = lazy("chromadb")

    # Instead of: from playwright.sync_api import sync_playwright
    sync_playwright = lazy("playwright.sync_api", attr="sync_playwright")

    # Later, when actually needed:
    client = chromadb.Client()   # import happens here, first time only
"""
from __future__ import annotations

import importlib
import logging
from types import ModuleType
from typing import Any

log = logging.getLogger("forge.lazy_imports")


class _LazyModule:
    """Proxy that defers import until first attribute access."""

    __slots__ = ("_module_path", "_attr", "_resolved", "_module")

    def __init__(self, module_path: str, attr: str | None = None):
        object.__setattr__(self, "_module_path", module_path)
        object.__setattr__(self, "_attr", attr)
        object.__setattr__(self, "_resolved", False)
        object.__setattr__(self, "_module", None)

    def _resolve(self) -> Any:
        if not object.__getattribute__(self, "_resolved"):
            module_path = object.__getattribute__(self, "_module_path")
            attr = object.__getattribute__(self, "_attr")
            try:
                mod = importlib.import_module(module_path)
                if attr:
                    mod = getattr(mod, attr)
                object.__setattr__(self, "_module", mod)
                object.__setattr__(self, "_resolved", True)
                log.debug("Lazy-loaded: %s%s", module_path, f".{attr}" if attr else "")
            except ImportError as e:
                log.warning("Optional dependency not available: %s (%s)", module_path, e)
                raise
        return object.__getattribute__(self, "_module")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __repr__(self) -> str:
        resolved = object.__getattribute__(self, "_resolved")
        module_path = object.__getattribute__(self, "_module_path")
        attr = object.__getattribute__(self, "_attr")
        target = f"{module_path}.{attr}" if attr else module_path
        state = "loaded" if resolved else "deferred"
        return f"<LazyModule({target}) [{state}]>"

    def __bool__(self) -> bool:
        """Check if the module is available without importing it."""
        try:
            self._resolve()
            return True
        except ImportError:
            return False


def lazy(module_path: str, attr: str | None = None) -> Any:
    """Create a lazy import proxy.

    Args:
        module_path: Dotted module path (e.g., "chromadb" or "playwright.sync_api")
        attr: Optional attribute to extract from the module after import

    Returns:
        A proxy object that imports the module on first attribute access.
    """
    return _LazyModule(module_path, attr)


def is_available(module_path: str) -> bool:
    """Check if a module is importable without actually importing it.

    Uses importlib.util.find_spec which is cheap — no module code is executed.
    """
    try:
        spec = importlib.util.find_spec(module_path)
        return spec is not None
    except (ModuleNotFoundError, ValueError):
        return False


# ── Pre-built lazy proxies for common heavy deps ────────────────────────

# RAG pipeline
chromadb = lazy("chromadb")
sentence_transformers = lazy("sentence_transformers")

# Browser automation
playwright_sync = lazy("playwright.sync_api", attr="sync_playwright")

# Solana / crypto
solana_client = lazy("solana.rpc.api", attr="Client")
solders_pubkey = lazy("solders.pubkey", attr="Pubkey")

# ML / torch (for TRIBE, fake audio, etc.)
torch = lazy("torch")
torchaudio = lazy("torchaudio")
transformers = lazy("transformers")

# Robinhood
robin_stocks = lazy("robin_stocks")
