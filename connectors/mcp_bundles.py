"""Vetted MCP bundle catalog shipped with MusabAI.

Bundles are real server definitions, not fake connection cards. A bundle is
reported available only when its runtime (and required credential) exists.
Enabling performs an actual initialize + tools/list handshake before persisting
the server into the project's private MCP configuration. Phase 8 keeps verified
installation separate from the live start/stop/reconnect process state.
"""
import hashlib
from contextlib import contextmanager
import json
import os
import re
import shlex
import shutil
import tempfile
import threading
import time
import weakref

from . import mcp_config, runtime_manager, tools

PATHS = {
    "/api/mcp-bundles",
    "/api/mcp-bundles/enable",
    "/api/mcp-bundles/test",
    "/api/mcp-bundles/disable",
    "/api/mcp-bundles/start",
    "/api/mcp-bundles/stop",
    "/api/mcp-bundles/reconnect",
}

BUNDLES = [
    {
        "id": "playwright",
        "name": "Microsoft Playwright MCP",
        "repository": "https://github.com/microsoft/playwright-mcp",
        "runtimes": ["node", "npm"],
        "command": "npx",
        "args": [
            "-y", "@playwright/mcp@0.0.83",
            "--isolated", "--headless", "--no-sandbox",
            "--executable-path", "/data/data/com.termux/files/usr/bin/chromium-browser",
        ],
        "termux_npm_package": "@playwright/mcp@0.0.83",
        "termux_npm_entry": "node_modules/@playwright/mcp/cli.js",
        "termux_args": [
            "--isolated", "--headless", "--no-sandbox",
            "--executable-path", "/data/data/com.termux/files/usr/bin/chromium-browser",
        ],
        "env": {
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
            "PLAYWRIGHT_BROWSERS_PATH": "0",
        },
        "termux_setup": "chromium",
        "install_method": "on_demand",
        "description": "Structured browser automation using Playwright accessibility snapshots. On Android, MusabAI uses Termux Chromium instead of Playwright's unsupported downloaded browser binaries.",
        "browser_role": "structured",
    },
    {
        "id": "browser-use",
        "name": "Browser Use MCP",
        "repository": "https://github.com/browser-use/browser-use",
        "runtimes": ["uvx"],
        "command": "uvx",
        "args": ["browser-use==0.13.5", "--cli-mcp"],
        "description": "Official Browser Use MCP for browser tasks. Android uses its stdio server with headless Linux Chromium in a managed Debian environment.",
        "browser_role": "complex",
        "manual_setup": "Android installs a dedicated Debian/Python 3.11 environment and Chromium automatically before the real MCP handshake. Initial setup downloads Linux packages.",
    },
    {
        "id": "open-browser-use",
        "name": "open-browser-use",
        "repository": "https://github.com/open-browser-use/open-browser-use",
        "runtimes": ["obu"],
        "command": "obu",
        "args": ["mcp", "stdio"],
        "description": "Controls an existing real Chromium session through the open-browser-use MCP server; useful when the task requires the user's logged-in session.",
        "browser_role": "existing_session",
        "manual_setup": "Requires its Chrome extension and native host on a supported desktop Chromium browser. Android Chrome does not load this extension. Use the working Playwright browser route on this device, or connect a supported desktop browser.",
        "native_fallback": "Use Microsoft Playwright MCP for browser tasks on this device; open-browser-use is a separate existing-session capability.",
    },
    {
        "id": "docling",
        "name": "Docling MCP",
        "repository": "https://github.com/docling-project/docling-mcp",
        "runtimes": ["uvx"],
        "command": "uvx",
        "args": ["--from", "docling-mcp[local]==3.3.0", "docling-mcp-server", "--transport", "stdio"],
        "env": {
            "DOCLING_MCP_CONVERSION_MODE": "local",
            "DOCLING_MCP_KEEP_IMAGES": "false",
        },
        "description": "Docling document understanding for PDF, Office, OCR, tables and structured conversion. The reviewed bundle pins Docling MCP 3.3.0 and starts its official stdio server in local mode.",
        "document_role": "structured",
        "manual_setup": "Android installs Docling in dedicated Debian/Python 3.11 so Linux PyTorch wheels can load. Initial setup requires substantial storage and network access; installation is verified by MCP tools/list.",
        "native_fallback": "MusabAI's built-in document tools remain available for Markdown, text, HTML, ordinary PDF text and ZIP files.",
    },
    {
        "id": "github",
        "name": "GitHub MCP Server",
        "repository": "https://github.com/github/github-mcp-server",
        "runtimes": ["github-mcp-server"],
        "command": "github-mcp-server",
        "args": ["stdio"],
        "credential_env_any": ["GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"],
        "env_alias": "GITHUB_PERSONAL_ACCESS_TOKEN",
        "description": "GitHub's official MCP server. Requires its binary and a GitHub token.",
        "native_fallback": "MusabAI's Android GitHub connector remains available when the MCP binary is absent.",
    },
    {
        "id": "filesystem",
        "name": "MCP Reference Filesystem",
        "repository": "https://github.com/modelcontextprotocol/servers",
        "runtimes": ["node", "npm"],
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem@0.6.2", "{root}"],
        "termux_npm_package": "@modelcontextprotocol/server-filesystem@0.6.2",
        "termux_npm_entry": "node_modules/@modelcontextprotocol/server-filesystem/dist/index.js",
        "termux_args": ["{root}"],
        "description": "Sandboxed file tools restricted to the current project root.",
        "native_fallback": "Use the built-in file tools for app-private projects. Termux cannot cross the Android application sandbox.",
    },
    {
        "id": "android",
        "name": "Android MCP (external ADB, optional)",
        "repository": "https://github.com/us-all/android-mcp-server",
        "runtimes": ["node", "npm", "adb"],
        "command": "npx",
        "args": ["-y", "@us-all/android-mcp@1.14.4"],
        "termux_npm_package": "@us-all/android-mcp@1.14.4",
        "termux_npm_entry": "node_modules/@us-all/android-mcp/dist/index.js",
        "termux_args": [],
        "env": {"ANDROID_MCP_ALLOW_WRITE": "true"},
        "description": "Optional third-party ADB MCP for external-device diagnostics. This is not MusabAI's native on-phone bridge and uses a managed Linux Node/sharp environment plus adb on Android. An external ADB device must still be paired.",
        "native_fallback": "MusabAI Android Native Bridge is separate and works locally without adb, Wireless ADB, Wi-Fi pairing or USB ADB.",
    },
    {
        "id": "memory",
        "name": "MCP Reference Memory",
        "repository": "https://github.com/modelcontextprotocol/servers",
        "runtimes": ["node", "npm"],
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory@0.6.2"],
        "termux_npm_package": "@modelcontextprotocol/server-memory@0.6.2",
        "termux_npm_entry": "node_modules/@modelcontextprotocol/server-memory/dist/index.js",
        "termux_args": [],
        "env": {"MEMORY_FILE_PATH": "{memory_file}"},
        "description": "Persistent project-scoped knowledge-graph memory.",
    },
]


def _item(bundle_id):
    for item in BUNDLES:
        if item["id"] == bundle_id:
            return item
    raise ValueError("Unknown MCP bundle")


def _credential(item):
    for name in item.get("credential_env_any") or []:
        value = os.environ.get(name)
        if value:
            return name, value
    return "", ""


def _runtime_requirements(item):
    from . import managed_linux
    if item['id'] in managed_linux.RECIPES and runtime_manager._phone_available():
        return runtime_manager.requirements(['proot-distro'])
    return runtime_manager.requirements(item.get("runtimes") or [])


def _termux_npm_root(item):
    package = str(item.get("termux_npm_package") or "")
    if not package:
        return ""
    digest = hashlib.sha256(package.encode("utf-8")).hexdigest()[:12]
    return os.path.join(runtime_manager.termux_home(), ".musabai", "mcp", item["id"], digest)


def _termux_npm_entry(item):
    root = _termux_npm_root(item)
    entry = str(item.get("termux_npm_entry") or "")
    if not root or not entry or entry.startswith("/") or ".." in entry.split("/"):
        raise RuntimeError("Invalid managed Termux MCP entry")
    return os.path.join(root, entry)


def _missing(item):
    state = _runtime_requirements(item)
    missing = list(state["missing"])
    if item.get("credential_env_any") and not _credential(item)[1]:
        missing.append("credential")
    return missing


def _spec(item, root):
    root = os.path.realpath(root)
    runtime = _runtime_requirements(item)
    from . import managed_linux
    if runtime['runtime'] == runtime_manager.TERMUX and item['id'] in managed_linux.RECIPES:
        return managed_linux.spec(item)
    memory_dir = os.path.join(root, ".newal")
    if runtime["runtime"] == runtime_manager.TERMUX and item.get("id") == "memory":
        project_key = hashlib.sha256(root.encode("utf-8")).hexdigest()[:20]
        memory_file = os.path.join(
            runtime_manager.termux_home(),
            ".musabai-mcp-memory-" + project_key + ".jsonl",
        )
    else:
        os.makedirs(memory_dir, exist_ok=True)
        memory_file = os.path.join(memory_dir, "mcp-memory.jsonl")
    replacements = {
        "{root}": root,
        "{memory_file}": memory_file,
    }

    def expand(value):
        text = str(value)
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    env = {str(k): expand(v) for k, v in (item.get("env") or {}).items()}
    env_name, env_value = _credential(item)
    if env_value:
        env[item.get("env_alias") or env_name] = env_value

    command = item["command"]
    args = [expand(x) for x in item.get("args") or []]
    spec = {"command": command, "args": args, "env": env}
    if runtime["runtime"] == runtime_manager.TERMUX and item.get("termux_npm_package"):
        root_dir = _termux_npm_root(item)
        entry = os.path.join(root_dir, expand(item["termux_npm_entry"]))
        spec = {
            "command": "node",
            "args": [entry] + [expand(x) for x in item.get("termux_args") or []],
            "env": env,
            "termux_cwd": root_dir,
        }
    return spec


_RUNNING_LOCK = threading.RLock()
_OPERATION_LOCKS = weakref.WeakValueDictionary()
_RUNNING = {}
_LAST_ERRORS = {}


def _runtime_state_path(root):
    path = mcp_config.path_for(root)
    return path.with_name(path.stem + ".runtime.json")


def _read_runtime_state(root):
    try:
        data = json.loads(_runtime_state_path(root).read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_runtime_state(root, data):
    path = _runtime_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mcp-runtime-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _key(root, bundle_id):
    return (os.path.realpath(root), bundle_id)


def _remember_process(root, item, server):
    process_id = str(getattr(server, "_termux_process_id", "") or "")
    if not process_id:
        return
    state = _read_runtime_state(root)
    state[item["id"]] = {
        "process_id": process_id,
        "started_at": int(time.time()),
    }
    _save_runtime_state(root, state)


def _forget_process(root, item):
    state = _read_runtime_state(root)
    if item["id"] in state:
        del state[item["id"]]
        _save_runtime_state(root, state)


def _attached_server(item, root, process_id):
    server = mcp_config.StdioServer(item["id"], _spec(item, root), root)
    server._termux = True
    server._termux_process_id = process_id
    return server


def _running_server(item, root):
    if not root:
        return None
    key = _key(root, item["id"])
    with _RUNNING_LOCK:
        server = _RUNNING.get(key)
        if server is not None:
            if server.alive():
                return server
            try:
                server.stop()
            except Exception:
                pass
            _RUNNING.pop(key, None)
            _forget_process(root, item)

        saved = _read_runtime_state(root).get(item["id"]) or {}
        process_id = str(saved.get("process_id") or "")
        if not process_id:
            return None
        server = _attached_server(item, root, process_id)
        if server.alive():
            _RUNNING[key] = server
            return server
        _forget_process(root, item)
        return None


def _installed_record(item, root):
    if not root:
        return {}
    try:
        record = mcp_config.read(root).get(item["id"]) or {}
    except OSError:
        return {}
    if record.get("bundle") != item["id"]:
        return {}
    if int(record.get("tested_at") or 0) <= 0 or int(record.get("tools") or 0) <= 0:
        return {}
    return record


def _lifecycle_status(item, runtime, installed, running, last_error):
    credential_missing = bool(item.get("credential_env_any") and not _credential(item)[1])
    if running:
        return "server_running"
    if credential_missing:
        return "permission_required"
    if runtime["unknown"]:
        return "termux_disconnected" if runtime["runtime"] == runtime_manager.TERMUX else "runtime_unavailable"
    if runtime["missing"]:
        return "tool_missing"
    if runtime["runtime"] == runtime_manager.TERMUX and not runtime["stdio"]:
        return "termux_disconnected"
    if runtime["runtime"] == runtime_manager.TERMUX and item["id"] == "filesystem":
        return "needs_setup"
    if last_error:
        return "health_failed"
    if installed:
        return "server_stopped"
    return "ready"


def catalog(root=None):
    from . import managed_linux
    android = runtime_manager._phone_available()
    needed = {item['id']: (['proot-distro'] if android and item['id'] in managed_linux.RECIPES
                           else list(item.get('runtimes') or [])) for item in BUNDLES}
    combined = runtime_manager.requirements(list(dict.fromkeys(command for names in needed.values() for command in names)))
    result = []
    for item in BUNDLES:
        names = needed[item['id']]
        runtime = dict(combined, missing=[name for name in combined['missing'] if name in names],
                       unknown=[name for name in combined['unknown'] if name in names])
        record = _installed_record(item, root)
        installed = bool(record)
        server = _running_server(item, root) if installed else None
        running = bool(server)
        key = _key(root, item["id"]) if root else None
        last_error = _LAST_ERRORS.get(key, "") if key else ""
        status = _lifecycle_status(item, runtime, installed, running, last_error)
        public = {
            k: v for k, v in item.items()
            if k not in {"command", "args", "env", "credential_env_any", "env_alias", "termux_npm_package", "termux_npm_entry", "termux_args"}
        }
        process_id = str(getattr(server, "_termux_process_id", "") or "") if server else ""
        public.update({
            "available": status == "ready" or (
                runtime['runtime'] == runtime_manager.TERMUX and runtime['stdio'] and not runtime['unknown']
                and status in {'tool_missing', 'health_failed'}
                and (item.get('termux_npm_package') or item['id'] in {'browser-use', 'docling'})),
            "status": status,
            "missing": list(runtime["missing"]),
            "runtime": runtime["runtime"],
            "runtime_reason": runtime["reason"],
            "dependencies": list(item.get("runtimes") or []),
            "install_method": "managed_linux" if runtime["runtime"] == runtime_manager.TERMUX and item["id"] in {"browser-use", "docling", "android"} else "managed_npm" if item.get("termux_npm_package") else ("on_demand" if item.get("command") in {"npx", "uvx"} else "manual_or_preinstalled"),
            "start_method": "runtime_process_start(stdio=true)",
            "stop_method": "runtime_process_stop",
            "health_check": "live process status + MCP tools/list",
            "verified": installed,
            "installed": installed,
            "connected": running,
            "running": running,
            "can_start": installed and status in {"server_stopped", "health_failed"},
            "enabled": installed,
            "tools": int(record.get("tools") or 0) if installed else 0,
            "tested_at": int(record.get("tested_at") or 0) if installed else 0,
            "process_id": process_id,
            "health": "healthy" if running else ("failed" if status == "health_failed" else "stopped" if installed else "unverified"),
            "last_error": last_error,
        })
        result.append(public)
    return result


def _session_root(handler, data):
    sid = data.get("session", "")
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
        raise ValueError("Open a project conversation first")
    return handler.service.get(sid).root


_CANCEL = threading.local()


@contextmanager
def cancel_scope(cancel):
    previous = getattr(_CANCEL, "token", None)
    _CANCEL.token = cancel
    try:
        yield
    finally:
        _CANCEL.token = previous


def _cancel_setup(process_id=None):
    token = getattr(_CANCEL, "token", None)
    if token is not None and token.is_set():
        if process_id:
            runtime_manager.process_stop(process_id)
        from . import providers
        raise providers.Cancelled()


def _wait_termux_process(process_id, timeout=420):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        _cancel_setup(process_id)
        last = runtime_manager.process_status(process_id)
        if last.get("status") == "completed":
            if last.get("exit_code") != 0:
                detail = str(last.get("logs") or "").strip()
                raise RuntimeError("Termux setup failed (exit_code=%s)" % last.get("exit_code")
                                   + (": " + detail[-6000:] if detail else ""))
            return last
        if last.get("status") not in {"running", "unknown"}:
            raise RuntimeError("Termux setup stopped unexpectedly")
        time.sleep(1.0)
    try:
        runtime_manager.process_stop(process_id)
    except Exception:
        pass
    raise RuntimeError("Termux setup timed out")


def _ensure_termux_npm(item):
    package = str(item.get("termux_npm_package") or "")
    if not package:
        return
    root = _termux_npm_root(item)
    entry = _termux_npm_entry(item)
    probe = runtime_manager.execute("test -f %s" % shlex.quote(entry), runtime_manager.TERMUX)
    if probe.get("status") != "completed" or probe.get("exit_code") not in (0, 1):
        raise RuntimeError("Managed MCP entry probe failed: status=%s exit_code=%s stderr=%s" % (
            probe.get("status"), probe.get("exit_code"), str(probe.get("stderr") or "")[-1200:]))
    if probe["exit_code"] == 0:
        return
    command = "mkdir -p {root} && npm install --prefix {root} --no-audit --no-fund --omit=dev {package}".format(
        root=shlex.quote(root),
        package=shlex.quote(package),
    )
    env = {
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
        "PLAYWRIGHT_BROWSERS_PATH": "0",
        "npm_config_update_notifier": "false",
    }
    started = runtime_manager.process_start(command, stdio=False, env=env)
    _wait_termux_process(started["id"], timeout=600)
    runtime_manager._clear_cache()
    verify = runtime_manager.execute("test -f %s" % shlex.quote(entry), runtime_manager.TERMUX)
    if verify.get("status") != "completed" or verify.get("exit_code") != 0:
        raise RuntimeError("Managed MCP entry verification failed: status=%s exit_code=%s stderr=%s" % (
            verify.get("status"), verify.get("exit_code"), str(verify.get("stderr") or "")[-1200:]))


def _ensure_termux_setup(item):
    _cancel_setup()
    runtime = _runtime_requirements(item)
    if runtime.get("runtime") != runtime_manager.TERMUX:
        return
    from . import managed_linux
    if item['id'] in managed_linux.RECIPES:
        managed_linux.ensure(item, getattr(_CANCEL, 'token', None))
        return
    if item.get('command') == 'uvx':
        from .automation import ensure_dependency
        ensure_dependency('uv', getattr(_CANCEL, 'token', None))
    _ensure_termux_npm(item)
    if item.get("termux_setup") != "chromium":
        return
    browser = runtime_manager.requirements(["chromium-browser"])
    if not browser.get("missing"):
        return
    first = runtime_manager.process_start("pkg install -y x11-repo", stdio=False)
    _wait_termux_process(first["id"])
    second = runtime_manager.process_start("pkg install -y chromium", stdio=False)
    _wait_termux_process(second["id"])
    runtime_manager._clear_cache()
    browser = runtime_manager.requirements(["chromium-browser"])
    if browser.get("missing") or browser.get("unknown"):
        raise RuntimeError("Chromium installation finished, but chromium-browser is still unavailable in Termux")


def _test(item, root):
    _ensure_termux_setup(item)
    runtime = _runtime_requirements(item)
    if runtime["unknown"]:
        raise RuntimeError(runtime["reason"])
    if runtime["missing"]:
        raise RuntimeError("Missing requirements: " + ", ".join(runtime["missing"]))
    if runtime["runtime"] == runtime_manager.TERMUX and item["id"] == "filesystem":
        raise RuntimeError(
            "Filesystem MCP cannot access MusabAI app-private project files from Termux. "
            "Use MusabAI built-in file tools until a shared project path is configured."
        )
    if runtime["runtime"] == runtime_manager.TERMUX and not runtime["stdio"]:
        raise RuntimeError(
            "Termux runtime is verified, but persistent MCP stdio transport is not active yet; "
            "use the localhost bridge before enabling this bundle."
        )
    server = mcp_config.StdioServer(item["id"], _spec(item, root), root)
    try:
        # First-run npx bundles on Termux may need to resolve/download packages
        # before the MCP process can answer initialize. CI/local hosts stay fast,
        # while Android gets a bounded longer handshake window.
        timeout = 240 if runtime["runtime"] == runtime_manager.TERMUX else 45
        server.start(timeout=timeout)
        return len(server.tools)
    finally:
        server.stop()


def _save_enabled(item, root, tools):
    with mcp_config.LOCK:
        servers = mcp_config.read(root)
        existing = servers.get(item["id"])
        if existing and existing.get("bundle") not in (None, item["id"]):
            raise ValueError("An MCP server already uses this name")
        if existing and not existing.get("bundle"):
            raise ValueError("Remove the custom MCP server with this name first")
        servers[item["id"]] = {
            "spec": _spec(item, root),
            "tools": int(tools),
            "tested_at": int(time.time()),
            "bundle": item["id"],
        }
        mcp_config.save(root, servers)


def _start(item, root):
    if not _installed_record(item, root):
        raise ValueError("Install and verify this MCP bundle first")
    current = _running_server(item, root)
    if current is not None:
        return int(_installed_record(item, root).get("tools") or len(current.tools) or 0)

    runtime = _runtime_requirements(item)
    if runtime["unknown"]:
        raise RuntimeError(runtime["reason"])
    if runtime["missing"]:
        raise RuntimeError("Missing requirements: " + ", ".join(runtime["missing"]))
    if runtime["runtime"] == runtime_manager.TERMUX and item["id"] == "filesystem":
        raise RuntimeError("Filesystem MCP needs a shared project path before it can start")
    if runtime["runtime"] == runtime_manager.TERMUX and not runtime["stdio"]:
        raise RuntimeError("Termux localhost bridge is not available")

    server = mcp_config.StdioServer(item["id"], _spec(item, root), root)
    server.start(timeout=45)
    with _RUNNING_LOCK:
        _RUNNING[_key(root, item["id"])] = server
        _remember_process(root, item, server)
    return len(server.tools)


def _stop(item, root, require_running=False):
    key = _key(root, item["id"])
    with _RUNNING_LOCK:
        server = _RUNNING.get(key)
        saved = _read_runtime_state(root).get(item["id"]) or {}
        if server is None and saved.get("process_id"):
            server = _attached_server(item, root, str(saved["process_id"]))
        if server is None:
            if require_running:
                raise ValueError("MCP server is not running")
            _forget_process(root, item)
            return
        try:
            if getattr(server, "_termux", False) and getattr(server, "_termux_process_id", ""):
                runtime_manager.process_stop(server._termux_process_id)
                server._termux_process_id = ""
            else:
                server.stop()
        except Exception as exc:
            raise RuntimeError("MCP process could not be stopped") from exc
        finally:
            _RUNNING.pop(key, None)
        _forget_process(root, item)


def _health(item, root):
    server = _running_server(item, root)
    if server is None:
        return _test(item, root)
    tools = server._list_tools()
    if not isinstance(tools, list) or not tools:
        raise RuntimeError("MCP health check returned no tools")
    return len(tools)


def operations_busy():
    with _RUNNING_LOCK:
        return any(lock.locked() for lock in list(_OPERATION_LOCKS.values()))


def perform(root, bundle_id, action):
    # Serialize lifecycle changes per project/bundle, not unrelated MCP servers.
    # A strong local reference keeps this lock alive while another caller waits.
    key = _key(root, _item(bundle_id)["id"])
    with _RUNNING_LOCK:
        lock = _OPERATION_LOCKS.setdefault(key, threading.Lock())
    while not lock.acquire(timeout=0.1):
        _cancel_setup()
    try:
        _cancel_setup()
        return _perform(root, bundle_id, action)
    finally:
        lock.release()


def _perform(root, bundle_id, action):
    item = _item(bundle_id)
    key = _key(root, item["id"])
    try:
        if action == "disable":
            _stop(item, root, require_running=False)
            with mcp_config.LOCK:
                servers = mcp_config.read(root)
                record = servers.get(item["id"])
                if not record or record.get("bundle") != item["id"]:
                    raise ValueError("Bundle is not enabled")
                del servers[item["id"]]
                mcp_config.save(root, servers)
            _LAST_ERRORS.pop(key, None)
            return {"ok": True, "enabled": False, "running": False}

        if action == "enable":
            count = _test(item, root)
            _save_enabled(item, root, count)
            _LAST_ERRORS.pop(key, None)
            return {"ok": True, "tools": count, "enabled": True, "running": False}

        if action == "test":
            count = _health(item, root)
            _LAST_ERRORS.pop(key, None)
            return {"ok": True, "tools": count, "running": _running_server(item, root) is not None}

        if action == "start":
            count = _start(item, root)
            _LAST_ERRORS.pop(key, None)
            return {"ok": True, "tools": count, "enabled": True, "running": True}

        if action == "stop":
            _stop(item, root, require_running=True)
            _LAST_ERRORS.pop(key, None)
            return {"ok": True, "enabled": True, "running": False}

        if action == "reconnect":
            if not _installed_record(item, root):
                raise ValueError("Install and verify this MCP bundle first")
            _stop(item, root, require_running=False)
            count = _start(item, root)
            _LAST_ERRORS.pop(key, None)
            return {"ok": True, "tools": count, "enabled": True, "running": True}

        raise ValueError("Unknown MCP lifecycle action")
    except (ValueError, KeyError, OSError, RuntimeError, TypeError, tools.ToolError) as exc:
        _LAST_ERRORS[key] = str(exc)[-6000:]
        raise


def route(handler, method, path, body=None):
    if path not in PATHS:
        return False
    try:
        data = handler._query() if method == "GET" else (body or {})
        root = None
        sid = data.get("session", "")
        if sid:
            root = _session_root(handler, data)

        if method == "GET" and path == "/api/mcp-bundles":
            handler._json({
                "bundles": catalog(root),
                "automatic": True,
                "note": "Installed, process-running and health are separate live states. Running is claimed only while the managed process is alive.",
            })
            return True

        if method != "POST" or path == "/api/mcp-bundles":
            handler._json({"error": "Method not allowed"}, 405)
            return True

        root = _session_root(handler, data)
        action = path.rsplit("/", 1)[-1]
        handler._json(perform(root, data.get("id"), action))
    except (ValueError, KeyError, OSError, RuntimeError, TypeError, tools.ToolError) as error:
        message = str(error)
        if len(message) > 6000:
            message = message[-6000:]
        handler._json({"error": "MCP lifecycle operation failed: " + message}, 400)
    return True
