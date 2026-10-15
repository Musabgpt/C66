"""Before a phone task is called done, the phone checks the work (items 4 and 5 of the research plan).

Agents fail most often by not checking their result (17.3% of the failures MAST classified) and by not knowing when the
task is done (12.4%); Mobile-Agent-v2's reflection step, which holds the screen against the goal, added about 30% to its
success. This is the part of that check that needs no model call of its own: when the model ends a turn that acted on
the phone, the turn's own record and the screen as it is now are checked for what shows plainly that the task is not
done:

* the text it typed last is still in a text field, and nothing was tapped or pressed after it: typed, never sent;
* its last action on the phone failed, and its reply does not say so.

Then the model is shown that, with the screen, once in the turn: it finishes the task, or says what is not done. When
nothing shows, nothing is asked and the turn ends as before (the screen is read again only after a typing). A reply
that says it failed, or asks the user something (whether to send, which contact), is never pushed on.

A routine is learned only from a turn whose end was checked (item 5; Agent Workflow Memory and Mobile-Agent-E keep only
what was seen to work): neither sign above, a reply that does not say it failed, and the text it typed last on the
screen - the check a routine's replay makes at its end. Before, every turn without an error taught a routine, a turn
that failed to send its message too.
"""
import re

from . import phone_fast, tools

ACTS = ("open_app", "open_url", "tap", "type", "swipe", "scroll", "key", "intent", "settings")
SUBMITS = ("tap", "key")                 # what can send a typed text: a tap on Send, or the key "enter"
LINE = re.compile(r"^\[(\d+)\]\s+(.*)$")
# A reply that says it did not work, matched on phone_fast._norm's folding (lower case, no marks, أ/ا, ة/ه, ى/ي, and
# punctuation made spaces: "couldn't" is "couldn t").
FAILED = re.compile(
    r"\b(?:could ?n t|could not|can t|cannot|can not|unable|failed|wasn t able|was not able|"
    r"did ?n t (?:work|go through|send|open)|did not (?:work|go through|send|open)|not (?:sent|delivered|possible))\b|"
    r"لم (?:استطع|اتمكن|يتم|ينجح|تنجح|يعمل|تعمل|يرسل|ترسل|اجد)|ما (?:قدرت|زبط|ظبط|نجح|مشي|اشتغل|لقيت|انبعت|انرسل)|"
    r"تعذر|فشل|لا استطيع|لا يمكنني|مش قادر|ما بقدر")
QUESTION = re.compile(r"[?؟](?=\s|$|[)\]\"'»*_])")
NOTE = "Before you finish, check the phone: %s.\n\nScreen now:\n%s"


def turn_log(agent):
    """This turn's phone record (phone_fast keeps it on the agent), or None when this turn did not use the phone."""
    st = getattr(agent, "_phone_turn", None)
    if not st or getattr(agent, "depth", 0) or st.get("turn") != getattr(getattr(agent, "session", None), "turn", None):
        return None
    return st


def last_typed(log):
    """(the text typed last in the turn, the record after it); ("", []) when nothing was typed."""
    for i in range(len(log) - 1, -1, -1):
        action, step, ok = log[i]
        if action == "type" and ok:
            return str((step or {}).get("text") or "").strip(), log[i + 1:]
    return "", []


def field_with(screen, text):
    """The number of the text field that holds the text on this screen listing ("[12] hi · text field, field, tap"),
    or 0. Typing sets a field's whole text, so the field starts with it (a long text is cut on the screen: its start
    is enough)."""
    want = phone_fast._norm(text)[:40]
    if not want:
        return 0
    for line in (screen or "").splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        words, sep, tags = m.group(2).rpartition(" · ")
        if sep and "field" in [t.strip() for t in tags.split(",")] and phone_fast._norm(words).startswith(want):
            return int(m.group(1))
    return 0


def says_failed(answer):
    return bool(FAILED.search(phone_fast._norm(answer)))


def asks(answer):
    """Whether the reply ends by asking the user something: a question mark that ends a sentence in its last lines
    (not the one inside a link: results?search_query=...)."""
    return bool(QUESTION.search(str(answer or "")[-300:]))


def _screen_now(st):
    try:
        screen = phone_fast.read_screen(timeout=8)
    except Exception:  # noqa: BLE001 - the phone may be away: the last screen this turn saw
        screen = ""
    if screen:
        st["screen"] = screen
    return screen or st.get("screen") or ""


def check(agent, answer, asked=False):
    """At the model's final answer: the note that sends it back to finish the task, when the phone shows plainly
    that it is not done and it was not sent back yet in this turn (asked), else "". Marks the turn checked
    (st["verified"]) or not, for learning a routine."""
    st = turn_log(agent)
    if st is None:
        return ""
    log = st["log"]
    if not any(action in ACTS for action, _, _ in log):
        return ""
    st["verified"] = False
    if says_failed(answer) or asks(answer):
        # it says itself what did not work, or it asks the user (whether to send, which contact): an honest end,
        # never pushed on - and it teaches no routine
        return ""
    signs = []
    screen = None
    typed, after = last_typed(log)
    if typed:
        screen = _screen_now(st)
        field = field_with(screen, typed)
        if field and not any(action in SUBMITS and ok for action, _, ok in after):
            signs.append('the text you typed last ("%s") is still in the field [%d], and nothing was tapped or '
                         'pressed after it: it was typed, not sent. If the task was to send or submit it, do it now '
                         '(tap the send button, or key "enter"); if typing it was the whole task, say so'
                         % (tools.clip(typed, 80), field))
    action, _, ok = log[-1]
    if not ok and action in ACTS:
        signs.append("your last phone action (%s) failed - its result is above - and your reply does not say so: "
                     "finish the task from the screen below, or tell the user plainly what is not done" % action)
    if not signs:
        st["verified"] = not typed or phone_fast.shows(screen, typed)
        return ""
    if asked:
        return ""
    if screen is None:
        screen = _screen_now(st)
    return NOTE % ("; and ".join(signs), tools.clip(screen or "(the screen could not be read)", 7000))
