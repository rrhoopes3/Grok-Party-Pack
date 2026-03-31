"""
Tests for the Lazy Import Helpers.

Covers: lazy(), is_available(), _LazyModule proxy behavior.
"""
import pytest

from forge.lazy_imports import lazy, is_available, _LazyModule


class TestIsAvailable:
    def test_stdlib_available(self):
        assert is_available("json")
        assert is_available("os")
        assert is_available("pathlib")

    def test_nonexistent_unavailable(self):
        assert not is_available("nonexistent_package_xyz_999")

    def test_forge_available(self):
        assert is_available("forge.config")


class TestLazyModule:
    def test_deferred_until_access(self):
        mod = lazy("json")
        # Should not be resolved yet
        assert not object.__getattribute__(mod, "_resolved")
        # Access triggers import
        _ = mod.dumps
        assert object.__getattribute__(mod, "_resolved")

    def test_attribute_access(self):
        mod = lazy("json")
        result = mod.dumps({"a": 1})
        assert result == '{"a": 1}'

    def test_attr_extraction(self):
        path_cls = lazy("pathlib", attr="Path")
        p = path_cls("/tmp")
        assert str(p) == "/tmp"

    def test_callable_proxy(self):
        dumps = lazy("json", attr="dumps")
        result = dumps({"key": "value"})
        assert '"key"' in result

    def test_repr_deferred(self):
        mod = lazy("json")
        r = repr(mod)
        assert "deferred" in r
        assert "json" in r

    def test_repr_loaded(self):
        mod = lazy("json")
        _ = mod.dumps  # trigger load
        r = repr(mod)
        assert "loaded" in r

    def test_bool_available(self):
        mod = lazy("json")
        assert bool(mod) is True

    def test_bool_unavailable(self):
        mod = lazy("nonexistent_package_xyz_999")
        assert bool(mod) is False

    def test_import_error_raised(self):
        mod = lazy("nonexistent_package_xyz_999")
        with pytest.raises(ImportError):
            _ = mod.some_attr

    def test_multiple_accesses_single_import(self):
        """Ensure the module is only imported once."""
        mod = lazy("json")
        _ = mod.dumps
        _ = mod.loads
        _ = mod.JSONDecodeError
        # All should work, module resolved once
        assert object.__getattribute__(mod, "_resolved")


class TestPrebuiltProxies:
    def test_prebuilt_proxies_are_lazy(self):
        """Pre-built proxies should not be resolved at import time."""
        from forge.lazy_imports import chromadb, torch, robin_stocks
        # These are _LazyModule instances, not resolved
        assert isinstance(chromadb, _LazyModule)
        assert isinstance(torch, _LazyModule)
        assert isinstance(robin_stocks, _LazyModule)
        # None should be resolved yet
        assert not object.__getattribute__(chromadb, "_resolved")
        assert not object.__getattribute__(torch, "_resolved")
        assert not object.__getattribute__(robin_stocks, "_resolved")
