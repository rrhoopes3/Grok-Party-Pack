"""
Config Migration System — versioned configuration with automatic upgrades.

Pattern borrowed from CLI agent config management: configurations are versioned
with a schema version number. When the app starts, it checks the stored version
against the current version and runs any pending migrations in order.

This prevents breakage when config keys are renamed, defaults change, or new
required fields are added between releases.

Usage:
    from forge.migrations import ConfigMigrator

    migrator = ConfigMigrator()

    @migrator.migration(from_version=1, to_version=2)
    def migrate_v1_to_v2(config: dict) -> dict:
        config["new_key"] = config.pop("old_key", "default")
        return config

    # At startup:
    config = migrator.run(loaded_config)
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("forge.migrations")

# Current schema version — bump this when adding new migrations
CURRENT_VERSION = 2

MigrationFn = Callable[[dict], dict]


class ConfigMigrator:
    """Manages versioned config migrations."""

    def __init__(self, backup_dir: Path | None = None):
        self._migrations: dict[tuple[int, int], MigrationFn] = {}
        self._backup_dir = backup_dir

    def migration(self, from_version: int, to_version: int) -> Callable[[MigrationFn], MigrationFn]:
        """Decorator to register a migration function."""
        def decorator(fn: MigrationFn) -> MigrationFn:
            self._migrations[(from_version, to_version)] = fn
            return fn
        return decorator

    def run(self, config: dict, config_path: Path | None = None) -> dict:
        """Run all pending migrations on a config dict.

        Args:
            config: The loaded configuration dictionary
            config_path: Optional path to the config file (for backup)

        Returns:
            The migrated configuration dictionary
        """
        version = config.get("_schema_version", 1)

        if version >= CURRENT_VERSION:
            return config

        if version > CURRENT_VERSION:
            log.warning(
                "Config version %d is newer than supported %d — skipping migrations",
                version, CURRENT_VERSION,
            )
            return config

        # Backup before migrating
        if config_path and config_path.exists():
            self._backup(config_path, version)

        # Run migrations in sequence
        while version < CURRENT_VERSION:
            next_version = version + 1
            key = (version, next_version)

            if key not in self._migrations:
                log.warning("No migration registered for v%d → v%d", version, next_version)
                break

            log.info("Running config migration: v%d → v%d", version, next_version)
            try:
                config = self._migrations[key](config)
                config["_schema_version"] = next_version
                version = next_version
            except Exception:
                log.exception("Migration v%d → v%d failed", version, next_version)
                break

        # Persist migrated config
        if config_path:
            try:
                config_path.write_text(json.dumps(config, indent=2, default=str))
                log.info("Saved migrated config to %s", config_path)
            except OSError:
                log.exception("Failed to save migrated config")

        return config

    def _backup(self, config_path: Path, version: int) -> None:
        """Create a timestamped backup of the config file before migration."""
        backup_dir = self._backup_dir or config_path.parent / "config_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{config_path.stem}_v{version}_{timestamp}{config_path.suffix}"
        try:
            shutil.copy2(config_path, backup_path)
            log.info("Config backup: %s", backup_path)
        except OSError:
            log.warning("Failed to backup config file")

    @property
    def registered_migrations(self) -> list[tuple[int, int]]:
        """List all registered migration paths."""
        return sorted(self._migrations.keys())


# ── Global migrator instance ─────────────────────────────────────────────

migrator = ConfigMigrator()


# ── Built-in migrations ──────────────────────────────────────────────────

@migrator.migration(from_version=1, to_version=2)
def _migrate_v1_to_v2(config: dict) -> dict:
    """v1 → v2: Normalize trading provider names, add permission defaults."""
    # Normalize provider names to lowercase
    if "trading_provider" in config:
        config["trading_provider"] = config["trading_provider"].lower()

    # Add permission defaults if missing
    if "permissions" not in config:
        config["permissions"] = {
            "headless_policy": "deny",
            "tool_overrides": {},
            "category_overrides": {},
        }

    # Rename deprecated keys
    if "max_iterations" in config:
        config["executor_max_iterations"] = config.pop("max_iterations")

    return config
