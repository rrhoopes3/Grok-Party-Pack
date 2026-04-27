"""
API key management — reads/writes the per-provider secrets in .env.

The on-disk file is the source of truth (so Docker/Compose/systemd see the
same value on next boot). On every write we also patch `os.environ` and the
corresponding attribute on `forge.config` so running code picks the new key
up without a process restart.

Security posture:
  * Raw values never leave the server — only `{set, last4, length}` is
    serialized. Masking happens here, before the HTTP layer.
  * The .env file already lives behind the global `.env` gitignore entry.
  * `list_keys()` / `set_key()` / `clear_key()` tolerate a missing .env by
    creating it on first write. A read of a missing file returns empties
    (never an exception) so the UI stays usable on a clean checkout.

Provider registry is intentionally explicit — we don't want to surface every
random FORGE_* env var in the UI. Add a provider here when adding a new
integration that takes a user-supplied key.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("forge.secrets_vault")

# ────────────────────────────────────────────────────────────────────────
# Paths
# ────────────────────────────────────────────────────────────────────────

# Write to forge/.env (next to config.py). This is the file Flask / gunicorn
# / Docker Compose all source on boot, so changes survive restart.
_ENV_FILE = Path(__file__).resolve().parent / ".env"

# Single writer lock — .env IO is rare enough that coarse locking is fine.
_LOCK = threading.Lock()


# ────────────────────────────────────────────────────────────────────────
# Provider registry
# ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Provider:
    id: str                   # stable slug used in URLs (a-z0-9-)
    label: str                # human-readable name in UI
    env_var: str              # the actual OS env var
    config_attr: str          # attribute on forge.config (live-patched on write)
    category: str             # "llm" | "tools" | "trading" | "infra"
    hint: str                 # placeholder / example prefix
    docs_url: str = ""        # where to get the key


PROVIDERS: list[Provider] = [
    Provider("xai", "xAI (Grok)", "XAI_API_KEY", "XAI_API_KEY",
             "llm", "xai-…", "https://console.x.ai/"),
    Provider("anthropic", "Anthropic (Claude)", "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
             "llm", "sk-ant-…", "https://console.anthropic.com/"),
    Provider("openai", "OpenAI", "OPENAI_API_KEY", "OPENAI_API_KEY",
             "llm", "sk-…", "https://platform.openai.com/api-keys"),
    Provider("github", "GitHub", "GITHUB_TOKEN", "GITHUB_TOKEN",
             "tools", "ghp_… or github_pat_…", "https://github.com/settings/tokens"),
    Provider("serper", "Serper (Web search)", "SERPER_API_KEY", "",
             "tools", "", "https://serper.dev/api-key"),
    Provider("tradier", "Tradier (Trading)", "FORGE_TRADIER_API_KEY", "TRADING_TRADIER_API_KEY",
             "trading", "", "https://documentation.tradier.com/"),
    Provider("polymarket_relayer", "Polymarket Relayer", "POLYMARKET_RELAYER_API_KEY",
             "POLYMARKET_RELAYER_API_KEY", "trading", "", ""),
    Provider("polymarket_pk", "Polymarket Private Key", "POLYMARKET_PRIVATE_KEY",
             "POLYMARKET_PRIVATE_KEY", "trading", "0x…", ""),
    Provider("arcrelay", "ArcRelay (Email)", "FORGE_ARCRELAY_API_KEY", "ARCRELAY_API_KEY",
             "infra", "", ""),
]

_BY_ID: dict[str, Provider] = {p.id: p for p in PROVIDERS}


def get_provider(provider_id: str) -> Optional[Provider]:
    return _BY_ID.get(provider_id)


# ────────────────────────────────────────────────────────────────────────
# .env file parsing — preserves unknown keys and comments
# ────────────────────────────────────────────────────────────────────────

# KEY=VALUE with optional quoting. Group 1 = key, group 2 = value (unquoted
# inner if quoted, else raw).
_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')


def _unquote(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]
    # strip inline comment only if value is not quoted
    hash_pos = val.find(" #")
    if hash_pos != -1:
        val = val[:hash_pos].rstrip()
    return val


def _quote_if_needed(val: str) -> str:
    """Quote values containing whitespace, #, or = to survive re-parse."""
    if val == "":
        return ""
    if any(c in val for c in (" ", "#", "\t", '"', "\\")):
        return '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return val


def _read_env_file() -> tuple[list[str], "OrderedDict[str, str]"]:
    """
    Return (original_lines, key->value dict). Comments/blanks kept in lines
    list so we can re-emit the file without losing user formatting.
    """
    if not _ENV_FILE.exists():
        return [], OrderedDict()
    try:
        raw = _ENV_FILE.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("Failed reading %s: %s", _ENV_FILE, e)
        return [], OrderedDict()

    lines = raw.splitlines()
    kv: OrderedDict[str, str] = OrderedDict()
    for line in lines:
        m = _ASSIGN_RE.match(line)
        if m and not line.lstrip().startswith("#"):
            kv[m.group(1)] = _unquote(m.group(2))
    return lines, kv


def _write_env_file(new_kv: dict[str, str]) -> None:
    """
    Rewrite .env merging `new_kv` into the existing file. Updates in place
    where a key already appears; appends at the end for brand-new keys.
    Preserves comments and unknown lines.
    """
    lines, existing = _read_env_file()
    seen: set[str] = set()
    out: list[str] = []

    for line in lines:
        m = _ASSIGN_RE.match(line)
        if m and not line.lstrip().startswith("#"):
            key = m.group(1)
            if key in new_kv:
                out.append(f"{key}={_quote_if_needed(new_kv[key])}")
                seen.add(key)
                continue
        out.append(line)

    # Append any keys not already present
    appended_header = False
    for key, val in new_kv.items():
        if key in seen:
            continue
        if not appended_header:
            if out and out[-1].strip() != "":
                out.append("")
            out.append("# Added via Forge Keys tab")
            appended_header = True
        out.append(f"{key}={_quote_if_needed(val)}")

    # Ensure parent dir exists (fresh checkout case)
    _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


# ────────────────────────────────────────────────────────────────────────
# Masking
# ────────────────────────────────────────────────────────────────────────

def _mask(val: str) -> dict:
    """
    Serialize a secret for UI consumption. Never returns the raw value.
      { set: bool, last4: "ab12" | "", length: int, masked: "•••• •••• ab12" }
    """
    if not val:
        return {"set": False, "last4": "", "length": 0, "masked": ""}
    last4 = val[-4:] if len(val) >= 4 else val
    # Hide leading characters but give a visual hint of length
    length = len(val)
    dots = "•" * min(max(length - 4, 4), 20)
    masked = f"{dots} {last4}" if length > 4 else last4
    return {"set": True, "last4": last4, "length": length, "masked": masked}


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

def _current_value(env_var: str) -> str:
    """
    Read a key preferring the live process env (reflects most recent write),
    falling back to the .env file (covers values set outside the UI that
    haven't been imported into the process yet — e.g. a freshly edited .env
    before restart).
    """
    val = os.environ.get(env_var, "")
    if val:
        return val
    _, kv = _read_env_file()
    return kv.get(env_var, "")


def list_keys() -> list[dict]:
    """Return the full provider list with masked status, ordered for UI."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "env_var": p.env_var,
            "category": p.category,
            "hint": p.hint,
            "docs_url": p.docs_url,
            **_mask(_current_value(p.env_var)),
        }
        for p in PROVIDERS
    ]


def _patch_live_config(provider: Provider, value: str) -> None:
    """
    Push `value` into os.environ AND the forge.config module attribute so
    already-imported code sees the change. `value=""` means clear.
    """
    if value:
        os.environ[provider.env_var] = value
    else:
        os.environ.pop(provider.env_var, None)

    if not provider.config_attr:
        return
    try:
        from forge import config as forge_config
        setattr(forge_config, provider.config_attr, value)
    except Exception as e:
        log.warning("Could not hot-patch forge.config.%s: %s", provider.config_attr, e)


def set_key(provider_id: str, value: str) -> dict:
    """
    Persist and activate a new value. Empty string == clear.
    Returns the updated masked record for the UI.
    """
    p = get_provider(provider_id)
    if p is None:
        raise KeyError(f"Unknown provider: {provider_id!r}")
    value = (value or "").strip()
    with _LOCK:
        _write_env_file({p.env_var: value})
        _patch_live_config(p, value)
    action = "cleared" if not value else "set"
    log.info("%s %s (%s)", action, p.env_var, p.id)
    return {
        "id": p.id,
        "label": p.label,
        "env_var": p.env_var,
        "category": p.category,
        "hint": p.hint,
        "docs_url": p.docs_url,
        **_mask(value),
    }


def clear_key(provider_id: str) -> dict:
    return set_key(provider_id, "")
