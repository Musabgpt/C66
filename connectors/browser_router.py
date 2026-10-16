"""BrowserToolSelector for MusabAI Phase 2.

The selector does not pretend a browser backend exists. It inspects tools that
are actually exposed to the current session and recommends the highest-priority
route that can satisfy the task:

API -> service-specific MCP -> Playwright MCP -> Browser Use -> open-browser-use.

When a task explicitly requires an already logged-in browser session,
open-browser-use is preferred among browser backends. For visually/interaction
heavy pages Browser Use may be preferred over Playwright. Android's native
Accessibility bridge is an existing device-local fallback after the Phase 2
backends; it is never reported as Playwright/Browser Use.
"""
import json
import re
from urllib.parse import urlsplit

from . import tools

NAMES = ["browser_tool_selector"]

_BROWSER_PREFIXES = {
    "playwright": "mcp__playwright__",
    "browser-use": "mcp__browser-use__",
    "open-browser-use": "mcp__open-browser-use__",
}

_API_HOSTS = (
    (("github.com", "api.github.com"), "github_", "GitHub API"),
    (("gitlab.com",), "gitlab_", "GitLab API"),
    (("drive.google.com",), "drive_", "Google Drive API"),
    (("mail.google.com",), "gmail_", "Gmail API"),
    (("calendar.google.com",), "calendar_", "Google Calendar API"),
    (("docs.google.com",), "docs_", "Google Docs API"),
    (("docs.google.com",), "sheets_", "Google Sheets API"),
    (("notion.so", "www.notion.so", "api.notion.com"), "notion_", "Notion API"),
    (("figma.com", "www.figma.com", "api.figma.com"), "figma_", "Figma API"),
)


def _host(url):
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        host = (urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host


def _matches_host(host, domain):
    return host == domain or host.endswith("." + domain)


def _api_candidate(names, url):
    host = _host(url)
    if not host:
        return None
    try:
        path = (urlsplit(str(url or "")).path or "").lower()
    except ValueError:
        path = ""
    if _matches_host(host, "docs.google.com") and "/spreadsheets/" in path:
        matching = sorted(name for name in names if name.startswith("sheets_"))
        if matching:
            return {
                "route": "api",
                "label": "Google Sheets API",
                "tools": matching,
                "reason": "A connected Google Sheets API is available for this spreadsheet.",
            }
    for domains, prefix, label in _API_HOSTS:
        if any(_matches_host(host, domain) for domain in domains):
            matching = sorted(name for name in names if name.startswith(prefix))
            if matching:
                return {
                    "route": "api",
                    "label": label,
                    "tools": matching,
                    "reason": "A connected first-party/service API is available for this site.",
                }
    return None


def _generic_mcp(names, service):
    hint = re.sub(r"[^a-z0-9_-]+", "-", str(service or "").strip().lower()).strip("-")
    if not hint:
        return None
    reserved = {"playwright", "browser-use", "open-browser-use"}
    if hint in reserved:
        return None
    prefix = "mcp__" + hint + "__"
    matching = sorted(name for name in names if name.startswith(prefix))
    if not matching:
        # A caller may give a human service label while the MCP server name is
        # qualified. Match only the server component; never tool text.
        for name in sorted(names):
            if not name.startswith("mcp__"):
                continue
            parts = name.split("__", 2)
            if len(parts) == 3 and hint in parts[1].lower() and parts[1] not in reserved:
                matching.append(name)
    if not matching:
        return None
    return {
        "route": "mcp",
        "label": "Registered MCP server",
        "tools": matching,
        "reason": "A registered MCP server matching the requested service is already connected.",
    }


def select(names, url="", service="", needs_session=False, complex_ui=False):
    """Return a deterministic route based only on currently exposed tools."""
    names = set(names or ())

    api = _api_candidate(names, url)
    if api:
        return _result(api, names, url, service, needs_session, complex_ui)

    generic = _generic_mcp(names, service)
    if generic:
        return _result(generic, names, url, service, needs_session, complex_ui)

    available = {
        key: sorted(name for name in names if name.startswith(prefix))
        for key, prefix in _BROWSER_PREFIXES.items()
    }

    if needs_session and available["open-browser-use"]:
        choice = {
            "route": "open-browser-use",
            "label": "open-browser-use",
            "tools": available["open-browser-use"],
            "reason": "The task explicitly needs the user's existing logged-in browser session.",
        }
    elif complex_ui and available["browser-use"]:
        choice = {
            "route": "browser-use",
            "label": "Browser Use",
            "tools": available["browser-use"],
            "reason": "The page is marked complex/visual and Browser Use is connected.",
        }
    elif available["playwright"]:
        choice = {
            "route": "playwright",
            "label": "Microsoft Playwright MCP",
            "tools": available["playwright"],
            "reason": "Playwright is the preferred structured browser backend.",
        }
    elif available["browser-use"]:
        choice = {
            "route": "browser-use",
            "label": "Browser Use",
            "tools": available["browser-use"],
            "reason": "Playwright is unavailable; Browser Use is connected.",
        }
    elif available["open-browser-use"]:
        choice = {
            "route": "open-browser-use",
            "label": "open-browser-use",
            "tools": available["open-browser-use"],
            "reason": "Other Phase 2 browser backends are unavailable; session browser control is connected.",
        }
    elif "phone" in names:
        choice = {
            "route": "android-session",
            "label": "Android native browser control",
            "tools": ["phone"],
            "reason": "No Phase 2 MCP browser backend is connected; use the existing on-device Accessibility bridge.",
        }
    else:
        choice = {
            "route": "unavailable",
            "label": "No browser backend",
            "tools": [],
            "reason": "No suitable API, MCP browser server, or Android browser-control tool is currently exposed.",
        }
    return _result(choice, names, url, service, needs_session, complex_ui)


def _result(choice, names, url, service, needs_session, complex_ui):
    availability = {
        "api": bool(_api_candidate(names, url)),
        "service_mcp": bool(_generic_mcp(names, service)),
        "playwright": any(name.startswith(_BROWSER_PREFIXES["playwright"]) for name in names),
        "browser_use": any(name.startswith(_BROWSER_PREFIXES["browser-use"]) for name in names),
        "open_browser_use": any(name.startswith(_BROWSER_PREFIXES["open-browser-use"]) for name in names),
        "android_session": "phone" in names,
    }
    return dict(choice, availability=availability, requirements={
        "needs_existing_session": bool(needs_session),
        "complex_ui": bool(complex_ui),
    })


def install():
    @tools.tool(
        "browser_tool_selector",
        "Choose the safest/most capable browser route that is actually available: API, matching MCP, Playwright, Browser Use, open-browser-use, then Android native fallback.",
        {
            "url": {"type": "string", "description": "Target page URL, if known"},
            "service": {"type": "string", "description": "Connected service or MCP server hint, if known"},
            "needs_session": {"type": "boolean", "description": "True when the task needs an already logged-in real browser session"},
            "complex_ui": {"type": "boolean", "description": "True for visually complex or DOM-hostile pages"},
        },
        [],
        "meta",
    )
    def browser_tool_selector(ctx, url="", service="", needs_session=False, complex_ui=False):
        session = getattr(ctx, "session", None)
        names = set(getattr(session, "tool_names", None) or ())
        result = select(names, url, service, needs_session, complex_ui)
        return json.dumps(result, ensure_ascii=False), {"browser_route": result["route"]}


def route(handler, method, path, body=None):
    if path != "/api/browser-router":
        return False
    if method != "GET":
        handler._json({"error": "Method not allowed"}, 405)
        return True
    try:
        data = handler._query()
        sid = data.get("session", "")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
            raise ValueError("Open a project conversation first")
        session = handler.service.get(sid)
        names = set(getattr(session, "tool_names", None) or ())
        handler._json(select(
            names,
            data.get("url", ""),
            data.get("service", ""),
            str(data.get("needs_session", "")).lower() in {"1", "true", "yes"},
            str(data.get("complex_ui", "")).lower() in {"1", "true", "yes"},
        ))
    except (ValueError, TypeError) as error:
        handler._json({"error": str(error)[:300]}, 400)
    return True
