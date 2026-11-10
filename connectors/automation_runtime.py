"""Install, run and call the MusabAI automation server, and expose its tools.

The automation server (automation_server.py) runs four real engines — Mobilerun and
uiautomator2 for the phone, Pydoll and Browser Use for the browser — in the managed
Debian container, because those libraries are Linux-only. This module installs them
in a dedicated venv, writes the server into the container, starts it as a durable
localhost process, and gives the model a small set of tools that call it.

Everything is driven by the engine's own free provider pool through the local
/v1/free endpoint, so no key of the user's own is needed.

The server outlives the engine (it is a Termux process). So its token is derived from
the engine's own key, the same after every restart, and a server left by an older app
(another source digest) is stopped and replaced.

Set MUSAB_AUTOMATION_ORIGIN to point at an already-running server (tests, or a server
started by hand); ensure() then does nothing and the tools just call it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import threading
import time
import urllib.error
import urllib.request

from . import tools

PORT = int(os.environ.get("MUSAB_AUTOMATION_PORT", "8799"))
REQUIREMENTS = (
    "mobilerun", "uiautomator2", "pydoll-python>=3,<4", "browser-use==0.13.5",
)
_DIGEST = hashlib.sha256((" ".join(REQUIREMENTS)).encode()).hexdigest()[:12]
ROOT = "/opt/musabai/automation/" + _DIGEST
SERVER_PATH = ROOT + "/automation_server.py"
APT = "ca-certificates python3 python3-venv chromium android-tools-adb"

_LOCK = threading.Lock()
_STATE = {"token": "", "engine": "", "ready_digest": ""}


# ---------------------------------------------------------------------------
# calling the server
# ---------------------------------------------------------------------------

def _external():
    return os.environ.get("MUSAB_AUTOMATION_ORIGIN")


def origin():
    return (_external() or "http://127.0.0.1:%d" % PORT).rstrip("/")


def _token():
    """The same after every engine restart: derived from the engine's own server key."""
    if not _STATE["token"]:
        given = os.environ.get("MUSAB_AUTOMATION_TOKEN")
        if not given:
            from . import server
            given = hashlib.sha256(("musab-automation:" + server.server_key()).encode("utf-8")).hexdigest()[:48]
        _STATE["token"] = given
    return _STATE["token"]


def _open(req, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 127.0.0.1: never a proxy
    return opener.open(req, timeout=timeout)


def call(path, body=None, timeout=600):
    data = json.dumps(body or {}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + _token()}
    req = urllib.request.Request(origin() + path, data=data, method="POST", headers=headers)
    try:
        with _open(req, timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise tools.ToolError("automation server error %d: %s" % (e.code, e.read()[:300].decode("utf-8", "replace")))
    except OSError as e:
        _STATE["ready_digest"] = ""                       # look again next time: it may have stopped
        raise tools.ToolError("the automation server does not answer (%s); try again" % e)
    if isinstance(out, dict) and out.get("ok") is False and out.get("error"):
        raise tools.ToolError(str(out["error"]))
    return out


def health(timeout=4):
    try:
        with _open(urllib.request.Request(origin() + "/health", method="GET"), timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _authorized(timeout=5):
    """Whether the server on the port takes this engine's token (it is ours)."""
    req = urllib.request.Request(origin() + "/ping", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + _token()})
    try:
        with _open(req, timeout) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# install + start in the managed Debian container
# ---------------------------------------------------------------------------

def _server_bytes():
    from . import automation_server
    with open(automation_server.__file__, "rb") as f:
        return f.read()


def source_digest():
    return hashlib.sha256(_server_bytes()).hexdigest()[:16]


def _fresh():
    """The server is up, it is this app's version, and it takes our token."""
    state = health()
    return bool(state) and state.get("digest") == source_digest() and _authorized()


def engine_base():
    """Where this engine's own HTTP server listens (the automation server calls /v1/free there)."""
    if _STATE["engine"]:
        return _STATE["engine"]
    from . import server, settings
    key = server.server_key()
    ports = []
    given = os.environ.get("MUSAB_ENGINE_BASE", "").rstrip("/")
    if given:
        _STATE["engine"] = given
        return given
    for p in (8795, int(settings.user().get("port") or 0), 8790):
        if p and p not in ports:
            ports.append(p)
    for p in ports:
        req = urllib.request.Request("http://127.0.0.1:%d/api/health" % p, headers={"X-NewAl-Key": key})
        try:
            with _open(req, 3) as r:
                if json.loads(r.read().decode("utf-8")).get("ok"):
                    _STATE["engine"] = "http://127.0.0.1:%d" % p
                    return _STATE["engine"]
        except Exception:  # noqa: BLE001
            continue
    return "http://127.0.0.1:%d" % ports[0]


def ensure(ctx=None):
    """Install the libraries and start the server; a no-op when an origin override is set.
    ctx (a tool's context) gives progress lines to the user and lets Stop cancel a long install."""
    if _external():
        return
    if _STATE["ready_digest"] and _STATE["ready_digest"] == (health() or {}).get("digest"):
        return
    with _LOCK:
        if _fresh():
            _STATE["ready_digest"] = source_digest()
            return
        from . import managed_linux as ml, mcp_bundles, runtime_manager as rm, phone, server, providers
        cancel = getattr(ctx, "cancel", None)

        def say(text):
            emit = getattr(ctx, "emit", None)
            if callable(emit):
                try:
                    emit({"type": "output", "text": text})
                except Exception:  # noqa: BLE001
                    pass

        def stopped():
            if cancel is not None and cancel.is_set():
                raise providers.Cancelled()

        def run(command, timeout=1800):
            stopped()
            process = rm.process_start(command)
            mcp_bundles._wait_termux_process(process["id"], timeout=timeout)

        def exists(command):
            result = rm.execute(command, rm.TERMUX)
            return result.get("status") == "completed" and result.get("exit_code") == 0

        with mcp_bundles.cancel_scope(cancel):
            if not exists("command -v proot-distro"):
                say("Preparing the automation engines (first time only): installing proot-distro in Termux...")
                run("pkg install -y proot-distro")
            if not exists(ml.login("test -f /etc/debian_version")):
                say("Setting up the Linux environment for the automation engines...")
                run("proot-distro install " + ml.IMAGE + " --name " + ml.CONTAINER)

            probe = ("test -x " + ROOT + "/venv/bin/python && " + ROOT
                     + "/venv/bin/python -c 'import mobilerun,uiautomator2,pydoll,browser_use' "
                       "&& test -x /usr/bin/chromium")
            if not exists(ml.login(probe)):
                say("Installing Chromium and system packages (a few minutes, once)...")
                run(ml.login("export DEBIAN_FRONTEND=noninteractive; apt-get update && "
                             "apt-get install -y --no-install-recommends " + APT))
                say("Installing Mobilerun, uiautomator2, Pydoll and Browser Use (a few minutes, once)...")
                run(ml.login("mkdir -p " + ROOT + " && python3 -m venv " + ROOT + "/venv && "
                             + ROOT + "/venv/bin/python -m pip install --upgrade pip && "
                             + ROOT + "/venv/bin/python -m pip install "
                             + " ".join(shlex.quote(r) for r in REQUIREMENTS)))
                if not exists(ml.login(probe)):
                    raise tools.ToolError("the automation libraries did not verify in the Linux environment; "
                                          "nothing was started")

            # A server from before (an older app, or one this engine can no longer talk to) goes first.
            if health() is not None:
                try:
                    call("/shutdown", {}, timeout=5)
                    _wait(lambda: health() is None, 8)
                except tools.ToolError:
                    pass                                  # not ours to ask: it is stopped below
            if health() is not None:
                rm.execute(ml.login("pkill -f automation_server.py || true"), rm.TERMUX)
                _wait(lambda: health() is None, 8)
            if health() is not None:
                raise tools.ToolError("an older automation server still holds port %d; close Termux and open it "
                                      "again, then retry" % PORT)

            say("Starting the automation server...")
            src64 = base64.b64encode(_server_bytes()).decode("ascii")
            run(ml.login("mkdir -p " + ROOT + " && printf %s " + shlex.quote(src64)
                         + " | base64 -d > " + SERVER_PATH), timeout=120)

            phone_url, phone_key = phone.config()
            env = {
                "MUSAB_PHONE_ORIGIN": phone_url or "http://127.0.0.1:8796",
                "MUSAB_PHONE_KEY": phone_key or "",
                "MUSAB_LLM_BASE": engine_base() + "/v1/free",
                "MUSAB_LLM_KEY": server.server_key(),
                "MUSAB_LLM_MODEL": os.environ.get("MUSAB_LLM_MODEL", "auto"),
                "MUSAB_AUTOMATION_TOKEN": _token(),
                "MUSAB_AUTOMATION_PORT": str(PORT),
                "CHROMIUM_BIN": "/usr/bin/chromium",
            }
            exports = " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in env.items())
            stopped()
            rm.process_start(ml.login(
                "cd " + ROOT + " && export " + exports + " && exec ./venv/bin/python automation_server.py"))
            if not _wait(_fresh, 60, stopped):
                raise tools.ToolError("the automation server did not start in time")
            _STATE["ready_digest"] = source_digest()


def _wait(done, seconds, check=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check is not None:
            check()
        if done():
            return True
        time.sleep(0.5)
    return bool(done())


def _ensure_and_call(ctx, path, body, timeout=600):
    ensure(ctx)
    return call(path, body, timeout=timeout)


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def _steps(value, default):
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _agent_text(out, what):
    if not isinstance(out, dict):
        return str(out)[:1500]
    said = str(out.get("reason") or out.get("result") or "").strip()
    steps = out.get("steps")
    tail = " (%s steps)" % steps if steps else ""
    if out.get("success"):
        return "Done%s: %s" % (tail, said or what)
    return ("Not completed%s: %s" % (tail, said or "the agent stopped without finishing")
            + "\nLook at the result yourself before telling the user it is done.")


def _page_text(out):
    if not isinstance(out, dict):
        return str(out)[:1500]
    head = "%s — %s" % (out.get("title") or "(no title)", out.get("url") or "")
    if "text" in out:
        lines = [head, "", str(out.get("text") or "").strip()]
        if out.get("elements"):
            lines += ["", "Can be used (click or type by these names):"] + ["  " + e for e in out["elements"]]
        return "\n".join(lines)
    if "value" in out:
        return "Typed. The field now holds: %s\n%s" % (out["value"], head)
    return head


@tools.tool("phone_agent",
            "Carry out a whole task on this phone autonomously with the Mobilerun agent, which looks at the real "
            "screen and taps, types and swipes until the goal is done, then reports. Use for goals that span "
            "several screens or apps (a task-memory tm: note, phone_routine or the plain phone tool stay faster "
            "for one known step). Needs the accessibility service on. The first use installs its engine in Termux "
            "(a few minutes, once).",
            {"goal": tools._s("the task in plain words, with the exact names and text to use, e.g. 'open WhatsApp "
                              "and send Ali: on my way'"),
             "max_steps": tools._i("step budget (default 18)")}, ["goal"], "phone")
def t_phone_agent(ctx, goal, max_steps=18):
    goal = str(goal or "").strip()
    if not goal:
        raise tools.ToolError("phone_agent needs a goal")
    if not _external():
        from . import phone
        try:
            phone.call("screen_tree", timeout=20)          # the screen is readable: accessibility is on
        except phone.PhoneError as e:
            raise tools.ToolError(str(e))
    steps = _steps(max_steps, 18)
    out = _ensure_and_call(ctx, "/phone_agent", {"goal": goal, "max_steps": steps}, timeout=min(930, 150 + 45 * steps))
    return _agent_text(out, goal), (out if isinstance(out, dict) else {})


@tools.tool("phone_power",
            "Low-level phone control through uiautomator2 when this phone's own ADB is connected (adb_connect "
            "first): op=dump (the UI as XML), click_text, click_xy, send_keys, press, app_start or app_current. "
            "Only for what the accessibility tools cannot reach; otherwise use phone or phone_agent.",
            {"op": tools._s("dump, click_text, click_xy, send_keys, press, app_start or app_current"),
             "text": tools._s("text for click_text/send_keys"),
             "x": tools._i("x for click_xy"), "y": tools._i("y for click_xy"),
             "key": tools._s("key for press, e.g. back"),
             "package": tools._s("package for app_start"),
             "clear": tools._b("clear the field before send_keys")}, ["op"], "phone")
def t_phone_power(ctx, op, text="", x=0, y=0, key="", package="", clear=False):
    body = {"op": str(op), "text": text, "x": int(x or 0), "y": int(y or 0),
            "key": key, "package": package, "clear": bool(clear)}
    out = _ensure_and_call(ctx, "/u2", body, timeout=120)
    if isinstance(out, dict) and "xml" in out:
        return str(out["xml"]), {"op": op}
    return json.dumps(out, ensure_ascii=False)[:1500], (out if isinstance(out, dict) else {})


@tools.tool("browser",
            "Drive a background browser one step at a time (Pydoll over a real Chromium): op=open(url), "
            "extract (the page's text and the names of its fields, buttons and links), click(target), "
            "type(target, text; submit=true presses Enter after), press(key) or back. A target is a name from "
            "extract (a button's or link's text, a field's placeholder or name) or a CSS selector. For a whole "
            "multi-step web task use browser_agent. Pages that need the user's own logged-in session belong in "
            "the phone's Chrome (phone open_url), not here.",
            {"op": tools._s("open, extract, click, type, press or back"),
             "url": tools._s("url for open"),
             "target": tools._s("for click/type: the element's visible name, or a CSS selector"),
             "text": tools._s("text for type"),
             "submit": tools._b("type: press Enter afterwards (search boxes, forms)"),
             "key": tools._s("press: Enter, Tab, Escape, ArrowDown, PageDown...")}, ["op"], "net")
def t_browser(ctx, op, url="", target="", text="", submit=False, key=""):
    body = {"op": str(op), "url": url, "target": target, "text": text, "submit": bool(submit), "key": key}
    out = _ensure_and_call(ctx, "/browser", body, timeout=120)
    meta = {k: v for k, v in out.items() if k not in ("text", "elements", "png_base64")} if isinstance(out, dict) else {}
    return _page_text(out), meta


@tools.tool("browser_agent",
            "Carry out a whole web task autonomously with the Browser Use agent in the background browser: it "
            "navigates, clicks, fills forms and reads pages until the task is done, then returns the answer. Use "
            "for multi-step web goals; use browser for one precise action, and web_search when no page visit is "
            "needed. The first use installs its engine in Termux (a few minutes, once).",
            {"task": tools._s("the web task in plain words, with the site and what to return, e.g. 'on "
                              "example.com find the cheapest flight BGW->IST next Friday and give its price'"),
             "max_steps": tools._i("step budget (default 25)")}, ["task"], "net")
def t_browser_agent(ctx, task, max_steps=25):
    task = str(task or "").strip()
    if not task:
        raise tools.ToolError("browser_agent needs a task")
    steps = _steps(max_steps, 25)
    out = _ensure_and_call(ctx, "/browser_agent", {"task": task, "max_steps": steps}, timeout=min(930, 150 + 45 * steps))
    return _agent_text(out, task), (out if isinstance(out, dict) else {})


@tools.tool("adb_connect",
            "Connect this phone's own ADB over wireless debugging so phone_power (uiautomator2) can drive it, "
            "with no computer. The user turns on Developer options > Wireless debugging; then op=pair with the "
            "pairing address (host:port) and the 6-digit code, op=connect with the connect address, op=status "
            "to list devices.",
            {"op": tools._s("pair, connect or status"),
             "address": tools._s("host:port shown by Wireless debugging"),
             "code": tools._s("the 6-digit pairing code (for pair)")}, ["op"], "phone")
def t_adb_connect(ctx, op, address="", code=""):
    op = str(op or "").strip().lower()
    if op not in ("pair", "connect", "status"):
        raise tools.ToolError("adb_connect op: pair, connect or status")
    out = _ensure_and_call(ctx, "/u2", {"op": "adb_" + op, "address": address, "code": code}, timeout=120)
    return json.dumps(out, ensure_ascii=False)[:1500], (out if isinstance(out, dict) else {})


NAMES = ["phone_agent", "phone_power", "browser", "browser_agent", "adb_connect"]
