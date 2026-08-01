"""
Priced multi-provider model registry for Fleet Mode (spec §5).

Loads defaults from ``forge.config.EXECUTOR_MODELS`` so the fleet stays in
sync with the rest of The Forge, then optionally merges project overrides
from TOML (``fleet.toml`` / ``models.toml``) or JSON.

Override files must be trusted project config (not untrusted task payload).
Costs are clamped to finite non-negative values.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from forge.config import EXECUTOR_MODELS
from forge.fleet.types import ModelEntry, RoutingTable

log = logging.getLogger("forge.fleet.registry")

# Default auto-routing table aligned with the fleet spec illustration.
DEFAULT_ROUTING = RoutingTable(
    plan="grok-4.5",
    implement="claude-sonnet-5",
    mechanical="gpt-5.4-mini",
    verify="grok-4.5",
    fallbacks={
        "anthropic": ["xai", "openai"],
        "xai": ["anthropic", "openai"],
        "openai": ["xai", "anthropic"],
    },
)

# Conservative floor when a model is unknown and a budget is enforced.
UNKNOWN_MODEL_FLOOR_USD = 0.05


def normalize_provider(provider: str) -> str:
    """Normalize provider labels to a stable lowercase key."""
    p = (provider or "").strip().lower()
    aliases = {
        "xai": "xai",
        "x.ai": "xai",
        "anthropic": "anthropic",
        "openai": "openai",
        "local": "local",
        "openai-compat": "openai-compat",
        "lmstudio": "local",
        "ollama": "local",
        "auto": "auto",
    }
    return aliases.get(p, p or "unknown")


def clamp_nonneg_finite(value: float, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v) or v < 0:
        return default
    return v


def clamp_tokens(n: int | float | None) -> int:
    try:
        v = int(n or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, v)


def infer_tier(model_id: str, provider: str, cost_in: float, cost_out: float) -> str:
    """Best-effort tier when the registry entry omits one."""
    prov = normalize_provider(provider)
    if prov in ("local", "openai-compat") or model_id.startswith(("lmstudio:", "ollama:")):
        return "local"
    if model_id == "auto" or prov == "auto":
        return "auto"
    mid = model_id.lower()
    if any(x in mid for x in ("opus", "fable", "pro", "grok-4.5", "gpt-5.5", "gpt-5.6-sol")):
        return "frontier"
    if cost_out >= 10.0 or cost_in >= 5.0:
        return "frontier"
    return "fast"


def _entry_from_executor_info(model_id: str, info: dict[str, Any]) -> ModelEntry:
    provider = str(info.get("provider", "unknown"))
    cost_in = clamp_nonneg_finite(info.get("cost_in", 0) or 0)
    cost_out = clamp_nonneg_finite(info.get("cost_out", 0) or 0)
    tier = str(info.get("tier") or infer_tier(model_id, provider, cost_in, cost_out))
    st = info.get("supports_temperature")
    sr = info.get("supports_reasoning")
    return ModelEntry(
        id=model_id,
        provider=provider,
        label=str(info.get("label") or model_id),
        cost_in=cost_in,
        cost_out=cost_out,
        tier=tier,
        base_url=info.get("base_url"),
        supports_tools=bool(info.get("supports_tools", True)),
        supports_temperature=None if st is None else bool(st),
        supports_reasoning=None if sr is None else bool(sr),
    )


def _load_toml(path: Path) -> dict[str, Any]:
    """Load TOML via stdlib tomllib (3.11+) or optional tomli."""
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                f"Cannot load TOML file {path}: need Python 3.11+ (tomllib) "
                "or the 'tomli' package. Use a .json override instead."
            ) from exc
    with path.open("rb") as f:
        return tomllib.load(f)


def _load_override_file(path: Path) -> dict[str, Any]:
    """Load override file. Path must be trusted project config, not task input."""
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Model registry override not found: {path}")
    if not path.is_file():
        raise ValueError(f"Model registry override is not a file: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if suffix in (".toml", ".tml"):
        return _load_toml(path)
    try:
        return _load_toml(path)
    except Exception:
        return json.loads(path.read_text(encoding="utf-8"))


class ModelRegistry:
    """In-memory priced model registry with optional project overrides."""

    def __init__(
        self,
        models: dict[str, ModelEntry] | None = None,
        routing: RoutingTable | None = None,
    ):
        self._models: dict[str, ModelEntry] = dict(models or {})
        self.routing: RoutingTable = routing or RoutingTable(
            plan=DEFAULT_ROUTING.plan,
            implement=DEFAULT_ROUTING.implement,
            mechanical=DEFAULT_ROUTING.mechanical,
            verify=DEFAULT_ROUTING.verify,
            fallbacks=dict(DEFAULT_ROUTING.fallbacks),
        )

    @classmethod
    def from_executor_models(
        cls,
        executor_models: dict[str, dict] | None = None,
        override_path: str | Path | None = None,
    ) -> ModelRegistry:
        """Build registry from EXECUTOR_MODELS (+ optional project file)."""
        raw = executor_models if executor_models is not None else EXECUTOR_MODELS
        models: dict[str, ModelEntry] = {}
        for mid, info in raw.items():
            models[mid] = _entry_from_executor_info(mid, info or {})

        reg = cls(models=models, routing=None)
        reg.routing = RoutingTable(
            plan=DEFAULT_ROUTING.plan,
            implement=DEFAULT_ROUTING.implement,
            mechanical=DEFAULT_ROUTING.mechanical,
            verify=DEFAULT_ROUTING.verify,
            fallbacks=dict(DEFAULT_ROUTING.fallbacks),
        )
        if override_path:
            reg.apply_override_file(override_path)
        return reg

    def apply_override_file(self, path: str | Path) -> None:
        """Merge models + routing from a project fleet/models file."""
        data = _load_override_file(Path(path))
        self._apply_override_dict(data)

    def _apply_override_dict(self, data: dict[str, Any]) -> None:
        models_block = data.get("models") or {}
        if isinstance(models_block, dict):
            for mid, info in models_block.items():
                if not isinstance(info, dict):
                    continue
                existing = self._models.get(mid)
                provider = str(
                    info.get("provider")
                    or (existing.provider if existing else "unknown")
                )
                cost_in = clamp_nonneg_finite(
                    info.get("cost_in", existing.cost_in if existing else 0) or 0
                )
                cost_out = clamp_nonneg_finite(
                    info.get("cost_out", existing.cost_out if existing else 0) or 0
                )
                label = str(
                    info.get("label") or (existing.label if existing else mid)
                )
                tier = str(
                    info.get("tier")
                    or (existing.tier if existing else None)
                    or infer_tier(mid, provider, cost_in, cost_out)
                )
                base_url = info.get("base_url")
                if base_url is None and existing:
                    base_url = existing.base_url
                supports_tools = bool(
                    info.get(
                        "supports_tools",
                        existing.supports_tools if existing else True,
                    )
                )
                st = info.get("supports_temperature")
                if st is None and existing:
                    st = existing.supports_temperature
                sr = info.get("supports_reasoning")
                if sr is None and existing:
                    sr = existing.supports_reasoning
                self._models[mid] = ModelEntry(
                    id=mid,
                    provider=provider,
                    label=label,
                    cost_in=cost_in,
                    cost_out=cost_out,
                    tier=tier,
                    base_url=base_url,
                    supports_tools=supports_tools,
                    supports_temperature=None if st is None else bool(st),
                    supports_reasoning=None if sr is None else bool(sr),
                )

        routing_block = data.get("routing") or {}
        auto = routing_block.get("auto") if isinstance(routing_block, dict) else None
        if isinstance(auto, dict):
            for key in ("plan", "implement", "mechanical", "verify"):
                if key in auto and auto[key]:
                    setattr(self.routing, key, str(auto[key]))
            fb = auto.get("fallbacks")
            if isinstance(fb, dict):
                merged: dict[str, list[str]] = dict(self.routing.fallbacks)
                for prov, chain in fb.items():
                    key = normalize_provider(str(prov))
                    if isinstance(chain, str):
                        merged[key] = [normalize_provider(chain)]
                    elif isinstance(chain, list):
                        merged[key] = [normalize_provider(str(c)) for c in chain]
                self.routing.fallbacks = merged

    def get(self, model_id: str) -> ModelEntry | None:
        return self._models.get(model_id)

    def require(self, model_id: str) -> ModelEntry:
        entry = self.get(model_id)
        if entry is None:
            raise KeyError(f"Unknown model in fleet registry: {model_id!r}")
        return entry

    def list_models(self) -> list[ModelEntry]:
        return list(self._models.values())

    def model_ids(self) -> list[str]:
        return list(self._models.keys())

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def __len__(self) -> int:
        return len(self._models)

    def calculate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        *,
        unknown_floor: float | None = None,
    ) -> float:
        """USD cost for a token pair.

        Tokens are clamped to >= 0. Unknown models return 0.0 unless
        ``unknown_floor`` is set (used when a budget is active).
        """
        in_t = clamp_tokens(input_tokens)
        out_t = clamp_tokens(output_tokens)
        entry = self.get(model_id)
        if entry is None:
            log.warning("fleet registry: unknown model %r — cannot price", model_id)
            if unknown_floor is not None:
                return clamp_nonneg_finite(unknown_floor)
            return 0.0
        cost = (in_t * entry.cost_in + out_t * entry.cost_out) / 1_000_000.0
        return clamp_nonneg_finite(cost)

    def _tier_reserve_amount(
        self,
        model_id: str,
        *,
        unknown_floor: float,
        missing_usage_floor: float,
    ) -> float:
        """Tier-based pessimistic reserve (no budget/estimate caps)."""
        miss = clamp_nonneg_finite(missing_usage_floor)
        entry = self.get(model_id)
        if entry is None:
            floor = max(clamp_nonneg_finite(unknown_floor) * 10.0, 0.50)
            log.warning(
                "fleet registry: estimating unknown model %r at reserve $%.4f",
                model_id,
                floor,
            )
            return floor
        tier = (entry.tier or "fast").lower()
        if tier == "local":
            return miss
        if tier == "frontier":
            priced = self.calculate_cost(model_id, 500_000, 250_000)
        else:
            priced = self.calculate_cost(model_id, 200_000, 100_000)
        return max(priced, miss)

    def estimate_step_floor(
        self,
        model_id: str,
        *,
        unknown_floor: float = UNKNOWN_MODEL_FLOOR_USD,
        missing_usage_floor: float = 0.01,
        estimated_cost_usd: float | None = None,
        budget: float | None = None,
        remaining_budget: float | None = None,
    ) -> float:
        """Pessimistic pre-dispatch estimate for budget reservation.

        - Tier-based synthetic load is the baseline floor.
        - ``estimated_cost_usd`` may *raise* the reserve (operator upper bound)
          but when a budget is active it **cannot undercut** the tier floor
          (trust boundary: step estimates are not a license to under-reserve).
        - Cap at ``remaining_budget`` (preferred) or ``budget`` so multi-step
          fleets are not starved by reserving the entire original budget.
        """
        miss = clamp_nonneg_finite(missing_usage_floor)
        tier_amt = self._tier_reserve_amount(
            model_id,
            unknown_floor=unknown_floor,
            missing_usage_floor=missing_usage_floor,
        )

        if estimated_cost_usd is not None:
            est = max(clamp_nonneg_finite(estimated_cost_usd), miss)
            if budget is not None or remaining_budget is not None:
                # Under budget: refuse underestimates below tier floor
                amt = max(est, tier_amt)
            else:
                # No budget: estimated is advisory upper bound
                amt = est
        else:
            amt = tier_amt

        # Cap at remaining headroom (not full original budget)
        cap = remaining_budget if remaining_budget is not None else budget
        if cap is not None and cap > 0:
            return min(amt, clamp_nonneg_finite(cap))
        if cap is not None and cap <= 0:
            return 0.0
        return amt

    def provider_of(self, model_id: str) -> str:
        """Return provider label; heuristic keys are lowercase-normalized names."""
        entry = self.get(model_id)
        if entry is None:
            if model_id.startswith("claude-"):
                return "anthropic"
            if model_id.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
                return "openai"
            if model_id.startswith(("lmstudio:", "ollama:")):
                return "local"
            return "xai"
        return entry.provider

    def models_for_provider(self, provider: str) -> list[ModelEntry]:
        key = normalize_provider(provider)
        return [
            m
            for m in self._models.values()
            if normalize_provider(m.provider) == key and m.id != "auto"
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "models": {m.id: m.to_dict() for m in self._models.values()},
            "routing": {"auto": self.routing.to_dict()},
        }
