"""The task memory (task_memory.py; TaskMemory.java in the app): MusabAI's categorized library of procedures from
AndroidControl, Mind2Web, InSTA-150K and hand-written ones reaches the model.

* A phone request that no learned routine fits comes with the task-memory procedure that does fit (phone_fast.note,
  connectors/phone_live/phone_fast.py), with the values the request gives for its blanks.
* phone_routine runs tm: procedures through the app (the same runner as the memory app's Run button), searches the
  memory (action=search) and shows one procedure (action=show).
* The phone prompt says so; the behavior profile's marker moves so open threads pick the new prompt up.
"""
import shutil

PROMPT_ADDITION = (
    "- A \"Task memory tm:...\" note under the request is a procedure from MusabAI's task memory (curated steps for "
    "apps like Facebook, WhatsApp, Instagram and Gmail, and recorded demonstrations from AndroidControl, Mind2Web and "
    "InSTA-150K). If it is runnable and fits, call phone_routine with that id and this request's values ({1}, {2}...: "
    "the post's text, the contact, the search) in one call; if it stops, continue by hand from the screen it shows. "
    "A guidance note lists steps to follow with the phone tool, adapting to the screen. phone_routine action=search "
    "with query finds other procedures when no note came.\n"
)


def apply(src, package, replace):
    for name in ("task_memory.py", "ext_task_memory_tests.py"):
        shutil.copyfile(src / name, package / name)

    replace(package / "agent_policy.py", "MARKER = 'MusabAI behavior profile v13'",
            "MARKER = 'MusabAI behavior profile v14'")

    lean_md = package / "agent_prompt_lean.md"
    text = lean_md.read_text(encoding="utf-8")
    anchor = "- Otherwise start with phone open_app"
    if anchor not in text:
        raise SystemExit("Unsafe patch refused: lean prompt anchor missing for the task memory")
    if "Task memory tm:" not in text:
        lean_md.write_text(text.replace(anchor, PROMPT_ADDITION + anchor, 1), encoding="utf-8")

    # The pinned engine's test counts the phone calls of a turn: the task-memory lookup under the request is a read
    # of the app's library, not an action on the phone, so it is left out of that count.
    replace(package.parent / "tests/test_newal_code.py",
            '        self.assertEqual([c["action"] for c in self.calls], ["screen", "tap"])\n'
            '        self.assertEqual(self.calls[1]["item"], 1)\n',
            '        acted = [c for c in self.calls if not c["action"].startswith("memory_")]   # the task memory is read\n'
            '        self.assertEqual([c["action"] for c in acted], ["screen", "tap"])\n'
            '        self.assertEqual(acted[1]["item"], 1)\n')

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_task_memory_tests import TaskMemoryTests  # noqa: E402,F401 - run with the parity tests in CI\n")
