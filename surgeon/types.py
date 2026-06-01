"""Surgeon — Data types for model surgery operations.

Standalone version extracted from Grok Party Pack / The Forge.
Tracks abliteration operations, scan results, and quality metrics.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class OperationStatus(str, Enum):
    PENDING = "pending"
    LOADING = "loading"        # SUMMON stage
    PROBING = "probing"        # PROBE stage
    DISTILLING = "distilling"  # DISTILL stage
    EXCISING = "excising"      # EXCISE stage
    VERIFYING = "verifying"    # VERIFY stage
    SAVING = "saving"          # REBIRTH stage
    ANALYZING = "analyzing"    # running analysis modules
    COMPLETED = "completed"
    FAILED = "failed"


class StageInfo(BaseModel):
    """Status of a single pipeline stage."""
    name: str
    status: str = "pending"  # pending | running | done | failed
    message: str = ""
    duration_seconds: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    """Model architecture metadata."""
    model_name: str
    architecture: str = ""
    num_layers: int = 0
    num_heads: int = 0
    hidden_size: int = 0
    intermediate_size: int = 0
    total_params: int = 0
    total_params_human: str = ""  # e.g., "7.24B"


class QualityMetrics(BaseModel):
    """Post-surgery quality measurements."""
    refusal_rate: float = 0.0       # % of harmful prompts still refused
    perplexity: float = 0.0         # language modeling quality
    coherence: float = 0.0          # generation coherence score
    kl_divergence: float = 0.0      # divergence from original model
    effective_rank: float = 0.0     # weight matrix effective rank


class ScanResult(BaseModel):
    """Result of scanning a model's refusal geometry (no modification)."""
    model_name: str
    architecture: str = ""
    num_layers: int = 0
    strong_layers: list[int] = Field(default_factory=list)
    refusal_strength_per_layer: dict[str, float] = Field(default_factory=dict)
    recommended_method: str = ""
    recommended_config: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    """Result from running analysis modules."""
    module_name: str
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class OperationRecord(BaseModel):
    """Full record of a surgery operation."""
    id: str = Field(default_factory=lambda: f"surgeon_{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    status: OperationStatus = OperationStatus.PENDING

    # Configuration
    model_name: str
    method: str = "advanced"
    device: str = "auto"
    dtype: str = "float16"
    quantization: str = ""
    config_overrides: dict[str, Any] = Field(default_factory=dict)

    # Progress
    stages: list[StageInfo] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)

    # Results
    model_info: ModelInfo | None = None
    quality_metrics: QualityMetrics | None = None
    output_path: str = ""
    analyses: list[AnalysisResult] = Field(default_factory=list)
    error: str = ""

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> OperationRecord:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)
