"""The personal assistant: what it knows about its user, what it asks before doing, and the page's small API.

* my_info: the user's own details (name, email, phone, city, passport...), kept in one file on the phone. The
  model reads them when a form or a booking asks, and saves a new one when the user gives it. Their values are
  not part of every request: only the names of what is saved are, so a detail leaves the phone when it is needed.
* Confirm before sending: with it on (the default), a call that sends or publishes words the user has not seen
  (a reply from a notification, an email, a post typed by a task-memory procedure) shows an approval first,
  whatever the permission mode. Their own words, and a draft they already answered, go out without a second
  question.
* /api/assistant: the settings sheet of the page (profile, confirmation, what the phone allows).
"""
import json
import os
import re
import threading

from . import settings, tools

PATH = os.path.join(settings.HOME, "assistant.json")
# What the settings sheet shows, in order. Any other short name can be saved too (frequent flyer number...).
FIELDS = ("name", "email", "phone", "city", "country", "address", "birth_date", "nationality", "passport")
MAX_FIELDS = 60
MAX_VALUE = 500
# Sent in the user's name with no tap of theirs: asked first while "confirm" is on.
SENDS = ("Phone(notification_reply)", "gmail_send")

_lock = threading.Lock()
_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _read():
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    profile = data.get("profile")
    data["profile"] = {str(k): str(v) for k, v in profile.items()} if isinstance(profile, dict) else {}
    data["confirm"] = data.get("confirm", True) is not False
    return data


def _write(data):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"profile": data["profile"], "confirm": data["confirm"]}, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, PATH)


def active():
    """This engine is the personal assistant (the Android app's own engine, see lean.assistant)."""
    try:
        from . import lean
        return lean.assistant()
    except Exception:  # noqa: BLE001
        return False


def field_name(name):
    """A detail's name as it is stored: "Birth date" and "birth-date" are birth_date."""
    key = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    if not _FIELD.match(key):
        raise tools.ToolError("a detail's name is a short word in English letters, like passport or home_address")
    return key


def profile():
    return dict(_read()["profile"])


def confirm():
    return _read()["confirm"]


def remember(name, value):
    key, value = field_name(name), " ".join(str(value or "").split())
    if not value:
        raise tools.ToolError("the value to save is empty")
    if len(value) > MAX_VALUE:
        raise tools.ToolError("too long to save as one detail (%d characters at most)" % MAX_VALUE)
    with _lock:
        data = _read()
        if key not in data["profile"] and len(data["profile"]) >= MAX_FIELDS:
            raise tools.ToolError("too many saved details; forget one first")
        data["profile"][key] = value
        _write(data)
    return key


def forget(name):
    key = field_name(name)
    with _lock:
        data = _read()
        if key not in data["profile"]:
            return False
        del data["profile"][key]
        _write(data)
    return True


def update(body):
    """The settings sheet saves: {"profile": {...}} replaces the listed details (an empty value forgets one),
    {"confirm": bool} switches the confirmation."""
    body = body if isinstance(body, dict) else {}
    with _lock:
        data = _read()
        changes = body.get("profile")
        if isinstance(changes, dict):
            for name, value in changes.items():
                key, value = field_name(name), " ".join(str(value or "").split())[:MAX_VALUE]
                if value:
                    if key not in data["profile"] and len(data["profile"]) >= MAX_FIELDS:
                        raise tools.ToolError("too many saved details; forget one first")
                    data["profile"][key] = value
                else:
                    data["profile"].pop(key, None)
        if "confirm" in body:
            data["confirm"] = bool(body["confirm"])
        _write(data)
    return state()


QUOTES = str.maketrans("", "", "\"'«»“”‘’`*_")
ROUTINE_TEXT = 20        # a routine's value this long is content (a post, a message), not a name or a search word


def _norm(text):
    return " ".join(str(text or "").translate(QUOTES).lower().split())


def _content(tool, args):
    """(the permission rule of this call, the texts it would send or publish in the user's name)."""
    args = args or {}
    if tool == "phone" and args.get("action") == "notification_reply":
        return "Phone(notification_reply)", [args.get("text")]
    if tool == "gmail_send":
        return "gmail_send", [args.get("text")]
    if tool == "phone_routine" and str(args.get("action") or "run") != "search":
        values = args.get("values")
        values = values if isinstance(values, (list, tuple)) else [values] if values else []
        return "phone_routine", [v for v in values if len(_norm(v)) >= ROUTINE_TEXT]
    return "", []


def _shown(agent):
    """What has been in front of the user in this conversation: their own messages, and what the assistant said
    before their latest one (they answered after reading it). What the assistant wrote in this turn is not: the
    user has not had the chance to say no to it."""
    events = list(getattr(getattr(agent, "session", None), "events", None) or [])
    last = max((i for i, e in enumerate(events) if e.get("type") == "turn_start" and not e.get("sub")), default=-1)
    parts = []
    for i, e in enumerate(events):
        if e.get("type") == "turn_start" and not e.get("sub"):
            parts.append(str(e.get("text") or ""))
        elif e.get("type") == "assistant" and i < last:
            parts.append(str(e.get("text") or ""))
    return _norm("\n".join(parts))


def rules(current, agent=None, tool="", args=None):
    """The permission rules of a call, with the assistant's own. While "confirm" is on, a call that sends or
    publishes words the user has not seen (a reply from a notification, an email, a post or message typed by a
    task-memory procedure) is asked first, whatever the permission mode: ask rules are read before the mode.
    Words the user wrote themselves, or that the assistant showed them before their latest message, go out
    without a second question. This does not rest on the model remembering to ask."""
    out = dict(current or {})
    if not (active() and confirm()):
        return out
    if agent is None:                      # no conversation to look at: what sends always asks
        wanted = list(SENDS)
    else:
        rule, texts = _content(tool, args)
        shown = _shown(agent) if texts else ""
        wanted = [rule] if texts and not all(_norm(t) and _norm(t) in shown for t in texts) else []
    out["ask"] = list(out.get("ask") or []) + [r for r in wanted if r not in (out.get("ask") or [])]
    return out


def context():
    """For the end of the system message: whether sending is confirmed, the user's name and city (so answers and
    searches fit them) and the names of the other saved details, never their values."""
    data = _read()
    saved = data["profile"]
    out = {"confirm_before_sending": data["confirm"], "saved_details": sorted(saved)}
    for key in ("name", "city", "country"):
        if saved.get(key):
            out["user_" + key] = saved[key]
    return out


def _node():
    """What Android allows the app, read from the phone's own status line: {"a11y": bool, "notifications": bool...}."""
    try:
        from . import phone
        if not phone.available():
            return {}
        text = str(phone.call("device", timeout=8).get("text") or "")
    except Exception:  # noqa: BLE001 - the sheet still opens when the phone part does not answer
        return {}
    found = re.search(r"node\(([^)]*)\)", text)
    out = {}
    for part in (found.group(1).split(",") if found else []):
        name, _, value = part.strip().partition("=")
        if name:
            out[name] = value.strip() == "on"
    return out


def state():
    data = _read()
    return {"ok": True, "profile": data["profile"], "confirm": data["confirm"], "fields": list(FIELDS),
            "node": _node()}


PAGES = ("accessibility", "notification_access", "notifications", "permissions")


def route(handler, method, path, body=None):
    """The page's API. True when the request was answered."""
    if not path.startswith("/api/assistant"):
        return False
    try:
        if method == "GET" and path == "/api/assistant":
            handler._json(state())
        elif method == "POST" and path == "/api/assistant":
            handler._json(update(body))
        elif method == "POST" and path == "/api/assistant/open":
            page = str((body or {}).get("page") or "")
            if page not in PAGES:
                raise tools.ToolError("unknown settings page")
            from . import phone
            handler._json(phone.call("settings", page=page))
        else:
            handler._json({"ok": False, "error": "unknown assistant request"}, 404)
    except tools.ToolError as e:
        handler._json({"ok": False, "error": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        handler._json({"ok": False, "error": str(e) or type(e).__name__}, 400)
    return True


@tools.tool("my_info",
            "The user's own saved details for forms, bookings and messages: name, email, phone, city, country, "
            "address, birth_date, nationality, passport and anything else they asked to keep. action=get returns "
            "them all; action=set saves one (field, value) when the user states it; action=forget removes one. "
            "Never guess a detail that is not saved: ask the user, then save it.",
            {"action": tools._s("get, set or forget"),
             "field": tools._s("the detail's name for set/forget, e.g. passport"),
             "value": tools._s("the value for set")},
            ["action"], "meta")
def t_my_info(ctx, action, field="", value=""):
    action = str(action or "").strip().lower()
    if action == "get":
        saved = profile()
        if not saved:
            return "Nothing is saved yet. Ask the user for what the form needs, then save it with action=set.", {}
        return "\n".join("%s: %s" % (k, saved[k]) for k in sorted(saved)), {"fields": sorted(saved)}
    if action == "set":
        key = remember(field, value)
        return "Saved %s." % key, {"field": key}
    if action == "forget":
        key = field_name(field)
        return ("Forgot %s." % key) if forget(key) else ("Nothing was saved as %s." % key), {"field": key}
    raise tools.ToolError("action is get, set or forget")


NAMES = ["my_info"]
