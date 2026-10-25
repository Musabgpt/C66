"""The Termux loop on the phone, without the agent having to know where things run.

* Automatic BusyBox. Small file/search/shell commands run in the app's native BusyBox environment, even
  with Termux connected, if Android can access the project. Users do not need to request busybox explicitly.
  Termux keeps running node, npm, gh, SDKs, heavier scripts and complex mixed commands, in the shared project.
  The existing permissions, timeouts and job cancellation still apply; no command is automatically rerun.
* Chrome. A page of a local server (127.0.0.1, localhost) opens in Chrome, not in whatever browser is the default
  (Samsung Internet on many phones); the default browser when Chrome is not installed.
* One GitHub login. A token connected in the app is given to gh and git in Termux too, so a push uses the token the
  user just chose; when gh refuses it, git alone gets it.
"""
import os
import re
import shlex
import sys
import threading
import time

from . import tools

TERMUX = "/data/data/com.termux/"
SHARED = ("/storage/", "/sdcard/", "/mnt/sdcard/", TERMUX)
CHROME = "com.android.chrome"
LOCAL = re.compile(r"^https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])(?::\d+)?(?:[/?#]|$)", re.I)
EXEC_LIMIT = 70          # the bridge answers a command in one request up to 75 s; longer ones run as a process


def in_termux():
    """This engine itself runs in Termux (bash is Termux's shell already)."""
    return "com.termux" in (os.environ.get("PREFIX") or "") or os.path.realpath(sys.executable).startswith(TERMUX)


def reachable(root):
    """Termux sees this folder at the same path: shared storage, or Termux's own home."""
    root = str(root or "")
    return bool(root) and (root + "/").startswith(SHARED)


def termux_ready():
    try:
        from . import runtime_manager
        return bool(runtime_manager._phone_available()) and \
            runtime_manager._termux_record().get("status") == "connected"
    except Exception:  # noqa: BLE001
        return False


def in_project(ctx, command, runtime=None):
    """The command, started in the project folder when Termux can see it (else as it was, in Termux's home)."""
    if runtime and str(runtime).strip().upper() not in ("", "TERMUX"):
        return command
    root = str(getattr(ctx, "root", "") or "")
    if not reachable(root):
        return command
    return "cd %s 2>/dev/null; %s" % (shlex.quote(root), command)


# Safe commands provided by the packaged Android BusyBox (plus shell built-ins).
# Preserve Termux for npm, node, gh, SDKs, git, Python environments and mixed/complex scripts.
LOCAL_APPLETS = frozenset((
    "busybox", "awk", "basename", "cat", "cp", "cut", "dirname", "env",
    "find", "grep", "head", "ls", "mkdir", "mv", "readlink", "realpath",
    "rm", "sed", "sleep", "sort", "tail", "touch", "tr", "uname", "wc",
    "xargs", "echo", "printf", "pwd", "true", "false", "test",
    "tar", "unzip", "gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz",
    "sha256sum", "sha512sum", "sha1sum", "md5sum", "base64", "base32",
    "date", "stat", "du", "df", "id", "whoami", "which", "seq", "tee",
    "uniq", "comm", "diff", "patch", "ps", "top", "free", "uptime",
    "pidof", "kill", "nice", "nohup", "timeout", "mktemp", "ln",
    "rmdir", "expr", "hexdump", "od", "strings", "split", "printenv",
))
TERMUX_COMMANDS = frozenset((
    "pkg", "apt", "apt-get", "dpkg", "pip", "pip3", "uv", "python", "python3",
    "node", "npm", "npx", "yarn", "pnpm", "git", "gh", "bash", "sh", "make",
    "cmake", "clang", "gcc", "g++", "gradle", "java", "javac", "curl",
    "wget", "ssh", "scp", "cargo", "rustc", "ffmpeg", "yt-dlp",
))


def needs_termux(command):
    """Only route known development/package commands when project is app-private."""
    try:
        return shlex.split(str(command or ""), posix=True)[0] in TERMUX_COMMANDS
    except (ValueError, IndexError):
        return False


def local_busybox_command(command):
    """Conservatively choose the on-phone shell without rerunning a partially completed command.

    Only known, explicitly bundled commands (including simple pipelines) qualify.
    Complex scripts and commands needing a package manager keep the old Termux route.
    """
    binary = os.environ.get("NEWAL_BUSYBOX") or ""
    if os.environ.get("NEWAL_BUSYBOX_AUTO") != "1" or not os.path.isfile(binary):
        return False
    cmd = str(command or "").strip()
    if not cmd or any(token in cmd for token in ("\n", "\r", ";", "&&", "||", "$(", "`", "<", ">")):
        return False
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars="|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    expecting_command = True
    found = False
    for token in tokens:
        if token == "|":
            if expecting_command:
                return False
            expecting_command = True
        elif expecting_command:
            if token not in LOCAL_APPLETS:
                return False
            found = True
            expecting_command = False
    return found and not expecting_command


def routes(ctx, command=None):
    """Use BusyBox in MusabAI for local commands; Termux only when its runtimes are needed.

    A call without a command retains legacy route detection for existing integrations.
    """
    try:
        from . import lean
        project = str(getattr(ctx, "root", "") or "")
        if not lean.active() or in_termux() or not termux_ready():
            return False
        if command is not None and local_busybox_command(command) and os.access(project, os.R_OK | os.X_OK):
            return False
        if not reachable(project):
            # npm/pkg/gh and other heavy tools still work in Termux's own HOME
            # even when the APK-private project cannot be read by Termux.
            return command is not None and needs_termux(command)
        return True
    except Exception:  # noqa: BLE001
        return False


def _emit(ctx, text):
    try:
        for line in text.splitlines():
            ctx.emit({"type": "output", "text": line})
    except Exception:  # noqa: BLE001
        pass


def run_in_termux(ctx, command, timeout=120, background=False):
    from . import runtime_manager
    command = str(command or "").strip()
    if not command:
        raise tools.ToolError("empty command")
    full = in_project(ctx, command)
    if background:
        data = runtime_manager.process_start(full)
        pid = data.get("id")
        return ("started in Termux as process %s: runtime_process_status shows its output, runtime_process_stop "
                "stops it" % pid), {"command": command, "process": pid, "runtime": "termux", "pending": True}
    timeout = min(max(1, int(timeout or 120)), 1800)
    started = time.time()
    note = ""
    if timeout <= EXEC_LIMIT:
        data = runtime_manager.execute(full)
        out = (data.get("stdout") or "") + (("\n" + data["stderr"]) if data.get("stderr") else "")
        code = data.get("exit_code")
        if data.get("status") == "timeout":
            code, note = 124, "\n(stopped after 75 s: run long commands with a larger timeout or background=true)"
        _emit(ctx, out)
    else:
        pid = runtime_manager.process_start(full).get("id")
        wait, seen, st = 0.15, 0, {}
        while True:
            cancel = getattr(ctx, "cancel", None)
            if cancel is not None and cancel.is_set():
                runtime_manager.process_stop(pid)
                raise tools.ToolError("cancelled")
            st = runtime_manager.process_status(pid)
            logs = st.get("logs") or ""
            if len(logs) > seen:
                _emit(ctx, logs[seen:])
                seen = len(logs)
            if st.get("status") != "running":
                break
            if time.time() - started > timeout:
                runtime_manager.process_stop(pid)
                note = "\n(stopped after %d s: give it a larger timeout, or background=true)" % timeout
                break
            time.sleep(wait)
            wait = min(1.0, wait * 1.5)
        out = st.get("logs") or ""
        code = 124 if note else st.get("exit_code")
    shown = tools.clip(out.strip()) + note
    return ("exit %s\n%s" % (code if code is not None else "?", shown)).rstrip(), {
        "command": command, "exit": code, "output": out[-20000:], "seconds": round(time.time() - started, 2),
        "sandboxed": False, "runtime": "termux"}


def wrap_bash():
    t = tools.REGISTRY.get("bash")
    if t is None or getattr(t.fn, "_termux", False):
        return
    original = t.fn

    def bash(ctx, command, timeout=120, background=False):
        if not routes(ctx, command):
            result = original(ctx, command, timeout=timeout, background=background)
            if local_busybox_command(command) and isinstance(result, tuple) and len(result) == 2:
                body, meta = result
                meta = dict(meta)
                meta["runtime"] = "android-busybox"
                return body, meta
            return result
        return run_in_termux(ctx, command, timeout, background)
    bash._termux = True
    t.fn = bash


def wrap_phone():
    """Open all ordinary web links in installed Chrome, with a browser fallback if absent."""
    t = tools.REGISTRY.get("phone")
    if t is None or getattr(t.fn, "_chrome", False):
        return
    inner = t.fn

    def phone(ctx, action, **args):
        url = str(args.get("url") or "")
        if action == "open_url" and url.lower().startswith(("https://", "http://")) and not args.get("package"):
            try:
                return inner(ctx, action, **dict(args, package=CHROME))
            except tools.ToolError as e:
                if "no app" not in str(e):
                    raise
        return inner(ctx, action, **args)
    phone._chrome = True
    t.fn = phone


# ------------------------------------------------------------------ GitHub: the app's token in Termux too

def token_kind(tok):
    tok = str(tok or "")
    if tok.startswith("github_pat_"):
        return "fine-grained"
    if tok.startswith("ghp_"):
        return "classic"
    if tok.startswith("gho_"):
        return "oauth"
    if tok.startswith(("ghu_", "ghs_")):
        return "app"
    return ""


def github_sync(tok):
    """gh and git in Termux sign in with the app's token. gh needs repo and read:org; when it refuses the token, git
    alone stores it (and stops asking gh's old login). True when it was handed over."""
    tok = str(tok or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{20,255}", tok) or in_termux() or not termux_ready():
        return False
    from . import runtime_manager
    script = (
        "T=%s\n"
        "ok=0\n"
        "if command -v gh >/dev/null 2>&1 && printf '%%s\\n' \"$T\" | gh auth login -h github.com --with-token "
        ">/dev/null 2>&1; then\n"
        "  gh auth setup-git -h github.com >/dev/null 2>&1 && ok=1\n"
        "fi\n"
        "if [ \"$ok\" = 0 ]; then\n"
        "  umask 077\n"
        "  printf 'https://x-access-token:%%s@github.com\\n' \"$T\" > \"$HOME/.git-credentials\"\n"
        "  git config --global --unset-all credential.https://github.com.helper >/dev/null 2>&1\n"
        "  git config --global --add credential.https://github.com.helper ''\n"
        "  git config --global --add credential.https://github.com.helper store\n"
        "fi\n"
        "echo synced:$ok\n") % tok
    try:
        data = runtime_manager.execute(script)
    except Exception:  # noqa: BLE001 - the app keeps the token either way
        return False
    return "synced:" in str(data.get("stdout") or "")


def github_sync_later(tok):
    threading.Thread(target=github_sync, args=(tok,), name="musab-github-sync", daemon=True).start()


def install():
    wrap_bash()
    wrap_phone()
