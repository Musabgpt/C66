"""The assistant's own browser, inside the app.

The Android app keeps one WebView of its own on a screen nobody sees (InAppBrowser.java, phone action "web"). It
opens no window, needs no Termux and no install, and it works the same with the app open, in the background or
closed: the browser tool drives it one step at a time, and the page shows a small live picture of it in the
conversation (shot). An app from before it existed answers "unknown action": the tool then falls back to the
Chromium in Termux as before.

Every step answers with the page as it is after the step (its text, then what can be used, numbered), so the
model does not spend a call on reading it again. Clicks are real touches, text goes in the way a keyboard puts
it, a cookie notice is accepted when a page opens, and several fields are filled in one call.
"""
import base64
import json
import os
import re

from . import tools

_STATE = {"supported": None}
PAGE_TEXT = 4500             # characters of a page's text given to the model at once
BRIEF_TEXT = 1200            # after a step that did not change the page
OPS = "open, extract, click, type, fill, press, scroll, find, wait, dismiss, upload or back"


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


def _item(it):
    kind = str(it.get("kind") or "")
    notes = [str(it[k]) for k in ("type",) if it.get(k)]
    if it.get("required"):
        notes.append("required")
    if it.get("disabled"):
        notes.append("disabled")
    line = "  [%s] %s%s: %s" % (it.get("i"), kind, " (%s)" % ", ".join(notes) if notes else "", it.get("name") or "(no label)")
    value = it.get("value")
    if value and kind in ("checkbox", "radio"):
        line += " (%s)" % value
    elif value:
        line += " = %s" % value
    if it.get("options"):
        more = " …+%d" % it["more_options"] if it.get("more_options") else ""
        line += "  {%s%s}" % (" | ".join(str(o) for o in it["options"]), more)
    return line


def _listing(page, text_max=PAGE_TEXT):
    """The page as the model reads it: its text, then what can be used, numbered."""
    lines = []
    if page.get("dialog"):
        lines += ["A dialog is open: %s. Its things are listed first; use them, or op=dismiss to close it." % page["dialog"],
                  str(page.get("dialog_text") or "").strip()[:800], ""]
    lines.append(str(page.get("text") or "").strip()[:text_max])
    total, start = page.get("text_total") or 0, page.get("text_from") or 0
    if total:
        lines.append("(Text: characters %d to %d of %d. op=scroll for the next part, op=find text=... to jump.)"
                     % (start, min(total, start + text_max), total))
    items = page.get("items") or []
    if items:
        lines += ["", "On the page (click or type by number, or by name):"]
        lines += [_item(it) for it in items]
    if page.get("more_items"):
        lines.append("  (%d more links are not listed: scroll, or find the one you need by its name.)" % page["more_items"])
    height, view, y = page.get("height") or 0, page.get("view") or 0, page.get("y") or 0
    if view and height > y + view + 40 and not total:
        lines += ["", "(More below: op=scroll.)"]
    return "\n".join(lines)


def _page(text_max=PAGE_TEXT):
    out = _call(op="read")
    return _head(out) + "\n\n" + _listing(out.get("page") or {}, text_max), out


def _notes(out):
    lines = []
    if out.get("accepted"):
        lines.append("(The cookie notice was accepted: %s.)" % out["accepted"])
    if out.get("covered"):
        lines.append("(Something covers that place: %s. If nothing happened, op=dismiss, then try again.)" % out["covered"])
    if out.get("note"):
        lines.append("Note: " + str(out["note"]))
    return lines


def fields_of(value):
    """The fields of op=fill, however the model wrote them: {"name": "value"}, [{"target", "value"}], or text lines."""
    if isinstance(value, str):
        text = value.strip()
        try:
            value = json.loads(text) if text[:1] in "{[" else text
        except ValueError:
            value = text
    pairs = []
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, (list, tuple)):
        for one in value:
            if isinstance(one, dict):
                name = one.get("target") or one.get("name") or one.get("field") or one.get("label")
                if name is None and len(one) == 1:
                    name, text = next(iter(one.items()))
                else:
                    text = one.get("value", one.get("text", ""))
                pairs.append((name, text))
            elif isinstance(one, (list, tuple)) and len(one) == 2:
                pairs.append((one[0], one[1]))
    elif isinstance(value, str):
        for line in re.split(r"[\n;]+", value):
            name, sep, text = line.partition("=")
            if not sep:
                name, sep, text = line.partition(":")
            if sep and name.strip():
                pairs.append((name.strip(), text.strip()))
    out = []
    for name, text in pairs:
        if name is None or str(name).strip() == "":
            continue
        if isinstance(text, bool):
            text = "yes" if text else "no"
        out.append({"target": str(name).strip(), "value": "" if text is None else str(text)})
    return out


def _file(ctx, path):
    """A file the browser may give to a page: one of this conversation's own files."""
    raw = str(path or "").strip()
    if not raw:
        raise tools.ToolError("upload needs path: a file made or received in this conversation")
    try:
        full = tools.resolve(ctx, raw)
    except tools.ToolError:
        raise
    if not tools.inside(ctx, full) or not os.path.isfile(full):
        raise tools.ToolError("upload takes a file of this conversation (made with file_create, or shared by the user): %s is not one" % raw)
    return full


def run(op, url="", target="", text="", submit=False, key="", fields=None, seconds=0, path="", ctx=None):
    """One step of the browser tool. Returns (text for the model, meta with url and title)."""
    op = str(op or "").strip().lower()
    out, body = None, ""
    if op == "open":
        if not str(url or "").strip():
            raise tools.ToolError("open needs a url")
        out = _call(op="open", url=str(url).strip())
        listing, _ = _page()
        body = "\n".join(_notes(out) + [listing])
        out = dict(out, note="", accepted="", covered="")
    elif op in ("extract", "read"):
        body, out = _page()
    elif op == "click":
        if not str(target or "").strip():
            raise tools.ToolError("click needs a target: an element's number or name from the page")
        out = _call(op="click", **_target(target))
        listing, _ = _page(PAGE_TEXT if out.get("moved") else BRIEF_TEXT)
        body = "Clicked %s. %s\n%s" % (out.get("element") or target,
                                       "The page changed:" if out.get("moved") else "The page did not go elsewhere; it is now:",
                                       listing)
    elif op == "type":
        if not str(target or "").strip():
            raise tools.ToolError("type needs a target: a field's number or name from the page")
        out = _call(op="type", value=str(text or ""), submit=bool(submit), **_target(target))
        body = "Typed into %s. It now holds: %s" % (out.get("element") or target, out.get("value", ""))
        if submit:
            listing, _ = _page(PAGE_TEXT if out.get("moved") else BRIEF_TEXT)
            body += "\nSent with Enter. %s\n%s" % ("The page changed:" if out.get("moved") else "The page is now:", listing)
    elif op == "fill":
        wanted = fields_of(fields)
        if not wanted:
            raise tools.ToolError('fill needs fields: {"the field\'s name or number": "its value", ...}')
        out = _call(timeout=120, op="fill", fields=wanted)
        lines = []
        for f in out.get("fields") or []:
            if f.get("ok"):
                lines.append("  %s = %s" % (f.get("name") or f.get("target"), f.get("value", "")))
            else:
                lines.append("  %s: NOT filled (%s)" % (f.get("target"), f.get("error") or "no reason given"))
        failed = out.get("failed") or 0
        body = "Filled %d of %d fields:\n%s" % (len(lines) - failed, len(lines), "\n".join(lines))
        if str(target or "").strip() and not failed:
            sent = _call(op="click", **_target(target))
            listing, _ = _page(PAGE_TEXT if sent.get("moved") else BRIEF_TEXT)
            body += "\nThen clicked %s. %s\n%s" % (sent.get("element") or target,
                                                   "The page changed:" if sent.get("moved") else "The page is now:", listing)
            out = dict(sent, fields=out.get("fields"), failed=failed)
        elif str(target or "").strip():
            body += "\nNot sent: fill what is missing first, then click %s." % target
    elif op == "press":
        which = str(key or "Enter").strip() or "Enter"
        names = {"enter": "Enter", "return": "Enter", "escape": "Escape", "esc": "Escape", "tab": "Tab", "down": "ArrowDown",
                 "up": "ArrowUp", "left": "ArrowLeft", "right": "ArrowRight", "arrowdown": "ArrowDown", "arrowup": "ArrowUp",
                 "arrowleft": "ArrowLeft", "arrowright": "ArrowRight", "backspace": "Backspace", "space": " "}
        if which.lower() not in names:
            raise tools.ToolError("press takes: Enter, Escape, Tab, ArrowDown, ArrowUp, ArrowLeft, ArrowRight, Backspace, Space")
        out = _call(op="key", key=names[which.lower()])
        listing, _ = _page(PAGE_TEXT if out.get("moved") else BRIEF_TEXT)
        body = "Pressed %s. %s\n%s" % (which, "The page changed:" if out.get("moved") else "The page is now:", listing)
    elif op == "back":
        out = _call(op="back")
        listing, _ = _page()
        body = "Went back.\n" + listing
    elif op == "scroll":
        where = str(text or target or "down").strip().lower()
        out = _call(op="scroll", to=where if where in ("up", "down", "top", "bottom") else "down")
        listing, _ = _page()
        body = "Scrolled %s.\n%s" % (where if where in ("up", "top", "bottom") else "down one screen", listing)
    elif op == "find":
        wanted = str(text or target or "").strip()
        if not wanted:
            raise tools.ToolError("find needs text: the words to look for on the page")
        out = _call(op="find", text=wanted)
        places = out.get("places") or []
        if not places:
            body = "%r is not on this page (%s). Scroll for content that loads later, or look on another page." % (wanted, _head(out))
        else:
            body = "%r is on the page %d time(s). The page was scrolled to the first.\n\n%s" % (
                wanted, out.get("count") or len(places), "\n---\n".join(places))
    elif op == "wait":
        out = _call(timeout=40, op="wait", text=str(text or ""), seconds=max(1, min(int(seconds or 8), 20)))
        listing, _ = _page()
        body = ("%s\n%s" % ("It is there now." if out.get("there") else
                            "It did not come in that time; the page is:", listing))
    elif op == "dismiss":
        out = _call(op="dismiss")
        listing, _ = _page(BRIEF_TEXT)
        did = str(out.get("did") or "done")
        body = "%s%s.\n%s" % (did[:1].upper(), did[1:], listing)
    elif op == "upload":
        if not str(target or "").strip():
            raise tools.ToolError("upload needs a target: the file field's number or name")
        out = _call(op="upload", path=_file(ctx, path), **_target(target))
        body = "Gave the file %s to %s.\n%s" % (out.get("value") or os.path.basename(str(path)), out.get("element") or target, _head(out))
    else:
        raise tools.ToolError("unknown browser op %r: %s" % (op, OPS))
    extra = _notes(out) if op != "open" else []
    if extra:
        body += "\n" + "\n".join(extra)
    return body, {"url": out.get("url") or "", "title": out.get("title") or "", "own_browser": True}


def where():
    """Where the browser is now, without a picture: {"url", "title", "loading"}; {} when it does not answer."""
    from . import phone
    try:
        out = phone.call("web", timeout=10, op="state")
    except phone.PhoneError:
        return {}
    return {"url": out.get("url") or "", "title": out.get("title") or "", "loading": bool(out.get("loading"))}


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
