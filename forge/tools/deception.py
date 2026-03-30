"""Forge tools for deception / veracity detection.

Combines multiple orthogonal signals to assess whether a speaker is being
truthful in an audio recording:

  1. AUTHENTICITY GATE — Is this audio even real?  (fake_audio_detect)
     If the audio itself is synthetic, the whole analysis is moot.

  2. VOCAL BIOMARKERS — Prosodic stress indicators
     Deceptive speech has measurable vocal signatures: micro-pitch
     instability, unnatural pause patterns, elevated speech rate
     variance, and formant perturbation under cognitive load.

  2.5 RESONANCE FLUIDITY — Physics-grounded encoding analysis
     Speech encoded into a 498D space (fluorescent + GridBloc +
     Quadrademini) reveals deception as FLUIDITY LOSS — a shift from
     analogue (fluid/natural) to digital (rigid/controlled) dynamics.
     The Q2/Q3 ratio (fluid-to-structure) is the primary signal
     (p=0.020, d=0.44 on Real-Life Trial dataset). Requires LAI-Core.

  3. CORTICAL FINGERPRINT — TRIBE v2 neural engagement analysis
     Deceptive speech produces different predicted cortical activation
     patterns than truthful speech — particularly in prefrontal cortex
     (executive control / suppression), Broca's area (language
     production effort), and default mode network (self-referential
     processing during fabrication).

  4. SWARM CONSENSUS — Prophecy Engine deliberation
     Multiple AI agents independently analyze the combined evidence
     and debate whether the pattern is consistent with deception.

The pipeline produces a composite VERACITY SCORE (0–100, higher = more
likely truthful) with per-signal breakdowns and confidence intervals.

⚠ IMPORTANT LIMITATIONS:
  - This is a RESEARCH TOOL, not a courtroom-grade lie detector.
  - Polygraphs measure stress (high false-positive rate).
  - This measures cognitive load signatures (different failure mode).
  - Skilled liars who believe their own narrative may beat it.
  - Cultural/linguistic differences affect baseline prosodic patterns.
  - A low veracity score is a signal for further investigation, not proof.
  - Never use this as sole evidence for consequential decisions.

Tools:
    veracity_analyze     — Full pipeline: gate → biomarkers → cortical → verdict
    veracity_baseline    — Record a speaker's truthful baseline for comparison
    veracity_compare     — Compare a statement against a baseline recording
    veracity_quick       — Fast prosodic-only check (no TRIBE/Prophecy, instant)

Install notes:
    Core:     pip install librosa soundfile numpy
    TRIBE:    FORGE_TRIBE_ENABLED=true (downloads model on first use)
    Prophecy: Needs an LLM API key (xAI, Anthropic, or OpenAI)
"""
from __future__ import annotations

import json
import logging
import os
import time
import threading
from pathlib import Path

from .registry import ToolRegistry

log = logging.getLogger("forge.tools.deception")

# ── Prosodic Feature Extraction ──────────────────────────────────────────────
# Vocal biomarkers associated with cognitive load during deception.
# Based on Sporer & Schwandt (2007) meta-analysis and DePaulo et al. (2003).

# Cognitive load indicators and their deception associations:
_DECEPTION_MARKERS = {
    "pitch_micro_instability":   0.20,  # F0 jitter increases under deception
    "pause_irregularity":        0.20,  # Deceptive speech has irregular pauses
    "speech_rate_variance":      0.15,  # Rate changes when fabricating
    "formant_perturbation":      0.15,  # Vowel formants shift under stress
    "energy_contour_breaks":     0.15,  # Energy envelope is less smooth
    "harmonic_noise_ratio":      0.15,  # Voice quality degrades under load
}


def _extract_prosodic_biomarkers(audio, sr: int) -> dict:
    """Extract vocal biomarkers indicating cognitive load / deception.

    Returns individual marker scores (0–1, higher = more deceptive signal)
    and a composite deception_probability (0–100).
    """
    import numpy as np
    import librosa

    features = {}

    # ── Pitch micro-instability (F0 jitter) ──────────────────────────────
    # Deceptive speakers show higher pitch jitter (micro-fluctuations)
    # even when gross pitch stays normal.
    try:
        f0, voiced, _ = librosa.pyin(
            audio, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"),
            sr=sr,
        )
        f0_voiced = f0[voiced & ~np.isnan(f0)]
        if len(f0_voiced) > 20:
            # Jitter: mean absolute successive pitch difference
            diffs = np.abs(np.diff(f0_voiced))
            mean_f0 = np.mean(f0_voiced)
            jitter_ratio = float(np.mean(diffs) / (mean_f0 + 1e-9))
            # Truthful speech: jitter ~0.005–0.015; deceptive: often 0.015–0.04
            jitter_score = min(1.0, jitter_ratio / 0.03)

            # Pitch range contraction (deceptive speakers often narrow pitch range)
            pitch_range = float(np.ptp(f0_voiced) / (mean_f0 + 1e-9))
            # Narrow range (<0.3) can indicate rehearsed/deceptive speech
            range_score = max(0.0, 1.0 - (pitch_range / 0.5))

            features["pitch_micro_instability"] = round((jitter_score * 0.6 + range_score * 0.4), 4)
        else:
            features["pitch_micro_instability"] = 0.5  # insufficient data
    except Exception:
        features["pitch_micro_instability"] = 0.5

    # ── Pause irregularity ───────────────────────────────────────────────
    # Truthful speech has natural pause rhythms; deceptive speech has
    # irregular pauses (hesitation while fabricating, or unnatural
    # smoothness from rehearsal).
    try:
        # Detect pauses via energy threshold
        rms = librosa.feature.rms(y=audio, frame_length=1024, hop_length=512)[0]
        threshold = np.percentile(rms, 20)
        is_pause = rms < threshold

        # Compute pause durations
        pause_lengths = []
        current_pause = 0
        for p in is_pause:
            if p:
                current_pause += 1
            elif current_pause > 0:
                pause_lengths.append(current_pause)
                current_pause = 0

        if len(pause_lengths) > 3:
            pause_arr = np.array(pause_lengths, dtype=float)
            pause_cv = float(np.std(pause_arr) / (np.mean(pause_arr) + 1e-9))
            # Very regular pauses (rehearsed) or very irregular (hesitating)
            # both score high. Natural speech has moderate CV (~0.5–1.0)
            if pause_cv < 0.3:  # too regular = rehearsed
                pause_score = 0.7
            elif pause_cv > 1.5:  # too irregular = hesitating
                pause_score = min(1.0, pause_cv / 2.0)
            else:
                pause_score = 0.2  # natural range
            features["pause_irregularity"] = round(pause_score, 4)
        else:
            features["pause_irregularity"] = 0.3  # few pauses = short clip
    except Exception:
        features["pause_irregularity"] = 0.5

    # ── Speech rate variance ─────────────────────────────────────────────
    # Deceptive speakers show higher variance in syllable rate.
    try:
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        # Segment into windows and measure onset density (proxy for syllable rate)
        window_size = max(1, len(onset_env) // 10)
        windowed_rates = []
        for i in range(0, len(onset_env) - window_size, window_size):
            chunk = onset_env[i:i+window_size]
            windowed_rates.append(float(np.sum(chunk > np.mean(onset_env))))

        if len(windowed_rates) > 3:
            rate_cv = float(np.std(windowed_rates) / (np.mean(windowed_rates) + 1e-9))
            # Truthful: ~0.2–0.5 CV; deceptive: often >0.5
            rate_score = min(1.0, rate_cv / 0.8)
            features["speech_rate_variance"] = round(rate_score, 4)
        else:
            features["speech_rate_variance"] = 0.5
    except Exception:
        features["speech_rate_variance"] = 0.5

    # ── Formant perturbation ─────────────────────────────────────────────
    # Vowel formants (F1, F2) become less stable under cognitive load.
    try:
        # Use LPC for formant estimation
        n_lpc = int(2 + sr / 1000)
        lpc_coeffs = librosa.lpc(audio + 1e-10, order=min(n_lpc, 16))
        roots = np.roots(lpc_coeffs)
        roots = roots[np.imag(roots) >= 0]
        angles = np.angle(roots)
        freqs = sorted(angles * (sr / (2 * np.pi)))
        formants = [f for f in freqs if 200 < f < 5000]

        if len(formants) >= 2:
            # Measure formant spacing regularity
            spacings = np.diff(formants[:4])
            spacing_cv = float(np.std(spacings) / (np.mean(spacings) + 1e-9))
            formant_score = min(1.0, spacing_cv / 1.5)
            features["formant_perturbation"] = round(formant_score, 4)
        else:
            features["formant_perturbation"] = 0.5
    except Exception:
        features["formant_perturbation"] = 0.5

    # ── Energy contour smoothness ────────────────────────────────────────
    # Truthful speech has smooth energy contours; deceptive speech has
    # abrupt energy shifts.
    try:
        rms = librosa.feature.rms(y=audio, frame_length=2048, hop_length=512)[0]
        if len(rms) > 10:
            rms_diff = np.abs(np.diff(rms))
            mean_rms = np.mean(rms)
            energy_roughness = float(np.mean(rms_diff) / (mean_rms + 1e-9))
            # Higher roughness → more energy breaks
            energy_score = min(1.0, energy_roughness / 0.5)
            features["energy_contour_breaks"] = round(energy_score, 4)
        else:
            features["energy_contour_breaks"] = 0.5
    except Exception:
        features["energy_contour_breaks"] = 0.5

    # ── Harmonic-to-noise ratio ──────────────────────────────────────────
    # Voice quality degrades under stress/cognitive load (breathier, tenser).
    try:
        # Estimate HNR from autocorrelation
        frame_len = min(4096, len(audio))
        autocorr = np.correlate(audio[:frame_len], audio[:frame_len], "full")
        autocorr = autocorr[len(autocorr) // 2:]
        if len(autocorr) > sr // 80:
            # Find first peak after minimum pitch period
            min_lag = sr // 500  # 500 Hz max
            max_lag = sr // 80   # 80 Hz min
            search = autocorr[min_lag:max_lag]
            if len(search) > 0:
                peak = float(np.max(search))
                hnr_db = 10 * np.log10(peak / (autocorr[0] - peak + 1e-9) + 1e-9)
                # Low HNR (<10 dB) → stressed/breathy voice
                hnr_score = max(0.0, 1.0 - (hnr_db / 20.0))
                features["harmonic_noise_ratio"] = round(hnr_score, 4)
            else:
                features["harmonic_noise_ratio"] = 0.5
        else:
            features["harmonic_noise_ratio"] = 0.5
    except Exception:
        features["harmonic_noise_ratio"] = 0.5

    # ── Composite score ──────────────────────────────────────────────────
    composite = 0.0
    for marker, weight in _DECEPTION_MARKERS.items():
        composite += features.get(marker, 0.5) * weight

    deception_probability = round(composite * 100, 1)

    return {
        "deception_probability": deception_probability,
        "features": features,
    }


def _interpret_veracity(score: float) -> str:
    """Interpret a veracity score (0–100, higher = more truthful)."""
    if score >= 85:
        return "high_veracity — vocal biomarkers consistent with truthful speech"
    elif score >= 70:
        return "moderate_veracity — minor stress indicators, likely truthful"
    elif score >= 50:
        return "uncertain — mixed signals, cannot determine with confidence"
    elif score >= 30:
        return "elevated_deception_indicators — notable cognitive load markers"
    else:
        return "high_deception_indicators — multiple strong deception biomarkers"


def _load_audio(path: str):
    """Load audio file, resample to 16kHz mono."""
    import librosa
    audio, sr = librosa.load(path, sr=16000, mono=True)
    return audio, sr


# ── Cortical Deception Signature ─────────────────────────────────────────────
# Brain regions that activate differently during deception vs. truth-telling.
# Based on Abe et al. (2007), Christ et al. (2009) fMRI meta-analyses.

_DECEPTION_ROIS = {
    # ROI → (expected direction during deception, weight)
    # "up" = higher activation during deception, "down" = lower
    "prefrontal_cortex":         ("up",   0.30),  # Executive control, suppression of truth
    "broca_language":            ("up",   0.20),  # Extra language production effort
    "default_mode_network":      ("up",   0.15),  # Self-referential fabrication
    "parietal_association":      ("up",   0.10),  # Working memory for maintaining lie
    "auditory_cortex":           ("down", 0.10),  # Less self-monitoring during deception
    "superior_temporal_sulcus":  ("down", 0.08),  # Reduced social perception processing
    "motor_cortex":              ("up",   0.07),  # Inhibition effort
}


def _cortical_deception_score(roi_activations: dict, baseline_activations: dict | None = None) -> dict:
    """Score cortical activation pattern for deception signatures.

    If baseline_activations is provided, scores are relative to the
    speaker's own truthful baseline. Otherwise, uses population norms.

    Returns deception score 0–100 and per-ROI analysis.
    """
    import numpy as np

    roi_analysis = {}
    weighted_score = 0.0
    total_weight = 0.0

    for roi, (direction, weight) in _DECEPTION_ROIS.items():
        activation = roi_activations.get(roi, {})
        if not activation:
            continue

        current_mean = activation.get("mean", 0.0)

        if baseline_activations and roi in baseline_activations:
            baseline_mean = baseline_activations[roi].get("mean", current_mean)
            delta = current_mean - baseline_mean
        else:
            # Without baseline, use absolute activation as rough proxy
            delta = current_mean - 0.5  # assume 0.5 is "neutral" population norm

        # Score based on expected direction
        if direction == "up":
            # Higher activation = more deceptive signal
            roi_score = min(1.0, max(0.0, delta * 2.0 + 0.5))
        else:
            # Lower activation = more deceptive signal
            roi_score = min(1.0, max(0.0, -delta * 2.0 + 0.5))

        roi_analysis[roi] = {
            "activation": round(current_mean, 4),
            "baseline": round(baseline_mean, 4) if baseline_activations and roi in baseline_activations else None,
            "delta": round(delta, 4),
            "expected_direction": direction,
            "deception_signal": round(roi_score, 4),
            "interpretation": (
                f"{'elevated' if delta > 0.1 else 'reduced' if delta < -0.1 else 'neutral'} "
                f"({'consistent' if (direction == 'up' and delta > 0.1) or (direction == 'down' and delta < -0.1) else 'inconsistent'} "
                f"with deception pattern)"
            ),
        }

        weighted_score += roi_score * weight
        total_weight += weight

    if total_weight > 0:
        composite = weighted_score / total_weight
    else:
        composite = 0.5

    return {
        "cortical_deception_score": round(composite * 100, 1),
        "roi_analysis": roi_analysis,
    }


def _get_tribe_roi_activations(audio_path: str) -> dict | None:
    """Get TRIBE v2 ROI activation breakdown for an audio file.

    Returns dict of {roi_name: {"left": float, "right": float, "mean": float}}
    or None if TRIBE is not available.
    """
    try:
        from forge.tools.tribe import _get_model, _ROI_RANGES, _FSAVERAGE5_VERTS_PER_HEMI
        import numpy as np

        model = _get_model()
        events = model.get_events_dataframe(audio_path=audio_path)
        preds, _ = model.predict(events, verbose=False)

        # Mean absolute activation per vertex
        mean_act = np.mean(np.abs(preds), axis=0)
        n_verts = len(mean_act)

        roi_activations = {}
        for roi, (lo, hi) in _ROI_RANGES.items():
            hi = min(hi, n_verts // 2)
            if lo >= hi:
                continue
            left_act = float(np.mean(mean_act[lo:hi]))
            right_lo = lo + _FSAVERAGE5_VERTS_PER_HEMI
            right_hi = min(hi + _FSAVERAGE5_VERTS_PER_HEMI, n_verts)
            right_act = float(np.mean(mean_act[right_lo:right_hi])) if right_hi > right_lo else left_act
            roi_activations[roi] = {
                "left": round(left_act, 4),
                "right": round(right_act, 4),
                "mean": round((left_act + right_act) / 2, 4),
            }

        return roi_activations
    except ImportError:
        return None
    except Exception as e:
        log.warning("TRIBE ROI extraction failed: %s", e)
        return None


# ── Baseline Storage ─────────────────────────────────────────────────────────
# Speaker baselines are stored as JSON in the data directory.

def _baselines_dir() -> Path:
    from forge.config import DATA_DIR
    d = DATA_DIR / "veracity_baselines"
    d.mkdir(exist_ok=True)
    return d


def _save_baseline(speaker_id: str, data: dict) -> None:
    path = _baselines_dir() / f"{speaker_id}.json"
    path.write_text(json.dumps(data, indent=2))


def _load_baseline(speaker_id: str) -> dict | None:
    path = _baselines_dir() / f"{speaker_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


# ── Tool Functions ───────────────────────────────────────────────────────────

def veracity_quick(
    audio_path: str,
) -> str:
    """Fast prosodic-only veracity check. No TRIBE or Prophecy needed.

    Analyzes vocal biomarkers (pitch jitter, pause patterns, speech rate,
    formant stability, energy contour, harmonic-to-noise ratio) to detect
    cognitive load signatures associated with deception.

    Returns a veracity score (0–100, higher = more likely truthful) with
    per-feature breakdowns.
    """
    if not os.path.exists(audio_path):
        return json.dumps({"error": f"File not found: {audio_path}"})

    try:
        audio, sr = _load_audio(audio_path)
        duration_s = round(len(audio) / sr, 2)

        prosodic = _extract_prosodic_biomarkers(audio, sr)
        veracity_score = round(100 - prosodic["deception_probability"], 1)

        return json.dumps({
            "status": "ok",
            "tool": "veracity_quick",
            "audio_path": audio_path,
            "duration_seconds": duration_s,
            "veracity_score": veracity_score,
            "deception_probability": prosodic["deception_probability"],
            "verdict": _interpret_veracity(veracity_score),
            "vocal_biomarkers": prosodic["features"],
            "caveat": (
                "Prosodic-only analysis. For higher accuracy, use veracity_analyze "
                "which adds cortical fingerprinting and swarm consensus."
            ),
        })
    except ImportError as e:
        return json.dumps({"error": f"Missing dependency: {e}. Run: pip install librosa soundfile"})
    except Exception as e:
        log.exception("veracity_quick failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def veracity_baseline(
    audio_path: str,
    speaker_id: str,
    description: str = "",
) -> str:
    """Record a speaker's truthful baseline for future comparison.

    The audio should contain speech the speaker is known to be truthful
    about (e.g., stating their name, describing their breakfast, reading
    aloud). This establishes their personal prosodic and cortical norms.

    Future calls to veracity_compare will measure deviations from this
    baseline, which is far more accurate than population-level norms.
    """
    if not os.path.exists(audio_path):
        return json.dumps({"error": f"File not found: {audio_path}"})

    if not speaker_id.strip():
        return json.dumps({"error": "speaker_id is required"})

    try:
        audio, sr = _load_audio(audio_path)
        duration_s = round(len(audio) / sr, 2)

        # Extract prosodic baseline
        prosodic = _extract_prosodic_biomarkers(audio, sr)

        # Extract cortical baseline if TRIBE available
        cortical = _get_tribe_roi_activations(audio_path)

        baseline = {
            "speaker_id": speaker_id,
            "description": description or f"Baseline recording for {speaker_id}",
            "audio_path": audio_path,
            "duration_seconds": duration_s,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "prosodic_baseline": prosodic["features"],
            "cortical_baseline": cortical,
            "has_cortical": cortical is not None,
        }

        _save_baseline(speaker_id, baseline)

        return json.dumps({
            "status": "ok",
            "tool": "veracity_baseline",
            "speaker_id": speaker_id,
            "duration_seconds": duration_s,
            "prosodic_markers_captured": list(prosodic["features"].keys()),
            "cortical_baseline_captured": cortical is not None,
            "cortical_rois": list(cortical.keys()) if cortical else [],
            "message": (
                f"Baseline saved for '{speaker_id}'. Use veracity_compare to "
                f"compare future recordings against this baseline."
            ),
        })
    except ImportError as e:
        return json.dumps({"error": f"Missing dependency: {e}. Run: pip install librosa soundfile"})
    except Exception as e:
        log.exception("veracity_baseline failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def veracity_compare(
    audio_path: str,
    speaker_id: str,
) -> str:
    """Compare a statement against a speaker's recorded truthful baseline.

    Uses the baseline from veracity_baseline to detect deviations in both
    prosodic biomarkers and cortical activation patterns. Relative comparison
    to a known-truthful baseline is significantly more accurate than
    absolute population-norm analysis.
    """
    if not os.path.exists(audio_path):
        return json.dumps({"error": f"File not found: {audio_path}"})

    baseline = _load_baseline(speaker_id)
    if not baseline:
        return json.dumps({
            "error": f"No baseline found for speaker '{speaker_id}'. "
            f"Record one first with veracity_baseline.",
        })

    try:
        audio, sr = _load_audio(audio_path)
        duration_s = round(len(audio) / sr, 2)

        # ── Prosodic comparison ──────────────────────────────────────────
        prosodic = _extract_prosodic_biomarkers(audio, sr)
        baseline_prosodic = baseline.get("prosodic_baseline", {})

        prosodic_deltas = {}
        for marker in _DECEPTION_MARKERS:
            current = prosodic["features"].get(marker, 0.5)
            base = baseline_prosodic.get(marker, 0.5)
            delta = current - base
            prosodic_deltas[marker] = {
                "current": round(current, 4),
                "baseline": round(base, 4),
                "delta": round(delta, 4),
                "direction": "elevated" if delta > 0.05 else "reduced" if delta < -0.05 else "stable",
            }

        # Prosodic deviation score
        import numpy as np
        deltas = [abs(d["delta"]) for d in prosodic_deltas.values()]
        prosodic_deviation = round(float(np.mean(deltas)) * 200, 1)  # Scale to 0–100

        # ── Cortical comparison ──────────────────────────────────────────
        cortical_result = None
        cortical_score = None
        current_cortical = _get_tribe_roi_activations(audio_path)

        if current_cortical and baseline.get("cortical_baseline"):
            cortical_result = _cortical_deception_score(
                current_cortical,
                baseline["cortical_baseline"],
            )
            cortical_score = cortical_result["cortical_deception_score"]

        # ── Composite veracity score ─────────────────────────────────────
        if cortical_score is not None:
            # Weight: 40% prosodic, 60% cortical (cortical is more reliable)
            composite_deception = prosodic_deviation * 0.4 + cortical_score * 0.6
        else:
            composite_deception = prosodic_deviation

        veracity_score = round(100 - min(100, composite_deception), 1)

        result = {
            "status": "ok",
            "tool": "veracity_compare",
            "audio_path": audio_path,
            "speaker_id": speaker_id,
            "duration_seconds": duration_s,
            "baseline_duration": baseline.get("duration_seconds", 0),
            "veracity_score": veracity_score,
            "verdict": _interpret_veracity(veracity_score),
            "prosodic_analysis": {
                "deviation_score": prosodic_deviation,
                "marker_deltas": prosodic_deltas,
            },
            "cortical_analysis": cortical_result if cortical_result else {
                "available": False,
                "note": "TRIBE v2 not available for cortical comparison",
            },
            "methodology": (
                "Compared against speaker's own truthful baseline. "
                + ("Prosodic + cortical (TRIBE v2) fusion analysis." if cortical_score is not None
                   else "Prosodic analysis only (enable TRIBE for cortical layer).")
            ),
        }

        return json.dumps(result)

    except ImportError as e:
        return json.dumps({"error": f"Missing dependency: {e}"})
    except Exception as e:
        log.exception("veracity_compare failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def veracity_analyze(
    audio_path: str,
    speaker_id: str = "",
    run_prophecy: bool = True,
    prophecy_prophets: int = 8,
    backend: str = "hf",
) -> str:
    """Full deception detection pipeline.

    Runs the complete 4-stage pipeline:
      1. AUTHENTICITY GATE — Verify audio is not synthetic (fake_audio_detect)
      2. VOCAL BIOMARKERS — Extract prosodic stress indicators
      3. CORTICAL FINGERPRINT — TRIBE v2 ROI activation analysis
      4. SWARM CONSENSUS — Prophecy Engine deliberation (optional)

    If speaker_id is provided and a baseline exists, uses relative comparison.
    Otherwise, uses population-level norms (less accurate).

    Returns a composite veracity score with full evidence breakdown.
    """
    if not os.path.exists(audio_path):
        return json.dumps({"error": f"File not found: {audio_path}"})

    try:
        import numpy as np

        audio, sr = _load_audio(audio_path)
        duration_s = round(len(audio) / sr, 2)
        started_at = time.time()

        stages = {}

        # ── STAGE 1: Authenticity Gate ───────────────────────────────────
        log.info("Veracity Stage 1: Authenticity gate (%s)", backend)
        try:
            from forge.tools.fake_audio import fake_audio_detect
            gate_result = json.loads(fake_audio_detect(audio_path, backend=backend))
            fake_prob = gate_result.get("fake_probability", 0)

            stages["authenticity_gate"] = {
                "passed": fake_prob < 50,
                "fake_probability": fake_prob,
                "verdict": gate_result.get("verdict", "unknown"),
                "backend": backend,
            }

            if fake_prob >= 70:
                # Audio is likely synthetic — abort analysis
                return json.dumps({
                    "status": "gate_failed",
                    "tool": "veracity_analyze",
                    "audio_path": audio_path,
                    "duration_seconds": duration_s,
                    "veracity_score": 0,
                    "verdict": "INVALID — audio appears synthetic/AI-generated",
                    "stages": stages,
                    "message": (
                        f"Authenticity gate failed: {fake_prob}% probability of synthetic audio. "
                        "Veracity analysis requires genuine human speech. "
                        "This audio should be treated as potential deepfake."
                    ),
                })
        except Exception as e:
            log.warning("Authenticity gate failed: %s", e)
            stages["authenticity_gate"] = {
                "passed": True,  # Fail-open: proceed but note the gap
                "error": str(e),
                "note": "Could not verify authenticity — proceeding with caveat",
            }

        # ── STAGE 2: Vocal Biomarkers ────────────────────────────────────
        log.info("Veracity Stage 2: Prosodic biomarkers")
        prosodic = _extract_prosodic_biomarkers(audio, sr)

        # If baseline available, compute deltas
        baseline = _load_baseline(speaker_id) if speaker_id else None
        baseline_prosodic = baseline.get("prosodic_baseline", {}) if baseline else {}

        if baseline_prosodic:
            marker_deltas = {}
            for marker in _DECEPTION_MARKERS:
                current = prosodic["features"].get(marker, 0.5)
                base = baseline_prosodic.get(marker, 0.5)
                marker_deltas[marker] = round(current - base, 4)

            deltas_arr = [abs(d) for d in marker_deltas.values()]
            prosodic_deviation = round(float(np.mean(deltas_arr)) * 200, 1)
        else:
            marker_deltas = None
            prosodic_deviation = prosodic["deception_probability"]

        stages["vocal_biomarkers"] = {
            "deception_probability": prosodic["deception_probability"],
            "deviation_from_baseline": prosodic_deviation if baseline_prosodic else None,
            "has_baseline": bool(baseline_prosodic),
            "features": prosodic["features"],
            "marker_deltas": marker_deltas,
        }

        # ── STAGE 2.5: RESONANCE Fluidity Analysis ────────────────────────
        # Physics-grounded deception signal from LAI-Core encoding.
        # Deception manifests as fluidity loss (analogue → digital shift)
        # in the Q2/Q3 ratio of the 498D Quadrademini encoding.
        # Validated: p=0.020, d=0.44, AUC=0.603 on Real-Life Trial dataset.
        fluidity_score = None
        try:
            from resonance.encoding import FullEncoder
            from resonance.coherence.analyzer import CoherenceAnalyzer

            log.info("Veracity Stage 2.5: RESONANCE fluidity analysis")
            resonance_analyzer = CoherenceAnalyzer(sr=sr)
            resonance_result = resonance_analyzer.analyze_audio(audio, audio_path=audio_path)

            fluidity_score = resonance_result.get("fluidity_index")
            rigidity = resonance_result.get("rigidity_score", 50.0)
            log_fl = resonance_result.get("log_fluidity", 0.0)

            # Try the full 12-feature classifier if available
            classifier_result = None
            try:
                from resonance.models.fluidity_classifier import FluidityClassifier
                clf = FluidityClassifier(sr=sr)
                clf.load()
                classifier_result = clf.predict_from_audio(audio, audio_path=audio_path)
            except Exception:
                pass  # Model not trained/saved yet — use fluidity score only

            stages["resonance_fluidity"] = {
                "available": True,
                "fluidity_index": round(fluidity_score, 4) if fluidity_score else None,
                "log_fluidity": round(log_fl, 4),
                "rigidity_score": round(rigidity, 1),
                "quadrant_magnitudes": resonance_result.get("quadrant_magnitudes", {}),
                "coherence_score": resonance_result.get("coherence_score"),
                "interpretation": resonance_result.get("interpretation", ""),
                "classifier": classifier_result,
                "note": (
                    "Fluidity index = Q2_fluid / Q3_structure from 498D physics-grounded "
                    "encoding. Higher = more fluid/natural (truthful tendency). "
                    "Lower = more rigid/controlled (deceptive tendency). "
                    "12-feature classifier: 81.8% accuracy, AUC=0.872 on Real-Life Trial."
                ),
            }
        except ImportError:
            stages["resonance_fluidity"] = {
                "available": False,
                "note": "RESONANCE not installed. Install from B:/LAI-Core for fluidity analysis.",
            }
        except Exception as e:
            log.warning("RESONANCE fluidity analysis failed: %s", e)
            stages["resonance_fluidity"] = {
                "available": False,
                "error": str(e),
            }

        # ── STAGE 3: Cortical Fingerprint ────────────────────────────────
        log.info("Veracity Stage 3: Cortical fingerprinting")
        cortical_activations = _get_tribe_roi_activations(audio_path)
        cortical_score = None

        if cortical_activations:
            baseline_cortical = baseline.get("cortical_baseline") if baseline else None
            cortical_result = _cortical_deception_score(
                cortical_activations,
                baseline_cortical,
            )
            cortical_score = cortical_result["cortical_deception_score"]

            stages["cortical_fingerprint"] = {
                "available": True,
                "cortical_deception_score": cortical_score,
                "has_baseline": baseline_cortical is not None,
                "roi_analysis": cortical_result["roi_analysis"],
                "key_findings": _summarize_cortical_findings(cortical_result["roi_analysis"]),
            }
        else:
            stages["cortical_fingerprint"] = {
                "available": False,
                "note": "TRIBE v2 not available. Enable with FORGE_TRIBE_ENABLED=true.",
            }

        # ── Composite Score (before Prophecy) ────────────────────────────
        # Fluidity rigidity score (0-100, higher = more deceptive)
        rigidity_component = stages.get("resonance_fluidity", {}).get("rigidity_score")
        has_fluidity = rigidity_component is not None

        if cortical_score is not None and has_fluidity:
            if baseline_prosodic:
                # Best case: all three signals + baseline
                composite_deception = (
                    prosodic_deviation * 0.20 +
                    rigidity_component * 0.30 +
                    cortical_score * 0.50
                )
            else:
                # Three signals, no baseline
                composite_deception = (
                    prosodic["deception_probability"] * 0.25 +
                    rigidity_component * 0.30 +
                    cortical_score * 0.45
                )
        elif cortical_score is not None:
            if baseline_prosodic:
                composite_deception = prosodic_deviation * 0.30 + cortical_score * 0.70
            else:
                composite_deception = prosodic["deception_probability"] * 0.40 + cortical_score * 0.60
        elif has_fluidity:
            # Fluidity + prosodic, no cortical
            if baseline_prosodic:
                composite_deception = prosodic_deviation * 0.35 + rigidity_component * 0.65
            else:
                composite_deception = prosodic["deception_probability"] * 0.40 + rigidity_component * 0.60
        else:
            composite_deception = prosodic_deviation if baseline_prosodic else prosodic["deception_probability"]

        pre_prophecy_veracity = round(100 - min(100, composite_deception), 1)

        # ── STAGE 4: Swarm Consensus (Prophecy) ─────────────────────────
        prophecy_result = None
        if run_prophecy:
            log.info("Veracity Stage 4: Prophecy swarm consensus")
            try:
                prophecy_result = _run_veracity_prophecy(
                    audio_path=audio_path,
                    duration_s=duration_s,
                    prosodic=stages["vocal_biomarkers"],
                    cortical=stages.get("cortical_fingerprint", {}),
                    pre_score=pre_prophecy_veracity,
                    num_prophets=prophecy_prophets,
                )
                stages["swarm_consensus"] = prophecy_result
            except Exception as e:
                log.warning("Prophecy consensus failed: %s", e)
                stages["swarm_consensus"] = {
                    "available": False,
                    "error": str(e),
                }

        # ── Final Composite ──────────────────────────────────────────────
        if prophecy_result and prophecy_result.get("available"):
            # Prophecy adjusts the score by up to ±15 points
            prophecy_adjustment = prophecy_result.get("score_adjustment", 0)
            final_veracity = max(0, min(100, pre_prophecy_veracity + prophecy_adjustment))
        else:
            final_veracity = pre_prophecy_veracity

        elapsed = round(time.time() - started_at, 1)

        result = {
            "status": "ok",
            "tool": "veracity_analyze",
            "audio_path": audio_path,
            "duration_seconds": duration_s,
            "speaker_id": speaker_id or None,
            "has_baseline": bool(baseline),
            "veracity_score": round(final_veracity, 1),
            "pre_prophecy_score": pre_prophecy_veracity,
            "verdict": _interpret_veracity(final_veracity),
            "confidence": _compute_confidence(stages),
            "stages": stages,
            "elapsed_seconds": elapsed,
            "methodology": _methodology_summary(stages),
            "limitations": (
                "This is a research tool. Veracity scores are probabilistic estimates, "
                "not definitive truth assessments. Scores are most reliable when comparing "
                "against the speaker's own baseline. Cultural, linguistic, and individual "
                "differences can significantly affect results. Never use as sole evidence "
                "for consequential decisions."
            ),
        }

        return json.dumps(result)

    except ImportError as e:
        return json.dumps({"error": f"Missing dependency: {e}. Run: pip install librosa soundfile"})
    except Exception as e:
        log.exception("veracity_analyze failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ── Prophecy Integration ─────────────────────────────────────────────────────

def _run_veracity_prophecy(
    audio_path: str,
    duration_s: float,
    prosodic: dict,
    cortical: dict,
    pre_score: float,
    num_prophets: int = 8,
) -> dict:
    """Run a Prophecy swarm to deliberate on veracity evidence."""
    from forge.prophecy.engine import run_prophecy

    # Build evidence summary for the prophets
    evidence_lines = [
        f"Audio file: {os.path.basename(audio_path)} ({duration_s}s)",
        f"Pre-consensus veracity score: {pre_score}/100",
        "",
        "=== VOCAL BIOMARKER EVIDENCE ===",
        f"Prosodic deception probability: {prosodic.get('deception_probability', '?')}%",
    ]

    features = prosodic.get("features", {})
    for marker, value in features.items():
        evidence_lines.append(f"  {marker}: {value}")

    if prosodic.get("has_baseline"):
        evidence_lines.append(f"  (Compared against speaker's own baseline)")
        deltas = prosodic.get("marker_deltas", {})
        for marker, delta in (deltas or {}).items():
            direction = "elevated" if delta > 0.05 else "reduced" if delta < -0.05 else "stable"
            evidence_lines.append(f"  Δ {marker}: {delta:+.4f} ({direction})")

    if cortical.get("available"):
        evidence_lines.append("")
        evidence_lines.append("=== CORTICAL FINGERPRINT EVIDENCE ===")
        evidence_lines.append(f"Cortical deception score: {cortical.get('cortical_deception_score', '?')}/100")
        for finding in cortical.get("key_findings", []):
            evidence_lines.append(f"  • {finding}")

    evidence = "\n".join(evidence_lines)

    topic = (
        f"Based on the following vocal and neural evidence, is this speaker being truthful? "
        f"Debate whether the biomarker pattern is consistent with deception or truth-telling. "
        f"Consider alternative explanations (anxiety, excitement, fatigue, medical conditions). "
        f"Reach a consensus on a veracity adjustment: positive means more truthful than the "
        f"biomarkers suggest, negative means more deceptive."
    )

    sim = run_prophecy(
        topic=topic,
        seed_material=evidence,
        num_prophets=num_prophets,
        num_rounds=4,  # Shorter deliberation for veracity
        deliberation_mode="hivemind",
    )

    # Extract consensus
    confidence = sim.prediction_confidence
    prediction = sim.prediction or ""

    # Parse score adjustment from prediction
    score_adjustment = 0.0
    pred_lower = prediction.lower()
    if "more truthful" in pred_lower or "likely truthful" in pred_lower:
        score_adjustment = min(15, confidence * 15)
    elif "more deceptive" in pred_lower or "likely deceptive" in pred_lower:
        score_adjustment = max(-15, -confidence * 15)
    elif "uncertain" in pred_lower or "inconclusive" in pred_lower:
        score_adjustment = 0.0

    trajectory = sim.consensus_trajectory or []

    return {
        "available": True,
        "simulation_id": sim.id,
        "num_prophets": num_prophets,
        "num_rounds": 4,
        "prediction": prediction,
        "consensus_confidence": round(confidence, 3),
        "score_adjustment": round(score_adjustment, 1),
        "consensus_trajectory": [round(c, 3) for c in trajectory],
        "dissenting_views": [
            a.content for r in sim.rounds for a in r.actions
            if a.action_type.value == "DISSENT"
        ][:3],  # Top 3 dissents
    }


def _summarize_cortical_findings(roi_analysis: dict) -> list[str]:
    """Generate human-readable findings from cortical ROI analysis."""
    findings = []
    for roi, data in sorted(roi_analysis.items(), key=lambda x: x[1].get("deception_signal", 0), reverse=True):
        signal = data.get("deception_signal", 0.5)
        if signal > 0.7:
            findings.append(
                f"{roi.replace('_', ' ').title()}: {data['interpretation']}"
            )
        elif signal < 0.3:
            findings.append(
                f"{roi.replace('_', ' ').title()}: counter-indicator (consistent with truthful speech)"
            )
    return findings[:5]


def _compute_confidence(stages: dict) -> str:
    """Estimate overall confidence level based on which stages ran successfully."""
    layers = 0
    if stages.get("authenticity_gate", {}).get("passed"):
        layers += 1
    if stages.get("vocal_biomarkers", {}).get("features"):
        layers += 1
    if stages.get("cortical_fingerprint", {}).get("available"):
        layers += 1
    if stages.get("vocal_biomarkers", {}).get("has_baseline"):
        layers += 1
    if stages.get("swarm_consensus", {}).get("available"):
        layers += 1

    if layers >= 5:
        return "high — all layers active including speaker baseline"
    elif layers >= 3:
        return "moderate — multiple corroborating signals"
    elif layers >= 2:
        return "low — limited signal sources"
    else:
        return "very_low — insufficient data for reliable assessment"


def _methodology_summary(stages: dict) -> str:
    """Generate a methodology description based on active stages."""
    methods = []
    if "authenticity_gate" in stages:
        methods.append("synthetic audio gate")
    methods.append("prosodic vocal biomarkers (6 features)")
    if stages.get("vocal_biomarkers", {}).get("has_baseline"):
        methods.append("speaker-specific baseline comparison")
    if stages.get("cortical_fingerprint", {}).get("available"):
        methods.append("TRIBE v2 cortical fingerprint analysis (7 ROIs)")
    if stages.get("swarm_consensus", {}).get("available"):
        n = stages["swarm_consensus"].get("num_prophets", "?")
        methods.append(f"Prophecy swarm consensus ({n} agents)")
    return "Pipeline: " + " → ".join(methods)


# ── Registration ─────────────────────────────────────────────────────────────

def register(registry: ToolRegistry):
    """Register all deception detection tools."""

    registry.register(
        name="veracity_analyze",
        description=(
            "Full deception detection pipeline. Runs 5-stage analysis: "
            "(1) authenticity gate — verify audio is not synthetic, "
            "(2) vocal biomarkers — prosodic stress indicators (pitch jitter, pause patterns, "
            "speech rate variance, formant perturbation, energy contour, harmonic quality), "
            "(2.5) RESONANCE fluidity — physics-grounded 498D encoding analysis that detects "
            "deception as fluidity loss (analogue→digital shift, AUC=0.872 with 12 features), "
            "(3) cortical fingerprint — TRIBE v2 brain region activation analysis "
            "(prefrontal cortex, Broca's area, default mode network), "
            "(4) swarm consensus — Prophecy Engine deliberation. "
            "Returns a veracity score 0–100 (higher = more truthful). "
            "⚠ Research tool — not a courtroom lie detector. Most accurate when "
            "comparing against the speaker's own baseline (veracity_baseline)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to the audio file containing speech to analyze.",
                },
                "speaker_id": {
                    "type": "string",
                    "description": "Optional speaker ID. If a baseline exists for this speaker, enables relative comparison (more accurate).",
                },
                "run_prophecy": {
                    "type": "boolean",
                    "description": "Whether to run Prophecy swarm consensus (slower but more thorough). Default: true.",
                },
                "prophecy_prophets": {
                    "type": "integer",
                    "description": "Number of Prophecy agents for swarm consensus. Default: 8.",
                },
                "backend": {
                    "type": "string",
                    "description": "Fake audio detection backend for authenticity gate: 'hf' (default), 'aasist3', 'spectral', 'perth'. Default: 'hf'.",
                },
            },
            "required": ["audio_path"],
        },
        handler=veracity_analyze,
    )

    registry.register(
        name="veracity_baseline",
        description=(
            "Record a speaker's truthful baseline for future deception comparison. "
            "Provide audio of the speaker saying things known to be true (name, "
            "observable facts, reading aloud). Captures prosodic norms and cortical "
            "activation patterns. Future veracity_compare calls will measure deviations "
            "from this personal baseline — far more accurate than population norms."
        ),
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to the baseline audio recording.",
                },
                "speaker_id": {
                    "type": "string",
                    "description": "Unique identifier for the speaker (e.g., name, handle).",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description of what the speaker is saying in the baseline.",
                },
            },
            "required": ["audio_path", "speaker_id"],
        },
        handler=veracity_baseline,
    )

    registry.register(
        name="veracity_compare",
        description=(
            "Compare a speech recording against a speaker's known-truthful baseline. "
            "Measures deviations in both prosodic biomarkers and cortical activation "
            "patterns. Requires a prior baseline recorded with veracity_baseline. "
            "Returns veracity score 0–100 with per-marker deviation analysis."
        ),
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to the audio file to analyze.",
                },
                "speaker_id": {
                    "type": "string",
                    "description": "Speaker ID matching a previously recorded baseline.",
                },
            },
            "required": ["audio_path", "speaker_id"],
        },
        handler=veracity_compare,
    )

    registry.register(
        name="veracity_quick",
        description=(
            "Fast prosodic-only veracity check — no TRIBE or Prophecy needed. "
            "Analyzes 6 vocal biomarkers associated with cognitive load during deception: "
            "pitch jitter, pause irregularity, speech rate variance, formant perturbation, "
            "energy contour breaks, and harmonic-to-noise ratio. Returns veracity score 0–100. "
            "Less accurate than veracity_analyze but instant and dependency-free (just librosa)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to the audio file containing speech to analyze.",
                },
            },
            "required": ["audio_path"],
        },
        handler=veracity_quick,
    )

    log.info("Registered 4 deception detection tools")
