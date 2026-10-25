"""Persistent scheduled reminders, autonomous phone tasks and condition watches.

Schedules are stored in the application's private SQLite database. The scheduler
runs with MusabAI's foreground Python service; Android can delay or stop it, so
this is best-effort scheduling (never promise exact delivery during force-stop).
"""
import datetime as dt
from contextlib import contextmanager
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from . import settings, tools

NAMES = ["scheduled_task_create", "scheduled_task_list", "scheduled_task_pause",
         "scheduled_task_resume", "scheduled_task_delete"]
KINDS = ("reminder", "agent", "watch")
EXECUTION_MODES = ("full-auto", "read-only")
MAX_TASKS = 100
MAX_PROMPT = 4000
MIN_REPEAT = 60  # minute; avoid runaway external checks


def db_path():
    return Path(settings.HOME) / "scheduled-tasks.sqlite3"


@contextmanager
def _connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("""CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
        kind TEXT NOT NULL, created REAL NOT NULL, next_run REAL NOT NULL,
        repeat_seconds INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
        last_run REAL, last_result TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
        session_id TEXT NOT NULL DEFAULT '', root TEXT NOT NULL DEFAULT '',
        permission_mode TEXT NOT NULL DEFAULT 'full-auto'
    )""")
    # Older versions created these jobs as forced read-only. The user explicitly
    # chose autonomous execution; restore this capability for their saved tasks.
    columns = {rec["name"] for rec in db.execute("PRAGMA table_info(scheduled_tasks)")}
    if "permission_mode" not in columns:
        db.execute("ALTER TABLE scheduled_tasks ADD COLUMN permission_mode TEXT "
                   "NOT NULL DEFAULT 'full-auto'")
    try:
        with db:
            yield db
    finally:
        db.close()


def _dict(row):
    return dict(row) if row is not None else None


def listing():
    with _connect() as db:
        return [_dict(r) for r in db.execute(
            "SELECT * FROM scheduled_tasks ORDER BY CASE status WHEN 'active' THEN 0 "
            "WHEN 'running' THEN 1 ELSE 2 END, next_run ASC").fetchall()]


def _at(when, delay_minutes, repeat_minutes):
    # Absolute values need an explicit UTC offset, so the phone's local time
    # is never silently mistaken for the server timezone.
    now = time.time()
    if when:
        stamp = str(when).strip().replace("Z", "+00:00")
        value = dt.datetime.fromisoformat(stamp)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("when must include timezone, e.g. 2026-11-01T09:00:00+00:00")
        due = value.timestamp()
    elif delay_minutes:
        due = now + max(1, int(delay_minutes)) * 60
    elif repeat_minutes:
        due = now + int(repeat_minutes) * 60
    else:
        raise ValueError("Specify when (timezone-aware ISO date) or delay_minutes")
    if due <= now:
        raise ValueError("Task time must be in the future")
    if due > now + 10 * 365 * 86400:
        raise ValueError("Task date must be within ten years")
    return due


def create(title, prompt, when="", delay_minutes=0, repeat_minutes=0,
           kind="reminder", root="", permission_mode="full-auto"):
    title, prompt = str(title or "").strip(), str(prompt or "").strip()
    if not title or len(title) > 140:
        raise ValueError("A task title (1-140 characters) is required")
    if not prompt or len(prompt) > MAX_PROMPT:
        raise ValueError("Task instructions must be 1-4000 characters")
    if kind not in KINDS:
        raise ValueError("kind must be reminder, agent or watch")
    if permission_mode not in EXECUTION_MODES:
        raise ValueError("permission_mode must be full-auto or read-only")
    repeat_minutes = int(repeat_minutes or 0)
    if repeat_minutes and (repeat_minutes < MIN_REPEAT or repeat_minutes > 525600):
        raise ValueError("Recurring tasks must be every 60-525600 minutes")
    if kind == "watch" and not repeat_minutes:
        raise ValueError("Condition watches require repeat_minutes >= 60")
    due = _at(when, delay_minutes, repeat_minutes)
    task = dict(id=uuid.uuid4().hex[:16], title=title, prompt=prompt, kind=kind,
                created=time.time(), next_run=due, repeat_seconds=repeat_minutes * 60,
                status="active", last_run=None, last_result="", attempts=0,
                session_id="", root=str(root or "")[:1000],
                permission_mode=permission_mode)
    with _connect() as db:
        count = db.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE status IN "
                           "('active', 'running', 'paused')").fetchone()[0]
        if count >= MAX_TASKS:
            raise ValueError("Maximum 100 active/paused tasks. Delete old tasks first.")
        db.execute("""INSERT INTO scheduled_tasks
                   (id,title,prompt,kind,created,next_run,repeat_seconds,status,
                    last_run,last_result,attempts,session_id,root,permission_mode)
                   VALUES (:id,:title,:prompt,:kind,:created,:next_run,:repeat_seconds,:status,
                           :last_run,:last_result,:attempts,:session_id,:root,:permission_mode)""", task)
    return task


def manage(task_id, action):
    if not re.fullmatch(r"[0-9a-f]{16}", str(task_id)):
        raise ValueError("Invalid task ID")
    if action not in ("pause", "resume", "delete", "run"):
        raise ValueError("Unsupported task action")
    with _connect() as db:
        row = db.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Task not found")
        if action == "delete":
            db.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
            return {"ok": True, "deleted": task_id}
        if row["status"] == "running":
            raise ValueError("Running task cannot be changed mid-execution")
        if action == "pause":
            db.execute("UPDATE scheduled_tasks SET status='paused' WHERE id=?", (task_id,))
        elif action == "resume":
            # Missed events run at next safe opportunity, not a burst of stale occurrences.
            next_run = row["next_run"]
            if next_run <= time.time():
                next_run = time.time() + 5
            db.execute("UPDATE scheduled_tasks SET status='active', attempts=0, "
                       "next_run=? WHERE id=?", (next_run, task_id))
        else:
            db.execute("UPDATE scheduled_tasks SET status='active', next_run=? WHERE id=?",
                       (time.time() + 1, task_id))
        return _dict(db.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone())


def _claim(now=None):
    now = time.time() if now is None else now
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM scheduled_tasks WHERE status='active' "
                         "AND next_run<=? ORDER BY next_run LIMIT 1", (now,)).fetchone()
        if row is None:
            return None
        db.execute("UPDATE scheduled_tasks SET status='running' WHERE id=?", (row["id"],))
        return _dict(row)


def _complete(row, result, session="", at=None, triggered=False):
    now = time.time() if at is None else at
    period = int(row["repeat_seconds"])
    if period:
        # Skip missed recurrences: one execution, never a storm after restart.
        next_run = float(row["next_run"]) + max(1, math.floor(
            (now - float(row["next_run"])) / period) + 1) * period
    else:
        next_run = float(row["next_run"])
    # Condition watches are one-shot triggers: do not repeat a potentially
    # external side effect hourly after the condition becomes true.
    again = bool(period) and not (row["kind"] == "watch" and triggered)
    with _connect() as db:
        db.execute("UPDATE scheduled_tasks SET status=?, next_run=?, last_run=?, "
                   "last_result=?, session_id=?, attempts=0 WHERE id=? AND status='running'",
                   ("active" if again else "done", next_run, now,
                    str(result or "")[:4000], session, row["id"]))


def _failure(row, error):
    n = int(row["attempts"]) + 1
    with _connect() as db:
        db.execute("UPDATE scheduled_tasks SET status=?, next_run=?, last_run=?, "
                   "last_result=?, attempts=? WHERE id=? AND status='running'",
                   ("paused" if n >= 3 else "active", time.time() + 60 * n,
                    time.time(), "Failed: " + str(error)[:2500], n, row["id"]))


def _notify(title, body):
    # PhoneServer is loopback-only and authenticates using the app's local key.
    # No remote push/paid service, nor arbitrary shell code in a notification.
    key = os.environ.get("NEWAL_PHONE_KEY") or ""
    if not key:
        raise RuntimeError("Android phone notification key unavailable")
    port = int(os.environ.get("NEWAL_PHONE_PORT") or 8796)
    data = json.dumps({"action": "notify", "title": title[:140],
                       "text": str(body or "")[:2200], "only_background": False},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d/phone" % port, data=data,
                                 headers={"X-NewAl-Key": key,
                                          "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.loads(response.read(20000).decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError("Notification not posted: " + str(result.get("error")))


def _run_agent(service, row):
    root = row.get("root") or os.environ.get("HOME") or str(Path.home())
    if not os.path.isdir(root):
        root = os.environ.get("HOME") or str(Path.home())
    mode = row.get("permission_mode") or "full-auto"
    session = service.create(root, mode=mode, worktree=False)
    prompt = row["prompt"]
    if row["kind"] == "watch":
        prompt = ("This is a scheduled condition-triggered task explicitly authorized by the user. "
                  "First determine whether the condition is satisfied. If not, reply exactly NO_CHANGE "
                  "and take no action. If it is satisfied, perform only the requested actions using "
                  "the enabled phone, shell, GitHub and other tools, verify the result, then report it. "
                  "A successful trigger stops this watch to prevent repeated side effects. "
                  "Respect Android permissions and do not invent capabilities.\n"
                  "Condition and requested actions:\n" + prompt)
    else:
        prompt = ("This scheduled task was requested by the user. Execute it now, rather than just "
                  "describing what to do. Use the phone tool for alarms/apps/settings and the actual "
                  "coding, GitHub and shell tools for software tasks when available. "
                  "Verify each action and report actual outcomes; respect missing Android permissions "
                  "and existing safeguards for operations beyond the user's stated request.\n"
                  "User's task:\n" + prompt)
    if mode == "read-only":
        prompt = ("This individual scheduled task was explicitly configured as read-only. "
                  "Inspect and report; do not perform modifications or external actions.\n" + prompt)
    answer = service.agent(session.id).run(prompt)
    return str(answer or ""), session.id


def run_one(service, now=None):
    row = _claim(now)
    if row is None:
        return False
    try:
        if row["kind"] == "reminder":
            text, sid = row["prompt"], ""
            _notify(row["title"], text)
        else:
            text, sid = _run_agent(service, row)
            if row["kind"] != "watch" or text.strip() != "NO_CHANGE":
                _notify(row["title"], text or "(No response from the configured model)")
        _complete(row, text, sid, at=now,
                  triggered=(row["kind"] == "watch" and text.strip() != "NO_CHANGE"))
    except Exception as exc:  # Keep the task for bounded retry; never call it a success.
        _failure(row, exc)
    return True


def recover_incomplete():
    """A service crash can leave a task marked running; reschedule it exactly once.

    Execution and notification delivery are best-effort, not exactly-once when
    Android kills the process between posting an alert and recording success.
    """
    with _connect() as db:
        db.execute("UPDATE scheduled_tasks SET status='active', next_run=? "
                   "WHERE status='running'", (time.time() + 10,))


def start(service):
    """Start only in the packaged app (or explicit tests), never all test Services."""
    if not (os.environ.get("NEWAL_PACKAGED_ENGINE") or
            os.environ.get("MUSABAI_SCHEDULED_TASKS") == "1"):
        return None
    recover_incomplete()
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                if not run_one(service):
                    stop.wait(10)
            except Exception:
                stop.wait(10)

    threading.Thread(target=worker, name="musabai-scheduled-tasks", daemon=True).start()
    return stop


def route(handler, method, path, body=None):
    if not path.startswith("/api/scheduled-tasks"):
        return False
    try:
        if path == "/api/scheduled-tasks" and method == "GET":
            handler._json({"tasks": listing(), "scheduler": bool(
                os.environ.get("NEWAL_PACKAGED_ENGINE") or
                os.environ.get("MUSABAI_SCHEDULED_TASKS") == "1")})
            return True
        if path == "/api/scheduled-tasks" and method == "POST":
            args = dict(body or {})
            obj = create(title=args.get("title"), prompt=args.get("prompt"),
                         when=args.get("when") or "",
                         delay_minutes=args.get("delay_minutes") or 0,
                         repeat_minutes=args.get("repeat_minutes") or 0,
                         kind=args.get("kind") or "reminder", root=args.get("root") or "",
                         permission_mode=args.get("permission_mode") or "full-auto")
            handler._json({"ok": True, "task": obj})
            return True
        match = re.fullmatch(r"/api/scheduled-tasks/([0-9a-f]{16})/(pause|resume|delete|run)", path)
        if method == "POST" and match:
            handler._json(manage(match.group(1), match.group(2)))
            return True
        handler._json({"error": "Unknown scheduled task route"}, 404)
    except (ValueError, TypeError, OverflowError) as exc:
        handler._json({"error": str(exc)}, 400)
    except Exception as exc:
        handler._json({"error": "Scheduled task error: " + str(exc)}, 500)
    return True


@tools.tool("scheduled_task_create",
            "Create an actual persisted scheduled task on this phone. For a reminder it notifies at its due time; "
            "for agent it executes the user's requested actions with normal full agent tools, including "
            "phone alarms, apps, GitHub, coding and Termux, unless permission_mode=read-only was chosen. "
            "watch checks a condition and performs the authorized task on first trigger, then stops. "
            "For a simple reminder use kind=reminder (only posts a notification); for a phone action "
            "such as setting an alarm use kind=agent. Provide timezone-aware when or delay_minutes; "
            "repeat_minutes >=60 for recurrence. The app service must stay running; Android may delay delivery.",
            {"title": tools._s("Short notification title"),
             "prompt": tools._s("Reminder text, research request or condition to check"),
             "when": tools._s("Future ISO-8601 datetime WITH timezone; omit if delay_minutes or repeat_minutes"),
             "delay_minutes": tools._i("Minutes from now until the first execution; omit if when"),
             "repeat_minutes": tools._i("0 for once; >=60 for recurring task"),
             "kind": {"type": "string", "enum": list(KINDS)},
             "permission_mode": {"type": "string", "enum": list(EXECUTION_MODES)}},
            ["title", "prompt"], "edit")
def tool_create(ctx, title, prompt, when="", delay_minutes=0, repeat_minutes=0, kind="reminder",
                permission_mode="full-auto"):
    if getattr(getattr(ctx, "session", None), "mode", "") == "read-only":
        raise tools.ToolError("Read-only mode cannot create scheduled tasks")
    try:
        task = create(title, prompt, when, delay_minutes, repeat_minutes, kind,
                      root=getattr(ctx, "root", ""), permission_mode=permission_mode)
    except (ValueError, OverflowError) as exc:
        raise tools.ToolError(str(exc)) from exc
    return json.dumps(task, ensure_ascii=False), task


@tools.tool("scheduled_task_list", "List saved scheduled tasks, next run, status and last result.",
            {}, [], "read")
def tool_list(ctx):
    rows = listing()
    return json.dumps(rows, ensure_ascii=False), {"tasks": rows}


def _command(ctx, task_id, command):
    if getattr(getattr(ctx, "session", None), "mode", "") == "read-only":
        raise tools.ToolError("Read-only mode cannot change scheduled tasks")
    try:
        result = manage(task_id, command)
    except ValueError as exc:
        raise tools.ToolError(str(exc)) from exc
    return json.dumps(result, ensure_ascii=False), result


@tools.tool("scheduled_task_pause", "Pause a scheduled task by ID (preserves its details).",
            {"task_id": tools._s("ID from scheduled_task_list")}, ["task_id"], "edit")
def tool_pause(ctx, task_id):
    return _command(ctx, task_id, "pause")


@tools.tool("scheduled_task_resume", "Resume a paused scheduled task by ID.",
            {"task_id": tools._s("ID from scheduled_task_list")}, ["task_id"], "edit")
def tool_resume(ctx, task_id):
    return _command(ctx, task_id, "resume")


@tools.tool("scheduled_task_delete", "Permanently delete a scheduled task by ID.",
            {"task_id": tools._s("ID from scheduled_task_list")}, ["task_id"], "edit")
def tool_delete(ctx, task_id):
    return _command(ctx, task_id, "delete")
