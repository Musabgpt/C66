"""Claude Code parity layer for the NewAl engine.

* Unique tool schemas: an OpenAI-compatible request with two functions of the same name is rejected by many
  providers (the free gateway routes to several of them), so every request is de-duplicated here.
* Deferred built-in tools: like Claude Code, the model always sees a small core (files, shell, tasks, web, skills)
  and discovers the long tail with ``tool_search``. The full list stays executable and is named in the runtime
  context, so no existing capability is removed; only the per-request schema cost drops.
* Session commands that Claude Code users expect: /context, /todos, /bashes, /doctor and ``!command`` shell mode.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

CORE = frozenset((
    "read", "edit", "apply_patch", "write", "glob", "grep", "bash", "powershell", "job", "todo", "task",
    "skill", "notebook_edit", "web_search", "web_fetch", "tool_search", "memory_recall", "phone", "github",
    "dependency_install", "capability_ensure", "task_checkpoint", "task_resume", "task_complete",
))
DEFER_MIN = 20          # small tool lists are sent whole (deferral would only add a search step)
BANG_TIMEOUT = 120
BANG_OUTPUT = 20000


def _name(spec):
    if not isinstance(spec, dict):
        return ""
    return str((spec.get("function") or {}).get("name") or "")


def enabled(agent):
    cfg = getattr(agent, "cfg", None) or {}
    if cfg.get("defer_tools") is False:
        return False
    # A tiny on-device model already gets the short SMALL_SET (under DEFER_MIN); a subagent keeps its declared tools.
    return not (getattr(agent, "agent_def", None) or {}).get("tools")


def unique(defs):
    seen, out = set(), []
    for d in defs:
        n = _name(d)
        if n and n not in seen:
            seen.add(n)
            out.append(d)
    return out


def deferred_names(agent, defs=None):
    """Built-in tools held back from the request until the model searches for them."""
    if not enabled(agent):
        return []
    from . import connectors
    defs = defs if defs is not None else getattr(agent, "_schemas", None)
    defs = unique(d for d in (defs if isinstance(defs, (list, tuple)) else []) if isinstance(d, dict))
    builtin = [_name(d) for d in defs if not _name(d).startswith("mcp__") and _name(d) not in connectors.OPERATIONS]
    if len(builtin) <= DEFER_MIN:
        return []
    found = set(getattr(agent.session, "discovered_builtin_tools", None) or ())
    return [n for n in builtin if n not in CORE and n not in found]


def finalize(agent, defs):
    """The schemas actually sent: unique names, deferred built-ins removed."""
    defs = unique(defs)
    hidden = set(deferred_names(agent, defs))
    return [d for d in defs if _name(d) not in hidden]


def offered_names(agent):
    """Every tool the agent may use this turn, visible or deferred."""
    names = {_name(d) for d in agent.schemas()}
    names |= set(getattr(agent.session, "tool_names", None) or ())
    return names


def mark_used(agent, name):
    """A deferred built-in the model called directly becomes visible from the next request."""
    s = agent.session
    current = list(getattr(s, "discovered_builtin_tools", None) or [])
    if name in current or name.startswith("mcp__") or name in CORE:
        return
    from . import tools
    if name not in tools.REGISTRY:
        return
    current.append(name)
    s.discovered_builtin_tools = current
    agent._schemas = None


def search_builtin(agent, query, limit=8):
    """Rank deferred built-ins for tool_search and expose the matches on the next step."""
    from . import tools, tool_search
    if getattr(agent, "session", None) is None:
        return []
    hidden = deferred_names(agent, getattr(agent, "_schemas", None) or [])
    if not hidden:
        return []
    pool = [tools.REGISTRY[n].schema() for n in hidden if n in tools.REGISTRY]
    scored = sorted(((tool_search._score(d, query), _name(d), d) for d in pool), key=lambda x: (-x[0], x[1]))
    matches = [d for score, _, d in scored if score][:limit]
    current = list(getattr(agent.session, "discovered_builtin_tools", None) or [])
    for d in matches:
        if _name(d) not in current:
            current.append(_name(d))
    agent.session.discovered_builtin_tools = current
    if matches:
        agent._schemas = None
        if hasattr(agent.session, "save_meta"):
            agent.session.save_meta()
    return matches


def deferred_note(agent):
    hidden = deferred_names(agent, getattr(agent, "_schemas", None) or [])
    if not hidden:
        return ""
    return ("\n\nDeferred tools (call tool_search with a capability to load their schemas, or call them by name): "
            + ", ".join(sorted(hidden)) + ".")


# ------------------------------------------------------------------ commands

COMMANDS = [
    ("context", "", "Show what fills the context window: system prompt, tools, messages"),
    ("todos", "", "Show the task checklist of this thread"),
    ("bashes", "", "List background shell jobs (also /jobs)"),
    ("doctor", "", "Check the engine: Python, git, shell tools, model and tool registry"),
]
ALIASES = {"jobs": "bashes", "tasks": "todos"}


def _tokens(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return max(0, len(text) // 4)


def context_report(agent):
    s = agent.session
    try:
        system = agent.request_messages()[0]["content"] if hasattr(agent, "request_messages") else ""
    except Exception:  # noqa: BLE001 - a report never breaks the thread
        system = agent.session.system or ""
    visible = agent.schemas()
    hidden = deferred_names(agent, agent._schemas or [])
    sys_t, tool_t, msg_t = _tokens(system), _tokens(visible), _tokens(s.messages)
    total = sys_t + tool_t + msg_t
    window = 0
    try:
        window = int(agent.client.context()) if agent.client else 0
    except Exception:  # noqa: BLE001
        window = 0
    window = window or 128000
    bar_n = 30
    filled = min(bar_n, round(bar_n * total / window))
    lines = [
        "Context ≈ %d / %d tokens (%d%%)" % (total, window, round(100 * total / window)),
        "[" + "█" * filled + "░" * (bar_n - filled) + "]",
        "  System prompt  ≈ %6d" % sys_t,
        "  Tools (%2d)     ≈ %6d   · %d deferred, loaded on demand" % (len(visible), tool_t, len(hidden)),
        "  Messages (%3d) ≈ %6d" % (len(s.messages), msg_t),
        "  Free           ≈ %6d" % max(0, window - total),
    ]
    if total > window * 0.7:
        lines.append("Tip: /compact frees space.")
    return "\n".join(lines)


def todos_report(session):
    items = getattr(session, "todo", None) or []
    if not items:
        return "No tasks in this thread."
    mark = {"done": "☑", "completed": "☑", "in_progress": "◐", "doing": "◐"}
    done = sum(1 for i in items if i.get("status") in ("done", "completed"))
    lines = ["Tasks %d/%d done" % (done, len(items))]
    lines += ["  %s %s" % (mark.get(i.get("status"), "☐"), i.get("text") or i.get("content") or "") for i in items]
    return "\n".join(lines)


def bashes_report(session):
    jobs = getattr(session, "jobs", None) or {}
    if not jobs:
        return "No background jobs."
    out = []
    for j in jobs.values():
        proc = getattr(j, "proc", None)
        code = proc.poll() if proc else None
        state = "running" if proc is not None and code is None else "exited %s" % code
        out.append("  %s · %s · %s" % (getattr(j, "id", "?"), state, str(getattr(j, "command", ""))[:100]))
    return "Background jobs:\n" + "\n".join(out)


def doctor_report(agent):
    from . import tools
    checks = []

    def add(ok, label, detail=""):
        checks.append("%s %s%s" % ("✓" if ok else "✗", label, (": " + detail) if detail else ""))

    add(True, "Python", sys.version.split()[0])
    for exe in ("git", "rg", "node"):
        path = shutil.which(exe)
        add(bool(path), exe, path or "not found (optional)")
    try:
        import importlib.util
        add(bool(importlib.util.find_spec("pytest")), "pytest", "unittest is used when it is missing")
    except Exception:  # noqa: BLE001
        pass
    names = [_name(d) for d in agent.schemas()]
    dups = sorted({n for n in names if names.count(n) > 1})
    add(not dups, "unique tool names", ", ".join(dups) if dups else "%d tools" % len(set(names)))
    add(len(tools.REGISTRY) > 0, "tool registry", "%d registered" % len(tools.REGISTRY))
    add(os.access(agent.session.root or ".", os.W_OK), "project writable", agent.session.root or "")
    try:
        from . import models
        spec = models.resolve(agent.session.model)
        add(True, "model", "%s (%s)" % (spec.get("name") or spec.get("id"), spec.get("provider")))
    except Exception as exc:  # noqa: BLE001
        add(False, "model", str(exc)[:200])
    return "\n".join(checks)


def command(service, s, name, args):
    """A parity command's reply, or None when the name is not one of them."""
    name = ALIASES.get(name, name)
    if name not in {n for n, _, _ in COMMANDS}:
        return None
    a = service.agent(s.id)
    if name == "context":
        if a.client is None:
            try:
                a.connect()
            except Exception:  # noqa: BLE001 - the report works without a model
                pass
        return {"reply": context_report(a)}
    if name == "todos":
        return {"reply": todos_report(s)}
    if name == "bashes":
        return {"reply": bashes_report(s)}
    if name == "doctor":
        return {"reply": doctor_report(a)}
    return None


def bang(service, s, command_text):
    """``!cmd``: run a shell command in the project now, show it, and put it in the conversation (Claude Code)."""
    cmd = command_text.strip()
    if not cmd:
        return {"started": False, "reply": "Usage: !<shell command>"}
    if service.busy(s.id):
        return {"started": False, "reply": "busy: this thread is still working (interrupt it first)"}
    if getattr(s, "mode", "") == "read-only":
        return {"started": False, "reply": "Read-only mode: shell commands are off (/mode to change)."}
    shell = os.environ.get("SHELL") or shutil.which("bash") or shutil.which("sh") or "/bin/sh"
    try:
        p = subprocess.run([shell, "-c", cmd], cwd=s.root or None, capture_output=True, text=True,
                           timeout=BANG_TIMEOUT, errors="replace")
        code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code = 124
        out = ((exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
        out += "\n(timed out after %d s)" % BANG_TIMEOUT
    except OSError as exc:
        code, out = 127, str(exc)
    if len(out) > BANG_OUTPUT:
        out = out[:BANG_OUTPUT // 2] + "\n…\n" + out[-BANG_OUTPUT // 2:]
    note = "<bash-input>%s</bash-input>\n<bash-stdout>%s</bash-stdout>\n<bash-exit>%d</bash-exit>" % (cmd, out, code)
    try:
        s.add({"role": "user", "content": "The user ran this shell command themselves:\n" + note})
    except Exception:  # noqa: BLE001 - showing the output still helps
        pass
    return {"started": False, "reply": "$ %s\n%s\n(exit %d)" % (cmd, out.rstrip(), code), "output": True,
            "code": code}


# ------------------------------------------------------------------ workspace: raw files and VS Code

RAW_LIMIT = 25 * 1024 * 1024
VSCODE_PORT = 8780


def raw_file(root, rel):
    """(bytes, mime) of a file inside the project for the chat's previews; ValueError outside it or too large."""
    import mimetypes
    root = os.path.realpath(root or "")
    if not root or not os.path.isdir(root):
        raise ValueError("no project")
    full = os.path.realpath(os.path.join(root, rel or ""))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("outside the project")
    if not os.path.isfile(full):
        raise ValueError("no such file")
    if os.path.getsize(full) > RAW_LIMIT:
        raise ValueError("too large to preview")
    mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
    if mime.startswith("text/html") or mime in ("application/xhtml+xml", "text/javascript", "application/javascript"):
        mime = "text/plain; charset=utf-8"      # project HTML never runs on the app's own origin (previews use srcdoc)
    with open(full, "rb") as f:
        return f.read(), mime


def vscode_up():
    import socket
    try:
        with socket.create_connection(("127.0.0.1", VSCODE_PORT), timeout=0.5):
            return True
    except OSError:
        return False


WORK_RULES = (
    "\n\nWorking style (like Cursor and Claude Code): for work with several steps keep a todo list and tick items as "
    "they finish; read a file before editing it and change only the lines that need it; after changing code run the "
    "project's tests or the program and fix what fails before saying it works; name files as `relative/path.ext` in "
    "answers (the user taps them to preview); keep answers short and lead with the result; when a request is "
    "ambiguous and a wrong guess is costly, ask one short question first.")


def work_rules(agent):
    if (getattr(agent, "agent_def", None) or {}).get("tools"):
        return ""          # subagents keep their own instructions
    return WORK_RULES
