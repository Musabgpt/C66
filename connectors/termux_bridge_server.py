"""Authenticated localhost runtime bridge for MusabAI inside Termux.

This file is intentionally standalone (stdlib only). It is copied into Termux
by runtime_manager and listens only on 127.0.0.1. Every request requires the
per-install bridge token. Long-running child processes keep durable metadata and
logs so status/stop can reconnect after the bridge itself restarts.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import platform
import select
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

# v3 separates bridge operation success from shell command success.
VERSION = 3
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8799
BASE_COMMANDS = ("node", "npm", "npx", "python", "git", "bash")
MAX_BODY = 1_000_000
MAX_COMMAND = 131_072
MAX_OUTPUT = 65_536


def _clip(value, limit=MAX_OUTPUT):
    text = value if isinstance(value, str) else str(value or "")
    return text if len(text) <= limit else text[-limit:]


def _proc_start(pid):
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        rest = raw[raw.rfind(")") + 2:].split()
        return rest[19] if len(rest) > 19 else ""
    except (OSError, ValueError):
        return ""


def _marker_matches(pid, process_id):
    try:
        data = Path(f"/proc/{int(pid)}/environ").read_bytes()
    except OSError:
        return False
    needle = ("MUSABAI_PROCESS_ID=" + process_id).encode() + b"\0"
    return needle in data


class BridgeState:
    def __init__(self, root=None):
        self.root = Path(root or os.path.expanduser("~/.musabai/runtime-bridge")).resolve()
        self.process_dir = self.root / "processes"
        self.process_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
            os.chmod(self.process_dir, 0o700)
        except OSError:
            pass
        self.started_at = int(time.time() * 1000)
        self._lock = threading.RLock()
        self._procs = {}
        self._stdio_locks = {}

    def _paths(self, process_id):
        if not isinstance(process_id, str) or len(process_id) != 36:
            raise ValueError("Invalid process id")
        try:
            uuid.UUID(process_id)
        except ValueError as exc:
            raise ValueError("Invalid process id") from exc
        base = self.process_dir / process_id
        return {
            "meta": base.with_suffix(".json"),
            "log": base.with_suffix(".log"),
            "exit": base.with_suffix(".exit"),
            "stop": base.with_suffix(".stopped"),
        }

    def _read_meta(self, process_id):
        paths = self._paths(process_id)
        try:
            data = json.loads(paths["meta"].read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Unknown process id") from exc
        if data.get("id") != process_id:
            raise ValueError("Invalid process record")
        return data, paths

    def health(self):
        return {
            "ok": True,
            "bridge": "musabai-termux",
            "version": VERSION,
            "pid": os.getpid(),
            "started_at": self.started_at,
        }

    def environment(self, commands=None):
        names = []
        for raw in commands or BASE_COMMANDS:
            name = str(raw or "").strip()
            if not name or len(name) > 64 or not all(ch.isalnum() or ch in "._+-" for ch in name):
                raise ValueError("Invalid command name")
            if name not in names:
                names.append(name)
        found = {}
        for name in names:
            path = shutil.which(name)
            found[name] = {"available": bool(path), "path": path or None}
        return {
            "ok": True,
            "home": os.path.expanduser("~"),
            "cwd": os.getcwd(),
            "python": os.path.realpath(os.sys.executable),
            "platform": platform.platform(),
            "commands": found,
            "bridge_started_at": self.started_at,
        }

    def exec(self, command, timeout=75):
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND:
            raise ValueError("Provide a non-empty command up to 131072 characters")
        try:
            timeout = max(1, min(int(timeout), 120))
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout must be an integer") from exc
        started = time.time()
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=os.path.expanduser("~"),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
            return {
                # A completed command is a successful bridge operation, even on exit 1.
                "ok": True,
                "status": "completed",
                "exit_code": proc.returncode,
                "stdout": _clip(proc.stdout),
                "stderr": _clip(proc.stderr),
                "command_success": proc.returncode == 0,
                "duration_ms": int((time.time() - started) * 1000),
            }
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return {
                "ok": True,
                "status": "timeout",
                "exit_code": None,
                "stdout": _clip(stdout),
                "stderr": _clip(stderr),
                "command_success": False,
                "duration_ms": int((time.time() - started) * 1000),
            }

    def start(self, command, stdio=False, extra_env=None):
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND:
            raise ValueError("Provide a non-empty command up to 131072 characters")
        stdio = bool(stdio)
        if extra_env is None:
            extra_env = {}
        if not isinstance(extra_env, dict) or len(extra_env) > 64:
            raise ValueError("Process environment must be an object with at most 64 entries")
        clean_env = {}
        total_env = 0
        for key, value in extra_env.items():
            if not isinstance(key, str) or not key or len(key) > 128 or not (
                key[0].isalpha() or key[0] == "_"
            ) or not all(ch.isalnum() or ch == "_" for ch in key):
                raise ValueError("Invalid process environment variable name")
            value = str(value)
            if "\x00" in value or len(value) > 16384:
                raise ValueError("Invalid process environment variable value")
            total_env += len(key) + len(value)
            if total_env > 65536:
                raise ValueError("Process environment is too large")
            clean_env[key] = value
        process_id = str(uuid.uuid4())
        paths = self._paths(process_id)
        for path in (paths["exit"], paths["stop"]):
            try:
                path.unlink()
            except OSError:
                pass
        exit_path = shlex.quote(str(paths["exit"]))
        wrapper = command + "\nrc=$?\nprintf '%s\\n' \"$rc\" > " + exit_path + "\nexit \"$rc\"\n"
        env = os.environ.copy()
        env.update(clean_env)
        env["MUSABAI_PROCESS_ID"] = process_id
        log = open(paths["log"], "ab", buffering=0)
        try:
            if stdio:
                proc = subprocess.Popen(
                    ["bash", "-lc", wrapper],
                    cwd=os.path.expanduser("~"),
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    start_new_session=True,
                    bufsize=0,
                )
            else:
                proc = subprocess.Popen(
                    ["bash", "-lc", wrapper],
                    cwd=os.path.expanduser("~"),
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        finally:
            log.close()
        meta = {
            "id": process_id,
            "pid": proc.pid,
            "proc_start": _proc_start(proc.pid),
            "started_at": int(time.time() * 1000),
            "mode": "stdio" if stdio else "process",
        }
        tmp = paths["meta"].with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, paths["meta"])
        try:
            os.chmod(paths["meta"], 0o600)
            os.chmod(paths["log"], 0o600)
        except OSError:
            pass
        with self._lock:
            self._procs[process_id] = proc
            if stdio:
                self._stdio_locks[process_id] = threading.Lock()
        return self.status(process_id)

    def request(self, process_id, message, timeout=120):
        meta, _ = self._read_meta(process_id)
        if meta.get("mode") != "stdio":
            raise ValueError("Process was not started in stdio mode")
        if not isinstance(message, dict):
            raise ValueError("message must be a JSON object")
        try:
            timeout = max(1, min(int(timeout), 300))
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout must be an integer") from exc
        raw = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > MAX_BODY:
            raise ValueError("stdio message is too large")
        with self._lock:
            proc = self._procs.get(process_id)
            request_lock = self._stdio_locks.get(process_id)
        if proc is None or request_lock is None or proc.stdin is None or proc.stdout is None:
            if self._identity_alive(meta):
                raise ValueError("stdio process survived but cannot be reattached after bridge restart; restart the MCP server")
            raise ValueError("stdio process is not running")
        with request_lock:
            if proc.poll() is not None:
                raise ValueError("stdio process is not running")
            try:
                proc.stdin.write(raw)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ValueError("stdio process closed its input") from exc
            request_id = message.get("id")
            if request_id is None:
                return {"ok": True, "id": process_id, "notification": True}
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise ValueError("stdio process stopped before replying")
                remaining = max(0.0, deadline - time.monotonic())
                ready, _, _ = select.select([proc.stdout], [], [], min(0.25, remaining))
                if not ready:
                    continue
                line = proc.stdout.readline(MAX_BODY + 1)
                if len(line) > MAX_BODY:
                    raise ValueError("stdio response is too large")
                if not line:
                    if proc.poll() is not None:
                        raise ValueError("stdio process stopped before replying")
                    continue
                try:
                    response = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if not isinstance(response, dict):
                    continue
                if response.get("id") == request_id and ("result" in response or "error" in response):
                    return {"ok": True, "id": process_id, "response": response}
            raise ValueError("stdio request timed out")

    def _identity_alive(self, meta):
        pid = int(meta.get("pid") or 0)
        if pid <= 1:
            return False
        start = _proc_start(pid)
        return bool(start and start == str(meta.get("proc_start") or "") and _marker_matches(pid, meta["id"]))

    def status(self, process_id):
        meta, paths = self._read_meta(process_id)
        with self._lock:
            proc = self._procs.get(process_id)
        exit_code = None
        if paths["stop"].exists():
            status = "stopped"
        elif paths["exit"].exists():
            try:
                exit_code = int(paths["exit"].read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                exit_code = None
            status = "completed"
        elif proc is not None and proc.poll() is None:
            status = "running"
        elif self._identity_alive(meta):
            status = "running"
        else:
            if proc is not None and proc.poll() is not None:
                exit_code = proc.returncode
                status = "completed"
            else:
                status = "unknown"
        try:
            size = paths["log"].stat().st_size
            with paths["log"].open("rb") as fh:
                if size > MAX_OUTPUT:
                    fh.seek(size - MAX_OUTPUT)
                logs = fh.read(MAX_OUTPUT).decode("utf-8", "replace")
        except OSError:
            logs = ""
        with self._lock:
            attached = bool(meta.get("mode") == "stdio" and self._procs.get(process_id) is not None)
        return {
            "ok": True,
            "id": process_id,
            "pid": int(meta["pid"]),
            "status": status,
            "exit_code": exit_code,
            "logs": logs,
            "started_at": int(meta.get("started_at") or 0),
            "mode": str(meta.get("mode") or "process"),
            "attached": attached,
        }

    def stop(self, process_id):
        meta, paths = self._read_meta(process_id)
        current = self.status(process_id)
        if current["status"] != "running":
            return current
        if not self._identity_alive(meta):
            return dict(current, status="unknown")
        pid = int(meta["pid"])
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline and self._identity_alive(meta):
            time.sleep(0.05)
        if self._identity_alive(meta):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        paths["stop"].write_text(str(int(time.time() * 1000)), encoding="utf-8")
        with self._lock:
            proc = self._procs.get(process_id)
            if proc is not None:
                try:
                    proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
        result = self.status(process_id)
        result["status"] = "stopped"
        result["ok"] = True
        return result


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "MusabAITermuxBridge/1"

    def log_message(self, fmt, *args):
        return

    def _authorized(self):
        expected = self.server.bridge_token
        supplied = self.headers.get("X-MusabAI-Token", "")
        auth = self.headers.get("Authorization", "")
        if not supplied and auth.startswith("Bearer "):
            supplied = auth[7:]
        return bool(supplied) and hmac.compare_digest(supplied, expected)

    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if size < 0 or size > MAX_BODY:
            raise ValueError("Request body too large")
        raw = self.rfile.read(size)
        try:
            value = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ValueError("Invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _dispatch(self):
        if not self._authorized():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlsplit(self.path)
        state = self.server.bridge_state
        try:
            if self.command == "GET" and parsed.path == "/health":
                out = state.health()
            elif self.command == "GET" and parsed.path == "/environment":
                values = parse_qs(parsed.query).get("commands", [])
                commands = []
                for value in values:
                    commands.extend([x for x in value.split(",") if x])
                out = state.environment(commands or None)
            elif self.command == "POST" and parsed.path == "/exec":
                body = self._body()
                out = state.exec(body.get("command"), body.get("timeout", 75))
            elif self.command == "POST" and parsed.path == "/process/start":
                body = self._body()
                out = state.start(body.get("command"), body.get("stdio", False), body.get("env"))
            elif self.command == "POST" and parsed.path == "/process/request":
                body = self._body()
                out = state.request(body.get("id"), body.get("message"), body.get("timeout", 120))
            elif self.command == "GET" and parsed.path == "/process/status":
                process_id = (parse_qs(parsed.query).get("id") or [""])[0]
                out = state.status(process_id)
            elif self.command == "POST" and parsed.path == "/process/stop":
                body = self._body()
                out = state.stop(body.get("id"))
            else:
                self._json(404, {"ok": False, "error": "not found"})
                return
            self._json(200, out)
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception:
            self._json(500, {"ok": False, "error": "bridge operation failed"})

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()


def make_server(host=DEFAULT_HOST, port=DEFAULT_PORT, token=None, root=None):
    token = token or os.environ.get("MUSABAI_TERMUX_BRIDGE_TOKEN", "")
    if not token or len(token) < 32:
        raise RuntimeError("MUSABAI_TERMUX_BRIDGE_TOKEN is required")
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError("bridge must bind to loopback")
    server = _Server((host, int(port)), BridgeHandler)
    server.bridge_token = token
    server.bridge_state = BridgeState(root)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    server = make_server(args.host, args.port, root=args.root)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
