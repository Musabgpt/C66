"""Independent free-provider adapters used by MusabAI Phase 3.

FreeLLMAPI stays an optional OpenAI-compatible gateway.  AI Horde is kept as a
separate last-resort provider so a FreeLLMAPI outage can never become a single
point of failure.
"""
import json
import os
import time

from . import providers

FREELLMAPI_DEFAULT_BASE = "http://127.0.0.1:3001/v1"
AIHORDE_BASE = "https://oai.aihorde.net/v1"
AIHORDE_ANONYMOUS_KEY = "0000000000"


def extra_free_specs():
    """Return optional FreeLLMAPI plus the independent anonymous AI Horde fallback."""
    out = []
    free_key = str(os.environ.get("FREELLMAPI_API_KEY") or "").strip()
    if free_key:
        out.append({
            "id": "freellmapi/auto-free",
            "name": "FreeLLMAPI",
            "base_url": str(os.environ.get("FREELLMAPI_BASE_URL") or FREELLMAPI_DEFAULT_BASE).rstrip("/"),
            "model": str(os.environ.get("FREELLMAPI_MODEL") or "auto"),
            "provider": "freellmapi",
            "api_key": free_key,
            "free": True,
            # Conservative protocol guarantees documented by FreeLLMAPI.
            "capabilities": ["text", "vision", "tools", "streaming", "json"],
        })

    out.append({
        "id": "aihorde/anonymous-free",
        "name": "AI Horde Free",
        "base_url": AIHORDE_BASE,
        "model": str(os.environ.get("AIHORDE_MODEL") or "auto"),
        "provider": "aihorde",
        "api_key": str(os.environ.get("AIHORDE_API_KEY") or AIHORDE_ANONYMOUS_KEY).strip()
                   or AIHORDE_ANONYMOUS_KEY,
        "free": True,
        # The dedicated adapter intentionally exposes only what the pilot
        # OpenAI surface can safely guarantee for arbitrary live workers.
        "capabilities": ["text", "streaming"],
    })
    return out


class AIHordeProvider:
    """Bounded text/streaming adapter for AI Horde's OpenAI-compatible pilot."""

    kind = "aihorde"

    def __init__(self, api_key="", base_url=AIHORDE_BASE, timeout=120):
        self.api_key = api_key or AIHORDE_ANONYMOUS_KEY
        self.base_url = str(base_url or AIHORDE_BASE).rstrip("/")
        self.timeout = max(15, min(int(timeout or 120), 180))
        self._models_cache = (0.0, [])

    def _headers(self):
        return {"Authorization": "Bearer " + self.api_key}

    def _models(self):
        now = time.monotonic()
        cached_at, cached = self._models_cache
        if cached and now - cached_at < 60:
            return list(cached)
        data = providers.get_json(self.base_url + "/models", self._headers(), timeout=20)
        models = [str(item.get("id")) for item in (data.get("data") or [])
                  if isinstance(item, dict) and item.get("id")]
        self._models_cache = (now, models)
        return list(models)

    def _resolve_model(self, requested):
        if requested and requested != "auto":
            return requested
        models = self._models()
        if not models:
            raise providers.ProviderError("AI Horde has no online text model available", 503)
        return models[0]

    @staticmethod
    def _message_payload(messages):
        # The OpenAI proxy accepts normal chat messages. Strip MusabAI-only
        # bookkeeping keys so a volunteer backend never receives internal state.
        allowed = {"role", "content", "name"}
        return [{k: v for k, v in m.items() if k in allowed}
                for m in (messages or []) if isinstance(m, dict)]

    def chat(self, model, messages, tools=None, max_tokens=4096, temperature=None,
             reasoning=None, on_event=None, cancel=None, extra=None):
        if tools:
            raise providers.ProviderError("AI Horde fallback does not support tool calls", 404)
        if cancel is not None and cancel.is_set():
            raise providers.Cancelled()

        chosen = self._resolve_model(model)
        body = {
            "model": chosen,
            "messages": self._message_payload(messages),
            "stream": True,
        }
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        if temperature is not None:
            body["temperature"] = temperature

        try:
            stream = providers.Stream(
                self.base_url + "/chat/completions",
                body,
                self._headers(),
                timeout=self.timeout,
            )
        except (OSError, Exception) as error:
            if isinstance(error, providers.ProviderError):
                raise
            raise providers.ProviderError("cannot reach AI Horde: %s" % error)

        if hasattr(providers, "_watch"):
            providers._watch(stream, cancel)

        out = providers.Completion()
        started = time.time()
        first = None
        content = []
        try:
            if stream.resp.status >= 400:
                text = stream.read_all()
                err = providers.ProviderError(
                    "AI Horde HTTP %d" % stream.resp.status,
                    stream.resp.status,
                    text,
                )
                retry = stream.resp.getheader("Retry-After")
                if retry:
                    err.retry_after = retry
                raise err
            for line in stream.lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("error"):
                    raise providers.ProviderError("AI Horde generation failed")
                usage = event.get("usage") or {}
                if usage:
                    prompt = int(usage.get("prompt_tokens") or 0)
                    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                    out.usage.update(
                        prompt=prompt,
                        cached=cached,
                        new=max(0, prompt - cached),
                        output=int(usage.get("completion_tokens") or 0),
                    )
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or choice.get("message") or {}
                    text = delta.get("content")
                    if text:
                        first = first or time.time()
                        content.append(text)
                        if on_event:
                            on_event("text", text)
                    if choice.get("finish_reason"):
                        out.finish = choice.get("finish_reason")
            out.content = "".join(content)
            return out
        finally:
            stream.close()
            done = time.time()
            out.timings["total_ms"] = (done - started) * 1000
            out.timings["ttft_ms"] = ((first or done) - started) * 1000
            out.timings["gen_ms"] = (done - (first or done)) * 1000
