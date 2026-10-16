"""Background agents (Cursor's background agents; Claude Code's agents that keep going while you work).

A background agent is an ordinary thread of the app that runs on its own: it starts from a task, works in its own git
worktree when the project is a git repository (so several agents and the user never write over each other), and ends
with a report. The user starts one with /bg <task> or the Agents panel; the agent itself starts them with the
background_agent tool, to split big work into independent parts that run at the same time, then waits for them and
applies their changes.

They run with the thread's model and mode. What they ask approval for shows up wherever the user is in the app; when
one finishes, the app says so (and Android does too when the app is in the background).
"""
import json
import os
import re
import threading
import time

NAMES = ["background_agent"]
MAX_RUNNING = 4
WAIT_LONGEST = 1800
SERVICE = None                       # the app's Service, set when it starts (tools reach the other threads with it)
_LOCK = threading.RLock()

BRIEF = ("\n\n(You are a background agent: you work on your own and nobody is watching this thread. Do the whole task "
         "without asking questions; when something is unclear make the reasonable choice and say which. Verify your "
         "work (tests or running it). End with a short report: what you did, the files you changed, how you checked "
         "it, and anything left to do.)")


class FleetError(ValueError):
    pass


def _path():
    from . import settings
    return os.path.join(settings.HOME, "fleet.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(reg):
    items = sorted(reg.items(), key=lambda kv: kv[1].get("started", 0))[-60:]      # the last 60 are kept
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dict(items), f, ensure_ascii=False)
    os.replace(tmp, p)


def _git_ready(root):
    from . import util
    top = util.git_root(root)
    if not top:
        return False
    code, _ = util.git(top, "rev-parse", "HEAD")
    return code == 0


def _name(prompt):
    words = re.sub(r"\s+", " ", str(prompt or "")).strip()
    return (words[:48] + "…") if len(words) > 48 else words


def is_background(sid):
    with _LOCK:
        return sid in _load()


def start(service, parent, prompt, name="", isolate=None):
    """Starts a background agent from the thread `parent`: (its session id, its name, works in a worktree)."""
    prompt = str(prompt or "").strip()
    if not prompt:
        raise FleetError("a background agent needs a task (prompt)")
    with _LOCK:
        reg = _load()
        if parent.id in reg:
            raise FleetError("a background agent cannot start more background agents: do the work here")
        running = [sid for sid in reg if service.busy(sid)]
        if len(running) >= MAX_RUNNING:
            raise FleetError("%d background agents are already running (the most at once): wait for one, or stop "
                             "one" % len(running))
        root = getattr(parent, "origin", "") or parent.root
        if isolate is None:
            isolate = _git_ready(root)
        try:
            s = service.create(root, model=parent.model, mode=parent.mode, worktree=bool(isolate))
        except ValueError:
            if not isolate:
                raise
            s, isolate = service.create(root, model=parent.model, mode=parent.mode, worktree=False), False
        name = (str(name or "").strip() or _name(prompt))[:60]
        s.title = "⚙ " + name
        s.save_meta()
        reg[s.id] = {"parent": parent.id, "name": name, "prompt": prompt[:4000], "started": time.time(),
                     "worktree": bool(isolate), "root": root}
        _save(reg)
    out = service.send(s.id, prompt + BRIEF)
    if not out.get("started"):
        raise FleetError(out.get("reply") or "the background agent did not start")
    service.broadcast({"type": "fleet", "state": "start", "session": parent.id, "agent": s.id, "name": name})
    return s.id, name, bool(isolate)


def _answer(s):
    for m in reversed(s.messages):
        if m.get("role") == "assistant":
            c = m.get("content")
            if isinstance(c, list):
                c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
            if str(c or "").strip():
                return str(c).strip()
    return ""


def info(service, sid, full=False):
    with _LOCK:
        rec = dict(_load().get(sid) or {})
    if not rec:
        raise FleetError("no background agent %s (background_agent action=list shows them)" % sid)
    try:
        s = service.get(sid)
    except (OSError, ValueError, KeyError):
        rec.update(id=sid, state="gone", answer="", changes=[])
        return rec
    busy = service.busy(sid)
    try:
        changes = [{k: c.get(k) for k in ("path", "status", "plus", "minus")} for c in s.changes()]
    except Exception:  # noqa: BLE001
        changes = []
    answer = _answer(s)
    rec.update(id=sid, state="running" if busy else ("done" if answer else "stopped"), title=s.title,
               steps=s.turn, changes=changes, answer=answer if full else answer[:300],
               updated=getattr(s, "updated", 0))
    return rec


def listing(service, parent=None):
    with _LOCK:
        reg = _load()
    out = []
    for sid, rec in sorted(reg.items(), key=lambda kv: -kv[1].get("started", 0))[:20]:
        if parent and rec.get("parent") != parent:
            continue
        try:
            out.append(info(service, sid))
        except FleetError:
            continue
    return out


def wait(service, ids, cancel=None, longest=WAIT_LONGEST, say=None):
    """Waits until the agents end (or `longest` seconds); returns their info with the full reports."""
    ids = [i for i in ids if i]
    started = time.time()
    while any(service.busy(i) for i in ids):
        if cancel is not None and cancel.is_set():
            break
        if time.time() - started > longest:
            break
        if say:
            say("Waiting for %d background agent(s)…" % sum(1 for i in ids if service.busy(i)))
        if cancel is not None:
            cancel.wait(3)
        else:
            time.sleep(3)
    return [info(service, i, full=True) for i in ids]


def apply(service, sid):
    from .service import apply_worktree
    s = service.get(sid)
    if service.busy(sid):
        raise FleetError("it is still working: wait for it first")
    if not getattr(s, "worktree", False):
        return {"ok": True, "applied": [], "note": "it worked in the project itself: its changes are already there"}
    return apply_worktree(s)


def _report(rec):
    mark = {"running": "⏳ running", "done": "✓ done", "stopped": "■ stopped", "gone": "? gone"}.get(rec["state"],
                                                                                                     rec["state"])
    lines = ["%s  %s  [%s]%s" % (rec["id"], rec.get("name", ""), mark, " (worktree)" if rec.get("worktree") else "")]
    if rec.get("changes"):
        lines.append("  changed: " + ", ".join("%s +%s -%s" % (c["path"], c.get("plus") or 0, c.get("minus") or 0)
                                               for c in rec["changes"][:20]))
    if rec.get("answer"):
        lines.append("  report: " + rec["answer"].replace("\n", "\n  "))
    return "\n".join(lines)


# ------------------------------------------------------------------ the tool

def install():
    from . import tools

    @tools.tool("background_agent", "Background agents: other agents that work at the same time as you, each in its "
                "own git worktree, on a self-contained part of a big job (e.g. 'write the tests for X', 'build the "
                "settings screen'). action=start (prompt: everything it needs, name): returns its id at once; list; "
                "result (id): its report and changed files; wait (ids, comma separated): until they finish, with "
                "their reports; apply (id): copy its changes into the project; stop (id). Give them parts that touch "
                "different files; check and apply their results before you finish.",
                {"action": tools._s("start, list, result, wait, apply or stop"),
                 "prompt": tools._s("start: the whole task, with what it needs to know"),
                 "name": tools._s("start: a short name"),
                 "id": tools._s("result, apply, stop: the agent's id; wait: ids separated by commas"),
                 "isolate": {"type": "boolean", "description": "start: its own git worktree (default: yes in a git "
                                                               "repository)"}},
                ["action"], "meta")
    def t_background_agent(ctx, action, prompt="", name="", id="", isolate=None):  # noqa: A002 - the param's name
        service = SERVICE
        if service is None:
            raise tools.ToolError("background agents need the MusabAI app (its engine runs them)")
        action = str(action or "").strip().lower()
        s = ctx.session
        try:
            if action == "start":
                if getattr(ctx.agent, "depth", 0):
                    raise FleetError("a sub-agent cannot start background agents")
                sid, nm, wt = start(service, s, prompt, name, isolate)
                return ("Background agent %s «%s» started%s. It works on its own; use action=wait id=%s for its "
                        "report, then action=apply." % (sid, nm, " in its own worktree" if wt else "", sid)), \
                    {"agent": sid, "worktree": wt}
            if action == "list":
                items = listing(service, parent=s.id) or listing(service)
                if not items:
                    return "No background agents yet (action=start).", {"agents": 0}
                return "\n".join(_report(r) for r in items), {"agents": len(items)}
            if action == "result":
                return _report(info(service, str(id).strip(), full=True)), {"agent": id}
            if action == "wait":
                ids = [x.strip() for x in str(id or "").split(",") if x.strip()]
                if not ids:
                    ids = [r["id"] for r in listing(service, parent=s.id) if r["state"] == "running"]
                if not ids:
                    return "No background agent is running.", {"agents": 0}
                done = wait(service, ids, cancel=ctx.cancel,
                            say=lambda t: ctx.emit({"type": "status", "text": t}))
                return "\n\n".join(_report(r) for r in done), {"agents": len(done)}
            if action == "apply":
                if getattr(s, "mode", "") == "read-only":
                    raise FleetError("read-only mode: nothing is applied")
                out = apply(service, str(id).strip())
                if out.get("error"):
                    raise FleetError(out["error"])
                applied = out.get("applied") or []
                return (out.get("note") or "Applied %d file(s): %s" % (len(applied), ", ".join(applied[:40]))), out
            if action == "stop":
                service.interrupt(str(id).strip())
                return "Asked %s to stop." % id, {"agent": id}
        except FleetError as e:
            raise tools.ToolError(str(e))
        raise tools.ToolError("action: start, list, result, wait, apply or stop")


# ------------------------------------------------------------------ slash commands and the app's routes

COMMANDS = [
    ("bg", "<task>", "Start a background agent on a task (its own worktree; keeps going while you work)"),
    ("agents", "", "The background agents: running, done, their reports and changes"),
    ("gh", "[owner/repo]", "GitHub at a glance: Actions runs and open pull requests with their checks"),
    ("watch", "[latest | <run id> | pr <n>] [owner/repo]", "Watch a GitHub Actions run until it ends, then say how"),
]


def command(service, s, name, args):
    if name == "bg":
        if not args:
            return {"reply": "Usage: /bg <task>. Example: /bg write tests for the parser and make them pass"}
        try:
            sid, nm, wt = start(service, s, args)
        except FleetError as e:
            return {"reply": str(e)}
        return {"reply": "⚙ Background agent «%s» started%s. It keeps working while you continue here; you will "
                         "see when it finishes. /agents shows them." % (nm, " in its own worktree (apply its changes "
                                                                            "when they are good)" if wt else "")}
    if name == "agents":
        items = listing(service)
        if not items:
            return {"reply": "No background agents yet. Start one: /bg <task>"}
        return {"reply": "\n\n".join(_report(r) for r in items)}
    if name in ("gh", "watch"):
        from . import gh_monitor
        return gh_monitor.command(service, s, name, args)
    return None


def route(service, method, path, q=None, b=None):
    """The app's /api/fleet and /api/gh routes: a JSON-able answer."""
    q, b = q or {}, b or {}
    try:
        if path == "/api/fleet" and method == "GET":
            return {"agents": listing(service)}
        if path == "/api/fleet/start" and method == "POST":
            sid, nm, wt = start(service, service.get(str(b.get("session") or "")), b.get("prompt"), b.get("name") or "")
            return {"ok": True, "id": sid, "name": nm, "worktree": wt}
        if path.startswith("/api/gh/"):
            from . import gh_monitor
            return gh_monitor.route(service, method, path, q, b)
    except (ValueError, OSError, KeyError) as e:
        return {"error": str(e)}
    return {"error": "not found"}
