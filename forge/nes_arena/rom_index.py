"""
ROM library indexer — scans the ROM root and catalogs `.nes` files.

The root is configured by FORGE_NES_ROMS_DIR (defaults to B:/Grok/forge/nes).
ROMs are exposed to the browser by slug so we never leak absolute paths in
the URL space.

We only serve `.nes` files (no SNES/`.smc` here — those are iNES-incompatible
and jsnes can't play them). If you want SNES support later, that's a swap to
a different in-browser emulator like Snes9x JS.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from forge.config import NES_ROMS_DIR

log = logging.getLogger("forge.nes_arena.rom_index")

# iNES magic: "NES\x1a" — 4 bytes at the start of a valid cartridge dump.
_INES_MAGIC = b"NES\x1a"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(path: Path) -> str:
    """Stable URL-safe id derived from the filename stem."""
    stem = path.stem.lower()
    slug = _SLUG_RE.sub("-", stem).strip("-")
    return slug or "unnamed"


def _clean_title(name: str) -> str:
    """
    Strip common ROM suffixes like (U), (Europe), [!], revision tags. We
    keep the core title readable for the UI picker.
    """
    name = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", name)  # drop (...) and [...]
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _is_ines_rom(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) == _INES_MAGIC
    except Exception:
        return False


def list_roms() -> list[dict]:
    """
    Walk the ROM dir and return entries the UI can render.
    Each entry:
      { slug, title, filename, size_bytes, path_rel }
    """
    root = Path(NES_ROMS_DIR)
    if not root.exists():
        log.warning("NES_ROMS_DIR does not exist: %s", root)
        return []

    out: list[dict] = []
    seen_slugs: set[str] = set()

    # `rglob("*.nes")` walks subfolders too — user's dump has "Roms/",
    # "NES ALL ROMS/", "Zelda 2/", etc.
    for path in sorted(root.rglob("*.nes")):
        if not path.is_file():
            continue
        # Filter out tiny/broken files and non-iNES dumps early
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < 16 * 1024:  # smaller than iNES header + minimum PRG/CHR
            continue
        if not _is_ines_rom(path):
            continue

        slug = _slugify(path)
        # dedupe slugs (some ROM folders have duplicate titles)
        suffix = 1
        base = slug
        while slug in seen_slugs:
            suffix += 1
            slug = f"{base}-{suffix}"
        seen_slugs.add(slug)

        out.append({
            "slug": slug,
            "title": _clean_title(path.stem),
            "filename": path.name,
            "size_bytes": size,
            "path_rel": str(path.relative_to(root)).replace("\\", "/"),
        })
    out.sort(key=lambda e: e["title"].lower())
    log.info("ROM index built: %d titles under %s", len(out), root)
    return out


# Scanning 1000+ files on every `list_roms` call is wasteful. Cache after
# the first call; `refresh=True` forces a rescan (useful if a user drops
# new ROMs into the folder without restarting Forge).
_INDEX_CACHE: Optional[list[dict]] = None


def _scan_roms() -> list[dict]:
    """Internal uncached scanner. Callers use list_roms() or rom_by_slug()."""
    return _list_roms_impl()


# Shadow the public name so `list_roms` becomes the cached entry point
# without breaking imports elsewhere.
_list_roms_impl = list_roms


def list_roms(refresh: bool = False) -> list[dict]:
    """Return the ROM index, cached after the first scan.

    Pass refresh=True to force a rescan (cheap-ish — stat()s every .nes file).
    """
    global _INDEX_CACHE
    if refresh or _INDEX_CACHE is None:
        _INDEX_CACHE = _list_roms_impl()
    return _INDEX_CACHE


def rom_by_slug(slug: str) -> Optional[dict]:
    for rom in list_roms():
        if rom["slug"] == slug:
            return rom
    return None


def get_rom_bytes(slug: str) -> Optional[bytes]:
    """Return the raw cartridge bytes for a slug, or None if not found."""
    rom = rom_by_slug(slug)
    if rom is None:
        return None
    path = Path(NES_ROMS_DIR) / rom["path_rel"]
    try:
        return path.read_bytes()
    except OSError as e:
        log.error("Failed reading ROM %s: %s", path, e)
        return None
