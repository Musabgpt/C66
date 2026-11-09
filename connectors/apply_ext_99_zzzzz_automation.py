"""The unified automation layer reaches the engine.

Copies the free-pool OpenAI endpoint (automation_llm.py) and the container automation
server (automation_server.py: Mobilerun + uiautomator2 + Pydoll + Browser Use) into the
engine package, and hooks the endpoint into the engine's HTTP server so the automation
backends can call one local, keyless, free-pool-backed /v1/free surface.

Runs after the task-memory extension (zzzzz > zzzz), so it appends its parity-test import
after that one.
"""
import shutil


def apply(src, package, replace):
    for name in ("automation_llm.py", "automation_llm_tests.py", "automation_server.py"):
        shutil.copyfile(src / name, package / name)

    server = package / "server.py"
    text = server.read_text(encoding="utf-8")
    get_anchor = '        if scheduled_tasks.route(self, "GET", path):\n            return\n'
    post_anchor = '        if scheduled_tasks.route(self, "POST", path, b):\n            return\n'
    if get_anchor not in text or post_anchor not in text:
        raise SystemExit("Unsafe patch refused: server.py route anchors missing for the automation endpoint")
    if "automation_llm.route" not in text:
        text = text.replace(
            get_anchor,
            '        from . import automation_llm\n'
            '        if automation_llm.route(self, "GET", path):\n            return\n' + get_anchor, 1)
        text = text.replace(
            post_anchor,
            '        from . import automation_llm\n'
            '        if automation_llm.route(self, "POST", path, b):\n            return\n' + post_anchor, 1)
        server.write_text(text, encoding="utf-8")

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .automation_llm_tests import AutomationLlmTests  # noqa: E402,F401 - run with the parity tests in CI\n")
