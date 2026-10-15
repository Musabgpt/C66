"""The phone never loops blind, and a stopped turn says its real cause in the user's language (vital.py). A turn that
ended only because the model stopped is not announced as a checked success."""
import shutil


def apply(src, package, replace):
    for name in ("vital.py", "ext_vital_tests.py"):
        shutil.copyfile(src / name, package / name)

    # the screen check wraps the phone tool last, around phone_fast and the Chrome routing
    replace(package / "tools.py", "_task_state.install()\n",
            "_task_state.install()\nfrom . import vital as _vital\n_vital.install()\n")

    ag = package / "agent.py"
    # a failing phone call is counted per screen: on a changed screen the same call is a new try
    replace(ag, "        key = circuit.Breaker.key(self.last_change_step, name, args)\n",
            "        from . import vital\n"
            "        key = circuit.Breaker.key(vital.change_mark(self, name), name, args)\n")
    # every stop says why, in the user's language (English keeps the engine's wording)
    replace(ag, '                    answer = answer or "Stopped after %d steps without finishing." % max_steps\n',
            '                    from . import vital\n'
            '                    answer = answer or vital.stop_text(self, "budget", max_steps)\n')
    replace(ag, '                        answer = "Stopped: %s." % self.breaker.reason\n',
            '                        from . import vital\n'
            '                        answer = vital.stop_text(self, "breaker", self.breaker.reason)\n')
    replace(ag, '                answer = answer or "Stopped safely after the task timeout. Resume to continue from the '
                'saved checkpoint."\n',
            '                from . import vital\n'
            '                answer = answer or vital.stop_text(self, "timeout")\n')
    # a stall names what went silent (a tool, or the provider) instead of always blaming the providers
    replace(ag, '                answer = "توقفت المحاولات بعد تعليق متكرر دون خطوة ناجحة. التقدم محفوظ؛ راجع حالة المزودين '
                'ثم استكمل."\n',
            '                from . import vital\n'
            '                answer = vital.stop_text(self, "stall", (getattr(supervisor, "last_event", ""),\n'
            '                                                          getattr(supervisor, "last_tool", "")))\n')

    # A turn without an error ended because the model stopped: its own report, not a checked success. The phone's
    # notification claims no check mark, and the task record does not call it verified.
    replace(package / "phone_ext.py",
            '    title = "MusabAI: توقفت المهمة" if error and error != "interrupted" else "MusabAI: انتهت المهمة ✓"\n',
            '    title = "MusabAI: توقفت المهمة" if error and error != "interrupted" else "MusabAI: انتهى الرد"\n')
    replace(package / "task_supervisor.py", '("Verified turn finished. " + str(answer or ""))',
            '("Turn finished (as reported by the model). " + str(answer or ""))')
    et = package / "ext_tests.py"
    replace(et, '        self.assertIn("انتهت", title)\n',
            '        self.assertEqual(title, "MusabAI: انتهى الرد")      # the model\'s own report: no check mark\n'
            '        self.assertIn("توقفت", phone_ext.summary("x", 95, "budget_exhausted")[0])\n')
    replace(et, '{"title": "MusabAI: انتهت المهمة ✓", "text": "ok\\n1m 0s"',
            '{"title": "MusabAI: انتهى الرد", "text": "ok\\n1m 0s"')

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_vital_tests import VitalTests  # noqa: E402,F401 - run with the parity tests in CI\n")
