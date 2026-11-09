#!/usr/bin/env python3
"""
backend.py - Mobile AI Agent backend (FastAPI).

A self-hosted personal assistant that runs on the user's own Android phone
inside Termux. It exposes:

  * A ReAct tool-calling agent driven by a free-tier AI router
    (Gemini -> OpenRouter -> Groq, automatic fallback on quota).
  * Live browser tools (Playwright) with a screenshot stream to the UI.
  * A github_tool (create / clone / read / edit / commit / push).
  * A termux_tool (shell, notifications, clipboard, app launch, volume).
  * An APScheduler-backed scheduler (SQLite) so the user can say, in chat,
    "every day at 9am open the browser, check X, push the result to GitHub
    and notify me" and have it run in the background with an Android
    notification on completion.

Run with:  uvicorn backend:app --host 127.0.0.1 --port 8900
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR", HERE / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "agent.db"
JOBS_DB_PATH = DATA_DIR / "jobs.db"

HOST = os.environ.get("AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENT_PORT", "8900"))

# Agent limits
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "14"))
TOOL_OUTPUT_LIMIT = 6000


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clip(text: str, limit: int = TOOL_OUTPUT_LIMIT) -> str:
    text = text if isinstance(text, str) else str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


# --------------------------------------------------------------------------
# AI Router  (OpenAI-compatible chat completions with provider fallback)
# --------------------------------------------------------------------------

class Provider:
    def __init__(self, name: str, base: str, key: str, model: str, extra_headers: dict | None = None):
        self.name = name
        self.base = base.rstrip("/")
        self.key = key
        self.model = model
        self.extra_headers = extra_headers or {}

    @property
    def enabled(self) -> bool:
        return bool(self.key)


class AIRouter:
    """Tries providers in order, falling through on quota / failure."""

    def __init__(self) -> None:
        self.providers: list[Provider] = []
        # 1) Gemini free tier (OpenAI-compatible endpoint)
        self.providers.append(Provider(
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            os.environ.get("GEMINI_API_KEY", ""),
            os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        ))
        # 2) OpenRouter free models
        self.providers.append(Provider(
            "openrouter",
            "https://openrouter.ai/api/v1",
            os.environ.get("OPENROUTER_API_KEY", ""),
            os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
            {"HTTP-Referer": "http://localhost", "X-Title": "Mobile AI Agent"},
        ))
        # 3) Groq
        self.providers.append(Provider(
            "groq",
            "https://api.groq.com/openai/v1",
            os.environ.get("GROQ_API_KEY", ""),
            os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        ))
        self.last_used: Optional[str] = None

    @property
    def active(self) -> list[Provider]:
        return [p for p in self.providers if p.enabled]

    def status(self) -> dict:
        return {
            "providers": [
                {"name": p.name, "model": p.model, "configured": p.enabled}
                for p in self.providers
            ],
            "last_used": self.last_used,
            "any_configured": bool(self.active),
        }

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       temperature: float = 0.3) -> dict:
        active = self.active
        if not active:
            raise RuntimeError(
                "No AI provider configured. Set GEMINI_API_KEY, OPENROUTER_API_KEY "
                "or GROQ_API_KEY in the environment (all have free tiers)."
            )
        errors = []
        for p in active:
            payload: dict[str, Any] = {
                "model": p.model,
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            headers = {"Authorization": f"Bearer {p.key}", "Content-Type": "application/json"}
            headers.update(p.extra_headers)
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    r = await client.post(f"{p.base}/chat/completions", json=payload, headers=headers)
                if r.status_code in (429, 402, 403, 500, 502, 503, 529):
                    errors.append(f"{p.name}: HTTP {r.status_code} {clip(r.text, 200)}")
                    continue
                r.raise_for_status()
                self.last_used = p.name
                data = r.json()
                data["_provider"] = p.name
                return data
            except httpx.HTTPStatusError as e:
                errors.append(f"{p.name}: {e.response.status_code} {clip(e.response.text, 200)}")
                continue
            except Exception as e:  # network / timeout -> next provider
                errors.append(f"{p.name}: {type(e).__name__}: {e}")
                continue
        raise RuntimeError("All AI providers failed:\n" + "\n".join(errors))


router = AIRouter()


# --------------------------------------------------------------------------
# Browser manager (Playwright) with live screenshot broadcast
# --------------------------------------------------------------------------

class WSHub:
    """Fan-out of JSON messages to a set of websocket clients."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def add(self, ws: WebSocket) -> None:
        self.clients.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


browser_hub = WSHub()


class BrowserManager:
    def __init__(self) -> None:
        self.pw = None
        self.browser = None
        self.page = None
        self.lock = asyncio.Lock()
        self.current_url = ""
        self._streamer: Optional[asyncio.Task] = None

    @property
    def available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except Exception:
            return False

    async def ensure(self):
        if self.page is not None:
            return self.page
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        launch: dict[str, Any] = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        }
        exe = (os.environ.get("CHROMIUM_BIN")
               or shutil.which("chromium")
               or shutil.which("chromium-browser"))
        if exe:
            launch["executable_path"] = exe
        self.browser = await self.pw.chromium.launch(**launch)
        ctx = await self.browser.new_context(
            viewport={"width": 390, "height": 760},
            user_agent=("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"),
        )
        self.page = await ctx.new_page()
        if self._streamer is None:
            self._streamer = asyncio.create_task(self._stream_loop())
        return self.page

    async def _stream_loop(self) -> None:
        """Push a screenshot to the browser tab roughly once a second."""
        while True:
            try:
                await asyncio.sleep(1.1)
                if self.page is None or not browser_hub.clients:
                    continue
                shot = await self.page.screenshot(type="jpeg", quality=55)
                await browser_hub.broadcast({
                    "type": "screenshot",
                    "data": base64.b64encode(shot).decode(),
                    "url": self.current_url,
                    "ts": time.time(),
                })
            except Exception:
                await asyncio.sleep(1.0)

    async def _push_shot(self) -> None:
        if self.page is None:
            return
        try:
            shot = await self.page.screenshot(type="jpeg", quality=60)
            await browser_hub.broadcast({
                "type": "screenshot",
                "data": base64.b64encode(shot).decode(),
                "url": self.current_url,
                "ts": time.time(),
            })
        except Exception:
            pass

    async def open(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        async with self.lock:
            page = await self.ensure()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            self.current_url = page.url
            await self._push_shot()
            title = await page.title()
            return f"Opened {page.url}\nTitle: {title}"

    async def click(self, target: str) -> str:
        async with self.lock:
            page = await self.ensure()
            # Try as CSS selector first, then by visible text.
            try:
                await page.click(target, timeout=6000)
            except Exception:
                try:
                    await page.get_by_text(target, exact=False).first.click(timeout=6000)
                except Exception as e:
                    return f"Could not click '{target}': {e}"
            await page.wait_for_timeout(600)
            self.current_url = page.url
            await self._push_shot()
            return f"Clicked '{target}'. Now at {page.url}"

    async def type(self, selector: str, text: str) -> str:
        async with self.lock:
            page = await self.ensure()
            try:
                await page.fill(selector, text, timeout=6000)
            except Exception:
                try:
                    await page.get_by_placeholder(selector).first.fill(text, timeout=6000)
                except Exception as e:
                    return f"Could not type into '{selector}': {e}"
            await self._push_shot()
            return f"Typed into '{selector}'."

    async def press(self, key: str) -> str:
        async with self.lock:
            page = await self.ensure()
            await page.keyboard.press(key)
            await page.wait_for_timeout(600)
            self.current_url = page.url
            await self._push_shot()
            return f"Pressed {key}. Now at {page.url}"

    async def back(self) -> str:
        async with self.lock:
            if self.page is None:
                return "No page open."
            await self.page.go_back(wait_until="domcontentloaded")
            self.current_url = self.page.url
            await self._push_shot()
            return f"Went back to {self.page.url}"

    async def tap(self, x: float, y: float) -> str:
        """Click at viewport coordinates (used for tap-to-intervene)."""
        async with self.lock:
            page = await self.ensure()
            await page.mouse.click(float(x), float(y))
            await page.wait_for_timeout(600)
            self.current_url = page.url
            await self._push_shot()
            return f"Tapped at ({int(x)}, {int(y)}). Now at {page.url}"

    async def extract_text(self) -> str:
        async with self.lock:
            page = await self.ensure()
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            return clip(text)

    async def screenshot(self) -> str:
        async with self.lock:
            page = await self.ensure()
            await self._push_shot()
            return f"Captured screenshot of {self.current_url or page.url}."

    async def close(self) -> None:
        try:
            if self._streamer:
                self._streamer.cancel()
            if self.browser:
                await self.browser.close()
            if self.pw:
                await self.pw.stop()
        except Exception:
            pass
        self.page = self.browser = self.pw = None


browser = BrowserManager()


# --------------------------------------------------------------------------
# GitHub tool
# --------------------------------------------------------------------------

async def sh(cmd: list[str], cwd: Optional[str] = None, timeout: int = 120) -> tuple[int, str]:
    """Run a command, return (exit_code, combined_output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    return proc.returncode or 0, out.decode(errors="replace")


WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", HERE / "workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)


def github_configured() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN")) or shutil.which("gh") is not None


def _auth_remote(url: str) -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://github.com/"):
        return url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")
    return url


async def github_tool(operation: str, repo: str = "", path: str = "",
                      content: str = "", message: str = "", url: str = "") -> str:
    op = (operation or "").lower()
    token = os.environ.get("GITHUB_TOKEN", "")

    if op == "status":
        who = "unknown"
        if shutil.which("gh"):
            code, out = await sh(["gh", "auth", "status"])
            who = clip(out, 400)
        return f"gh available: {shutil.which('gh') is not None}; token set: {bool(token)}\n{who}"

    if op == "create_repo":
        if not shutil.which("gh"):
            return "Creating a repo needs the GitHub CLI (gh). Install it or create the repo in the web UI."
        code, out = await sh(["gh", "repo", "create", repo, "--private", "--clone"],
                             cwd=str(WORKSPACE))
        return clip(out)

    if op == "clone":
        target = url or (f"https://github.com/{repo}.git" if repo else "")
        if not target:
            return "clone needs a repo (owner/name) or a url."
        name = Path(target).stem
        code, out = await sh(["git", "clone", _auth_remote(target)], cwd=str(WORKSPACE))
        return clip(out) + f"\nCloned into {WORKSPACE / name}"

    # The following operate inside a cloned repo under WORKSPACE/<repo-name>.
    repo_name = Path(repo).name if repo else ""
    repo_dir = WORKSPACE / repo_name if repo_name else WORKSPACE

    if op == "read_file":
        f = repo_dir / path
        if not f.exists():
            return f"No such file: {f}"
        return clip(f.read_text(errors="replace"))

    if op in ("write_file", "edit_file"):
        f = repo_dir / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        return f"Wrote {len(content)} chars to {f}"

    if op in ("commit_push", "commit", "push"):
        if not repo_dir.exists():
            return f"No local checkout at {repo_dir}. Clone first."
        await sh(["git", "add", "-A"], cwd=str(repo_dir))
        code, out = await sh(["git", "commit", "-m", message or "Update via Mobile AI Agent"],
                             cwd=str(repo_dir))
        log = out
        if op != "commit":
            # ensure remote uses token auth if available
            code2, remote = await sh(["git", "remote", "get-url", "origin"], cwd=str(repo_dir))
            if token and remote.strip().startswith("https://github.com/"):
                await sh(["git", "remote", "set-url", "origin", _auth_remote(remote.strip())],
                         cwd=str(repo_dir))
            code3, out3 = await sh(["git", "push"], cwd=str(repo_dir))
            log += "\n" + out3
        return clip(log)

    if op == "pull":
        if not repo_dir.exists():
            return f"No local checkout at {repo_dir}. Clone first."
        code, out = await sh(["git", "pull", "--ff-only"], cwd=str(repo_dir))
        return clip(out)

    return (f"Unknown github operation '{operation}'. "
            "Use: status, create_repo, clone, pull, read_file, write_file, commit_push.")


# --------------------------------------------------------------------------
# Termux tool
# --------------------------------------------------------------------------

def termux_available() -> bool:
    return bool(os.environ.get("PREFIX", "").startswith("/data/data/com.termux")) \
        or shutil.which("termux-notification") is not None


async def termux_tool(action: str, command: str = "", title: str = "",
                      content: str = "", text: str = "", package: str = "",
                      level: str = "") -> str:
    a = (action or "").lower()

    if a == "shell":
        if not command:
            return "shell needs a command."
        code, out = await sh(["sh", "-c", command])
        return f"(exit {code})\n{clip(out)}"

    if a in ("notify", "notification"):
        if shutil.which("termux-notification"):
            code, out = await sh(["termux-notification", "--title", title or "AI Agent",
                                  "--content", content or text or ""])
            return f"Notification sent. {clip(out,200)}"
        return "termux-notification not found. Install the Termux:API app and the termux-api package."

    if a == "clipboard_set":
        if shutil.which("termux-clipboard-set"):
            proc = await asyncio.create_subprocess_exec(
                "termux-clipboard-set", stdin=asyncio.subprocess.PIPE)
            await proc.communicate((text or content).encode())
            return "Clipboard set."
        return "termux-clipboard-set not found."

    if a == "clipboard_get":
        if shutil.which("termux-clipboard-get"):
            code, out = await sh(["termux-clipboard-get"])
            return clip(out)
        return "termux-clipboard-get not found."

    if a in ("open_app", "launch", "am_start"):
        if package:
            # Prefer termux-open / am monkey launch by package.
            code, out = await sh(["sh", "-c",
                                  f"am start -n {package} 2>/dev/null || "
                                  f"monkey -p {package} -c android.intent.category.LAUNCHER 1"])
            return clip(out) or f"Attempted to launch {package}"
        return "open_app needs a package name."

    if a == "volume":
        if shutil.which("termux-volume"):
            args = ["termux-volume"]
            if level:
                parts = level.split()
                args += parts
            code, out = await sh(args)
            return clip(out)
        return "termux-volume not found."

    return (f"Unknown termux action '{action}'. "
            "Use: shell, notify, clipboard_set, clipboard_get, open_app, volume.")


# --------------------------------------------------------------------------
# Scheduler  (APScheduler + SQLite jobstore)
# --------------------------------------------------------------------------

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{JOBS_DB_PATH}")},
    timezone=os.environ.get("AGENT_TZ", "UTC"),
)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            prompt TEXT NOT NULL,
            cron TEXT,
            interval_minutes INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            result TEXT
        );
        """)


def task_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    job = scheduler.get_job(f"task-{d['id']}")
    d["next_run"] = job.next_run_time.isoformat() if job and job.next_run_time else None
    return d


def _trigger_for(cron: Optional[str], interval_minutes: Optional[int]):
    if cron:
        return CronTrigger.from_crontab(cron, timezone=scheduler.timezone)
    if interval_minutes:
        return IntervalTrigger(minutes=int(interval_minutes), timezone=scheduler.timezone)
    return None


def sync_job(task: dict) -> None:
    """Create / update / remove the APScheduler job for a task row."""
    job_id = f"task-{task['id']}"
    existing = scheduler.get_job(job_id)
    trigger = _trigger_for(task.get("cron"), task.get("interval_minutes"))
    if not task["enabled"] or trigger is None:
        if existing:
            scheduler.remove_job(job_id)
        return
    if existing:
        scheduler.reschedule_job(job_id, trigger=trigger)
    else:
        scheduler.add_job(run_scheduled_task, trigger=trigger, args=[task["id"]],
                          id=job_id, replace_existing=True, misfire_grace_time=3600)


async def run_scheduled_task(task_id: int) -> None:
    """Executed by APScheduler. Runs the agent headless and notifies."""
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or not row["enabled"]:
            return
        cur = conn.execute(
            "INSERT INTO task_runs(task_id, started_at, status) VALUES(?,?,?)",
            (task_id, now_iso(), "running"))
        run_id = cur.lastrowid
        conn.commit()
        prompt = row["prompt"]
        name = row["name"]

    log_lines: list[str] = []

    async def emit(ev: dict) -> None:
        log_lines.append(json.dumps(ev, ensure_ascii=False))

    status = "ok"
    try:
        final = await run_agent(prompt, emit, system_extra=(
            "You are running as a scheduled background task; no user is watching. "
            "Complete the task end to end, then give a one-line summary."))
        log_lines.append("FINAL: " + (final or ""))
    except Exception as e:
        status = "error"
        final = f"{type(e).__name__}: {e}"
        log_lines.append("ERROR: " + traceback.format_exc())

    with db() as conn:
        conn.execute(
            "UPDATE task_runs SET finished_at=?, status=?, result=? WHERE id=?",
            (now_iso(), status, clip("\n".join(log_lines), 20000), run_id))
        conn.commit()

    # Android notification
    try:
        await termux_tool("notify", title=f"Task: {name}",
                          content=(final or status)[:180])
    except Exception:
        pass


# Create the SQLite schema at import time so the module is safe to use
# directly (tests, embedded use) and before the app lifespan runs.
init_db()


# --------------------------------------------------------------------------
# Tool schemas + dispatch (ReAct)
# --------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "browser_open",
        "description": "Open a URL in the live browser.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "browser_click",
        "description": "Click an element by CSS selector or by its visible text.",
        "parameters": {"type": "object", "properties": {
            "selector_or_text": {"type": "string"}}, "required": ["selector_or_text"]}}},
    {"type": "function", "function": {
        "name": "browser_type",
        "description": "Type text into an input identified by a CSS selector or placeholder.",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string"}, "text": {"type": "string"}},
            "required": ["selector", "text"]}}},
    {"type": "function", "function": {
        "name": "browser_press",
        "description": "Press a keyboard key (e.g. Enter) in the browser.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "browser_extract_text",
        "description": "Return the visible text of the current page.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "browser_screenshot",
        "description": "Capture the current page and show it in the Live Browser tab.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "github_tool",
        "description": "Work with GitHub repos: status, create_repo, clone, read_file, "
                       "write_file, commit_push.",
        "parameters": {"type": "object", "properties": {
            "operation": {"type": "string"},
            "repo": {"type": "string", "description": "owner/name"},
            "path": {"type": "string"},
            "content": {"type": "string"},
            "message": {"type": "string"},
            "url": {"type": "string"}}, "required": ["operation"]}}},
    {"type": "function", "function": {
        "name": "termux_tool",
        "description": "Control the phone via Termux: shell, notify, clipboard_set, "
                       "clipboard_get, open_app, volume.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string"},
            "command": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "text": {"type": "string"},
            "package": {"type": "string"},
            "level": {"type": "string"}}, "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "schedule_task",
        "description": "Create a background scheduled task. Provide either a cron "
                       "expression (minute hour day month weekday) or interval_minutes.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
            "prompt": {"type": "string", "description": "What the agent should do each run."},
            "cron": {"type": "string"},
            "interval_minutes": {"type": "integer"}},
            "required": ["name", "prompt"]}}},
]


async def dispatch_tool(name: str, args: dict,
                        emit: Callable[[dict], Awaitable[None]]) -> str:
    try:
        if name == "browser_open":
            return await browser.open(args.get("url", ""))
        if name == "browser_click":
            return await browser.click(args.get("selector_or_text", ""))
        if name == "browser_type":
            return await browser.type(args.get("selector", ""), args.get("text", ""))
        if name == "browser_press":
            return await browser.press(args.get("key", "Enter"))
        if name == "browser_extract_text":
            return await browser.extract_text()
        if name == "browser_screenshot":
            return await browser.screenshot()
        if name == "github_tool":
            return await github_tool(
                args.get("operation", ""), args.get("repo", ""), args.get("path", ""),
                args.get("content", ""), args.get("message", ""), args.get("url", ""))
        if name == "termux_tool":
            return await termux_tool(
                args.get("action", ""), args.get("command", ""), args.get("title", ""),
                args.get("content", ""), args.get("text", ""), args.get("package", ""),
                args.get("level", ""))
        if name == "schedule_task":
            return create_task_from_agent(args)
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool {name} failed: {type(e).__name__}: {e}"


def create_task_from_agent(args: dict) -> str:
    name = args.get("name") or "Scheduled task"
    prompt = args.get("prompt") or ""
    cron = args.get("cron") or None
    interval = args.get("interval_minutes") or None
    if not prompt:
        return "schedule_task needs a prompt."
    if not cron and not interval:
        return "schedule_task needs either a cron expression or interval_minutes."
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(name, prompt, cron, interval_minutes, enabled, created_at) "
            "VALUES(?,?,?,?,1,?)",
            (name, prompt, cron, interval, now_iso()))
        task_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    sync_job(dict(row))
    when = cron or f"every {interval} min"
    return f"Scheduled '{name}' (id {task_id}, {when})."


# --------------------------------------------------------------------------
# The agent loop (ReAct via native tool calling)
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a capable mobile phone agent running on the user's own Android device (Termux).
You help by taking real actions with your tools, not just talking.

Tools:
- browser_open / browser_click / browser_type / browser_press / browser_extract_text / browser_screenshot
  drive a real browser. The user watches it live, so narrate what you do.
- github_tool manages the user's GitHub repositories.
- termux_tool controls the phone: run shell commands, send Android notifications,
  read/write the clipboard, launch apps, change volume.
- schedule_task creates background jobs (cron or interval). Use it when the user asks
  for something recurring like "every day at 9am ...".

Guidelines:
- Reply in the user's language (Arabic if they write Arabic).
- Take the smallest number of steps that gets the job done, then stop.
- After a browser action, consider browser_extract_text to read the result.
- When you have finished, give a short, clear final answer. Do not keep calling tools
  once the task is done.
"""


async def run_agent(user_text: str,
                    emit: Callable[[dict], Awaitable[None]],
                    history: Optional[list[dict]] = None,
                    system_extra: str = "") -> str:
    system = SYSTEM_PROMPT + ("\n" + system_extra if system_extra else "")
    messages: list[dict] = history if history is not None else [
        {"role": "system", "content": system}]
    if history is None:
        pass
    messages.append({"role": "user", "content": user_text})

    final_text = ""
    for step in range(MAX_STEPS):
        await emit({"type": "thinking", "step": step + 1})
        data = await router.complete(messages, tools=TOOL_SCHEMAS)
        await emit({"type": "provider", "name": data.get("_provider")})
        msg = data["choices"][0]["message"]
        # Normalise to a plain dict the API will accept back.
        assistant_msg: dict[str, Any] = {"role": "assistant",
                                         "content": msg.get("content") or ""}
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            final_text = msg.get("content") or ""
            await emit({"type": "final", "text": final_text})
            return final_text

        if assistant_msg["content"]:
            await emit({"type": "assistant", "text": assistant_msg["content"]})

        for tc in tool_calls:
            fname = tc["function"]["name"]
            raw = tc["function"].get("arguments") or "{}"
            try:
                fargs = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                fargs = {}
            await emit({"type": "tool_call", "name": fname, "args": fargs})
            result = await dispatch_tool(fname, fargs, emit)
            await emit({"type": "tool_result", "name": fname, "result": clip(result, 1500)})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", fname),
                "content": clip(result),
            })

    await emit({"type": "final", "text": final_text or "Reached the step limit."})
    return final_text or "Reached the step limit."


# --------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    # Rebuild jobs from the tasks table (jobstore also persists them).
    with db() as conn:
        for row in conn.execute("SELECT * FROM tasks WHERE enabled=1").fetchall():
            try:
                sync_job(dict(row))
            except Exception:
                pass
    yield
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
    await browser.close()


app = FastAPI(title="Mobile AI Agent", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(HERE / "index.html")


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse({
        "name": "Mobile AI Agent",
        "short_name": "AI Agent",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0b0f17",
        "theme_color": "#0b0f17",
        "icons": [{
            "src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
    })


@app.get("/icon.svg")
async def icon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
        '<rect width="192" height="192" rx="40" fill="#0b0f17"/>'
        '<circle cx="96" cy="78" r="34" fill="none" stroke="#38bdf8" stroke-width="8"/>'
        '<circle cx="84" cy="74" r="6" fill="#38bdf8"/><circle cx="108" cy="74" r="6" fill="#38bdf8"/>'
        '<rect x="70" y="120" width="52" height="34" rx="10" fill="#38bdf8"/>'
        '</svg>')
    return PlainTextResponse(svg, media_type="image/svg+xml")


@app.get("/sw.js")
async def service_worker():
    js = (
        "self.addEventListener('install',e=>self.skipWaiting());"
        "self.addEventListener('activate',e=>self.clients.claim());"
        "self.addEventListener('fetch',e=>{});"
    )
    return PlainTextResponse(js, media_type="application/javascript")


@app.get("/api/status")
async def api_status():
    return {
        "time": now_iso(),
        "ai": router.status(),
        "browser": {"available": browser.available, "open": browser.page is not None,
                    "url": browser.current_url},
        "github": {"configured": github_configured(),
                   "gh_cli": shutil.which("gh") is not None,
                   "token": bool(os.environ.get("GITHUB_TOKEN"))},
        "termux": {"available": termux_available(),
                   "notifications": shutil.which("termux-notification") is not None},
        "scheduler": {"running": scheduler.running,
                      "jobs": len(scheduler.get_jobs())},
    }


# ---- Tasks CRUD ----

class TaskIn(BaseModel):
    name: str
    prompt: str
    cron: Optional[str] = None
    interval_minutes: Optional[int] = None
    enabled: bool = True


@app.get("/api/tasks")
async def list_tasks():
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    return [task_to_dict(r) for r in rows]


@app.post("/api/tasks")
async def create_task(t: TaskIn):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(name, prompt, cron, interval_minutes, enabled, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (t.name, t.prompt, t.cron, t.interval_minutes, int(t.enabled), now_iso()))
        tid = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    sync_job(dict(row))
    return task_to_dict(row)


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, t: TaskIn):
    with db() as conn:
        conn.execute(
            "UPDATE tasks SET name=?, prompt=?, cron=?, interval_minutes=?, enabled=? WHERE id=?",
            (t.name, t.prompt, t.cron, t.interval_minutes, int(t.enabled), task_id))
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    sync_job(dict(row))
    return task_to_dict(row)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int):
    job = scheduler.get_job(f"task-{task_id}")
    if job:
        scheduler.remove_job(f"task-{task_id}")
    with db() as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.execute("DELETE FROM task_runs WHERE task_id=?", (task_id,))
        conn.commit()
    return {"ok": True}


@app.post("/api/tasks/{task_id}/run")
async def run_task_now(task_id: int):
    asyncio.create_task(run_scheduled_task(task_id))
    return {"ok": True, "started": True}


@app.get("/api/tasks/{task_id}/runs")
async def task_runs(task_id: int):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 30",
            (task_id,)).fetchall()
    return [dict(r) for r in rows]


# ---- Manual browser controls (Live Browser tab intervention) ----

class BrowserAction(BaseModel):
    action: str
    url: Optional[str] = None
    target: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None


@app.post("/api/browser")
async def browser_action(a: BrowserAction):
    try:
        if a.action == "open":
            return {"result": await browser.open(a.url or "")}
        if a.action == "click":
            return {"result": await browser.click(a.target or "")}
        if a.action == "type":
            return {"result": await browser.type(a.selector or "", a.text or "")}
        if a.action == "press":
            return {"result": await browser.press(a.key or "Enter")}
        if a.action == "tap":
            return {"result": await browser.tap(a.x or 0, a.y or 0)}
        if a.action == "back":
            return {"result": await browser.back()}
        if a.action == "screenshot":
            return {"result": await browser.screenshot()}
        if a.action == "extract":
            return {"result": await browser.extract_text()}
        return {"error": f"unknown action {a.action}"}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


# ---- GitHub quick actions (Dashboard Push / Pull buttons) ----

class GitAction(BaseModel):
    operation: str
    repo: Optional[str] = ""
    path: Optional[str] = ""
    content: Optional[str] = ""
    message: Optional[str] = ""
    url: Optional[str] = ""


@app.post("/api/github")
async def github_action(a: GitAction):
    res = await github_tool(a.operation, a.repo or "", a.path or "",
                            a.content or "", a.message or "", a.url or "")
    return {"result": res}


class TermuxAction(BaseModel):
    action: str
    command: Optional[str] = ""
    title: Optional[str] = ""
    content: Optional[str] = ""
    text: Optional[str] = ""
    package: Optional[str] = ""
    level: Optional[str] = ""


@app.post("/api/termux")
async def termux_action(a: TermuxAction):
    res = await termux_tool(a.action, a.command or "", a.title or "",
                            a.content or "", a.text or "", a.package or "", a.level or "")
    return {"result": res}


# ---- WebSockets ----

@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    async def emit(ev: dict) -> None:
        try:
            await ws.send_json(ev)
        except Exception:
            pass

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                data = {"text": raw}
            text = (data.get("text") or "").strip()
            if not text:
                continue
            await emit({"type": "user_echo", "text": text})
            try:
                await run_agent(text, emit, history=history)
            except Exception as e:
                await emit({"type": "error", "text": f"{type(e).__name__}: {e}"})
            await emit({"type": "done"})
    except WebSocketDisconnect:
        return
    except Exception:
        return


@app.websocket("/ws/browser")
async def ws_browser(ws: WebSocket):
    await ws.accept()
    await browser_hub.add(ws)
    # Send an immediate frame if a page exists.
    if browser.page is not None:
        await browser._push_shot()
    try:
        while True:
            # Keep the socket alive; ignore any inbound.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        browser_hub.remove(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend:app", host=HOST, port=PORT, reload=False)
