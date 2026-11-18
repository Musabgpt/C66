"""Read public skill identifiers from ClawHub's search index."""
import json
import re
from urllib.parse import urlencode

from . import tools


# Offline adaptation of youteacher/meeting-minutes-action-items 1.2.0 (MIT-0).
_MEETING = re.compile(
    r"\b(?:meeting minutes|meeting summary|summari[sz]e (?:this |the )?meeting|meeting action items)\b"
    r"|(?:محضر|ملخص|تلخيص|لخص)\s+(?:(?:هذا|هذه)\s+)?(?:ال)?اجتماع", re.I)

_PHONE = re.compile(
    r"\b(?:on (?:my|the) phone|open (?:the )?(?:settings|calculator|clock) app|android settings)\b"
    r"|(?:على (?:هاتفي|الهاتف|الجوال)|افتح (?:تطبيق|إعدادات|اعدادات|الحاسبة|الآلة الحاسبة))", re.I)
_WEB = re.compile(
    r"https?://|\b(?:in (?:your|the) browser|open (?:the )?website|browse to)\b"
    r"|(?:بالمتصفح|في المتصفح|افتح (?:موقع|صفحة|رابط))", re.I)


def task_note(text):
    notes = []
    if _MEETING.search(text):
        notes.append("Meeting minutes workflow: summarize the supplied notes in the user's language. "
                "Separate decisions from proposals. For action items give the owner, deadline and "
                "supporting passage where available. Mark missing owners or dates as unspecified. "
                "List unresolved questions. If no notes are available, ask for them.")
    if _PHONE.search(text):
        # Local Android implementation of pelae1/mobilerun-official 1.2.0 (MIT-0).
        notes.append("Android workflow: use phone to open the requested app, inspect the returned UI tree, "
                     "and act on controls visible in that observation. After navigation use the new screen's "
                     "labels and IDs. Use screen_look for visual content. Verify the requested result on screen.")
    if _WEB.search(text):
        # Local browser implementation of tinyfish/tinyfish 1.0.6 (MIT-0).
        notes.append("Web workflow: use web_search to locate an unknown site. For a supplied URL use web_fetch "
                     "to read static text, or browser to open it when the user asks to browse or interact. "
                     "Use returned controls for navigation and read the resulting page before reporting the result.")
    return "\n".join(notes)


@tools.tool("clawhub_search", "Find public OpenClaw skills by topic. Use generic keywords without private user data. Returns catalog identifiers only.",
            {"topic": tools._s("Short public topic, for example timezone conversion")}, ["topic"], "net")
def search(ctx, topic):
    if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 96:
        raise tools.ToolError("Supply a topic of 1 to 96 characters")
    url = "https://clawhub.ai/api/v1/search?" + urlencode({"q": topic.strip(), "limit": 5})
    try:
        results = json.loads(tools._get(url))["results"]
        if not isinstance(results, list):
            raise ValueError("invalid results")
    except Exception as exc:
        raise tools.ToolError("ClawHub search unavailable") from exc
    matches = []
    for row in results[:5]:
        if not isinstance(row, dict) or row.get("source") != "clawhub":
            continue
        owner, slug = row.get("ownerHandle"), row.get("slug")
        if all(isinstance(part, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", part)
               for part in (owner, slug)):
            matches.append(owner + "/" + slug)
    return json.dumps(matches), {"count": len(matches)}
