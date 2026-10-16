"""Phase 6 execution routing and a free local scratch executor.

The scratch executor is intentionally honest: it uses an ephemeral working
directory, bounded time/output and a scrubbed environment, but it is not a
container or an OS security boundary. Project files are never copied implicitly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from . import tools

NAMES = ["execution_plan", "sandbox_exec"]
MAX_FILES = 32
MAX_INPUT_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 32_000


def _names(ctx):
    session = getattr(ctx, "session", None)
    return set(getattr(session, "tool_names", None) or ())


def _available_prefix(names, prefix):
    return sorted(name for name in names if name.startswith(prefix))


def plan(names, request=""):
    names = set(names or ())
    text = str(request or "").casefold()
    phoneish = bool(re.search(r"\b(android|phone|termux|apk|appium|هاتف|اندرويد|أندرويد|ترمكس|تطبيق)\b", text))
    remoteish = bool(re.search(r"\b(remote|cloud|e2b|sandbox|سحابة|بعيد|ساندبوكس)\b", text))
    gitish = bool(re.search(r"\b(git|commit|branch|diff|repo|repository|جت|كوميت|فرع|مستودع)\b", text))

    routes = []
    if (phoneish or remoteish) and "runtime_exec" in names:
        routes.append({"backend": "runtime_manager", "tool": "runtime_exec",
                       "reason": "Use the unified runtime manager: Termux for Android shell commands, or an explicitly configured remote runtime."})
    if phoneish and "termux_exec" in names:
        routes.append({"backend": "termux", "tool": "termux_exec",
                       "reason": "Use the phone's existing Termux environment for Android/Linux commands."})
    if phoneish:
        appium = _available_prefix(names, "mcp__appium")
        if appium:
            routes.append({"backend": "appium", "tool": appium[0],
                           "reason": "Use connected Appium only for device automation that needs it."})
    if "sandbox_exec" in names:
        routes.append({"backend": "local_scratch", "tool": "sandbox_exec",
                       "reason": "Run isolated scratch inputs without mutating the project workspace."})
    if remoteish:
        e2b = _available_prefix(names, "mcp__e2b")
        if e2b:
            routes.append({"backend": "e2b", "tool": e2b[0],
                           "reason": "Use E2B only when it is actually connected and exposed."})
    if "bash" in names:
        routes.append({"backend": "project_host", "tool": "bash",
                       "reason": "Run project build/test commands in the current workspace."})
    if gitish:
        for candidate in ("git_status", "git_diff", "git_log", "git_commit"):
            if candidate in names:
                routes.append({"backend": "git", "tool": candidate,
                               "reason": "Use the bounded project Git tool rather than inventing repository state."})
                break

    seen = set()
    unique = []
    for row in routes:
        key = row["tool"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return {
        "request": str(request or "")[:1000],
        "routes": unique[:8],
        "note": "local_scratch is an ephemeral work directory, not an OS/container security boundary; normal permissions still apply.",
    }


def _safe_rel(path):
    p = Path(str(path))
    if p.is_absolute() or not p.parts or ".." in p.parts:
        raise tools.ToolError("Scratch file paths must be safe relative paths")
    return p


def run_scratch(command, files=None, timeout=30):
    if not isinstance(command, str) or not command.strip() or len(command) > 32768:
        raise tools.ToolError("Provide a non-empty command up to 32768 characters")
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        raise tools.ToolError("timeout must be an integer")
    timeout = max(1, min(timeout, 120))
    files = files or {}
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise tools.ToolError("files must be an object with at most 32 UTF-8 files")
    total = 0
    prepared = []
    for name, content in files.items():
        if not isinstance(content, str):
            raise tools.ToolError("Scratch file contents must be UTF-8 text")
        rel = _safe_rel(name)
        total += len(content.encode("utf-8"))
        if total > MAX_INPUT_BYTES:
            raise tools.ToolError("Scratch inputs exceed 1 MB")
        prepared.append((rel, content))

    shell = shutil.which("bash") or shutil.which("sh")
    if not shell:
        raise tools.ToolError("No local shell is available")
    base = os.environ.get("NEWAL_SANDBOX_HOME") or None
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="musabai-scratch-", dir=base) as tmp:
        root = Path(tmp)
        for rel, content in prepared:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(root),
            "TMPDIR": str(root),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", ""),
        }
        argv = [shell, "-lc", command] if Path(shell).name == "bash" else [shell, "-c", command]
        try:
            proc = subprocess.run(argv, cwd=str(root), env=env, capture_output=True, text=True,
                                  timeout=timeout, errors="replace")
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            proc = None
            timed_out = True
            stdout = (exc.stdout or "")
            stderr = (exc.stderr or "")
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
        if proc is not None:
            stdout, stderr = proc.stdout, proc.stderr
            code = proc.returncode
        else:
            code = None
        outputs = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                try:
                    rel = str(path.relative_to(root)).replace(os.sep, "/")
                    size = path.stat().st_size
                except OSError:
                    continue
                outputs.append({"path": rel, "bytes": size})
                if len(outputs) >= 128:
                    break
        return {
            "ok": not timed_out and code == 0,
            "exit_code": code,
            "timed_out": timed_out,
            "stdout": stdout[:MAX_OUTPUT_CHARS],
            "stderr": stderr[:MAX_OUTPUT_CHARS],
            "files": outputs,
            "isolation": "ephemeral-workdir-not-container",
        }


def install():
    @tools.tool(
        "execution_plan",
        "Choose among the unified runtime manager and actual local, Termux, Appium/E2B MCP and Git execution tools exposed to this session. Does not execute.",
        {"request": {"type": "string", "description": "task to route"}},
        ["request"],
        "meta",
    )
    def execution_plan(ctx, request):
        result = plan(_names(ctx), request)
        return json.dumps(result, ensure_ascii=False), {"execution_routes": len(result["routes"])}

    @tools.tool(
        "sandbox_exec",
        "Run a bounded command in a fresh temporary scratch directory using only explicitly supplied UTF-8 files. This prevents accidental project mutation but is not an OS/container security boundary.",
        {
            "command": {"type": "string", "description": "shell command to run in the scratch directory"},
            "files": {"type": "object", "description": "optional mapping of safe relative file paths to UTF-8 content"},
            "timeout": {"type": "integer", "description": "timeout seconds, 1-120"},
        },
        ["command"],
        "exec",
    )
    def sandbox_exec(ctx, command, files=None, timeout=30):
        result = run_scratch(command, files, timeout)
        return json.dumps(result, ensure_ascii=False), {
            "exit": result["exit_code"],
            "timed_out": result["timed_out"],
            "isolation": result["isolation"],
        }
