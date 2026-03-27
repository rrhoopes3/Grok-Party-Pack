"""
Image and audio generation tools — OpenAI DALL-E 3, TTS, and transcription.

Requires OPENAI_API_KEY for all operations.

Tools:
    generate_image      — Generate an image from a text prompt (DALL-E 3)
    generate_speech     — Text-to-speech audio generation (OpenAI TTS)
    transcribe_audio    — Speech-to-text transcription (OpenAI Whisper)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.request
import urllib.error

from .registry import ToolRegistry

log = logging.getLogger("forge.tools.image_gen")


def _get_openai_key() -> str:
    from forge.config import OPENAI_API_KEY
    return OPENAI_API_KEY


def _openai_request(path: str, body: dict, timeout: int = 60) -> dict:
    """Make a POST request to the OpenAI API."""
    url = f"https://api.openai.com/v1{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {_get_openai_key()}")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Tool Implementations ─────────────────────────────────────────────────

def generate_image(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "standard",
    style: str = "vivid",
    output_path: str = "",
) -> str:
    """Generate an image using DALL-E 3."""
    if not _get_openai_key():
        return json.dumps({"error": "OPENAI_API_KEY not configured"})

    try:
        response = _openai_request("/images/generations", {
            "model": "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "style": style,
            "response_format": "b64_json" if output_path else "url",
        }, timeout=120)

        image_data = response["data"][0]
        revised_prompt = image_data.get("revised_prompt", prompt)

        if output_path and "b64_json" in image_data:
            # Save to file
            img_bytes = base64.b64decode(image_data["b64_json"])
            if not output_path.lower().endswith(".png"):
                output_path += ".png"
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(img_bytes)
            return json.dumps({
                "status": "ok",
                "path": output_path,
                "size_bytes": len(img_bytes),
                "revised_prompt": revised_prompt,
            })
        else:
            return json.dumps({
                "status": "ok",
                "url": image_data.get("url", ""),
                "revised_prompt": revised_prompt,
                "note": "URL expires after 1 hour. Use output_path to save permanently.",
            })

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return json.dumps({"error": f"OpenAI API error {e.code}: {error_body[:500]}"})
    except Exception as e:
        log.exception("generate_image failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def generate_speech(
    text: str,
    voice: str = "alloy",
    model: str = "tts-1",
    output_path: str = "",
    speed: float = 1.0,
) -> str:
    """Generate speech audio from text using OpenAI TTS."""
    if not _get_openai_key():
        return json.dumps({"error": "OPENAI_API_KEY not configured"})

    if not output_path:
        from forge.config import DATA_DIR
        output_path = str(DATA_DIR / f"tts_{int(time.time())}.mp3")

    try:
        url = "https://api.openai.com/v1/audio/speech"
        body = json.dumps({
            "model": model,
            "input": text,
            "voice": voice,
            "speed": speed,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {_get_openai_key()}")
        req.add_header("Content-Type", "application/json")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with urllib.request.urlopen(req, timeout=60) as resp:
            audio_bytes = resp.read()
            with open(output_path, "wb") as f:
                f.write(audio_bytes)

        return json.dumps({
            "status": "ok",
            "path": output_path,
            "size_bytes": len(audio_bytes),
            "voice": voice,
            "model": model,
        })

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return json.dumps({"error": f"OpenAI API error {e.code}: {error_body[:500]}"})
    except Exception as e:
        log.exception("generate_speech failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def transcribe_audio(
    audio_path: str,
    language: str = "",
    prompt: str = "",
) -> str:
    """Transcribe audio to text using OpenAI Whisper."""
    if not _get_openai_key():
        return json.dumps({"error": "OPENAI_API_KEY not configured"})

    if not os.path.exists(audio_path):
        return json.dumps({"error": f"File not found: {audio_path}"})

    try:
        import mimetypes
        # Build multipart form data manually (no requests dependency)
        boundary = f"----ForgeUpload{int(time.time() * 1000)}"
        lines = []

        # model field
        lines.append(f"--{boundary}")
        lines.append('Content-Disposition: form-data; name="model"')
        lines.append("")
        lines.append("whisper-1")

        # language field (optional)
        if language:
            lines.append(f"--{boundary}")
            lines.append('Content-Disposition: form-data; name="language"')
            lines.append("")
            lines.append(language)

        # prompt field (optional)
        if prompt:
            lines.append(f"--{boundary}")
            lines.append('Content-Disposition: form-data; name="prompt"')
            lines.append("")
            lines.append(prompt)

        # file field
        filename = os.path.basename(audio_path)
        mime_type = mimetypes.guess_type(audio_path)[0] or "audio/mpeg"
        lines.append(f"--{boundary}")
        lines.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"')
        lines.append(f"Content-Type: {mime_type}")
        lines.append("")

        # Encode text parts
        header_bytes = ("\r\n".join(lines) + "\r\n").encode("utf-8")

        with open(audio_path, "rb") as f:
            file_bytes = f.read()

        footer_bytes = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = header_bytes + file_bytes + footer_bytes

        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {_get_openai_key()}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        return json.dumps({
            "status": "ok",
            "text": result.get("text", ""),
            "audio_path": audio_path,
        })

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return json.dumps({"error": f"OpenAI API error {e.code}: {error_body[:500]}"})
    except Exception as e:
        log.exception("transcribe_audio failed")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ── Registration ─────────────────────────────────────────────────────────

def register(registry: ToolRegistry):
    """Register image/audio generation tools."""

    registry.register(
        name="generate_image",
        description=(
            "Generate an image from a text prompt using OpenAI DALL-E 3. "
            "Returns a temporary URL or saves to a file path. "
            "Sizes: 1024x1024, 1024x1792, 1792x1024. "
            "Quality: standard or hd. Style: vivid or natural."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the image to generate",
                },
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1024x1792", "1792x1024"],
                    "default": "1024x1024",
                },
                "quality": {
                    "type": "string",
                    "enum": ["standard", "hd"],
                    "default": "standard",
                },
                "style": {
                    "type": "string",
                    "enum": ["vivid", "natural"],
                    "default": "vivid",
                },
                "output_path": {
                    "type": "string",
                    "description": "File path to save the image. If empty, returns a temporary URL.",
                },
            },
            "required": ["prompt"],
        },
        handler=generate_image,
    )

    registry.register(
        name="generate_speech",
        description=(
            "Convert text to speech audio using OpenAI TTS. "
            "Voices: alloy, echo, fable, onyx, nova, shimmer. "
            "Models: tts-1 (fast) or tts-1-hd (higher quality). "
            "Saves an MP3 file and returns the path."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to convert to speech"},
                "voice": {
                    "type": "string",
                    "enum": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
                    "default": "alloy",
                },
                "model": {
                    "type": "string",
                    "enum": ["tts-1", "tts-1-hd"],
                    "default": "tts-1",
                },
                "output_path": {
                    "type": "string",
                    "description": "File path for the output audio. Defaults to data dir.",
                },
                "speed": {
                    "type": "number",
                    "description": "Speech speed (0.25 to 4.0, default 1.0)",
                    "default": 1.0,
                },
            },
            "required": ["text"],
        },
        handler=generate_speech,
    )

    registry.register(
        name="transcribe_audio",
        description=(
            "Transcribe audio to text using OpenAI Whisper. "
            "Supports mp3, mp4, mpeg, mpga, m4a, wav, and webm. "
            "Max file size 25MB. Optionally specify language (ISO-639-1 code)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Absolute path to the audio file",
                },
                "language": {
                    "type": "string",
                    "description": "Language code (e.g. 'en', 'es', 'fr'). Auto-detected if empty.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional prompt to guide transcription style/terminology",
                },
            },
            "required": ["audio_path"],
        },
        handler=transcribe_audio,
    )
