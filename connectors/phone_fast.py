"""Fast, accurate phone use, and routines the agent learns from what worked.

Speed: a model round trip costs seconds, a local screen read milliseconds. So every action that changes the screen
(open_app, open_url, tap, type, swipe, scroll, key) comes back with the screen as it is now: the app reads it right
after the action (A11y.withScreen) and the model sees the result of its action without asking for it. After opening an
app the app waits until that app is really in front.
Arabic app names (واتساب، يوتيوب، كروم...) open the right app.

Routines: when a turn that used the phone ends without an error, its steps are saved: a tap on item [7] becomes a tap
on that item's text (numbers change, texts don't), and a text that came from the request (the contact, the message,
the search) becomes a slot. The next request like it gets a note with the routine, and phone_routine replays all of it
in one call, checking each step; where the screen differs it stops and shows the screen, and the turn that finishes by
hand saves the corrected steps over the old ones. At its end the text the routine typed last must be on the screen: if
it is not, the routine says so, and the turn checks by hand before it says the task is done.
"""
import json
import os
import re
import threading
import time

from . import settings, tools

NAMES = ["phone_routine"]
OBSERVE = ("open_app", "open_url", "tap", "type", "swipe", "scroll", "key", "intent", "settings")
SKIP = ("screen", "screenshot", "wait", "apps", "battery", "device", "notifications_read", "crash_reports",
        "automation_list", "clipboard", "notify")
APP_NAMES = {
    "واتساب": "com.whatsapp", "واتس": "com.whatsapp", "الواتس": "com.whatsapp", "واتس اب": "com.whatsapp",
    "whatsapp": "com.whatsapp", "واتساب للأعمال": "com.whatsapp.w4b",
    "يوتيوب": "com.google.android.youtube", "اليوتيوب": "com.google.android.youtube",
    "youtube": "com.google.android.youtube",
    "كروم": "com.android.chrome", "جوجل كروم": "com.android.chrome", "chrome": "com.android.chrome",
    "المتصفح": "com.android.chrome",
    "تيرمكس": "com.termux", "termux": "com.termux",
    "تلغرام": "org.telegram.messenger", "تيليجرام": "org.telegram.messenger", "telegram": "org.telegram.messenger",
    "انستغرام": "com.instagram.android", "انستقرام": "com.instagram.android", "instagram": "com.instagram.android",
    "فيسبوك": "com.facebook.katana", "facebook": "com.facebook.katana",
    "ماسنجر": "com.facebook.orca", "messenger": "com.facebook.orca",
    "تيك توك": "com.zhiliaoapp.musically", "تيكتوك": "com.zhiliaoapp.musically", "tiktok": "com.zhiliaoapp.musically",
    "سناب": "com.snapchat.android", "سناب شات": "com.snapchat.android", "snapchat": "com.snapchat.android",
    "تويتر": "com.twitter.android", "اكس": "com.twitter.android",
    "جيميل": "com.google.android.gm", "gmail": "com.google.android.gm",
    "خرائط": "com.google.android.apps.maps", "الخرائط": "com.google.android.apps.maps",
    "خرائط جوجل": "com.google.android.apps.maps", "maps": "com.google.android.apps.maps",
    "متجر بلاي": "com.android.vending", "بلاي ستور": "com.android.vending", "play store": "com.android.vending",
    "الإعدادات": "com.android.settings", "الاعدادات": "com.android.settings", "settings": "com.android.settings",
    "الكاميرا": "camera", "الصور": "com.google.android.apps.photos", "جيتهاب": "com.github.android",
    "github": "com.github.android", "سبوتيفاي": "com.spotify.music", "spotify": "com.spotify.music",
    "نتفلكس": "com.netflix.mediaclient", "netflix": "com.netflix.mediaclient",
}
_lock = threading.RLock()
MAX_ROUTINES = 120


def _phone():
    from . import phone
    return phone


def _path():
    return os.path.join(settings.HOME, "phone_routines.json")


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(routines):
    with _lock:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(routines[-MAX_ROUTINES:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path())


# ------------------------------------------------------------------ the screen

ITEM = re.compile(r"^\[(\d+)\]\s+(.*?)(?:\s+·\s+[^·]*)?$")


def items(screen):
    """{number: text} of a screen listing ("[7] مروة · tap" -> {7: "مروة"}); items without text are left out."""
    out = {}
    for line in (screen or "").splitlines():
        m = ITEM.match(line.strip())
        if m and not m.group(2).startswith("("):
            text = m.group(2).strip()
            if text.startswith("hint: "):
                text = text[6:]
            out[int(m.group(1))] = text
    return out


def foreground(screen):
    m = re.match(r"App: .*?\(([\w.]+)\)", screen or "")
    return m.group(1) if m else ""


def read_screen(timeout=15):
    r = _phone().call("screen", timeout=timeout)
    return str(r.get("text") or "")


def compact_screen(agent):
    """After an automatic compaction in a turn that used the phone: the screen as it is now, read again, so the summary
    is not all the model has of the phone. Nothing when this turn did not use the phone, or the phone is away."""
    ctx = getattr(agent, "last_ctx", None)
    if ctx is None:
        return ""
    try:
        st = _state(ctx)
    except AttributeError:
        return ""
    if not (st["log"] or st["screen"]):
        return ""
    try:
        screen = read_screen()
    except Exception:  # noqa: BLE001 - the phone may be away; the compaction goes on without the screen
        return ""
    if not screen:
        return ""
    st["screen"] = screen
    return tools.clip(screen, 7000)


def app_name(name):
    key = re.sub(r"\s+", " ", str(name or "").strip().lower())
    key = re.sub(r"^(?:تطبيق|برنامج|app)\s+", "", key)
    return APP_NAMES.get(key, name)


# ------------------------------------------------------------------ the phone tool, faster

def _state(ctx):
    """This turn's phone log on the agent: [(action, args, ok)], and the last screen listing."""
    agent = ctx.agent
    turn = getattr(ctx.session, "turn", 0)
    st = getattr(agent, "_phone_turn", None)
    if not st or st.get("turn") != turn:
        st = {"turn": turn, "log": [], "screen": "", "routine": None}
        agent._phone_turn = st
    return st


def _step(action, args, screen):
    """The step as it is saved: a tap on a number becomes a tap on the item's text."""
    keep = {k: v for k, v in args.items() if k in ("name", "text", "item", "url", "direction", "page", "x", "y")
            and v not in (None, "")}
    if action == "tap" and keep.get("item") and not keep.get("text"):
        label = items(screen).get(int(keep["item"]))
        if label:
            keep = {"text": label}
    if action == "type":
        keep.pop("item", None)
    return dict(keep, action=action)


def wrap_phone():
    t = tools.REGISTRY.get("phone")
    if t is None or getattr(t.fn, "_fast", False):
        return
    original = t.fn

    def fast(ctx, action, **args):
        action = str(action or "")
        if action == "open_app" and args.get("name"):
            args["name"] = app_name(args["name"])
        try:
            st = _state(ctx)
        except AttributeError:                        # a call outside an agent's turn: nothing to remember
            return original(ctx, action, **args)
        before = st["screen"]
        try:
            text, meta = original(ctx, action, **args)
        except tools.ToolError:
            st["log"].append((action, args, False))
            raise
        if action == "screen":
            st["screen"] = text
        if action not in SKIP:
            st["log"].append((action, _step(action, args, before), True))
        screen = ""
        if isinstance(meta, dict) and isinstance(meta.get("phone"), dict):
            screen = str(meta["phone"].pop("screen", "") or "")      # the app sends the screen with the action
        if screen and action in OBSERVE:
            st["screen"] = screen
            text = "%s\n\nScreen now:\n%s" % (text, tools.clip(screen, 7000))
        return text, meta
    fast._fast = True
    t.fn = fast
    t.description = DOC


def observe(action, args):
    """The screen after an action; after opening an app, once that app is in front (up to ~3 s)."""
    try:
        if action in ("open_app", "open_url", "intent", "settings"):
            want = str(args.get("name") or "") if action == "open_app" else ""
            screen = ""
            for _ in range(6):
                screen = read_screen()
                fg = foreground(screen)
                if fg and not fg.startswith(("dev.newal", "com.android.systemui")) and (
                        not want or "." not in want or fg == want):
                    if items(screen):
                        return screen
                time.sleep(0.5)
            return screen
        return read_screen()
    except Exception:  # noqa: BLE001 - no accessibility service: the action's own answer is all there is
        return ""


DOC = ("Use the Android phone this runs on: open_app (name: the app's name in any language, or its package), "
       "open_url (links, YouTube or web searches, local servers in Chrome), tap (item: the number from the latest "
       "screen, or text), type (text into the focused field; tap the field first), swipe/scroll (direction), key "
       "(name: back, home, enter), settings (page), screen (lists the UI as numbered items), screenshot, alarm, "
       "timer, clipboard, notify, install_apk. Every action that changes the screen returns the new screen in its "
       "result: read it there instead of calling screen. Reading the screen and tapping need NewAl Code's "
       "accessibility service. Not for files or code.")


# ------------------------------------------------------------------ learning what worked

def _norm(text):
    t = re.sub(r"[ً-ْـ]", "", str(text or "").lower())
    t = t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def _words(text):
    return {w for w in _norm(text).split() if len(w) > 1}


def template(request, steps):
    """(steps with slots, slot values, the request with "…" for the slots)."""
    req = _norm(request)
    values, out = [], []
    for st in steps:
        st = dict(st)
        for key in ("text",):
            v = str(st.get(key) or "").strip()
            if len(v) >= 2 and _norm(v) and _norm(v) in req:
                if v not in values:
                    values.append(v)
                st[key] = "{%d}" % (values.index(v) + 1)
        out.append(st)
    pattern = req
    for v in values:
        pattern = pattern.replace(_norm(v), "…")
    return out, values, pattern


def learn(agent, error=""):
    """At the end of a turn: the phone steps of a turn that ended well become (or replace) a routine."""
    try:
        st = getattr(agent, "_phone_turn", None)
        if getattr(agent, "depth", 0) or error or not st or st.get("turn") != getattr(agent.session, "turn", None):
            return None
        log = st["log"]
        if not log or not log[-1][2]:
            return None
        steps = []
        for action, step, ok in log:
            if not ok or action == "phone_routine":
                continue
            if steps and steps[-1] == step:
                continue
            steps.append(step)
        if not any(s["action"] in ("tap", "type", "swipe", "scroll", "key") for s in steps):
            used = st.get("routine")
            if used:
                touch(used)
            return None
        request = getattr(agent, "_phone_request", "") or ""
        if not request.strip():
            return None
        steps, values, pattern = template(request, steps[:40])
        with _lock:
            old = [r for r in load() if r.get("pattern") == pattern]
            routines = [r for r in load() if r.get("pattern") != pattern]
            rid = old[0]["id"] if old else "r%d" % (max([int(str(r.get("id", "r0"))[1:] or 0) for r in routines]
                                                        + [0]) + 1)
            routines.append({"id": rid, "request": request[:300], "pattern": pattern, "values": values,
                             "steps": steps, "uses": int(old[0].get("uses") or 0) if old else 0,
                             "saved": int(time.time())})
            save(routines)
        return rid
    except Exception:  # noqa: BLE001 - learning never breaks a turn
        return None


def touch(rid):
    with _lock:
        routines = load()
        for r in routines:
            if r.get("id") == rid:
                r["uses"] = int(r.get("uses") or 0) + 1
                r["last"] = int(time.time())
        save(routines)


def match(request, routines=None):
    """The saved routine closest to the request, when it is close enough: most of the routine's own words (its
    slots left out) are in the request, and they are a fair part of the request."""
    words = _words(request)
    if not words:
        return None
    best, score = None, 0.0
    for r in routines if routines is not None else load():
        rw = _words(r.get("pattern", "").replace("…", " "))
        if not rw:
            continue
        common = len(words & rw)
        s = common / float(len(rw))
        if common / float(len(words)) >= 0.3 and s > score:
            best, score = r, s
    return best if score >= 0.6 else None


def describe(r):
    def one(s):
        a = s.get("action")
        arg = s.get("name") or s.get("text") or s.get("url") or s.get("direction") or s.get("page") or s.get("item") or ""
        return "%s %s" % (a, json.dumps(arg, ensure_ascii=False) if arg != "" else "")
    return " → ".join(one(s).strip() for s in r.get("steps") or [])


def note(agent, request):
    """The line a matching routine adds to the user's message (on the phone, in lean mode)."""
    agent._phone_request = request
    try:
        from . import lean
        if not lean.active() or "phone_routine" not in agent.tool_names():
            return ""                                 # (a small on-device model has the phone tool only)
        r = match(request)
    except Exception:  # noqa: BLE001
        return ""
    if not r:
        return ""
    vals = ", ".join('{%d} was "%s"' % (i + 1, v) for i, v in enumerate(r.get("values") or []))
    return ("(Saved phone routine %s, learned from \"%s\"%s: %s. If it fits this request, call phone_routine with "
            "id %s and the values for this request in one call; it checks each step and shows the final screen.)"
            % (r["id"], r.get("request", "")[:120], (" — " + vals) if vals else "", describe(r), r["id"]))


# ------------------------------------------------------------------ replaying

def _fill(step, values):
    out = {}
    for k, v in step.items():
        if isinstance(v, str):
            v = re.sub(r"\{(\d+)\}", lambda m: values[int(m.group(1)) - 1] if int(m.group(1)) <= len(values) else
                       m.group(0), v)
        out[k] = v
    return out


def expected(r, values):
    """The text the routine typed last, slots filled: once the routine has sent or searched it, it is on the screen."""
    for s in reversed(r.get("steps") or []):
        if s.get("action") == "type" and str(s.get("text") or "").strip():
            text = _fill({"text": s["text"]}, values)["text"]
            return text if _norm(text) else ""
    return ""


def shows(screen, text):
    """Whether the text is on the screen (a long text may be cut or wrapped: its start is enough)."""
    t = _norm(text)[:40]
    return bool(t) and t in _norm(screen)


def _settle(screen, want):
    """The screen once the text is on it: a message can take a moment to appear after Send (up to ~2 s)."""
    for _ in range(4):
        if not want or shows(screen, want):
            break
        time.sleep(0.5)
        try:
            screen = read_screen()
        except Exception:  # noqa: BLE001 - keep the last screen
            break
    return screen


def install():
    @tools.tool("phone_routine",
                "Replay a saved phone routine (a task done before on this phone) in one call: each step is checked, "
                "and the final screen must show the text it typed last. values fill its slots {1}, {2}... for this "
                "request (a contact, a message, a search). Where the screen differs it stops and shows the screen: "
                "continue by hand from there. action=list shows the saved routines; action=forget removes one.",
                {"id": tools._s("the routine's id, e.g. r3"),
                 "values": {"type": "array", "items": {"type": "string"},
                            "description": "slot values in order: {1}, {2}..."},
                 "action": tools._s("run (default), list or forget")}, [], "phone")
    def t_phone_routine(ctx, id="", values=None, action="run"):
        if action == "list":
            rs = load()
            if not rs:
                return "No saved routines yet: they are saved when a phone task ends well."
            return "\n".join("%s: \"%s\" (%d uses) %s" % (r["id"], r.get("request", "")[:80], r.get("uses", 0),
                                                        describe(r)) for r in rs[-30:])
        r = next((x for x in load() if x.get("id") == id), None)
        if r is None:
            raise tools.ToolError("no routine %s (action=list shows them)" % id)
        if action == "forget":
            save([x for x in load() if x.get("id") != id])
            return "forgot %s" % id
        vals = [str(v) for v in (values or [])]
        need = len(r.get("values") or [])
        if len(vals) < need:
            vals += (r.get("values") or [])[len(vals):need]
        phone = _phone()
        st = _state(ctx)
        st["routine"] = id
        done = []
        for i, step in enumerate(r.get("steps") or []):
            if ctx.cancel is not None and ctx.cancel.is_set():
                raise tools.ToolError("cancelled")
            s = _fill(step, vals)
            act = s.pop("action")
            if act == "open_app" and s.get("name"):
                s["name"] = app_name(s["name"])
            try:
                res = phone.call(act, **s)
                if not res.get("ok", True):
                    raise phone.PhoneError(res.get("error") or res.get("text") or "failed")
            except phone.PhoneError as e:
                screen = observe("tap", {})
                st["screen"] = screen or st["screen"]
                return ("Routine %s stopped at step %d of %d (%s %s): %s\nDone before it: %s\n\nScreen now:\n%s\n"
                        "Continue by hand from this screen." % (
                            id, i + 1, len(r["steps"]), act, json.dumps(s, ensure_ascii=False), e,
                            "; ".join(done) or "nothing", tools.clip(screen, 7000))), {"routine": id, "done": False}
            done.append("%s %s" % (act, res.get("text") or ""))
            st["log"].append((act, dict(s, action=act), True))
            if res.get("screen"):
                st["screen"] = str(res["screen"])
            elif act in ("open_app", "open_url"):
                observe(act, s)
        touch(id)
        want = expected(r, vals)
        screen = _settle(observe("tap", {}), want)
        st["screen"] = screen
        if want and not shows(screen, want):
            return ("Routine %s ran its %d steps, but \"%s\" is not on the screen now, so it may not have gone "
                    "through. Look at the screen (screen_look if you must) and fix it by hand before you say it is "
                    "done.\n\nScreen now:\n%s" % (id, len(done), want, tools.clip(screen, 7000))), \
                {"routine": id, "done": False}
        check = ('Checked: "%s" is on the screen.' % want) if want else \
            "Nothing typed to check on the screen: check the result yourself before you say it is done."
        return ("Routine %s done (%d steps): %s\n%s\n\nScreen now:\n%s" % (
            id, len(done), "; ".join(done), check, tools.clip(screen, 7000))), {"routine": id, "done": True}
    wrap_phone()
