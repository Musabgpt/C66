"""A tool call a hosted model writes as text in the attribute layout is run, not shown (found on the phone with the free
public model, 29/10): <tool_call name="functions.phone"><arguments>{"action":"battery"}</arguments></tool_call>.

repair.py already turns <tool_call>{...}</tool_call> and <function=name> into calls, but only recognised a hosted
model's text call when it began with a bare "<tool_call>", so this one was printed in the chat, nothing ran and the
person got no answer. The "functions." / "default_api." prefix some models put on a tool's name is dropped too."""
import shutil


def apply(src, package, replace):
    rp = package / "repair.py"
    replace(rp, '    return bool(re.search(r"<tool_call>|<function=[\\w.-]+>", text or ""))\n',
            '    return bool(re.search(r"<tool_call(?:>|\\s+name=)|<function=[\\w.-]+>", text or ""))\n')
    replace(rp, '    t = re.sub(r"(?s)<tool_call>.*?(?:</tool_call>|$)", "", text or "")\n',
            '    t = re.sub(r"(?s)<tool_call(?:>|\\s[^>]*>).*?(?:</tool_call>|$)", "", text or "")\n')
    replace(rp, '    base = str(name or "").strip()\n',
            '    base = str(name or "").strip()\n'
            '    # "functions.phone" (OpenAI\'s namespace), "default_api.read" (Gemini\'s): the tool\'s own name\n'
            '    base = re.sub(r"^(?:functions|default_api|tools)\\.", "", base)\n')
    replace(rp, '    for m in re.finditer(r"<function=([\\w.-]+)>\\s*(.*?)\\s*(?:</function>|$)", text, re.S):\n',
            '    for m in re.finditer(r"<tool_call\\s+name=[\\"\']?([\\w.-]+)[\\"\']?\\s*>\\s*(.*?)\\s*(?:</tool_call>|$)", text,\n'
            '                         re.S):\n'
            '        # the attribute layout: <tool_call name="phone"><arguments>{...}</arguments></tool_call>\n'
            '        body = m.group(2)\n'
            '        inner = re.search(r"<arguments>\\s*(.*?)\\s*(?:</arguments>|$)", body, re.S)\n'
            '        raw = inner.group(1) if inner else body\n'
            '        args = _xml_params(raw)\n'
            '        args = args if args is not None else loads(raw)\n'
            '        take({"name": m.group(1), "arguments": args if args is not None else {"__main__": raw}}, m.span())\n'
            '    for m in re.finditer(r"<function=([\\w.-]+)>\\s*(.*?)\\s*(?:</function>|$)", text, re.S):\n')
    shutil.copyfile(src / "ext_textcalls_tests.py", package / "ext_textcalls_tests.py")
    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_textcalls_tests import TextCallTests  # noqa: E402,F401 - run with the parity tests in CI\n")
