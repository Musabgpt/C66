"""Long work runs to the end.

The step budget was kept small (25 on a device, 60 for an API model) because a weak model could loop until its steps
ran out, and the budget was the only thing that stopped it. Those loops are now caught directly: the phone screen
check and the per-screen breaker (vital.py), the circuit breaker on a repeated failing call, and the supervisor's
stall (4 min) and hard-timeout (30 min) watchdogs. So an API model is given a larger budget per turn, and a long,
honest task finishes instead of stopping half way with "Stopped after 60 steps".

Only the plain API default is raised: it is read from circuit.step_budget, so a set max_steps, a sub-agent's own
budget, a model on the device (a slow phone CPU, where a runaway is expensive) and "alive": false all keep the value
they had.
"""
from . import circuit


ALIVE_STEPS = 200


def step_budget(cfg, agent_def, on_device):
    cfg = cfg or {}
    base = circuit.step_budget(cfg, agent_def, local=on_device)
    if (base == circuit.API_STEPS and not on_device and cfg.get("alive", True)
            and not (agent_def or {}).get("steps") and not cfg.get("max_steps")):
        return ALIVE_STEPS
    return base
