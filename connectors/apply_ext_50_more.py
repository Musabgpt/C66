"""More Cursor-style power: @git-diff / @git-log / @problems / @folder:path mentions, and the scaffold_project tool
(web, React, Flask, Express, Telegram bot, Python CLI starters)."""
import shutil


def apply(src, package, replace):
    for name in ("mentions.py", "templates.py"):
        shutil.copyfile(src / name, package / name)
    replace(package / "service.py",
            "    def _turn(self, sid, text, images):\n        a = self.agent(sid)\n        s = a.session\n",
            "    def _turn(self, sid, text, images):\n        a = self.agent(sid)\n        s = a.session\n"
            "        from . import mentions\n        text = mentions.expand(s, text)\n")
    tl = package / "tools.py"
    replace(tl, "_code_intel.install()\n", "_code_intel.install()\nfrom . import templates as _templates\n_templates.install()\n")
    replace(tl, " + _code_intel.NAMES\n", " + _code_intel.NAMES + _templates.NAMES\n")
