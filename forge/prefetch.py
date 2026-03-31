"""
Startup Prefetch — parallel initialization of expensive subsystems.

Pattern borrowed from CLI agent startup optimization: fire provider health
checks, config validation, vault decryption, and optional dependency probes
concurrently at boot, rather than sequentially blocking the first request.

Usage:
    from forge.prefetch import run_prefetch

    # At app startup (before first request):
    results = run_prefetch()
    # results = {"providers": {...}, "optional_deps": {...}, ...}
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

log = logging.getLogger("forge.prefetch")


def _check_xai() -> dict:
    """Verify xAI SDK connectivity."""
    try:
        from forge.config import XAI_API_KEY
        if not XAI_API_KEY:
            return {"xai": {"status": "unconfigured", "ms": 0}}
        t0 = time.monotonic()
        from xai_sdk import Client
        client = Client()
        elapsed = (time.monotonic() - t0) * 1000
        return {"xai": {"status": "ok", "ms": round(elapsed, 1)}}
    except Exception as e:
        return {"xai": {"status": "error", "error": str(e), "ms": 0}}


def _check_anthropic() -> dict:
    """Verify Anthropic SDK availability."""
    try:
        from forge.config import ANTHROPIC_API_KEY
        if not ANTHROPIC_API_KEY:
            return {"anthropic": {"status": "unconfigured", "ms": 0}}
        t0 = time.monotonic()
        import anthropic
        anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        elapsed = (time.monotonic() - t0) * 1000
        return {"anthropic": {"status": "ok", "ms": round(elapsed, 1)}}
    except Exception as e:
        return {"anthropic": {"status": "error", "error": str(e), "ms": 0}}


def _check_openai() -> dict:
    """Verify OpenAI SDK availability."""
    try:
        from forge.config import OPENAI_API_KEY
        if not OPENAI_API_KEY:
            return {"openai": {"status": "unconfigured", "ms": 0}}
        t0 = time.monotonic()
        import openai
        openai.OpenAI(api_key=OPENAI_API_KEY)
        elapsed = (time.monotonic() - t0) * 1000
        return {"openai": {"status": "ok", "ms": round(elapsed, 1)}}
    except Exception as e:
        return {"openai": {"status": "error", "error": str(e), "ms": 0}}


def _probe_optional_deps() -> dict:
    """Check which optional heavy dependencies are available."""
    from forge.lazy_imports import is_available
    deps = {
        "chromadb": is_available("chromadb"),
        "sentence_transformers": is_available("sentence_transformers"),
        "playwright": is_available("playwright"),
        "solana": is_available("solana"),
        "torch": is_available("torch"),
        "robin_stocks": is_available("robin_stocks"),
    }
    return {"optional_deps": deps}


def _init_vault() -> dict:
    """Ensure vault directory and master key exist."""
    try:
        t0 = time.monotonic()
        from forge.config import VAULTS_DIR
        VAULTS_DIR.mkdir(exist_ok=True)
        elapsed = (time.monotonic() - t0) * 1000
        return {"vault": {"status": "ok", "ms": round(elapsed, 1)}}
    except Exception as e:
        return {"vault": {"status": "error", "error": str(e), "ms": 0}}


def _init_data_dirs() -> dict:
    """Ensure all data directories exist."""
    try:
        from forge.config import (
            DATA_DIR, CONVERSATIONS_DIR, RUNS_DIR, VAULTS_DIR,
            TRADING_DATA_DIR, PROPHECY_DATA_DIR, SURGEON_DATA_DIR,
            SURGEON_MODELS_DIR, TRIBE_CACHE_DIR, RAG_DATA_DIR,
        )
        dirs = [DATA_DIR, CONVERSATIONS_DIR, RUNS_DIR, VAULTS_DIR,
                TRADING_DATA_DIR, PROPHECY_DATA_DIR, SURGEON_DATA_DIR,
                SURGEON_MODELS_DIR, TRIBE_CACHE_DIR, RAG_DATA_DIR]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return {"data_dirs": {"status": "ok", "count": len(dirs)}}
    except Exception as e:
        return {"data_dirs": {"status": "error", "error": str(e)}}


def run_prefetch() -> dict[str, Any]:
    """Run all prefetch tasks concurrently. Returns a summary dict.

    Call this at startup before the first request. Non-blocking: each check
    runs in its own thread, total time = max(individual check times).
    """
    t0 = time.monotonic()
    results: dict[str, Any] = {}

    tasks = {
        "xai": _check_xai,
        "anthropic": _check_anthropic,
        "openai": _check_openai,
        "optional_deps": _probe_optional_deps,
        "vault": _init_vault,
        "data_dirs": _init_data_dirs,
    }

    with ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="prefetch") as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result(timeout=10)
                results.update(result)
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}
                log.warning("Prefetch '%s' failed: %s", name, e)

    elapsed = (time.monotonic() - t0) * 1000
    results["_prefetch_ms"] = round(elapsed, 1)
    log.info("Prefetch complete in %.0fms: %s", elapsed,
             {k: v.get("status", "ok") if isinstance(v, dict) else v
              for k, v in results.items() if k != "_prefetch_ms"})
    return results
