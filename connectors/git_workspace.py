"""Phase 6 bounded Git operations for the current project workspace.

This adapter uses the Git executable already available on the execution host.
On Android, broader Linux work can be routed to connected Termux. It never
pushes, rewrites history, changes remotes or stores credentials.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from . import tools

NAMES = ["git_status", "git_diff", "git_log", "git_commit"]
MAX_TEXT = 32000


def _root(ctx):
    session = getattr(ctx, "session", None)
    root = Path(getattr(session, "root", "") or "").resolve()
    if not root.is_dir():
        raise tools.ToolError("Open a project workspace first")
    return root


def _git(root, args, timeout=30):
    git = shutil.which("git")
    if not git:
        raise tools.ToolError("Git is not installed on this execution host; use connected Termux when available")
    proc = subprocess.run([git, "-C", str(root), *args], capture_output=True, text=True,
                          timeout=max(1, min(int(timeout), 120)), errors="replace")
    data = {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:MAX_TEXT],
        "stderr": proc.stderr[:MAX_TEXT],
    }
    if proc.returncode != 0:
        raise tools.ToolError((proc.stderr or proc.stdout or "Git command failed")[:4000])
    return data


def _paths(values):
    if values is None:
        return []
    if not isinstance(values, list) or not values or len(values) > 100:
        raise tools.ToolError("paths must contain 1-100 relative project paths")
    out = []
    for value in values:
        p = Path(str(value))
        if p.is_absolute() or ".." in p.parts:
            raise tools.ToolError("Git paths must be relative and remain inside the project")
        out.append(str(p).replace("\\", "/"))
    return out


def install():
    @tools.tool("git_status", "Read Git branch and working-tree status for the current project.",
                {}, [], "read")
    def git_status(ctx):
        result = _git(_root(ctx), ["status", "--porcelain=v1", "-b"])
        return json.dumps(result, ensure_ascii=False), {"git": "status"}

    @tools.tool("git_diff", "Read the current project Git diff. Set cached=true for staged changes.",
                {"cached": {"type": "boolean", "description": "show staged diff"}}, [], "read")
    def git_diff(ctx, cached=False):
        args = ["diff", "--no-ext-diff", "--unified=3"]
        if cached:
            args.append("--cached")
        result = _git(_root(ctx), args)
        return json.dumps(result, ensure_ascii=False), {"git": "diff", "cached": bool(cached)}

    @tools.tool("git_log", "Read recent commits from the current project.",
                {"count": {"type": "integer", "description": "number of commits, 1-50"}}, [], "read")
    def git_log(ctx, count=10):
        count = max(1, min(int(count or 10), 50))
        result = _git(_root(ctx), ["log", f"-{count}", "--date=iso", "--pretty=format:%h%x09%ad%x09%s"])
        return json.dumps(result, ensure_ascii=False), {"git": "log", "count": count}

    @tools.tool(
        "git_commit",
        "Stage explicit relative project paths and create one local Git commit. Does not push, force, rewrite history or change remotes.",
        {
            "message": {"type": "string", "description": "commit message"},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "1-100 relative paths to stage"},
        },
        ["message", "paths"],
        "exec",
    )
    def git_commit(ctx, message, paths):
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise tools.ToolError("Provide a commit message up to 500 characters")
        root = _root(ctx)
        safe = _paths(paths)
        _git(root, ["add", "--", *safe])
        result = _git(root, ["commit", "-m", message.strip()], timeout=60)
        return json.dumps(result, ensure_ascii=False), {"git": "commit"}
