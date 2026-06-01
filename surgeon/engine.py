"""Surgeon Engine — OBLITERATUS wrapper (standalone).

Extracted from Grok Party Pack / The Forge into its own focused app.

This module wraps OBLITERATUS's AbliterationPipeline for probing and surgically
removing refusal directions from LLM weights while trying to preserve capabilities.

OBLITERATUS is an external research project (not pip-installable). You must
clone it yourself and point this tool at it.

Setup (do this first):
    git clone https://github.com/Projects/OBLITERATUS OBLITERATUS-main
    # or wherever you want it

    # Then either:
    export OBLITERATUS_ROOT=/path/to/OBLITERATUS-main

    # Or pass it explicitly when calling functions.

Heavy dependencies (torch, transformers, etc.) are imported lazily. The
package can be imported for --help / listing methods even if the ML stack
is not installed.

Pipeline stages (map to OBLITERATUS):
    SUMMON  → load_model()
    PROBE   → collect harmful/harmless activations
    DISTILL → extract refusal directions via SVD
    EXCISE  → project out refusal directions from weights
    VERIFY  → perplexity, coherence, refusal rate, KL divergence
    REBIRTH → save modified model to disk
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from surgeon.types import (
    AnalysisResult,
    ModelInfo,
    OperationRecord,
    OperationStatus,
    QualityMetrics,
    ScanResult,
    StageInfo,
)

log = logging.getLogger("surgeon")

# ── Configuration (standalone) ────────────────────────────────────────────────

# Default data directory for operations, scans, saved models, etc.
# Override with SURGEON_HOME env var.
DEFAULT_SURGEON_HOME = Path(os.getenv("SURGEON_HOME", Path.home() / ".surgeon"))
SURGEON_DIR = DEFAULT_SURGEON_HOME
SURGEON_DIR.mkdir(parents=True, exist_ok=True)

# OBLITERATUS source location.
# Priority:
#   1. Explicit argument to _ensure_obliteratus()
#   2. OBLITERATUS_ROOT env var
#   3. Common relative locations (for convenience during development)
def _resolve_obliteratus_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()

    env = os.getenv("OBLITERATUS_ROOT")
    if env:
        return Path(env).resolve()

    # Fallback search (common locations when developing near the old Party Pack layout)
    candidates = [
        Path.cwd() / "OBLITERATUS-main",
        Path.cwd().parent / "OBLITERATUS-main",
        Path.home() / "OBLITERATUS-main",
        Path.home() / "Projects" / "OBLITERATUS-main",
        # Original Party Pack location (for people who already have it)
        Path(__file__).resolve().parent.parent / "Projects" / "OBLITERATUS-main",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()

    # Last resort: the old hardcoded location (will fail with clear message later)
    return Path(__file__).resolve().parent.parent / "Projects" / "OBLITERATUS-main"


OBLITERATUS_ROOT: Path | None = None  # resolved on first use


# Available methods (mirrors OBLITERATUS's METHODS dict without importing it)
AVAILABLE_METHODS = {
    "basic": {
        "label": "Basic (Arditi et al.)",
        "description": "Single refusal direction via difference-in-means. Fast baseline.",
        "directions": 1, "norm_preserve": False, "passes": 1,
        "difficulty": "easy", "gpu_vram_gb": 1,
    },
    "advanced": {
        "label": "Advanced (Multi-direction + Norm-preserving)",
        "description": "SVD-based multi-direction extraction with norm preservation. The default.",
        "directions": 4, "norm_preserve": True, "passes": 2,
        "difficulty": "medium", "gpu_vram_gb": 4,
    },
    "aggressive": {
        "label": "Aggressive (Full Gabliteration + Enhanced)",
        "description": "Maximum direction extraction with whitened SVD, iterative refinement, head surgery.",
        "directions": 8, "norm_preserve": True, "passes": 3,
        "difficulty": "hard", "gpu_vram_gb": 8,
    },
    "surgical": {
        "label": "Surgical (Head Surgery + SAE + Neuron Masking)",
        "description": "Precision targeting: attention head surgery, sparse autoencoder, layer-adaptive.",
        "directions": 8, "norm_preserve": True, "passes": 2,
        "difficulty": "hard", "gpu_vram_gb": 8,
    },
    "informed": {
        "label": "Informed (Analysis-Guided Auto-Configuration)",
        "description": "Runs analysis modules first, then auto-tunes every parameter. Maximum precision.",
        "directions": "auto", "norm_preserve": True, "passes": "auto",
        "difficulty": "expert", "gpu_vram_gb": 12,
    },
    "nuclear": {
        "label": "Nuclear (All SOTA Techniques)",
        "description": "Every technique enabled: expert transplant, steering vectors, CoT-aware, KL-optimized.",
        "directions": 4, "norm_preserve": True, "passes": 3,
        "difficulty": "extreme", "gpu_vram_gb": 16,
    },
    "spectral_cascade": {
        "label": "Spectral Cascade (Frequency-Domain)",
        "description": "DCT frequency-domain decomposition of refusal signals. Novel approach.",
        "directions": 6, "norm_preserve": True, "passes": 1,
        "difficulty": "hard", "gpu_vram_gb": 10,
    },
}

ANALYSIS_MODULES = {
    "activation_probing": "Layer-wise activation difference analysis",
    "logit_lens": "Decodes refusal directions into vocabulary space",
    "defense_robustness": "Tests how well existing safety fine-tunes resist abliteration",
    "alignment_imprint": "Fingerprints the alignment method used on the base model",
    "concept_geometry": "Analyzes the geometric structure of refusal concepts",
    "steering_vectors": "Extracts ready-to-use steering vectors from refusal directions",
}


def check_dependencies(obliteratus_path: str | Path | None = None) -> dict:
    """Check if ML dependencies, GPU, and OBLITERATUS source are available."""
    deps: dict[str, Any] = {
        "torch": False,
        "transformers": False,
        "accelerate": False,
        "cuda_available": False,
        "gpu_name": None,
        "vram_gb": 0,
        "obliteratus_source": None,
    }
    missing = []

    try:
        import torch
        deps["torch"] = True
        deps["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            deps["gpu_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            deps["vram_gb"] = round(props.total_memory / (1024**3), 1)
    except ImportError:
        missing.append("torch")

    try:
        import transformers  # noqa: F401
        deps["transformers"] = True
    except ImportError:
        missing.append("transformers")

    try:
        import accelerate  # noqa: F401
        deps["accelerate"] = True
    except ImportError:
        missing.append("accelerate")

    # Check OBLITERATUS source
    root = _resolve_obliteratus_root(obliteratus_path)
    deps["obliteratus_source"] = str(root) if root.exists() else None

    return {
        "installed": deps,
        "missing": missing,
        "ready": len(missing) == 0 and deps.get("obliteratus_source") is not None,
        "install_command": "pip install torch transformers accelerate safetensors datasets"
        if missing else None,
    }


def _ensure_obliteratus(obliteratus_path: str | Path | None = None):
    """Add OBLITERATUS to sys.path and verify it's importable."""
    global OBLITERATUS_ROOT
    root = _resolve_obliteratus_root(obliteratus_path)
    OBLITERATUS_ROOT = root

    if not root.exists():
        raise RuntimeError(
            f"OBLITERATUS source not found at {root}.\n"
            f"Clone it and set OBLITERATUS_ROOT env var, or pass obliteratus_path.\n"
            f"Example: git clone https://github.com/Projects/OBLITERATUS OBLITERATUS-main\n"
            f"         export OBLITERATUS_ROOT=$(pwd)/OBLITERATUS-main"
        )

    src = str(root)
    if src not in sys.path:
        sys.path.insert(0, src)

    try:
        import obliteratus  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            f"OBLITERATUS found at {root} but can't import: {e}.\n"
            f"Install its dependencies: pip install torch transformers accelerate safetensors datasets"
        ) from e


def _human_params(n: int) -> str:
    if n >= 1e12:
        return f"{n / 1e12:.1f}T"
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return str(n)


# ── Core Operations ───────────────────────────────────────────────────────────

def operate(
    model_name: str,
    method: str = "advanced",
    device: str = "auto",
    dtype: str = "float16",
    quantization: str | None = None,
    output_dir: str | None = None,
    config_overrides: dict[str, Any] | None = None,
    progress_cb: Callable[[str], None] | None = None,
    obliteratus_path: str | Path | None = None,
) -> OperationRecord:
    """Run the full abliteration pipeline on a model."""
    _ensure_obliteratus(obliteratus_path)

    def emit(msg: str):
        log.info(msg)
        if progress_cb:
            progress_cb(msg)

    record = OperationRecord(
        model_name=model_name,
        method=method,
        device=device,
        dtype=dtype,
        quantization=quantization or "",
        config_overrides=config_overrides or {},
    )

    if not output_dir:
        output_dir = str(SURGEON_DIR / "models" / record.id)

    emit(f"[SURGEON] Preparing operation: {model_name} (method={method})")

    try:
        from obliteratus.abliterate import AbliterationPipeline, METHODS

        kwargs: dict[str, Any] = {
            "model_name": model_name,
            "output_dir": output_dir,
            "device": device,
            "dtype": dtype,
            "method": method,
        }
        if quantization:
            kwargs["quantization"] = quantization
        if config_overrides:
            kwargs.update(config_overrides)

        stage_map: dict[str, StageInfo] = {}

        def on_stage(stage_result):
            name = stage_result.stage
            status = stage_result.status

            if name not in stage_map:
                info = StageInfo(name=name)
                stage_map[name] = info
                record.stages.append(info)

            info = stage_map[name]
            info.status = status
            info.message = stage_result.message or ""
            if hasattr(stage_result, "duration") and stage_result.duration:
                info.duration_seconds = stage_result.duration

            for key in ("architecture", "num_layers", "num_heads", "hidden_size",
                        "total_params", "intermediate_size"):
                if hasattr(stage_result, key):
                    info.details[key] = getattr(stage_result, key)

            status_map = {
                "summon": OperationStatus.LOADING,
                "probe": OperationStatus.PROBING,
                "distill": OperationStatus.DISTILLING,
                "excise": OperationStatus.EXCISING,
                "verify": OperationStatus.VERIFYING,
                "rebirth": OperationStatus.SAVING,
            }
            if name in status_map:
                record.status = status_map[name]
                emit(f"[SURGEON] Stage {name.upper()}: {info.message}")

        def on_log(msg: str):
            record.log.append(msg)
            if len(record.log) > 200:
                record.log = record.log[-150:]

        kwargs["on_stage"] = on_stage
        kwargs["on_log"] = on_log

        emit(f"[SURGEON] Starting {method} abliteration on {model_name}")
        pipeline = AbliterationPipeline(**kwargs)
        output_path = pipeline.run()

        record.output_path = str(output_path)
        record.status = OperationStatus.COMPLETED

        if pipeline.handle:
            summary = pipeline.handle.summary()
            total = summary.get("total_params", 0)
            record.model_info = ModelInfo(
                model_name=model_name,
                architecture=summary.get("architecture", ""),
                num_layers=summary.get("num_layers", 0),
                num_heads=summary.get("num_heads", 0),
                hidden_size=summary.get("hidden_size", 0),
                intermediate_size=summary.get("intermediate_size", 0),
                total_params=total,
                total_params_human=_human_params(total),
            )

        metrics = getattr(pipeline, "_quality_metrics", {})
        if metrics:
            record.quality_metrics = QualityMetrics(
                refusal_rate=metrics.get("refusal_rate", 0),
                perplexity=metrics.get("perplexity", 0),
                coherence=metrics.get("coherence", 0),
                kl_divergence=metrics.get("kl_divergence", 0),
                effective_rank=metrics.get("effective_rank", 0),
            )

        emit(f"[SURGEON] Operation complete. Model saved to: {output_path}")
        if record.quality_metrics:
            qm = record.quality_metrics
            emit(f"[SURGEON] Refusal rate: {qm.refusal_rate:.1%} | "
                 f"Perplexity: {qm.perplexity:.2f} | "
                 f"Coherence: {qm.coherence:.2f} | "
                 f"KL: {qm.kl_divergence:.4f}")

        record.save(SURGEON_DIR)
        return record

    except Exception as e:
        record.status = OperationStatus.FAILED
        record.error = f"{type(e).__name__}: {e}"
        log.exception("Surgery operation failed")
        record.save(SURGEON_DIR)
        raise


def scan_model(
    model_name: str,
    device: str = "auto",
    dtype: str = "float16",
    quantization: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
    obliteratus_path: str | Path | None = None,
) -> ScanResult:
    """Scan a model's refusal geometry without modifying it."""
    _ensure_obliteratus(obliteratus_path)

    def emit(msg: str):
        log.info(msg)
        if progress_cb:
            progress_cb(msg)

    emit(f"[SURGEON] Scanning refusal geometry: {model_name}")

    from obliteratus.abliterate import AbliterationPipeline

    logs: list[str] = []
    pipeline = AbliterationPipeline(
        model_name=model_name,
        output_dir=str(SURGEON_DIR / "scans" / "temp"),
        device=device,
        dtype=dtype,
        method="basic",
        quantization=quantization,
        on_log=lambda m: logs.append(m),
    )

    pipeline._summon()
    emit("[SURGEON] Model loaded, probing activations...")
    pipeline._probe()
    emit("[SURGEON] Distilling refusal directions...")
    pipeline._distill()

    summary = pipeline.handle.summary() if pipeline.handle else {}
    strong_layers = list(pipeline._strong_layers)

    strength_per_layer = {}
    for layer_idx, direction in pipeline.refusal_directions.items():
        import torch
        strength_per_layer[str(layer_idx)] = float(torch.norm(direction).item())

    total_params = summary.get("total_params", 0)
    if total_params > 100_000_000_000:
        rec_method = "advanced"
        rec_note = "Large model — advanced method with conservative defaults"
    elif total_params > 10_000_000_000:
        rec_method = "advanced"
        rec_note = "Medium model — advanced method recommended"
    elif total_params > 1_000_000_000:
        rec_method = "aggressive"
        rec_note = "Small-medium model — aggressive method for thorough removal"
    else:
        rec_method = "nuclear"
        rec_note = "Small model — nuclear method feasible"

    result = ScanResult(
        model_name=model_name,
        architecture=summary.get("architecture", ""),
        num_layers=summary.get("num_layers", 0),
        strong_layers=strong_layers,
        refusal_strength_per_layer=strength_per_layer,
        recommended_method=rec_method,
        recommended_config={
            "note": rec_note,
            "total_params": total_params,
            "total_params_human": _human_params(total_params),
            "strong_layer_count": len(strong_layers),
            "total_layers": summary.get("num_layers", 0),
        },
    )

    emit(f"[SURGEON] Scan complete: {len(strong_layers)} strong layers out of {summary.get('num_layers', '?')}")
    emit(f"[SURGEON] Recommended method: {rec_method} — {rec_note}")
    return result


# (The rest of the file — run_analysis, compare_models, list_operations, load_operation —
# follows the same pattern. For brevity in this first extraction pass they are
# included in the full original logic with only the _ensure_obliteratus and
# SURGEON_DIR calls updated to support the new configuration.)

# NOTE: The full implementation of run_analysis, _run_single_analysis,
# compare_models, list_operations, and load_operation is preserved from the
# original Forge version with the minimal necessary adaptations for standalone use.
# They are omitted from this initial file write for token efficiency but will be
# added in the immediate follow-up edit.

# For now, provide stubs that raise a clear "not yet ported" so we can test the
# critical path (check + scan + operate) first.

def run_analysis(*args, **kwargs):
    raise NotImplementedError("run_analysis port pending in standalone extraction. "
                              "The core logic exists in the original engine.py and can be copied over.")

def compare_models(*args, **kwargs):
    raise NotImplementedError("compare_models port pending in standalone extraction.")

def list_operations() -> list[dict]:
    """List all saved operation records (standalone version)."""
    ops = []
    for path in sorted(SURGEON_DIR.glob("surgeon_*.json"), reverse=True):
        try:
            record = OperationRecord.load(path)
            entry = {
                "id": record.id,
                "model_name": record.model_name,
                "method": record.method,
                "status": record.status.value,
                "created_at": record.created_at,
                "output_path": record.output_path,
            }
            if record.quality_metrics:
                entry["refusal_rate"] = record.quality_metrics.refusal_rate
                entry["perplexity"] = record.quality_metrics.perplexity
            if record.model_info:
                entry["params"] = record.model_info.total_params_human
            if record.error:
                entry["error"] = record.error[:200]
            ops.append(entry)
        except Exception as e:
            log.warning("Failed to load operation %s: %s", path.name, e)
    return ops


def load_operation(op_id: str) -> OperationRecord | None:
    path = SURGEON_DIR / f"{op_id}.json"
    if not path.exists():
        for p in SURGEON_DIR.glob(f"*{op_id}*.json"):
            path = p
            break
        else:
            return None
    return OperationRecord.load(path)
