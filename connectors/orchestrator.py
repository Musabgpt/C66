"""Phase 5 deterministic tool orchestration hints for MusabAI.

The orchestrator never executes actions or grants permission. It observes the
tools already exposed to the session and returns a short recommended sequence.
"""
from __future__ import annotations

import json
import re

from . import tools

NAMES = ["orchestrator_plan"]


def _has_prefix(names, prefix):
    return sorted(n for n in names if n.startswith(prefix))


def plan(names, request=""):
    names = set(names or ())
    text = str(request or "").casefold()
    steps = []
    used = set()

    def add(tool, reason):
        if tool in names and tool not in used:
            used.add(tool)
            steps.append({"tool": tool, "reason": reason})

    codeish = bool(re.search(r"\b(code|bug|error|build|test|repo|repository|project|file|class|function|gradle|python|java|kotlin|كود|خطا|خطأ|بناء|اختبار|مستودع|مشروع|ملف)\b", text))
    webish = bool(re.search(r"\b(web|internet|latest|news|research|search|website|url|ويب|انترنت|النت|احدث|أحدث|اخبار|أخبار|بحث|موقع)\b", text))
    docish = bool(re.search(r"\b(pdf|docx|document|spreadsheet|pptx|xlsx|ocr|مستند|وثيقة|بي دي اف|اكسل)\b", text))
    browserish = bool(re.search(r"\b(browser|login|page|click|form|session|متصفح|تسجيل|صفحة|اضغط|نموذج)\b", text))
    memoryish = bool(re.search(r"\b(previous|remember|memory|last time|قبل|سابق|تذكر|ذاكرة)\b", text))
    phoneish = bool(re.search(r"\b(android|phone|app|notification|termux|هاتف|اندرويد|تطبيق|اشعار|ترمكس)\b", text))
    executionish = bool(re.search(r"\b(run|execute|build|test|sandbox|termux|command|git|commit|diff|نفذ|نفّذ|شغل|شغّل|بناء|اختبار|ترمكس|مستودع|كوميت)\b", text))
    gitish = bool(re.search(r"\b(git|commit|branch|diff|repository|repo|مستودع|فرع|كوميت)\b", text))
    continuationish = bool(re.search(r"\b(continue|resume|previous task|pick up|where.*stopped|كمل|كمّل|اكمل|أكمل|تابع|استأنف|استكمال)\b", text))
    longish = bool(re.search(r"\b(long|multi[- ]?step|phase|project|implementation|build|migration|طويل|مراحل|مرحلة|مشروع|تنفيذ|بناء)\b", text))

    if continuationish:
        add("task_resume", "Load the latest durable project checkpoint before reconstructing unfinished work.")
    if codeish:
        add("project_rag_search", "Retrieve relevant project evidence before broad edits or guesses.")
    if memoryish:
        add("memory_recall", "Recall bounded project lessons that may apply to this request.")
    if docish:
        add("document_engine_selector", "Choose the document backend that is actually available.")
    if browserish:
        add("browser_tool_selector", "Choose the available API/MCP/browser route before UI automation.")
    if webish:
        add("search_router", "Use configured search/crawl for live web evidence.")
        add("searxng_search", "Use discovery search when deep crawl is not required.")
    if phoneish:
        add("termux_exec", "Use Termux only when the separate Android shell environment is required.")
        add("phone", "Use the built-in local Android bridge for device UI/actions.")
    if executionish:
        add("execution_plan", "Choose the actual available execution host before running cross-environment commands.")
    if gitish:
        add("git_status", "Inspect real local repository state before Git changes.")
    if longish:
        add("task_checkpoint", "Persist meaningful verified progress so long work can resume after a restart.")
    if codeish:
        add("read", "Inspect exact files before modifying them.")
        add("grep", "Locate exact symbols or references after retrieval.")
        add("bash", "Run the relevant tests/build and capture real output.")
    if not steps:
        for candidate, reason in (
            ("project_rag_search", "Retrieve project context if the task concerns the current workspace."),
            ("read", "Inspect current evidence before acting."),
            ("browser_tool_selector", "Choose an available browser route if web interaction is needed."),
            ("search_router", "Use configured live research only if current web evidence is needed."),
        ):
            add(candidate, reason)

    return {
        "request": str(request or "")[:1000],
        "steps": steps[:8],
        "available": {
            "project_rag": "project_rag_search" in names,
            "memory": "memory_recall" in names,
            "browser_router": "browser_tool_selector" in names,
            "search_router": "search_router" in names,
            "document_router": "document_engine_selector" in names,
            "termux": "termux_exec" in names,
            "phone": "phone" in names,
            "execution_router": "execution_plan" in names,
            "scratch": "sandbox_exec" in names,
            "git": "git_status" in names,
            "task_state": "task_resume" in names and "task_checkpoint" in names,
            "mcp_tools": len(_has_prefix(names, "mcp__")),
        },
        "note": "This plan is advisory only; normal permissions still apply and unavailable tools are never invented.",
    }


def install():
    @tools.tool(
        "orchestrator_plan",
        "Plan a short evidence-first tool sequence using only tools exposed to the current session. This does not execute actions or bypass permissions.",
        {"request": {"type": "string", "description": "the user's current task"}},
        ["request"],
        "meta",
    )
    def orchestrator_plan(ctx, request):
        session = getattr(ctx, "session", None)
        names = set(getattr(session, "tool_names", None) or ())
        result = plan(names, request)
        return json.dumps(result, ensure_ascii=False), {"orchestration_steps": len(result["steps"])}
