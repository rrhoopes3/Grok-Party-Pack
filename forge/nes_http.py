"""Flask Blueprint for NES Arena + related controller proxy HTTP API."""
from __future__ import annotations
import base64 as _b64
import logging

from flask import Blueprint, Response, jsonify, request

from forge import nes_arena as _nes
from forge.config import (
    NES_COACH_MODEL,
    NES_CONTROLLER_MODEL,
    NES_COACH_INTERVAL_MS,
)

log = logging.getLogger("forge.nes_http")
nes_bp = Blueprint("nes", __name__)

@nes_bp.route("/api/nes/roms", methods=["GET"])
def nes_roms():
    return jsonify({"roms": _nes.list_roms()})


@nes_bp.route("/api/nes/rom/<slug>", methods=["GET"])
def nes_rom_bytes(slug: str):
    """Return ROM bytes as base64 JSON so the browser can hand them to jsnes."""
    data = _nes.get_rom_bytes(slug)
    if data is None:
        return jsonify({"error": f"Unknown ROM: {slug!r}"}), 404
    rom = _nes.rom_by_slug(slug)
    return jsonify({
        "slug": slug,
        "title": rom["title"] if rom else slug,
        "filename": rom["filename"] if rom else f"{slug}.nes",
        "size_bytes": len(data),
        "data_b64": _b64.b64encode(data).decode("ascii"),
    })


@nes_bp.route("/api/nes/sessions", methods=["GET"])
def nes_sessions_list():
    return jsonify({"sessions": _nes.list_sessions()})


@nes_bp.route("/api/nes/sessions", methods=["POST"])
def nes_sessions_new():
    """Body: { rom_slug, mode, coach_model?, controller_model?, coach_interval_ms? }"""
    body = request.get_json(silent=True) or {}
    slug = (body.get("rom_slug") or "").strip()
    mode = (body.get("mode") or "hybrid-coach").strip()
    if not slug:
        return jsonify({"error": "rom_slug is required"}), 400
    rom = _nes.rom_by_slug(slug)
    if rom is None:
        return jsonify({"error": f"Unknown ROM: {slug!r}"}), 404
    s = _nes.create_session(
        rom_slug=slug,
        rom_title=rom["title"],
        mode=mode,
        coach_model=body.get("coach_model") or NES_COACH_MODEL,
        controller_model=body.get("controller_model") or NES_CONTROLLER_MODEL,
        coach_interval_ms=int(body.get("coach_interval_ms") or NES_COACH_INTERVAL_MS),
    )
    return jsonify(s.summary())


@nes_bp.route("/api/nes/sessions/<session_id>", methods=["GET"])
def nes_sessions_get(session_id: str):
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    return jsonify(s.summary())


@nes_bp.route("/api/nes/sessions/<session_id>", methods=["DELETE"])
def nes_sessions_delete(session_id: str):
    if not _nes.delete_session(session_id):
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    return jsonify({"deleted": session_id})


@nes_bp.route("/api/nes/sessions/<session_id>/tick", methods=["POST"])
def nes_sessions_tick(session_id: str):
    """Browser heartbeat: frame screenshot + observed game state."""
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    s.ingest_tick(
        frame_b64=body.get("frame_b64", ""),
        frame_n=int(body.get("frame_n", 0) or 0),
        state=body.get("state", {}) or {},
    )
    return jsonify({"ok": True, "frame_n": s.last_frame_n})


@nes_bp.route("/api/nes/sessions/<session_id>/coach", methods=["POST"])
def nes_sessions_coach(session_id: str):
    """Ask the coach model for a plan. Reuses the session's last frame unless
    the body overrides it."""
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    frame_b64 = body.get("frame_b64") or s.last_frame_b64
    plan_history_texts = [p.text for p in s.plan_history]

    try:
        result = _nes.coach_advise(
            model=body.get("model") or s.coach_model,
            rom_title=s.rom_title,
            mode=s.mode,
            frame_b64=frame_b64,
            plan_history=plan_history_texts,
            score=s.last_score,
            lives=s.last_lives,
            level=s.last_level,
            extra_context=body.get("extra_context", ""),
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        log.exception("nes coach failed for %s", session_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    from forge.nes_arena.session import CoachPlan
    plan = CoachPlan(
        text=result["plan"],
        emitted_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        model=result["model"],
        ms=result["ms"],
        frame_n=s.last_frame_n,
        raw_response=result["raw"],
    )
    s.set_plan(plan)
    return jsonify({
        "plan": plan.text,
        "model": plan.model,
        "ms": plan.ms,
        "used_vision": result.get("used_vision", False),
        "frame_n": plan.frame_n,
    })


@nes_bp.route("/api/nes/sessions/<session_id>/event", methods=["POST"])
def nes_sessions_event(session_id: str):
    """Record an in-game event (death, level, powerup) + vault deposit."""
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    kind = (body.get("kind") or "note").strip()
    summary = (body.get("summary") or "").strip()
    frame_n = int(body.get("frame_n", s.last_frame_n) or 0)
    extra = body.get("extra", {}) or {}
    if not summary:
        return jsonify({"error": "summary is required"}), 400

    from forge.nes_arena.session import NESEvent
    s.add_event(NESEvent(kind=kind, frame_n=frame_n, summary=summary, extra=extra))

    vault_status = _nes.log_event(
        session_id=session_id,
        rom_slug=s.rom_slug,
        rom_title=s.rom_title,
        kind=kind,
        summary=summary,
        frame_n=frame_n,
        extra=extra,
    )
    return jsonify({"ok": True, **vault_status})


@nes_bp.route("/api/nes/sessions/<session_id>/controller", methods=["POST"])
def nes_controller_grok(session_id: str):
    """Fast controller via Forge's provider stack (Grok/Claude/GPT).

    Use when LM Studio is too slow (or absent). Body:
      { "frame_b64": "data:image/png;base64,...",
        "coach_plan": "go right",
        "recent_actions": "[...]",
        "model": "grok-4-1-fast-non-reasoning" }

    Returns {"buttons":[...],"hold_ms":N,"ms":latency,"raw":"..."}.
    Vision-capable models get the frame; others get a text-only prompt
    that degrades gracefully (still usable for menu nav).
    """
    s = _nes.get_session(session_id)
    if s is None:
        return jsonify({"error": f"Unknown session: {session_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    model = (body.get("model") or s.controller_model or "grok-4-1-fast-non-reasoning").strip()
    frame_b64 = body.get("frame_b64") or s.last_frame_b64
    coach_plan = (body.get("coach_plan") or "").strip()[:300]
    recent = (body.get("recent_actions") or "").strip()[:200]

    # Reuse the NES coach's provider-agnostic LLM caller — it already
    # handles vision vs text, Anthropic/OpenAI/Grok routing, and
    # max_completion_tokens / temperature quirks.
    from forge.nes_arena.coach import (
        _call_openai_compat, _call_anthropic, _provider_for, _supports_vision,
    )
    from forge.config import (
        XAI_API_KEY as _XAI, ANTHROPIC_API_KEY as _ANT, OPENAI_API_KEY as _OAI,
        LMSTUDIO_BASE_URL as _LM, OLLAMA_BASE_URL as _OLL,
    )

    system = (
        "You are the fast controller for an NES game. You receive one "
        "frame and a strategic plan. Output EXACTLY one JSON object, no "
        "prose, no markdown, no thinking:\n"
        '  {"buttons":["LEFT"|"RIGHT"|"UP"|"DOWN"|"A"|"B"|"START"|"SELECT"],'
        '"hold_ms":INTEGER_50_TO_400}\n'
        "Empty buttons = do nothing this tick. Jump=A. Run=B+RIGHT.\n\n"
        "CRITICAL — START BUTTON RULES:\n"
        "• In actual gameplay (Mario running, Link exploring, etc.), "
        "START *pauses the game*. NEVER press START during gameplay.\n"
        "• Only press START when the screen shows a title/menu image: "
        "logo + 'PRESS START' text + no HUD. If you see score, lives, "
        "or a character, it's gameplay — do NOT press START.\n"
        "• If you just pressed START on the prior tick, do NOT press it "
        "again. The menu has already advanced.\n\n"
        "React to what you see. Prefer movement + jumps over menu buttons."
    )
    user_prompt = (
        f"Coach plan: {coach_plan or '(none — act on screen cues)'}\n"
        f"Your last actions: {recent or '(none)'}\n"
        f"Output JSON only."
    )

    import time as _t, re as _re
    started = _t.monotonic()
    use_vision = bool(frame_b64) and _supports_vision(model)
    image = frame_b64 if use_vision else None

    # Route through the openai SDK directly here (not via coach helpers)
    # so we can pull the raw usage block back out for cost accounting —
    # the coach helpers drop it on the floor since the coach has its own
    # cost tracking on the session object.
    from openai import OpenAI
    import anthropic

    try:
        provider = _provider_for(model)
        usage_in = 0
        usage_out = 0
        if provider == "anthropic":
            client = anthropic.Anthropic(api_key=_ANT)
            content: list = []
            if image:
                import re as _r2
                m = _r2.match(r"^data:([^;]+);base64,(.+)$", image)
                mime, b64 = (m.group(1), m.group(2)) if m else ("image/png", image)
                content.append({"type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64}})
            content.append({"type": "text", "text": user_prompt})
            resp = client.messages.create(
                model=model, max_tokens=200, system=system,
                messages=[{"role": "user", "content": content}],
            )
            from forge.providers import anthropic_message_text
            raw = anthropic_message_text(resp.content)
            u = getattr(resp, "usage", None)
            if u:
                usage_in = int(getattr(u, "input_tokens", 0) or 0)
                usage_out = int(getattr(u, "output_tokens", 0) or 0)
        else:
            if provider == "openai":
                base_url, api_key = None, _OAI or ""
            elif provider == "lmstudio":
                base_url = _LM; api_key = "lm-studio"
                model = model.removeprefix("lmstudio:") or "default"
            elif provider == "ollama":
                base_url = _OLL; api_key = "ollama"
                model = model.removeprefix("ollama:") or "default"
            else:  # xai
                base_url = "https://api.x.ai/v1"; api_key = _XAI or ""
            client = OpenAI(api_key=api_key or "none", base_url=base_url)
            if image:
                m2 = _re.match(r"^data:([^;]+);base64,(.+)$", image)
                mime, b64 = (m2.group(1), m2.group(2)) if m2 else ("image/png", image)
                user_content = [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": user_prompt},
                ]
            else:
                user_content = user_prompt
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": user_content}]
            # Reasoning models use max_completion_tokens; everything else max_tokens
            call_kwargs = {"model": model, "messages": messages}
            m_lower = model.lower()
            if m_lower.startswith(("o1-", "o3-", "o4-", "gpt-5")):
                call_kwargs["max_completion_tokens"] = 200
            else:
                call_kwargs["max_tokens"] = 200
            if not m_lower.startswith(("claude-opus-4-7", "claude-opus-4-6",
                    "claude-sonnet-4-6", "claude-opus-4-5", "claude-sonnet-4-5",
                    "claude-haiku-4-5", "o1-", "o3-", "o4-", "gpt-5")):
                call_kwargs["temperature"] = 0.4
            resp = client.chat.completions.create(**call_kwargs)
            raw = resp.choices[0].message.content or ""
            u = getattr(resp, "usage", None)
            if u:
                usage_in = int(getattr(u, "prompt_tokens", 0) or 0)
                usage_out = int(getattr(u, "completion_tokens", 0) or 0)
    except Exception as e:
        log.exception("nes controller (grok path) failed for %s", session_id)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502

    # Cost lookup — same pricing source the chess tab uses.
    from forge.config import EXECUTOR_MODELS as _EM
    info = _EM.get(model, {})
    cost_usd = (usage_in * float(info.get("cost_in", 0) or 0)
                + usage_out * float(info.get("cost_out", 0) or 0)) / 1_000_000.0
    s.add_cost(cost_usd)   # rolls up on the session for MCP tool visibility

    ms = int((_t.monotonic() - started) * 1000)

    # Permissive JSON extraction — model might wrap in fences / prose.
    text = (raw or "").strip()
    if not text:
        return jsonify({"buttons": [], "hold_ms": 120, "ms": ms,
                        "raw": "", "warning": "empty reply"}), 200
    fence = _re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    body_text = fence.group(1) if fence else text
    obj_match = _re.search(r"\{[\s\S]*?\}", body_text)
    buttons: list = []
    hold_ms = 120
    if obj_match:
        try:
            import json as _json
            parsed = _json.loads(obj_match.group(0))
            raw_buttons = parsed.get("buttons") or []
            if isinstance(raw_buttons, list):
                valid = {"LEFT","RIGHT","UP","DOWN","A","B","START","SELECT"}
                buttons = [str(b).upper().strip() for b in raw_buttons
                           if str(b).upper().strip() in valid]
            try:
                hold_ms = max(40, min(400, int(parsed.get("hold_ms", 120))))
            except Exception:
                pass
        except Exception:
            pass

    return jsonify({
        "buttons": buttons, "hold_ms": hold_ms, "ms": ms,
        "model": model, "used_vision": use_vision, "raw": text[:300],
        "usage": {
            "input_tokens": usage_in,
            "output_tokens": usage_out,
            "cost_usd": round(cost_usd, 6),
            "session_total_cost_usd": round(s.cost_usd, 6),
            "session_api_calls": s.api_calls,
        },
    })


@nes_bp.route("/api/lmstudio/models", methods=["GET"])
def lmstudio_models():
    """Proxy LM Studio's /v1/models so the UI can populate dropdowns
    with the user's actually-loaded-right-now models instead of a stale
    hard-coded list. Returns {models: [...]} on success, {error} on
    reach failure. ~3s timeout — we'd rather fail fast than block a
    page load when LM Studio isn't running."""
    import json as _json, urllib.request, urllib.error
    from forge.config import LMSTUDIO_BASE_URL as _LM
    base = (_LM or "http://localhost:1234/v1").rstrip("/")
    try:
        with urllib.request.urlopen(base + "/models", timeout=3) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return jsonify({
            "error": f"LM Studio unreachable at {base}: {e.reason}",
            "models": [],
        }), 502
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}", "models": []}), 500

    # LM Studio returns {"data":[{"id":"qwen/qwen3.5-9b", "object":"model", ...}, ...]}.
    # Filter out embedding models — they can't do chat.
    raw = data.get("data") or []
    out = []
    for m in raw:
        mid = m.get("id") or ""
        if not mid: continue
        if "embed" in mid.lower(): continue
        out.append({"id": mid, "label": mid})
    return jsonify({"base_url": base, "models": out})


@nes_bp.route("/api/nes/controller", methods=["POST"])
def nes_controller_proxy():
    """Same-origin proxy for the NES controller loop's LM Studio calls.

    Why this exists: the browser can't POST `application/json` to a
    different-origin localhost service without a CORS preflight, and
    LM Studio's OPTIONS handler is broken — it tries to treat OPTIONS
    as a chat completion and 400s with "'messages' field is required".
    Proxying through the Forge backend (same origin as the page) skips
    preflight entirely.

    Body:
      {
        "target_url": "http://localhost:1234/v1",
        "body": { model, messages, max_tokens, ... }
      }
    Returns LM Studio's response verbatim (body + status) so the
    browser can parse it as if it had called directly.
    """
    import json as _json
    import urllib.request
    import urllib.error

    payload = request.get_json(silent=True) or {}
    target = (payload.get("target_url") or "").strip().rstrip("/")
    inner = payload.get("body") or {}
    if not target:
        return jsonify({"error": "target_url required"}), 400
    if not isinstance(inner, dict) or not inner.get("messages"):
        return jsonify({"error": "body.messages required"}), 400

    url = target + "/chat/completions"
    data = _json.dumps(inner).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return Response(body, status=resp.status, content_type="application/json")
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
            return Response(body, status=e.code, content_type="application/json")
        except Exception:
            return jsonify({"error": f"HTTP {e.code} from {target}"}), 502
    except urllib.error.URLError as e:
        return jsonify({
            "error": f"Could not reach LM Studio at {target}: {e.reason}",
            "hint": "Is LM Studio running and is the model loaded?",
        }), 502
    except Exception as e:
        log.exception("nes_controller_proxy failed")
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

