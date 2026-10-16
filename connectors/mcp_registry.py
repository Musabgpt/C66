"""Official MCP Registry discovery with safe remote installation.

MusabAI never executes arbitrary package commands obtained from the public
registry. Registry entries are inspected first; only literal HTTPS
streamable-http remotes can be installed automatically. The endpoint must
complete a real MCP initialize + tools/list handshake before it is persisted.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import mcp_config

REGISTRY_ORIGIN = "https://registry.modelcontextprotocol.io"
PATHS = {
    "/api/mcp-registry",
    "/api/mcp-registry/inspect",
    "/api/mcp-registry/install",
}
MAX_BYTES = 2 * 1024 * 1024
_NAME = re.compile(r"^[^\s\x00-\x1f\x7f]{1,240}$")
_LOCAL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Registry redirects are not accepted")


def _read_json(url, timeout=15):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "registry.modelcontextprotocol.io":
        raise ValueError("Registry request must use the official HTTPS origin")
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "MusabAI-MCP-Registry/1",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError("Registry returned HTTP %s" % response.status)
            raw = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError):
        raise RuntimeError("Official MCP Registry is unavailable") from None
    if len(raw) > MAX_BYTES:
        raise RuntimeError("Registry response exceeds the size limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Registry returned invalid JSON") from None
    if not isinstance(data, dict):
        raise RuntimeError("Registry returned an invalid response")
    return data


def _server(record):
    value = record.get("server") if isinstance(record, dict) else None
    if isinstance(value, dict):
        return value
    return record if isinstance(record, dict) else {}


def _public(record):
    server = _server(record)
    name = server.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None
    repository = server.get("repository") if isinstance(server.get("repository"), dict) else {}
    remotes = server.get("remotes") if isinstance(server.get("remotes"), list) else []
    packages = server.get("packages") if isinstance(server.get("packages"), list) else []
    meta = record.get("_meta") if isinstance(record, dict) and isinstance(record.get("_meta"), dict) else {}
    official = meta.get("io.modelcontextprotocol.registry/official")
    official = official if isinstance(official, dict) else {}
    return {
        "name": name,
        "title": str(server.get("title") or name)[:200],
        "description": str(server.get("description") or "")[:1000],
        "version": str(server.get("version") or "")[:100],
        "repository": str(repository.get("url") or "")[:1000],
        "remote_count": len(remotes),
        "package_count": len(packages),
        "status": str(official.get("status") or record.get("status") or "active")[:80],
    }


def search(query="", limit=20):
    query = str(query or "").strip()
    if len(query) > 120:
        raise ValueError("Registry search is limited to 120 characters")
    limit = max(1, min(int(limit or 20), 25))
    params = {"limit": str(limit), "version": "latest"}
    if query:
        params["search"] = query
    data = _read_json(REGISTRY_ORIGIN + "/v0.1/servers?" + urllib.parse.urlencode(params))
    rows = data.get("servers")
    if not isinstance(rows, list):
        raise RuntimeError("Registry response is missing the server list")
    result = []
    for row in rows[:limit]:
        item = _public(row)
        if item:
            result.append(item)
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {"servers": result, "count": len(result), "next_cursor": metadata.get("nextCursor")}


def detail(name):
    name = str(name or "").strip()
    if not _NAME.fullmatch(name):
        raise ValueError("Invalid MCP Registry server name")
    encoded = urllib.parse.quote(name, safe="")
    data = _read_json(REGISTRY_ORIGIN + "/v0.1/servers/" + encoded + "/versions/latest")
    server = _server(data)
    if not server or server.get("name") != name:
        raise RuntimeError("Registry returned a mismatched server")
    return server


def _literal_https_remote(server):
    remotes = server.get("remotes")
    if not isinstance(remotes, list):
        return None
    for remote in remotes:
        if not isinstance(remote, dict) or remote.get("type") != "streamable-http":
            continue
        url = remote.get("url")
        if not isinstance(url, str) or "{" in url or "}" in url:
            continue
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname and not parsed.username and
                not parsed.password and not parsed.query and not parsed.fragment):
            return remote
    return None


def inspect(name):
    server = detail(name)
    remote = _literal_https_remote(server)
    packages = server.get("packages") if isinstance(server.get("packages"), list) else []
    remote_headers = []
    variables = []
    if remote:
        for header in remote.get("headers") or []:
            if isinstance(header, dict) and isinstance(header.get("name"), str):
                remote_headers.append({
                    "name": header["name"][:100],
                    "required": bool(header.get("isRequired")),
                    "secret": bool(header.get("isSecret")),
                    "description": str(header.get("description") or "")[:300],
                })
        if isinstance(remote.get("variables"), dict):
            variables = sorted(str(k)[:100] for k in remote["variables"].keys())
    installable = bool(remote) and not remote_headers and not variables
    reasons = []
    if not remote:
        reasons.append("No literal HTTPS streamable-http remote is published")
    if remote_headers:
        reasons.append("Remote requires configured HTTP headers")
    if variables:
        reasons.append("Remote URL requires template variables")
    if packages:
        reasons.append("Package metadata is shown for review but never executed automatically")
    public = _public(server) or {"name": name, "title": name}
    public.update({
        "installable": installable,
        "remote": remote.get("url") if remote else "",
        "required_headers": remote_headers,
        "variables": variables,
        "packages": [
            {
                "registryType": str(p.get("registryType") or "")[:40],
                "identifier": str(p.get("identifier") or "")[:300],
                "version": str(p.get("version") or "")[:100],
                "transport": str((p.get("transport") or {}).get("type") or "")[:40],
            }
            for p in packages if isinstance(p, dict)
        ][:20],
        "reasons": reasons,
        "policy": "Only literal HTTPS streamable-http remotes are auto-installed; registry package commands are never executed.",
    })
    return public


def _local_name(server_name):
    tail = server_name.rsplit("/", 1)[-1].lower()
    tail = re.sub(r"[^a-z0-9_-]+", "-", tail).strip("-_")
    if not tail or not tail[0].isalpha():
        tail = "mcp-" + tail
    tail = tail[:24].rstrip("-_")
    if not _LOCAL_NAME.fullmatch(tail):
        tail = "registry-mcp"
    return tail


def install(root, server_name, local_name="", token=""):
    info = inspect(server_name)
    if not info["installable"]:
        raise ValueError("This registry entry needs manual configuration: " + "; ".join(info["reasons"]))
    local_name = str(local_name or "").strip() or _local_name(server_name)
    if not _LOCAL_NAME.fullmatch(local_name) or "__" in local_name:
        raise ValueError("Use a local server name such as my-server (24 characters maximum)")
    spec = mcp_config.validate(local_name, info["remote"], token)
    server = mcp_config.HttpServer(local_name, spec, root)
    try:
        server.start(timeout=30)
        tools = len(server.tools)
    finally:
        server.stop()
    with mcp_config.LOCK:
        saved = mcp_config.read(root)
        existing = saved.get(local_name)
        if existing and existing.get("registry") != server_name:
            raise ValueError("A different MCP server already uses this local name")
        saved[local_name] = {
            "spec": spec,
            "tools": tools,
            "tested_at": int(time.time()),
            "registry": server_name,
            "registry_version": info.get("version") or "",
        }
        mcp_config.save(root, saved)
    return {"ok": True, "name": local_name, "tools": tools, "server": server_name}


def _session_root(handler, data):
    sid = data.get("session", "")
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
        raise ValueError("Open a project conversation first")
    return handler.service.get(sid).root


def route(handler, method, path, body=None):
    if path not in PATHS:
        return False
    try:
        data = handler._query() if method == "GET" else (body or {})
        if path == "/api/mcp-registry" and method == "GET":
            handler._json(search(data.get("q", ""), data.get("limit", 20)))
            return True
        if path == "/api/mcp-registry/inspect" and method == "GET":
            handler._json(inspect(data.get("name", "")))
            return True
        if path == "/api/mcp-registry/install" and method == "POST":
            root = _session_root(handler, data)
            handler._json(install(root, data.get("name", ""), data.get("local_name", ""), data.get("token", "")))
            return True
        handler._json({"error": "Method not allowed"}, 405)
    except (ValueError, RuntimeError, OSError, TypeError) as error:
        handler._json({"error": str(error)[:400]}, 400)
    return True
