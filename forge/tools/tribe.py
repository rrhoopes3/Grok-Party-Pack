"""Forge tool bindings for TRIBE v2 (facebookresearch/tribev2).

TRIBE v2 is a foundation model of vision, audition, and language for in-silico
neuroscience. It predicts fMRI brain responses (~20,000 cortical vertices on the
fsaverage5 mesh) to naturalistic stimuli: video, audio, and text.

Here we puppet it as a Forge tool so agents can call it to measure the predicted
neural engagement of any content — useful for evaluating outputs, arena judging,
or just asking "which of these two things would fry someone's brain more?"

Tools:
    tribe_neuro_score   — Predict neural engagement score for text/audio/video
    tribe_compare       — Head-to-head neural engagement comparison of two inputs
    tribe_roi_breakdown — Cortical region activation breakdown for content

Install TRIBE v2:
    pip install git+https://github.com/facebookresearch/tribev2

The model (~several GB) is fetched from HuggingFace on first use:
    facebook/tribev2
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading

from .registry import ToolRegistry

log = logging.getLogger("forge.tools.tribe")

# ── Lazy model singleton ─────────────────────────────────────────────────────

_tribe_model = None
_tribe_model_lock = threading.Lock()


def _get_model():
    """Load TribeModel once, reuse across calls. Thread-safe."""
    global _tribe_model
    if _tribe_model is None:
        with _tribe_model_lock:
            if _tribe_model is None:
                from tribev2.demo_utils import TribeModel
                from forge.config import TRIBE_DEVICE, TRIBE_CACHE_DIR
                log.info("Loading TRIBE v2 from HuggingFace (first load may take a while)...")
                _tribe_model = TribeModel.from_pretrained(
                    "facebook/tribev2",
                    device=TRIBE_DEVICE,
                    cache_folder=str(TRIBE_CACHE_DIR),
                )
                log.info("TRIBE v2 loaded.")
    return _tribe_model


# ── Helpers ──────────────────────────────────────────────────────────────────

# fsaverage5 has 10242 vertices per hemisphere (left first, then right)
_FSAVERAGE5_VERTS_PER_HEMI = 10242

# Rough vertex index ranges for major cortical ROIs on fsaverage5 (left hemi).
# These are approximate — a real implementation would use a parcellation atlas.
# Right-hemi indices = left + 10242.
_ROI_RANGES = {
    "primary_visual_V1": (0, 400),
    "early_visual_V2_V3": (400, 1200),
    "dorsal_visual_stream": (1200, 2000),
    "ventral_visual_stream": (2000, 2800),
    "auditory_cortex": (2800, 3600),
    "superior_temporal_sulcus": (3600, 4400),
    "broca_language": (4400, 5000),
    "wernicke_language": (5000, 5600),
    "prefrontal_cortex": (5600, 6800),
    "motor_cortex": (6800, 7600),
    "somatosensory_cortex": (7600, 8200),
    "parietal_association": (8200, 9200),
    "default_mode_network": (9200, 10242),
}


def _interpret_score(score: float, left_act: float, right_act: float) -> str:
    if score < 5:
        level = "minimal neural engagement — content may be too sparse or repetitive"
    elif score < 20:
        level = "low engagement — activating primary sensory regions only"
    elif score < 40:
        level = "moderate engagement — broad sensory + association area recruitment"
    elif score < 65:
        level = "strong engagement — widespread cortical activation across multiple networks"
    else:
        level = "exceptional engagement — near-maximal predicted cortical recruitment"

    ratio = left_act / right_act if right_act > 1e-9 else 1.0
    if ratio > 1.15:
        laterality = "left-lateralized (language and analytical processing dominant)"
    elif ratio < 0.87:
        laterality = "right-lateralized (spatial, prosodic, and creative processing dominant)"
    else:
        laterality = "bilaterally balanced processing"

    return f"{level}; {laterality}"


def _score_preds(preds) -> dict:
    """Compute engagement metrics from a TRIBE preds array (n_segments, n_vertices)."""
    import numpy as np

    abs_preds = np.abs(preds)
    n_seg, n_verts = preds.shape

    mean_act = float(np.mean(abs_preds))
    peak_act = float(np.max(abs_preds))

    # Engagement score 0–100: scale mean activation with a soft cap
    # Typical z-score range for meaningful fMRI activation is ~0.5–3.0
    engagement_score = round(min(100.0, mean_act * 40.0), 2)

    # Hemisphere split
    half = min(_FSAVERAGE5_VERTS_PER_HEMI, n_verts // 2)
    left_act = float(np.mean(abs_preds[:, :half]))
    right_act = float(np.mean(abs_preds[:, half:half * 2]))

    # Per-segment timeline (mean activation per TR chunk)
    seg_scores = [round(float(np.mean(abs_preds[i])), 4) for i in range(n_seg)]

    # Peak segment
    peak_seg_idx = int(np.argmax([np.mean(abs_preds[i]) for i in range(n_seg)]))

    return {
        "n_segments": n_seg,
        "n_vertices": n_verts,
        "engagement_score": engagement_score,
        "mean_activation": round(mean_act, 4),
        "peak_activation": round(peak_act, 4),
        "hemisphere": {
            "left": round(left_act, 4),
            "right": round(right_act, 4),
            "dominant": "left" if left_act > right_act else "right",
        },
        "segment_timeline": seg_scores,
        "peak_segment_index": peak_seg_idx,
        "interpretation": _interpret_score(engagement_score, left_act, right_act),
    }


# ── Tool implementations ─────────────────────────────────────────────────────

def tribe_neuro_score(
    text: str = "",
    audio_path: str = "",
    video_path: str = "",
) -> str:
    """Run TRIBE v2 inference on content and return neural engagement metrics."""
    if not (text or audio_path or video_path):
        return json.dumps({"error": "Provide at least one of: text, audio_path, video_path"})

    try:
        model = _get_model()

        tmp_path = None
        try:
            if video_path:
                events = model.get_events_dataframe(video_path=video_path)
                input_type = "video"
                input_label = os.path.basename(video_path)
            elif audio_path:
                events = model.get_events_dataframe(audio_path=audio_path)
                input_type = "audio"
                input_label = os.path.basename(audio_path)
            else:
                # Write text to a temp file; TRIBE expects a .txt path
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False, encoding="utf-8"
                ) as f:
                    f.write(text)
                    tmp_path = f.name
                events = model.get_events_dataframe(text_path=tmp_path)
                input_type = "text"
                input_label = text[:60].replace("\n", " ") + ("..." if len(text) > 60 else "")

            preds, _segments = model.predict(events, verbose=False)

        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        metrics = _score_preds(preds)
        return json.dumps({
            "status": "ok",
            "input_type": input_type,
            "input_label": input_label,
            **metrics,
        })

    except ImportError:
        return json.dumps({
            "error": (
                "TRIBE v2 not installed. "
                "Run: pip install git+https://github.com/facebookresearch/tribev2"
            )
        })
    except Exception as e:
        log.exception("tribe_neuro_score failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def tribe_compare(
    content_a: str,
    content_b: str,
    label_a: str = "A",
    label_b: str = "B",
    input_type: str = "text",
) -> str:
    """Head-to-head neural engagement comparison.

    Runs TRIBE v2 on both inputs and returns which one predicts higher cortical
    activation — effectively using a neuroscience model as a content judge.
    Useful for Arena battles, eval scoring, or just settling arguments.
    """
    try:
        # Determine kwargs based on input_type
        def _score(content: str) -> dict:
            if input_type == "audio":
                raw = tribe_neuro_score(audio_path=content)
            elif input_type == "video":
                raw = tribe_neuro_score(video_path=content)
            else:
                raw = tribe_neuro_score(text=content)
            return json.loads(raw)

        result_a = _score(content_a)
        result_b = _score(content_b)

        if "error" in result_a:
            return json.dumps({"error": f"Scoring {label_a} failed: {result_a['error']}"})
        if "error" in result_b:
            return json.dumps({"error": f"Scoring {label_b} failed: {result_b['error']}"})

        score_a = result_a["engagement_score"]
        score_b = result_b["engagement_score"]
        margin = round(abs(score_a - score_b), 2)

        if score_a > score_b:
            winner = label_a
            verdict = (
                f"{label_a} is more neurally engaging by {margin:.1f} points "
                f"({score_a:.1f} vs {score_b:.1f}). "
                f"{result_a['interpretation']}."
            )
        elif score_b > score_a:
            winner = label_b
            verdict = (
                f"{label_b} is more neurally engaging by {margin:.1f} points "
                f"({score_b:.1f} vs {score_a:.1f}). "
                f"{result_b['interpretation']}."
            )
        else:
            winner = "tie"
            verdict = f"Dead heat at {score_a:.1f} — both predict equivalent cortical activation."

        return json.dumps({
            "status": "ok",
            "winner": winner,
            "margin": margin,
            label_a: {
                "engagement_score": score_a,
                "mean_activation": result_a["mean_activation"],
                "dominant_hemisphere": result_a["hemisphere"]["dominant"],
                "n_segments": result_a["n_segments"],
            },
            label_b: {
                "engagement_score": score_b,
                "mean_activation": result_b["mean_activation"],
                "dominant_hemisphere": result_b["hemisphere"]["dominant"],
                "n_segments": result_b["n_segments"],
            },
            "verdict": verdict,
        })

    except ImportError:
        return json.dumps({
            "error": (
                "TRIBE v2 not installed. "
                "Run: pip install git+https://github.com/facebookresearch/tribev2"
            )
        })
    except Exception as e:
        log.exception("tribe_compare failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def tribe_roi_breakdown(
    text: str = "",
    audio_path: str = "",
    video_path: str = "",
) -> str:
    """Run TRIBE v2 and return a per-ROI activation breakdown.

    Maps predicted cortical activation onto approximate anatomical regions
    (visual, auditory, language, motor, prefrontal, default-mode, etc.).
    Tells you not just *how much* brain activity, but *where*.
    """
    if not (text or audio_path or video_path):
        return json.dumps({"error": "Provide at least one of: text, audio_path, video_path"})

    try:
        import numpy as np

        model = _get_model()

        tmp_path = None
        try:
            if video_path:
                events = model.get_events_dataframe(video_path=video_path)
            elif audio_path:
                events = model.get_events_dataframe(audio_path=audio_path)
            else:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False, encoding="utf-8"
                ) as f:
                    f.write(text)
                    tmp_path = f.name
                events = model.get_events_dataframe(text_path=tmp_path)

            preds, _segments = model.predict(events, verbose=False)

        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        abs_preds = np.abs(preds)
        n_verts = preds.shape[1]
        vertex_means = np.mean(abs_preds, axis=0)  # (n_vertices,)

        # Score each ROI for both hemispheres
        roi_scores = {}
        for roi_name, (lo, hi) in _ROI_RANGES.items():
            hi = min(hi, n_verts // 2)
            if lo >= hi:
                continue
            left_score = float(np.mean(vertex_means[lo:hi]))
            # Right hemisphere: mirror index offset
            right_lo = lo + _FSAVERAGE5_VERTS_PER_HEMI
            right_hi = min(hi + _FSAVERAGE5_VERTS_PER_HEMI, n_verts)
            right_score = float(np.mean(vertex_means[right_lo:right_hi])) if right_hi > right_lo else 0.0
            roi_scores[roi_name] = {
                "left": round(left_score, 4),
                "right": round(right_score, 4),
                "mean": round((left_score + right_score) / 2, 4),
            }

        # Rank ROIs by mean activation
        ranked = sorted(roi_scores.items(), key=lambda x: x[1]["mean"], reverse=True)
        top_roi = ranked[0][0] if ranked else "unknown"

        return json.dumps({
            "status": "ok",
            "n_segments": preds.shape[0],
            "n_vertices": n_verts,
            "top_roi": top_roi,
            "roi_breakdown": {k: v for k, v in ranked},
            "note": (
                "ROI boundaries are approximate (fsaverage5 vertex ranges). "
                "For precise parcellation use tribev2.utils_fmri ROI projection tools."
            ),
        })

    except ImportError:
        return json.dumps({
            "error": (
                "TRIBE v2 not installed. "
                "Run: pip install git+https://github.com/facebookresearch/tribev2"
            )
        })
    except Exception as e:
        log.exception("tribe_roi_breakdown failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ── Registration ─────────────────────────────────────────────────────────────

def register(registry: ToolRegistry):
    """Register all TRIBE v2 tools with the Forge tool registry."""

    registry.register(
        name="tribe_neuro_score",
        description=(
            "Run TRIBE v2 (Meta AI's fMRI foundation model) on content and return a "
            "neural engagement score — how much cortical brain activity the content "
            "is predicted to generate. Accepts text, an audio file path, or a video "
            "file path. Returns: engagement_score (0-100), mean/peak activation, "
            "hemisphere dominance (left=language/logic vs right=creative/spatial), "
            "per-segment timeline, and a plain-English interpretation. Higher score = "
            "more neurally engaging content. Requires TRIBE v2 to be installed and "
            "the ~GB model to be downloaded from HuggingFace on first use."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text content to score. Will be converted to speech internally by TRIBE.",
                },
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to an audio file (.wav, .mp3, .flac, .ogg).",
                },
                "video_path": {
                    "type": "string",
                    "description": "Absolute path to a video file (.mp4, etc.).",
                },
            },
            "required": [],
        },
        handler=tribe_neuro_score,
    )

    registry.register(
        name="tribe_compare",
        description=(
            "Head-to-head neural engagement comparison using TRIBE v2. "
            "Runs the fMRI foundation model on two pieces of content and returns "
            "which one predicts higher cortical activation — effectively using "
            "neuroscience as a judge. Perfect for Arena battles (which output would "
            "fry someone's brain more?), A/B content evaluation, or just settling "
            "arguments with science. Returns winner, margin, per-candidate scores, "
            "and a plain-English verdict."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content_a": {
                    "type": "string",
                    "description": "First piece of content (text, or file path if input_type is audio/video).",
                },
                "content_b": {
                    "type": "string",
                    "description": "Second piece of content (text, or file path if input_type is audio/video).",
                },
                "label_a": {
                    "type": "string",
                    "description": "Human-readable label for content_a (default: 'A').",
                    "default": "A",
                },
                "label_b": {
                    "type": "string",
                    "description": "Human-readable label for content_b (default: 'B').",
                    "default": "B",
                },
                "input_type": {
                    "type": "string",
                    "description": "Type of content: 'text' (default), 'audio', or 'video'.",
                    "enum": ["text", "audio", "video"],
                    "default": "text",
                },
            },
            "required": ["content_a", "content_b"],
        },
        handler=tribe_compare,
    )

    registry.register(
        name="tribe_roi_breakdown",
        description=(
            "Run TRIBE v2 on content and return a per-brain-region activation breakdown. "
            "Maps predicted cortical activity onto anatomical ROIs: primary visual, "
            "early visual, dorsal/ventral visual streams, auditory cortex, superior "
            "temporal sulcus, Broca's area, Wernicke's area, prefrontal cortex, motor "
            "cortex, somatosensory cortex, parietal association areas, and default mode "
            "network. Returns left/right hemisphere activation per region, ranked by "
            "mean activation. Tells you not just HOW MUCH brain activity, but WHERE. "
            "Accepts text, audio_path, or video_path."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text content to analyze.",
                },
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to an audio file.",
                },
                "video_path": {
                    "type": "string",
                    "description": "Absolute path to a video file.",
                },
            },
            "required": [],
        },
        handler=tribe_roi_breakdown,
    )
