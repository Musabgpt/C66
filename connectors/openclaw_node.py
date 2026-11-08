"""OpenClaw Node capabilities for MusabAI on Android (derived from REA analysis of ai.openclaw.app v2026.10.70).

Adds first-class on-device node actions to the `phone` tool:
* notification_reply: reply directly to a replyable notification (WhatsApp, Telegram, Messages) via Android RemoteInput
  in the background without opening the app or touching the screen.
* notification_action / notification_dismiss: trigger a notification action button or dismiss a notification.
* contacts / contact_add: search device contacts (with Arabic/Latin folded matching) or open the contact editor.
* calendar / calendar_add: read upcoming calendar agenda events or schedule a new event.
* location: read the phone's current GPS/network location and Google Maps link.
* media: control active media playback (play_pause, play, pause, next, previous, stop, mute).
* sensors: read hardware sensors (step counter, accelerometer, light, pressure, proximity) and battery state.
"""
from . import phone_fast, tools

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

READ_ONLY_NODE_ACTIONS = ("contacts", "calendar", "location", "sensors")

ACTION_ALIASES = {
    "reply_notification": "notification_reply",
    "notification_send": "notification_reply",
    "notifications_reply": "notification_reply",
    "dismiss_notification": "notification_dismiss",
    "notifications_dismiss": "notification_dismiss",
    "clear_notification": "notification_dismiss",
    "trigger_notification": "notification_action",
    "search_contacts": "contacts",
    "find_contact": "contacts",
    "list_contacts": "contacts",
    "get_contacts": "contacts",
    "read_contacts": "contacts",
    "add_contact": "contact_add",
    "create_contact": "contact_add",
    "read_calendar": "calendar",
    "get_calendar": "calendar",
    "list_events": "calendar",
    "calendar_events": "calendar",
    "add_event": "calendar_add",
    "create_event": "calendar_add",
    "event_create": "calendar_add",
    "schedule_event": "calendar_add",
    "get_location": "location",
    "read_location": "location",
    "current_location": "location",
    "gps": "location",
    "media_control": "media",
    "play_pause": "media",
    "read_sensors": "sensors",
    "get_sensors": "sensors",
    "motion": "sensors",
    "steps": "sensors",
}

EXTRA_DOC = (
    " Also supports direct node actions without screen navigation: "
    "notification_reply (target/package/name, text: reply to a notification in the background via RemoteInput), "
    "notification_action (target, button/item), notification_dismiss (target or all=true), "
    "contacts (query/name: search contacts), contact_add (name, number, email), "
    "calendar (query, days: read upcoming events), calendar_add (title, in_minutes, duration_minutes, location), "
    "location (current GPS/network coordinates and Maps URL), "
    "media (command: play_pause, play, pause, next, previous, stop, mute), "
    "and sensors (steps, accelerometer, light, pressure, proximity, battery)."
)


def install():
    # 1. Register new actions in phone.ACTIONS if present
    try:
        from . import phone
        if hasattr(phone, "ACTIONS"):
            if isinstance(phone.ACTIONS, set):
                phone.ACTIONS.update(NODE_ACTIONS)
            elif isinstance(phone.ACTIONS, tuple):
                phone.ACTIONS = tuple(dict.fromkeys(list(phone.ACTIONS) + list(NODE_ACTIONS)))
            elif isinstance(phone.ACTIONS, list):
                for a in NODE_ACTIONS:
                    if a not in phone.ACTIONS:
                        phone.ACTIONS.append(a)
    except Exception:  # noqa: BLE001
        pass

    # 2. Register aliases in repair.PHONE_ACTIONS and repair.PHONE_ARGS if present
    try:
        from . import repair
        if hasattr(repair, "PHONE_ACTIONS") and isinstance(repair.PHONE_ACTIONS, dict):
            repair.PHONE_ACTIONS.update(ACTION_ALIASES)
        if hasattr(repair, "PHONE_ARGS") and isinstance(repair.PHONE_ARGS, dict):
            repair.PHONE_ARGS.setdefault("target", ("notification", "sender", "pkg"))
            repair.PHONE_ARGS.setdefault("query", ("search", "filter", "contact", "keyword"))
            repair.PHONE_ARGS.setdefault("title", ("event", "summary", "subject"))
            repair.PHONE_ARGS.setdefault("number", ("phone", "phone_number", "tel", "to", "recipient"))
            repair.PHONE_ARGS.setdefault("command", ("media_command", "control", "cmd"))
        if hasattr(repair, "NUMERIC") and isinstance(repair.NUMERIC, set):
            repair.NUMERIC.update({"days", "in_minutes", "duration_minutes", "minutes", "index"})
    except Exception:  # noqa: BLE001
        pass

    # 3. Update phone_fast.SKIP and phone_fast.DOC
    for act in READ_ONLY_NODE_ACTIONS:
        if act not in phone_fast.SKIP:
            phone_fast.SKIP = tuple(list(phone_fast.SKIP) + [act])
    if "notification_reply" not in phone_fast.DOC:
        phone_fast.DOC = phone_fast.DOC + EXTRA_DOC

    # 4. Update tools.REGISTRY["phone"] description and schema parameters (supports both flat tool.params and nested properties)
    t = tools.REGISTRY.get("phone")
    if t is not None:
        if "notification_reply" not in (t.description or ""):
            t.description = phone_fast.DOC
        params = getattr(t, "params", None) or getattr(t, "parameters", None)
        if isinstance(params, dict):
            props = params.get("properties") if isinstance(params.get("properties"), dict) else params
            act_prop = props.get("action")
            if isinstance(act_prop, dict):
                if isinstance(act_prop.get("enum"), list):
                    for a in NODE_ACTIONS:
                        if a not in act_prop["enum"]:
                            act_prop["enum"].append(a)
                desc = str(act_prop.get("description") or "")
                if desc and "notification_reply" not in desc:
                    act_prop["description"] = desc + " | " + ", ".join(NODE_ACTIONS)
            props.setdefault("query", tools._s("search query for contacts, calendar, or notifications_read"))
            props.setdefault("target", tools._s("target app/package/title for notification_reply, action, or dismiss"))
            props.setdefault("button", tools._s("notification action button label for notification_action"))
            props.setdefault("command", tools._s("media command: play_pause, play, pause, next, previous, stop, mute"))
            props.setdefault("days", tools._i("number of days ahead for calendar (default 7)"))
            props.setdefault("in_minutes", tools._i("minutes from now for calendar_add start (default 60)"))
            props.setdefault("duration_minutes", tools._i("event duration in minutes for calendar_add (default 60)"))
            props.setdefault("location", tools._s("event location for calendar_add"))
            props.setdefault("email", tools._s("contact email for contact_add"))
            props.setdefault("company", tools._s("contact company for contact_add"))
            props.setdefault("all", {"type": "boolean", "description": "dismiss all notifications or list all screen nodes"})
