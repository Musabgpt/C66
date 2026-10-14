"""Two engine changes about what the conversation keeps and how it is saved.

- Pruning old tool results records their short stubs, one line, not the whole conversation again. A resume puts each
  stub back on the result with the same tool_call_id; a stub whose result does not match is skipped.
- After an automatic compaction in a turn that used the phone, the screen as it is now is read again and goes into the
  continuity note (phone_fast.compact_screen). continuity.compact_messages keeps it as content, never as instructions.
"""


def apply(src, package, replace):
    session = package / "session.py"
    replace(session, '    def replace_messages(self, messages, note="compact"):\n',
            '    def prune_messages(self, stubs):\n'
            '        """Old tool results with their short stubs: {index: (tool_call_id, stub)}. The stubs go in place\n'
            '        one short line is recorded instead of the whole conversation."""\n'
            '        with self.lock:\n'
            '            for i, (_, stub) in stubs.items():\n'
            '                self.messages[i] = dict(self.messages[i], content=stub)\n'
            '            self._append({"t": "prune",\n'
            '                          "stubs": {str(i): [tid, stub] for i, (tid, stub) in stubs.items()}})\n'
            '\n'
            '    def replace_messages(self, messages, note="compact"):\n')
    replace(session, '                elif t == "ev":\n                    events.append(rec["e"])\n',
            '                elif t == "prune":\n'
            '                    for k, (tid, stub) in (rec.get("stubs") or {}).items():\n'
            '                        i = int(k)\n'
            '                        msg = messages[i] if i < len(messages) else {}\n'
            '                        if msg.get("role") == "tool" and msg.get("tool_call_id") == tid:\n'
            '                            messages[i] = dict(msg, content=stub)\n'
            '                elif t == "ev":\n'
            '                    events.append(rec["e"])\n')

    agent = package / "agent.py"
    replace(agent, "        pruned, out = 0, []\n", "        pruned, stubs = 0, {}\n")
    replace(agent, '                m = dict(m, content=stub)\n                pruned += 1\n            out.append(m)\n'
                   '        if pruned:\n            s.replace_messages(out, note="prune")\n',
            '                stubs[i] = (m.get("tool_call_id"), stub)\n                pruned += 1\n'
            '        if pruned:\n            s.prune_messages(stubs)\n')
    replace(agent, "        s.replace_messages(continuity.compact_messages(s, summary, s.active_objective))\n",
            "        from . import phone_fast\n"
            "        s.replace_messages(continuity.compact_messages(\n"
            '            s, summary, s.active_objective, screen="" if force else phone_fast.compact_screen(self)))\n')
