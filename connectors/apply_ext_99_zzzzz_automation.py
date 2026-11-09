"""The unified automation layer reaches the engine.

Copies the free-pool OpenAI endpoint (automation_llm.py), the container automation
server (automation_server.py) and its runtime+tools (automation_runtime.py) into the
engine package; hooks the endpoint into the HTTP server; registers the five automation
tools (phone_agent, phone_power, browser, browser_agent, adb_connect) for the phone;
and teaches the prompt one professional routing policy across every tool.

Runs after the task-memory extension (zzzzz > zzzz).
"""
import shutil


PROMPT_TOOLS = ("web_search and web_fetch for the web; weather for the weather; "
                "phone, screen_look, phone_routine and phone_sequence for the phone; ")
PROMPT_TOOLS_NEW = ("web_search and web_fetch for the web; weather for the weather; "
                    "phone, screen_look, phone_routine and phone_sequence for one known phone step; "
                    "phone_agent for a whole phone task (the Mobilerun agent that reads the screen and acts); "
                    "phone_power with adb_connect for low-level control through uiautomator2; "
                    "browser and browser_agent for the live web browser (Pydoll and Browser Use over Chromium); ")

POLICY_ANCHOR = "- Otherwise start with phone open_app"
POLICY = (
    "- Choosing a phone engine: for a single known step, the phone tool, phone_routine or a task-memory tm: note is "
    "fastest. For a whole goal that spans screens or apps (\"book a ride\", \"reply to the latest message and like "
    "it\"), call phone_agent with the goal in one go — it looks at the real screen and taps, types and swipes until "
    "it is done, then reports. Use phone_power (op=dump/click_text/send_keys/app_start) only after adb_connect has "
    "paired this phone's own wireless ADB, for cases the accessibility tools cannot reach.\n"
    "- The web: for one precise action use browser (open, click, type, extract); for a whole web task "
    "(\"find the cheapest flight and tell me\") use browser_agent, which drives the same live Chromium to the end "
    "and returns the answer. Prefer a direct API or web_search when a page visit is not actually needed.\n"
    "- All of these run on the user's device and use the free model pool; nothing needs a key. Keep to the fewest "
    "steps, verify the real result, and answer in one line in the user's language.\n"
)


def apply(src, package, replace):
    for name in ("automation_llm.py", "automation_llm_tests.py", "automation_server.py",
                 "automation_runtime.py", "automation_runtime_tests.py"):
        shutil.copyfile(src / name, package / name)

    # 1) The free-pool OpenAI endpoint, hooked into the engine HTTP server.
    # Anchor on the pinned engine's own lines (present before any other patch,
    # so this is independent of patch ordering): the GET /api/health check and
    # the POST _body() call, both right after the shared _gate.
    server = package / "server.py"
    text = server.read_text(encoding="utf-8")
    get_anchor = '        if path == "/api/health":\n            return self._json({"ok": True})\n'
    post_anchor = '        b = self._body()\n'
    if get_anchor not in text or post_anchor not in text:
        raise SystemExit("Unsafe patch refused: server.py route anchors missing for the automation endpoint")
    if "automation_llm.route" not in text:
        text = text.replace(get_anchor,
                            '        from . import automation_llm\n'
                            '        if automation_llm.route(self, "GET", path):\n            return\n' + get_anchor, 1)
        text = text.replace(post_anchor,
                            post_anchor
                            + '        from . import automation_llm\n'
                            '        if automation_llm.route(self, "POST", path, b):\n            return\n', 1)
        server.write_text(text, encoding="utf-8")

    # 2) Register the five automation tools (phone-gated, next to the other phone tools).
    tools_py = package / "tools.py"
    t = tools_py.read_text(encoding="utf-8")
    if "_automation_rt" not in t:
        replace(tools_py,
                "from . import weather as _weather  # noqa: F401 - registers the weather tool\n",
                "from . import weather as _weather  # noqa: F401 - registers the weather tool\n"
                "from . import automation_runtime as _automation_rt  # noqa: F401 - registers the automation tools\n")
        replace(tools_py,
                "        names += _phone_ext.NAMES + _phone_fast.NAMES\n",
                "        names += _phone_ext.NAMES + _phone_fast.NAMES + _automation_rt.NAMES\n")

    # 3) Keep the tools in the lean allowlist and the parity inventory.
    replace(package / "lean.py", '    "web_search", "web_fetch", "weather",\n',
            '    "web_search", "web_fetch", "weather",\n'
            '    "phone_agent", "phone_power", "browser", "browser_agent", "adb_connect",\n')
    replace(package / "claude_parity.py",
            '"web_search", "web_fetch", "weather", "tool_search", "memory_recall", "phone", "github",',
            '"web_search", "web_fetch", "weather", "tool_search", "memory_recall", "phone", '
            '"phone_agent", "phone_power", "browser", "browser_agent", "adb_connect", "github",')

    # 4) One routing policy in the prompt, and the new tools named in the inventory.
    lean_md = package / "agent_prompt_lean.md"
    md = lean_md.read_text(encoding="utf-8")
    if "phone_agent for a whole phone task" not in md:
        if PROMPT_TOOLS not in md or POLICY_ANCHOR not in md:
            raise SystemExit("Unsafe patch refused: lean prompt anchors missing for the automation policy")
        md = md.replace(PROMPT_TOOLS, PROMPT_TOOLS_NEW, 1)
        md = md.replace(POLICY_ANCHOR, POLICY + POLICY_ANCHOR, 1)
        lean_md.write_text(md, encoding="utf-8")

    # 5) Open threads pick up the new prompt.
    replace(package / "agent_policy.py", "MARKER = 'MusabAI behavior profile v14'",
            "MARKER = 'MusabAI behavior profile v15'")

    # 6) Run the hermetic tests with the parity suite in CI.
    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .automation_llm_tests import AutomationLlmTests  # noqa: E402,F401 - parity CI\n")
        f.write("from .automation_runtime_tests import AutomationToolsTests  # noqa: E402,F401 - parity CI\n")
