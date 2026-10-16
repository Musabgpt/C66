"""Long work runs to the end (alive.py): an API model gets the larger budget by default; a device model, a set
max_steps and a sub-agent's own budget are unchanged, and "alive": false restores 60."""
import unittest

from . import alive, circuit


class AliveTests(unittest.TestCase):
    def test_an_api_model_gets_the_larger_budget_by_default(self):
        self.assertEqual(alive.step_budget({}, None, False), alive.ALIVE_STEPS)
        self.assertGreater(alive.ALIVE_STEPS, circuit.API_STEPS)

    def test_a_model_on_the_device_keeps_its_small_budget(self):
        self.assertEqual(alive.step_budget({}, None, True), circuit.LOCAL_STEPS)

    def test_a_set_max_steps_wins(self):
        self.assertEqual(alive.step_budget({"max_steps": 9}, None, False), 9)
        self.assertEqual(alive.step_budget({"max_steps": 9}, None, True), 9)

    def test_a_subagents_own_budget_wins(self):
        self.assertEqual(alive.step_budget({}, {"steps": 7}, False), 7)

    def test_alive_false_restores_the_old_budget(self):
        self.assertEqual(alive.step_budget({"alive": False}, None, False), circuit.API_STEPS)
        self.assertEqual(alive.step_budget({"alive": False}, None, True), circuit.LOCAL_STEPS)
