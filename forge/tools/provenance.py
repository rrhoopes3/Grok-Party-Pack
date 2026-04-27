"""
VRC-48M Provenance — media signing and verification.

Embeds invisible, compression-robust provenance fingerprints into images
using spread-spectrum frequency-domain watermarking.  Each signed file
carries a generator_id, timestamp, and optional metadata.  The embedded
pattern is derived from an HMAC of the provenance record so only holders
of the signing key can generate or verify the watermark.

Provenance metadata is stored in a sidecar file (<path>.vrc48m.json).
The watermark proves the sidecar wasn't fabricated after the fact.

Reference backend — swappable with the full VRC-48M engine.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import struct
import time
from pathlib import Path

log = logging.getLogger("forge.tools.provenance")

SIDECAR_SUFFIX = ".vrc48m.json"
_VERSION = "vrc48m-ref-1"
_DEFAULT_ALPHA = 3.0
_DETECTION_THRESHOLD = 0.02


# ── Key / HMAC helpers ─────────────────────────────────────────────────────

def _get_key() -> bytes:
    return os.getenv("FORGE_VRC48M_KEY", "vrc48m-dev-key-change-in-prod").encode()


def _canonical(prov: dict) -> str:
    return json.dumps(
        {"g": prov["generator_id"], "t": prov["timestamp"],
         "m": prov.get("metadata", {})},
        sort_keys=True, separators=(",", ":"),
    )


def _derive_seed(key: bytes, canon: str) -> int:
    h = hmac.new(key, canon.encode(), hashlib.sha256).digest()
    return struct.unpack(">I", h[:4])[0]


def _hmac_hex(key: bytes, canon: str) -> str:
    return hmac.new(key, canon.encode(), hashlib.sha256).hexdigest()


# ── Watermark engine (reference spread-spectrum) ──────────────────────────

def _bandpass(arr):
    """Zero out DC and high-freq, keep mid-band (10-45 % of Nyquist)."""
    import numpy as np
    H, W = arr.shape
    freq = np.fft.fftshift(np.fft.fft2(arr))
    cy, cx = H // 2, W // 2
    y = np.arange(H).reshape(-1, 1) - cy
    x = np.arange(W).reshape(1, -1) - cx
    dist = np.sqrt(y * y + x * x)
    max_r = max(min(cy, cx), 1)
    mask = ((dist > max_r * 0.1) & (dist < max_r * 0.45)).astype(np.float64)
    filtered = np.real(np.fft.ifft2(np.fft.ifftshift(freq * mask)))
    return filtered


def _make_watermark(seed: int, shape: tuple[int, int]):
    import numpy as np
    rng = np.random.RandomState(seed)
    w = rng.randn(*shape)
    w_bp = _bandpass(w)
    std = float(np.std(w_bp))
    if std > 1e-12:
        w_bp = w_bp / std
    return w_bp


def _embed(lum, wm, alpha: float = _DEFAULT_ALPHA):
    import numpy as np
    return np.clip(lum + alpha * wm, 0, 255).astype(np.uint8)


def _detect(lum, wm) -> tuple[float, float]:
    """Pearson correlation between luminance and expected watermark.

    Returns (confidence 0-1, raw_correlation).
    """
    import numpy as np
    lum = lum.astype(np.float64)
    lc = lum - np.mean(lum)
    wc = wm - np.mean(wm)
    den = float(np.sqrt(np.sum(lc ** 2) * np.sum(wc ** 2)))
    if den < 1e-12:
        return 0.0, 0.0
    corr = float(np.sum(lc * wc) / den)
    confidence = min(1.0, max(0.0, corr / (_DETECTION_THRESHOLD * 3)))
    return confidence, corr


# ── Tool implementations ──────────────────────────────────────────────────

def provenance_sign(
    input_path: str,
    output_path: str,
    generator_id: str,
    metadata_json: str = "{}",
) -> str:
    """Sign an image with VRC-48M provenance."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return json.dumps({"error": "Requires Pillow + numpy.  pip install Pillow numpy"})

    p = Path(input_path)
    if not p.exists():
        return json.dumps({"error": f"File not found: {input_path}"})

    try:
        meta = json.loads(metadata_json) if metadata_json else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid metadata JSON: {exc}"})

    try:
        img = Image.open(input_path).convert("RGB")
    except Exception as exc:
        return json.dumps({"error": f"Cannot open image: {exc}"})

    lum = np.array(img.convert("L"), dtype=np.float64)
    H, W = lum.shape

    prov = {
        "generator_id": generator_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metadata": meta,
    }
    canon = _canonical(prov)
    key = _get_key()
    seed = _derive_seed(key, canon)
    sig = _hmac_hex(key, canon)

    wm = _make_watermark(seed, (H, W))
    lum_marked = _embed(lum, wm)

    # Rebuild RGB: replace luminance, keep chrominance
    import numpy as _np
    rgb = _np.array(img, dtype=_np.float64)
    scale = _np.where(lum > 0, lum_marked.astype(_np.float64) / (lum + 1e-6), 1.0)
    for c in range(3):
        rgb[:, :, c] = _np.clip(rgb[:, :, c] * scale, 0, 255)
    out_img = Image.fromarray(rgb.astype(_np.uint8), "RGB")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(output_path)

    sidecar = {
        "version": _VERSION,
        "generator_id": prov["generator_id"],
        "timestamp": prov["timestamp"],
        "metadata": prov["metadata"],
        "original_size": [H, W],
        "signature": sig,
    }
    sidecar_path = output_path + SIDECAR_SUFFIX
    Path(sidecar_path).write_text(json.dumps(sidecar, indent=2))

    log.info("Signed %s -> %s (generator=%s)", input_path, output_path, generator_id)
    return json.dumps({
        "status": "ok",
        "input": input_path,
        "output": output_path,
        "sidecar": sidecar_path,
        "generator_id": generator_id,
        "timestamp": prov["timestamp"],
    })


def provenance_verify(input_path: str) -> str:
    """Verify VRC-48M provenance of an image."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return json.dumps({"error": "Requires Pillow + numpy.  pip install Pillow numpy"})

    p = Path(input_path)
    if not p.exists():
        return json.dumps({"error": f"File not found: {input_path}"})

    sidecar_path = input_path + SIDECAR_SUFFIX
    if not Path(sidecar_path).exists():
        return json.dumps({
            "verified": False,
            "reason": "no_sidecar",
            "message": f"No provenance sidecar found at {sidecar_path}",
        })

    try:
        sidecar = json.loads(Path(sidecar_path).read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return json.dumps({"verified": False, "reason": "bad_sidecar", "message": str(exc)})

    required = {"version", "generator_id", "timestamp", "original_size", "signature"}
    if not required.issubset(sidecar.keys()):
        return json.dumps({"verified": False, "reason": "incomplete_sidecar",
                           "message": f"Missing fields: {required - sidecar.keys()}"})

    # Rebuild canonical + verify HMAC
    prov = {
        "generator_id": sidecar["generator_id"],
        "timestamp": sidecar["timestamp"],
        "metadata": sidecar.get("metadata", {}),
    }
    canon = _canonical(prov)
    key = _get_key()
    expected_sig = _hmac_hex(key, canon)

    if not hmac.compare_digest(expected_sig, sidecar["signature"]):
        return json.dumps({
            "verified": False,
            "reason": "signature_mismatch",
            "message": "Sidecar HMAC does not match — provenance may have been tampered with",
        })

    # Watermark detection
    try:
        img = Image.open(input_path).convert("L")
    except Exception as exc:
        return json.dumps({"verified": False, "reason": "unreadable_image", "message": str(exc)})

    orig_H, orig_W = sidecar["original_size"]
    lum = np.array(img, dtype=np.float64)

    if lum.shape != (orig_H, orig_W):
        img_resized = img.resize((orig_W, orig_H), Image.LANCZOS)
        lum = np.array(img_resized, dtype=np.float64)

    seed = _derive_seed(key, canon)
    wm = _make_watermark(seed, (orig_H, orig_W))
    confidence, raw_corr = _detect(lum, wm)

    verified = confidence > 0.3
    return json.dumps({
        "verified": verified,
        "confidence": round(confidence, 4),
        "raw_correlation": round(raw_corr, 6),
        "generator_id": sidecar["generator_id"],
        "timestamp": sidecar["timestamp"],
        "metadata": sidecar.get("metadata", {}),
        "threshold": _DETECTION_THRESHOLD,
    })


def provenance_inspect(input_path: str) -> str:
    """Read VRC-48M sidecar metadata without watermark verification."""
    sidecar_path = input_path + SIDECAR_SUFFIX
    if not Path(sidecar_path).exists():
        return json.dumps({"found": False, "message": f"No sidecar at {sidecar_path}"})

    try:
        sidecar = json.loads(Path(sidecar_path).read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return json.dumps({"found": False, "message": str(exc)})

    return json.dumps({
        "found": True,
        "version": sidecar.get("version"),
        "generator_id": sidecar.get("generator_id"),
        "timestamp": sidecar.get("timestamp"),
        "metadata": sidecar.get("metadata", {}),
        "original_size": sidecar.get("original_size"),
        "note": "Sidecar read only — use provenance_verify to check watermark integrity",
    })


# ── Registration ───────────────────────────────────────────────────────────

def register(registry):
    registry.register(
        name="provenance_sign",
        description=(
            "Sign an image with VRC-48M provenance.  Embeds an invisible, "
            "compression-robust watermark and writes a sidecar (.vrc48m.json) "
            "with generator_id, timestamp, and metadata.  Requires Pillow + numpy."
        ),
        parameters={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "Path to source image"},
                "output_path": {"type": "string", "description": "Path to save signed image"},
                "generator_id": {
                    "type": "string",
                    "description": "Identifier for the generator/model (e.g. 'grok-4.20')",
                },
                "metadata_json": {
                    "type": "string",
                    "description": "Optional JSON object with extra provenance context",
                },
            },
            "required": ["input_path", "output_path", "generator_id"],
        },
        handler=provenance_sign,
    )
    registry.register(
        name="provenance_verify",
        description=(
            "Verify VRC-48M provenance of an image.  Checks sidecar HMAC and "
            "watermark correlation.  Returns verified/unverified with confidence "
            "score.  Requires Pillow + numpy."
        ),
        parameters={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "Path to image to verify"},
            },
            "required": ["input_path"],
        },
        handler=provenance_verify,
    )
    registry.register(
        name="provenance_inspect",
        description=(
            "Read VRC-48M sidecar metadata for an image without performing "
            "watermark verification.  Fast — no image processing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "Path to image to inspect"},
            },
            "required": ["input_path"],
        },
        handler=provenance_inspect,
    )
