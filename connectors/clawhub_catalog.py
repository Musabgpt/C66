"""Pinned local adaptations and discovery of public ClawHub identifiers."""
import json
import re
import unicodedata
from typing import NamedTuple
from urllib.parse import urlencode

from . import tools


class Workflow(NamedTuple):
    source: str
    version: str
    trigger: re.Pattern
    instructions: str


# These on-device adaptations use the published versions below. ClawHub lists them as MIT-0.
# The Second Read package additionally includes the MIT license reproduced at the end of this file.
WORKFLOWS = {
    "meeting_minutes": Workflow(
        "youteacher/meeting-minutes-action-items", "1.2.0",
        re.compile(r"\b(?:meeting minutes|meeting summary|summari[sz]e (?:this |the )?meeting|meeting action items)\b"
                   r"|(?:محضر|ملخص|تلخيص|لخص)\s+(?:(?:هذا|هذه)\s+)?(?:ال)?اجتماع", re.I),
        "Meeting minutes workflow: summarize supplied notes in the user's language. Separate decisions from proposals. "
        "For each action give the owner, deadline and supporting passage where available. Mark missing owners or dates "
        "as unspecified. List unresolved questions. If no notes are available, ask for them."),
    "android_phone": Workflow(
        "pelae1/mobilerun-official", "1.2.0",
        re.compile(r"\b(?:on (?:my|the) phone|open (?:the )?(?:settings|calculator|clock) app|android settings)\b"
                   r"|(?:على (?:هاتفي|الهاتف|الجوال)|افتح (?:تطبيق|إعدادات|اعدادات|الحاسبة|الآلة الحاسبة))", re.I),
        "Android workflow: for arithmetic explicitly requested in the calculator app, call calculator_app. Otherwise use phone to open the requested app and inspect its returned UI tree. Use the labels "
        "and IDs from the current observation. Use screen_look for visual content. Verify the result on screen."),
    "web_browser": Workflow(
        "tinyfish/tinyfish", "1.0.6",
        re.compile(r"https?://|\b(?:in (?:your|the) browser|open (?:the )?website|browse to)\b"
                   r"|(?:بالمتصفح|في المتصفح|افتح (?:موقع|صفحة|رابط))", re.I),
        "Web workflow: use web_search to locate an unknown site. For a supplied URL use web_fetch to read static text, "
        "or browser to open it when the user asks to browse or interact. Use returned controls for navigation "
        "and read the resulting page before reporting the result."),
    "explain_simply": Workflow(
        "deciqai/feynman-technique", "1.0.8",
        re.compile(r"\b(?:explain (?:this |it )?(?:simply|like i.m (?:five|a beginner))|feynman technique|"
                   r"test my understanding)\b|(?:اشرح(?:ها|ه)? (?:لي )?(?:ببساطة|كأني مبتدئ)|اختبر فهمي)", re.I),
        "Explain the concept in the user's language with a concrete example. Define each essential term and show "
        "what would change if an input changed. If using an analogy, identify where it breaks down. If the user "
        "wants to test their understanding, invite a prediction and use their answer to identify a specific gap."),
    "weekly_review": Workflow(
        "abdullah944/weekly-goal-review-skill", "0.1.0",
        re.compile(r"\b(?:weekly (?:goal )?review|review my week|review (?:my|our) weekly goals)\b"
                   r"|(?:راجع (?:معي )?أهداف(?:ي|نا) (?:هذا )?الأسبوع|مراجعة (?:أهداف )?الأسبوع)", re.I),
        "Review the stated goal, the week's notes and relevant deadlines. Identify completed work, outstanding "
        "tasks and constraints supported by the notes. Provide a short next-week plan and a numbered, reusable "
        "checklist that fits the user's available time."),
    "weekly_meals": Workflow(
        "harrylabsj/weekly-meal-planner", "1.0.1",
        re.compile(r"\b(?:weekly (?:meal plan|menu)|meal plan for (?:the |this )?week|plan (?:our|my) meals)\b"
                   r"|(?:جدول|خطة|قائمة|خطط) (?:لي )?(?:وجبات|للوجبات)(?:ي|نا| الأسبوع| أسبوعية)?", re.I),
        "Plan meals for the requested days and household size, using the user's tastes, food restrictions, budget "
        "and available cooking time. Reuse ingredients across days. Give a day-by-day menu and one consolidated "
        "shopping list with quantities. If a budget is supplied, state the currency and label approximate prices."),
    "proofread_draft": Workflow(
        "c-kapsalis/second-read", "0.3.1",
        re.compile(r"\b(?:proofread(?:ing)?|copyedit|second read of (?:this|my) draft)\b"
                   r"|(?:دقق|راجع) (?:لي )?(?:هذا )?(?:النص|المسودة) (?:لغويا|إملائيا)|"
                   r"(?:تدقيق لغوي|صحح (?:لي )?الأخطاء (?:اللغوية|الإملائية))", re.I),
        "Read the supplied draft with its audience, purpose and requested scope in mind. Preserve factual claims, "
        "voice, intended certainty and formatting. For a review, anchor proposed changes to exact phrases and "
        "distinguish errors from stylistic alternatives. When a revised draft is requested, provide it directly."),
}


def task_note(text):
    # Users can type Arabic with or without vowel marks and tatweel.
    text = "".join(c for c in unicodedata.normalize("NFC", text)
                   if not ("\u064b" <= c <= "\u065f" or c == "\u0640"))
    return "\n".join(spec.instructions for spec in WORKFLOWS.values() if spec.trigger.search(text))


@tools.tool("clawhub_workflow", "Load instructions for an installed workflow when its meaning fits the user's task.",
            {"skill_id": {"type": "string", "enum": list(WORKFLOWS)}}, ["skill_id"], "read")
def workflow(ctx, skill_id):
    if not isinstance(skill_id, str) or skill_id not in WORKFLOWS:
        raise tools.ToolError("Unknown installed workflow. Choose one of: " + ", ".join(WORKFLOWS))
    spec = WORKFLOWS[skill_id]
    return spec.instructions, {"workflow": skill_id, "source": spec.source, "version": spec.version}


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


# License accompanying c-kapsalis/second-read 0.3.1.
# MIT License
#
# Copyright (c) 2026 Christoforos Kapsalis
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
