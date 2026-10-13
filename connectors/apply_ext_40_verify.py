"""Smarter finishing: the verification after a turn that changed files also requires them to be free of certain
errors (syntax, undefined names, invalid JSON, broken shell), before or without project tests."""
import shutil


def apply(src, package, replace):
    shutil.copyfile(src / "verify_lint.py", package / "verify_lint.py")
    replace(package / "agent.py",
            '        """(ok | None when there is nothing to run, output, command)."""\n',
            '        """(ok | None when there is nothing to run, output, command)."""\n'
            '        from . import verify_lint\n'
            '        lint = verify_lint.changed_problems(ctx)\n'
            '        if lint:\n'
            '            self.emit({"type": "verify", "command": "diagnostics", "ok": False, "exit": 1, "output": lint})\n'
            '            return False, lint, "diagnostics"\n')
