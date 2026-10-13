"""Cursor-style workspace, applied by apply_parity.py: Agent/Plan/Ask modes, file previews in the chat, VS Code
(code-server) from Termux, more free providers (Zhipu GLM-4.7-Flash, Mistral Devstral), working rules, and
keep-alive (termux-wake-lock) for long tasks."""
import shutil

FREE_PROVIDERS = '''    {
        "id": "zhipu/glm-4.7-flash-free",
        "name": "Zhipu GLM-4.7-Flash Free",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
        "provider": "openai",
        "api_key_envs": ["ZHIPU_API_KEY", "ZAI_API_KEY"],
        "secret_id": "zhipu",
        "free": True,
        "capabilities": ["text", "tools", "streaming", "json", "reasoning", "long_context", "coding"],
    },
    {
        "id": "mistral/devstral-free",
        "name": "Mistral Devstral (Experiment plan)",
        "base_url": "https://api.mistral.ai/v1",
        "model": "devstral-small-latest",
        "provider": "openai",
        "api_key_envs": ["MISTRAL_API_KEY"],
        "secret_id": "mistral",
        "free": True,
        "capabilities": ["text", "tools", "streaming", "json", "long_context", "coding"],
    },
]
'''

VSCODE = r"""# VS Code (code-server) for NewAl Code: newal-vscode start | stop | status (installed on first start).
cat > "$PREFIX/bin/newal-vscode" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
PORT=8780
LOG="$HOME/.newal-code/vscode.log"
up() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/healthz"; }
case "${1:-start}" in
  start)
    if up; then echo "VS Code runs: http://127.0.0.1:$PORT"; exit 0; fi
    if ! command -v code-server > /dev/null 2>&1; then
      echo "Installing VS Code (code-server) once; this takes a few minutes..."
      yes | pkg install -y tur-repo && yes | pkg install -y code-server || { echo "code-server could not be installed (see pkg's message above)"; exit 1; }
    fi
    command -v termux-wake-lock > /dev/null 2>&1 && termux-wake-lock
    mkdir -p "$HOME/projects" "$HOME/.newal-code"
    # Its own session, so it keeps running when this Termux window closes.
    set -- code-server --auth none --bind-addr "127.0.0.1:$PORT" --disable-telemetry --disable-update-check "$HOME/projects"
    if command -v setsid > /dev/null 2>&1; then
      setsid nohup "$@" < /dev/null >> "$LOG" 2>&1 &
    elif command -v python > /dev/null 2>&1; then
      python -c 'import subprocess, sys; subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=open(sys.argv[1], "ab"), stderr=subprocess.STDOUT, start_new_session=True)' "$LOG" "$@"
    else
      nohup "$@" < /dev/null >> "$LOG" 2>&1 &
    fi
    for i in $(seq 1 90); do up && break; sleep 1; done
    if up; then echo "VS Code runs: go back to the app."; else echo "VS Code did not start: $LOG"; exit 1; fi ;;
  stop) pkill -f "code-server.*127.0.0.1:$PORT" && echo "stopped" ;;
  status) if up; then echo "running"; else echo "stopped"; fi ;;
  *) echo "newal-vscode start | stop | status"; exit 2 ;;
esac
SH
chmod 700 "$PREFIX/bin/newal-vscode"
# Let the app start it by itself next time (Termux's RUN_COMMAND).
mkdir -p "$HOME/.termux"
grep -qs '^ *allow-external-apps *= *true' "$HOME/.termux/termux.properties" || echo 'allow-external-apps = true' >> "$HOME/.termux/termux.properties"
command -v termux-reload-settings > /dev/null 2>&1 && termux-reload-settings || true
# Long tasks keep running with the screen off (Termux's wake lock; release it with termux-wake-unlock).
command -v termux-wake-lock > /dev/null 2>&1 && termux-wake-lock || true
"""


def apply(src, package, replace):
    for name in ("cursor_ui.js", "cursor_ui.css"):
        shutil.copyfile(src / name, package / "ui" / name)
    ui = package / "ui"
    replace(ui / "index.html", '<link rel="stylesheet" href="connectors.css">',
            '<link rel="stylesheet" href="connectors.css">\n<link rel="stylesheet" href="cursor_ui.css">')
    replace(ui / "index.html", '<script src="automatic_updates.js"></script>',
            '<script src="automatic_updates.js"></script>\n<script src="cursor_ui.js"></script>')
    app = ui / "app.js"
    replace(app, '    text = (text != null ? text : $("#input").value).trim();\n',
            '    text = (text != null ? text : $("#input").value).trim();\n'
            '    if (window.MusabCursor) text = window.MusabCursor.transform(text);\n')
    replace(app, '        const rec = X.items[ev.id];\n        const m = ev.meta || {};\n',
            '        const rec = X.items[ev.id];\n        const m = ev.meta || {};\n'
            '        if (rec && ev.ok && window.MusabCursor) window.MusabCursor.decorate(rec.el, ev);\n')

    sv = package / "server.py"
    replace(sv, '            if path == "/api/file":\n',
            '            if path == "/api/file/raw":\n                from . import claude_parity\n'
            '                try:\n                    data, mime = claude_parity.raw_file(q.get("root") or "", q.get("path") or "")\n'
            '                except ValueError as e:\n                    return self._json({"error": str(e)}, 404)\n'
            '                return self._text(data, mime)\n'
            '            if path == "/api/vscode/status":\n                from . import claude_parity\n'
            '                return self._json({"up": claude_parity.vscode_up(), "port": claude_parity.VSCODE_PORT})\n'
            '            if path == "/api/vscode/link":\n                from . import termux\n'
            '                return self._json({"command": termux.link_command(self.server.server_address[1])'
            '.replace("/termux/setup?", "/termux/vscode?")})\n'
            '            if path == "/api/file":\n')

    # VS Code needs no earlier Termux setup: the app hands Termux a one-time command for this script.
    replace(sv, '        if path == "/termux/setup" and self.command == "GET":\n',
            '        if path in ("/termux/setup", "/termux/vscode") and self.command == "GET":\n')
    replace(sv, '            if path == "/termux/app.zip":\n',
            '            if path == "/termux/vscode":\n                from . import termux\n'
            '                return self._text(termux.VSCODE_SCRIPT, "text/x-shellscript")\n'
            '            if path == "/termux/app.zip":\n')
    tx = package / "termux.py"
    tx.write_text(tx.read_text() + "\n\n# VS Code (code-server) in Termux, started from the app's VS Code button.\nVSCODE_SCRIPT = %r\n"
                  % ("#!/data/data/com.termux/files/usr/bin/bash\n" + VSCODE + "newal-vscode start\n"))

    # Code intelligence the agent uses itself (Cursor-style): problems, outline, definitions/references, VS Code.
    shutil.copyfile(src / "code_intel.py", package / "code_intel.py")
    tl = package / "tools.py"
    replace(tl, "_tool_search.install()\n", "_tool_search.install()\nfrom . import code_intel as _code_intel\n_code_intel.install()\n")
    replace(tl, " + _tool_search.NAMES\n", " + _tool_search.NAMES + _code_intel.NAMES\n")
    replace(tl, "        found = diagnostics.check(path, text)\n",
            "        found = diagnostics.check(path, text)\n        from . import code_intel\n"
            "        found = list(found) + code_intel.extra_problems(path)\n")
    cp = package / "claude_parity.py"
    replace(cp, '"dependency_install", "capability_ensure",', '"diagnostics", "code_outline", "find_symbol", "dependency_install", "capability_ensure",')
    replace(cp, '"project\'s tests or the program and fix what fails before saying it works; ',
            '"project\'s tests or the program and fix what fails before saying it works; use code_outline to navigate big "\n'
            '    "files, find_symbol before renaming or changing a signature, and diagnostics on the changed files before "\n'
            '    "finishing; when VS Code runs, vscode_open shows the user the file you changed; ')

    replace(package / "agent_policy.py",
            "    return claude_parity.deferred_note(agent) + '\\n\\nCurrent runtime capabilities",
            "    return claude_parity.work_rules(agent) + claude_parity.deferred_note(agent) + '\\n\\nCurrent runtime capabilities")

    replace(package / "provider_pool.py", '        "capabilities": ["text", "tools", "streaming", "reasoning", "long_context", "coding"],\n    },\n]\n',
            '        "capabilities": ["text", "tools", "streaming", "reasoning", "long_context", "coding"],\n    },\n' + FREE_PROVIDERS)
    replace(package / "provider_keys.py", '    ("nvidia", "NVIDIA Free"),\n',
            '    ("nvidia", "NVIDIA Free"),\n    ("zhipu", "Zhipu GLM-4.7-Flash Free"),\n    ("mistral", "Mistral Free (Experiment)"),\n')

    # The pinned test expectations name the pool; they gain the two new providers (vault-backed keys).
    replace(package / "provider_pool_tests.py",
            '             patch.object(provider_pool.settings, "user", return_value={}):\n'
            '            ids = [x["id"] for x in provider_pool.candidates(c)]\n        self.assertEqual(ids, [\n'
            '            "groq/gpt-oss-120b-free",\n            "gemini/3.7-flash-free",\n            "openrouter/free",\n'
            '            "nvidia/nemotron-3-ultra-free",\n            "aihorde/anonymous-free",\n',
            '             patch.object(provider_pool.settings, "user", return_value={}):\n'
            '            ids = [x["id"] for x in provider_pool.candidates(c)]\n        self.assertEqual(ids, [\n'
            '            "groq/gpt-oss-120b-free",\n            "gemini/3.7-flash-free",\n            "openrouter/free",\n'
            '            "nvidia/nemotron-3-ultra-free",\n            "zhipu/glm-4.7-flash-free",\n'
            '            "mistral/devstral-free",\n            "aihorde/anonymous-free",\n')
    replace(package / "provider_keys_tests.py", '["groq", "gemini", "openrouter", "nvidia"]',
            '["groq", "gemini", "openrouter", "nvidia", "zhipu", "mistral"]')
    replace(package / "termux.py", "# GitHub: one login for gh and git in Termux and for the app",
            VSCODE + "# GitHub: one login for gh and git in Termux and for the app")
