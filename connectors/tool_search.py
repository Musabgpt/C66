"""Claude Code-style, demand-driven discovery of *already connected* MCP tools.

This is a capability gateway, not an installer. It never trusts registry data as
permission, starts unconfigured servers, or executes a selected tool by itself.
The existing MCP manager and permission/hook checks remain the execution gate.
"""
from __future__ import annotations

import json
import re

from . import tools

NAMES = ["tool_search"]
DEFER_ABOVE = 24
INITIAL_LIMIT = 8
ACTIVE_LIMIT = 48
MAX_QUERY = 300
_WORDS = re.compile(r"[^\W_]{2,}", re.UNICODE)
_NOISE = frozenset((
    "the", "for", "with", "from", "this", "that", "into", "then", "please",
    "use", "tool", "tools", "mcp", "and", "can", "you", "get", "show",
    "phone", "android", "service", "on", "in", "of", "to", "as", "it", "we", "do", "my", "me", "work",
    "من", "في", "على", "هذا", "هذه", "الى", "إلى", "بدي", "عندي", "عبر", "مع",
))


def _name(spec):
    return str((spec.get("function") or {}).get("name") or "")


def _description(spec):
    return str((spec.get("function") or {}).get("description") or "")


def _words(text):
    return {w for w in _WORDS.findall(str(text or "").casefold()) if w not in _NOISE}


def _score(item, query):
    words = _words(query)
    if not words:
        return 0
    name = _name(item).casefold()
    desc = _description(item).casefold()
    searchable = name.replace("__", " ").replace("_", " ").replace("-", " ")
    tokens = _words(searchable)
    description_tokens = _words(desc)
    score = 0
    for word in words:
        if word in tokens:
            score += 9
        elif len(word) >= 4 and word in name:
            score += 5
        if word in description_tokens:
            score += 3
        elif len(word) >= 4 and word in desc:
            score += 1
    exact = str(query or "").strip().casefold()
    if len(exact) > 3 and exact in searchable:
        score += 15
    return score


def _rank(schemas, query, limit):
    seen = set()
    scored = []
    for item in schemas:
        name = _name(item)
        if not name.startswith("mcp__") or name in seen:
            continue
        seen.add(name)
        score = _score(item, query)
        if score:
            scored.append((score, name, item))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [entry[2] for entry in scored[:limit]]


def visible_schemas(schemas, session):
    """Filter only large MCP catalogs; never hide core file/bash/recovery tools.

    Small installations retain historical compatibility. A larger catalog is
    reduced to selected tools plus a bounded relevant initial set, and the model
    can discover more through tool_search in subsequent requests.
    """
    schemas = list(schemas)
    if len(schemas) <= DEFER_ABOVE:
        return schemas
    selected = set(getattr(session, "discovered_mcp_tools", ()) or ())
    objective = str(getattr(session, "active_objective", "") or getattr(session, "goal", "") or "")
    initial = {_name(item) for item in _rank(schemas, objective, INITIAL_LIMIT)}
    selected |= initial
    # Enforce an explicit cap even if the stored session was edited externally.
    return [item for item in schemas if _name(item) in selected][:ACTIVE_LIMIT]


def search_schemas(schemas, query, limit=8):
    query = str(query or "").strip()
    if not query or len(query) > MAX_QUERY:
        raise tools.ToolError("Give a capability query of 1–300 characters")
    if isinstance(limit, bool):
        raise tools.ToolError("limit must be a number")
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise tools.ToolError("limit must be a number") from exc
    limit = max(1, min(limit, 12))
    return _rank(schemas, query, limit)


def _allowed(agent, name):
    agent_def = getattr(agent, "agent_def", None) or {}
    restrictions = agent_def.get("tools") or []
    if not restrictions:
        return True
    explicit = [t for t in restrictions if t.startswith("mcp__")]
    return any(name == entry or name.startswith(entry + "__") for entry in explicit)


def activate(agent, query, limit=8):
    """Expose discovered definitions on the *next model call*, not execute them.

    The same subagent restrictions apply before search and after activation.
    """
    schemas = list(agent.mcp.schemas())
    matches = search_schemas([s for s in schemas if _allowed(agent, _name(s))], query, limit)
    current = [n for n in (getattr(agent.session, "discovered_mcp_tools", None) or [])
               if any(_name(d) == n for d in schemas) and _allowed(agent, n)]
    activated = []
    for item in matches:
        name = _name(item)
        if name not in current and len(current) < ACTIVE_LIMIT:
            current.append(name)
            activated.append(name)
    agent.session.discovered_mcp_tools = current
    if activated:
        agent._schemas = None
        if hasattr(agent.session, "save_meta"):
            agent.session.save_meta()
    return {
        "matched": len(matches), "newly_available": activated,
        "tools": [{"name": _name(item), "description": _description(item)[:220]}
                  for item in matches],
        "note": ("Tools are available on the next model step and still require "
                 "the original permissions. No MCP tool has been invoked."),
    }


def install():
    @tools.tool(
        "tool_search",
        "Search tools offered by already connected MCP servers. Activates matching "
        "tool schemas on the next step; does not install, authorize or execute tools. "
        "Use before a task requiring an MCP tool not visible in the current request.",
        {"query": tools._s("service, capability or action to discover"),
         "limit": tools._i("maximum number of matching tools, 1–12")},
        ["query"], "read",
    )
    def tool_search(ctx, query, limit=8):
        agent = getattr(ctx, "agent", None)
        if agent is None:
            raise tools.ToolError("No active agent to receive the tool definitions")
        return json.dumps(activate(agent, query, limit), ensure_ascii=False)
