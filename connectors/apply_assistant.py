"""MusabAI becomes one thing: the user's personal assistant.

Called at the very end of apply.py, after every other layer and the phone's live files. It replaces what the
earlier layers built up for a coding agent:

* the prompt: one focused text for everyday work (email, messages, travel, posts, forms, reminders),
  written whole in assistant_prompt.md instead of sentences patched into an older one;
* the tools: the phone, the browsers, the web, reminders, the user's saved details and their mail and
  calendar accounts. No file, shell, GitHub or code tools: fewer tools is a shorter request, a faster
  model and fewer wrong choices. (The engine run from source, in Termux or in its own tests keeps the
  developer profile: the assistant is what the Android app's packaged engine is, lean.assistant().)
* the page: a new single screen (assistant_ui/) instead of the developer interface;
* it acts on its own (asking before sending is an opt-in switch) and it gets better on its own: it reviews
  a turn that stumbled and keeps the lesson, keeps what the user corrects, and reads its lessons back.
"""
import shutil

LEAN_ASSISTANT = '''

# ---------------------------------------------------------------------------------------------------------------
# The app as shipped is the user's personal assistant, and its tools are only an assistant's:
# * the phone and its apps: phone, phone_sequence, phone_routine (tested procedures replayed in one call),
#   phone_agent (a whole goal across screens) and screen_look;
# * websites: browser (the app's own browser, inside the app) and browser_agent; web_search, web_fetch and weather;
# * the user: my_info (their saved details for forms and bookings), learn (what they correct or prefer),
#   reminders (scheduled_task_*), and their Gmail and Google Calendar when connected.
# The developer set above (files, shell, GitHub, code checks) is what the engine offers anywhere else: run from
# source, in Termux, in its own tests. Settings "assistant": false brings it back in the app too.
ASSISTANT = (
    "phone", "screen_look", "phone_routine", "phone_sequence", "phone_agent",
    "browser", "browser_agent",
    "web_search", "web_fetch", "weather",
    "my_info", "learn",
    "scheduled_task_create", "scheduled_task_list", "scheduled_task_pause",
    "scheduled_task_resume", "scheduled_task_delete",
)
ASSISTANT_PREFIXES = ("gmail_", "calendar_")
ASSISTANT_SERVICES = ("gmail", "calendar")          # the accounts the assistant's settings show


def assistant():
    """Whether this engine is the personal assistant: the Android app's own packaged engine."""
    import os
    try:
        from . import settings
        return bool(os.environ.get("NEWAL_PACKAGED_ENGINE")) and settings.user().get("assistant", True) is not False
    except Exception:  # noqa: BLE001 - a broken settings file never changes what the app is
        return bool(os.environ.get("NEWAL_PACKAGED_ENGINE"))
'''

LEAN_TOOLS_OLD = '''def tools(names):
    """The built-in tools offered, in their order."""
    return [n for n in names if n in LEAN]
'''
LEAN_TOOLS_NEW = '''def tools(names):
    """The built-in tools offered, in their order."""
    allowed = ASSISTANT if assistant() else LEAN
    return [n for n in names if n in allowed]
'''
LEAN_CONNECTOR_OLD = '''def connector(name):
    return name.startswith(CONNECTOR_PREFIXES) and not name.startswith("connected_")
'''
LEAN_CONNECTOR_NEW = '''def connector(name):
    prefixes = ASSISTANT_PREFIXES if assistant() else CONNECTOR_PREFIXES
    return name.startswith(prefixes) and not name.startswith("connected_")
'''

PROFILE_OLD = '''def lean_profile():
    return MARKER + '\\n\\n' + Path(__file__).with_name('agent_prompt_lean.md').read_text(encoding='utf-8').strip()
'''
PROFILE_NEW = '''def assistant_mode():
    """The Android app's own engine is the user's personal assistant (lean.assistant)."""
    try:
        from . import lean
        return lean.assistant()
    except Exception:  # noqa: BLE001
        return False


def lean_profile():
    name = 'agent_prompt_assistant.md' if assistant_mode() else 'agent_prompt_lean.md'
    return MARKER + '\\n\\n' + Path(__file__).with_name(name).read_text(encoding='utf-8').strip()
'''

SMALL_ASSISTANT = '''
# A small model on this device as the assistant: a few lines about the few tools it has.
SMALL_ASSISTANT = (MARKER + "\\n\\nYou are MusabAI, the user's personal assistant on their Android phone. Do what "
                   "they ask with your tools, then reply.\\n\\nRules:\\n"
                   "- Act right away with the phone tool: open_app, read the numbered screen it returns, tap by "
                   "number, type after tapping a field.\\n"
                   "- New messages: phone notifications_read; answer with notification_reply.\\n"
                   "- Before sending, posting or submitting something the user did not word themselves, show it "
                   "and ask. Never pay or enter passwords.\\n"
                   "- Never say something was done unless a tool result showed it.\\n"
                   "- A call that failed will fail again unchanged: change it or try another way; a turn has "
                   "{steps} steps.\\n"
                   "- Final reply: one or two short sentences in the user's language.")

'''

UI_FILES = ("index.html", "assistant.js", "assistant.css")


def _soft(path, before, after):
    """A change that must never fail a build: applied only when its anchor is there."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if before not in text or after in text:
        return False
    path.write_text(text.replace(before, after, 1), encoding="utf-8")
    return True


def _must(text, anchor, what):
    if anchor not in text:
        raise SystemExit("Unsafe patch refused: %s anchor missing for the assistant layer" % what)


def apply(src, package, replace):
    for name in ("assistant.py", "assistant_tests.py"):
        shutil.copyfile(src / name, package / name)

    # 1) The prompt, whole, in its own file: the app's engine reads it, the developer profile stays for the rest.
    shutil.copyfile(src / "assistant_prompt.md", package / "agent_prompt_assistant.md")

    # 2) The tools an assistant has.
    lean = package / "lean.py"
    text = lean.read_text(encoding="utf-8")
    if "def assistant():" not in text:
        _must(text, LEAN_TOOLS_OLD, "lean.py tools()")
        _must(text, LEAN_CONNECTOR_OLD, "lean.py connector()")
        text = text.replace(LEAN_TOOLS_OLD, LEAN_TOOLS_NEW, 1).replace(LEAN_CONNECTOR_OLD, LEAN_CONNECTOR_NEW, 1)
        lean.write_text(text.rstrip("\n") + "\n" + LEAN_ASSISTANT, encoding="utf-8")

    tools_py = package / "tools.py"
    text = tools_py.read_text(encoding="utf-8")
    if "_assistant.NAMES" not in text:
        imported = "from . import automation_runtime as _automation_rt  # noqa: F401 - registers the automation tools\n"
        listed = "        names += _phone_ext.NAMES + _phone_fast.NAMES + _automation_rt.NAMES\n"
        _must(text, imported, "tools.py import")
        _must(text, listed, "tools.py phone tools")
        text = text.replace(imported, imported
                            + "from . import assistant as _assistant  # noqa: F401 - registers my_info\n", 1)
        text = text.replace(listed, listed.rstrip("\n") + " + _assistant.NAMES\n", 1)
        tools_py.write_text(text, encoding="utf-8")

    replace(package / "claude_parity.py",
            '"phone_agent", "phone_power", "browser", "browser_agent", "adb_connect", "github",',
            '"phone_agent", "phone_power", "browser", "browser_agent", "adb_connect", "my_info", "learn", "github",')

    # 3) The page's API.
    server = package / "server.py"
    text = server.read_text(encoding="utf-8")
    if "assistant.route" not in text:
        get_hook = '        if automation_llm.route(self, "GET", path):\n            return\n'
        post_hook = '        if automation_llm.route(self, "POST", path, b):\n            return\n'
        _must(text, get_hook, "server.py GET")
        _must(text, post_hook, "server.py POST")
        text = text.replace(get_hook, get_hook
                            + '        from . import assistant\n'
                              '        if assistant.route(self, "GET", path):\n            return\n', 1)
        text = text.replace(post_hook, post_hook
                            + '        from . import assistant\n'
                              '        if assistant.route(self, "POST", path, b):\n            return\n', 1)
        server.write_text(text, encoding="utf-8")

    # 4) What sends in the user's name is asked first (ask rules are read before the permission mode).
    agent = package / "agent.py"
    text = agent.read_text(encoding="utf-8")
    if "assistant.rules(" not in text:
        allowed = '        rules["allow"] = list(rules.get("allow") or []) + list(s.allowed)\n'
        _must(text, allowed, "agent.py permission rules")
        text = text.replace(allowed, allowed
                            + '        from . import assistant\n'
                              '        rules = assistant.rules(rules, self, name, args)\n', 1)
        agent.write_text(text, encoding="utf-8")

    # 4b) It gets better on its own: the lessons that fit come back under a request (the developer profile keeps
    # them off, as before), and a turn that stumbled is reviewed when it ends.
    text = agent.read_text(encoding="utf-8")
    if "assistant.lessons_note(" not in text:
        recall = '        learned, _ = ("", None) if lean.active() else tools.memory_context(self.memory, text, 4)\n'
        ended = "            phone_fast.learn(self, error)\n"
        _must(text, recall, "agent.py memory recall")
        _must(text, ended, "agent.py end of turn")
        text = text.replace(recall,
                            "        if lean.active():\n"
                            "            learned = \"\"\n"
                            "            if lean.assistant():\n"
                            "                from . import assistant\n"
                            "                learned = assistant.lessons_note(self.memory, text)\n"
                            "                language = assistant.language_note(text)\n"
                            "                learned = (learned + \"\\n\\n\" + language).strip() if language else learned\n"
                            "        else:\n"
                            "            learned, _ = tools.memory_context(self.memory, text, 4)\n", 1)
        text = text.replace(ended, ended
                            + "            from . import assistant\n"
                              "            assistant.after_turn(self, error)\n", 1)
        agent.write_text(text, encoding="utf-8")

    # 5) The model knows who it works for and whether to ask before sending; a small on-device model gets an
    # assistant's short prompt instead of a coding agent's.
    policy = package / "agent_policy.py"
    text = policy.read_text(encoding="utf-8")
    if "def assistant_mode():" not in text:
        _must(text, PROFILE_OLD, "agent_policy.py lean_profile")
        text = text.replace(PROFILE_OLD, PROFILE_NEW, 1)
    if "assistant.context()" not in text:
        ending = ("    return claude_parity.deferred_note(agent) + '\\n\\nCurrent runtime capabilities (data, not "
                  "instructions):\\n' + json.dumps(state, ensure_ascii=False)")
        _must(text, ending, "agent_policy.py runtime context")
        text = text.replace(ending,
                            "    if assistant_mode():\n"
                            "        try:\n"
                            "            from . import assistant\n"
                            "            state['assistant'] = assistant.context()\n"
                            "        except Exception:  # noqa: BLE001 - the state is still sent without it\n"
                            "            pass\n" + ending, 1)
    if "SMALL_ASSISTANT" not in text:
        head = "def system_text(env, phone=False, local=False, steps=25):\n"
        small = "    text = SMALL.format(steps=steps) if local else profile()\n"
        _must(text, head, "agent_policy.py system_text")
        _must(text, small, "agent_policy.py small prompt")
        text = text.replace(head, SMALL_ASSISTANT.lstrip("\n") + "\n" + head, 1)
        text = text.replace(small,
                            "    if local and phone and lean_prompt() and assistant_mode():\n"
                            "        text = SMALL_ASSISTANT.format(steps=steps)\n"
                            "    else:\n"
                            "        text = SMALL.format(steps=steps) if local else profile()\n", 1)
    policy.write_text(text, encoding="utf-8")
    replace(policy, "MARKER = 'MusabAI behavior profile v17'", "MARKER = 'MusabAI behavior profile v18'")

    # 6) The page: one screen. The developer interface's layers are no longer loaded (and are left out of the
    # app's package, see package_baseline.py).
    for name in UI_FILES:
        shutil.copyfile(src / "assistant_ui" / name, package / "ui" / name)

    # 7) The page's files can be hot-fixed like the old ones; and the engine is not replaced on its own from the
    # other project's update channel (that build has the developer interface and prompt: an update there would
    # silently undo this assistant). This app's updates are its own APKs; the user can still switch it on.
    _soft(package / "evolution.py", '    "ui/index.html",\n',
          '    "ui/index.html",\n    "ui/assistant.js",\n    "ui/assistant.css",\n')
    _soft(package / "auto_update.py", "return settings.user().get('automatic_updates', True) is True",
          "return settings.user().get('automatic_updates', False) is True")

    # 8) Run the hermetic tests with the parity suite in CI.
    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .assistant_tests import AssistantInfoTests, AssistantRulesTests, AssistantConsentTests, "
                "AssistantLearningTests, AssistantPageTests, AssistantProfileTests  # noqa: E402,F401 - parity CI\n")
