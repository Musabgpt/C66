"""Review (Cursor-style): per-hunk Keep / Undo in the review pane, and a restore button on every message that puts
the project back to how it was before it (the session's checkpoints)."""
import shutil


def apply(src, package, replace):
    shutil.copyfile(src / "review.py", package / "review.py")
    ui = package / "ui"
    for name in ("ext_review.js", "ext_review.css"):
        shutil.copyfile(src / name, ui / name)
    replace(ui / "index.html", '<script src="cursor_ui.js"></script>',
            '<script src="cursor_ui.js"></script>\n<script src="ext_review.js"></script>')
    replace(ui / "index.html", '<link rel="stylesheet" href="cursor_ui.css">',
            '<link rel="stylesheet" href="cursor_ui.css">\n<link rel="stylesheet" href="ext_review.css">')
    replace(package / "server.py", '                if action == "revert":\n',
            '                if action in ("hunks", "revert_hunk", "restore"):\n                    from . import review\n'
            '                    return self._json(review.route(s, action, b)[1])\n'
            '                if action == "revert":\n')
    app = ui / "app.js"
    replace(app, "      diff.appendChild(diffView(c.diff));\n    };\n",
            "      if (window.MusabReview) diff.appendChild(window.MusabReview.hunkView(S.current, c, () => refreshChanges()));\n"
            "      else diff.appendChild(diffView(c.diff));\n    };\n")
    replace(app, '        const u = h("div", "msg-user", \'<div class="bubble" dir="auto">\' + esc(ev.text) + "</div>");\n'
                 '        L.turn.appendChild(u);\n',
            '        const u = h("div", "msg-user", \'<div class="bubble" dir="auto">\' + esc(ev.text) + "</div>");\n'
            '        L.turn.appendChild(u);\n'
            '        if (window.MusabReview) window.MusabReview.onTurn(u, ev, replay);\n')
