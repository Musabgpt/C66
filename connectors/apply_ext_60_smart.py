"""Smarter and faster: leaner requests (earlier turns' long tool results shortened) and a reviewer pass on the
turn's diff before the agent finishes."""
import shutil


def apply(src, package, replace):
    shutil.copyfile(src / "smart.py", package / "smart.py")
    ag = package / "agent.py"
    replace(ag, '        return [{"role": "system", "content": self.system_prompt() + agent_policy.runtime_context(self)}] + '
                '[_sendable(m) for m in self.session.messages]\n',
            '        from . import smart\n'
            '        return [{"role": "system", "content": self.system_prompt() + agent_policy.runtime_context(self)}] + '
            'smart.trim([_sendable(m) for m in self.session.messages])\n')
    replace(ag, '            return False, lint, "diagnostics"\n',
            '            return False, lint, "diagnostics"\n'
            '        from . import smart\n'
            '        found = smart.review(self, ctx)\n'
            '        if found:\n'
            '            self.emit({"type": "verify", "command": "review", "ok": False, "exit": 1, "output": found})\n'
            '            return False, found, "review"\n')
