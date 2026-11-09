"""Install, run and call the MusabAI automation server, and expose its tools.

The automation server (automation_server.py) runs four real engines — Mobilerun and
uiautomator2 for the phone, Pydoll and Browser Use for the browser — in the managed
Debian container, because those libraries are Linux-only. This module installs them
in a dedicated venv, writes the server into the container, starts it as a durable
localhost process, and gives the model a small set of tools that call it.

Everything is driven by the engine's own free provider pool through the local
/v1/free endpoint, so no key of the user's own is needed.

Set MUSAB_AUTOMATION_ORIGIN to point at an already-running server (tests, or a server
started by hand); ensure() then does nothing and the tools just call it.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
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
_STATE = {"token": "", "process_id": "", "ready": False}


# ---------------------------------------------------------------------------
# calling the server
# ---------------------------------------------------------------------------

def origin():
    return os.environ.get("MUSAB_AUTOMATION_ORIGIN", "http://127.0.0.1:%d" % PORT).rstrip("/")


def _token():
    if not _STATE["token"]:
        _STATE["token"] = os.environ.get("MUSAB_AUTOMATION_TOKEN") or secrets.token_urlsafe(24)
    return _STATE["token"]


def call(path, body=None, timeout=600):
    data = json.dumps(body or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("MUSAB_AUTOMATION_ORIGIN") is None:
        headers["Authorization"] = "Bearer " + _token()
    req = urllib.request.Request(origin() + path, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise tools.ToolError("automation server error %d: %s" % (e.code, e.read()[:300].decode("utf-8", "replace")))
    except OSError as e:
        raise tools.ToolError("automation server unreachable: %s" % e)
    if isinstance(out, dict) and out.get("ok") is False and out.get("error"):
        raise tools.ToolError(str(out["error"]))
    return out


def health(timeout=4):
    try:
        req = urllib.request.Request(origin() + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# install + start in the managed Debian container
# ---------------------------------------------------------------------------

def _server_source():
    from . import automation_server
    with open(automation_server.__file__, "r", encoding="utf-8") as f:
        return f.read()


def ensure(cancel=None):
    """Install the libraries and start the server; a no-op when an origin override is set."""
    if os.environ.get("MUSAB_AUTOMATION_ORIGIN"):
        return
    with _LOCK:
        if _STATE["ready"] and health() is not None:
            return
        from . import managed_linux as ml, runtime_manager as rm, phone, server
        import base64, shlex

        def run(command):
            process = rm.process_start(command)
            from . import mcp_bundles
            mcp_bundles._wait_termux_process(process["id"], timeout=1800)

        def exists(command):
            result = rm.execute(command, rm.TERMUX)
            return result.get("status") == "completed" and result.get("exit_code") == 0

        if not exists("command -v proot-distro"):
            run("pkg install -y proot-distro")
        if not exists(ml.login("test -f /etc/debian_version")):
            run("proot-distro install " + ml.IMAGE + " --name " + ml.CONTAINER)

        probe = ("test -x " + ROOT + "/venv/bin/python && " + ROOT
                 + "/venv/bin/python -c 'import mobilerun,uiautomator2,pydoll,browser_use' "
                   "&& test -x /usr/bin/chromium")
        if not exists(ml.login(probe)):
            run(ml.login("export DEBIAN_FRONTEND=noninteractive; apt-get update && "
                         "apt-get install -y --no-install-recommends " + APT))
            run(ml.login("mkdir -p " + ROOT + " && python3 -m venv " + ROOT + "/venv && "
                         + ROOT + "/venv/bin/python -m pip install --upgrade pip && "
                         + ROOT + "/venv/bin/python -m pip install " + " ".join(shlex.quote(r) for r in REQUIREMENTS)))
            if not exists(ml.login(probe)):
                raise tools.ToolError("automation libraries did not verify in the container; nothing was started")

        # Write the current server source into the container.
        src64 = base64.b64encode(_server_source().encode("utf-8")).decode("ascii")
        run(ml.login("mkdir -p " + ROOT + " && printf %s " + shlex.quote(src64)
                     + " | base64 -d > " + SERVER_PATH))

        phone_url, phone_key = phone.config()
        env = {
            "MUSAB_PHONE_ORIGIN": phone_url or "http://127.0.0.1:8796",
            "MUSAB_PHONE_KEY": phone_key or "",
            "MUSAB_LLM_BASE": "http://127.0.0.1:%d/v1/free" % _engine_port(),
            "MUSAB_LLM_KEY": server.server_key(),
            "MUSAB_LLM_MODEL": os.environ.get("MUSAB_LLM_MODEL", "auto"),
            "MUSAB_AUTOMATION_TOKEN": _token(),
            "MUSAB_AUTOMATION_PORT": str(PORT),
            "CHROMIUM_BIN": "/usr/bin/chromium",
        }
        exports = " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in env.items())
        proc = rm.process_start(ml.login(
            "cd " + ROOT + " && export " + exports + " && exec ./venv/bin/python automation_server.py"))
        _STATE["process_id"] = proc["id"]

        deadline = time.time() + 40
        while time.time() < deadline:
            if cancel is not None and cancel.is_set():
                from . import providers
                raise providers.Cancelled()
            if health() is not None:
                _STATE["ready"] = True
                return
            time.sleep(1.0)
        raise tools.ToolError("automation server did not become healthy in time")


def _engine_port():
    try:
        from . import server
        return int(getattr(server, "PORT", 0)) or 8795
    except Exception:
        return 8795


def _ensure_and_call(path, body, timeout=600):
    ensure()
    return call(path, body, timeout=timeout)


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def _summary(result):
    if isinstance(result, dict):
        return json.dumps({k: v for k, v in result.items() if k != "png_base64"}, ensure_ascii=False)[:1500]
    return str(result)[:1500]


@tools.tool("phone_agent",
            "Carry out a whole task on this phone autonomously with the Mobilerun agent, which looks at the real "
            "screen and taps, types and swipes until the goal is done. Use for multi-step phone goals in any app "
            "(the task memory and the plain phone tool remain for single known steps). Needs the accessibility "
            "service on.",
            {"goal": tools._s("the task in plain words, e.g. 'open WhatsApp and send Ali: on my way'"),
             "max_steps": tools._i("step budget (default 18)")}, ["goal"], "phone")
def t_phone_agent(ctx, goal, max_steps=18):
    out = _ensure_and_call("/phone_agent", {"goal": str(goal or ""), "max_steps": int(max_steps or 18)}, timeout=900)
    return _summary(out), out if isinstance(out, dict) else {}


@tools.tool("phone_power",
            "Low-level phone control through uiautomator2 (a power backend) when an ADB device is connected: "
            "op=dump|click_text|click_xy|send_keys|press|app_start|app_current. Use adb_connect first; prefer "
            "phone_agent or the phone tool when ADB is not set up.",
            {"op": tools._s("dump, click_text, click_xy, send_keys, press, app_start or app_current"),
             "text": tools._s("text for click_text/send_keys"),
             "x": tools._i("x for click_xy"), "y": tools._i("y for click_xy"),
             "key": tools._s("key for press, e.g. back"),
             "package": tools._s("package for app_start"),
             "clear": tools._b("clear the field before send_keys")}, ["op"], "phone")
def t_phone_power(ctx, op, text="", x=0, y=0, key="", package="", clear=False):
    body = {"op": str(op), "text": text, "x": int(x or 0), "y": int(y or 0),
            "key": key, "package": package, "clear": bool(clear)}
    out = _ensure_and_call("/u2", body, timeout=120)
    return _summary(out), out if isinstance(out, dict) else {}


@tools.tool("browser",
            "Drive the live browser one step at a time (Pydoll over a real Chromium): op=open(url), click(target: a "
            "CSS selector or visible text), type(target,text), or extract (the page's visible text, to read the "
            "result). For a whole multi-step web task use browser_agent instead.",
            {"op": tools._s("open, click, type or extract"),
             "url": tools._s("url for open"),
             "target": tools._s("CSS selector or visible text for click/type"),
             "text": tools._s("text for type")}, ["op"], "run")
def t_browser(ctx, op, url="", target="", text=""):
    body = {"op": str(op), "url": url, "target": target, "text": text}
    out = _ensure_and_call("/browser", body, timeout=120)
    return _summary(out), out if isinstance(out, dict) else {}


@tools.tool("browser_agent",
            "Carry out a whole web task autonomously with the Browser Use agent on the live Chromium: it navigates, "
            "clicks, fills forms and reads pages until the task is done, then reports the result. Use for multi-step "
            "web goals; use the browser tool for a single precise action.",
            {"task": tools._s("the web task in plain words, e.g. 'find the cheapest flight BGW->IST next Friday'"),
             "max_steps": tools._i("step budget (default 25)")}, ["task"], "run")
def t_browser_agent(ctx, task, max_steps=25):
    out = _ensure_and_call("/browser_agent", {"task": str(task or ""), "max_steps": int(max_steps or 25)}, timeout=900)
    return _summary(out), out if isinstance(out, dict) else {}


@tools.tool("adb_connect",
            "Connect this phone's own ADB over wireless debugging so the uiautomator2 power backend can drive it "
            "(no PC). Turn on Developer options > Wireless debugging first. op=pair (with address host:port and the "
            "6-digit code) then op=connect (with the connect address), or op=status.",
            {"op": tools._s("pair, connect or status"),
             "address": tools._s("host:port shown by Wireless debugging"),
             "code": tools._s("the 6-digit pairing code (for pair)")}, ["op"], "run")
def t_adb_connect(ctx, op, address="", code=""):
    out = _ensure_and_call("/u2", {"op": "adb_" + str(op), "address": address, "code": code}, timeout=120)
    return _summary(out), out if isinstance(out, dict) else {}


NAMES = ["phone_agent", "phone_power", "browser", "browser_agent", "adb_connect"]
