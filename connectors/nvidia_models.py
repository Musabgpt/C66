"""NVIDIA NIM models alongside the existing MusabAI providers.

Only phone-secure Android Keystore credentials are used; no API keys in the APK,
Git repository, model catalog, response JSON or diagnostics.
"""
import json
import threading
import time
import urllib.request

from . import provider_keys

_BASE = "https://integrate.api.nvidia.com/v1"
_CACHE = (0.0, [])
_LOCK = threading.RLock()
_DEFAULT = "nvidia/nemotron-3-ultra-550b-a55b"


def _spec(model):
    name = str(model or "").strip()
    if not name or len(name) > 150 or any(c not in
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:-" for c in name):
        return None
    return {
        "id": "nvidia/" + name, "name": "NVIDIA · " + name,
        "provider": "openai", "preset": "nvidia", "base_url": _BASE,
        "model": name, "secret_id": "nvidia", "api_key_env": "NVIDIA_API_KEY",
        "context": 65536, "free": True,
        "capabilities": ["text", "tools", "streaming", "coding"]
    }


def _fetch_catalog(key):
    req = urllib.request.Request(
        _BASE + "/models",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json",
                 "User-Agent": "MusabAI/Android"})
    with urllib.request.urlopen(req, timeout=5) as reply:
        if reply.status != 200:
            raise ValueError("NVIDIA model catalog unavailable")
        raw = reply.read(2000001)
    if len(raw) > 2000000:
        raise ValueError("NVIDIA catalog exceeds limit")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        return []
    # The NVIDIA catalog includes embedding, reranking, audio and other
    # endpoints. Only expose conversational models in MusabAI's chat picker.
    excluded = ("embed", "rerank", "retrieval", "whisper", "asr",
                "text-to-speech", "speech-to-text", "audio", "image-gen")
    return [item["id"] for item in (data.get("data") or [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
            and not any(word in item["id"].lower() for word in excluded)]


def models():
    """Bounded cached discovery. Failure never removes the default or existing providers."""
    global _CACHE
    default = _spec(_DEFAULT)
    try:
        key = provider_keys.secret("nvidia")
    except Exception:
        return [default]
    if not key:
        return [default]
    with _LOCK:
        if time.monotonic() >= _CACHE[0]:
            try:
                names = _fetch_catalog(key)
                _CACHE = (time.monotonic() + 600, names[:80])
            except Exception:
                # Cache failures briefly; don't block every model menu refresh.
                _CACHE = (time.monotonic() + 60, _CACHE[1])
        names = list(_CACHE[1])
    result = {default["id"]: default}
    for model in names:
        spec = _spec(model)
        if spec is not None:
            result.setdefault(spec["id"], spec)
    return list(result.values())
