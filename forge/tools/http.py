from __future__ import annotations
import json
import urllib.request
import urllib.error
import urllib.parse
from .registry import ToolRegistry
from forge.security import SafeRedirectHandler, check_public_url


def _opener():
    return urllib.request.build_opener(SafeRedirectHandler.build())


def http_get(url: str, headers: str = "") -> str:
    """Perform an HTTP GET request and return the response."""
    try:
        url = check_public_url(url)
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "TheForge/1.0")
        if headers:
            try:
                h = json.loads(headers)
                for k, v in h.items():
                    req.add_header(k, v)
            except json.JSONDecodeError:
                pass

        with _opener().open(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.dumps({
                "status": resp.status,
                "url": resp.url,
                "content_type": resp.headers.get("Content-Type", ""),
                "body": body[:6_000] if body else "",
            })
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:2_000]
        except Exception:
            pass
        return json.dumps({"error": f"HTTP {e.code}: {e.reason}", "body": body})
    except urllib.error.URLError as e:
        return json.dumps({"error": f"URL error: {e.reason}"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def http_post(url: str, body: str = "", headers: str = "", content_type: str = "application/json") -> str:
    """Perform an HTTP POST request and return the response."""
    try:
        url = check_public_url(url)
        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("User-Agent", "TheForge/1.0")
        req.add_header("Content-Type", content_type)
        if headers:
            try:
                h = json.loads(headers)
                for k, v in h.items():
                    req.add_header(k, v)
            except json.JSONDecodeError:
                pass

        with _opener().open(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            return json.dumps({
                "status": resp.status,
                "url": resp.url,
                "content_type": resp.headers.get("Content-Type", ""),
                "body": resp_body[:6_000] if resp_body else "",
            })
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except urllib.error.HTTPError as e:
        resp_body = ""
        try:
            resp_body = e.read().decode("utf-8", errors="replace")[:2_000]
        except Exception:
            pass
        return json.dumps({"error": f"HTTP {e.code}: {e.reason}", "body": resp_body})
    except urllib.error.URLError as e:
        return json.dumps({"error": f"URL error: {e.reason}"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# -- Registration ------------------------------------------------------------

def register(registry: ToolRegistry):
    registry.register(
        name="http_get",
        description="Perform an HTTP GET request and return the response body (capped at 6K chars).",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to request"},
                "headers": {"type": "string", "description": "Optional JSON object of extra headers, e.g. '{\"Authorization\": \"Bearer xxx\"}'"},
            },
            "required": ["url"],
        },
        handler=http_get,
    )
    registry.register(
        name="http_post",
        description="Perform an HTTP POST request with an optional body and return the response (capped at 6K chars).",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to POST to"},
                "body": {"type": "string", "description": "Request body (usually JSON string)"},
                "headers": {"type": "string", "description": "Optional JSON object of extra headers"},
                "content_type": {"type": "string", "description": "Content-Type header (default: application/json)"},
            },
            "required": ["url"],
        },
        handler=http_post,
    )
