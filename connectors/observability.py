"""Phase 7 privacy-first observability for MusabAI.

Local JSONL metadata is the default. No prompts, tool arguments, tool output,
credentials, or project file contents are recorded. External OTLP export only
happens through an explicit tool call and only when the user configured a target.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request

from . import tools

NAMES = ["observability_status", "observability_tail", "observability_export"]
_LOCK = threading.RLock()
MAX_BYTES = 5 * 1024 * 1024
MAX_EVENTS = 5000
MAX_TAIL = 200

_ALLOWED = {
    "type", "t", "name", "ok", "seconds", "exit", "model", "id", "local",
    "context", "auto", "done", "progress", "steps", "agent", "sub",
}


def _home():
    root = Path(os.environ.get("NEWAL_OBSERVABILITY_HOME") or (
        Path(os.environ.get("NEWAL_CODE_HOME") or Path.home() / ".newal-code") / "observability"
    ))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_id(root):
    return hashlib.sha256(os.path.abspath(str(root or "")).encode("utf-8")).hexdigest()[:16]


def _path(root):
    return _home() / (_project_id(root) + ".jsonl")


def _session_hash(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:240]
    return str(value)[:240]


def sanitize(event):
    """Keep operational metadata only; intentionally discard free-form payloads."""
    event = dict(event or {})
    clean = {
        "type": str(event.get("type") or "event")[:80],
        "t": float(event.get("t") or time.time()),
        "session": _session_hash(event.get("session")),
    }
    for key in _ALLOWED:
        if key in ("type", "t") or key not in event:
            continue
        value = event[key]
        if key == "id":
            # Tool-call ids and model ids are operational metadata but can be long.
            value = _scalar(value)
        if isinstance(value, (str, bool, int, float)) or value is None:
            clean[key] = _scalar(value)
    return clean


def _rotate(path):
    try:
        if path.stat().st_size <= MAX_BYTES:
            return
    except OSError:
        return
    backup = path.with_suffix(".jsonl.1")
    try:
        backup.unlink(missing_ok=True)
        path.replace(backup)
    except OSError:
        pass


def record(root, event):
    """Best-effort local metadata logging. Observability must never break the agent."""
    try:
        row = sanitize(event)
        path = _path(root)
        with _LOCK:
            _rotate(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return row
    except Exception:
        return None


def _read(root, limit=MAX_TAIL):
    path = _path(root)
    if not path.exists():
        return []
    limit = max(1, min(int(limit or 50), MAX_TAIL))
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(sanitize(row))
    return out


def _configured():
    return {
        "langfuse": bool((os.environ.get("LANGFUSE_OTLP_ENDPOINT") or os.environ.get("LANGFUSE_BASE_URL"))
                         and os.environ.get("LANGFUSE_PUBLIC_KEY")
                         and os.environ.get("LANGFUSE_SECRET_KEY")),
        "phoenix": bool(os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")),
    }


def status(root):
    path = _path(root)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    rows = _read(root, MAX_TAIL)
    return {
        "local": True,
        "path": str(path),
        "bytes": size,
        "recent_events": len(rows),
        "external_export_automatic": False,
        "configured_exporters": _configured(),
        "records_prompts_or_tool_payloads": False,
    }


def _safe_endpoint(value):
    url = urllib.parse.urlparse(str(value or "").strip())
    if url.scheme not in ("http", "https") or not url.netloc:
        raise tools.ToolError("Observability endpoint must be an http(s) URL")
    if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise tools.ToolError("Plain HTTP observability export is allowed only to loopback; use HTTPS otherwise")
    return urllib.parse.urlunparse(url)


def _join(base, suffix):
    return base.rstrip("/") + "/" + suffix.lstrip("/")


def _headers_for(target):
    if target == "langfuse":
        base = os.environ.get("LANGFUSE_OTLP_ENDPOINT") or _join(os.environ.get("LANGFUSE_BASE_URL", ""), "/api/public/otel")
        endpoint = base if base.rstrip("/").endswith("/v1/traces") else _join(base, "/v1/traces")
        public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
        if not public or not secret:
            raise tools.ToolError("Langfuse keys are not configured")
        token = base64.b64encode((public + ":" + secret).encode("utf-8")).decode("ascii")
        return _safe_endpoint(endpoint), {
            "Authorization": "Basic " + token,
            "Content-Type": "application/json",
            "x-langfuse-ingestion-version": "4",
        }
    if target == "phoenix":
        base = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "")
        if not base:
            raise tools.ToolError("Phoenix collector is not configured")
        endpoint = base if base.rstrip("/").endswith("/v1/traces") else _join(base, "/v1/traces")
        headers = {"Content-Type": "application/json"}
        raw = os.environ.get("PHOENIX_CLIENT_HEADERS", "")
        for part in raw.split(","):
            if not part.strip() or "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            if key and "\n" not in key and "\r" not in key:
                headers[key] = value.strip()
        return _safe_endpoint(endpoint), headers
    raise tools.ToolError("target must be langfuse or phoenix")


def _hex_id(seed, length):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


def _otlp(events, project):
    spans = []
    for i, event in enumerate(events):
        stamp = int(float(event.get("t") or time.time()) * 1_000_000_000)
        seed = json.dumps(event, sort_keys=True, ensure_ascii=False) + ":" + str(i)
        attrs = [
            {"key": "musabai.event.type", "value": {"stringValue": str(event.get("type", "event"))}},
            {"key": "musabai.project", "value": {"stringValue": project}},
            {"key": "musabai.session", "value": {"stringValue": str(event.get("session", ""))}},
        ]
        for key in ("name", "model", "agent", "ok", "seconds", "exit", "progress", "steps"):
            if key not in event:
                continue
            value = event[key]
            if isinstance(value, bool):
                otel = {"boolValue": value}
            elif isinstance(value, int):
                otel = {"intValue": str(value)}
            elif isinstance(value, float):
                otel = {"doubleValue": value}
            else:
                otel = {"stringValue": str(value)[:240]}
            attrs.append({"key": "musabai." + key, "value": otel})
        spans.append({
            "traceId": _hex_id(seed + ":trace", 32),
            "spanId": _hex_id(seed + ":span", 16),
            "name": "musabai." + str(event.get("type", "event"))[:80],
            "kind": 1,
            "startTimeUnixNano": str(stamp),
            "endTimeUnixNano": str(stamp + 1_000_000),
            "attributes": attrs,
            "status": {"code": 1 if event.get("ok") is not False else 2},
        })
    return {
        "resourceSpans": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": "MusabAI"}},
                {"key": "service.version", "value": {"stringValue": "phase7"}},
            ]},
            "scopeSpans": [{"scope": {"name": "musabai.local-observability"}, "spans": spans}],
        }]
    }


def export(root, target, limit=100):
    target = str(target or "").strip().lower()
    limit = max(1, min(int(limit or 100), MAX_TAIL))
    events = _read(root, limit)
    if not events:
        raise tools.ToolError("No local observability events are available to export")
    endpoint, headers = _headers_for(target)
    body = json.dumps(_otlp(events, _project_id(root)), separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            code = int(getattr(response, "status", 200))
    except Exception as error:
        raise tools.ToolError("Observability export failed: " + str(error)[:300])
    if code < 200 or code >= 300:
        raise tools.ToolError("Observability export returned HTTP " + str(code))
    return {"ok": True, "target": target, "events": len(events), "status": code}


def install():
    @tools.tool("observability_status",
                "Show local privacy-safe trace status and whether optional exporters are configured. Never returns credentials.",
                {}, [], "read")
    def observability_status(ctx):
        result = status(ctx.session.root)
        return json.dumps(result, ensure_ascii=False), {"observability": "status"}

    @tools.tool("observability_tail",
                "Read recent local observability metadata. Prompts, tool arguments and tool output are not recorded.",
                {"limit": {"type": "integer", "description": "1-200 recent events"}}, [], "read")
    def observability_tail(ctx, limit=50):
        rows = _read(ctx.session.root, limit)
        return json.dumps(rows, ensure_ascii=False), {"observability": "tail", "events": len(rows)}

    @tools.tool("observability_export",
                "Explicitly export recent sanitized metadata as OTLP/HTTP to a configured Langfuse or Phoenix endpoint. No prompt/tool payloads are exported.",
                {
                    "target": {"type": "string", "enum": ["langfuse", "phoenix"], "description": "configured exporter"},
                    "limit": {"type": "integer", "description": "1-200 recent events"},
                }, ["target"], "connector_write")
    def observability_export(ctx, target, limit=100):
        result = export(ctx.session.root, target, limit)
        return json.dumps(result, ensure_ascii=False), {"observability": "export", **result}
