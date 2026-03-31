"""
Tests for the Config Migration System.

Covers: ConfigMigrator, migration registration, sequential execution,
        backup creation, version skipping, error handling.
"""
import json
import pytest
from pathlib import Path

from forge.migrations import ConfigMigrator, CURRENT_VERSION, migrator as global_migrator


class TestConfigMigrator:
    def test_no_migration_needed(self):
        m = ConfigMigrator()
        config = {"_schema_version": CURRENT_VERSION, "key": "value"}
        result = m.run(config)
        assert result == config

    def test_single_migration(self):
        m = ConfigMigrator()

        @m.migration(from_version=1, to_version=2)
        def migrate(config):
            config["new_key"] = "added"
            return config

        config = {"_schema_version": 1}
        result = m.run(config)
        assert result["new_key"] == "added"
        assert result["_schema_version"] == 2

    def test_chained_migrations(self, monkeypatch):
        import forge.migrations as mmod
        monkeypatch.setattr(mmod, "CURRENT_VERSION", 3)

        m = ConfigMigrator()

        @m.migration(from_version=1, to_version=2)
        def m1(config):
            config["step1"] = True
            return config

        @m.migration(from_version=2, to_version=3)
        def m2(config):
            config["step2"] = True
            return config

        config = {"_schema_version": 1}
        result = m.run(config)
        assert result["step1"] is True
        assert result["step2"] is True
        assert result["_schema_version"] == 3

    def test_default_version_is_1(self):
        m = ConfigMigrator()

        @m.migration(from_version=1, to_version=2)
        def migrate(config):
            config["migrated"] = True
            return config

        # No _schema_version key defaults to 1
        config = {"old_key": "value"}
        result = m.run(config)
        assert result["migrated"] is True

    def test_missing_migration_stops(self):
        m = ConfigMigrator()
        # Register v1→v2 but not v2→v3
        @m.migration(from_version=1, to_version=2)
        def migrate(config):
            config["step1"] = True
            return config

        config = {"_schema_version": 1}
        result = m.run(config)
        # Should stop at v2 since v2→v3 doesn't exist
        assert result["_schema_version"] == 2
        assert result["step1"] is True

    def test_migration_error_stops(self):
        m = ConfigMigrator()

        @m.migration(from_version=1, to_version=2)
        def bad_migrate(config):
            raise ValueError("migration failed")

        config = {"_schema_version": 1}
        result = m.run(config)
        # Should stay at v1 since migration failed
        assert result.get("_schema_version", 1) == 1

    def test_registered_migrations(self):
        m = ConfigMigrator()

        @m.migration(from_version=1, to_version=2)
        def m1(c): return c

        @m.migration(from_version=2, to_version=3)
        def m2(c): return c

        assert m.registered_migrations == [(1, 2), (2, 3)]


class TestBackup:
    def test_backup_created(self, tmp_path):
        config_path = tmp_path / "config.json"
        config = {"_schema_version": 1, "key": "value"}
        config_path.write_text(json.dumps(config))

        m = ConfigMigrator(backup_dir=tmp_path / "backups")

        @m.migration(from_version=1, to_version=2)
        def migrate(c):
            c["migrated"] = True
            return c

        m.run(config, config_path=config_path)

        backups = list((tmp_path / "backups").glob("config_v1_*"))
        assert len(backups) == 1


class TestPersistence:
    def test_migrated_config_saved(self, tmp_path):
        config_path = tmp_path / "config.json"
        config = {"_schema_version": 1, "data": "preserved"}
        config_path.write_text(json.dumps(config))

        m = ConfigMigrator()

        @m.migration(from_version=1, to_version=2)
        def migrate(c):
            c["added"] = True
            return c

        m.run(config, config_path=config_path)

        saved = json.loads(config_path.read_text())
        assert saved["_schema_version"] == 2
        assert saved["added"] is True
        assert saved["data"] == "preserved"


class TestGlobalMigrator:
    def test_v1_to_v2_normalizes_provider(self):
        config = {
            "_schema_version": 1,
            "trading_provider": "YFinance",
        }
        result = global_migrator.run(config.copy())
        assert result["trading_provider"] == "yfinance"

    def test_v1_to_v2_adds_permissions(self):
        config = {"_schema_version": 1}
        result = global_migrator.run(config.copy())
        assert "permissions" in result
        assert result["permissions"]["headless_policy"] == "deny"

    def test_v1_to_v2_renames_max_iterations(self):
        config = {"_schema_version": 1, "max_iterations": 20}
        result = global_migrator.run(config.copy())
        assert "max_iterations" not in result
        assert result["executor_max_iterations"] == 20
