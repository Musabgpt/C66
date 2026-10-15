"""Long work runs to the end: an API model's per-turn step budget is raised (alive.py), now that loops are caught
directly rather than by the small budget."""
import shutil


def apply(src, package, replace):
    for name in ("alive.py", "ext_alive_tests.py"):
        shutil.copyfile(src / name, package / name)

    replace(package / "agent.py",
            "        max_steps = circuit.step_budget(cfg, self.agent_def, local=client.on_device)\n",
            "        from . import alive\n"
            "        max_steps = alive.step_budget(cfg, self.agent_def, client.on_device)\n")

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_alive_tests import AliveTests  # noqa: E402,F401 - run with the parity tests in CI\n")
