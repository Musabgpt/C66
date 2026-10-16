"""Phone tasks on a free model: what broke them in builds #382/#383.

* A connected service's tools (up to 512 per MCP service) were sent with every request; with them the fixed part of a
  request was ~100K tokens, so the engine compacted after every step and lost the task each time. They are deferred
  now (claude_parity), and a summary is skipped when the fixed part, not the conversation, fills the window.
* Calls an API model writes into its text in the Qwen3-Coder layout (<tool_call><function=phone><parameter=action>
  open_app</parameter>...) are run instead of shown; other names for the phone's arguments and actions are mapped
  (package_name -> name, settings_page -> page, open_settings -> settings, the screen_look action -> the tool).
* Thinking aloud in English next to a tool call ("The user wants me to...") is shown as reasoning, not as the answer.
* When the accessibility service is off or stopped, the phone tool says so once, clearly, and the agent stops asking
  the screen instead of retrying until the circuit breaker trips.
"""


def apply(src, package, replace):
    rp = package / "repair.py"
    replace(rp, '                 "set_timer": "timer"}\n',
            '                 "set_timer": "timer", "open_settings": "settings", "settings_page": "settings",\n'
            '                 "open_setting": "settings", "get_screen": "screen", "screen_read": "screen",\n'
            '                 "look": "screen", "view_screen": "screen", "input_text": "type", "type_text": "type",\n'
            '                 "enter_text": "type", "click": "tap", "press": "tap", "touch": "tap",\n'
            '                 "wait_seconds": "wait", "sleep": "wait", "home": "key", "back": "key"}\n'
            'PHONE_ARGS = {"name": ("package_name", "package", "app", "app_name", "application", "app_package", "key_name",\n'
            '                       "button"),\n'
            '              "page": ("settings_page", "setting", "section", "settings"),\n'
            '              "text": ("message", "value", "input", "content", "query"),\n'
            '              "item": ("index", "element", "item_id", "number_on_screen"),\n'
            '              "url": ("link", "uri", "href")}\n'
            'NUMERIC = {"item", "x", "y", "hour", "minute", "seconds", "level", "offset", "limit", "timeout"}\n')
    replace(rp, '    if base == "job" and isinstance(args.get("id"), int):\n',
            '    if base == "phone":\n'
            '        base, args = _phone_args(args, known)\n'
            '    if base == "job" and isinstance(args.get("id"), int):\n')
    replace(rp, '# ------------------------------------------------------------------ calls in the text\n',
            '''def _phone_args(args, known):
    """The phone's own names for an action and its arguments (models trained on other agents use theirs)."""
    action = str(args.get("action") or "").strip().lower().replace("-", "_").replace(" ", "_")
    from . import phone
    if action not in phone.ACTIONS:
        action = PHONE_ACTIONS.get(action, action)
    if action in ("screen_look", "look_at_screen", "describe_screen") and (known is None or "screen_look" in known):
        question = args.get("question") or args.get("text") or args.get("query") or ""
        return "screen_look", ({"question": str(question)} if question else {})
    if action == "key" and not args.get("name"):
        raw = str(args.get("action") or "").strip().lower()
        if raw in ("home", "back"):
            args["name"] = raw
    args["action"] = action
    for good, others in PHONE_ARGS.items():
        if args.get(good) not in (None, ""):
            continue
        for o in others:
            if args.get(o) not in (None, ""):
                args[good] = args.pop(o)
                break
    for k in list(args):
        if k in NUMERIC and isinstance(args[k], str) and re.fullmatch(r"\\s*-?\\d+\\s*", args[k]):
            args[k] = int(args[k])
    return "phone", args


def _xml_params(body):
    """Arguments in the Qwen3-Coder layout: <parameter=action>open_app</parameter><parameter=name>...</parameter>."""
    pairs = re.findall(r"<parameter=([\\w.-]+)>\\s*(.*?)\\s*</parameter>", body or "", re.S)
    if not pairs:
        return None
    out = {}
    for key, value in pairs:
        value = value.strip()
        parsed = loads(value) if value[:1] in "{[" else None
        out[key] = parsed if parsed is not None else value
    return out


def tagged_call(text):
    """A call written as tagged text (<tool_call>, <function=...>): never an example in an answer."""
    return bool(re.search(r"<tool_call>|<function=[\\w.-]+>", text or ""))


def strip_calls(text):
    """The text without tagged calls, whole or cut off at the end."""
    t = re.sub(r"(?s)<tool_call>.*?(?:</tool_call>|$)", "", text or "")
    t = re.sub(r"(?s)<function=[\\w.-]+>.*?(?:</function>|$)", "", t)
    return t.strip()


# ------------------------------------------------------------------ calls in the text
''')
    replace(rp, '        args = loads(m.group(2))\n        take({"name": m.group(1), "arguments": args if args is not None else {"__main__": m.group(2)}}, m.span())\n',
            '        args = _xml_params(m.group(2))\n        args = args if args is not None else loads(m.group(2))\n'
            '        take({"name": m.group(1), "arguments": args if args is not None else {"__main__": m.group(2)}}, m.span())\n')
    replace(rp, '    return calls, rest.strip()\n',
            '    if found:\n        rest = re.sub(r"<tool_call>\\s*</tool_call>|</?tool_call>", "", rest)\n'
            '    return calls, rest.strip()\n')

    ag = package / "agent.py"
    replace(ag, '                if not comp.tool_calls and comp.content and client.on_device:\n',
            '                if not comp.tool_calls and comp.content and (client.on_device or repair.tagged_call(comp.content)):\n')
    replace(ag, '        if shown(comp.content):\n            self.emit({"type": "assistant", "text": shown(comp.content), "final": not comp.tool_calls})\n        return comp\n',
            '        if shown(comp.content):\n            from . import claude_parity\n'
            '            claude_parity.show_reply(self, comp, shown(comp.content))\n        return comp\n')
    replace(ag, '        if len(s.messages) < 4:\n            return False\n',
            '        if len(s.messages) < 4:\n            return False\n'
            '        if not force:\n            from . import claude_parity\n'
            '            if not claude_parity.compaction_helps(self, ctx_len, limit):\n                return False\n')

    tl = package / "tools.py"
    replace(tl, '            raise ToolError("%s\\n%s" % (e, phone.WRONG_TOOL) if action in ("type", "tap", "screen") else str(e))\n',
            '            msg = str(e)\n'
            '            if "ACCESSIBILITY_" in msg or "accessibility service" in msg:\n'
            '                raise ToolError(msg.replace("ACCESSIBILITY_OFF: ", "").replace("ACCESSIBILITY_STOPPED: ", "")\n'
            '                                + "\\nStop here: tell the user, in their language, exactly what to switch on "\n'
            '                                  "and that you will continue when they say it is on. Opening apps and links "\n'
            '                                  "still works without it.")\n'
            '            raise ToolError("%s\\n%s" % (e, phone.WRONG_TOOL) if action in ("type", "tap", "screen") else msg)\n')

    import shutil
    shutil.copyfile(src / "ext_phonefix_tests.py", package / "ext_phonefix_tests.py")
    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_phonefix_tests import PhoneFixTests  # noqa: E402,F401 - run with the parity tests in CI\n")

    # The app (built after this step): the screen actions wait for a rebinding accessibility service and say whether
    # it is off or stopped (A11y.java), instead of one message for both.
    phone_java = package.parent.parent / "android-lite/app/src/main/java/dev/newal/code/lite/Phone.java"
    if phone_java.exists():
        text = phone_java.read_text(encoding="utf-8")
        if "PhoneControlService.need()" in text:
            phone_java.write_text(text.replace("PhoneControlService.need()", "A11y.need(ctx)"), encoding="utf-8")
