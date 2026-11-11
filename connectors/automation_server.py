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
import atexit
import base64
import concurrent.futures
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = 4

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")      # Browser Use: nothing leaves the phone but the task

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
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()            # the app answers a failed action with 400 and says why in the body
    except OSError as e:
        raise PhoneError("MusabAI's phone server is not reachable (%s)" % e)
    try:
        out = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise PhoneError("the phone server sent an unreadable answer")
    if not out.get("ok", False):
        raise PhoneError(out.get("error") or "phone action failed")
    return out


class PhoneError(RuntimeError):
    """What the phone said went wrong, in its own words (accessibility off, no such app...)."""


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
    # The screen must be readable before anything else: with the accessibility service off the
    # phone says so in its own words, and that is the answer (not a minute of retries).
    await asyncio.to_thread(phone_call, "screen_tree")

    from mobilerun import MobileAgent, DeviceConfig
    from mobilerun.config_manager import MobileConfig

    config = MobileConfig()
    config.device = DeviceConfig(platform="android", portal_mode="disabled", auto_setup=False)
    config.agent.max_steps = int(max_steps)
    config.agent.vision_only = False
    config.agent.reasoning = False       # one fast agent, no manager/executor round trips
    config.agent.streaming = False       # whole answers: a stalled free model can be replaced mid-request
    config.telemetry.enabled = False

    agent = MobileAgent(goal=goal, config=config, llms=mobilerun_llms(), driver=make_driver(),
                        timeout=min(900, 120 + 45 * int(max_steps)))
    result = await agent.run()
    success = getattr(result, "success", None)
    reason = getattr(result, "reason", "")
    steps = getattr(result, "steps", None)
    return {"ok": True, "success": success, "reason": _clip(str(reason), 2000),
            "steps": steps if isinstance(steps, int) else None}


# ---------------------------------------------------------------------------
# Shared Chromium: Pydoll for single steps, Browser Use for whole tasks
# ---------------------------------------------------------------------------

_PAGE_SUMMARY_JS = r"""
const out = [];
const seen = new Set();
const label = (e) => (e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('title') ||
                      e.getAttribute('alt') || '').trim().replace(/\s+/g, ' ').slice(0, 60);
const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
for (const e of document.querySelectorAll('input, textarea, select, button, a[href], [role=button], [contenteditable=true]')) {
  if (out.length >= 40) break;
  if (!visible(e)) continue;
  const tag = e.tagName.toLowerCase();
  const type = (e.getAttribute('type') || '').toLowerCase();
  if (tag === 'input' && type === 'hidden') continue;
  let line;
  if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.isContentEditable) {
    const hint = e.getAttribute('placeholder') || e.getAttribute('aria-label') || e.getAttribute('name') || e.id || '';
    const sel = e.id ? '#' + e.id : (e.getAttribute('name') ? tag + '[name="' + e.getAttribute('name') + '"]' : '');
    line = (type === 'submit' || type === 'button') ? 'button "' + label(e) + '"'
         : 'field "' + hint + '"' + (sel ? ' ' + sel : '') + (e.value ? ' = "' + String(e.value).slice(0, 40) + '"' : '');
  } else if (tag === 'a') {
    const t = label(e); if (!t) continue; line = 'link "' + t + '"';
  } else {
    const t = label(e); if (!t) continue; line = 'button "' + t + '"';
  }
  if (!seen.has(line)) { seen.add(line); out.push(line); }
}
return JSON.stringify({title: document.title, text: (document.body && document.body.innerText) || '', elements: out});
"""

_KEYS = {"RETURN": "ENTER", "ESC": "ESCAPE", "DEL": "DELETE", "UP": "ARROWUP", "DOWN": "ARROWDOWN",
         "LEFT": "ARROWLEFT", "RIGHT": "ARROWRIGHT", "PGDN": "PAGEDOWN", "PGUP": "PAGEUP", "SPACEBAR": "SPACE"}


class Browser:
    """One headless Chromium for both browser engines, one operation at a time. Pydoll drives
    single steps; the Browser Use agent attaches to the same browser over CDP for whole tasks."""

    def __init__(self):
        self.chrome = None
        self.tab = None
        self._lock = None
        self._prefer_url = ""

    def lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()          # made on the server's loop, at first use
        return self._lock

    def cdp_url(self):
        return "http://127.0.0.1:%d" % CDP_PORT

    async def _launch(self):
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

    @staticmethod
    async def _ping(tab):
        try:
            await asyncio.wait_for(tab.execute_script("return 1", return_by_value=True), 5)
            return True
        except Exception:
            return False

    async def _live_tab(self):
        """The tab to drive, alive. Ours when it still answers; else an open one (the Browser Use
        agent may have closed or replaced ours), preferring the page it ended on; else a new tab;
        and a browser that died is started again."""
        if self.chrome is not None:
            if self.tab is not None and await self._ping(self.tab):
                return self.tab
            self.tab = None
            try:
                tabs = [t for t in await asyncio.wait_for(self.chrome.get_opened_tabs(), 8) if await self._ping(t)]
                chosen = None
                for t in tabs:
                    try:
                        if self._prefer_url and await t.current_url() == self._prefer_url:
                            chosen = t
                            break
                    except Exception:
                        pass
                chosen = chosen or (tabs[0] if tabs else None)
                if chosen is None:
                    chosen = await asyncio.wait_for(self.chrome.new_tab(), 10)
                for t in tabs:                              # one page at a time: no pile of tabs
                    if t is not chosen:
                        try:
                            await asyncio.wait_for(t.close(), 5)
                        except Exception:
                            pass
                if await self._ping(chosen):
                    self.tab = chosen
                    return chosen
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.chrome.stop(), 10)
            except Exception:
                pass
            self.chrome = self.tab = None
        return await self._launch()

    async def _page(self, tab, **more):
        out = {"ok": True, "url": await tab.current_url(), "title": await _title(tab)}
        out.update(more)
        return out

    async def open(self, url):
        url = str(url or "").strip()
        if not url:
            return {"ok": False, "error": "open needs a url"}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        async with self.lock():
            tab = await self._live_tab()
            try:
                await tab.go_to(url, timeout=45)
            except Exception as e:
                return {"ok": False, "error": "could not open %s: %s" % (url, _short(e))}
            return await self._page(tab)

    async def click(self, target):
        async with self.lock():
            tab = await self._live_tab()
            el = await _find(tab, target)
            if el is None:
                return {"ok": False, "error": "nothing on the page matches %r (op=extract lists what is there)" % target}
            before = await tab.current_url()
            try:
                await el.scroll_into_view()
            except Exception:
                pass
            await el.click()
            await _settle(tab, before)
            return await self._page(tab)

    async def type(self, target, text, submit=False):
        text = str(text if text is not None else "")
        async with self.lock():
            tab = await self._live_tab()
            el = await _find(tab, target, field=True)
            if el is None:
                return {"ok": False, "error": "no field matches %r (op=extract lists the fields)" % target}
            await el.click()                                   # focus it, as a person would
            try:
                await el.clear()
            except Exception:
                pass
            await el.insert_text(text)
            value = await _active_value(tab)
            if value.strip() != text.strip():                  # some fields only take real key presses
                try:
                    await el.clear()
                except Exception:
                    pass
                await el.type_text(text)
                value = await _active_value(tab)
            if value.strip() != text.strip():
                return {"ok": False, "error": "the field did not take the text (it holds %r)" % value[:80]}
            before = await tab.current_url()
            if submit:
                await _enter(tab)
                await _settle(tab, before)
            return await self._page(tab, value=value[:200])

    async def press(self, key):
        from pydoll.constants import Key
        name = str(key or "Enter").strip().upper().replace(" ", "").replace("_", "")
        k = getattr(Key, _KEYS.get(name, name), None)
        if k is None:
            return {"ok": False, "error": "unknown key %r (Enter, Tab, Escape, Backspace, ArrowDown, PageDown...)" % key}
        async with self.lock():
            tab = await self._live_tab()
            before = await tab.current_url()
            if k == Key.ENTER:
                await _enter(tab)
            else:
                await tab.keyboard.press(k)
            await _settle(tab, before)
            return await self._page(tab)

    async def back(self):
        async with self.lock():
            tab = await self._live_tab()
            before = await tab.current_url()
            await tab.execute_script("history.back()")
            await _settle(tab, before)
            return await self._page(tab)

    async def extract(self):
        async with self.lock():
            tab = await self._live_tab()
            raw = _script_value(await tab.execute_script(_PAGE_SUMMARY_JS, return_by_value=True))
            try:
                page = json.loads(raw)
            except ValueError:
                page = {"title": "", "text": raw, "elements": []}
            return {"ok": True, "url": await tab.current_url(), "title": page.get("title", ""),
                    "text": _clip(page.get("text", ""), 8000), "elements": page.get("elements", [])}

    async def screenshot(self):
        async with self.lock():
            tab = await self._live_tab()
            b64 = await tab.take_screenshot(as_base64=True, quality=60)
            return {"ok": True, "png_base64": b64, "url": await tab.current_url()}

    async def run_agent(self, task, max_steps=25):
        from browser_use import Agent, BrowserSession
        async with self.lock():
            await self._live_tab()
            session = BrowserSession(cdp_url=self.cdp_url(), keep_alive=True)
            urls = []
            try:
                agent = Agent(task=task, llm=browser_use_llm(), browser_session=session,
                              use_vision=False, max_actions_per_step=4, max_failures=3, llm_timeout=150)
                history = await agent.run(max_steps=int(max_steps))
                urls = [str(u) for u in (history.urls() or []) if u]
                return {"ok": True, "result": _clip(str(history.final_result() or ""), 4000),
                        "success": history.is_successful(), "steps": history.number_of_steps(),
                        "urls": urls[-5:]}
            finally:
                try:
                    await asyncio.wait_for(session.stop(), 15)     # detach; the browser stays
                except Exception:
                    pass
                self._prefer_url = urls[-1] if urls else ""
                self.tab = None            # the agent may have closed or replaced our tab

    async def close(self):
        if self.chrome is not None:
            try:
                await asyncio.wait_for(self.chrome.stop(), 10)
            except Exception:
                pass
        self.chrome = self.tab = None


def _short(error):
    return ("%s: %s" % (type(error).__name__, error))[:200]


async def _title(tab):
    try:
        return _script_value(await tab.execute_script("return document.title", return_by_value=True))
    except Exception:
        return ""


async def _enter(tab):
    """Enter as a real keyboard sends it: with its character, so the page's key listeners run
    and a form submits (a bare key event does not submit a form)."""
    try:
        from pydoll.commands import InputCommands
        from pydoll.protocol.input.types import KeyEventType
        key = dict(key="Enter", code="Enter", windows_virtual_key_code=13, native_virtual_key_code=13)
        await tab._execute_command(InputCommands.dispatch_key_event(type=KeyEventType.KEY_DOWN, text="\r", **key))
        await tab._execute_command(InputCommands.dispatch_key_event(type=KeyEventType.KEY_UP, **key))
        return
    except Exception:
        pass
    done = _script_value(await tab.execute_script(
        "const a = document.activeElement; if (a && a.form) { a.form.requestSubmit ? a.form.requestSubmit() "
        ": a.form.submit(); return 'form'; } return 'none';", return_by_value=True))
    if done != "form":
        from pydoll.constants import Key
        await tab.keyboard.press(Key.ENTER)


async def _active_value(tab):
    """What the focused field holds now."""
    try:
        return str(_script_value(await tab.execute_script(
            "const a = document.activeElement; "
            "return a ? String(a.value !== undefined ? a.value : (a.innerText || '')) : ''",
            return_by_value=True)) or "")
    except Exception:
        return ""


async def _settle(tab, before, limit=8.0):
    """After an action: give a navigation a moment to start, then wait for the page to be ready."""
    await asyncio.sleep(0.4)
    end = time.monotonic() + limit
    while time.monotonic() < end:
        try:
            state = _script_value(await asyncio.wait_for(
                tab.execute_script("return document.readyState", return_by_value=True), 5))
        except Exception:
            state = ""                                   # mid-navigation: the old document is gone
        if state == "complete":
            return
        await asyncio.sleep(0.25)


def _script_value(res):
    if isinstance(res, dict):
        r = res.get("result")
        if isinstance(r, dict):
            r = r.get("result", r)
        if isinstance(r, dict):
            return r.get("value", "")
        return r if isinstance(r, str) else ""
    return res if isinstance(res, str) else ""


def _looks_like_selector(target):
    if target.startswith(("/", "(")):                    # XPath
        return True
    if any(ch.isspace() for ch in target):
        return any(tok in target for tok in (" > ", "[", "]"))
    return target[:1] in "#.[" or any(ch in target for ch in "[]>=:") or (
        "." in target and not target.lower().startswith(("http", "www.")) and target.split(".")[0].isalpha()
        and target.split(".")[0].lower() in _TAGS)


_TAGS = {"a", "button", "input", "textarea", "select", "div", "span", "form", "label", "li", "img", "p",
         "h1", "h2", "h3", "td", "tr", "nav", "section", "summary"}


def _attr(name, value):
    return '[%s="%s"]' % (name, value.replace("\\", "\\\\").replace('"', '\\"'))


async def _try(coro, wait):
    try:
        return await asyncio.wait_for(coro, wait + 3)
    except Exception:
        return None


async def _find(tab, target, field=False):
    """The element a target names: a CSS/XPath selector, or what a person would call it — its
    visible text, or for a field its placeholder, label, name or id."""
    target = str(target or "").strip()
    if not target:
        return None
    if _looks_like_selector(target):
        el = await _try(tab.query(target, timeout=5, raise_exc=False), 5)
        if el:
            return el
    by_attr = ["placeholder", "aria-label", "name", "title"] if field else ["aria-label", "title", "value", "alt"]
    for name in by_attr:
        prefix = ("input%s, textarea%s, select%s" % ((_attr(name, target),) * 3)) if field else _attr(name, target)
        el = await _try(tab.query(prefix, timeout=0, raise_exc=False), 1)
        if el:
            return el
    if target.replace("-", "").replace("_", "").isalnum():
        el = await _try(tab.query("#" + target, timeout=0, raise_exc=False), 1)
        if el:
            return el
    el = await _try(tab.find(text=target, timeout=4, raise_exc=False), 4)
    if el:
        return el
    if not _looks_like_selector(target):
        return await _try(tab.query(target, timeout=1, raise_exc=False), 1)
    return None


BROWSER = Browser()


# ---------------------------------------------------------------------------
# uiautomator2 power backend (only when an ADB device is reachable)
# ---------------------------------------------------------------------------

A11Y_NOTE = ("MusabAI's accessibility service did not come back after uiautomator2 detached. The user must switch "
             "it off and on in Settings > Accessibility > MusabAI: phone control before the phone tool, the task "
             "memory or phone_agent can work again.")
OLD_ANDROID_NOTE = ("uiautomator2 is not safe on this phone while an accessibility service is switched on: Android 9 "
                    "and older have a system bug that, when uiautomator2 detaches, can restart the phone's interface "
                    "or leave the accessibility service dead. Use the phone tool or phone_agent instead (they work "
                    "through accessibility). phone_power works here only after the user switches every accessibility "
                    "service off in Settings > Accessibility.")
A11Y_IDLE_NOTE = ("MusabAI's accessibility service was not reading the screen before this call and still is not: the "
                  "screen may be off or locked, or the service needs to be switched off and on in Settings > "
                  "Accessibility > MusabAI: phone control.")


class U2:
    """uiautomator2, attached only for the length of one call.

    While its server is attached, Android unbinds every other accessibility service, MusabAI's own
    phone bridge included: the phone tool, the task memory and the Mobilerun agent are blind until
    it detaches. And on some Android versions the bridge then comes back dead (measured on
    Android 9, whose handler for a dying UiAutomation is unsynchronized: about one detach in three),
    while one more attach-and-detach revives it. So a call notes how the bridge was, attaches, acts,
    detaches, checks that the bridge reads the screen again, and repeats the detach cycle until it
    does. A bridge the user never switched on is left alone. On Android 9 and older the detach
    itself can crash the system while any accessibility service is on: there it does not attach."""

    REPAIRS = 4          # extra attach-and-detach cycles when a working bridge stays dead
    SETTLE = 8.0         # seconds Android gets to rebind the bridge after a detach
    SAFE_SDK = 29        # Android 10 serialized UiAutomationManager.destroyUiAutomationService
    SERVER = "com.wetest.uia2.Main"

    def __init__(self):
        self.serial = None
        self._lock = threading.Lock()

    def _adb(self, serial=None):
        import adbutils
        serial = serial or self.serial
        return adbutils.adb.device(serial=serial) if serial else adbutils.adb.device()

    def unsafe(self, adb):
        """Why uiautomator2 must not attach to this phone now, or "" when it may.

        uiautomator2 detaches by killing its server. On Android 9 and older the two death handlers
        in UiAutomationManager race (no lock, no null check): the accessibility services are then
        registered twice and system_server dies with "Windows for accessibility callback already
        set!" (seen on Android 9: the whole interface restarted), or the service stays bound but
        dead. With no accessibility service switched on there is nothing to register twice."""
        try:
            sdk = int(str(adb.shell("getprop ro.build.version.sdk", timeout=20)).strip())
        except ValueError:
            sdk = 0
        if sdk >= self.SAFE_SDK:
            return ""
        state = str(adb.shell("settings get secure accessibility_enabled; "
                              "settings get secure enabled_accessibility_services", timeout=20))
        lines = [line.strip() for line in state.splitlines() if line.strip()]
        on = lines[0] != "0" if lines else False
        services = lines[1] if len(lines) > 1 else ""
        return OLD_ANDROID_NOTE if on and services not in ("", "null") else ""

    def _attach(self, adb):
        import uiautomator2 as u2
        try:
            device = u2.connect(adb)      # starts the server
        except Exception:
            self._sweep(adb)              # never leave a half-started server holding the screen
            raise
        try:
            device.info                   # an attach that does not answer, fails here
        except Exception:
            self._detach(device, adb)
            raise
        return device

    def _detach(self, device, adb):
        try:
            device.stop_uiautomator()
        except Exception:
            pass
        try:
            atexit.unregister(device.stop_uiautomator)        # the library registers one per attach
        except Exception:
            pass
        self._sweep(adb)

    def _sweep(self, adb):
        """No uiautomator2 server outlives a call: one that does keeps every accessibility service
        unbound. ([M] keeps the shell that runs pgrep out of its own match.)"""
        pattern = self.SERVER[:-4] + "[" + self.SERVER[-4] + "]" + self.SERVER[-3:]
        try:
            if str(adb.shell("pgrep -f '%s'" % pattern, timeout=20)).strip():
                adb.shell("pkill -f '%s'" % pattern, timeout=20)
                time.sleep(1.0)
        except Exception:
            pass

    def bridge(self):
        """MusabAI's accessibility bridge right now: "ok" (it reads the screen), "broken" (switched on
        but not reading), "off" (the user has not switched it on), or None when there is no app to ask.
        a11y_state only looks (the screen actions would wait for the service and open its settings
        page); the screen is then read once, because a dead service can still look connected."""
        if not PHONE_KEY:
            return None
        try:
            state = phone_call("a11y_state")
        except PhoneError as e:
            text = str(e).lower()
            if "not reachable" in text:
                return None
            if "unknown" not in text:
                return "broken"
            state = None                  # an app from before a11y_state: read the screen
        if state is not None:
            if state.get("enabled") is False:
                return "off"
            if not (state.get("connected") and state.get("alive")):
                return "broken"
        try:
            phone_call("screen_tree")
        except PhoneError:
            return "broken"
        return "ok"

    def bridge_back(self, wait=None):
        """Wait for the bridge to read the screen again after a detach."""
        end = time.monotonic() + (self.SETTLE if wait is None else wait)
        while True:
            if self.bridge() == "ok":
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(1.0)

    def _release(self, device, adb, before):
        """Detach, then bring back the bridge this call took away. Returns (back, repair cycles);
        back is None when there was no bridge to bring back."""
        self._detach(device, adb)
        if before not in ("ok", "broken"):
            return None, 0
        back = self.bridge_back()
        cycles = 0
        limit = self.REPAIRS if before == "ok" else 1     # one try for a bridge that was already down
        while not back and cycles < limit:
            cycles += 1
            try:
                self._detach(self._attach(adb), adb)
            except Exception:
                pass
            back = self.bridge_back()
        return back, cycles

    def run(self, op, args):
        """One uiautomator2 action, with the bridge restored afterwards."""
        with self._lock:
            adb = self._adb(args.get("serial"))
            refusal = self.unsafe(adb)
            if refusal:
                return {"ok": False, "error": refusal}
            before = self.bridge()
            device = self._attach(adb)
            failure = None
            try:
                out = self._op(device, op, args)
            except Exception as e:
                failure, out = e, None
            back, cycles = self._release(device, adb, before)
            note = "" if back in (None, True) else (A11Y_NOTE if before == "ok" else A11Y_IDLE_NOTE)
            if failure is not None:
                raise RuntimeError(_short(failure) + ("  " + note if note else ""))
            if back is not None:
                out["accessibility"] = "restored" if back else ("suspended" if before == "ok" else "unavailable")
                if cycles:
                    out["repair_cycles"] = cycles
                if note:
                    out["note"] = note
            return out

    def _op(self, d, op, args):
        if op == "connect":
            if args.get("serial"):
                self.serial = args["serial"]
            info = d.info
            return {"ok": True, "serial": getattr(d, "serial", self.serial or ""), "sdk": info.get("sdkInt"),
                    "display": {"w": info.get("displayWidth"), "h": info.get("displayHeight")}}
        if op == "dump":
            return {"ok": True, "xml": _clip(d.dump_hierarchy(), 20000)}
        if op == "click_text":
            target = d(text=args["text"])
            if not target.exists:
                target = d(textContains=args["text"])
            if not target.exists:
                return {"ok": False, "error": "nothing on the screen says %r" % args["text"]}
            target.click()
            return {"ok": True}
        if op == "click_xy":
            d.click(args["x"], args["y"])
            return {"ok": True}
        if op == "send_keys":
            d.send_keys(args["text"], clear=bool(args.get("clear")))
            return {"ok": True}
        if op == "press":
            d.press(args.get("key") or "back")
            return {"ok": True}
        if op == "app_start":
            d.app_start(args["package"], stop=bool(args.get("stop")))
            return {"ok": True}
        if op == "app_current":
            return {"ok": True, "current": d.app_current()}
        raise ValueError("unknown uiautomator2 op %r: dump, click_text, click_xy, send_keys, press, app_start "
                         "or app_current" % op)

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
            if ok:
                self.serial = address
            return {"ok": ok, "output": (p.stdout + p.stderr).strip()[:500]}
        raise ValueError("unknown adb op: " + str(op))


U2BACKEND = U2()


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

MAX_BODY = 1 << 20

_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, name="musab-automation-loop", daemon=True).start()
_PHONE_TASK = threading.Lock()          # the phone has one screen: one agent on it at a time
_BUSY = {"phone_agent": False, "browser_agent": False}


def _await(coro, timeout=600):
    """Run a coroutine on the server's loop from a request thread. A call that outlives its
    time is cancelled there, so it releases what it holds instead of running on unseen."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise TimeoutError("still not finished after %d s; stopped" % timeout)


def source_digest():
    """Which version of this file is running: the runtime replaces a server left by an older app."""
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def _steps(value, default, most):
    try:
        return max(1, min(int(value or default), most))
    except (TypeError, ValueError):
        return default


def dispatch(path, data):
    if path == "/health":
        return {"ok": True, "version": VERSION, "digest": source_digest(), "busy": dict(_BUSY),
                "llm": {"base": LLM_BASE, "model": LLM_MODEL},
                "phone": bool(PHONE_KEY), "chromium": os.path.exists(CHROMIUM_BIN)}
    if path == "/ping":                                   # an authenticated health check
        return {"ok": True, "version": VERSION, "digest": source_digest()}
    if path == "/shutdown":
        def stop():
            try:
                _await(BROWSER.close(), timeout=15)
            except Exception:
                pass
            os._exit(0)
        threading.Timer(0.3, stop).start()
        return {"ok": True, "stopping": True}
    if path == "/phone_agent":
        goal = str(data.get("goal") or "").strip()
        if not goal:
            return {"ok": False, "error": "phone_agent needs a goal"}
        if not _PHONE_TASK.acquire(blocking=False):
            return {"ok": False, "error": "another phone task is still running; wait for it to finish"}
        _BUSY["phone_agent"] = True
        try:
            steps = _steps(data.get("max_steps"), 18, 40)
            return _await(run_phone_agent(goal, steps), timeout=min(900, 120 + 45 * steps))
        finally:
            _BUSY["phone_agent"] = False
            _PHONE_TASK.release()
    if path == "/browser_agent":
        task = str(data.get("task") or "").strip()
        if not task:
            return {"ok": False, "error": "browser_agent needs a task"}
        _BUSY["browser_agent"] = True
        try:
            steps = _steps(data.get("max_steps"), 25, 60)
            return _await(BROWSER.run_agent(task, steps), timeout=min(900, 120 + 45 * steps))
        finally:
            _BUSY["browser_agent"] = False
    if path == "/browser":
        op = data.get("op")
        if op == "open":
            return _await(BROWSER.open(data.get("url", "")), timeout=90)
        if op == "click":
            return _await(BROWSER.click(data.get("target", "")), timeout=60)
        if op == "type":
            return _await(BROWSER.type(data.get("target", ""), data.get("text", ""), bool(data.get("submit"))),
                          timeout=60)
        if op == "press":
            return _await(BROWSER.press(data.get("key") or data.get("text") or "Enter"), timeout=45)
        if op == "back":
            return _await(BROWSER.back(), timeout=45)
        if op == "extract":
            return _await(BROWSER.extract(), timeout=45)
        if op == "screenshot":
            return _await(BROWSER.screenshot(), timeout=45)
        return {"ok": False, "error": "unknown browser op %r: open, click, type, press, back or extract" % op}
    if path == "/u2":
        op = data.get("op")
        if isinstance(op, str) and op.startswith("adb_"):
            return U2BACKEND.adb(op, data.get("address", ""), data.get("code", ""))
        # uiautomator2 takes the accessibility bridge away while it is attached: never under a phone agent
        if not _PHONE_TASK.acquire(blocking=False):
            return {"ok": False, "error": "a phone task is still running; wait for it to finish"}
        try:
            return U2BACKEND.run(op, data)
        finally:
            _PHONE_TASK.release()
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

    def _authorized(self):
        given = self.headers.get("Authorization", "")
        given = given[7:].strip() if given.lower().startswith("bearer ") else ""
        return bool(TOKEN) and secrets.compare_digest(given.encode("utf-8"), TOKEN.encode("utf-8"))

    def do_POST(self):
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self._send(413, {"ok": False, "error": "request too large"})
            return
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if not isinstance(data, dict):
                raise ValueError("a JSON object is expected")
        except Exception as e:
            self._send(400, {"ok": False, "error": "bad request: %s" % e})
            return
        try:
            self._send(200, dispatch(self.path, data))
        except PhoneError as e:
            self._send(200, {"ok": False, "error": str(e)})
        except Exception as e:
            self._send(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        if self.path == "/health":                         # says only that it runs, and which version
            self._send(200, dispatch("/health", {}))
        else:
            self._send(404, {"ok": False, "error": "POST only"})


def main():
    if not TOKEN:
        raise SystemExit("MUSAB_AUTOMATION_TOKEN is not set: this server only runs with a token")
    host = os.environ.get("MUSAB_AUTOMATION_HOST", "127.0.0.1")
    port = int(os.environ.get("MUSAB_AUTOMATION_PORT", "8799"))
    server = ThreadingHTTPServer((host, port), Handler)
    print("musab-automation %d (%s) on %s:%d" % (VERSION, source_digest(), host, port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
