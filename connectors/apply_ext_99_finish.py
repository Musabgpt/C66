"""Before a phone task is called done, the phone checks the work, and only a checked turn teaches a routine
(finish.py; items 4 and 5 of the research plan)."""
import shutil


def apply(src, package, replace):
    for name in ("finish.py", "ext_finish_tests.py"):
        shutil.copyfile(src / name, package / name)

    ag = package / "agent.py"
    replace(ag, "        verify_rounds = goal_rounds = stop_rounds = 0\n",
            "        verify_rounds = goal_rounds = stop_rounds = 0\n"
            "        phone_checked = False\n")
    replace(ag, "                # The computer checks the work: the project's tests, when this turn changed files.\n",
            "                # The phone checks the work too: a turn that acted on the phone does not end on a screen that\n"
            "                # shows plainly the task is not done (a typed text never sent, a failed last action): the\n"
            "                # model is sent back once, with the screen (finish.py).\n"
            "                if self.depth == 0:\n"
            "                    from . import finish\n"
            "                    note = finish.check(self, answer, phone_checked)\n"
            "                    if note:\n"
            "                        phone_checked = True\n"
            '                        self.emit({"type": "status", "text": "The phone\'s screen shows the task is not '
            'done yet: continuing…"})\n'
            '                        s.add({"role": "user", "content": note})\n'
            "                        continue\n"
            "                # The computer checks the work: the project's tests, when this turn changed files.\n")

    # a routine is learned only from a turn whose end was checked
    replace(package / "phone_fast.py",
            "        if not log or not log[-1][2]:\n            return None\n",
            "        if not log or not log[-1][2]:\n            return None\n"
            '        if not st.get("verified"):\n'
            "            return None                 # its end was not checked, or showed it unfinished (finish.py)\n")
    # the lean tests' turns end the way the agent loop ends one now: with the check of their end
    replace(package / "ext_lean_tests.py",
            '            tools.call(ctx, "phone", args)\n        return phone_fast.learn(a, error)\n',
            '            tools.call(ctx, "phone", args)\n'
            "        if not error:\n"
            "            from . import finish\n"
            '            finish.check(a, "done", False)          # the turn\'s end is checked, as the agent loop does\n'
            "        return phone_fast.learn(a, error)\n")

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_finish_tests import FinishTests  # noqa: E402,F401 - run with the parity tests in CI\n")
