"""Music capability pack — local text-to-music via ACE-Step.

Chains music_generate with filesystem so an agent can:
- Produce .wav files from natural-language style prompts
- Save, rename, and organize generated audio
- Iterate on prompts based on critique

Zero external API cost — ACE-Step runs on the user's GPU. Latency is the
tradeoff: 20s–several minutes per clip depending on duration + hardware.
Requires the ACE-Step server launched separately (see music_status).
"""
from forge.packs import CapabilityPack, PackBudget


MUSIC_PACK = CapabilityPack(
    name="music",
    description=(
        "Local text-to-music generation via ACE-Step (3.5B diffusion model). "
        "Takes style/genre prompts + optional lyrics, returns .wav files. "
        "Requires ACE-Step running locally — see music_status to verify. "
        "$0 per generation; latency scales with duration + GPU."
    ),
    tools=["music", "filesystem", "search"],
    # Text model is fine — prompts are short, feedback is textual (file path).
    default_model="lm-studio/qwen2.5-coder-32b-instruct",
    fallback_models=["claude-sonnet-4-6", "gpt-4o-mini", "grok-4.20-0309"],
    guardrail_profile="standard",
    budget=PackBudget(max_cost_usd=0.25, max_steps=10, max_iterations_per_step=8),
    ui_panels=["output"],
    env_required=[],
    env_optional=[
        "FORGE_ACESTEP_URL",          # default http://127.0.0.1:7865
        "FORGE_ACESTEP_CHECKPOINT",   # default "" → auto-download
    ],
    deps_required=[],  # urllib only — no extra Python deps
    feature_flag="MUSIC_PACK_ENABLED",
)
