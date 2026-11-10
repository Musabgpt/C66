"""A local OpenAI-compatible endpoint backed by MusabAI's free provider pool.

The automation backends (Mobilerun's MobileAgent, the Browser Use agent) speak the
OpenAI chat-completions protocol and always send an ``Authorization: Bearer`` header.
The free gateways reject a present-but-foreign token, so those agents cannot call a
free gateway directly. They call this endpoint instead: one place, inside the engine,
that picks a free model, watches it, and moves on when it fails.

What it guarantees to an agent loop:
  * An answer or a clear error within a bounded time. A model that sends nothing for
    STALL seconds is cancelled and the next one is tried; the stalled model cools down
    in the pool's health table, so the next request does not wait for it again.
  * Never an empty answer. Reasoning models spend the output budget on thinking and
    return no content when ``max_tokens`` is small, so the budget has a floor, and an
    answer with neither text nor tool calls counts as a failure of that model.
  * Structured output on any model. ``response_format`` goes to models that support it;
    for the others the schema is stated in the prompt and the JSON is checked here.
  * ``stream: true`` is served as server-sent events (the whole answer in one delta).

Routes (behind the engine's existing 127.0.0.1 + key gate):
  POST /v1/free/chat/completions   OpenAI chat completion
  GET  /v1/free/models             the free models currently reachable
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid

PATHS = ("/v1/free/chat/completions", "/v1/free/models")

MIN_OUTPUT_TOKENS = 2048     # reasoning models answer with nothing below their thinking budget
STALL_SECONDS = 45.0         # no token and no event for this long: the model is stuck
HARD_SECONDS = 240.0         # one attempt, however much it streams
TOTAL_SECONDS = 300.0        # the whole request, across models
MAX_ATTEMPTS = 5

# Cold-start order among the keyless gateway's models, from measurements (answers with
# content, a few seconds, large context). Only a prior: what the pool's health table
# learns (latency, failures, cooldowns) outranks it, and unknown models are still tried.
PRIOR = (
    "nvidia/nemotron-3.5-super-vl:free",
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "cohere/north-mini-code:free",
    "kilo-auto/free",
    "openrouter/free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
)

_LAST_GOOD = {"id": ""}


class _Holder:
    """What provider_pool.candidates() reads from a client: its spec."""

    def __init__(self, spec):
        self.spec = spec


def _primary(model=""):
    from . import provider_pool
    want = str(model or "").strip()
    if want and want not in ("auto", "kilo-auto/free"):
        for spec in provider_pool._kilo_models():
            if want in (spec.get("id"), spec.get("model")):
                return dict(spec)
    return {
        "id": "kilo-auto/free", "model": "kilo-auto/free", "name": "Kilo Free",
        "base_url": provider_pool.KILO_BASE, "provider": "openai", "free": True, "api_key": "",
        "capabilities": ["text", "tools", "streaming", "reasoning"],
    }


def _has_image(messages):
    from . import provider_pool
    return provider_pool._contains_image(messages or [])


def _candidates(model, required, wants_json):
    """Specs to try, best first: a model the caller named, then the user's own keyed free
    providers, then the keyless gateway's models by what is known about them."""
    from . import provider_pool
    primary = _primary(model)
    named = str(model or "").strip() not in ("", "auto", "kilo-auto/free")
    holder = _Holder(primary)
    rest = provider_pool.candidates(holder, required)
    pool = [primary] + rest
    if not any(provider_pool._available(s) for s in pool):
        pool = [primary] + provider_pool.candidates(holder, required, include_cooling=True)

    def rank(item):
        index, spec = item
        pid = provider_pool._provider_id(spec)
        keyless_gateway = provider_pool._base(spec) == provider_pool.KILO_BASE
        state = provider_pool.HEALTH.state(pid)
        latency = state.get("latency_ms") if state.get("state") == "available" else None
        prior = PRIOR.index(pid) if pid in PRIOR else len(PRIOR)
        caps = provider_pool.capabilities(spec) or frozenset()
        return (
            0 if (named and index == 0) else 1,
            0 if provider_pool._available(spec) else 1,
            0 if spec.get("provider") != "aihorde" else 1,          # the anonymous last resort stays last
            0 if not keyless_gateway else 1,                        # the user's own keyed providers first
            0 if (not wants_json or "json" in caps) else 1,
            0 if pid == _LAST_GOOD["id"] else 1,
            0 if latency is not None else 1,                        # proven before unproven
            latency if latency is not None else prior * 1000,
            index,
        )

    ordered = [spec for _, spec in sorted(enumerate(pool), key=rank)]
    return [s for s in ordered if provider_pool._supports(s, required, legacy_current=True)] or ordered


def _schema_instruction(response_format):
    kind = (response_format or {}).get("type")
    if kind == "json_schema":
        schema = ((response_format.get("json_schema") or {}).get("schema")) or {}
        return ("Respond with only one JSON object that validates against this JSON schema. "
                "No prose, no code fences.\n" + json.dumps(schema, ensure_ascii=False))
    return "Respond with only one JSON object. No prose, no code fences."


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _clean_json(text):
    """The JSON object in a model's answer ('' when there is none)."""
    text = (text or "").strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return ""
        text = text[start:end + 1]
    try:
        json.loads(text)
    except ValueError:
        return ""
    return text


def _attempt(spec, messages, tools, max_tokens, temperature, extra):
    """One model, watched. Returns (completion, latency) or raises."""
    from . import provider_pool, providers, request_context
    cancel = threading.Event()
    progress = {"at": time.monotonic()}
    box = {}

    def on_event(kind, payload):
        progress["at"] = time.monotonic()

    def work():
        try:
            prov = provider_pool.provider(spec)
            sent = request_context.project(spec, messages, tools)
            box["out"] = prov.chat(spec.get("model") or spec["id"], sent, tools=tools, max_tokens=max_tokens,
                                   temperature=temperature, on_event=on_event, cancel=cancel, extra=extra or None)
        except BaseException as error:  # noqa: BLE001 - handed to the caller's thread
            box["error"] = error

    started = time.monotonic()
    thread = threading.Thread(target=work, name="musab-free-llm", daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(0.25)
        now = time.monotonic()
        if now - progress["at"] > STALL_SECONDS or now - started > HARD_SECONDS:
            cancel.set()
            thread.join(3.0)
            raise TimeoutError("model stalled: nothing for %d s" % int(now - progress["at"]))
    if "error" in box:
        if isinstance(box["error"], providers.Cancelled):
            raise TimeoutError("model stalled")
        raise box["error"]
    return box["out"], time.monotonic() - started


def complete(body):
    """Answer an OpenAI chat-completions request from the free pool. Returns (status, dict)."""
    from . import provider_pool

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return 400, {"error": {"message": "messages is required", "type": "invalid_request_error"}}
    tools = body.get("tools") or None
    temperature = body.get("temperature")
    temperature = float(temperature) if temperature is not None else None
    asked = int(body.get("max_tokens") or body.get("max_completion_tokens") or 0)
    max_tokens = max(asked, MIN_OUTPUT_TOKENS)
    response_format = body.get("response_format") if isinstance(body.get("response_format"), dict) else None
    wants_json = bool(response_format) and response_format.get("type") in ("json_schema", "json_object")

    required = {"text", "streaming"}
    if tools:
        required.add("tools")
    if _has_image(messages):
        required.add("vision")

    try:
        candidates = _candidates(body.get("model", ""), required, wants_json)
    except Exception as e:  # noqa: BLE001
        return 503, {"error": {"message": "%s: %s" % (type(e).__name__, e), "type": "server_error"}}

    deadline = time.monotonic() + TOTAL_SECONDS
    failures = []
    for spec in candidates[:MAX_ATTEMPTS]:
        if time.monotonic() > deadline - 5:
            break
        pid = provider_pool._provider_id(spec)
        native_json = wants_json and "json" in (provider_pool.capabilities(spec) or frozenset())
        extra = {}
        sent = messages
        if wants_json:
            if native_json:
                extra["response_format"] = response_format
            else:
                sent = list(messages) + [{"role": "system", "content": _schema_instruction(response_format)}]
        if tools and body.get("tool_choice") not in (None, "auto"):
            extra["tool_choice"] = body["tool_choice"]
        try:
            out, latency = _attempt(spec, sent, tools, max_tokens, temperature, extra)
            content = out.content or ""
            if wants_json and not out.tool_calls:
                content = _clean_json(content)
                if not content:
                    raise ValueError("the model did not return the JSON object that was asked for")
            if not content.strip() and not out.tool_calls:
                raise ValueError("empty answer (finish: %s)" % (out.finish or "?"))
        except Exception as error:  # noqa: BLE001 - every failure moves to the next model
            provider_pool._mark_failure(spec, error)
            failures.append("%s: %s" % (pid, provider_pool._safe_error(error)))
            continue
        provider_pool._mark_success(spec, latency)
        _LAST_GOOD["id"] = pid
        return 200, _shape(out, content, spec)

    detail = "; ".join(failures[-MAX_ATTEMPTS:]) or "no free model is available right now"
    return 502, {"error": {"message": "the free models did not answer (" + detail + ")", "type": "upstream_error"}}


def _shape(out, content, spec):
    message = {"role": "assistant", "content": content}
    calls = out.message().get("tool_calls")
    if calls:
        message["tool_calls"] = calls
    usage = out.usage or {}
    prompt = int(usage.get("prompt") or 0) + int(usage.get("cached") or 0)
    completion = int(usage.get("output") or 0)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": spec.get("model") or spec.get("id") or "free",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if calls else (out.finish or "stop")}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion},
    }


def sse_events(payload):
    """The completion as OpenAI stream chunks: the whole answer in one delta, then the finish."""
    choice = payload["choices"][0]
    message = choice["message"]
    base = {"id": payload["id"], "object": "chat.completion.chunk", "created": payload["created"],
            "model": payload["model"]}
    delta = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        delta["tool_calls"] = [dict(call, index=i) for i, call in enumerate(message["tool_calls"])]
    yield dict(base, choices=[{"index": 0, "delta": delta, "finish_reason": None}])
    yield dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
               usage=payload["usage"])


def models_list():
    from . import provider_pool
    rows = [{"id": "kilo-auto/free", "object": "model", "owned_by": "musabai-free"}]
    try:
        for spec in provider_pool._kilo_models():
            rows.append({"id": spec.get("id"), "object": "model", "owned_by": "musabai-free"})
    except Exception:  # noqa: BLE001
        pass
    seen, out = set(), []
    for r in rows:
        if r["id"] and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return {"object": "list", "data": out}


def _stream(handler, body):
    """Server-sent events: headers at once and a comment every few seconds while the model
    works, so a client's read timeout does not fire on a slow answer."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    box = {}
    worker = threading.Thread(target=lambda: box.update(result=complete(body)), name="musab-free-sse", daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(8.0)
            if worker.is_alive():
                handler.wfile.write(b": working\n\n")
                handler.wfile.flush()
        status, payload = box.get("result") or (502, {"error": {"message": "no result", "type": "server_error"}})
        if status != 200:
            handler.wfile.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8"))
        else:
            for event in sse_events(payload):
                handler.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()
    except OSError:
        pass                                   # the client went away


def route(handler, method, path, body=None):
    """Engine server hook. Returns True when it handled the path."""
    if path == "/v1/free/models" and method == "GET":
        handler._json(models_list())
        return True
    if path == "/v1/free/chat/completions" and method == "POST":
        body = body or {}
        if body.get("stream"):
            _stream(handler, body)
        else:
            status, payload = complete(body)
            handler._json(payload, status)
        return True
    return False
