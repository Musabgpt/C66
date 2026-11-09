"""MusabAI automation server — the one app's power backends in a managed Debian container.

A single authenticated localhost service that gives the agent four real automation
engines over one coherent HTTP surface, so the Android app stays one app with one
model while heavy Linux-only libraries run where they can:

  * Mobilerun (droidrun) ``MobileAgent`` driving the phone through MusabDriver — our own
    accessibility bridge (PhoneServer on 127.0.0.1), with no Portal app and no ADB.
  * uiautomator2 as an optional power backend, used only when an ADB device is already
    reachable (the engine's wireless-debugging pairing connects it); never required.
  * Pydoll driving one shared Chromium over the Chrome DevTools Protocol.
  * Browser Use ``Agent`` driving that same Chromium for goal-level browser tasks.

All language-model calls go to the engine's local OpenAI-compatible endpoint, which routes
to the free provider pool (Gemini -> OpenRouter -> Groq -> Kilo free), so no key is needed
and the whole app shares one provider policy.

This module is stdlib-only at import time; the heavy libraries are imported lazily inside
the handlers that use them, so the server starts and answers /health even before every
backend is installed. It listens only on 127.0.0.1 and requires a per-start bearer token.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = 1

PHONE_ORIGIN = os.environ.get("MUSAB_PHONE_ORIGIN", "http://127.0.0.1:8796")
PHONE_KEY = os.environ.get("MUSAB_PHONE_KEY", "")
LLM_BASE = os.environ.get("MUSAB_LLM_BASE", "http://127.0.0.1:8795/v1/free").rstrip("/")
LLM_MODEL = os.environ.get("MUSAB_LLM_MODEL", "kilo-auto/free")
LLM_KEY = os.environ.get("MUSAB_LLM_KEY", "musab-local")
CHROMIUM_BIN = os.environ.get("CHROMIUM_BIN", "/usr/bin/chromium")
CDP_PORT = int(os.environ.get("MUSAB_CDP_PORT", "9333"))
TOKEN = os.environ.get("MUSAB_AUTOMATION_TOKEN", "")


def _clip(text, limit=6000):
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


# ---------------------------------------------------------------------------
# PhoneServer client  (our accessibility bridge; no Portal, no ADB)
# ---------------------------------------------------------------------------

def phone_call(action, **params):
    """One request to the Android app's PhoneServer. Raises on a non-ok answer."""
    body = dict(params)
    body["action"] = action
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        PHONE_ORIGIN + "/phone", data=data, method="POST",
        headers={"Content-Type": "application/json", "X-NewAl-Key": PHONE_KEY})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read().decode("utf-8"))
    if not out.get("ok", False):
        raise RuntimeError(out.get("error") or "phone action failed")
    return out


# ---------------------------------------------------------------------------
# MusabDriver — a Mobilerun DeviceDriver over PhoneServer
# ---------------------------------------------------------------------------

def make_driver():
    """Build a Mobilerun DeviceDriver backed by MusabAI's own phone bridge."""
    from mobilerun_core_local.driver.base import DeviceDriver

    class MusabDriver(DeviceDriver):
        platform = "Android"
        supported = {
            "tap", "swipe", "input_text", "press_button", "start_app",
            "screenshot", "get_ui_tree", "get_date", "get_apps",
        }
        supported_buttons = {"back", "home", "enter"}

        async def connect(self):
            await asyncio.to_thread(phone_call, "device")

        async def ensure_connected(self):
            await self.connect()

        async def tap(self, x, y):
            await asyncio.to_thread(phone_call, "tap", x=int(x), y=int(y))

        async def swipe(self, x1, y1, x2, y2, duration_ms=1000):
            await asyncio.to_thread(
                phone_call, "gesture", x1=int(x1), y1=int(y1), x2=int(x2),
                y2=int(y2), duration=int(duration_ms))

        async def input_text(self, text, clear=False, humanize=False, wpm=0):
            await asyncio.to_thread(phone_call, "type", text=str(text))
            return True

        async def press_button(self, button):
            b = str(button).lower()
            if b not in self.supported_buttons:
                raise ValueError("unsupported button: " + button)
            await asyncio.to_thread(phone_call, "key", name=b)

        async def start_app(self, package, activity=None):
            out = await asyncio.to_thread(phone_call, "open_app", name=package)
            return out.get("text", "opened " + package)

        async def get_apps(self, include_system=True):
            out = await asyncio.to_thread(phone_call, "apps")
            apps = []
            for line in (out.get("text") or "").splitlines():
                line = line.strip()
                if line.endswith(")") and "(" in line:
                    label, pkg = line.rsplit("(", 1)
                    apps.append({"label": label.strip(), "package": pkg[:-1].strip()})
            return apps

        async def screenshot(self, hide_overlay=True):
            out = await asyncio.to_thread(phone_call, "screenshot_bytes")
            return base64.b64decode(out["png_base64"])

        async def get_ui_tree(self, filter=True):
            out = await asyncio.to_thread(phone_call, "screen_tree")
            return out["state"]

        async def get_date(self):
            try:
                out = await asyncio.to_thread(phone_call, "device")
                return out.get("text", "")
            except Exception:
                return time.strftime("%Y-%m-%d %H:%M:%S")

    return MusabDriver()


# ---------------------------------------------------------------------------
# LLM wiring (engine's local OpenAI-compatible endpoint -> free provider pool)
# ---------------------------------------------------------------------------

def mobilerun_llms():
    from mobilerun import load_llm
    llm = load_llm(
        "OpenAILike", LLM_MODEL,
        api_base=LLM_BASE, api_key=LLM_KEY,
        is_chat_model=True, is_function_calling_model=True,
        context_window=128000, timeout=120, max_retries=2)
    return {"manager": llm, "executor": llm, "fast_agent": llm, "app_opener": llm}


def browser_use_llm():
    from browser_use import ChatOpenAI
    return ChatOpenAI(model=LLM_MODEL, base_url=LLM_BASE, api_key=LLM_KEY, temperature=0.2)


# ---------------------------------------------------------------------------
# Mobilerun phone agent
# ---------------------------------------------------------------------------

async def run_phone_agent(goal, max_steps=18):
    from mobilerun import MobileAgent, DeviceConfig
    from mobilerun.config_manager import MobileConfig

    config = MobileConfig()
    config.device = DeviceConfig(platform="android", portal_mode="disabled", auto_setup=False)
    config.agent.max_steps = int(max_steps)
    config.agent.vision_only = False
    config.telemetry.enabled = False

    agent = MobileAgent(goal=goal, config=config, llms=mobilerun_llms(), driver=make_driver())
    result = await agent.run()
    success = getattr(result, "success", None)
    reason = getattr(result, "reason", "")
    steps = getattr(result, "steps", None)
    return {"ok": True, "success": success, "reason": _clip(str(reason), 2000),
            "steps": steps if isinstance(steps, int) else None}


# ---------------------------------------------------------------------------
# Shared Chromium + Pydoll
# ---------------------------------------------------------------------------

class Browser:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.chrome = None
        self.tab = None

    async def tab_ready(self):
        if self.tab is not None:
            return self.tab
        from pydoll.browser.chromium import Chrome
        from pydoll.browser.options import ChromiumOptions
        profile = os.environ.get("MUSAB_CHROME_PROFILE", "/opt/musabai/chrome-profile")
        os.makedirs(profile, exist_ok=True)
        # A crashed run leaves a Singleton* lock that aborts the next launch; clear it.
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                os.unlink(os.path.join(profile, name))
            except OSError:
                pass
        opts = ChromiumOptions()
        opts.binary_location = CHROMIUM_BIN
        for flag in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                     "--remote-debugging-port=%d" % CDP_PORT,
                     "--user-data-dir=%s" % profile,
                     "--window-size=412,915"):
            opts.add_argument(flag)
        self.chrome = Chrome(options=opts, connection_port=CDP_PORT)
        self.tab = await self.chrome.start()
        return self.tab

    def cdp_url(self):
        return "http://127.0.0.1:%d" % CDP_PORT

    async def open(self, url):
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        tab = await self.tab_ready()
        await tab.go_to(url)
        return {"ok": True, "url": await tab.current_url(), "title": await _safe_title(tab)}

    async def click(self, target):
        tab = await self.tab_ready()
        el = await _find(tab, target)
        if el is None:
            return {"ok": False, "error": "no element matching %r" % target}
        await el.click()
        await asyncio.sleep(0.6)
        return {"ok": True, "url": await tab.current_url()}

    async def type(self, target, text):
        tab = await self.tab_ready()
        el = await _find(tab, target)
        if el is None:
            return {"ok": False, "error": "no field matching %r" % target}
        await el.insert_text(text)
        return {"ok": True}

    async def extract(self):
        tab = await self.tab_ready()
        res = await tab.execute_script(
            "return (document.body && document.body.innerText) || ''", return_by_value=True)
        text = _script_value(res)
        return {"ok": True, "text": _clip(text, 8000)}

    async def screenshot(self):
        tab = await self.tab_ready()
        b64 = await tab.take_screenshot(as_base64=True, quality=60)
        return {"ok": True, "png_base64": b64, "url": await tab.current_url()}

    async def run_agent(self, task, max_steps=25):
        from browser_use import Agent, BrowserSession
        await self.tab_ready()
        session = BrowserSession(cdp_url=self.cdp_url(), keep_alive=True)
        agent = Agent(task=task, llm=browser_use_llm(), browser_session=session,
                      use_vision=False, max_actions_per_step=4)
        history = await agent.run(max_steps=int(max_steps))
        return {"ok": True, "result": _clip(str(history.final_result() or ""), 4000),
                "success": history.is_successful(), "steps": history.number_of_steps(),
                "urls": [str(u) for u in (history.urls() or [])][-5:]}


async def _safe_title(tab):
    try:
        res = await tab.execute_script("return document.title", return_by_value=True)
        return _script_value(res)
    except Exception:
        return ""


def _script_value(res):
    if isinstance(res, dict):
        r = res.get("result")
        if isinstance(r, dict):
            r = r.get("result", r)
        if isinstance(r, dict):
            return r.get("value", "")
        return r if isinstance(r, str) else ""
    return res if isinstance(res, str) else ""


async def _find(tab, target):
    """Find an element by CSS selector, else by visible text."""
    target = str(target or "").strip()
    try:
        if target and target[0] in ".#[" or (" " not in target and (">" in target or "[" in target)):
            el = await tab.query(target, timeout=6, raise_exc=False)
            if el:
                return el
    except Exception:
        pass
    try:
        el = await tab.find(text=target, timeout=6, raise_exc=False)
        if el:
            return el
    except Exception:
        pass
    try:
        return await tab.query(target, timeout=4, raise_exc=False)
    except Exception:
        return None


BROWSER = Browser()


# ---------------------------------------------------------------------------
# uiautomator2 optional power backend (only when an ADB device is reachable)
# ---------------------------------------------------------------------------

class U2:
    def __init__(self):
        self.device = None

    def connect(self, serial=None):
        import uiautomator2 as u2
        self.device = u2.connect(serial) if serial else u2.connect()
        info = self.device.info
        return {"ok": True, "serial": getattr(self.device, "serial", serial or ""),
                "sdk": info.get("sdkInt"), "display": {
                    "w": info.get("displayWidth"), "h": info.get("displayHeight")}}

    def ensure(self):
        if self.device is None:
            self.connect()
        return self.device

    def adb(self, op, address="", code=""):
        import subprocess
        address = str(address or "")
        if op == "adb_status":
            out = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=30)
            return {"ok": True, "devices": out.stdout.strip()}
        if op == "adb_pair":
            p = subprocess.run(["adb", "pair", address], input=(str(code or "") + "\n"),
                               capture_output=True, text=True, timeout=60)
            return {"ok": p.returncode == 0, "output": (p.stdout + p.stderr).strip()[:500]}
        if op == "adb_connect":
            p = subprocess.run(["adb", "connect", address], capture_output=True, text=True, timeout=60)
            ok = p.returncode == 0 and "connected" in (p.stdout + p.stderr).lower()
            return {"ok": ok, "output": (p.stdout + p.stderr).strip()[:500]}
        raise ValueError("unknown adb op: " + str(op))

    def op(self, op, args):
        d = self.ensure()
        if op == "dump":
            return {"ok": True, "xml": _clip(d.dump_hierarchy(), 12000)}
        if op == "click_text":
            d(text=args["text"]).click()
            return {"ok": True}
        if op == "click_xy":
            d.click(args["x"], args["y"])
            return {"ok": True}
        if op == "send_keys":
            d.send_keys(args["text"], clear=bool(args.get("clear")))
            return {"ok": True}
        if op == "press":
            d.press(args.get("key", "back"))
            return {"ok": True}
        if op == "app_start":
            d.app_start(args["package"], stop=bool(args.get("stop")))
            return {"ok": True}
        if op == "app_current":
            return {"ok": True, "current": d.app_current()}
        raise ValueError("unknown u2 op: " + str(op))


U2BACKEND = U2()


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, name="musab-automation-loop", daemon=True).start()


def _await(coro, timeout=600):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=timeout)


def dispatch(path, data):
    if path == "/health":
        return {"ok": True, "version": VERSION, "llm": {"base": LLM_BASE, "model": LLM_MODEL},
                "phone": bool(PHONE_KEY), "chromium": os.path.exists(CHROMIUM_BIN)}
    if path == "/phone_agent":
        return _await(run_phone_agent(data.get("goal", ""), data.get("max_steps", 18)))
    if path == "/browser_agent":
        return _await(BROWSER.run_agent(data.get("task", ""), data.get("max_steps", 25)))
    if path == "/browser":
        op = data.get("op")
        if op == "open":
            return _await(BROWSER.open(data.get("url", "")))
        if op == "click":
            return _await(BROWSER.click(data.get("target", "")))
        if op == "type":
            return _await(BROWSER.type(data.get("target", ""), data.get("text", "")))
        if op == "extract":
            return _await(BROWSER.extract())
        if op == "screenshot":
            return _await(BROWSER.screenshot())
        return {"ok": False, "error": "unknown browser op: %s" % op}
    if path == "/u2":
        op = data.get("op")
        if isinstance(op, str) and op.startswith("adb_"):
            return U2BACKEND.adb(op, data.get("address", ""), data.get("code", ""))
        if op == "connect":
            return U2BACKEND.connect(data.get("serial"))
        return U2BACKEND.op(op, data)
    return {"ok": False, "error": "unknown path: %s" % path}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if self.path != "/health" and TOKEN and token != TOKEN:
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception as e:
            self._send(400, {"ok": False, "error": "bad request: %s" % e})
            return
        try:
            self._send(200, dispatch(self.path, data))
        except Exception as e:
            self._send(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        if self.path == "/health":
            self._send(200, dispatch("/health", {}))
        else:
            self._send(404, {"ok": False, "error": "POST only"})


def main():
    host = os.environ.get("MUSAB_AUTOMATION_HOST", "127.0.0.1")
    port = int(os.environ.get("MUSAB_AUTOMATION_PORT", "8799"))
    server = ThreadingHTTPServer((host, port), Handler)
    print("musab-automation %d on %s:%d" % (VERSION, host, port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
