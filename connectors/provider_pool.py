"""Capability-aware, free-only provider failover for MusabAI.

The selected model is used only when it can satisfy the request capabilities.
If it fails before emitting any visible stream event, MusabAI walks the
canonical free pool:

Groq -> Gemini -> OpenRouter Free -> NVIDIA.

Selection is protocol-aware (text, vision, tools, streaming, structured JSON,
reasoning and explicit extended capabilities) while preserving the Phase 1
retry/cooldown circuit breaker. Providers without credentials are skipped.
Partial replies are retried only at an explicit uncommitted request boundary
whose caller can discard the current UI draft before another attempt.
"""
import json
import math
import logging
import os
import random
import re
import threading
import time
import urllib.request
from email.utils import parsedate_to_datetime

from . import free_provider_adapters, providers, settings
from .tool_protocol import InvalidToolResponse

FREE_POOL = [
    {
        "id": "groq/gpt-oss-120b-free",
        "name": "Groq Free",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "provider": "openai",
        "api_key_envs": ["GROQ_API_KEY"],
        "secret_id": "groq",
        "free": True,
        "capabilities": ["text", "tools", "streaming", "json", "reasoning", "long_context", "coding"],
    },
    {
        "id": "gemini/3.7-flash-free",
        "name": "Google Gemini Free",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.7-flash",
        "provider": "openai",
        "api_key_envs": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "secret_id": "gemini",
        "free": True,
        "capabilities": ["text", "vision", "tools", "streaming", "json", "reasoning", "long_context", "coding"],
    },
    {
        "id": "openrouter/free",
        "name": "OpenRouter Free",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openrouter/free",
        "provider": "openai",
        "api_key_envs": ["OPENROUTER_API_KEY"],
        "secret_id": "openrouter",
        "free": True,
        "capabilities": ["text", "vision", "tools", "streaming", "json", "reasoning", "long_context", "coding"],
    },
    {
        "id": "nvidia/nemotron-3-ultra-free",
        "name": "NVIDIA Free Endpoint",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "provider": "openai",
        "api_key_envs": ["NVIDIA_API_KEY", "NVAPI_KEY"],
        "secret_id": "nvidia",
        "free": True,
        "capabilities": ["text", "tools", "streaming", "reasoning", "long_context", "coding"],
    },
]

_RETRYABLE = {404, 408, 429, 500, 502, 503, 504}
_LOG = logging.getLogger("newal.provider_pool")
KILO_BASE = 'https://api.kilo.ai/api/gateway'
_KILO_CACHE = (0.0, [])


def _fetch_kilo_catalog():
    request = urllib.request.Request(KILO_BASE + '/models', headers={'User-Agent':'MusabAI/Phase10'})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError('Model catalog exceeds size limit')
    return json.loads(raw)


def _kilo_models():
    """Discover actual zero-price tool models; never infer free access from a name."""
    global _KILO_CACHE
    if _KILO_CACHE[0] > _now():
        return list(_KILO_CACHE[1])
    try:
        data = _fetch_kilo_catalog()
        rows = []
        for item in data.get('data', []):
            price = item.get('pricing') or {}
            if any(float(price.get(k, -1)) != 0 for k in ('prompt', 'completion')):
                continue
            if any(float(price.get(k) or 0) != 0 for k in ('request', 'image', 'internal_reasoning', 'web_search', 'input_cache_read', 'input_cache_write')):
                continue
            params = item.get('supported_parameters') or []
            mid = str(item.get('id') or '')
            if not mid or mid == 'kilo-auto/free' or 'tools' not in params:
                continue
            caps = ['text', 'tools', 'streaming']
            if 'image' in (item.get('architecture') or {}).get('input_modalities', []):
                caps.append('vision')
            if 'reasoning' in params:
                caps.append('reasoning')
            if 'response_format' in params:
                caps.append('json')
            rows.append({'id':mid, 'model':mid, 'name':mid, 'provider':'openai',
                         'base_url':KILO_BASE, 'api_key':'', 'free':True, 'capabilities':caps})
        # Prefer the route verified with a real anonymous tool call in Phase 10.
        rows.sort(key=lambda s: s['id'] != 'stepfun/step-5-preview-free')
        _KILO_CACHE = (_now() + 300, rows[:16])
        return list(_KILO_CACHE[1])
    except (providers.ProviderError, OSError, ValueError, TypeError):
        _KILO_CACHE = (_now() + 30, [])
        return []


def _now():
    return time.monotonic()


def _provider_id(spec):
    return str((spec or {}).get("id") or (spec or {}).get("model") or (spec or {}).get("base_url") or "unknown")


def _base(spec):
    return str((spec or {}).get("base_url") or "").rstrip("/")


def _key(spec):
    value = str(spec.get("api_key") or "")
    if value:
        return value
    names = spec.get("api_key_envs") or ([spec.get("api_key_env")] if spec.get("api_key_env") else [])
    for name in names:
        if name and os.environ.get(name):
            return os.environ[name]
    secret_id = spec.get("secret_id")
    if secret_id:
        try:
            from . import provider_keys
            return provider_keys.secret(secret_id)
        except Exception:
            # Desktop/CI or an Android app before a key was configured.
            return ""
    return ""


def _explicitly_free(spec):
    if spec.get("free") is True:
        return True
    mid = str(spec.get("id") or "").lower()
    model = str(spec.get("model") or "").lower()
    return mid.endswith("/free") or model.endswith(":free") or model == "openrouter/free"


def _status(error):
    return int(getattr(error, "status", 0) or 0)


def _retryable(error):
    if isinstance(error, InvalidToolResponse):
        return True
    if isinstance(error, (TimeoutError, OSError)):
        return True
    status = _status(error)
    if status in _RETRYABLE:
        return True
    text = str(error).lower()
    return any(word in text for word in (
        "overload", "temporarily unavailable", "service unavailable", "timeout",
        "timed out", "stalled", "rate limit", "too many requests", "connection reset",
        "cannot reach", "connection refused",
    ))


def _timeout_error(error):
    return isinstance(error, TimeoutError) or _status(error) in {408, 504} or any(
        word in str(error).lower() for word in ('timeout', 'timed out', 'stalled'))


def _retry_after_seconds(error):
    """Read a provider-directed Retry-After without trusting unbounded values."""
    values = [
        getattr(error, "retry_after", None),
        getattr(error, "retry_after_seconds", None),
    ]
    headers = getattr(error, "headers", None)
    if isinstance(headers, dict):
        values += [headers.get("Retry-After"), headers.get("retry-after")]

    body = getattr(error, "body", "")
    if body:
        try:
            parsed = json.loads(body) if isinstance(body, str) else body
        except (ValueError, TypeError):
            parsed = None

        def find(value):
            if isinstance(value, dict):
                for key in ("retry_after", "retry_after_seconds", "retryAfter"):
                    if key in value:
                        return value[key]
                for nested in value.values():
                    found = find(nested)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for nested in value:
                    found = find(nested)
                    if found is not None:
                        return found
            return None

        values.append(find(parsed))

    text = str(error)
    match = re.search(r"retry(?:-|\s*)after[^0-9]{0,12}(\d+(?:\.\d+)?)", text, re.I)
    if match:
        values.append(match.group(1))

    for raw in values:
        if raw in (None, ""):
            continue
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            try:
                dt = parsedate_to_datetime(str(raw))
                seconds = dt.timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                continue
        if math.isfinite(seconds) and seconds >= 0:
            return max(1, min(int(seconds + 0.999), 1800))
    return None


def _cooldown_seconds(error, failures):
    status = _status(error)
    explicit = _retry_after_seconds(error)
    if isinstance(error, InvalidToolResponse):
        base = 180
    elif status == 404:
        base = 90
    elif status == 429 or "rate limit" in str(error).lower():
        base = 30
    elif status in {502, 503, 504} or "overload" in str(error).lower():
        base = 60
    elif status in {401, 403}:
        base = 300
    elif status == 400:
        base = 180
    elif status == 408 or isinstance(error, TimeoutError):
        base = 20
    else:
        base = 20
    if failures >= 3:
        base = max(base, 120)
    return max(base, explicit or 0)


def _redact_diagnostic(text):
    text = str(text or "")
    text = re.sub(
        r"(?i)((?:api[_-]?key|authorization|token|secret)\s*[=:]\s*)([^\s,;]+)",
        r"\1<redacted>",
        text,
    )
    return text[:2000]


def _diagnostic_failure(spec, error):
    # Raw provider failures belong in diagnostics, never in the user-facing
    # aggregate error. Obvious credential-shaped values are redacted first.
    _LOG.warning(
        "provider failure id=%s status=%s detail=%s",
        _provider_id(spec),
        _status(error),
        _redact_diagnostic(error),
    )


class ProviderHealthManager:
    """Thread-safe health/circuit-breaker state shared by the free provider pool."""

    def __init__(self):
        self._data = {}
        self._lock = threading.RLock()

    def state(self, provider_id):
        with self._lock:
            return dict(self._data.get(provider_id) or {})

    def available(self, spec):
        state = self.state(_provider_id(spec))
        return float(state.get("cooldown_until") or 0) <= _now()

    def success(self, spec, latency):
        pid = _provider_id(spec)
        with self._lock:
            self._data[pid] = {
                "state": "available",
                "failures": 0,
                "cooldown_until": 0.0,
                "last_error_class": "",
                "latency_ms": int(max(0.0, latency) * 1000),
                "updated_at": time.time(),
            }

    def failure(self, spec, error):
        pid = _provider_id(spec)
        _diagnostic_failure(spec, error)
        with self._lock:
            old = self._data.get(pid) or {}
            failures = int(old.get("failures") or 0) + 1
            wait = _cooldown_seconds(error, failures)
            status = _status(error)
            if isinstance(error, InvalidToolResponse):
                state = 'invalid_tool_response'
            elif status in {401, 403}:
                state = "auth_error"
            elif status == 404:
                state = "capability_mismatch"
            elif _timeout_error(error):
                state = "timeout"
            elif status == 429:
                state = "rate_limited"
            elif status in {500, 502, 503, 504} or "overload" in str(error).lower():
                state = "overloaded"
            else:
                state = "cooldown"
            self._data[pid] = {
                "state": state,
                "failures": failures,
                "cooldown_until": _now() + wait,
                "last_error_class": _safe_error(error),
                "status": status,
                "retry_after_seconds": _retry_after_seconds(error),
                "latency_ms": old.get("latency_ms"),
                "updated_at": time.time(),
            }

    def snapshot(self):
        now = _now()
        with self._lock:
            return {
                pid: dict(value, cooling_down=float(value.get("cooldown_until") or 0) > now)
                for pid, value in self._data.items()
            }

    def forget(self, provider_id):
        with self._lock:
            self._data.pop(provider_id, None)

    def clear(self):
        with self._lock:
            self._data.clear()


HEALTH = ProviderHealthManager()


def _state(pid):
    return HEALTH.state(pid)


def _available(spec):
    return HEALTH.available(spec) and (_base(spec) != KILO_BASE or HEALTH.available({'id':'gateway:' + KILO_BASE}))


def _mark_success(spec, latency):
    HEALTH.success(spec, latency)


def _mark_failure(spec, error):
    HEALTH.failure(spec, error)
    if _base(spec) == KILO_BASE and _status(error) in {401, 403, 429}:
        try:
            body = json.loads(getattr(error, 'body', '') or '{}')
            metadata = (body.get('error') or {}).get('metadata') or {}
        except (ValueError, TypeError, AttributeError):
            metadata = {}
        # Only explicitly upstream model capacity permits another model route.
        # Account/gateway limits apply to all models and honor the same cooldown.
        if not str(metadata.get('limit_source', '')).startswith('upstream_'):
            HEALTH.failure({'id':'gateway:' + KILO_BASE}, error)


def health_snapshot():
    """Small diagnostics object for UI/tests; never contains credentials or raw backend bodies."""
    return HEALTH.snapshot()


def reset_health():
    """Tests and explicit reconnect actions can clear the in-process breaker."""
    HEALTH.clear()
    global _KILO_CACHE
    _KILO_CACHE = (0.0, [])


_CAPABILITY_NAMES = frozenset({
    "text", "vision", "tools", "streaming", "json", "reasoning",
    "long_context", "coding",
})
_IMAGE_TYPES = frozenset({"image", "image_url", "input_image", "input_image_url"})


def _normalize_capabilities(value):
    if value is None:
        return frozenset()
    if isinstance(value, str):
        value = value.replace(",", " ").split()
    if isinstance(value, dict):
        value = [name for name, enabled in value.items() if enabled]
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(name).strip().lower() for name in value if str(name).strip())


def capabilities(spec):
    """Return declared/known capabilities, or None for a legacy unknown model."""
    spec = spec or {}
    if "capabilities" in spec:
        return _normalize_capabilities(spec.get("capabilities"))

    pid, base = _provider_id(spec), _base(spec)
    model = str(spec.get("model") or "")
    if base == KILO_BASE and (model or pid) == 'kilo-auto/free':
        return frozenset({'text', 'tools', 'streaming', 'reasoning'})
    for known in FREE_POOL:
        known_model = str(known.get("model") or "")
        if pid == _provider_id(known) or (model and model == known_model) or (
                not model and base and base == _base(known)):
            return _normalize_capabilities(known.get("capabilities"))

    # User/local specs can opt in without adopting the full capabilities field.
    hints = set()
    modalities = _normalize_capabilities(spec.get("modalities") or spec.get("input_modalities"))
    if modalities:
        hints.update({"text", "streaming"})
        if "image" in modalities or "vision" in modalities:
            hints.add("vision")
    for field, cap in (
        ("vision", "vision"), ("tool_use", "tools"), ("tools", "tools"),
        ("streaming", "streaming"), ("json", "json"),
        ("reasoning", "reasoning"), ("coding", "coding"),
        ("long_context", "long_context"),
    ):
        if spec.get(field) is True:
            hints.add(cap)
    return frozenset(hints | {"text"}) if hints else None


def _contains_image(value):
    if isinstance(value, (list, tuple)):
        return any(_contains_image(item) for item in value)
    if not isinstance(value, dict):
        return False
    kind = str(value.get("type") or "").lower()
    if kind in _IMAGE_TYPES or kind.startswith("image_") or "image_url" in value:
        return True
    return any(_contains_image(v) for k, v in value.items() if k != "text")


def request_capabilities(messages, tools=None, required_capabilities=None, **kwargs):
    """Infer hard request requirements; callers may add explicit capabilities."""
    required = {"text", "streaming"}
    if _contains_image(messages or []):
        required.add("vision")
    if tools:
        required.add("tools")

    extra = kwargs.get("extra") if isinstance(kwargs.get("extra"), dict) else {}
    response_format = kwargs.get("response_format") or extra.get("response_format")
    if response_format or kwargs.get("json_schema") or extra.get("json_schema"):
        required.add("json")

    reasoning = kwargs.get("reasoning")
    if reasoning not in (None, "", "off", "none", False):
        required.add("reasoning")

    required.update(_normalize_capabilities(required_capabilities))
    return frozenset(required)


def _supports(spec, required, legacy_current=False):
    caps = capabilities(spec)
    if caps is None:
        # Preserve legacy selected-model behavior for text/tool turns, but never
        # guess that an unknown model can see images. Unknown fallbacks are
        # limited to plain streamed text unless they declare capabilities.
        assumed = {"text", "streaming"}
        if legacy_current:
            assumed.update({"tools", "reasoning"})
        return set(required).issubset(assumed)
    return set(required).issubset(caps)


def _safe_error(error):
    """Classify provider failures without leaking backend bodies/metadata."""
    status = _status(error)
    text = str(error).lower()
    if status in {401, 403}:
        return "authentication failed"
    if status == 404:
        return "capability unavailable"
    if _timeout_error(error):
        return "timed out"
    if status == 429 or "rate limit" in text or "too many requests" in text:
        return "rate limited"
    if status in {500, 502, 503, 504} or "overload" in text or "temporarily unavailable" in text:
        return "temporarily unavailable"
    if isinstance(error, (TimeoutError, OSError)) or "timeout" in text or "cannot reach" in text:
        return "connection failed"
    if status == 400:
        return "request rejected"
    return "request failed"


def candidates(current, required_capabilities=None, include_cooling=False):
    """Return configured, credentialed, free fallbacks in canonical order."""
    current_spec = getattr(current, "spec", {}) or {}
    seen_ids = {_provider_id(current_spec)}
    seen_bases = {_base(current_spec)} if _base(current_spec) else set()

    # Canonical pool first. User-added fallbacks are accepted only when they
    # explicitly declare themselves free, so a fallback can never silently bill.
    configured = settings.user().get("fallback_models") or []
    extras = free_provider_adapters.extra_free_specs()
    gateways = [x for x in extras if x.get("provider") == "freellmapi"]
    last_resort = [x for x in extras if x.get("provider") == "aihorde"]
    kilo = _kilo_models() if _base(current_spec) == KILO_BASE and (
        include_cooling or HEALTH.available({'id':'gateway:' + KILO_BASE})) else []
    values = gateways + list(FREE_POOL) + kilo + [
        x for x in configured if isinstance(x, dict) and _explicitly_free(x)
    ] + last_resort

    out = []
    for raw in values:
        spec = dict(raw)
        mid = _provider_id(spec)
        base = _base(spec)
        if not mid or mid in seen_ids or not base or (base in seen_bases and base != KILO_BASE):
            continue
        key = _key(spec)
        if (spec.get("api_key_env") or spec.get("api_key_envs")) and not key:
            continue
        if not _explicitly_free(spec) or (not include_cooling and not _available(spec)):
            continue
        if required_capabilities is not None and not _supports(spec, required_capabilities):
            continue
        spec["api_key"] = key
        seen_ids.add(mid)
        seen_bases.add(base)
        out.append(spec)
    return out


def provider(spec):
    if spec.get("provider") == "anthropic":
        return providers.Anthropic(spec.get("api_key", ""), spec.get("base_url") or "https://api.anthropic.com")
    if spec.get("provider") == "aihorde":
        return free_provider_adapters.AIHordeProvider(
            spec.get("api_key", ""),
            spec.get("base_url") or free_provider_adapters.AIHORDE_BASE,
        )
    return providers.OpenAICompat(spec["base_url"], spec.get("api_key", ""), spec.get("headers"))


class _ObservedEvents:
    """Tracks whether an attempt has emitted anything visible to the UI."""
    def __init__(self, callback):
        self.callback = callback
        self.emitted = False

    def __call__(self, kind, payload):
        self.emitted = True
        if self.callback:
            self.callback(kind, payload)


def _attempt(client_provider, model, messages, tools, kwargs):
    call_kwargs = dict(kwargs)
    validator = call_kwargs.pop('_completion_validator', None)
    observed = _ObservedEvents(call_kwargs.get("on_event"))
    call_kwargs["on_event"] = observed
    started = _now()
    try:
        result = client_provider.chat(model, messages, tools=tools, **call_kwargs)
        if validator is not None:
            validator(result, tools)
        return result, observed.emitted, _now() - started
    except Exception as error:
        setattr(error, "_musab_emitted", observed.emitted)
        raise


def _call_with_retry(spec, client_provider, model, messages, tools, kwargs):
    from . import request_context
    messages = request_context.project(spec, messages, tools)
    attempts = 2
    last = None
    for attempt in range(attempts):
        started = _now()
        try:
            result, emitted, latency = _attempt(client_provider, model, messages, tools, kwargs)
            _mark_success(spec, latency)
            return result
        except providers.Cancelled as cancelled:
            # A TaskSupervisor watchdog cancel is not the user's Stop. Before
            # any visible stream output it is safe to convert the stall into a
            # timeout so the Phase 3 pool can move to another healthy provider.
            # User cancellation must always propagate immediately.
            token = kwargs.get("cancel")
            reason = token.reason() if token is not None and hasattr(token, "reason") else ""
            if reason != "watchdog_stall":
                raise
            emitted = bool(getattr(cancelled, "_musab_emitted", False))
            if token is not None and hasattr(token, "consume_watchdog"):
                token.consume_watchdog()
            if emitted:
                safe = providers.ProviderError(
                    "Model stream stalled after partial output; resume from the saved checkpoint."
                )
                setattr(safe, "_musab_emitted", True)
                _mark_failure(spec, safe)
                safe._musab_provider_id = _provider_id(spec)
                raise safe
            error = TimeoutError("provider stalled without progress")
            setattr(error, "_musab_emitted", False)
            _mark_failure(spec, error)
            raise error
        except (providers.ProviderError, TimeoutError, OSError) as error:
            last = error
            # Once the UI has received stream output, switching providers would
            # duplicate or contradict text/tool calls. Surface that failure.
            if getattr(error, "_musab_emitted", False):
                _mark_failure(spec, error)
                error._musab_provider_id = _provider_id(spec)
                raise
            # Capability/rate-limit/timeout responses should move to a healthy
            # provider immediately; repeating the same request is blind retrying.
            if (isinstance(error, InvalidToolResponse) or not _retryable(error) or _timeout_error(error) or _status(error) in {404, 429}
                    or attempt + 1 >= attempts):
                _mark_failure(spec, error)
                raise
            # Short jittered retry. We intentionally fail over quickly rather
            # than making the user wait through long exponential backoff.
            delay = min(1.5, 0.25 * (2 ** attempt) + random.random() * 0.15)
            time.sleep(delay)
    raise last


class ProviderUnavailable(providers.ProviderError):
    """Retry metadata for a failed, uncommitted model request; no tools executed."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _next_retry(client, required, excluded=()):
    specs = [getattr(client, "spec", {}) or {}] + candidates(client, required, include_cooling=True)
    waits = []
    for spec in specs:
        if _provider_id(spec) in excluded:
            continue
        if not _supports(spec, required, legacy_current=spec is specs[0]):
            continue
        state = HEALTH.state(_provider_id(spec))
        if state.get("state") in {"auth_error", "capability_mismatch"} or state.get("status") == 400:
            continue
        left = float(state.get("cooldown_until") or 0) - _now()
        if _base(spec) == KILO_BASE:
            left = max(left, float(HEALTH.state('gateway:' + KILO_BASE).get('cooldown_until') or 0) - _now())
        if left > 0:
            waits.append(left)
    return min(waits) if waits else None


def _local_last_resort(client, messages, tools, required, owner, on_status, kwargs):
    if (getattr(client, "spec", {}) or {}).get("provider") == "local":
        return False, None
    configured = settings.user().get("models") or {}
    if not isinstance(configured, dict):
        return False, None
    for mid, raw in configured.items():
        if not isinstance(raw, dict) or raw.get("provider") != "local":
            continue
        spec = dict(raw, id=mid)
        if not os.path.isfile(str(spec.get("file") or "")) or not _supports(spec, required) or not _available(spec):
            continue
        from . import models
        try:
            if on_status:
                on_status("محاولة الاستكمال بنموذج محلي مثبت يدعم متطلبات المهمة: " + mid)
            backup = models.connect(spec)  # retained runtime enforces the device RAM budget
            result = backup.chat(messages, tools=tools, owner=owner, **kwargs)
        except providers.Cancelled:
            raise
        except (providers.ProviderError, OSError, ValueError, RuntimeError) as error:
            _mark_failure(spec, error)
            if getattr(error, "_musab_emitted", False):
                raise
            continue
        previous = _provider_id(getattr(client, "spec", {}) or {})
        client.spec, client.provider, client.model_name, client.server = backup.spec, backup.provider, backup.model_name, backup.server
        client._last_fallback = {"from":previous, "to":mid, "error":"online pool unavailable"}
        return True, result
    return False, None


def chat_recovering(client, messages, tools=None, required_capabilities=None,
                    on_status=None, on_reset=None, validate_tools=False, recovery_budget=90, owner="main", **kwargs):
    """Bounded automatic recovery at a model-request boundary, preserving tool results.

    The agent opts into partial-response recovery with on_reset: no tool from
    this request has executed or been committed to history yet. Other callers
    retain the no-replay contract. Stop always interrupts recovery.
    """
    deadline = _now() + max(0, min(float(recovery_budget), 120))
    token = kwargs.get("cancel")
    required = request_capabilities(messages, tools, required_capabilities, **kwargs)
    if validate_tools:
        from .tool_protocol import validate_completion
        kwargs['_completion_validator'] = validate_completion
    excluded = set()
    for attempt in range(3):
        if token is not None and token.is_set():
            raise providers.Cancelled()
        try:
            previous = _provider_id(getattr(client, 'spec', {}) or {})
            result = chat(client, messages, tools=tools, required_capabilities=required_capabilities, excluded_providers=excluded, **kwargs)
            actual = _provider_id(getattr(client, 'spec', {}) or {})
            if actual != previous and on_status:
                on_status('نجح الاستكمال بالنموذج البديل: ' + actual)
            return result
        except ProviderUnavailable as error:
            local, result = _local_last_resort(client, messages, tools, required, owner, on_status, kwargs)
            if local:
                return result
            wait = error.retry_after
            if wait is None or getattr(error, "_musab_emitted", False) or attempt == 2:
                raise
            if _now() + wait + 0.1 > deadline:
                raise
            if on_status:
                on_status("المزود غير متاح مؤقتًا؛ حُفظت نتائج الخطوات السابقة. إعادة المحاولة خلال %d ثانية." % (wait + 1))
            until = _now() + wait + 0.05
            while _now() < until:
                seconds = min(1, until - _now())
                if token is not None:
                    if token.wait(seconds):
                        raise providers.Cancelled()
                else:
                    time.sleep(seconds)
        except (providers.ProviderError, TimeoutError, OSError) as error:
            if token is not None and token.is_set():
                raise providers.Cancelled() from error
            if not getattr(error, '_musab_emitted', False) or not callable(on_reset):
                raise
            # Remove only this failed request's draft. Completed tool messages
            # stay in `messages`; incomplete tool calls never leave the provider.
            on_reset()
            failed_id = getattr(error, "_musab_provider_id", None)
            if failed_id:
                excluded.add(failed_id)
            if not _retryable(error) or attempt == 2:
                if isinstance(error, OSError):
                    final = providers.ProviderError('Provider stream disconnected before completion', 502)
                    final._musab_emitted = True
                    raise final from error
                raise
            if on_status:
                on_status('أرسل النموذج استدعاءً ناقصًا؛ لم أنفّذه وأنتقل إلى بديل.' if isinstance(error, InvalidToolResponse) else
                          'انقطع رد المزود؛ أستكمل بنموذج متاح مع الاحتفاظ بنتائج الأدوات المنفّذة.')
            if isinstance(error, InvalidToolResponse):
                messages = messages + [{'role':'user', 'content':
                    str(error)[:2500] + '\nNo tools from that response were executed. Supply complete required arguments. '
                    'Use small, complete file edits; do not write placeholder content.'}]
    raise AssertionError("bounded retry loop exhausted")


def public_error(error):
    if isinstance(error, InvalidToolResponse):
        return 'لم يُنتج المزود استدعاء أدوات صالحًا بعد محاولات الاستكمال. لم تُنفّذ الاستدعاءات الناقصة؛ الملفات السابقة محفوظة. راجع حالة النماذج في Musab Hub.'
    if isinstance(error, ProviderUnavailable):
        if error.retry_after is not None:
            return "المزودون المناسبون للمهمة غير متاحين مؤقتًا. حُفظ التقدم؛ يمكن الاستكمال بعد نحو %d ثانية أو بعد إعداد مزود احتياطي يدعم الأدوات." % (error.retry_after + 1)
        return "لم يتوفر مزود مناسب للمهمة. راجع حالة المزودين وإعداداتهم في Musab Hub؛ تفاصيل الخطأ محفوظة في التشخيص."
    if _timeout_error(error) or (getattr(error, '_musab_emitted', False) and _retryable(error)):
        return 'انقطع رد المزود ولم تنجح محاولات الاستكمال. حُفظ تقدم المهمة ونتائج الأدوات المنفّذة؛ يمكنك المتابعة من آخر خطوة. تفاصيل الانقطاع في التشخيص.'
    return str(error)


def chat(client, messages, tools=None, required_capabilities=None, excluded_providers=(), **kwargs):
    current_spec = dict(getattr(client, "spec", {}) or {})
    current_spec.setdefault("id", getattr(client, "model_name", "current"))
    current_spec.setdefault("model", getattr(client, "model_name", "current"))
    old_id = _provider_id(current_spec)
    required = request_capabilities(
        messages, tools=tools, required_capabilities=required_capabilities, **kwargs
    )
    errors = []
    incompatible = []

    if not _supports(current_spec, required, legacy_current=True):
        incompatible.append(old_id)
    elif old_id not in excluded_providers and _available(current_spec):
        try:
            return _call_with_retry(current_spec, client.provider, client.model_name, messages, tools, kwargs)
        except (providers.ProviderError, TimeoutError, OSError) as first:
            # Do not fall back after any visible stream event.
            if getattr(first, "_musab_emitted", False):
                raise
            if isinstance(first, InvalidToolResponse):
                messages = messages + [{'role':'user','content':str(first)[:2500] + '\nNo tools were executed. Correct all required arguments.'}]
            errors.append(old_id + ": " + _safe_error(first))
    else:
        errors.append(old_id + (": failed earlier in this request" if old_id in excluded_providers else ": cooling down"))

    fallbacks = [spec for spec in candidates(client, required) if _provider_id(spec) not in excluded_providers]
    if incompatible and not fallbacks:
        names = ", ".join(sorted(required))
        raise providers.ProviderError(
            "No configured free AI provider supports this request (requires: %s)" % names
        )

    for spec in fallbacks:
        next_provider = provider(spec)
        try:
            result = _call_with_retry(spec, next_provider, spec.get("model") or spec["id"], messages, tools, kwargs)
            client.provider = next_provider
            client.model_name = spec.get("model") or spec["id"]
            client.spec = spec
            client._last_fallback = {
                "from": old_id,
                "to": spec["id"],
                "error": errors[-1] if errors else "",
            }
            return result
        except (providers.ProviderError, TimeoutError, OSError) as error:
            if getattr(error, "_musab_emitted", False):
                raise
            errors.append(spec["id"] + ": " + _safe_error(error))

    message = "Free AI providers are temporarily unavailable"
    if errors:
        message += " (" + " | ".join(errors)[:900] + ")"
    raise ProviderUnavailable(message, _next_retry(client, required, excluded_providers))
