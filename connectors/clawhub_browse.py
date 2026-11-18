"""Browse owner-qualified identifiers from ClawHub's public catalog, one page per tool call."""
from collections import OrderedDict
from hashlib import sha256
import json
import re
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from . import tools


_endpoint = "https://clawhub.ai/api/v1/skills"
_identifier = re.compile(r"[A-Za-z0-9_-]{1,100}")
_lock = Lock()
_pages = OrderedDict()
_cursors = OrderedDict()
_retry_at = 0


def _remember(mapping, key, value):
    mapping[key] = value
    mapping.move_to_end(key)
    if len(mapping) > 128:
        mapping.popitem(last=False)


@tools.tool("clawhub_browse", "Browse public ClawHub listings, newest first. Omit page for the first 20. "
            "Use the returned next_page to continue. These are registry identifiers, not installed skills.",
            {"page": tools._s("Page token from a previous clawhub_browse result")}, [], "net")
def browse(ctx, page=""):
    global _retry_at
    if not isinstance(page, str) or (page and not re.fullmatch(r"[0-9a-f]{24}", page)):
        raise tools.ToolError("Use the next_page token returned by clawhub_browse")
    with _lock:
        cached = _pages.get(page)
        if cached and monotonic() < cached[0]:
            return cached[1]
        if page and page not in _cursors:
            raise tools.ToolError("Page token expired. Start again without a page token.")
        if monotonic() < _retry_at:
            raise tools.ToolError("ClawHub requested a delay. Try again later.")
        cursor = _cursors.get(page)
    params = {"sort": "createdAt", "limit": 20}
    if cursor:
        params["cursor"] = cursor   # Only an opaque cursor previously issued by the registry.
    try:
        response = json.loads(tools._get(_endpoint + "?" + urlencode(params)))
        items, more = response["items"], response["nextCursor"]
        if not isinstance(items, list) or (more is not None and
                (not isinstance(more, str) or not 0 < len(more) <= 8192 or more == cursor)):
            raise ValueError("Invalid catalog page")
        ids = []
        for item in items:
            if isinstance(item, dict):
                owner, slug = item.get("ownerHandle"), item.get("slug")
                if all(isinstance(v, str) and _identifier.fullmatch(v) for v in (owner, slug)):
                    ids.append(owner + "/" + slug)
    except HTTPError as exc:
        if exc.code in (429, 503):
            delay = exc.headers.get("Retry-After", "") if exc.headers else ""
            seconds = int(delay) if re.fullmatch(r"[0-9]{1,9}", delay) else 60
            with _lock:
                _retry_at = max(_retry_at, monotonic() + seconds)
            raise tools.ToolError("ClawHub requested a delay. Retry after %d seconds." % seconds) from exc
        raise tools.ToolError("ClawHub catalog returned HTTP %d" % exc.code) from exc
    except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
        raise tools.ToolError("ClawHub catalog page unavailable") from exc
    token = sha256(more.encode("utf-8")).hexdigest()[:24] if more else None
    output = json.dumps({"catalog_ids": ids, "next_page": token}, ensure_ascii=False)
    result = output, {"count": len(ids), "has_more": more is not None}
    with _lock:
        if token:
            _remember(_cursors, token, more)
        _remember(_pages, page, (monotonic() + 60, result))
    return result
