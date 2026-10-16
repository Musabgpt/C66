#!/usr/bin/env python3
"""Deterministic Promptfoo provider for MusabAI routing contract evaluation."""
import json
from pathlib import Path
import sys
import types

# Promptfoo executes script providers relative to the config file directory.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

# Load the pure planners without starting the full tool registry, which imports
# these modules back during registration.
stub = types.ModuleType("newal_code.tools")
class ToolError(Exception):
    pass
def tool(*args, **kwargs):
    return lambda fn: fn
stub.ToolError = ToolError
stub.tool = tool
sys.modules["newal_code.tools"] = stub

from newal_code import execution, orchestrator

prompt = sys.argv[1] if len(sys.argv) > 1 else ""
names = {
    "project_rag_search", "memory_recall", "document_engine_selector",
    "browser_tool_selector", "search_router", "searxng_search",
    "termux_exec", "phone", "read", "grep", "bash",
    "execution_plan", "sandbox_exec",
    "git_status", "git_diff", "git_log", "git_commit",
    "task_checkpoint", "task_resume", "task_complete", "task_list",
}
payload = {
    "execution": execution.plan(names, prompt),
    "orchestrator": orchestrator.plan(names, prompt),
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
