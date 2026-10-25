"""Install scheduled task tools, local API, scheduler and UI into the pinned engine."""
import shutil


def apply(src, package, replace):
    for name in ("scheduled_tasks.py", "scheduled_tasks_tests.py"):
        shutil.copyfile(src / name, package / name)
    for name in ("scheduled_tasks_ui.js", "scheduled_tasks.css"):
        target = "scheduled_tasks.js" if name.endswith("_ui.js") else name
        shutil.copyfile(src / name, package / "ui" / target)

    # Tool registration and persistent sessions use the original engine's registry.
    tools = package / "tools.py"
    replace(tools, "from . import task_state as _task_state\n",
            "from . import task_state as _task_state\n"
            "from . import scheduled_tasks as _scheduled_tasks\n")
    replace(tools, " + _tool_search.NAMES\n",
            " + _tool_search.NAMES + _scheduled_tasks.NAMES\n")

    # Lean phone mode must offer the same tools to the model as the full mode.
    replace(package / "lean.py", '    "read", "edit", "write", "glob", "grep", "bash", "job", "todo",\n',
            '    "read", "edit", "write", "glob", "grep", "bash", "job", "todo",\n'
            '    "scheduled_task_create", "scheduled_task_list", "scheduled_task_pause",\n'
            '    "scheduled_task_resume", "scheduled_task_delete",\n')

    # Routes inherit the existing authenticated server key, loopback + origin checks.
    server = package / "server.py"
    replace(server, '        if task_state.route(self, "GET", path):\n            return\n',
            '        from . import scheduled_tasks\n'
            '        if scheduled_tasks.route(self, "GET", path):\n'
            '            return\n'
            '        if task_state.route(self, "GET", path):\n            return\n')
    replace(server, '        if task_state.route(self, "POST", path, b):\n            return\n',
            '        from . import scheduled_tasks\n'
            '        if scheduled_tasks.route(self, "POST", path, b):\n'
            '            return\n'
            '        if task_state.route(self, "POST", path, b):\n            return\n')
    replace(server, '    httpd.automatic_stop = auto_update.start(Handler.service)\n',
            '    httpd.automatic_stop = auto_update.start(Handler.service)\n'
            '    from . import scheduled_tasks\n'
            '    httpd.scheduled_stop = scheduled_tasks.start(Handler.service)\n')

    index = package / "ui/index.html"
    replace(index, '<link rel="stylesheet" href="connectors.css">',
            '<link rel="stylesheet" href="connectors.css">\n'
            '<link rel="stylesheet" href="scheduled_tasks.css">')
    replace(index, '<script src="automatic_updates.js"></script>',
            '<script src="automatic_updates.js"></script>\n'
            '<script src="scheduled_tasks.js"></script>')

    # Ensure existing execution/tool tests run with the new schedule tests.
    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as out:
        out.write("\nfrom .scheduled_tasks_tests import ScheduledTaskTests  # noqa: E402,F401\n")
