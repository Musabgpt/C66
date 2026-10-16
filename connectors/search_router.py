"""Phase 3 SearchRouter: SearXNG discovery -> Crawl4AI deep reading.

Provider URLs are configuration, not secrets, and live in MusabAI's private
home. An optional Crawl4AI bearer token is stored in Android Keystore when the
Android connector host is available, or read from CRAWL4AI_API_TOKEN on desktop.

The router never claims either provider is ready until a real request succeeds.
"""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import phone, settings, tools

NAMES = ["searxng_search", "crawl4ai_read", "search_router"]
PATHS = {
    "/api/search-layer",
    "/api/search-layer/save",
    "/api/search-layer/test",
    "/api/search-layer/remove-token",
}
LOCK = threading.RLock()
MAX_JSON = 4 * 1024 * 1024
MAX_TEXT = 48_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Provider redirects are not accepted")


def _path():
    return Path(settings.HOME) / "search-layer.json"


def _read_config():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _save_config(data):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".search-layer-")
    try:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _base(value, provider):
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    if len(value) > 2048:
        raise ValueError(provider + " URL is too long")
    u = urllib.parse.urlsplit(value)
    loopback = (u.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    if not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("Invalid " + provider + " URL")
    if u.scheme != "https" and not (u.scheme == "http" and loopback):
        raise ValueError(provider + " must use HTTPS, or HTTP on localhost")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path.rstrip("/"), "", ""))


def _target(value):
    value = str(value or "").strip()
    if len(value) > 4096:
        raise tools.ToolError("URL is too long")
    u = urllib.parse.urlsplit(value)
    if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
        raise tools.ToolError("Use an http/https URL")
    return value


def _open_json(url, *, method="GET", body=None, headers=None, timeout=30):
    req = urllib.request.Request(url, method=method, headers={
        "Accept": "application/json",
        "User-Agent": "MusabAI-SearchRouter/1",
        **(headers or {}),
    })
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        req.data = raw
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as response:
            raw = response.read(MAX_JSON + 1)
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as error:
        raise RuntimeError("Provider returned HTTP %s" % error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError):
        raise RuntimeError("Provider is unavailable") from None
    if status < 200 or status >= 300:
        raise RuntimeError("Provider returned HTTP %s" % status)
    if len(raw) > MAX_JSON:
        raise RuntimeError("Provider response exceeds size limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RuntimeError("Provider returned invalid JSON") from None
    return data


def _crawl_token():
    value = os.environ.get("CRAWL4AI_API_TOKEN", "").strip()
    if value:
        return value
    try:
        result = phone.call("provider_secret_get", timeout=15, provider="crawl4ai")
        if result.get("ok"):
            return str(result.get("value") or "")
    except Exception:
        pass
    return ""


def _set_crawl_token(value):
    value = str(value or "").strip()
    if not value:
        return
    if len(value) < 8 or len(value) > 8192 or re.search(r"\s", value):
        raise ValueError("Invalid Crawl4AI token")
    if not phone.available():
        raise ValueError("On desktop set CRAWL4AI_API_TOKEN in the environment; Android stores it in Keystore")
    result = phone.call("provider_secret_set", timeout=15, provider="crawl4ai", value=value)
    if not result.get("ok"):
        raise RuntimeError("Could not store Crawl4AI token securely")


def _remove_crawl_token():
    if phone.available():
        try:
            phone.call("provider_secret_remove", timeout=15, provider="crawl4ai")
        except Exception:
            pass


def _searx(base, query, limit=8, categories="", language="", time_range=""):
    if not base:
        raise tools.ToolError("Configure a SearXNG URL first")
    query = str(query or "").strip()
    if not query or len(query) > 500:
        raise tools.ToolError("Search query must contain 1-500 characters")
    limit = max(1, min(int(limit or 8), 20))
    params = {"q": query, "format": "json"}
    if categories:
        params["categories"] = str(categories)[:120]
    if language:
        params["language"] = str(language)[:32]
    if time_range in {"day", "month", "year"}:
        params["time_range"] = time_range
    data = _open_json(base + "/search?" + urllib.parse.urlencode(params), timeout=25)
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("SearXNG JSON search is not enabled on this instance")
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = str(item.get("title") or "")[:500]
        content = str(item.get("content") or "")[:2000]
        if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
            continue
        out.append({
            "title": title,
            "url": url,
            "snippet": content,
            "engine": str(item.get("engine") or "")[:100],
            "score": item.get("score"),
        })
        if len(out) >= limit:
            break
    return out


def _find_markdown(value):
    best = ""
    stack = [value]
    seen = 0
    while stack and seen < 5000:
        item = stack.pop()
        seen += 1
        if isinstance(item, dict):
            for key, child in item.items():
                if key in {"markdown", "fit_markdown", "raw_markdown"}:
                    if isinstance(child, str) and len(child) > len(best):
                        best = child
                    elif isinstance(child, dict):
                        for field in ("fit_markdown", "raw_markdown", "markdown_with_citations"):
                            text = child.get(field)
                            if isinstance(text, str) and len(text) > len(best):
                                best = text
                if isinstance(child, (dict, list)):
                    stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)
    return best[:MAX_TEXT]


def _crawl(base, url):
    if not base:
        raise tools.ToolError("Configure a Crawl4AI URL first")
    url = _target(url)
    token = _crawl_token()
    headers = {"Authorization": "Bearer " + token} if token else {}
    data = _open_json(base + "/crawl", method="POST", body={"urls": [url]}, headers=headers, timeout=90)
    markdown = _find_markdown(data)
    if not markdown:
        # Keep a bounded structured result rather than inventing extracted text.
        rendered = json.dumps(data, ensure_ascii=False)
        markdown = rendered[:MAX_TEXT]
    return {
        "url": url,
        "markdown": markdown,
        "provider": "crawl4ai",
    }


def _config():
    data = _read_config()
    return {
        "searxng": _base(data.get("searxng", ""), "SearXNG") if data.get("searxng") else "",
        "crawl4ai": _base(data.get("crawl4ai", ""), "Crawl4AI") if data.get("crawl4ai") else "",
    }


def install():
    @tools.tool(
        "searxng_search",
        "Search the web through the user's configured self-hosted SearXNG instance. Returns real URLs/titles/snippets; no API key is required by MusabAI.",
        {
            "query": {"type": "string", "description": "search query"},
            "limit": {"type": "integer", "description": "1-20 results"},
            "categories": {"type": "string", "description": "optional SearXNG categories"},
            "language": {"type": "string", "description": "optional language code"},
            "time_range": {"type": "string", "description": "optional day, month or year"},
        },
        ["query"],
        "net",
    )
    def searxng_search(ctx, query, limit=8, categories="", language="", time_range=""):
        rows = _searx(_config()["searxng"], query, limit, categories, language, time_range)
        return json.dumps(rows, ensure_ascii=False), {"provider": "searxng", "results": len(rows)}

    @tools.tool(
        "crawl4ai_read",
        "Deep-read one http/https page with the configured Crawl4AI server and return bounded Markdown/structured output.",
        {"url": {"type": "string", "description": "page URL"}},
        ["url"],
        "net",
    )
    def crawl4ai_read(ctx, url):
        result = _crawl(_config()["crawl4ai"], url)
        return result["markdown"], {"provider": "crawl4ai", "url": result["url"]}

    @tools.tool(
        "search_router",
        "Automatic research route: search with SearXNG, then optionally deep-read the top results with Crawl4AI.",
        {
            "query": {"type": "string", "description": "research query"},
            "limit": {"type": "integer", "description": "1-20 search results"},
            "deep": {"type": "boolean", "description": "deep-read top results with Crawl4AI"},
            "deep_limit": {"type": "integer", "description": "number of top results to deep-read, 1-3"},
        },
        ["query"],
        "net",
    )
    def search_router(ctx, query, limit=8, deep=False, deep_limit=2):
        cfg = _config()
        rows = _searx(cfg["searxng"], query, limit)
        deep_rows = []
        if deep:
            if not cfg["crawl4ai"]:
                raise tools.ToolError("Deep search requested but Crawl4AI is not configured")
            for row in rows[:max(1, min(int(deep_limit or 2), 3))]:
                try:
                    page = _crawl(cfg["crawl4ai"], row["url"])
                    deep_rows.append({"url": row["url"], "markdown": page["markdown"]})
                except Exception as error:
                    deep_rows.append({"url": row["url"], "error": str(error)[:300]})
        result = {"query": query, "search": rows, "deep": deep_rows}
        return json.dumps(result, ensure_ascii=False)[:MAX_TEXT], {
            "provider": "search-router", "results": len(rows), "deep_reads": len(deep_rows)
        }


def _test_searx(url):
    rows = _searx(url, "MusabAI connectivity test", 1)
    return {"ok": True, "results": len(rows)}


def _test_crawl(url):
    token = _crawl_token()
    headers = {"Authorization": "Bearer " + token} if token else {}
    data = _open_json(url + "/health", headers=headers, timeout=15)
    if not isinstance(data, dict) or str(data.get("status", "")).lower() not in {"ok", "healthy"}:
        raise RuntimeError("Crawl4AI health response is invalid")
    return {"ok": True, "version": str(data.get("version") or "")[:100]}


def route(handler, method, path, body=None):
    if path not in PATHS:
        return False
    try:
        if method == "GET" and path == "/api/search-layer":
            cfg = _config()
            handler._json({
                "searxng": cfg["searxng"],
                "crawl4ai": cfg["crawl4ai"],
                "crawl4ai_token": bool(_crawl_token()),
                "order": ["searxng", "crawl4ai"],
            })
            return True
        if method != "POST" or path == "/api/search-layer":
            handler._json({"error": "Method not allowed"}, 405)
            return True
        data = body or {}
        if path.endswith("/save"):
            cfg = _config()
            if "searxng" in data:
                cfg["searxng"] = _base(data.get("searxng", ""), "SearXNG")
            if "crawl4ai" in data:
                cfg["crawl4ai"] = _base(data.get("crawl4ai", ""), "Crawl4AI")
            with LOCK:
                _save_config(cfg)
            if data.get("crawl4ai_token"):
                _set_crawl_token(data["crawl4ai_token"])
            handler._json({"ok": True})
            return True
        if path.endswith("/remove-token"):
            _remove_crawl_token()
            handler._json({"ok": True})
            return True
        if path.endswith("/test"):
            provider = data.get("provider")
            cfg = _config()
            if provider == "searxng":
                handler._json(_test_searx(cfg["searxng"]))
                return True
            if provider == "crawl4ai":
                handler._json(_test_crawl(cfg["crawl4ai"]))
                return True
            raise ValueError("Unknown search provider")
    except (ValueError, RuntimeError, tools.ToolError, OSError) as error:
        handler._json({"error": str(error)[:400]}, 400)
    return True
