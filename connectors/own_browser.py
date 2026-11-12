"""The assistant's own browser, inside the app.

The Android app keeps one WebView of its own behind the conversation (InAppBrowser.java, phone action "web"). It
opens no window, needs no Termux and no install: the browser tool drives it one step at a time, and the page
shows a small live picture of it in the conversation (shot). An app from before it existed answers "unknown
action": the tool then falls back to the Chromium in Termux as before.
"""
import base64

from . import tools

_STATE = {"supported": None}
PAGE_TEXT = 4500             # characters of a page's text given to the model at once


def available():
    """Whether this engine runs in an app that has its own browser."""
    from . import phone
    if not phone.available():
        return False
    if _STATE["supported"] is None:
        try:
            phone.call("web", timeout=10, op="state")
            _STATE["supported"] = True
        except phone.PhoneError as e:
            if "unknown" not in str(e).lower():
                return False                 # the app did not answer now: asked again next time
            _STATE["supported"] = False      # an older app
    return bool(_STATE["supported"])


def _call(timeout=60, **args):
    from . import phone
    try:
        return phone.call("web", timeout=timeout, **args)
    except phone.PhoneError as e:
        raise tools.ToolError(str(e))


def _target(target):
    """A target is the number read gave an element ("7", "[7]") or its name."""
    text = str(target or "").strip()
    digits = text.strip("[]# ")
    if digits.isdigit() and len(digits) <= 3:
        return {"index": int(digits), "text": ""}
    return {"index": 0, "text": text}


def _head(out):
    return "%s — %s" % (out.get("title") or "(no title)", out.get("url") or "")


def _listing(page):
    """The page as the model reads it: its text, then what can be used, numbered."""
    lines = [str(page.get("text") or "").strip()[:PAGE_TEXT]]
    items = page.get("items") or []
    if items:
        lines += ["", "On the page (click or type by number, or by name):"]
        for it in items:
            line = "  [%s] %s: %s" % (it.get("i"), it.get("kind"), it.get("name") or "(no label)")
            if it.get("value"):
                line += " = %s" % it["value"]
            lines.append(line)
    height, view, y = page.get("height") or 0, page.get("view") or 0, page.get("y") or 0
    if view and height > y + view + 40:
        lines += ["", "(More below: op=scroll, then extract again.)"]
    return "\n".join(lines)


def run(op, url="", target="", text="", submit=False, key=""):
    """One step of the browser tool. Returns (text for the model, meta with url and title)."""
    op = str(op or "").strip().lower()
    if op == "open":
        if not str(url or "").strip():
            raise tools.ToolError("open needs a url")
        out = _call(op="open", url=str(url).strip())
        page = _call(op="read").get("page") or {}
        body = _head(out) + "\n\n" + _listing(page)
    elif op in ("extract", "read"):
        out = _call(op="read")
        body = _head(out) + "\n\n" + _listing(out.get("page") or {})
    elif op == "click":
        if not str(target or "").strip():
            raise tools.ToolError("click needs a target: an element's number or name from extract")
        out = _call(op="click", **_target(target))
        body = "Clicked %s. %s\n%s" % (out.get("element") or target,
                                       "The page changed:" if out.get("moved") else "Still on:", _head(out))
    elif op == "type":
        if not str(target or "").strip():
            raise tools.ToolError("type needs a target: a field's number or name from extract")
        out = _call(op="type", value=str(text or ""), submit=bool(submit), **_target(target))
        body = "Typed. The field now holds: %s\n%s" % (out.get("value", ""), _head(out))
        if submit:
            body += "\n(Sent with Enter: extract to read what came back.)"
    elif op == "press":
        if str(key or "Enter").strip().lower() not in ("enter", "return"):
            raise tools.ToolError("this browser presses Enter only: click or type for anything else")
        out = _call(op="key")
        body = "Pressed Enter.\n" + _head(out)
    elif op == "back":
        out = _call(op="back")
        body = "Went back.\n" + _head(out)
    elif op == "scroll":
        out = _call(op="scroll", pages=1)
        body = "Scrolled down one screen: extract to read it.\n" + _head(out)
    else:
        raise tools.ToolError("unknown browser op %r: open, extract, click, type, press, scroll or back" % op)
    if out.get("note"):
        body += "\nNote: " + str(out["note"])
    return body, {"url": out.get("url") or "", "title": out.get("title") or "", "own_browser": True}


def shot(width=360):
    """The browser's screen now as JPEG bytes, with its address and title: (bytes or b"", state)."""
    from . import phone
    try:
        out = phone.call("web", timeout=15, op="shot", width=int(width))
    except phone.PhoneError:
        return b"", {}
    try:
        picture = base64.b64decode(out.get("jpeg") or "")
    except ValueError:
        picture = b""
    return picture, {"url": out.get("url") or "", "title": out.get("title") or "", "loading": bool(out.get("loading"))}
