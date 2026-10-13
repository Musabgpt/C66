"""The phone batch: screen_look (screenshots read by a vision model), android_test_app (install, open, watch and
report on an APK the agent built) and a notification with a summary when a long turn ends in the background."""
import shutil


def apply(src, package, replace):
    shutil.copyfile(src / "phone_ext.py", package / "phone_ext.py")
    tl = package / "tools.py"
    replace(tl, "_templates.install()\n", "_templates.install()\nfrom . import phone_ext as _phone_ext\n_phone_ext.install()\n")
    replace(tl, '        names.append("phone")\n    return names\n',
            '        names.append("phone")\n        names += _phone_ext.NAMES\n    return names\n')
    replace(package / "agent.py",
            '                   "changes": [{k: c[k] for k in ("path", "status", "plus", "minus")} for c in changes]})\n'
            '        return answer\n',
            '                   "changes": [{k: c[k] for k in ("path", "status", "plus", "minus")} for c in changes]})\n'
            '        if self.depth == 0:\n'
            '            from . import phone_ext\n'
            '            phone_ext.finished(self, answer, seconds, error, changes)\n'
            '        return answer\n')
    cp = package / "claude_parity.py"
    replace(cp, '"task_checkpoint", "task_resume", "task_complete",\n))',
            '"task_checkpoint", "task_resume", "task_complete",\n    "screen_look", "android_test_app",\n))')
    replace(cp, '"ambiguous and a wrong guess is costly, ask one short question first.")',
            '"ambiguous and a wrong guess is costly, ask one short question first. On a phone: screen_look shows you the "\n'
            '    "screen (or an image) through a vision model; after building an Android app, test it with android_test_app "\n'
            '    "and fix crashes before saying it works.")')
