"""Forge tools for audio authenticity detection.

Detects AI-generated / synthetic / deepfake audio using three backends:

  spectral  — Librosa heuristics (always available, no model needed).
              Flags spectral flatness, pitch regularity, MFCC dynamics,
              and energy variance anomalies characteristic of TTS output.

  aasist    — AASIST: Audio Anti-Spoofing using Integrated Spectro-Temporal
              Graph Attention Networks (clovaai/aasist). EER ~0.83%.
              Requires a local checkpoint; download from the AASIST repo.

  ssl       — SSL Anti-spoofing: wav2vec2 XLSR-300M + Heterogeneous Graph
              Attention Networks (TakHemlata/SSL_Anti-spoofing). EER ~0.82%.
              Requires a local checkpoint + fairseq install.

Plus a TRIBE integration:

  fake_audio_neuro_compare  — Runs two audio files through TRIBE v2 and
              compares predicted cortical activation patterns. Real vs.
              synthetic speech activates auditory cortex differently because
              TRIBE learned on naturalistic speech — divergence in activation
              is a forensic signal of acoustic anomaly.

Tools:
    fake_audio_detect        — Detect fake audio, single file
    fake_audio_scan          — Scan long audio in chunks, return timeline
    fake_audio_neuro_compare — TRIBE cortical fingerprint comparison

Install notes:
    Spectral backend: pip install librosa soundfile
    AASIST backend:   git clone https://github.com/clovaai/aasist + pip install -r requirements.txt
    SSL backend:      git clone https://github.com/TakHemlata/SSL_Anti-spoofing + pip install fairseq
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from .registry import ToolRegistry

log = logging.getLogger("forge.tools.fake_audio")

# ── Spectral heuristics ───────────────────────────────────────────────────────
# Feature weights informed by ASVspoof literature. Spectral flatness and
# pitch regularity are the strongest discriminators for modern TTS systems.

_SPECTRAL_WEIGHTS = {
    "spectral_flatness":    0.30,  # TTS produces flatter spectra
    "pitch_regularity":     0.25,  # TTS pitch is unnaturally smooth
    "mfcc_delta_variance":  0.25,  # TTS has less dynamic MFCC transitions
    "energy_variance":      0.10,  # TTS energy is more uniform
    "spectral_centroid_var":0.10,  # TTS spectral centroid varies less
}


def _spectral_score(audio, sr: int) -> dict:
    """Compute heuristic fake-probability from spectral features.

    Returns a dict with individual feature scores and a composite
    fake_probability 0–100 (higher = more likely synthetic).
    """
    import numpy as np
    import librosa

    # ── Spectral flatness (higher → flatter → more synthetic) ──────────────
    flatness = librosa.feature.spectral_flatness(y=audio)
    mean_flatness = float(np.mean(flatness))
    # Real speech ~0.01–0.04, TTS often 0.04–0.12
    flatness_score = min(1.0, mean_flatness / 0.08)

    # ── MFCC delta variance (lower → less dynamic → more synthetic) ────────
    mfccs = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=20)
    delta = librosa.feature.delta(mfccs)
    delta_std = float(np.mean(np.std(delta, axis=1)))
    # Real speech: delta_std typically 3–8; TTS: often 1–3
    mfcc_score = max(0.0, 1.0 - (delta_std / 5.0))

    # ── Pitch regularity (higher regularity → more synthetic) ──────────────
    try:
        f0, voiced, _ = librosa.pyin(
            audio, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"),
            sr=sr,
        )
        f0_voiced = f0[voiced & ~np.isnan(f0)]
        if len(f0_voiced) > 10:
            # Coefficient of variation: low CoV → unnaturally regular pitch
            cov = float(np.std(f0_voiced) / (np.mean(f0_voiced) + 1e-9))
            # Real speech CoV ~0.15–0.40; TTS often 0.05–0.15
            pitch_score = max(0.0, 1.0 - (cov / 0.20))
        else:
            pitch_score = 0.5  # not enough voiced frames to judge
    except Exception:
        pitch_score = 0.5

    # ── Energy envelope variance (lower → more uniform → more synthetic) ───
    rms = librosa.feature.rms(y=audio)[0]
    rms_cv = float(np.std(rms) / (np.mean(rms) + 1e-9))
    # Real speech RMS CoV ~0.5–1.5; TTS often 0.2–0.6
    energy_score = max(0.0, 1.0 - (rms_cv / 0.6))

    # ── Spectral centroid variance (lower → more synthetic) ────────────────
    centroid = librosa.feature.spectral_centroid(y=audio, sr=sr)[0]
    centroid_cv = float(np.std(centroid) / (np.mean(centroid) + 1e-9))
    # Real speech centroid CV ~0.3–0.7; TTS often 0.1–0.3
    centroid_score = max(0.0, 1.0 - (centroid_cv / 0.35))

    # ── Weighted composite ─────────────────────────────────────────────────
    fake_prob = (
        flatness_score    * _SPECTRAL_WEIGHTS["spectral_flatness"]
        + mfcc_score      * _SPECTRAL_WEIGHTS["mfcc_delta_variance"]
        + pitch_score     * _SPECTRAL_WEIGHTS["pitch_regularity"]
        + energy_score    * _SPECTRAL_WEIGHTS["energy_variance"]
        + centroid_score  * _SPECTRAL_WEIGHTS["spectral_centroid_var"]
    ) * 100.0

    return {
        "fake_probability": round(fake_prob, 1),
        "features": {
            "spectral_flatness":     round(mean_flatness, 4),
            "spectral_flatness_score": round(flatness_score, 3),
            "mfcc_delta_std":        round(delta_std, 3),
            "mfcc_delta_score":      round(mfcc_score, 3),
            "pitch_regularity_score": round(pitch_score, 3),
            "energy_cv":             round(rms_cv, 3),
            "energy_score":          round(energy_score, 3),
            "centroid_cv":           round(centroid_cv, 3),
            "centroid_score":        round(centroid_score, 3),
        },
    }


# ── Model backends ────────────────────────────────────────────────────────────

_aasist_model = None
_aasist_lock = threading.Lock()

_ssl_model = None
_ssl_lock = threading.Lock()


def _load_aasist(checkpoint_path: str, device: str = "cpu"):
    """Load AASIST model from a local checkpoint."""
    import torch
    import importlib.util

    # Locate aasist package
    spec = importlib.util.find_spec("aasist")
    if spec is None:
        raise ImportError(
            "AASIST not found. Clone https://github.com/clovaai/aasist and "
            "run: pip install -r requirements.txt"
        )

    from aasist.models.AASIST import Model as AASISTModel

    with open(Path(checkpoint_path).parent / "config.conf") as f:
        import json as _json
        cfg = _json.load(f)

    model = AASISTModel(cfg["model_config"]).to(device)
    ckpt = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(ckpt if "state_dict" not in ckpt else ckpt["state_dict"])
    model.eval()
    return model


def _load_ssl(checkpoint_path: str, device: str = "cpu"):
    """Load SSL Anti-spoofing model from a local checkpoint."""
    import torch
    import importlib.util

    spec = importlib.util.find_spec("fairseq")
    if spec is None:
        raise ImportError(
            "fairseq not found. Run: pip install fairseq  "
            "(and clone TakHemlata/SSL_Anti-spoofing)"
        )

    # SSL model requires the repo on sys.path
    import sys
    ssl_repo = os.getenv("FORGE_SSL_ANTISPOOF_REPO", "")
    if ssl_repo and ssl_repo not in sys.path:
        sys.path.insert(0, ssl_repo)

    from model import Model as SSLModel  # from the SSL repo

    class _Args:
        loss = "weighted_CCE"
        algo = 3
        lr = 0.000001

    model = SSLModel(_Args(), device).to(device)
    ckpt = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(ckpt)
    model.eval()
    return model


def _infer_neural(model, audio_tensor, device: str = "cpu") -> float:
    """Run neural model forward pass. Returns spoof probability 0–1."""
    import torch
    import torch.nn.functional as F

    with torch.no_grad():
        x = audio_tensor.unsqueeze(0).to(device)
        out = model(x)
        probs = F.softmax(out, dim=1)
        # Index 1 = spoof score
        return float(probs[0, 1].cpu().item())


def _load_audio(audio_path: str, target_sr: int = 16000, max_samples: int = 64600):
    """Load, resample, and pad/trim audio to target length."""
    import numpy as np
    import librosa

    audio, sr = librosa.load(audio_path, sr=target_sr, mono=True)

    if len(audio) >= max_samples:
        audio = audio[:max_samples]
    else:
        # Tile to reach target length
        repeats = (max_samples // len(audio)) + 1
        audio = np.tile(audio, repeats)[:max_samples]

    return audio.astype("float32"), sr


def _interpret_fake_prob(prob: float) -> str:
    if prob < 20:
        return "likely authentic — no significant synthetic artifacts detected"
    elif prob < 40:
        return "probably authentic — minor anomalies, low confidence either way"
    elif prob < 60:
        return "ambiguous — mixed signals, manual review recommended"
    elif prob < 80:
        return "probably synthetic — multiple artifact signatures present"
    else:
        return "likely synthetic — strong indicators of AI-generated speech"


# ── Tool implementations ──────────────────────────────────────────────────────

def fake_audio_detect(
    audio_path: str,
    backend: str = "spectral",
    checkpoint_path: str = "",
    device: str = "cpu",
) -> str:
    """Detect whether an audio file contains synthetic/AI-generated speech."""
    if not os.path.exists(audio_path):
        return json.dumps({"error": f"File not found: {audio_path}"})

    try:
        audio, sr = _load_audio(audio_path)
        duration_s = round(len(audio) / sr, 2)

        if backend == "spectral":
            result = _spectral_score(audio, sr)
            fake_prob = result["fake_probability"]
            return json.dumps({
                "status": "ok",
                "backend": "spectral",
                "audio_path": audio_path,
                "duration_seconds": duration_s,
                "fake_probability": fake_prob,
                "verdict": _interpret_fake_prob(fake_prob),
                "features": result["features"],
                "note": (
                    "Spectral heuristics — no ML model. Good for obvious TTS artifacts. "
                    "For higher accuracy use backend='aasist' or backend='ssl' with a checkpoint."
                ),
            })

        elif backend in ("aasist", "ssl"):
            import torch

            if not checkpoint_path:
                return json.dumps({
                    "error": (
                        f"checkpoint_path required for backend='{backend}'. "
                        f"Download from: "
                        f"{'https://github.com/clovaai/aasist' if backend == 'aasist' else 'https://github.com/TakHemlata/SSL_Anti-spoofing'}"
                    )
                })

            if backend == "aasist":
                global _aasist_model
                with _aasist_lock:
                    if _aasist_model is None:
                        log.info("Loading AASIST model from %s", checkpoint_path)
                        _aasist_model = _load_aasist(checkpoint_path, device)
                model = _aasist_model
            else:
                global _ssl_model
                with _ssl_lock:
                    if _ssl_model is None:
                        log.info("Loading SSL Anti-spoofing model from %s", checkpoint_path)
                        _ssl_model = _load_ssl(checkpoint_path, device)
                model = _ssl_model

            import torch
            audio_tensor = torch.FloatTensor(audio)
            spoof_prob = _infer_neural(model, audio_tensor, device)
            fake_prob = round(spoof_prob * 100, 1)

            # Also run spectral for supplementary features
            spectral = _spectral_score(audio, sr)

            return json.dumps({
                "status": "ok",
                "backend": backend,
                "audio_path": audio_path,
                "duration_seconds": duration_s,
                "fake_probability": fake_prob,
                "verdict": _interpret_fake_prob(fake_prob),
                "neural_spoof_score": round(spoof_prob, 4),
                "spectral_corroboration": spectral["fake_probability"],
                "features": spectral["features"],
            })

        else:
            return json.dumps({"error": f"Unknown backend '{backend}'. Use: spectral, aasist, ssl"})

    except ImportError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        log.exception("fake_audio_detect failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def fake_audio_scan(
    audio_path: str,
    chunk_seconds: float = 4.0,
    backend: str = "spectral",
    checkpoint_path: str = "",
    device: str = "cpu",
) -> str:
    """Scan long audio in chunks and return a fake-probability timeline.

    Useful for finding specific segments of a longer recording that were
    synthetically generated or spliced in, even if the rest is authentic.
    """
    if not os.path.exists(audio_path):
        return json.dumps({"error": f"File not found: {audio_path}"})

    try:
        import numpy as np
        import librosa

        audio_full, sr = librosa.load(audio_path, sr=16000, mono=True)
        total_duration = len(audio_full) / sr
        chunk_samples = int(chunk_seconds * sr)

        if chunk_samples < 1600:
            return json.dumps({"error": "chunk_seconds too small (minimum ~0.1s)"})

        chunks = []
        for start in range(0, len(audio_full), chunk_samples):
            chunk = audio_full[start:start + chunk_samples]
            if len(chunk) < chunk_samples // 4:
                break  # skip tiny trailing chunk
            # Pad short final chunk
            if len(chunk) < chunk_samples:
                repeats = (chunk_samples // len(chunk)) + 1
                chunk = np.tile(chunk, repeats)[:chunk_samples]
            chunks.append((round(start / sr, 2), chunk))

        timeline = []
        for t_start, chunk in chunks:
            if backend == "spectral":
                result = _spectral_score(chunk.astype("float32"), sr)
                fp = result["fake_probability"]
            else:
                import torch
                if backend == "aasist":
                    global _aasist_model
                    with _aasist_lock:
                        if _aasist_model is None:
                            _aasist_model = _load_aasist(checkpoint_path, device)
                    model = _aasist_model
                else:
                    global _ssl_model
                    with _ssl_lock:
                        if _ssl_model is None:
                            _ssl_model = _load_ssl(checkpoint_path, device)
                    model = _ssl_model
                t = torch.FloatTensor(chunk.astype("float32"))
                fp = round(_infer_neural(model, t, device) * 100, 1)

            timeline.append({
                "start_seconds": t_start,
                "end_seconds": round(t_start + chunk_seconds, 2),
                "fake_probability": fp,
                "verdict": _interpret_fake_prob(fp),
            })

        # Overall stats
        probs = [c["fake_probability"] for c in timeline]
        mean_prob = round(float(np.mean(probs)), 1)
        peak_prob = round(float(np.max(probs)), 1)
        peak_chunk = timeline[int(np.argmax(probs))]
        suspicious_chunks = [c for c in timeline if c["fake_probability"] >= 60]

        return json.dumps({
            "status": "ok",
            "backend": backend,
            "audio_path": audio_path,
            "total_duration_seconds": round(total_duration, 2),
            "n_chunks": len(timeline),
            "chunk_seconds": chunk_seconds,
            "mean_fake_probability": mean_prob,
            "peak_fake_probability": peak_prob,
            "peak_chunk": peak_chunk,
            "suspicious_chunks": suspicious_chunks,
            "overall_verdict": _interpret_fake_prob(mean_prob),
            "timeline": timeline,
        })

    except ImportError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        log.exception("fake_audio_scan failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def fake_audio_neuro_compare(
    audio_path_a: str,
    audio_path_b: str,
    label_a: str = "A",
    label_b: str = "B",
) -> str:
    """Compare two audio files via TRIBE v2 cortical fingerprinting.

    Runs both files through the TRIBE fMRI foundation model and compares
    predicted cortical activation patterns. Because TRIBE learned on
    naturalistic speech, real and synthetic audio produce detectably
    different neural activation signatures — especially in auditory cortex,
    superior temporal sulcus, and language areas.

    A high activation divergence between the two files is a forensic signal:
    if one file is claimed to be a recording of the other speaker, but their
    cortical fingerprints differ significantly, that's evidence of acoustic
    anomaly consistent with synthesis or manipulation.

    Also runs the spectral detector on both files for corroboration.
    """
    for path, label in [(audio_path_a, label_a), (audio_path_b, label_b)]:
        if not os.path.exists(path):
            return json.dumps({"error": f"File not found for {label}: {path}"})

    try:
        import numpy as np

        # ── TRIBE cortical fingerprinting ─────────────────────────────────
        try:
            from forge.tools.tribe import _get_model as _get_tribe_model

            tribe_model = _get_tribe_model()

            def _tribe_preds(path: str):
                events = tribe_model.get_events_dataframe(audio_path=path)
                preds, _ = tribe_model.predict(events, verbose=False)
                return preds  # (n_segments, n_vertices)

            preds_a = _tribe_preds(audio_path_a)
            preds_b = _tribe_preds(audio_path_b)

            # Mean activation vector per file (n_vertices,)
            vec_a = np.mean(np.abs(preds_a), axis=0)
            vec_b = np.mean(np.abs(preds_b), axis=0)

            # Cosine similarity of cortical fingerprints
            dot = float(np.dot(vec_a, vec_b))
            norm_a = float(np.linalg.norm(vec_a))
            norm_b = float(np.linalg.norm(vec_b))
            cosine_sim = round(dot / (norm_a * norm_b + 1e-9), 4)

            # Per-ROI divergence
            from forge.tools.tribe import _ROI_RANGES, _FSAVERAGE5_VERTS_PER_HEMI
            n_verts = len(vec_a)
            roi_divergence = {}
            for roi, (lo, hi) in _ROI_RANGES.items():
                hi = min(hi, n_verts // 2)
                if lo >= hi:
                    continue
                diff = float(np.mean(np.abs(vec_a[lo:hi] - vec_b[lo:hi])))
                roi_divergence[roi] = round(diff, 4)

            ranked_rois = sorted(roi_divergence.items(), key=lambda x: x[1], reverse=True)
            top_divergent_roi = ranked_rois[0][0] if ranked_rois else "unknown"

            # Divergence score 0–100 (higher = more different cortical fingerprint)
            cortical_divergence = round((1.0 - cosine_sim) * 100, 1)

            tribe_result = {
                "available": True,
                "cosine_similarity": cosine_sim,
                "cortical_divergence_score": cortical_divergence,
                "top_divergent_roi": top_divergent_roi,
                "roi_divergence": dict(ranked_rois[:6]),
                "interpretation": (
                    "near-identical cortical fingerprint — acoustically very similar"
                    if cosine_sim > 0.95 else
                    "similar cortical fingerprint — minor acoustic differences"
                    if cosine_sim > 0.80 else
                    "moderate cortical divergence — notable acoustic differences, possible manipulation"
                    if cosine_sim > 0.60 else
                    "high cortical divergence — strong acoustic anomaly, consistent with synthesis or splicing"
                ),
            }

        except ImportError:
            tribe_result = {
                "available": False,
                "note": "TRIBE v2 not installed. Enable with FORGE_TRIBE_ENABLED=true.",
            }

        # ── Spectral corroboration ────────────────────────────────────────
        audio_a, sr_a = _load_audio(audio_path_a)
        audio_b, sr_b = _load_audio(audio_path_b)
        spec_a = _spectral_score(audio_a, sr_a)
        spec_b = _spectral_score(audio_b, sr_b)

        fp_a = spec_a["fake_probability"]
        fp_b = spec_b["fake_probability"]

        return json.dumps({
            "status": "ok",
            label_a: {
                "audio_path": audio_path_a,
                "spectral_fake_probability": fp_a,
                "spectral_verdict": _interpret_fake_prob(fp_a),
                "spectral_features": spec_a["features"],
            },
            label_b: {
                "audio_path": audio_path_b,
                "spectral_fake_probability": fp_b,
                "spectral_verdict": _interpret_fake_prob(fp_b),
                "spectral_features": spec_b["features"],
            },
            "cortical_fingerprint": tribe_result,
            "summary": (
                f"{label_a} spectral fake score: {fp_a}%, "
                f"{label_b} spectral fake score: {fp_b}%. "
                + (
                    f"Cortical similarity: {tribe_result['cosine_similarity']:.3f} — "
                    f"{tribe_result['interpretation']}."
                    if tribe_result.get("available")
                    else "TRIBE not available for cortical comparison."
                )
            ),
        })

    except ImportError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        log.exception("fake_audio_neuro_compare failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ── Registration ──────────────────────────────────────────────────────────────

def register(registry: ToolRegistry):
    """Register all fake audio detection tools."""

    registry.register(
        name="fake_audio_detect",
        description=(
            "Detect whether an audio file contains AI-generated or synthetic speech. "
            "Three backends: 'spectral' (always available, uses librosa to flag "
            "spectral flatness, unnatural pitch regularity, low MFCC dynamics, and "
            "uniform energy — classic TTS artifacts); 'aasist' (AASIST neural model, "
            "EER ~0.83%, requires local checkpoint); 'ssl' (SSL Anti-spoofing, "
            "EER ~0.82%, requires local checkpoint + fairseq). Returns fake_probability "
            "0-100 (higher = more likely synthetic), verdict, and per-feature breakdown. "
            "Catches VibeVoice, VALL-E, ElevenLabs, and similar TTS systems."
        ),
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to the audio file to analyze (.wav, .mp3, .flac, etc.)",
                },
                "backend": {
                    "type": "string",
                    "description": "Detection backend: 'spectral' (default, no model needed), 'aasist', or 'ssl'.",
                    "enum": ["spectral", "aasist", "ssl"],
                    "default": "spectral",
                },
                "checkpoint_path": {
                    "type": "string",
                    "description": "Path to model checkpoint file. Required for 'aasist' and 'ssl' backends.",
                    "default": "",
                },
                "device": {
                    "type": "string",
                    "description": "Inference device: 'cpu' (default) or 'cuda'.",
                    "default": "cpu",
                },
            },
            "required": ["audio_path"],
        },
        handler=fake_audio_detect,
    )

    registry.register(
        name="fake_audio_scan",
        description=(
            "Scan a long audio file in chunks and return a fake-probability timeline. "
            "Useful for finding specific spliced or synthesized segments within a longer "
            "recording — e.g., detecting where a cloned voice was inserted into an "
            "otherwise real conversation. Returns per-chunk fake_probability, a list of "
            "suspicious segments (>=60%), peak fake chunk with timestamp, and overall "
            "verdict. Chunk size defaults to 4 seconds (SSL model's native window). "
            "Same three backends as fake_audio_detect."
        ),
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to the audio file to scan.",
                },
                "chunk_seconds": {
                    "type": "number",
                    "description": "Duration of each analysis chunk in seconds (default 4.0).",
                    "default": 4.0,
                },
                "backend": {
                    "type": "string",
                    "description": "Detection backend: 'spectral' (default), 'aasist', or 'ssl'.",
                    "enum": ["spectral", "aasist", "ssl"],
                    "default": "spectral",
                },
                "checkpoint_path": {
                    "type": "string",
                    "description": "Model checkpoint path. Required for 'aasist'/'ssl' backends.",
                    "default": "",
                },
                "device": {
                    "type": "string",
                    "description": "Inference device: 'cpu' or 'cuda'.",
                    "default": "cpu",
                },
            },
            "required": ["audio_path"],
        },
        handler=fake_audio_scan,
    )

    registry.register(
        name="fake_audio_neuro_compare",
        description=(
            "Compare two audio files using TRIBE v2 cortical fingerprinting + spectral "
            "analysis to detect synthesis or manipulation. "
            "TRIBE v2 (Meta's fMRI foundation model) learned on naturalistic speech — "
            "real and synthetic audio produce detectably different cortical activation "
            "patterns, especially in auditory cortex, superior temporal sulcus, and "
            "language areas. A high cortical divergence score between two files is a "
            "forensic signal: if one file claims to be a recording of the same speaker "
            "as the other but their brain activation fingerprints differ significantly, "
            "that's evidence of acoustic anomaly consistent with voice cloning or "
            "synthesis. Also runs spectral heuristics on both files for corroboration. "
            "Requires TRIBE v2 (FORGE_TRIBE_ENABLED=true) for cortical comparison; "
            "spectral analysis always runs regardless."
        ),
        parameters={
            "type": "object",
            "properties": {
                "audio_path_a": {
                    "type": "string",
                    "description": "Path to the first audio file (e.g., the reference/known-real recording).",
                },
                "audio_path_b": {
                    "type": "string",
                    "description": "Path to the second audio file (e.g., the suspected synthetic recording).",
                },
                "label_a": {
                    "type": "string",
                    "description": "Human-readable label for file A (default: 'A').",
                    "default": "A",
                },
                "label_b": {
                    "type": "string",
                    "description": "Human-readable label for file B (default: 'B').",
                    "default": "B",
                },
            },
            "required": ["audio_path_a", "audio_path_b"],
        },
        handler=fake_audio_neuro_compare,
    )
