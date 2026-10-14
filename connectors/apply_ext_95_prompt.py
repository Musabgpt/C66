"""The v10 prompt (docs: the MusabAI prompt study).

* prompts.system builds the fixed system message from agent_policy.system_text: the focused core, or a short prompt for
  a small model on this device, then the environment and the phone note.
* The paragraph that was added to every user message (document, RAG, orchestration, Termux... tools again) is gone:
  the core and each tool's own description say it once.
* The work rules are part of the core now, so the runtime context no longer repeats them.
* Task checkpoints are for long work only: their three tools wait in the deferred list (tool_search loads them).
"""


def apply(src, package, replace):
    pr = package / "prompts.py"
    replace(pr, '    text = agent_policy.profile() + "\\n\\nEnvironment: {os}; shell: {shell}.".format(**environment("", shell))\n'
                '    if phone:\n        text += "\\n\\n" + agent_policy.PHONE_GUIDANCE\n'
                '    if local:\n        text += "\\n\\n" + BREAKER_RULE.format(steps=steps)\n'
                '    if phone:\n        text = text.replace("on their computer", "on their phone") + "\\n\\n" + PHONE\n',
            '    text = agent_policy.system_text(environment("", shell), phone=phone, local=local, steps=steps)\n')

    ag = package / "agent.py"
    text = ag.read_text(encoding="utf-8")
    start = text.find('        parts.append("For document tasks use document_create/read/download')
    if start < 0:
        raise SystemExit("Unsafe patch refused: per-message tool note not found in agent.py")
    end = text.index("\n", start) + 1
    replace(ag, text[start:end], "")

    replace(package / "agent_policy.py",
            "    return claude_parity.work_rules(agent) + claude_parity.deferred_note(agent) + ",
            "    return claude_parity.deferred_note(agent) + ")
    replace(package / "claude_parity.py", ' "task_checkpoint", "task_resume", "task_complete",\n', '\n')
