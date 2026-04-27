"""Music generation tool — ACE-Step local inference via HTTP.

ACE-Step is a 3.5B diffusion text-to-music model that runs on a consumer
GPU (~8GB VRAM). It exposes a FastAPI `/generate` endpoint via infer-api.py
— user launches that separately, Forge just calls it.

Setup:
  cd "B:/AI Tunes/ACE-Step"
  venv/Scripts/python.exe infer-api.py    # FastAPI on :7865
  # or the Gradio UI:
  venv/Scripts/acestep.exe --port 7865 --bf16 true

Tools:
  music_generate   — text+lyrics → .wav file on disk
  music_status     — health-check the ACE-Step server without generating
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

from .registry import ToolRegistry
from forge.config import ACESTEP_BASE_URL, ACESTEP_CHECKPOINT


_DEFAULT_TIMEOUT = 600.0  # generation can take 20s–5m depending on duration


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    """POST JSON; return dict response or {"error": ...}."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
    except urllib.error.URLError as e:
        return {
            "error": (
                f"Could not reach ACE-Step at {url}: {e.reason}. "
                f"Launch it first: cd 'B:/AI Tunes/ACE-Step' && "
                f"venv/Scripts/python.exe infer-api.py"
            )
        }
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": f"Non-JSON response from ACE-Step: {raw[:200]}"}


def music_generate(
    prompt: str,
    lyrics: str = "",
    audio_duration: float = 30.0,
    infer_step: int = 60,
    guidance_scale: float = 15.0,
    seed: int = 42,
    output_path: str = "",
) -> str:
    """Generate music via ACE-Step.

    Args:
      prompt:         Genre/style tags, e.g. "synthwave, retro, upbeat, 120bpm"
      lyrics:         Optional lyrics; pass empty string for instrumental.
      audio_duration: Length in seconds (8–240 typical).
      infer_step:     Diffusion steps (30–100; higher = better quality, slower).
      guidance_scale: CFG scale (7.5–20 typical).
      seed:           Random seed for reproducibility.
      output_path:    Where to save the .wav; empty = ACE-Step picks.

    Returns:
      JSON string {status, output_path, message}.
    """
    if not prompt or not prompt.strip():
        return json.dumps({"error": "prompt cannot be empty"})

    payload = {
        "checkpoint_path": ACESTEP_CHECKPOINT,
        "bf16": True,
        "torch_compile": False,
        "device_id": 0,
        "output_path": output_path or None,
        "audio_duration": float(audio_duration),
        "prompt": prompt,
        "lyrics": lyrics,
        "infer_step": int(infer_step),
        "guidance_scale": float(guidance_scale),
        "scheduler_type": "euler",
        "cfg_type": "apg",
        "omega_scale": 10.0,
        "actual_seeds": [int(seed)],
        "guidance_interval": 0.5,
        "guidance_interval_decay": 0.0,
        "min_guidance_scale": 3.0,
        "use_erg_tag": True,
        "use_erg_lyric": False,
        "use_erg_diffusion": True,
        "oss_steps": [],
        "guidance_scale_text": 0.0,
        "guidance_scale_lyric": 0.0,
    }
    url = ACESTEP_BASE_URL.rstrip("/") + "/generate"
    result = _post_json(url, payload, timeout=_DEFAULT_TIMEOUT)
    return json.dumps(result)


def music_status() -> str:
    """Check whether the ACE-Step server is reachable. Does NOT generate."""
    url = ACESTEP_BASE_URL.rstrip("/") + "/docs"  # FastAPI /docs always served
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.dumps({
                "reachable": True,
                "base_url": ACESTEP_BASE_URL,
                "status_code": resp.status,
            })
    except urllib.error.URLError as e:
        return json.dumps({
            "reachable": False,
            "base_url": ACESTEP_BASE_URL,
            "error": str(e.reason),
            "hint": (
                "Launch ACE-Step: cd 'B:/AI Tunes/ACE-Step' && "
                "venv/Scripts/python.exe infer-api.py"
            ),
        })


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="music_generate",
        description=(
            "Generate music via ACE-Step (local 3.5B diffusion model). Takes "
            "a style/genre prompt and optional lyrics, returns the path to a "
            ".wav file. Requires the ACE-Step server running (see music_status). "
            "Generation takes 20s for a 30s clip on an A100; several minutes on "
            "a consumer GPU."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Genre/style/mood tags, e.g. 'synthwave, retro, upbeat, 120bpm'",
                },
                "lyrics": {
                    "type": "string",
                    "description": "Optional lyrics. Empty string = instrumental.",
                },
                "audio_duration": {
                    "type": "number",
                    "description": "Length in seconds (8–240 typical)",
                },
                "infer_step": {
                    "type": "integer",
                    "description": "Diffusion steps (30–100). More = better quality, slower.",
                },
                "guidance_scale": {
                    "type": "number",
                    "description": "CFG scale (7.5–20 typical)",
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed for reproducibility",
                },
                "output_path": {
                    "type": "string",
                    "description": "Where to save the .wav. Empty = ACE-Step picks.",
                },
            },
            "required": ["prompt"],
        },
        handler=music_generate,
    )
    registry.register(
        name="music_status",
        description=(
            "Check whether the ACE-Step music server is running and reachable. "
            "Call this first before music_generate if you're not sure it's up."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=music_status,
    )
