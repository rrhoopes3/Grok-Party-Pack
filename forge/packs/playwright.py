"""Playwright capability pack — drive a real Chromium via @playwright/mcp.

Pairs browser automation with filesystem + image + search so an agent can:
- Navigate to any page, read its accessibility tree (cheap, text-only)
- Click / fill / type to operate web apps like a user
- Screenshot for vision-model feedback loops
- Evaluate JS to scrape or read runtime state
- Scrape structured data and save to disk

Default model is vision-capable because screenshot feedback is a core loop.
"""
from forge.packs import CapabilityPack, PackBudget


PLAYWRIGHT_PACK = CapabilityPack(
    name="playwright",
    description=(
        "Browser automation via Microsoft's @playwright/mcp. Drives a real "
        "Chromium: navigate, click, fill, snapshot, screenshot, evaluate. "
        "First run pulls Chromium (~150MB) via npx. Requires Node + npm on "
        "PATH. Headless by default; set headed mode via env if you want to "
        "watch the browser work."
    ),
    tools=["playwright", "filesystem", "image", "search"],
    # Vision-capable default — screenshots are a core feedback loop.
    default_model="claude-sonnet-4-6",
    fallback_models=[
        "grok-4.20-0309-vision",
        "gpt-4o",
        "lm-studio/qwen2.5-vl-32b-instruct",
    ],
    guardrail_profile="standard",
    budget=PackBudget(max_cost_usd=1.0, max_steps=20, max_iterations_per_step=10),
    ui_panels=["output"],
    env_required=[],  # npx downloads Chromium on first run
    env_optional=[
        "FORGE_MCP_SERVER_PLAYWRIGHT_ENABLED",
        "PLAYWRIGHT_HEADLESS",       # "false" to run headed (watch browser)
        "PLAYWRIGHT_BROWSERS_PATH",  # override Chromium cache location
    ],
    deps_required=["mcp"],            # MCP client SDK
    feature_flag="PLAYWRIGHT_PACK_ENABLED",
)
