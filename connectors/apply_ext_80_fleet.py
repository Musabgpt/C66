"""Wider reach: background agents that work at the same time (each in its own worktree), and GitHub Actions and pull
requests watched from the chat (/bg, /agents, /gh, /watch, the Agents and GitHub panels, two tools)."""
import shutil


def apply(src, package, replace):
    for name in ("fleet.py", "gh_monitor.py"):
        shutil.copyfile(src / name, package / name)
    ui = package / "ui"
    for name in ("ext_fleet.js", "ext_fleet.css"):
        shutil.copyfile(src / name, ui / name)
    replace(ui / "index.html", '<script src="ext_tabs.js"></script>',
            '<script src="ext_tabs.js"></script>\n<script src="ext_fleet.js"></script>')
    replace(ui / "index.html", '<link rel="stylesheet" href="ext_tabs.css">',
            '<link rel="stylesheet" href="ext_tabs.css">\n<link rel="stylesheet" href="ext_fleet.css">')
    replace(ui / "app.js", "    window.NewAlWorkspaceSession = () => S.current;\n",
            "    window.NewAlWorkspaceSession = () => S.current;\n    window.NewAlOpenSession = id => openSession(id);\n")

    tl = package / "tools.py"
    replace(tl, "_phone_ext.install()\n",
            "_phone_ext.install()\nfrom . import fleet as _fleet, gh_monitor as _gh_monitor\n_fleet.install()\n"
            "_gh_monitor.install()\n")
    replace(tl, " + _code_intel.NAMES + _templates.NAMES\n",
            " + _code_intel.NAMES + _templates.NAMES + _fleet.NAMES + _gh_monitor.NAMES\n")
    replace(package / "permissions.py", 'GITHUB_LOOKS = ("runs", "run", "logs", "prs", "pr", "issues", "issue")',
            'GITHUB_LOOKS = ("runs", "run", "logs", "prs", "pr", "issues", "issue", "overview", "watch", "checks", '
            '"artifacts")')

    sv = package / "service.py"
    replace(sv, "        self.plan_turn = set()\n",
            "        self.plan_turn = set()\n        from . import fleet\n        fleet.SERVICE = self\n")
    replace(sv, "        from . import claude_parity\n        parity = claude_parity.command(self, s, name, args)\n",
            "        from . import fleet\n        out = fleet.command(self, s, name, args)\n"
            "        if out is not None:\n            return out\n"
            "        from . import claude_parity\n        parity = claude_parity.command(self, s, name, args)\n")
    replace(sv, '    ("doctor", "", "Check the engine: Python, git, shell tools, model and tool registry"),\n',
            '    ("doctor", "", "Check the engine: Python, git, shell tools, model and tool registry"),\n'
            '    ("bg", "<task>", "Start a background agent on a task (its own worktree; keeps going while you work)"),\n'
            '    ("agents", "", "The background agents: running, done, their reports and changes"),\n'
            '    ("gh", "[owner/repo]", "GitHub at a glance: Actions runs and open pull requests with their checks"),\n'
            '    ("watch", "[latest | <run id> | pr <n>] [owner/repo]", "Watch a GitHub Actions run until it ends"),\n')

    srv = package / "server.py"
    replace(srv, '            if path == "/api/sessions":\n                return self._json(sessmod.listing(',
            '            if path == "/api/fleet" or path.startswith("/api/gh/"):\n'
            '                from . import fleet\n'
            '                return self._json(fleet.route(svc, "GET", path, q, None))\n'
            '            if path == "/api/sessions":\n                return self._json(sessmod.listing(')
    replace(srv, '            if path == "/api/sessions":\n                s = svc.create(',
            '            if path.startswith(("/api/fleet/", "/api/gh/")):\n'
            '                from . import fleet\n'
            '                return self._json(fleet.route(svc, "POST", path, None, b))\n'
            '            if path == "/api/sessions":\n                s = svc.create(')

    cp = package / "claude_parity.py"
    replace(cp, '    "screen_look", "android_test_app",\n',
            '    "screen_look", "android_test_app", "background_agent", "github_monitor",\n')
    replace(cp, '"and fix crashes before saying it works.")',
            '"and fix crashes before saying it works. Big work with independent parts: start background agents "\n'
            '    "(background_agent), each on its own part in its own worktree, keep working, then wait for them and "\n'
            '    "apply their results. After pushing, github_monitor watches the Actions run (background=true) and "\n'
            '    "shows the failing step\'s log.")')
