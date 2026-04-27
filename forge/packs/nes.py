"""NES Arena capability pack — Grok as coach/player for classic NES games.

The emulator itself (jsnes) runs in the user's browser. This pack equips
an agent to:
  * Enumerate the ROM library the Forge indexed
  * Coach an active game — strategic plans from a vision-capable model
  * Log significant events (deaths, level changes) to the Vault so future
    Grok runs on the same ROM can recall past mistakes

No launching a ROM from the agent side — that's a user-initiated action
in the browser for now. The pack is inspect + coach only.

Default model is vision-capable so the coach can read the actual screen.
"""
from forge.packs import CapabilityPack, PackBudget


NES_PACK = CapabilityPack(
    name="nes",
    description=(
        "Coach model for live NES emulator sessions. jsnes runs in the user's "
        "browser; this pack lets Grok see the current frame + game state, "
        "propose strategic plans, and log deaths / powerups / level changes "
        "to forge:vault so future runs on the same ROM remember what worked."
    ),
    tools=["nes", "filesystem", "image"],
    # Vision-capable default — the coach sees the actual game screen.
    default_model="claude-sonnet-4-6",
    fallback_models=[
        "grok-4.20-0309-vision",
        "gpt-4o",
        "grok-4.20-0309-reasoning",  # text-only fallback — reads state summary
    ],
    guardrail_profile="lenient",
    budget=PackBudget(max_cost_usd=0.50, max_steps=15, max_iterations_per_step=8),
    ui_panels=["output"],
    env_required=[],
    env_optional=[
        "FORGE_NES_PACK_ENABLED",
        "FORGE_NES_ROMS_DIR",
        "FORGE_NES_COACH_MODEL",
        "FORGE_NES_CONTROLLER_MODEL",
        "FORGE_NES_COACH_INTERVAL_MS",
    ],
    deps_required=[],
    feature_flag="NES_PACK_ENABLED",
)
