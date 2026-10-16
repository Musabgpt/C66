"""MusabAI on the phone: it never loops blind, and every stop says its real cause in the user's language.

* The phone, screen-aware: every action that changes the screen already comes back with the screen. When the screen
  is the same after 3 actions in a row, the result says so (the actions had no effect: try another way); after 6
  the turn stops and says why, instead of spending the rest of the step budget. The breaker counts a failing phone
  call per screen: the same call failing on a screen that changed is a new try, not a repeat.
* Stops in the user's language with the real cause: the step budget, the breaker, a tool that never answered, a
  provider that went silent, the task's time limit. English requests keep the engine's English wording.
"""
import hashlib
import re

from . import circuit, tools

SAME_NOTE, SAME_STOP = 3, 6
CHANGES = ("open_app", "open_url", "tap", "type", "swipe", "scroll", "key", "intent", "settings")
MARK = "\n\nScreen now:\n"
ARABIC = re.compile(r"[؀-ۿ]")
_VOLATILE = re.compile(r"\d+")


# ------------------------------------------------------------------ the phone: the screen decides

def fingerprint(screen):
    """The screen without what changes by itself (clock, counters, battery): two listings of the same screen match."""
    text = _VOLATILE.sub("#", str(screen or "")).strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] if text else ""


def _turn(agent):
    st = getattr(agent, "_phone_turn", None)
    return st if isinstance(st, dict) else {}


def change_mark(agent, name):
    """What makes the same call a new try: a file change; for the phone, also a different screen."""
    step = getattr(agent, "last_change_step", -1)
    if name != "phone":
        return step
    return "%s/%s" % (step, fingerprint(_turn(agent).get("screen")))


def _screen_in(text):
    text = str(text or "")
    i = text.rfind(MARK)
    return text[i + len(MARK):] if i >= 0 else None


def wrap_phone():
    t = tools.REGISTRY.get("phone")
    if t is None or getattr(t.fn, "_vital", False):
        return
    inner = t.fn

    def phone(ctx, action, **args):
        agent = getattr(ctx, "agent", None)
        before = fingerprint(_turn(agent).get("screen")) if agent is not None else ""
        out = inner(ctx, action, **args)
        if agent is None or str(action or "") not in CHANGES:
            return out
        text, meta = out if isinstance(out, tuple) else (out, {})
        st = _turn(agent)
        screen = _screen_in(text)
        if screen is None or not st:
            return out                                  # no screen came back: nothing to judge
        same = bool(before) and fingerprint(screen) == before
        st["same"] = st.get("same", 0) + 1 if same else 0
        n = st["same"]
        if n >= SAME_STOP:
            breaker = getattr(agent, "breaker", None)
            if breaker is not None and not breaker.open:
                breaker.state = circuit.OPEN
                breaker.reason = "the phone screen did not change after %d actions in a row" % n
            text += "\n(Stopping: the screen has not changed after your last %d actions; they had no effect.)" % n
        elif n >= SAME_NOTE:
            text += ("\n(Note: the screen has not changed after your last %d actions: they had no effect here. "
                     "Read the screen above and do something different: another item, scroll, back, or another "
                     "route such as open_url.)" % n)
        return (text, meta) if isinstance(out, tuple) else text
    phone._vital = True
    t.fn = phone


def install():
    wrap_phone()


# ------------------------------------------------------------------ stops, in the user's language

def _user_text(agent):
    for m in reversed(getattr(getattr(agent, "session", None), "messages", None) or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def arabic(agent):
    return bool(ARABIC.search(_user_text(agent)))


RESUME = " التقدم محفوظ؛ اكتب «كمّل» لتكمل من آخر خطوة."


def _breaker_ar(reason):
    m = re.match(r"^(.+?) failed (\d+) times the same way \((.*)\)$", reason or "", re.S)
    if m:
        return "توقفت: الأداة %s فشلت %s مرات بنفس الطريقة (%s)." % (m.group(1), m.group(2), m.group(3).strip())
    m = re.match(r"^the step budget \((\d+)\) is spent$", reason or "")
    if m:
        return "توقفت: انتهت خطوات هذا الدور (%s) قبل ما تخلص المهمة." % m.group(1)
    m = re.match(r"^the phone screen did not change after (\d+) actions in a row$", reason or "")
    if m:
        return "توقفت: شاشة الهاتف ما تغيّرت بعد %s أفعال متتالية، يعني الأفعال ما كان لها أثر." % m.group(1)
    return "توقفت: %s." % reason


def stop_text(agent, kind, detail=""):
    """The answer of a turn that stopped: why, in the user's language. English keeps the engine's own wording."""
    ar = arabic(agent)
    if kind == "budget":
        if ar:
            return "توقفت: انتهت خطوات هذا الدور (%s) قبل ما تخلص المهمة." % detail + RESUME
        return "Stopped after %s steps without finishing." % detail
    if kind == "breaker":
        return _breaker_ar(detail) + RESUME if ar else "Stopped: %s." % detail
    if kind == "timeout":
        if ar:
            return "توقفت: وصلت المهمة للحد الأقصى للوقت." + RESUME
        return "Stopped safely after the task timeout. Resume to continue from the saved checkpoint."
    if kind == "stall":
        event, tool = tuple(detail)[:2] if isinstance(detail, (tuple, list)) and len(detail) >= 2 else ("", "")
        if event == "tool_start" and tool:
            why = "الأداة %s ما رجّعت نتيجة لمدة طويلة، ثلاث مرات متتالية" % tool
            en = "the tool %s gave no result for a long time, three times in a row" % tool
        else:
            why = "المزود ما رد لمدة طويلة، ثلاث مرات متتالية"
            en = "the model provider did not answer for a long time, three times in a row"
        if ar:
            return "توقفت: " + why + "." + RESUME
        return "Stopped: %s. Progress is saved; send \"continue\" to resume." % en
    return str(detail or "")
