"""The task memory, for the agent: MusabAI's library of procedures (AndroidControl, Mind2Web, InSTA-150K and hand-written
ones for Facebook, WhatsApp, Instagram...), sorted into categories, that the Android app ships and searches
(TaskMemory.java). The engine reaches it through the app's phone server, so there is one library and one matcher.

* note(agent, request): when no learned routine fits a phone request, the procedure that does fit, with the values
  the request gives, goes under the request (phone_fast.note): "افتح فيسبوك وانشر منشور" comes with the Facebook post
  procedure and its steps.
* phone_routine runs a procedure whose id starts with "tm:" (the app carries the steps out, trying each label of a
  step and waiting for slow screens) and searches the memory (action=search).
Without the app (a desktop engine, Termux) there is no memory: everything here answers "" or an error, never a guess.
"""
import json

PREFIX = "tm:"
RUN_TIMEOUT = 240


def _phone():
    from . import phone
    return phone


def available():
    try:
        return bool(_phone().available())
    except Exception:  # noqa: BLE001 - no phone: no memory
        return False


def note(agent, request):
    """The task-memory note for the request ("" when nothing fits confidently, or there is no phone)."""
    request = str(request or "").strip()
    if not request or not available():
        return ""
    try:
        r = _phone().call("memory_match", text=request[:2000], timeout=25)
    except Exception:  # noqa: BLE001 - the memory is help, never a reason for a turn to fail
        return ""
    if not isinstance(r, dict) or not r.get("ok", True) or not r.get("confident"):
        return ""
    match = r.get("match") or {}
    try:
        agent._memory_match = match
    except Exception:  # noqa: BLE001
        pass
    return str(r.get("text") or "")


def _clip(text, n=7000):
    from . import tools
    return tools.clip(str(text or ""), n)


def run(ctx, ref, values=None):
    """(text, meta) of running procedure ref (tm:...) with the values; the screen at the end (or where it stopped)."""
    from . import tools
    phone = _phone()
    vals = [str(v) for v in (values or [])]
    if not vals:
        remembered = getattr(getattr(ctx, "agent", None), "_memory_match", None) or {}
        if remembered.get("id") == ref:
            vals = [str(v) for v in remembered.get("values") or []]
    try:
        r = phone.call("memory_run", id=ref, values=vals, timeout=RUN_TIMEOUT)
    except phone.PhoneError as e:
        raise tools.ToolError(str(e))
    done = bool(r.get("done"))
    screen = str(r.get("screen") or "")
    text = str(r.get("text") or "")
    try:
        from . import phone_fast
        st = phone_fast._state(ctx)
        st["routine"] = ref
        if screen:
            st["screen"] = phone_fast.clean_screen(screen)
    except Exception:  # noqa: BLE001 - a call outside a turn: nothing to remember
        pass
    head = "Task memory %s %s" % (ref, "done" if done else "did not finish")
    if r.get("stopped_at", -1) is not None and int(r.get("stopped_at", -1)) >= 0:
        tail = "Continue by hand from this screen."
    elif done:
        # a small model otherwise goes on looking for a Post button that was already pressed
        tail = ("Finished and checked: nothing is left to do on the phone for this request. Tell the user in one line, "
                "in the language of their request, what was done.")
    else:
        tail = "Check the screen and finish by hand before you say it is done."
    out = "%s: %s" % (head, text)
    if screen:
        out += "\n\nScreen now:\n" + _clip(screen)
    if tail:
        out += "\n" + tail
    return out, {"routine": ref, "done": done, "memory": True}


def search(query, limit=8):
    from . import tools
    phone = _phone()
    query = str(query or "").strip()
    if not query:
        raise tools.ToolError("search needs query: what the task is")
    try:
        r = phone.call("memory_search", text=query, limit=int(limit or 8), timeout=25)
    except phone.PhoneError as e:
        raise tools.ToolError(str(e))
    return ("Task memory for \"%s\" (run a runnable one with phone_routine id=tm:...; follow a guidance one by hand):\n%s"
            % (query, r.get("text") or "nothing")), {"memory": True}


def show(ref):
    from . import tools
    phone = _phone()
    try:
        r = phone.call("memory_get", id=ref, timeout=25)
    except phone.PhoneError as e:
        raise tools.ToolError(str(e))
    proc = r.get("procedure") or {}
    blanks = proc.get("blanks") or []
    extra = ("\nBlanks: " + ", ".join("{%d} = %s" % (i + 1, b) for i, b in enumerate(blanks))) if blanks else ""
    kind = "runnable with phone_routine" if proc.get("executable") else "guidance: do it by hand"
    return "%s\n(%s)%s" % (r.get("text") or json.dumps(proc, ensure_ascii=False), kind, extra), {"memory": True}
