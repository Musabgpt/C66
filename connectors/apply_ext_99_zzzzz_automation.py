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
                    "browser and browser_agent for web work in a background browser (Pydoll and Browser Use "
                    "over Chromium); ")

POLICY_ANCHOR = "- Otherwise start with phone open_app"
POLICY = (
    "- Choosing a phone engine: one known step is fastest with the phone tool, phone_routine or a task-memory tm: "
    "note. For a whole goal that spans screens or apps (\"book a ride\", \"reply to the latest message and like "
    "it\"), call phone_agent once with the goal, naming the exact app, person and text: it reads the real screen "
    "and taps, types and swipes until it is done, then reports. If it reports not completed, look at the screen "
    "and finish by hand. phone_power (uiautomator2) is only for what these cannot reach, after adb_connect has "
    "paired this phone's own wireless ADB; while one of its calls runs Android suspends the accessibility tools, "
    "and it brings them back before returning. If it reports accessibility suspended, stop and tell the user to "
    "switch MusabAI's accessibility service off and on.\n"
    "- Web work in the background (reading a page, searching a site, a form that needs no login of the user's): "
    "browser op=open, then op=extract to read the page and see the names of its fields, buttons and links, then "
    "click or type by those names (type with submit=true for a search box). For a whole web task (\"find the "
    "cheapest flight and tell me\") call browser_agent once with the site and what to return. web_search and "
    "web_fetch are enough when a result or one page answers it; the phone's Chrome is for a page the user should "
    "see or one that needs their own logged-in session.\n"
    "- These engines run on the user's device with the free model pool; nothing needs a key. The first use of "
    "phone_agent, browser or browser_agent installs its engine in Termux (a few minutes, once): say so when it "
    "takes long. Fewest steps, verify the real result, answer in one line in the user's language.\n"
)

CHROME_BEFORE = "For websites, open web URLs in the phone's Chrome."
CHROME_AFTER = ("For websites the user should see, or that need their own logged-in session, open web URLs in the "
                "phone's Chrome.")


def _soft(path, before, after):
    """A change that improves wording or wiring but must never fail a build: applied only
    when its anchor is there."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if before not in text or after in text:
        return False
    path.write_text(text.replace(before, after, 1), encoding="utf-8")
    return True


def apply(src, package, replace):
    for name in ("automation_llm.py", "automation_llm_tests.py", "automation_server.py",
                 "automation_server_tests.py", "automation_runtime.py", "automation_runtime_tests.py",
                 "automation_requirements.txt", "own_browser.py", "own_browser_tests.py"):
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
    # The engine says where it really listens (the port can differ from the default), so the
    # automation server calls /v1/free at the right address.
    _soft(server, '    httpd.base_url = "http://%s:%d/" % (host, httpd.server_address[1])\n',
          '    httpd.base_url = "http://%s:%d/" % (host, httpd.server_address[1])\n'
          '    os.environ["MUSAB_ENGINE_BASE"] = httpd.base_url.rstrip("/")\n')

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
    _soft(lean_md, CHROME_BEFORE, CHROME_AFTER)

    # 5) Open threads pick up the new prompt.
    replace(package / "agent_policy.py", "MARKER = 'MusabAI behavior profile v14'",
            "MARKER = 'MusabAI behavior profile v17'")

    # 6) When a mode asks before a phone action, the question names what the new tools will do
    # (they have no "action" argument: the question read "phone: ?").
    permissions = package / "permissions.py"
    _soft(permissions,
          'return Decision(ASK, "phone: %s%s" % (args.get("action") or "?", _phone_detail(args)))',
          'return Decision(ASK, "phone: %s%s" % (args.get("action") or args.get("op") or tool, _phone_detail(args)))')
    _soft(permissions,
          'for k in ("name", "url", "text", "number", "page", "item", "direction"):',
          'for k in ("name", "url", "text", "number", "page", "item", "direction", "goal", "package", "address"):')

    # 7) Run the hermetic tests with the parity suite in CI.
    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .automation_llm_tests import AutomationLlmTests  # noqa: E402,F401 - parity CI\n")
        f.write("from .automation_runtime_tests import AutomationToolsTests, AutomationEnsureTests  # noqa: E402,F401\n")
        f.write("from .automation_server_tests import Uiautomator2Tests, PhoneCallTests, ServerTests, TargetTests  # noqa: E402,F401\n")
        f.write("from .own_browser_tests import OwnBrowserTests  # noqa: E402,F401\n")
