"""Tool safety for the model, and the phone tool's description.

* The phone tool refuses the app's internal actions. Provider keys and connector calls are made by the engine itself,
  directly; the model's phone tool no longer reaches them. In full-auto mode a phone action needed no approval, so a
  model could have read a stored key through it.
* An install's approval names the APK it installs.
* The phone's description names what the action list alone does not say: the install's path, notifications, crash
  reports and recorded workflows. It no longer lists screenshot: that is a file, and screen_look is how the model sees.
* The file reader stays in the project folder: a sibling folder whose name starts the same ("proj-secret" next to
  "proj") is outside it.
"""
import shutil


def apply(src, package, replace):
    replace(package / "tools.py",
            '    def t_phone(ctx, action, **args):\n'
            '        if ctx.cancel is not None and ctx.cancel.is_set():\n'
            '            raise ToolError("cancelled")\n',
            '    def t_phone(ctx, action, **args):\n'
            '        if ctx.cancel is not None and ctx.cancel.is_set():\n'
            '            raise ToolError("cancelled")\n'
            '        if str(action) not in phone.ACTIONS:\n'
            '            raise ToolError("the phone tool has no action %r; its actions are: %s"\n'
            '                            % (str(action), ", ".join(phone.ACTIONS)))\n')
    replace(package / "permissions.py",
            '    for k in ("name", "url", "text", "number", "page", "item", "direction"):\n',
            '    for k in ("name", "path", "url", "text", "number", "page", "item", "direction"):\n')
    replace(package / "server.py",
            '    full = os.path.abspath(os.path.join(root, rel))\n'
            '    if not full.startswith(os.path.abspath(root)):\n'
            '        return {"error": "outside the project"}\n',
            '    base = os.path.abspath(root)\n'
            '    full = os.path.abspath(os.path.join(base, rel))\n'
            '    if os.path.commonpath([base, full]) != base:\n'
            '        return {"error": "outside the project"}\n')
    replace(package / "phone_fast.py",
            '       "(name: back, home, enter), settings (page), screen (lists the UI as numbered items), screenshot, alarm, "\n'
            '       "timer, clipboard, notify, install_apk. Every action that changes the screen returns the new screen in its "\n',
            '       "(name: back, home, enter), settings (page), screen (lists the UI as numbered items), alarm, "\n'
            '       "timer, clipboard, notify, install_apk (path), notifications_read (needs Notification Access), "\n'
            '       "crash_reports (of an app under test), automation_start/stop/list/replay (name). Every action that changes "\n'
            '       "the screen returns the new screen in its "\n')
    shutil.copyfile(src / "ext_toolsafety_tests.py", package / "ext_toolsafety_tests.py")
    tests = package.parent / "tests" / "test_newal_code.py"
    if tests.exists():
        # CI runs desktop/tests/test_newal_code.py: the tool-safety tests run there, with the other engine tests.
        replace(tests, 'if __name__ == "__main__":\n    unittest.main()\n',
                'from newal_code.ext_toolsafety_tests import ToolSafetyTests  # noqa: E402,F401 - tool safety\n\n\n'
                'if __name__ == "__main__":\n    unittest.main()\n')
