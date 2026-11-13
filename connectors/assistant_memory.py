"""The conversation's own memory: what was asked and done in it survives when a long conversation is shortened.

A conversation is sent to the model whole, until it fills most of the model's context. Then the engine shortens
it: a summary written by the model takes the place of everything between the first and the latest message. For a
personal assistant that loses what matters most, and a free model can return an empty summary (its thinking spends
the tokens), which would lose everything.

For the assistant the shortened conversation is built from the conversation itself:

* a record of every turn, taken from what the conversation showed (never written by a model, so complete): what
  the user asked, in their words, what was done (files made, mail sent, pages opened, forms sent, posts and
  replies, reminders and details saved) and what the answer said;
* the model's summary, asked for the facts an assistant needs (names, dates, numbers, addresses, choices, prices
  and links found), with room enough to answer, asked again once when it comes back empty;
* the last exchanges word for word, then the message being worked on.
"""
import re

TURNS_SHOWN = 40                   # turns in the record (the newest)
RECORD_MAX = 9000                  # characters of the record
ASKED, ANSWER = 500, 400           # characters of a request and of an answer kept in the record
EXCHANGES = 3                      # earlier exchanges kept word for word
EXCHANGE_MAX = 3000
SUMMARY_TOKENS = 2048

COMPACT = ("This conversation is long and will be shortened. Write the notes that let you carry on from them alone. "
           "Keep, exactly as they were given: the user's names, dates, times, numbers, addresses, accounts, choices "
           "and preferences; what was found (options, prices, links, order and booking numbers); what was sent, "
           "published, booked or saved and to whom; and what is still to do. Plain text, at most 300 words, no "
           "tool calls.")


def _clip(text, n):
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _one(text, n):
    return _clip(str(text or "").replace("\n", " "), n)


def _did(name, args, meta):
    """One line for what a successful step did, or "" for a step that only looked."""
    args, meta = args or {}, meta or {}
    if name == "file_create":
        f = meta.get("file") or {}
        return "made the file %s" % (f.get("name") or args.get("name") or "?")
    if name == "file_read":
        return "read the file %s" % (meta.get("name") or args.get("path") or "?")
    if name == "gmail_send":
        return "emailed %s: %s" % (_one(args.get("to"), 80), _one(args.get("subject"), 100))
    if name == "gmail_reply":
        return "replied to an email (id %s)" % _one(args.get("id"), 40)
    if name == "browser":
        op = str(args.get("op") or "")
        where = _one(meta.get("title") or meta.get("url"), 90)
        if op == "open":
            return "opened %s (%s)" % (_one(args.get("url"), 120), where) if where else "opened %s" % _one(args.get("url"), 120)
        if op == "fill":
            return "filled a form on %s%s" % (where or "a page", " and clicked %s" % _one(args.get("target"), 40) if args.get("target") else "")
        if op == "upload":
            return "gave the file %s to a page" % _one(args.get("path"), 60)
        return ""
    if name == "phone":
        action = str(args.get("action") or "")
        if action == "notification_reply":
            return "replied to %s: %s" % (_one(args.get("target"), 60), _one(args.get("text"), 120))
        if action in ("calendar_add", "alarm", "timer", "sms", "call", "open_url"):
            detail = args.get("title") or args.get("number") or args.get("to") or args.get("url") or args.get("time") or ""
            return "phone %s %s" % (action, _one(detail, 80))
        return ""
    if name == "phone_routine":
        if str(args.get("action") or "run") == "search":
            return ""
        values = args.get("values")
        values = values if isinstance(values, (list, tuple)) else [values] if values else []
        return "ran the procedure %s%s" % (_one(args.get("id"), 60), " with " + "; ".join(_one(v, 120) for v in values) if values else "")
    if name == "phone_agent":
        return "phone task: %s" % _one(args.get("goal") or args.get("task"), 140)
    if name == "browser_agent":
        return "web task: %s" % _one(args.get("task"), 140)
    if name == "scheduled_task_create":
        return "saved a reminder: %s (%s)" % (_one(args.get("title") or args.get("prompt") or args.get("text"), 100),
                                              _one(args.get("when") or args.get("delay_minutes") or "", 40))
    if name == "my_info" and str(args.get("action") or "") == "set":
        fields = args.get("fields") if isinstance(args.get("fields"), dict) else {k: v for k, v in args.items() if k not in ("action",)}
        return "saved the user's details: %s" % ", ".join(sorted(str(k) for k in fields))[:120]
    if name == "learn":
        return "kept a lesson: %s" % _one(args.get("lesson"), 120)
    if name == "web_search":
        return "searched the web: %s" % _one(args.get("query"), 100)
    return ""


def record(events):
    """[{"asked", "did": [...], "answer", "failed"}] for each turn the conversation showed, oldest first."""
    turns, current, starts = [], None, {}
    for e in events or ():
        if not isinstance(e, dict) or e.get("sub"):
            continue                                  # a helper agent's own steps belong to the step that ran it
        kind = e.get("type")
        if kind == "turn_start":
            current = {"asked": str(e.get("text") or ""), "did": [], "answer": "", "failed": 0}
            turns.append(current)
        elif current is None:
            continue
        elif kind == "tool_start":
            starts[e.get("id")] = (str(e.get("name") or ""), e.get("args") or {})
        elif kind == "tool_end":
            name, args = starts.pop(e.get("id"), (str(e.get("name") or ""), {}))
            if not e.get("ok"):
                current["failed"] += 1
                continue
            line = _did(name or e.get("name") or "", args, e.get("meta"))
            if line and line not in current["did"]:
                current["did"].append(line)
        elif kind == "assistant" and e.get("final"):
            current["answer"] = str(e.get("text") or "")
        elif kind == "turn_end" and not current["answer"]:
            current["answer"] = str(e.get("answer") or "")
    return turns


def record_text(events, limit=RECORD_MAX):
    """The record as the model reads it, newest turns kept when it is too long."""
    turns = record(events)
    lines = []
    for n, t in enumerate(turns, 1):
        parts = ["%d. The user asked: %s" % (n, _clip(t["asked"].split("\n\nFiles attached to this message")[0], ASKED))]
        attached = re.findall(r"^(/\S.*)$", t["asked"].split("Files attached to this message", 1)[-1], re.M) \
            if "Files attached to this message" in t["asked"] else []
        if attached:
            parts.append("   With the files: " + ", ".join(p.rsplit("/", 1)[-1] for p in attached))
        if t["did"]:
            parts.append("   Done: " + "; ".join(t["did"][:12]))
        if t["answer"]:
            parts.append("   Answered: " + _one(t["answer"], ANSWER))
        elif t["failed"]:
            parts.append("   (no answer: %d step(s) failed)" % t["failed"])
        lines.append("\n".join(parts))
    lines = lines[-TURNS_SHOWN:]
    while lines and sum(len(l) + 1 for l in lines) > limit:
        lines.pop(0)
    skipped = len(turns) - len(lines)
    head = "(%d earlier turn(s) are not listed)\n" % skipped if skipped else ""
    return head + "\n".join(lines)


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def exchanges(messages, upto, count=EXCHANGES):
    """The last complete exchanges before index upto: (the user's message, the final answer to it), word for word."""
    from . import continuity
    out, user = [], None
    for m in messages[:upto]:
        if continuity._actual_user(m):
            user = m
        elif user is not None and m.get("role") == "assistant" and not m.get("tool_calls") and _text(m.get("content")).strip():
            out.append((user, {"role": "assistant", "content": _clip(_text(m.get("content")), EXCHANGE_MAX)}))
            user = None
    return out[-count:]


def summarize(agent, note=""):
    """The model's notes for the shortened conversation; "" when it gives none (the record still holds the turns)."""
    s = agent.session
    ask = COMPACT + ((" Focus: " + note) if note else "")
    msgs = agent.request_messages() + [{"role": "user", "content": ask}]
    for _ in range(2):
        try:
            comp = agent.client.chat(msgs, tools=None, owner=s.id, max_tokens=SUMMARY_TOKENS, reasoning="off",
                                     cancel=agent.cancel)
        except Exception:  # noqa: BLE001 - the record alone carries the conversation
            return ""
        text = (getattr(comp, "content", "") or "").strip()
        if text:
            return text
    return ""


def compact_messages(session, summary, objective="", screen=""):
    """The shortened conversation: the first request, the notes (the record, the summary, the task), the last
    exchanges word for word, and the message being worked on. Always user and assistant in turn."""
    from . import continuity
    kept = continuity.compact_messages(session, summary or "(The model gave no summary: the record below is complete.)",
                                       objective, screen)
    note = next((m for m in kept if m.get("role") == "assistant"), None)
    rec = record_text(getattr(session, "events", None) or [])
    if note is not None and rec:
        marker = "[Durable task anchor"
        body = str(note.get("content") or "")
        block = ("[What this conversation asked and did, turn by turn: taken from the conversation itself, not from a "
                 "summary. Use it for names, files, addresses and what was already sent or done.]\n" + rec + "\n")
        note["content"] = body.replace(marker, block + marker, 1) if marker in body else body + "\n" + block
    messages = list(getattr(session, "messages", None) or [])
    real = [i for i, m in enumerate(messages) if continuity._actual_user(m)]
    if len(real) < 2 or note is None:
        return kept
    first, latest = messages[real[0]], messages[real[-1]]
    middle = [pair for pair in exchanges(messages, real[-1]) if pair[0] is not first]
    out = [first, note]
    for user, answer in middle:
        out += [user, answer]
    out.append(latest)
    return out
