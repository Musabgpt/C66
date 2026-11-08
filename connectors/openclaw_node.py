"""OpenClaw Node capabilities for MusabAI on Android.

Upgrades MusabAI's `phone` tool with fast, permission-safe background node actions inspired by OpenClaw Node
(ai.openclaw.app):
- `notification_reply`: reply directly to WhatsApp, Telegram, Messages, or any notification with a RemoteInput
  action in the background — without opening the app or navigating the UI.
- `notification_action`: trigger a secondary button on an active notification by label.
- `notification_dismiss`: dismiss a specific notification by sender/title/app or `"all"`.
- `contacts` / `contact_add`: search saved contacts with Arabic/English text folding (`TextFold`), or open the
  pre-filled contact insert sheet. `sms` and `call` also resolve contact names automatically.
- `calendar` / `calendar_add`: read upcoming agenda events from `CalendarContract.Instances` or add a calendar event.
- `location`: read last-known device GPS/network coordinates with a ready-to-open Google Maps link.
- `media`: control active media playback (`play_pause`, `play`, `pause`, `next`, `previous`, `stop`).
- `sensors`: read step counter and device environment/motion sensors (`SensorManager`).
"""

NODE_LOOK = (
    "notifications_read",
    "contacts",
    "calendar",
    "location",
    "sensors",
)

NODE_ACTIONS = (
    "notification_reply",
    "notification_action",
    "notification_dismiss",
    "contacts",
    "contact_add",
    "calendar",
    "calendar_add",
    "location",
    "media",
    "sensors",
)

# Kept compact (type-only) so json.dumps(REGISTRY["phone"].schema()) stays well under the 2000-byte phone budget.
NODE_PARAMS = {
    "query": {"type": "string"},
    "target": {"type": "string"},
    "button": {"type": "string"},
    "command": {"type": "string"},
    "days": {"type": "integer"},
    "limit": {"type": "integer"},
    "minutes": {"type": "integer"},
    "start_ms": {"type": "integer"},
    "end_ms": {"type": "integer"},
    "location": {"type": "string"},
    "description": {"type": "string"},
    "email": {"type": "string"},
}

COMPACT_DOC = (
    "Use the Android phone locally: screen, screenshot, tap, type, swipe, open_app, open_url, install_apk, "
    "notifications_read, notification_reply, contacts, calendar, location, media, sensors, crash_reports. "
    "Not for files or code: write files with write, run programs with bash."
)

# Common hallucinations when models invoke OpenClaw-style node actions by dotted names or aliases.
ACTION_ALIASES = {
    "notifications.list": "notifications_read",
    "notifications_list": "notifications_read",
    "notification_list": "notifications_read",
    "notifications.reply": "notification_reply",
    "reply_notification": "notification_reply",
    "notifications.actions": "notification_action",
    "notifications.dismiss": "notification_dismiss",
    "contacts.search": "contacts",
    "contacts_search": "contacts",
    "contact_search": "contacts",
    "contacts.list": "contacts",
    "contacts.add": "contact_add",
    "calendar.events": "calendar",
    "calendar_events": "calendar",
    "calendar.list": "calendar",
    "calendar.add": "calendar_add",
    "location.get": "location",
    "get_location": "location",
    "media.control": "media",
    "media_control": "media",
    "motion.pedometer": "sensors",
    "motion.activity": "sensors",
    "pedometer": "sensors",
}


def _normalize_node_call(action, args):
    act = ACTION_ALIASES.get(str(action or "").strip().lower(), str(action or "").strip())
    out = dict(args or {})
    if act in ("notification_reply", "notification_action", "notification_dismiss"):
        if "target" not in out:
            for k in ("query", "name", "sender", "contact", "app", "package", "key"):
                if out.get(k):
                    out["target"] = str(out.pop(k))
                    break
        if act == "notification_reply" and "text" not in out:
            for k in ("message", "reply", "body"):
                if out.get(k):
                    out["text"] = str(out.pop(k))
                    break
    elif act == "contacts":
        if "query" not in out:
            for k in ("name", "target", "text"):
                if out.get(k):
                    out["query"] = str(out.get(k))
                    break
    elif act in ("sms", "call"):
        if "number" not in out:
            for k in ("to", "contact", "name", "target", "query"):
                if out.get(k):
                    out["number"] = str(out.get(k))
                    break
    elif act == "media":
        if "command" not in out:
            for k in ("button", "name", "text"):
                if out.get(k):
                    out["command"] = str(out.get(k))
                    break
    return act, out


def install():
    from . import phone, phone_fast, repair, tools

    phone.LOOK = tuple(dict.fromkeys(tuple(phone.LOOK) + NODE_LOOK))
    phone.ACTIONS = tuple(dict.fromkeys(tuple(phone.ACTIONS) + NODE_ACTIONS))
    phone.DOC = COMPACT_DOC
    for k, spec in NODE_PARAMS.items():
        phone.PARAMS.setdefault(k, spec)

    phone_fast.SKIP = tuple(dict.fromkeys(tuple(phone_fast.SKIP) + NODE_LOOK + NODE_ACTIONS))

    entry = tools.REGISTRY.get("phone")
    if entry is not None:
        entry.description = phone.DOC
        if "action" in entry.params and "enum" in entry.params["action"]:
            entry.params["action"]["enum"] = list(phone.ACTIONS)
        for k, spec in NODE_PARAMS.items():
            entry.params.setdefault(k, spec)
        if not getattr(entry.fn, "_openclaw_node", False):
            prev_fn = entry.fn

            def wrapped_phone(ctx, action, **args):
                act, norm_args = _normalize_node_call(action, args)
                return prev_fn(ctx, act, **norm_args)

            wrapped_phone._openclaw_node = True
            wrapped_phone._vital = getattr(prev_fn, "_vital", False)
            wrapped_phone._fast = getattr(prev_fn, "_fast", False)
            entry.fn = wrapped_phone

    if not getattr(repair.normalize, "_openclaw_node", False):
        prev_norm = repair.normalize

        def normalize(name, args, schemas=()):
            name, args = prev_norm(name, args, schemas)
            low = str(name or "").strip().lower()
            if low in ACTION_ALIASES and isinstance(args, dict):
                act, norm_args = _normalize_node_call(ACTION_ALIASES[low], args)
                norm_args["action"] = act
                return "phone", norm_args
            if name == "phone" and isinstance(args, dict) and args.get("action"):
                act, norm_args = _normalize_node_call(args.get("action"), args)
                norm_args["action"] = act
                return "phone", norm_args
            return name, args

        normalize._openclaw_node = True
        repair.normalize = normalize
