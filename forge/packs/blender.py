"""Blender capability pack — drive Blender via blender-mcp.

Pairs Blender tools with filesystem + image + search so an agent can:
- Inspect scene state → reason about it
- Execute Python to build/modify geometry
- Download Polyhaven/Sketchfab assets
- Generate 3D models via Hyper3D Rodin
- Screenshot viewport for vision-model feedback loops
"""
from forge.packs import CapabilityPack, PackBudget


BLENDER_PACK = CapabilityPack(
    name="blender",
    description=(
        "3D modeling, scene manipulation, and asset pipeline in Blender via "
        "blender-mcp. Supports Polyhaven, Sketchfab, and Hyper3D Rodin. "
        "Requires Blender running with the blender-mcp addon enabled and "
        "connected (3D View sidebar → 'Connect to Claude')."
    ),
    tools=["blender", "filesystem", "image", "search"],
    # Vision-capable default — viewport screenshots are a core feedback loop.
    default_model="claude-sonnet-4-6",
    fallback_models=[
        "grok-4.20-0309-vision",
        "gpt-4o",
        "lm-studio/qwen2.5-vl-32b-instruct",
    ],
    guardrail_profile="standard",
    budget=PackBudget(max_cost_usd=2.0, max_steps=15, max_iterations_per_step=12),
    ui_panels=["output", "viewport_preview"],
    env_required=[],  # blender addon is configured in-app, not via env
    env_optional=[
        "BLENDER_PORT",               # Blender addon socket (default 9876)
        "FORGE_HYPER3D_API_KEY",      # optional — only if using Hyper3D Rodin gen
    ],
    deps_required=["mcp"],            # MCP client SDK
    feature_flag="BLENDER_PACK_ENABLED",
)
