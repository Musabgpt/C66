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
    nohup code-server --auth none --bind-addr "127.0.0.1:$PORT" --disable-telemetry --disable-update-check "$HOME/projects" >> "$LOG" 2>&1 &
    for i in $(seq 1 90); do up && break; sleep 1; done
    if up; then echo "VS Code runs: go back to the app."; else echo "VS Code did not start: $LOG"; exit 1; fi ;;
  stop) pkill -f "code-server.*127.0.0.1:$PORT" && echo "stopped" ;;
  status) if up; then echo "running"; else echo "stopped"; fi ;;
  *) echo "newal-vscode start | stop | status"; exit 2 ;;
esac
SH
chmod 700 "$PREFIX/bin/newal-vscode"
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
            '            if path == "/api/file":\n')

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
