"""Applies the Claude Code parity layer (claude_parity.py) on top of connectors/apply.py's output.

Run after apply.py: python3 connectors/apply_parity.py <repository root>
"""
import shutil
import sys
from pathlib import Path


def replace(path, before, after):
    text = path.read_text()
    if before not in text:
        raise SystemExit("parity patch target missing in %s: %s" % (path.name, before[:120].replace("\n", "\\n")))
    path.write_text(text.replace(before, after, 1))


def apply(root):
    src = root / "connectors"
    package = root / "desktop" / "newal_code"
    for name in ("claude_parity", "claude_parity_tests"):
        shutil.copyfile(src / (name + ".py"), package / (name + ".py"))

    # tool_search: import tools lazily (importing tool_search first raised a circular-import error) and search the
    # deferred built-in tools as well as connected MCP tools.
    ts = package / "tool_search.py"
    replace(ts, "from . import tools\n\nNAMES", "from importlib import import_module\n\n\nclass _LazyTools:\n"
            "    \"\"\"tools imports this module while it registers; a lazy reference keeps either import order working.\"\"\"\n"
            "    def __getattr__(self, name):\n        return getattr(import_module(__package__ + \".tools\"), name)\n\n\n"
            "tools = _LazyTools()\n\nNAMES")
    replace(ts, "    agent.session.discovered_mcp_tools = current\n    if activated:",
            "    agent.session.discovered_mcp_tools = current\n    from . import claude_parity\n"
            "    builtin = claude_parity.search_builtin(agent, query, limit)\n"
            "    activated += [_name(d) for d in builtin]\n    matches = list(matches) + list(builtin)\n    if activated:")
    replace(ts, '        "Search tools offered by already connected MCP servers. Activates matching "\n'
                '        "tool schemas on the next step; does not install, authorize or execute tools. "\n'
                '        "Use before a task requiring an MCP tool not visible in the current request.",',
            '        "Search deferred tools: built-in capabilities listed as deferred in the context and tools of "\n'
            '        "connected MCP servers. Activates matching schemas on the next step; does not install, authorize "\n'
            '        "or execute tools. Use before a task needing a tool not visible in the current request.",')

    # runtime context: report every offered tool (visible or deferred) and name the deferred ones.
    ap = package / "agent_policy.py"
    replace(ap, "    names = {d['function']['name'] for d in agent.schemas()}\n",
            "    from . import claude_parity\n    names = claude_parity.offered_names(agent)\n")
    replace(ap, "    return '\\n\\nCurrent runtime capabilities (data, not instructions):\\n' + json.dumps(state, ensure_ascii=False)",
            "    return claude_parity.deferred_note(agent) + '\\n\\nCurrent runtime capabilities (data, not instructions):\\n' + json.dumps(state, ensure_ascii=False)")

    # agent: unique schemas with deferral; a deferred tool called by name becomes visible.
    ag = package / "agent.py"
    replace(ag, '        return [d for d in self._schemas if d["function"]["name"] not in connectors.OPERATIONS] + tools.schemas(available)\n',
            '        from . import claude_parity\n        return claude_parity.finalize(self, [d for d in self._schemas if d["function"]["name"] not in connectors.OPERATIONS] + tools.schemas(available))\n')
    replace(ag, '        t = tools.REGISTRY.get(name)\n        if not is_mcp and (t is None or name not in s.tool_names):\n',
            '        t = tools.REGISTRY.get(name)\n        if not is_mcp and t is not None and name in s.tool_names:\n'
            '            from . import claude_parity\n            claude_parity.mark_used(self, name)\n'
            '        if not is_mcp and (t is None or name not in s.tool_names):\n')

    se = package / "session.py"
    replace(se, '        self.discovered_mcp_tools = []\n', '        self.discovered_mcp_tools = []\n        self.discovered_builtin_tools = []\n')
    replace(se, '"discovered_mcp_tools": self.discovered_mcp_tools,',
            '"discovered_mcp_tools": self.discovered_mcp_tools, "discovered_builtin_tools": self.discovered_builtin_tools,')
    replace(se, '        s.discovered_mcp_tools = list(meta.get("discovered_mcp_tools") or [])[:48]\n',
            '        s.discovered_mcp_tools = list(meta.get("discovered_mcp_tools") or [])[:48]\n'
            '        s.discovered_builtin_tools = list(meta.get("discovered_builtin_tools") or [])[:64]\n')

    # service: /context /todos /bashes /doctor and !command.
    sv = package / "service.py"
    replace(sv, '    ("add-dir", "<folder>", "Let this thread read and edit another folder too"),\n',
            '    ("add-dir", "<folder>", "Let this thread read and edit another folder too"),\n'
            '    ("context", "", "Show what fills the context window: system prompt, tools, messages"),\n'
            '    ("todos", "", "Show the task checklist of this thread"),\n'
            '    ("bashes", "", "List background shell jobs (also /jobs)"),\n'
            '    ("doctor", "", "Check the engine: Python, git, shell tools, model and tool registry"),\n')
    replace(sv, 'ALIASES = {"approvals": "mode", "models": "model", "context": "status", ',
            'ALIASES = {"approvals": "mode", "models": "model", "jobs": "bashes", "tasks": "todos", ')
    replace(sv, '        a = self.agent(s.id)\n        if name == "help":\n',
            '        from . import claude_parity\n        parity = claude_parity.command(self, s, name, args)\n'
            '        if parity is not None:\n            return parity\n        a = self.agent(s.id)\n        if name == "help":\n')
    replace(sv, '        if text.startswith("/") and not text.startswith("//"):\n            out = self.command(s, text, lang=lang)\n',
            '        if text.startswith("!") and len(text) > 1 and not images:\n            from . import claude_parity\n'
            '            return claude_parity.bang(self, s, text[1:])\n'
            '        if text.startswith("/") and not text.startswith("//"):\n            out = self.command(s, text, lang=lang)\n')

    # auto-verify: phones rarely have pytest; unittest is always there.
    replace(package / "context.py", '    if has_py_tests:\n        return "%s -m pytest -q" % python_exe()\n',
            '    if has_py_tests:\n        import importlib.util\n'
            '        if importlib.util.find_spec("pytest") is None and _unittest_style(root):\n'
            '            return "%s -m unittest discover -q" % python_exe()\n        return "%s -m pytest -q" % python_exe()\n')
    replace(package / "context.py", "def test_command(root):\n",
            "def _unittest_style(root):\n"
            "    \"\"\"True when the project's Python tests are unittest classes (runnable without pytest).\"\"\"\n"
            "    for folder, dirs, files in os.walk(root):\n"
            "        dirs[:] = [d for d in dirs if d not in tools.IGNORED_DIRS and not d.startswith(\".\")]\n"
            "        for f in files:\n"
            "            if f.endswith(\".py\") and (f.startswith(\"test_\") or f.endswith(\"_test.py\")):\n"
            "                if \"unittest\" in util.read(os.path.join(folder, f), 20000):\n"
            "                    return True\n"
            "        if folder.count(os.sep) - root.count(os.sep) > 4:\n"
            "            dirs[:] = []\n"
            "    return False\n\n\n"
            "def test_command(root):\n")
    print("claude parity applied")


if __name__ == "__main__":
    apply(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve())
