"""
Tests for VRC-48M provenance tools — signing, verification, and guardrail.
"""
import json
import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

np = pytest.importorskip("numpy")
PIL = pytest.importorskip("PIL")
from PIL import Image

# Import the module directly to avoid xai_sdk dependency in registry.py
_mod_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "forge", "tools", "provenance.py")
_spec = importlib.util.spec_from_file_location("provenance", _mod_path)
_prov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prov)

SIDECAR_SUFFIX = _prov.SIDECAR_SUFFIX
_bandpass = _prov._bandpass
_canonical = _prov._canonical
_derive_seed = _prov._derive_seed
_detect = _prov._detect
_embed = _prov._embed
_get_key = _prov._get_key
_hmac_hex = _prov._hmac_hex
_make_watermark = _prov._make_watermark
provenance_inspect = _prov.provenance_inspect
provenance_sign = _prov.provenance_sign
provenance_verify = _prov.provenance_verify

from forge.guardrails import check_media_provenance, GuardrailEngine


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_test_image(path: str, w: int = 256, h: int = 256):
    """Create a synthetic test image with mid-frequency texture."""
    arr = np.random.RandomState(42).randint(0, 256, (h, w, 3), dtype=np.uint8)
    Image.fromarray(arr, "RGB").save(path)


# ── Unit tests: crypto helpers ──────────────────────────────────────────────

class TestCryptoHelpers:
    def test_canonical_deterministic(self):
        prov = {"generator_id": "grok", "timestamp": "2026-01-01T00:00:00Z", "metadata": {}}
        assert _canonical(prov) == _canonical(prov)

    def test_canonical_different_generators(self):
        a = {"generator_id": "grok", "timestamp": "T", "metadata": {}}
        b = {"generator_id": "dall-e", "timestamp": "T", "metadata": {}}
        assert _canonical(a) != _canonical(b)

    def test_derive_seed_deterministic(self):
        key = b"test"
        canon = '{"g":"x","m":{},"t":"T"}'
        assert _derive_seed(key, canon) == _derive_seed(key, canon)

    def test_derive_seed_different_keys(self):
        canon = '{"g":"x","m":{},"t":"T"}'
        assert _derive_seed(b"key1", canon) != _derive_seed(b"key2", canon)

    def test_hmac_hex_length(self):
        assert len(_hmac_hex(b"k", "data")) == 64


# ── Unit tests: watermark engine ────────────────────────────────────────────

class TestWatermarkEngine:
    def test_bandpass_preserves_shape(self):
        arr = np.random.randn(64, 64)
        out = _bandpass(arr)
        assert out.shape == (64, 64)

    def test_bandpass_removes_dc(self):
        arr = np.ones((64, 64)) * 100.0
        out = _bandpass(arr)
        assert abs(np.mean(out)) < 1.0

    def test_make_watermark_deterministic(self):
        a = _make_watermark(12345, (64, 64))
        b = _make_watermark(12345, (64, 64))
        assert np.allclose(a, b)

    def test_make_watermark_different_seeds(self):
        a = _make_watermark(111, (64, 64))
        b = _make_watermark(222, (64, 64))
        assert not np.allclose(a, b)

    def test_embed_preserves_range(self):
        lum = np.full((64, 64), 128.0)
        wm = _make_watermark(1, (64, 64))
        out = _embed(lum, wm)
        assert out.min() >= 0
        assert out.max() <= 255

    def test_detect_positive(self):
        lum = np.random.RandomState(7).rand(128, 128) * 200 + 28
        wm = _make_watermark(99, (128, 128))
        marked = _embed(lum, wm, alpha=5.0).astype(np.float64)
        conf, raw = _detect(marked, wm)
        assert conf > 0.3, f"Expected high confidence, got {conf} (raw={raw})"

    def test_detect_negative(self):
        lum = np.random.RandomState(7).rand(128, 128) * 200 + 28
        wm = _make_watermark(99, (128, 128))
        wrong_wm = _make_watermark(100, (128, 128))
        marked = _embed(lum, wm, alpha=5.0).astype(np.float64)
        conf, raw = _detect(marked, wrong_wm)
        assert conf < 0.3, f"Expected low confidence for wrong key, got {conf}"

    def test_detect_unsigned(self):
        lum = np.random.RandomState(7).rand(128, 128) * 200 + 28
        wm = _make_watermark(99, (128, 128))
        conf, _ = _detect(lum, wm)
        assert conf < 0.3, f"Expected low confidence for unsigned image, got {conf}"


# ── Integration tests: tool handlers ────────────────────────────────────────

class TestProvenanceSign:
    def test_sign_creates_output_and_sidecar(self, tmp_path):
        src = str(tmp_path / "src.png")
        dst = str(tmp_path / "signed.png")
        _make_test_image(src)

        result = json.loads(provenance_sign(src, dst, "test-gen"))
        assert result["status"] == "ok"
        assert os.path.exists(dst)
        assert os.path.exists(dst + SIDECAR_SUFFIX)

    def test_sign_sidecar_contents(self, tmp_path):
        src = str(tmp_path / "src.png")
        dst = str(tmp_path / "signed.png")
        _make_test_image(src)

        provenance_sign(src, dst, "my-model", '{"task": "demo"}')
        sidecar = json.loads(open(dst + SIDECAR_SUFFIX).read())
        assert sidecar["generator_id"] == "my-model"
        assert sidecar["metadata"] == {"task": "demo"}
        assert "signature" in sidecar
        assert sidecar["original_size"] == [256, 256]

    def test_sign_missing_input(self, tmp_path):
        result = json.loads(provenance_sign("/nonexistent.png", "/out.png", "x"))
        assert "error" in result

    def test_sign_bad_metadata(self, tmp_path):
        src = str(tmp_path / "src.png")
        _make_test_image(src)
        result = json.loads(provenance_sign(src, str(tmp_path / "o.png"), "x", "not json"))
        assert "error" in result


class TestProvenanceVerify:
    def test_verify_signed_image(self, tmp_path):
        src = str(tmp_path / "src.png")
        dst = str(tmp_path / "signed.png")
        _make_test_image(src)
        provenance_sign(src, dst, "forge-test")

        result = json.loads(provenance_verify(dst))
        assert result["verified"] is True
        assert result["confidence"] > 0.3
        assert result["generator_id"] == "forge-test"

    def test_verify_unsigned_image(self, tmp_path):
        src = str(tmp_path / "unsigned.png")
        _make_test_image(src)
        result = json.loads(provenance_verify(src))
        assert result["verified"] is False
        assert result["reason"] == "no_sidecar"

    def test_verify_tampered_sidecar(self, tmp_path):
        src = str(tmp_path / "src.png")
        dst = str(tmp_path / "signed.png")
        _make_test_image(src)
        provenance_sign(src, dst, "gen")

        sidecar_path = dst + SIDECAR_SUFFIX
        sidecar = json.loads(open(sidecar_path).read())
        sidecar["generator_id"] = "evil-impersonator"
        open(sidecar_path, "w").write(json.dumps(sidecar))

        result = json.loads(provenance_verify(dst))
        assert result["verified"] is False
        assert result["reason"] == "signature_mismatch"

    def test_verify_missing_file(self):
        result = json.loads(provenance_verify("/nonexistent.png"))
        assert "error" in result

    def test_verify_resized_image(self, tmp_path):
        src = str(tmp_path / "src.png")
        dst = str(tmp_path / "signed.png")
        resized = str(tmp_path / "small.png")
        _make_test_image(src, 256, 256)
        provenance_sign(src, dst, "gen")

        img = Image.open(dst)
        img.resize((128, 128), Image.LANCZOS).save(resized)
        # Copy sidecar so verify can find it
        import shutil
        shutil.copy(dst + SIDECAR_SUFFIX, resized + SIDECAR_SUFFIX)

        result = json.loads(provenance_verify(resized))
        assert result["confidence"] > 0.0


class TestProvenanceInspect:
    def test_inspect_signed(self, tmp_path):
        src = str(tmp_path / "src.png")
        dst = str(tmp_path / "signed.png")
        _make_test_image(src)
        provenance_sign(src, dst, "inspector-test")

        result = json.loads(provenance_inspect(dst))
        assert result["found"] is True
        assert result["generator_id"] == "inspector-test"

    def test_inspect_no_sidecar(self, tmp_path):
        src = str(tmp_path / "bare.png")
        _make_test_image(src)
        result = json.loads(provenance_inspect(src))
        assert result["found"] is False


# ── Guardrail tests ──────────────────────────────────────────────────────────

class TestMediaProvenanceGuardrail:
    def test_ignores_non_image_tools(self):
        r = check_media_provenance("read_file", {"path": "/tmp/x"})
        assert r.passed is True

    def test_warns_on_unsigned_media(self, tmp_path):
        src = str(tmp_path / "unsigned.png")
        _make_test_image(src)
        r = check_media_provenance("resize_image", {"input_path": src})
        assert r.passed is False
        assert r.severity == "warning"
        assert "VRC-48M" in r.message

    def test_passes_on_signed_media(self, tmp_path):
        src = str(tmp_path / "src.png")
        dst = str(tmp_path / "signed.png")
        _make_test_image(src)
        provenance_sign(src, dst, "g")

        r = check_media_provenance("resize_image", {"input_path": dst})
        assert r.passed is True

    def test_convert_image_also_checked(self, tmp_path):
        src = str(tmp_path / "bare.png")
        _make_test_image(src)
        r = check_media_provenance("convert_image", {"input_path": src})
        assert r.passed is False

    def test_guardrail_in_engine(self):
        engine = GuardrailEngine(enabled=True)
        violations = engine.check_input("resize_image", {"input_path": "/nonexistent/img.png"})
        provenance_violations = [v for v in violations if v.guardrail_name == "media_provenance"]
        assert len(provenance_violations) > 0
