"""A local OpenAI-compatible endpoint backed by MusabAI's free provider pool.

The automation backends (Mobilerun's MobileAgent, the Browser Use agent) speak the
OpenAI chat-completions protocol and always send an ``Authorization: Bearer`` header.
The free gateways reject a present-but-foreign token, so those agents cannot call a
free gateway directly. They call this endpoint instead — one place, inside the engine,
that routes through ``provider_pool`` (Gemini -> OpenRouter -> Groq -> Kilo free) with
real health and fallback, and needs no key of the caller's own.

Routes (behind the engine's existing 127.0.0.1 + key gate):
  POST /v1/free/chat/completions   OpenAI chat completion, non-streaming
  GET  /v1/free/models             the free tool-capable models currently reachable
"""

from __future__ import annotations

import json
import time
import uuid

PATHS = ("/v1/free/chat/completions", "/v1/free/models")


def _free_spec(model=""):
    from . import provider_pool
    want = str(model or "").strip()
    if want and want not in ("auto", "kilo-auto/free"):
        for spec in provider_pool._kilo_models():
            if spec.get("id") == want or spec.get("model") == want:
                return dict(spec)
    return {
        "id": "kilo-auto/free", "model": "kilo-auto/free", "name": "Kilo Free",
        "base_url": provider_pool.KILO_BASE, "provider": "openai", "free": True,
        "capabilities": ["text", "tools", "streaming"],
    }


def complete(body):
    """Answer an OpenAI chat-completions request from the free pool. Returns (status, dict)."""
    from . import models, provider_pool, providers

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return 400, {"error": {"message": "messages is required", "type": "invalid_request_error"}}
    tools = body.get("tools") or None
    temperature = body.get("temperature")
    max_tokens = int(body.get("max_tokens") or body.get("max_completion_tokens") or 2048)

    try:
        client = models.connect(_free_spec(body.get("model", "")))
    except Exception as e:  # noqa: BLE001
        return 503, {"error": {"message": "%s: %s" % (type(e).__name__, e), "type": "server_error"}}

    kwargs = {"max_tokens": max_tokens}
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    try:
        out = provider_pool.chat_recovering(client, messages, tools=tools, **kwargs)
    except providers.Cancelled:
        return 499, {"error": {"message": "cancelled", "type": "cancelled"}}
    except Exception as e:  # noqa: BLE001
        return 502, {"error": {"message": provider_pool.public_error(e), "type": "upstream_error"}}

    msg = out.message()
    clean = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        clean["tool_calls"] = msg["tool_calls"]
    finish = "tool_calls" if clean.get("tool_calls") else (out.finish or "stop")
    usage = out.usage or {}
    prompt = int(usage.get("prompt") or 0) + int(usage.get("cached") or 0)
    completion = int(usage.get("output") or 0)
    return 200, {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": getattr(client, "model_name", "kilo-auto/free"),
        "choices": [{"index": 0, "message": clean, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion},
    }


def models_list():
    from . import provider_pool
    rows = [{"id": "kilo-auto/free", "object": "model", "owned_by": "musabai-free"}]
    try:
        for spec in provider_pool._kilo_models():
            rows.append({"id": spec.get("id"), "object": "model", "owned_by": "musabai-free"})
    except Exception:
        pass
    seen, out = set(), []
    for r in rows:
        if r["id"] and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return {"object": "list", "data": out}


def route(handler, method, path, body=None):
    """Engine server hook. Returns True when it handled the path."""
    if path == "/v1/free/models" and method == "GET":
        handler._json(models_list())
        return True
    if path == "/v1/free/chat/completions" and method == "POST":
        status, payload = complete(body or {})
        handler._json(payload, status)
        return True
    return False
