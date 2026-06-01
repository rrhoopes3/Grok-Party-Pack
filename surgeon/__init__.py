"""Surgeon — Standalone model surgery toolkit (extracted from Grok Party Pack).

Wraps OBLITERATUS abliteration for probing and surgically editing LLM refusal
behavior. This is the heavy, high-VRAM, research-grade piece that was previously
buried inside The Forge.

See engine.py for the main API and README.md for setup instructions.
"""

from surgeon.engine import (
    AVAILABLE_METHODS,
    ANALYSIS_MODULES,
    check_dependencies,
    operate,
    scan_model,
    run_analysis,
    compare_models,
    list_operations,
    load_operation,
)
from surgeon.types import (
    OperationRecord,
    OperationStatus,
    ModelInfo,
    QualityMetrics,
    ScanResult,
    AnalysisResult,
)

__all__ = [
    # Operations
    "operate",
    "scan_model",
    "run_analysis",
    "compare_models",
    "check_dependencies",
    # Management
    "list_operations",
    "load_operation",
    # Data
    "AVAILABLE_METHODS",
    "ANALYSIS_MODULES",
    # Types
    "OperationRecord",
    "OperationStatus",
    "ModelInfo",
    "QualityMetrics",
    "ScanResult",
    "AnalysisResult",
]
