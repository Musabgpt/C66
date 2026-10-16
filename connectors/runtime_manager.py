"""Unified runtime manager for MusabAI.

Phase 6 adds a real authenticated localhost bridge inside Termux. The Android
app uses RUN_COMMAND only to bootstrap/restart that bridge when necessary; once
up, command discovery, synchronous execution and durable process lifecycle all
flow over 127.0.0.1. ANDROID_NATIVE remains adb-free.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import termux_bridge_server, tools

ANDROID_NATIVE = "ANDROID_NATIVE"
TERMUX = "TERMUX"
REMOTE = "REMOTE"
PRODUCT_RUNTIMES = (ANDROID_NATIVE, TERMUX, REMOTE)
TERMUX_BASE_COMMANDS = ("node", "npm", "npx", "python", "git", "bash")
NAMES = [
    "runtime_status",
    "runtime_exec",
    "runtime_process_start",
    "runtime_process_status",
    "runtime_process_stop",
]
BRIDGE_PORT = 8799

_CACHE_LOCK = threading.RLock()
_CACHE_AT = 0.0
_CACHE_VALUES = {}
_CACHE_STATE = ""
_CACHE_TIMES = {}
_BOOTSTRAP_LOCK = threading.Lock()


class BridgeError(RuntimeError):
    pass


class BridgeUnavailable(BridgeError):
    pass


class BridgeVersionMismatch(BridgeError):
    pass


def _phone_available():
    try:
        from . import phone
        return bool(phone.available())
    except Exception:
        return False


def _phone_key():
    try:
        from . import phone
        _, key = phone.config()
        return str(key or "")
    except Exception:
        return ""


def _connectors():
    from . import connectors
    return connectors


def _termux_record():
    if not _phone_available():
        return {"status": "unavailable", "error": "Android phone bridge unavailable"}
    try:
        return _native('termux_status')
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


def _native(op, **kwargs):
    return _connectors().native(op, **kwargs)


def _validated_commands(commands):
    out = []
    for value in commands or ():
        name = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", name):
            raise ValueError("Invalid runtime command name")
        if name not in out:
            out.append(name)
    return out


def _clear_cache():
    global _CACHE_AT, _CACHE_VALUES, _CACHE_STATE, _CACHE_TIMES
    with _CACHE_LOCK:
        _CACHE_AT = 0.0
        _CACHE_VALUES = {}
        _CACHE_STATE = ""
        _CACHE_TIMES = {}


def _bridge_origin():
    raw = str(os.environ.get("MUSABAI_TERMUX_BRIDGE_URL") or
              ("http://127.0.0.1:%d" % BRIDGE_PORT)).strip()
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise BridgeError("Termux bridge must be an HTTP loopback origin")
    return raw.rstrip("/")


def _bridge_token():
    key = _phone_key()
    if not key:
        raise BridgeError("Android phone key is unavailable")
    return hashlib.sha256(b"musabai-termux-bridge-v1\0" + key.encode("utf-8")).hexdigest()


def _bridge_request(method, path, payload=None, timeout=3.0):
    url = _bridge_origin() + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-MusabAI-Token": _bridge_token(), "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read(65536) or b"{}")
            message = body.get("error") if isinstance(body, dict) else ""
        except ValueError:
            message = ""
        raise BridgeError(message or "Termux bridge returned HTTP %d" % exc.code) from exc
    except OSError as exc:
        cause = getattr(exc, 'reason', exc)
        error = BridgeUnavailable if isinstance(cause, ConnectionRefusedError) else BridgeError
        raise error("Termux localhost bridge is not answering") from exc
    if len(raw) > 1_000_000:
        raise BridgeError("Termux bridge response exceeded 1 MB")
    try:
        result = json.loads(raw or b"{}")
    except ValueError as exc:
        raise BridgeError("Termux bridge returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise BridgeError("Termux bridge returned invalid JSON")
    # ok describes the bridge operation; /exec reports shell outcomes separately
    # in status, exit_code and command_success (including nonzero exits/timeouts).
    if result.get("ok") is False:
        raise BridgeError(str(result.get("error") or "Termux bridge operation failed"))
    return result


def _bridge_health(timeout=0.6):
    data = _bridge_request("GET", "/health", timeout=timeout)
    if data.get("bridge") != "musabai-termux" or int(data.get("version") or 0) != termux_bridge_server.VERSION:
        raise BridgeVersionMismatch("Unexpected Termux bridge version")
    return data


def _bootstrap_bridge():
    state = _termux_record()
    if state.get("status") != "connected":
        raise BridgeError(state.get("error") or "Test Termux connection before starting the localhost bridge")
    source = Path(termux_bridge_server.__file__).read_bytes()
    if len(source) > 100_000:
        raise BridgeError("Termux bridge source is unexpectedly large")
    token = _bridge_token().encode("utf-8")
    source64 = base64.b64encode(source).decode("ascii")
    token64 = base64.b64encode(token).decode("ascii")
    py = (
        "import base64,sys,pathlib;"
        "p=pathlib.Path(sys.argv[1]);"
        "p.write_bytes(base64.b64decode(sys.argv[2]))"
    )
    command = "\n".join([
        "set -eu",
        'D="$HOME/.musabai/runtime-bridge"',
        'mkdir -p "$D/state" "$D/processes"',
        'chmod 700 "$HOME/.musabai" "$D" "$D/state" "$D/processes" 2>/dev/null || true',
        "python -c %s \"$D/server.py\" %s" % (shlex.quote(py), shlex.quote(source64)),
        "python -c %s \"$D/token\" %s" % (shlex.quote(py), shlex.quote(token64)),
        'chmod 600 "$D/server.py" "$D/token"',
        'if [ -s "$D/bridge.pid" ]; then OLD="$(cat "$D/bridge.pid" 2>/dev/null || true)"; '
        '[ -n "$OLD" ] && kill "$OLD" 2>/dev/null || true; fi',
        'nohup env MUSABAI_TERMUX_BRIDGE_TOKEN="$(cat "$D/token")" '
        'python "$D/server.py" --host 127.0.0.1 --port %d --root "$D/state" '
        '>>"$D/bridge.log" 2>&1 </dev/null &' % BRIDGE_PORT,
        'echo $! > "$D/bridge.pid"',
    ])
    try:
        result = _native("termux_exec", command=command)
        if result.get("status") == "running" and result.get("id"):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                time.sleep(0.1)
                result = _native("termux_result", id=result["id"])
                if result.get("status") != "running":
                    break
    except Exception as exc:
        raise BridgeError("Termux bridge bootstrap failed") from exc
    if result.get("status") != "completed" or result.get("exit_code") not in (0, None):
        raise BridgeError("Termux bridge bootstrap command failed")
    _clear_cache()


def _ensure_bridge():
    # A slow/auth-failing bridge is not a dead process. Restarting it would detach
    # live MCP stdio sessions, especially while dependency installs load the CPU.
    with _BOOTSTRAP_LOCK:
        try:
            return _bridge_health()
        except (BridgeUnavailable, BridgeVersionMismatch):
            _bootstrap_bridge()
        last = None
        for _ in range(50):
            try:
                return _bridge_health()
            except BridgeUnavailable as exc:
                last = exc
                time.sleep(0.1)
        raise BridgeError("Termux bridge did not become ready") from last


def _bridge_environment(commands, health=None):
    requested = _validated_commands(commands)
    health = health if health is not None else _ensure_bridge()
    query = urllib.parse.urlencode({"commands": ",".join(requested)})
    data = _bridge_request("GET", "/environment?" + query, timeout=3)
    return data, health


def termux_home():
    """Return the real Termux HOME reported by the authenticated localhost bridge."""
    try:
        env, _ = _bridge_environment([])
    except BridgeError as exc:
        raise tools.ToolError(str(exc)) from exc
    home = str(env.get("home") or "")
    if not home.startswith("/") or "\x00" in home:
        raise tools.ToolError("Termux bridge returned an invalid home directory")
    return home


def probe_termux(commands=None, max_age=5.0):
    """Verify command availability inside Termux through the localhost bridge."""
    global _CACHE_AT, _CACHE_VALUES, _CACHE_STATE, _CACHE_TIMES
    requested = _validated_commands(commands or TERMUX_BASE_COMMANDS)
    record = _termux_record()
    state = str(record.get("status") or "unavailable")
    if state != "connected":
        # A disconnect is an environment boundary. Drop the previous command
        # snapshot so reconnect always performs a fresh /environment probe.
        _clear_cache()
        return {
            "runtime": TERMUX,
            "status": state,
            "bridge": False,
            "commands": {name: None for name in requested},
            "reason": record.get("error") or "Connect and test Termux before probing commands.",
        }

    try:
        health = _ensure_bridge()
    except BridgeError as exc:
        return {
            "runtime": TERMUX,
            "status": "error",
            "bridge": False,
            "commands": {name: None for name in requested},
            "reason": str(exc),
        }

    cache_state = "connected:%s" % health.get("started_at", "")
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE_STATE == cache_state and all(name in _CACHE_VALUES and now - _CACHE_TIMES.get(name, _CACHE_AT) <= max_age for name in requested):
            return {
                "runtime": TERMUX,
                "status": "connected",
                "bridge": True,
                "commands": {name: _CACHE_VALUES[name] for name in requested},
                "reason": "Command availability was verified through the Termux localhost bridge.",
            }

    try:
        env, health = _bridge_environment(list(dict.fromkeys(list(TERMUX_BASE_COMMANDS) + requested)), health=health)
    except BridgeError as exc:
        return {
            "runtime": TERMUX,
            "status": "error",
            "bridge": False,
            "commands": {name: None for name in requested},
            "reason": str(exc),
        }
    values = {}
    for name, row in (env.get("commands") or {}).items():
        if name in TERMUX_BASE_COMMANDS or name in requested:
            values[name] = bool((row or {}).get("available"))
    for name in list(TERMUX_BASE_COMMANDS) + requested:
        values.setdefault(name, None)

    with _CACHE_LOCK:
        _CACHE_AT = time.monotonic()
        new_state = "connected:%s" % health.get("started_at", "")
        if _CACHE_STATE != new_state:
            _CACHE_VALUES, _CACHE_TIMES = {}, {}
        _CACHE_VALUES.update(values)
        _CACHE_TIMES.update({name: _CACHE_AT for name in values})
        _CACHE_STATE = new_state

    return {
        "runtime": TERMUX,
        "status": "connected",
        "bridge": True,
        "commands": {name: values.get(name) for name in requested},
        "reason": "Command availability was verified through the Termux localhost bridge.",
    }


def requirements(commands):
    """Resolve command requirements without confusing Android PATH with Termux."""
    requested = _validated_commands(commands)
    if _phone_available():
        probed = probe_termux(requested)
        values = probed["commands"]
        missing = [name for name in requested if values.get(name) is False]
        unknown = [name for name in requested if values.get(name) is None]
        return {
            "runtime": TERMUX,
            "missing": missing,
            "unknown": unknown,
            "reason": probed["reason"],
            "bridge": bool(probed.get("bridge")),
            "stdio": bool(probed.get("bridge")),
        }

    missing = [name for name in requested if shutil.which(name) is None]
    return {
        "runtime": "LOCAL_HOST",
        "missing": missing,
        "unknown": [],
        "reason": "Checked on the non-Android development host.",
        "bridge": False,
        "stdio": True,
    }


def command_status(command):
    state = requirements([command])
    name = _validated_commands([command])[0]
    if name in state["missing"]:
        available = False
    elif name in state["unknown"]:
        available = None
    else:
        available = True
    return {
        "command": name,
        "runtime": state["runtime"],
        "available": available,
        "reason": state["reason"],
        "bridge": state.get("bridge", False),
        "stdio": state["stdio"],
    }


def _remote_config():
    raw = str(os.environ.get("NEWAL_REMOTE_RUNTIME_URL") or "").strip()
    if not raw:
        return {"configured": False, "url": "", "reason": "No remote runtime configured."}
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.path not in ("", "/")
    ):
        return {"configured": False, "url": "", "reason": "Remote runtime must be an HTTPS origin."}
    return {"configured": True, "url": raw.rstrip("/"), "reason": "Remote HTTPS runtime configured."}


def snapshot(probe=False):
    native_ok = _phone_available()
    termux = _termux_record() if native_ok else {"status": "unavailable", "error": "Android phone bridge unavailable"}
    bridge = None
    if native_ok and termux.get("status") == "connected":
        try:
            bridge = _ensure_bridge()
        except BridgeError:
            bridge = None
    termux_row = {
        "runtime": TERMUX,
        "available": termux.get("status") == "connected" and bridge is not None,
        "status": termux.get("status") or "unavailable",
        "reason": termux.get("error") or "",
        "bridge": {
            "connected": bridge is not None,
            "url": _bridge_origin() if bridge is not None else "",
            "version": bridge.get("version") if bridge else None,
            "started_at": bridge.get("started_at") if bridge else None,
        },
        "commands": {},
    }
    if termux.get("status") == "connected" and bridge is None:
        termux_row["status"] = "bridge_error"
        termux_row["reason"] = "Termux is connected but its localhost runtime bridge is unavailable."
    if probe and termux.get("status") == "connected":
        checked = probe_termux(TERMUX_BASE_COMMANDS, max_age=0)
        termux_row["commands"] = checked["commands"]
        termux_row["reason"] = checked["reason"]
        termux_row["available"] = checked.get("bridge", False)
        termux_row["status"] = checked["status"]

    remote = _remote_config()
    return {
        "runtimes": [
            {
                "runtime": ANDROID_NATIVE,
                "available": native_ok,
                "requires_adb": False,
                "shell": False,
                "reason": "Local app/phone bridge; Android actions use localhost and never adb.",
            },
            termux_row,
            {
                "runtime": REMOTE,
                "available": remote["configured"],
                "configured": remote["configured"],
                "reason": remote["reason"],
            },
        ],
        "shell_commands": list(TERMUX_BASE_COMMANDS),
        "policy": "On Android, Termux shell discovery/execution uses the authenticated localhost bridge on 127.0.0.1.",
    }


def _remote_exec(command):
    cfg = _remote_config()
    if not cfg["configured"]:
        raise tools.ToolError(cfg["reason"])
    body = json.dumps({"command": command}).encode("utf-8")
    req = urllib.request.Request(
        cfg["url"] + "/exec",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    token = str(os.environ.get("NEWAL_REMOTE_RUNTIME_TOKEN") or "").strip()
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read(1_000_001)
    except Exception as exc:
        raise tools.ToolError("Remote runtime request failed") from exc
    if len(raw) > 1_000_000:
        raise tools.ToolError("Remote runtime response exceeded 1 MB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise tools.ToolError("Remote runtime returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise tools.ToolError("Remote runtime returned invalid JSON")
    return data


def execute(command, runtime=None):
    """Return the command outcome; nonzero exits and timeouts are not bridge errors."""
    if not isinstance(command, str) or not command.strip() or len(command) > 131072:
        raise tools.ToolError("Provide a non-empty command up to 131072 characters")
    requested = str(runtime or "").strip().upper()
    if requested and requested not in PRODUCT_RUNTIMES:
        raise tools.ToolError("runtime must be ANDROID_NATIVE, TERMUX or REMOTE")

    if requested:
        chosen = requested
    elif _phone_available():
        chosen = TERMUX
    elif _remote_config()["configured"]:
        chosen = REMOTE
    else:
        raise tools.ToolError("No supported runtime is available")

    if chosen == ANDROID_NATIVE:
        raise tools.ToolError(
            "ANDROID_NATIVE is the local phone/app bridge and does not run shell commands or adb. "
            "Use the phone tool for Android actions, or TERMUX for shell commands."
        )

    if chosen == TERMUX:
        state = _termux_record()
        if state.get("status") != "connected":
            raise tools.ToolError(state.get("error") or "Connect and test Termux before running commands")
        try:
            _ensure_bridge()
            result = _bridge_request("POST", "/exec", {"command": command, "timeout": 75}, timeout=80)
        except BridgeError as exc:
            raise tools.ToolError(str(exc)) from exc
        result["runtime"] = TERMUX
        return result

    data = _remote_exec(command)
    data.setdefault("runtime", REMOTE)
    return data


def process_start(command, stdio=False, env=None):
    if not isinstance(command, str) or not command.strip() or len(command) > 131072:
        raise tools.ToolError("Provide a non-empty command up to 131072 characters")
    payload = {"command": command, "stdio": bool(stdio)}
    if env is not None:
        if not isinstance(env, dict):
            raise tools.ToolError("Process environment must be an object")
        payload["env"] = {str(k): str(v) for k, v in env.items()}
    try:
        _ensure_bridge()
        data = _bridge_request("POST", "/process/start", payload, timeout=5)
    except BridgeError as exc:
        raise tools.ToolError(str(exc)) from exc
    data["runtime"] = TERMUX
    return data


def process_request(process_id, message, timeout=120):
    if not isinstance(process_id, str):
        raise tools.ToolError("Invalid process id")
    if not isinstance(message, dict):
        raise tools.ToolError("MCP stdio message must be an object")
    try:
        timeout = max(1, min(int(timeout), 300))
    except (TypeError, ValueError) as exc:
        raise tools.ToolError("Invalid process request timeout") from exc
    try:
        _ensure_bridge()
        data = _bridge_request(
            "POST",
            "/process/request",
            {"id": process_id, "message": message, "timeout": timeout},
            timeout=timeout + 5,
        )
    except BridgeError as exc:
        raise tools.ToolError(str(exc)) from exc
    data["runtime"] = TERMUX
    return data


def process_status(process_id):
    if not isinstance(process_id, str):
        raise tools.ToolError("Invalid process id")
    try:
        _ensure_bridge()
        data = _bridge_request(
            "GET", "/process/status?" + urllib.parse.urlencode({"id": process_id}), timeout=3
        )
    except BridgeError as exc:
        raise tools.ToolError(str(exc)) from exc
    data["runtime"] = TERMUX
    return data


def process_stop(process_id):
    if not isinstance(process_id, str):
        raise tools.ToolError("Invalid process id")
    try:
        _ensure_bridge()
        data = _bridge_request("POST", "/process/stop", {"id": process_id}, timeout=6)
    except BridgeError as exc:
        raise tools.ToolError(str(exc)) from exc
    data["runtime"] = TERMUX
    return data


def install():
    @tools.tool(
        "runtime_status",
        "Inspect MusabAI runtime state. On Android, verifies node/npm/npx/python/git/bash through the authenticated Termux localhost bridge.",
        {"probe": {"type": "boolean", "description": "when true, verify the standard shell commands inside Termux"}},
        [],
        "meta",
    )
    def runtime_status(ctx, probe=False):
        data = snapshot(bool(probe))
        return json.dumps(data, ensure_ascii=False), {"runtime_count": len(data["runtimes"])}

    @tools.tool(
        "runtime_exec",
        "Run a bounded shell command through the unified runtime manager. Android shell commands execute in Termux over 127.0.0.1; ANDROID_NATIVE never invokes adb.",
        {
            "command": {"type": "string", "description": "shell command"},
            "runtime": {
                "type": "string",
                "enum": [ANDROID_NATIVE, TERMUX, REMOTE],
                "description": "optional explicit runtime; auto prefers Termux on Android",
            },
        },
        ["command"],
        "exec",
    )
    def runtime_exec(ctx, command, runtime=None):
        data = execute(command, runtime)
        return json.dumps(data, ensure_ascii=False), {
            "runtime": data.get("runtime"),
            "exit": data.get("exit_code"),
            "pending": data.get("status") == "running",
        }

    @tools.tool(
        "runtime_process_start",
        "Start a durable long-running process inside Termux. Returns a process id; use runtime_process_status/stop instead of repeating the command.",
        {"command": {"type": "string", "description": "shell command"}},
        ["command"],
        "exec",
    )
    def runtime_process_start(ctx, command):
        data = process_start(command)
        return json.dumps(data, ensure_ascii=False), {
            "runtime": TERMUX, "process": data.get("id"), "pending": data.get("status") == "running"
        }

    @tools.tool(
        "runtime_process_status",
        "Read status and recent logs for a Termux bridge process without re-running it.",
        {"id": {"type": "string", "description": "process id from runtime_process_start"}},
        ["id"],
        "read",
    )
    def runtime_process_status(ctx, id):
        data = process_status(id)
        return json.dumps(data, ensure_ascii=False), {"runtime": TERMUX, "process": id}

    @tools.tool(
        "runtime_process_stop",
        "Stop the exact Termux process group previously started by runtime_process_start. PID identity is verified before signaling.",
        {"id": {"type": "string", "description": "process id from runtime_process_start"}},
        ["id"],
        "exec",
    )
    def runtime_process_stop(ctx, id):
        data = process_stop(id)
        return json.dumps(data, ensure_ascii=False), {"runtime": TERMUX, "process": id}
