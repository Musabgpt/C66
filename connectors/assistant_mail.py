"""Email the assistant sends is a whole, well-formed message, so it is read as a person's mail and not as spam.

The engine's gmail_send built the least a message can be: a recipient, a subject and plain text. Mail services
judge a message by how it is made as much as by what it says, and what it lacked is what filters look for:

* who it is from, by name ("Musab Ali <musab@...>", not a bare address);
* an HTML part beside the plain one (every mail app sends both; a lone text part reads as a script), in the
  direction of its language, so Arabic is not shown left to right;
* a date and proper encoding of a non-Latin subject;
* an answer that is an answer: gmail_reply puts the reply in the thread of the message it answers (In-Reply-To,
  References, the thread id, "Re:"). A reply sent as a new message to someone is what unsolicited mail looks like;
* a body: a message with no subject, a few characters or only a link is refused or flagged before it goes.

Files of the conversation can be attached. Where a message lands in the end is the receiving service's decision
(a first message to someone new can still be held): the tool says so instead of promising the inbox.
"""
import base64
import html
import json
import mimetypes
import os
import re
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses

from . import tools

ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
ATTACH_MAX = 18 * 1024 * 1024            # Gmail takes 25 MB encoded: this much raw, with room for the rest
GENERIC = {"test", "testing", "hi", "hello", "hey", "تجربة", "اختبار", "تست", "مرحبا", "السلام عليكم", "(no subject)"}
_SENDER = {"at": 0.0, "name": "", "address": ""}
NAMES = ["gmail_send", "gmail_reply"]


def _request(method, path, body=None, query=None):
    from . import connectors
    return connectors.request("gmail", method, path, body, query)


def sender():
    """(name, address) the mail goes out as: the account's own address, with the name the user is known by."""
    if _SENDER["address"] and time.monotonic() - _SENDER["at"] < 600:
        return _SENDER["name"], _SENDER["address"]
    name = address = ""
    try:
        address = str(_request("GET", "/gmail/v1/users/me/profile").get("emailAddress") or "")
    except Exception:  # noqa: BLE001 - mail still goes out: Gmail fills in the address itself
        pass
    try:
        for one in _request("GET", "/gmail/v1/users/me/settings/sendAs").get("sendAs") or []:
            if one.get("isPrimary") or (address and one.get("sendAsEmail") == address):
                name = str(one.get("displayName") or "")
                address = address or str(one.get("sendAsEmail") or "")
                break
    except Exception:  # noqa: BLE001 - not every connection may read the account's settings
        pass
    if not name:
        try:
            from . import assistant
            name = str(assistant.profile().get("name") or "")
        except Exception:  # noqa: BLE001
            name = ""
    _SENDER.update(at=time.monotonic(), name=name.strip(), address=address.strip())
    return _SENDER["name"], _SENDER["address"]


class MailError(tools.ToolError, ValueError):
    """What is wrong with a message, said to the model (and a ValueError, as the engine's own mail tool raised)."""


def one_line(value, what):
    """A header value: one line. A line break in it would start another header (a hidden recipient, a new subject)."""
    value = str(value or "")
    if "\n" in value or "\r" in value:
        raise MailError("%s has a line break in it: an address or a subject is one line" % what)
    return value.strip()


def addresses(text, what="to"):
    """The addresses in "a@b.c, Name <d@e.f>; g@h.i", each checked."""
    if isinstance(text, (list, tuple)):
        raw = [one_line(x, what) for x in text]
    else:
        raw = [one_line(text, what).replace(";", ",").replace("،", ",")]
    out = []
    for name, address in getaddresses([str(x) for x in raw]):
        address = address.strip()
        if not address:
            continue
        if not ADDRESS.match(address):
            raise MailError("%s: %r is not an email address (ask the user for it, or find it in their contacts or mail)"
                            % (what, address))
        out.append(formataddr((name.strip(), address)) if name.strip() else address)
    return out


def as_html(text):
    """The same words as a plain HTML part: paragraphs, line breaks and links, each in its own direction."""
    def paragraph(p):
        body = html.escape(p.strip()).replace("\n", "<br>\n")
        body = re.sub(r"(https?://[^\s<]+[^\s<.,;:!?)\]»،])", r'<a href="\1">\1</a>', body)
        return '<p dir="auto" style="margin:0 0 1em 0">%s</p>' % body
    parts = [paragraph(p) for p in re.split(r"\n\s*\n", str(text).strip()) if p.strip()]
    return ('<div dir="auto" style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55">\n%s\n</div>'
            % "\n".join(parts))


def signed(text, name):
    """The text, ending with the user's name when it ends with none: mail from nobody is not read as a person's."""
    text, name = str(text or "").strip(), str(name or "").strip()
    if not name or not text:
        return text
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    last = lines[-1]
    tail = " ".join(lines[-3:]).lower()
    if any(len(part) >= 3 and part.lower() in tail for part in name.split()) or len(last) <= 40 and len(lines) > 1:
        return text                          # it is signed, or ends with a short line that is a closing or a name
    return text + "\n\n" + name


def concerns(subject, text):
    """What makes a filter distrust this message, each as one sentence. An empty list: nothing stands out."""
    subject, text = str(subject or "").strip(), str(text or "").strip()
    found = []
    words = re.sub(r"https?://\S+", "", text).strip()
    if subject.lower() in GENERIC or len(subject) < 4:
        found.append("the subject says nothing about the message (%r): a subject that names what it is about reads as real mail" % subject)
    if len(subject) > 8 and subject.upper() == subject and re.search(r"[A-Z]{4,}", subject):
        found.append("the subject is all capitals")
    if len(re.findall(r"[!؟?]{2,}|[$€£]{2,}", subject + " " + text[:300])) > 0:
        found.append("repeated punctuation (!!, ??) reads as an advertisement")
    if re.search(r"https?://\S+", text) and len(words) < 20:
        found.append("the message is almost only a link: say in a sentence what the link is")
    elif len(words) < 20:
        found.append("the message is only a few characters long")
    return found


def _attachment(ctx, path):
    raw = str(path or "").strip()
    full = tools.resolve(ctx, raw)
    if not tools.inside(ctx, full) or not os.path.isfile(full):
        raise tools.ToolError("attach takes a file of this conversation (made with file_create, or shared by the user): %s is not one" % raw)
    return full


def build(to, subject, text, cc="", bcc="", name="", address="", reply=None, files=()):
    """The message, whole. reply: {"id": the Message-ID answered, "references": its References} for an answer."""
    msg = EmailMessage()
    if address:
        msg["From"] = formataddr((name, address)) if name else address
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    if reply and reply.get("id"):
        msg["In-Reply-To"] = reply["id"]
        msg["References"] = (str(reply.get("references") or "") + " " + reply["id"]).strip()
    msg.set_content(text)
    msg.add_alternative(as_html(text), subtype="html")
    size = 0
    for path in files:
        with open(path, "rb") as f:
            data = f.read()
        size += len(data)
        if size > ATTACH_MAX:
            raise tools.ToolError("the attachments are over 18 MB together: send fewer, or a link to them")
        kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
        main, _, sub = kind.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=os.path.basename(path))
    return msg


def _files(ctx, attach):
    if not attach:
        return []
    if isinstance(attach, str):
        text = attach.strip()
        try:
            attach = json.loads(text) if text.startswith("[") else [p for p in re.split(r"[\n,;]+", text) if p.strip()]
        except ValueError:
            attach = [text]
    return [_attachment(ctx, p) for p in attach if str(p).strip()]


def _send(msg, thread=""):
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
    body = {"raw": raw}
    if thread:
        body["threadId"] = thread
    return _request("POST", "/gmail/v1/users/me/messages/send", body)


def _report(what, to, subject, files, name, added, found, data):
    lines = ["%s to %s. Subject: %s" % (what, ", ".join(to), subject)]
    if files:
        lines.append("Attached: " + ", ".join(os.path.basename(f) for f in files))
    if added:
        lines.append("It was signed with the user's name (%s)." % name)
    if found:
        lines.append("It may be held as spam: " + "; ".join(found) + ".")
    lines.append("It is in the account's Sent mail (id %s). Where it lands is the receiving service's decision: a first "
                 "message to someone can still go to their spam folder, and their marking it \"not spam\" once settles it."
                 % (data.get("id") or "?"))
    return "\n".join(lines)


def send(ctx, to, subject, text, cc="", bcc="", attach=None):
    to_list, cc_list, bcc_list = addresses(to), addresses(cc, "cc"), addresses(bcc, "bcc")
    subject, text = one_line(subject, "the subject"), str(text or "").strip()
    if not to_list:
        raise tools.ToolError("the message has no recipient: to is an email address")
    if not subject:
        raise tools.ToolError("give the message a subject that says what it is about: mail without one is treated as spam")
    files = _files(ctx, attach)
    if not text and not files:
        raise tools.ToolError("the message is empty: write what it says")
    name, address = sender()
    body = signed(text, name)
    data = _send(build(to_list, subject, body, cc_list, bcc_list, name, address, files=files))
    return (_report("Sent", to_list, subject, files, name, body != text, concerns(subject, text), data),
            {"connector": "gmail", "id": data.get("id", ""), "thread": data.get("threadId", "")})


def reply(ctx, id, text, all=False, attach=None):
    """Answers a message inside its own conversation."""
    mid, text = str(id or "").strip(), str(text or "").strip()
    if not mid:
        raise tools.ToolError("gmail_reply needs id: the id of the message to answer (from gmail_search)")
    if not text:
        raise tools.ToolError("the reply is empty: write what it says")
    from . import connectors
    original = _request("GET", "/gmail/v1/users/me/messages/" + connectors.segment(mid), query={"format": "metadata"})
    heads = {}
    for h in ((original.get("payload") or {}).get("headers") or []):
        heads.setdefault(str(h.get("name") or "").lower(), str(h.get("value") or ""))
    name, address = sender()
    to_list = addresses(heads.get("reply-to") or heads.get("from") or "", "the sender")
    if not to_list:
        raise tools.ToolError("that message names no sender to answer")
    cc_list = []
    if all:
        mine = address.lower()
        seen = {a.lower() for a in to_list}
        for one in addresses([heads.get("to", ""), heads.get("cc", "")], "cc"):
            bare = one.rsplit("<", 1)[-1].rstrip(">").lower()
            if bare != mine and one.lower() not in seen and not any(bare in s for s in seen):
                cc_list.append(one)
                seen.add(one.lower())
    subject = heads.get("subject", "").strip() or "(no subject)"
    if not re.match(r"^\s*(re|رد)\s*:", subject, re.I):
        subject = "Re: " + subject
    files = _files(ctx, attach)
    body = signed(text, name)
    msg = build(to_list, subject, body, cc_list, [], name, address,
                reply={"id": heads.get("message-id", ""), "references": heads.get("references", "")}, files=files)
    data = _send(msg, str(original.get("threadId") or ""))
    out = _report("Replied", to_list + cc_list, subject, files, name, body != text, [], data)
    return out, {"connector": "gmail", "id": data.get("id", ""), "thread": data.get("threadId", "")}


def install():
    """Takes the place of the engine's gmail_send, and adds gmail_reply beside it."""
    from . import connectors
    S = tools._s

    @tools.tool("gmail_send",
                "Send a new email from the user's Gmail. It goes out as a complete message under the user's name. "
                "subject: what it is about, in a few words (never empty). text: the whole message as plain text, "
                "with a greeting and the user's name at the end. To answer a message the user received, use "
                "gmail_reply instead.",
                {"to": S("recipient address(es), comma separated"), "subject": S("subject"), "text": S("the message"),
                 "cc": S("copy to (optional)"), "bcc": S("hidden copy to (optional)"),
                 "attach": S("paths of files of this conversation to attach, comma separated (optional)")},
                ["to", "subject", "text"], "connector_write")
    def gmail_send(ctx, to, subject, text, cc="", bcc="", attach=None):
        return send(ctx, to, subject, text, cc, bcc, attach)

    @tools.tool("gmail_reply",
                "Answer an email the user received, inside its own conversation (the right subject, thread and "
                "recipient are taken from the message). id: the message's id from gmail_search. all=true answers "
                "everyone it was sent to.",
                {"id": S("id of the message being answered"), "text": S("the reply"),
                 "all": tools._b("reply to everyone on the message"),
                 "attach": S("paths of files of this conversation to attach (optional)")},
                ["id", "text"], "connector_write")
    def gmail_reply(ctx, id, text, all=False, attach=None):
        return reply(ctx, id, text, all, attach)

    connectors.OPERATIONS["gmail_send"] = ("gmail", "POST", "")
    connectors.OPERATIONS["gmail_reply"] = ("gmail", "POST", "")
