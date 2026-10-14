"""The phone, used the way a person tests on one.

* screen_look: a screenshot of what is on the phone (or any image file in the project) is shown to a vision model,
  which answers a question about it: layout, colours, text that the accessibility tree misses, games, canvases.
* android_test_app: install an APK the agent built, open it, watch it for a few seconds and report what happened:
  did it start, did it crash ("keeps stopping"), what the screen shows (text and a vision description), and the
  app's own MusabTestBridge crash/ANR reports.
* finished(): when a long turn ends while the app is in the background (or the screen is off), Android shows a
  notification with a short summary; tapping it opens the app.
"""
import base64
import os
import re
import threading
import time

NAMES = ["screen_look", "android_test_app"]
MAX_IMAGE = 3_500_000          # bytes sent to a vision model after encoding
NOTIFY_AFTER = 20              # seconds: shorter turns end while the user is still looking
INSTALLERS = ("packageinstaller", "com.android.packageinstaller", "com.google.android.packageinstaller")
CRASH = re.compile(r"keeps stopping|has stopped|isn.t responding|stopped working|توقف|لا يستجيب|متوقف", re.I)

VISION_PROMPT = ("This is a screenshot of an Android phone screen{what}. {question}\n"
                 "Answer precisely and briefly: what app and screen it is, the visible text, buttons and their "
                 "positions (top/middle/bottom, left/right), and anything that looks broken (overlapping or cut-off "
                 "text, empty areas that should have content, error messages, wrong colours or contrast).")


def _phone():
    from . import phone
    return phone


def phone_ready():
    try:
        return bool(_phone().available())
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ images

def _encode(path):
    """A data: URL for an image file, made small enough for a vision request."""
    with open(path, "rb") as f:
        data = f.read()
    mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else \
        "image/webp" if path.lower().endswith(".webp") else "image/png"
    if len(data) * 4 // 3 > MAX_IMAGE:
        try:
            from PIL import Image  # noqa: WPS433 - optional; the phone sends a small JPEG of its own
            import io
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((720, 1600))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=72)
            data, mime = out.getvalue(), "image/jpeg"
        except Exception:  # noqa: BLE001
            pass
    if len(data) * 4 // 3 > MAX_IMAGE:
        raise ValueError("the image is too big to show to the model (%d KB)" % (len(data) // 1024))
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def describe(agent, image_path, question="", what=""):
    """What a vision model sees in the image ("" when no vision model answered)."""
    client = getattr(agent, "client", None)
    if client is None or getattr(client, "on_device", False):
        return ""
    prompt = VISION_PROMPT.format(what=what, question=(question or "Describe what is on it.").strip())
    msg = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                        {"type": "image_url", "image_url": {"url": _encode(image_path)}}]}]
    s = agent.session
    comp = client.chat(msg, tools=None, owner=s.id, max_tokens=900, reasoning="off", cancel=agent.cancel)
    return _text(getattr(comp, "content", "") or "").strip()


def screenshot():
    """(path of a small image of the screen, the full screenshot's path)."""
    r = _phone().call("screenshot", timeout=30)
    full = r.get("path") or ""
    small = r.get("preview_path") or ""
    return (small if small and os.path.isfile(small) else full), full


def screen_text():
    try:
        return str(_phone().call("screen", timeout=20).get("text") or "")
    except Exception as e:  # noqa: BLE001
        return "(screen unreadable: %s)" % e


def foreground(text):
    m = re.match(r"App: .*?\(([\w.]+)\)", text or "")
    return m.group(1) if m else ""


def installed():
    try:
        text = str(_phone().call("apps", timeout=30).get("text") or "")
    except Exception:  # noqa: BLE001
        return set()
    return set(re.findall(r"\(([\w.]+)\)\s*$", text, re.M))


def application_id(root):
    """The applicationId the project's Gradle files declare ("" when none)."""
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("build", ".gradle", "node_modules", ".git")]
        for f in files:
            if f in ("build.gradle", "build.gradle.kts"):
                try:
                    with open(os.path.join(folder, f), encoding="utf-8", errors="replace") as fh:
                        m = re.search(r"applicationId\s*=?\s*[\"']([\w.]+)[\"']", fh.read())
                except OSError:
                    continue
                if m:
                    return m.group(1)
        if folder.count(os.sep) - root.count(os.sep) > 3:
            dirs[:] = []
    return ""


# ------------------------------------------------------------------ pictures for the chat

def _picture(ctx, path):
    """{"shot": {"sid", "name"}}: the chat's copy of a picture the agent looked at ({} when it cannot be kept). The
    phone clears its screenshot cache when the next screenshot is taken, so the copy is what the chat shows later."""
    from . import session_images
    try:
        shot = session_images.keep(ctx.session.id, path)
    except Exception:  # noqa: BLE001 - the copy is for the chat; the answer never waits on it
        shot = None
    return {"shot": shot} if shot else {}


# ------------------------------------------------------------------ tools

def install():
    from . import tools

    @tools.tool("screen_look", "Look at the phone's screen with your eyes (a screenshot shown to a vision model) and "
                "answer a question about it: layout, colours, images, games, text the screen tool cannot read, what "
                "looks broken. Open the app first: it shows whatever is on the screen. With image: look at an image "
                "file in the project instead (a UI screenshot, a design, a chart).",
                {"question": tools._s("what to check, e.g. 'is the login button visible and aligned?'"),
                 "image": tools._s("optional image file in the project to look at instead of the screen")},
                [], "read")
    def t_screen_look(ctx, question="", image=""):
        if image:
            path = tools.resolve(ctx, image)
            if not os.path.isfile(path):
                raise tools.ToolError("no such image: %s" % image)
            what = ""
        else:
            if not phone_ready():
                raise tools.ToolError("no phone here: screen_look needs NewAl Code on Android (or pass image)")
            try:
                path, full = screenshot()
            except Exception as e:  # noqa: BLE001
                return ("No screenshot (%s). Screenshots need Android 11 and NewAl Code's accessibility service. "
                        "What the screen tool reads:\n%s" % (e, screen_text()[:4000])), {"vision": False}
            what = ""
        try:
            seen = describe(ctx.agent, path, question, what)
        except Exception as e:  # noqa: BLE001 - no vision provider: still useful
            seen, err = "", str(e)
        else:
            err = ""
        if seen:
            return seen[:6000], dict({"vision": True}, **_picture(ctx, path))
        fallback = "" if image else "\nWhat the screen tool reads:\n" + screen_text()[:4000]
        meta = dict({"vision": False}, **_picture(ctx, path))
        return ("No vision model answered%s. Add a free Gemini key (vision) in Providers, or use a model that "
                "reads images.%s" % ((": " + err[:300]) if err else "", fallback)), meta

    @tools.tool("android_test_app", "Test an Android app you built on this phone like a person would: install the APK "
                "(the user approves Android's prompt), open the app, watch it, and report whether it started or "
                "crashed, what the screen shows (text and a screenshot description) and its crash/ANR reports. "
                "Then fix and run it again. The APK must be in the project or the phone's shared storage (copy a "
                "Termux build to ~/storage/downloads first).",
                {"apk": tools._s("path of the .apk to install (omit to test the installed app)"),
                 "app": tools._s("the app's package or name (default: the project's applicationId)"),
                 "question": tools._s("what to check on its first screen"),
                 "seconds": tools._i("how long to watch it after opening (default 6)")},
                [], "phone")
    def t_test_app(ctx, apk="", app="", question="", seconds=6):
        if not phone_ready():
            raise tools.ToolError("no phone here: android_test_app needs NewAl Code on Android")
        ph = _phone()
        report, meta = [], {"installed": None, "started": False, "crashed": False}
        app = str(app or "").strip() or application_id(ctx.root)
        if apk:
            path = tools.resolve(ctx, apk)
            if not os.path.isfile(path) or not path.endswith(".apk"):
                raise tools.ToolError("no such APK: %s" % apk)
            before = installed()
            ctx.emit({"type": "status", "text": "Installing %s: approve it on the phone…" % os.path.basename(path)})
            try:
                ph.call("install_apk", path=path)
            except ph.PhoneError as e:
                raise tools.ToolError("install failed: %s" % e)
            seen_installer, deadline = False, time.time() + 180
            while time.time() < deadline:
                if ctx.cancel is not None and ctx.cancel.is_set():
                    raise tools.ToolError("cancelled")
                time.sleep(2)
                fg = foreground(screen_text())
                if any(i in fg for i in INSTALLERS):
                    seen_installer = True
                    continue
                new = installed() - before
                if new:
                    app = app or sorted(new)[0]
                    meta["installed"] = True
                    break
                if seen_installer:                   # the installer closed: installed (an update) or cancelled
                    meta["installed"] = True
                    break
            report.append("Install: %s" % ("done" if meta["installed"] else
                                           "not confirmed (the user may not have approved it yet)"))
        if not app:
            raise tools.ToolError("which app? pass app (its package or name); no applicationId in the project")
        try:
            ph.call("open_app", name=app)
        except ph.PhoneError as e:
            report.append("Open %s: failed (%s)" % (app, e))
            return "\n".join(report), meta
        seconds = max(2, min(int(seconds or 6), 30))
        text, fg = "", ""
        for _ in range(max(1, seconds // 2)):
            time.sleep(2)
            text = screen_text()
            fg = foreground(text)
            if CRASH.search(text):
                meta["crashed"] = True
                break
        meta["started"] = bool(fg) and (fg == app or app.lower() in text.lower()[:200]) and not meta["crashed"]
        meta["foreground"] = fg
        if meta["crashed"]:
            report.append("Result: CRASHED: Android shows a crash dialog.")
        elif meta["started"]:
            report.append("Result: started, %s is on screen after %ds." % (fg, seconds))
        else:
            report.append("Result: not on screen (the screen shows %s): it may have closed or crashed at start."
                          % (fg or "something else"))
        report.append("Screen text:\n" + text[:2500])
        try:
            small, _ = screenshot()
            meta.update(_picture(ctx, small))
            seen = describe(ctx.agent, small, question or "Does this app screen look right?", " of the app %s" % app)
            if seen:
                report.append("What the screenshot shows:\n" + seen[:2500])
        except Exception:  # noqa: BLE001 - text alone still tells a lot
            pass
        try:
            reports = str(ph.call("crash_reports", timeout=20).get("text") or "")
            if reports and "no local test reports" not in reports:
                report.append("Crash/ANR reports:\n" + reports[-4000:])
        except Exception:  # noqa: BLE001
            pass
        if meta["crashed"] or not meta["started"]:
            report.append("Next: read the stack trace (crash reports above, or `logcat -d -b crash` in Termux if it "
                          "has adb access), fix the cause, rebuild and run android_test_app again.")
        return "\n\n".join(report), meta


# ------------------------------------------------------------------ the end of a turn

def summary(answer, seconds, error="", changes=()):
    text = re.sub(r"[`*_#>\[\]]", "", str(answer or "")).strip()
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    if len(first) > 180:
        first = first[:177].rstrip() + "…"
    mins, secs = divmod(int(seconds or 0), 60)
    took = ("%dm %ds" % (mins, secs)) if mins else ("%ds" % secs)
    bits = [took]
    if changes:
        bits.append("%d file%s changed" % (len(changes), "" if len(changes) == 1 else "s"))
    title = "MusabAI: توقفت المهمة" if error and error != "interrupted" else "MusabAI: انتهت المهمة ✓"
    return title, ((first + "\n") if first else "") + " · ".join(bits)


def finished(agent, answer, seconds, error="", changes=()):
    """A notification with the turn's summary, sent only while the user is not looking at the app."""
    try:
        cfg = getattr(agent, "cfg", None) or {}
        if getattr(agent, "depth", 0) or cfg.get("notify_on_finish") is False or error == "interrupted":
            return
        if float(seconds or 0) < float(cfg.get("notify_after_seconds") or NOTIFY_AFTER) or not phone_ready():
            return
    except Exception:  # noqa: BLE001
        return
    title, text = summary(answer, seconds, error, changes)

    def send():
        try:
            _phone().call("notify", timeout=10, title=title, text=text, only_background=True)
        except Exception:  # noqa: BLE001 - a notification is a courtesy
            pass
    threading.Thread(target=send, name="musab-finish-notify", daemon=True).start()


def approval_waiting(req, answered, pending=None, delay=15):
    """A tool call has waited `delay` seconds for the user's approval (often a background agent, or a long turn the
    user walked away from): Android says so, when the app is not on screen. Nothing is sent once it is answered."""
    if not phone_ready():
        return

    def run():
        if answered.wait(delay) or (pending is not None and not pending()):
            return
        args = req.get("args") or {}
        what = str(args.get("command") or args.get("path") or args.get("url") or args.get("action") or "")[:160]
        try:
            _phone().call("notify", timeout=10, title="MusabAI ينتظر موافقتك",
                          text=("%s: %s" % (req.get("tool") or "tool", what)).strip(": "), only_background=True)
        except Exception:  # noqa: BLE001 - a notification is a courtesy
            pass
    threading.Thread(target=run, name="musab-approval-notify", daemon=True).start()
