"""Phase 8 durable local task checkpoints for MusabAI.

Checkpoints are project-scoped summaries stored locally outside the workspace.
They help the agent resume long work after a restart, but they do not execute
work in the background and never bypass normal tool permissions.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

from . import tools

NAMES = ["task_checkpoint", "task_resume", "task_complete", "task_list"]
_LOCK = threading.RLock()
_ID = re.compile(r"[A-Za-z0-9._-]{1,80}")
MAX_TASKS = 64
MAX_HISTORY = 20
MAX_OBJECTIVE = 2000
MAX_FIELD = 4000
MAX_EVIDENCE = 2000


def _home():
    root = Path(os.environ.get("NEWAL_TASK_STATE_HOME") or (
        Path(os.environ.get("NEWAL_CODE_HOME") or Path.home() / ".newal-code") / "tasks"
    ))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_id(root):
    return hashlib.sha256(os.path.abspath(str(root or "")).encode("utf-8")).hexdigest()[:16]


def _path(root):
    return _home() / (_project_id(root) + ".json")


def _text(value, limit=MAX_FIELD):
    return str(value or "").strip()[:limit]


def _task_id(value):
    value = _text(value, 80)
    if value and not _ID.fullmatch(value):
        raise tools.ToolError("task_id may contain only letters, numbers, dot, underscore and dash")
    return value


def _empty():
    return {"version": 1, "tasks": {}}


def _load(root):
    path = _path(root)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise tools.ToolError("Task checkpoint store is unreadable: " + str(error)[:240])
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        raise tools.ToolError("Task checkpoint store has an invalid format")
    return {"version": 1, "tasks": data["tasks"]}


def _save(root, data):
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + str(os.getpid()) + "-" + str(threading.get_ident()))
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)
    temp.write_text(payload, encoding="utf-8")
    os.replace(temp, path)


def _new_id(objective):
    seed = (_text(objective, MAX_OBJECTIVE) + ":" + str(time.time_ns())).encode("utf-8")
    return "task-" + hashlib.sha256(seed).hexdigest()[:12]


def _prune(tasks, keep_id):
    if len(tasks) <= MAX_TASKS:
        return
    removable = sorted(
        (row for row in tasks.values()
         if row.get("id") != keep_id and row.get("status") == "complete"),
        key=lambda row: float(row.get("updated_at") or 0),
    )
    for row in removable:
        if len(tasks) <= MAX_TASKS:
            break
        tasks.pop(row.get("id"), None)
    if len(tasks) > MAX_TASKS:
        active = sorted(
            (row for row in tasks.values() if row.get("id") != keep_id),
            key=lambda row: float(row.get("updated_at") or 0),
        )
        for row in active:
            if len(tasks) <= MAX_TASKS:
                break
            tasks.pop(row.get("id"), None)


def checkpoint(root, task_id="", objective="", progress="", next_step="", blocker="", evidence="", session_id=""):
    with _LOCK:
        data = _load(root)
        tasks = data["tasks"]
        task_id = _task_id(task_id)
        objective = _text(objective, MAX_OBJECTIVE)
        if not task_id:
            task_id = _new_id(objective)
        current = tasks.get(task_id)
        if current is None:
            if not objective:
                raise tools.ToolError("A new checkpoint requires a non-empty objective")
            now = time.time()
            current = {
                "id": task_id,
                "objective": objective,
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "checkpoint": {},
                "history": [],
            }
        elif not isinstance(current, dict):
            raise tools.ToolError("Stored task checkpoint is invalid")
        now = time.time()
        if objective:
            current["objective"] = objective
        if session_id:
            current["session_id"] = _text(session_id, 120)
        current["status"] = "active"
        current["updated_at"] = now
        point = {
            "progress": _text(progress),
            "next_step": _text(next_step),
            "blocker": _text(blocker),
            "evidence": _text(evidence, MAX_EVIDENCE),
            "at": now,
        }
        current["checkpoint"] = point
        history = current.get("history")
        if not isinstance(history, list):
            history = []
        history.append(point)
        current["history"] = history[-MAX_HISTORY:]
        tasks[task_id] = current
        _prune(tasks, task_id)
        _save(root, data)
        return current


def _belongs(row, session_id):
    prefix = "auto-%s-turn-" % str(session_id)[:40]
    return row.get("session_id") == session_id or (
        not row.get("session_id") and (not str(row.get("id", "")).startswith("auto-")
                                      or str(row.get("id", "")).startswith(prefix)))


def resume(root, task_id="", session_id=""):
    with _LOCK:
        data = _load(root)
        task_id = _task_id(task_id)
        tasks = [row for row in data["tasks"].values() if isinstance(row, dict)]
        if task_id:
            row = data["tasks"].get(task_id)
            return row if isinstance(row, dict) else None
        if session_id:
            # Legacy automatic checkpoints encode their owner in the id. Explicit
            # project checkpoints without an owner remain available for migration.
            tasks = [row for row in tasks if _belongs(row, session_id)]
        active = [row for row in tasks if row.get("status") == "active"]
        if not active:
            return None
        return max(active, key=lambda row: float(row.get("updated_at") or 0))


def complete(root, task_id, summary=""):
    with _LOCK:
        data = _load(root)
        task_id = _task_id(task_id)
        if not task_id:
            raise tools.ToolError("task_id is required")
        row = data["tasks"].get(task_id)
        if not isinstance(row, dict):
            raise tools.ToolError("Unknown task checkpoint")
        now = time.time()
        row["status"] = "complete"
        row["updated_at"] = now
        row["summary"] = _text(summary)
        history = row.get("history")
        if not isinstance(history, list):
            history = []
        history.append({"progress": row["summary"], "next_step": "", "blocker": "", "evidence": "", "at": now})
        row["history"] = history[-MAX_HISTORY:]
        _save(root, data)
        return row


def list_tasks(root, include_completed=False, limit=20):
    with _LOCK:
        data = _load(root)
        try:
            limit = max(1, min(int(limit or 20), MAX_TASKS))
        except (TypeError, ValueError):
            raise tools.ToolError("limit must be an integer")
        rows = [row for row in data["tasks"].values() if isinstance(row, dict)]
        if not include_completed:
            rows = [row for row in rows if row.get("status") == "active"]
        rows.sort(key=lambda row: float(row.get("updated_at") or 0), reverse=True)
        return [{
            "id": row.get("id"),
            "objective": row.get("objective", ""),
            "status": row.get("status", "active"),
            "updated_at": row.get("updated_at"),
            "next_step": (row.get("checkpoint") or {}).get("next_step", ""),
            "blocker": (row.get("checkpoint") or {}).get("blocker", ""),
        } for row in rows[:limit]]


def install():
    @tools.tool(
        "task_checkpoint",
        "Persist a bounded local project checkpoint for long work. Stores summaries only; it does not run in the background.",
        {
            "task_id": {"type": "string", "description": "existing checkpoint id; omit to create one"},
            "objective": {"type": "string", "description": "stable task objective; required for a new checkpoint"},
            "progress": {"type": "string", "description": "what has actually been completed"},
            "next_step": {"type": "string", "description": "specific next action"},
            "blocker": {"type": "string", "description": "current blocker, if any"},
            "evidence": {"type": "string", "description": "short verification evidence such as run id, commit or test result"},
        },
        [],
        "meta",
    )
    def task_checkpoint(ctx, task_id="", objective="", progress="", next_step="", blocker="", evidence=""):
        row = checkpoint(ctx.session.root, task_id, objective, progress, next_step, blocker, evidence, session_id=ctx.session.id)
        return json.dumps(row, ensure_ascii=False), {"task_id": row["id"], "checkpointed": True}

    @tools.tool(
        "task_resume",
        "Return one durable local task checkpoint. Omit task_id to resume this conversation's most recently active task.",
        {"task_id": {"type": "string", "description": "checkpoint id; omit for latest active task"}},
        [],
        "read",
    )
    def task_resume(ctx, task_id=""):
        row = resume(ctx.session.root, task_id, session_id=ctx.session.id)
        return json.dumps(row or {"active": False}, ensure_ascii=False), {
            "task_id": row.get("id") if row else None,
            "active": bool(row),
        }

    @tools.tool(
        "task_complete",
        "Mark a durable local task checkpoint complete after the result has been verified.",
        {
            "task_id": {"type": "string", "description": "checkpoint id"},
            "summary": {"type": "string", "description": "short verified completion summary"},
        },
        ["task_id"],
        "meta",
    )
    def task_complete(ctx, task_id, summary=""):
        row = complete(ctx.session.root, task_id, summary)
        return json.dumps(row, ensure_ascii=False), {"task_id": row["id"], "completed": True}

    @tools.tool(
        "task_list",
        "List bounded task checkpoint summaries for the current project.",
        {
            "include_completed": {"type": "boolean", "description": "include completed checkpoints"},
            "limit": {"type": "integer", "description": "1-64 rows"},
        },
        [],
        "read",
    )
    def task_list(ctx, include_completed=False, limit=20):
        rows = list_tasks(ctx.session.root, include_completed, limit)
        return json.dumps(rows, ensure_ascii=False), {"tasks": len(rows)}


def route(handler, method, path, body=None):
    if path != '/api/task-state':
        return False
    if method != 'GET':
        handler._json({'error': 'Method not allowed'}, 405)
        return True
    try:
        sid = handler._query().get('session', '')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
            raise ValueError('Open a conversation before viewing its tasks')
        service = handler.service
        sess = service.get(sid)
        with _LOCK:
            rows = [r for r in _load(sess.root)['tasks'].values() if isinstance(r, dict) and _belongs(r, sid)]
            rows.sort(key=lambda r: float(r.get('updated_at') or 0), reverse=True)
            rows = [{key: row.get(key) for key in ("id", "objective", "status", "updated_at", "checkpoint")}
                    for row in rows[:20]]
        worker = getattr(service, 'threads', {}).get(sid)
        running = bool(worker and worker.is_alive())
        owner = getattr(service, 'agents', {}).get(sid)
        supervisor = getattr(owner, 'supervisor', None)
        active = resume(sess.root, session_id=sid)
        handler._json({'tasks': rows, 'active_task': active.get('id') if active else '',
                       'running': running, 'supervisor': supervisor.snapshot() if running and supervisor else {}})
    except (ValueError, KeyError, OSError, tools.ToolError) as error:
        handler._json({'error': str(error)}, 400)
    return True
