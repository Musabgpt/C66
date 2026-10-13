"""Smarter and faster turns.

* Leaner requests (faster, cheaper, fewer compactions): tool results from earlier user turns are shortened in what is
  sent to the model (the full text stays in the thread). Only turns before the latest user message change, so the
  request's prefix stays the same while a turn runs and providers can reuse their prompt cache.
* A second look before finishing (Cursor/Claude Code's reviewer): when a turn changed code, the model reviews the
  turn's diff once, with no tools, and either approves it or lists concrete bugs, which the agent then fixes.
"""
import re

KEEP_HEAD = 700
KEEP_TAIL = 400
TRIM_ABOVE = 2000
REVIEW_DIFF = 14000
REVIEW_MIN_LINES = 3

REVIEW_PROMPT = (
    "You are a strict code reviewer. Below is the diff the coding agent just made for this request:\n\n"
    "<request>\n{request}\n</request>\n\n<diff>\n{diff}\n</diff>\n\n"
    "Look only for real defects that would make the change fail or misbehave: wrong logic, a broken call or import, "
    "an unhandled case the request needs, a typo in a name, a security hole, code left half-changed. Ignore style and "
    "suggestions. If there is nothing that must be fixed, reply with exactly: LGTM\n"
    "Otherwise reply with at most 5 lines, each: path:line - the bug - the fix.")


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def trim(messages):
    """The messages to send, with long tool results from earlier user turns shortened (a new list)."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    out = []
    for i, m in enumerate(messages):
        if i < last_user and m.get("role") == "tool" and isinstance(m.get("content"), str) \
                and len(m["content"]) > TRIM_ABOVE:
            c = m["content"]
            m = dict(m, content=c[:KEEP_HEAD] + "\n[... %d characters of this earlier result omitted; run the tool "
                                                 "again if you need them ...]\n" % (len(c) - KEEP_HEAD - KEEP_TAIL)
                     + c[-KEEP_TAIL:])
        out.append(m)
    return out


def enabled(agent):
    cfg = getattr(agent, "cfg", None) or {}
    if cfg.get("review_before_finish") is False or getattr(agent, "depth", 0):
        return False
    client = getattr(agent, "client", None)
    if client is None or getattr(client, "on_device", False):
        return False                          # a phone's own small model: one more call costs minutes
    return True


def _diff(agent):
    s = agent.session
    try:
        changes = s.changes(s.turn)
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(c.get("diff", "") for c in changes)


def review(agent, ctx):
    """Concrete bugs the reviewer found in this turn's changes ("" when approved, skipped or unavailable). Runs once
    per turn."""
    if getattr(ctx, "_musab_reviewed", False) or not enabled(agent):
        return ""
    ctx._musab_reviewed = True
    diff = _diff(agent)
    changed = sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
    if changed < REVIEW_MIN_LINES:
        return ""
    s = agent.session
    request = str(getattr(s, "active_objective", "") or "")[:2000]
    prompt = REVIEW_PROMPT.format(request=request or "(see the diff)", diff=diff[:REVIEW_DIFF])
    agent.emit({"type": "status", "text": "Reviewing the changes…"})
    try:
        comp = agent.client.chat([{"role": "user", "content": prompt}], tools=None, owner=s.id, max_tokens=500,
                                 reasoning="off", cancel=agent.cancel)
    except Exception:  # noqa: BLE001 - the review is a help; a failed call never blocks the turn
        return ""
    text = _text(getattr(comp, "content", "") or "").strip()
    if not text or re.match(r"^\W*LGTM\b", text, re.I) or len(text) < 8:
        return ""
    return ("A reviewer looked at your diff and found problems. Fix the real ones (ignore any that are wrong), "
            "then finish:\n" + text[:1500])
