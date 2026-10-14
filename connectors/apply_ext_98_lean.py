"""MusabAI on the phone, focused on Termux, GitHub, VS Code, Chrome and the phone (lean.py), with fast phone use and
routines learned from what worked (phone_fast.py). Nothing is deleted: the other tools are just not offered on the
phone, and settings "lean": false brings them back."""
import shutil


def apply(src, package, replace):
    for name in ("lean.py", "phone_fast.py", "agent_prompt_lean.md", "ext_lean_tests.py"):
        shutil.copyfile(src / name, package / name)

    # the prompt: on the phone, the focused one (agent_prompt_lean.md); v11 makes threads pick it up
    ap = package / "agent_policy.py"
    replace(ap, "MARKER = 'MusabAI behavior profile v10'", "MARKER = 'MusabAI behavior profile v11'")
    replace(ap, '    """The fixed system prompt: the core, or SMALL for a model on this device, then the environment."""\n',
            '    """The fixed system prompt: the core, or SMALL for a model on this device, then the environment."""\n'
            '    if phone and not local and lean_prompt():\n'
            '        # on the phone: the focused prompt for the four tools and the phone (lean.py), the phone note in it\n'
            '        return lean_profile() + "\\n\\nEnvironment: {os}; shell: {shell}.".format(**env)\n')
    replace(ap, "def stale_builtin(text):\n",
            "def lean_prompt():\n"
            "    try:\n"
            "        from . import settings\n"
            "        return bool(settings.user().get('lean', True))\n"
            "    except Exception:  # noqa: BLE001\n"
            "        return False\n\n\n"
            "def lean_profile():\n"
            "    return MARKER + '\\n\\n' + Path(__file__).with_name('agent_prompt_lean.md').read_text(encoding='utf-8').strip()\n\n\n"
            "def stale_builtin(text):\n")
    # the lean set is small: every tool is sent, nothing is deferred
    replace(package / "claude_parity.py", '    if cfg.get("defer_tools") is False:\n        return False\n',
            '    if cfg.get("defer_tools") is False:\n        return False\n'
            '    from . import lean\n'
            '    if lean.active():\n'
            '        return False\n')

    tl = package / "tools.py"
    replace(tl, "_phone_ext.install()\n",
            "_phone_ext.install()\nfrom . import phone_fast as _phone_fast\n_phone_fast.install()\n")
    replace(tl, "        names += _phone_ext.NAMES\n", "        names += _phone_ext.NAMES + _phone_fast.NAMES\n")

    ag = package / "agent.py"
    # the tools offered: the lean set on the phone (a sub-agent with its own list keeps it)
    replace(ag, '        if self.depth >= MAX_DEPTH and "task" in names:\n            names.remove("task")\n'
                '        return names\n',
            '        if self.depth >= MAX_DEPTH and "task" in names:\n            names.remove("task")\n'
            '        from . import lean\n'
            '        if not (self.agent_def and self.agent_def.get("tools")) and lean.active():\n'
            '            names = lean.tools(names)\n'
            '        return names\n')
    # no MCP servers, and of the connected services only GitHub and Termux
    replace(ag, '                available_mcp = self.mcp.schemas()\n',
            '                from . import lean\n'
            '                available_mcp = [] if lean.active() else self.mcp.schemas()\n')
    replace(ag, '        available = connectors.names()\n',
            '        available = connectors.names()\n'
            '        from . import lean\n'
            '        if lean.active():\n'
            '            available = [n for n in available if lean.connector(n)]\n')
    # a saved phone routine that fits the request comes with it; the general lessons stay out on the phone
    replace(ag, '        learned, _ = tools.memory_context(self.memory, text, 4)\n',
            '        from . import lean, phone_fast\n'
            '        routine = phone_fast.note(self, text) if self.depth == 0 else ""\n'
            '        if routine:\n'
            '            parts.append(routine)\n'
            '        learned, _ = ("", None) if lean.active() else tools.memory_context(self.memory, text, 4)\n')
    # at the end of a turn that ended well, its phone steps become a routine
    replace(ag, '            phone_ext.finished(self, answer, seconds, error, changes)\n',
            '            phone_ext.finished(self, answer, seconds, error, changes)\n'
            '            from . import phone_fast\n'
            '            phone_fast.learn(self, error)\n')

    # the app sends the new screen with every action that changes it (A11y.withScreen), built after this step
    phone_java = package.parent.parent / "android-lite/app/src/main/java/dev/newal/code/lite/Phone.java"
    if phone_java.exists():
        text = phone_java.read_text(encoding="utf-8")
        for before, after in (
                ("return A11y.need(ctx).tap(a);", "return A11y.withScreen(A11y.need(ctx).tap(a), false);"),
                ("return A11y.need(ctx).type(a);", "return A11y.withScreen(A11y.need(ctx).type(a), false);"),
                ('return A11y.need(ctx).swipe(a.optString("direction", "down"), "scroll".equals(action));',
                 'return A11y.withScreen(A11y.need(ctx).swipe(a.optString("direction", "down"), '
                 '"scroll".equals(action)), false);'),
                ('return A11y.need(ctx).key(a.optString("name"));',
                 'return A11y.withScreen(A11y.need(ctx).key(a.optString("name")), true);'),
                ('return openApp(ctx, a.optString("name"));',
                 'return A11y.withScreen(openApp(ctx, a.optString("name")), true);'),
                ('return start(ctx, safeUrl(ctx, a.optString("url")),\n                        "opened " + a.optString("url"));',
                 'return A11y.withScreen(start(ctx, safeUrl(ctx, a.optString("url")),\n'
                 '                        "opened " + a.optString("url")), true);')):
            if before not in text:
                raise SystemExit("Unsafe patch refused: %s not found in Phone.java" % before)
            text = text.replace(before, after, 1)
        phone_java.write_text(text, encoding="utf-8")

    # the interface: only Termux, GitHub, VS Code, Chrome (Preview) and the chat
    for name in ("lean_ui.js", "lean_ui.css"):
        shutil.copyfile(src / name, package / "ui" / name)
    index = package / "ui/index.html"
    replace(index, '<link rel="stylesheet" href="ext_review.css">\n',
            '<link rel="stylesheet" href="ext_review.css">\n<link rel="stylesheet" href="lean_ui.css">\n')
    replace(index, '<script src="ext_review.js"></script>\n',
            '<script src="ext_review.js"></script>\n<script src="lean_ui.js"></script>\n')

    # DeepSeek retired deepseek-chat and deepseek-reasoner (24 July 2026): its models are deepseek-v4-flash and
    # deepseek-v4-pro, with a 1M window. "The key works, but DeepSeek lists no model" came from the old names. A key
    # whose list has none of the known names still connects, with the chat models it does list.
    cn = package / "connect.py"
    replace(cn, '        "base_url": "https://api.deepseek.com/v1", "context": 128000,\n'
                '        "prefer": [r"^deepseek-chat$", r"^deepseek-reasoner$"],\n'
                '        "fallback": ["deepseek-chat", "deepseek-reasoner"],\n',
            '        "base_url": "https://api.deepseek.com/v1", "context": 1000000,\n'
            '        "prefer": [r"^deepseek-v(\\d+(?:\\.\\d+)?)-flash$", r"^deepseek-v(\\d+(?:\\.\\d+)?)-pro$",\n'
            '                   r"^deepseek-chat$", r"^deepseek-reasoner$"],\n'
            '        "fallback": ["deepseek-v4-flash", "deepseek-v4-pro"],\n')
    replace(cn, '        if n not in out and (not names or n in names):\n            out.append(n)\n    return out[:2]\n',
            '        if n not in out and (not names or n in names):\n            out.append(n)\n'
            '    if not out:\n'
            '        # none of the names we know: the provider renamed its models; its own chat models still work\n'
            '        skip = re.compile(r"embed|whisper|tts|audio|image|moderation|rerank|guard", re.I)\n'
            '        out = [n for n in names if n and not skip.search(n)][:2]\n'
            '    return out[:2]\n')

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_lean_tests import LeanTests  # noqa: E402,F401 - run with the parity tests in CI\n")
