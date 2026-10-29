"""MusabAI as a personal assistant on the phone: a home (greeting, microphone, quick tiles) in place of the empty
chat, and one-step requests (alarms, timers, reminders, calls, messages, directions, searches) done by the phone itself
through NewAlPhone.quick instead of waiting for a model (home_ui.js). On a computer it adds nothing. Runs after
apply_ext_98_lean, whose lean_ui files it is placed after."""
import shutil


def apply(src, package, replace):
    for name in ("home_ui.js", "home_ui.css"):
        shutil.copyfile(src / name, package / "ui" / name)
    index = package / "ui/index.html"
    replace(index, '<link rel="stylesheet" href="lean_ui.css">\n',
            '<link rel="stylesheet" href="lean_ui.css">\n<link rel="stylesheet" href="home_ui.css">\n')
    replace(index, '<script src="lean_ui.js"></script>\n',
            '<script src="lean_ui.js"></script>\n<script src="home_ui.js"></script>\n')
