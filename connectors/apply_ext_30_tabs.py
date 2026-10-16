"""Workspace bar: Files, Terminal and a live Preview of dev servers running on the phone."""
import shutil


def apply(src, package, replace):
    shutil.copyfile(src / "dev_ports.py", package / "dev_ports.py")
    ui = package / "ui"
    for name in ("ext_tabs.js", "ext_tabs.css"):
        shutil.copyfile(src / name, ui / name)
    replace(ui / "index.html", '<script src="cursor_ui.js"></script>',
            '<script src="cursor_ui.js"></script>\n<script src="ext_tabs.js"></script>')
    replace(ui / "index.html", '<link rel="stylesheet" href="cursor_ui.css">',
            '<link rel="stylesheet" href="cursor_ui.css">\n<link rel="stylesheet" href="ext_tabs.css">')
    replace(package / "server.py", '            if path == "/api/vscode/status":\n',
            '            if path == "/api/ports":\n                from . import dev_ports\n'
            '                return self._json({"ports": dev_ports.open_ports()})\n'
            '            if path == "/api/vscode/status":\n')
