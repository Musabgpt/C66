import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from . import task_state, task_supervisor


class TaskSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.store = Path(self.tmp.name) / "tasks"
        self.env = patch.dict(os.environ, {
            "NEWAL_TASK_STATE_HOME": str(self.store),
            "NEWAL_SUPERVISOR_HEARTBEAT_SECONDS": "0.05",
            "NEWAL_SUPERVISOR_STALL_SECONDS": "0.10",
            "NEWAL_SUPERVISOR_HARD_TIMEOUT_SECONDS": "0.40",
            "NEWAL_SUPERVISOR_MAX_RECOVERIES": "2",
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.session = SimpleNamespace(id="session-test", root=str(self.root))
        self.user_cancel = threading.Event()
        self.events = []

    def make(self, objective="Build and test the project", turn=1):
        return task_supervisor.TaskSupervisor(
            self.session, self.user_cancel, self.events.append, objective, turn
        )

    def wait_for(self, predicate, timeout=1.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def test_only_completed_successful_tools_reset_consecutive_stalls(self):
        sup = self.make()
        for event in ({'type':'status'}, {'type':'tool_args'},
                      {'type':'tool_end','ok':False},
                      {'type':'tool_end','ok':True,'meta':{'exit_code':1}},
                      {'type':'tool_end','ok':True,'meta':{'pending':True}}):
            sup.recoveries = 2
            sup.observe(event)
            self.assertEqual(sup.recoveries, 2)
        sup.observe({'type':'tool_end','name':'write','ok':True,'meta':{}})
        self.assertEqual(sup.recoveries, 0)
        self.assertFalse(sup.cancel_token.is_set())

    def test_unfinished_checklist_never_marks_task_complete(self):
        self.session.todo = [
            {"content": "Write game.js", "status": "in_progress"},
            {"content": "Test in Android browser", "status": "pending"},
        ]
        sup = self.make("Build the airplane and tank HTML game")
        sup.start()
        sup.finish(answer="Ready. What would you like me to do?")
        row = task_state.resume(self.root, sup.task_id)
        self.assertEqual(row["status"], "active")
        self.assertIn("Write game.js", row["checkpoint"]["next_step"])
        self.assertIn("without verified", row["checkpoint"]["blocker"])

    def test_lost_task_reply_does_not_complete_task_with_empty_todo(self):
        self.session.todo = []
        sup = self.make("شغل اللعبة")
        sup.start()
        sup.finish(answer="ما عندي مهمة، شو بدك أعمل؟")
        row = task_state.resume(self.root, sup.task_id)
        self.assertEqual(row["status"], "active")
        self.assertIn("without verified", row["checkpoint"]["blocker"])

    def test_manual_stop_sets_real_cancel_and_preserves_resume_checkpoint(self):
        sup = self.make()
        sup.start()
        sup.request_stop()
        self.assertTrue(sup.cancel_token.is_set())
        self.assertEqual(sup.cancel_token.reason(), "user")
        row = task_state.resume(self.root, sup.task_id)
        self.assertEqual(row["status"], "active")
        self.assertIn("Stopped by user", row["checkpoint"]["blocker"])
        sup.finish(error="interrupted")
        self.assertEqual(task_state.resume(self.root, sup.task_id)["status"], "active")

    def test_watchdog_emits_heartbeat_then_cancels_stalled_operation(self):
        sup = self.make()
        sup.start()
        self.assertTrue(self.wait_for(lambda: any(e.get("type") == "task_heartbeat" for e in self.events)))
        self.assertTrue(self.wait_for(lambda: sup.cancel_token.reason() == "watchdog_stall"))
        self.assertEqual(sup.recoveries, 1)
        self.assertTrue(self.wait_for(lambda: any("another route" in (e.get("text") or "") for e in self.events)))
        row = task_state.resume(self.root, sup.task_id)
        self.assertIn("No progress", row["checkpoint"]["blocker"])
        self.assertTrue(sup.consume_watchdog())
        self.assertFalse(sup.cancel_token.is_set())
        sup.finish(error="test cleanup")

    def test_stall_exhaustion_is_not_a_hard_timeout(self):
        with patch.dict(os.environ, {
            "NEWAL_SUPERVISOR_HEARTBEAT_SECONDS": "0.05",
            "NEWAL_SUPERVISOR_STALL_SECONDS": "0.10",
            "NEWAL_SUPERVISOR_HARD_TIMEOUT_SECONDS": "0.50",
            "NEWAL_SUPERVISOR_MAX_RECOVERIES": "1",
        }, clear=False):
            sup = self.make()
            sup.start()
            self.assertTrue(self.wait_for(lambda: sup.cancel_token.reason() == "watchdog_stall"))
            self.assertTrue(sup.consume_watchdog())
            self.assertTrue(self.wait_for(lambda: sup.cancel_token.reason() == "stall_exhausted"))
            snap = sup.snapshot()
            self.assertEqual(snap["reason"], "stall_exhausted")
            self.assertGreaterEqual(snap["recoveries"], 1)
            sup.finish(error="timeout")
            row = task_state.resume(self.root, sup.task_id)
            self.assertEqual(row["status"], "active")

    def test_progress_resets_watchdog_and_checkpoints_verified_step(self):
        sup = self.make()
        sup.start()
        time.sleep(0.06)
        sup.observe({"type": "tool_start", "name": "bash", "step": 3})
        time.sleep(0.06)
        self.assertFalse(sup.cancel_token.is_set())
        sup.observe({"type": "verify", "ok": True, "command": "pytest", "step": 4})
        row = task_state.resume(self.root, sup.task_id)
        self.assertIn("Verification passed", row["checkpoint"]["progress"])
        self.assertEqual(row["checkpoint"]["evidence"], "pytest")
        sup.finish(answer="done")
        self.assertEqual(task_state.resume(self.root, sup.task_id)["status"], "complete")

    def test_continuation_reuses_existing_task_state_instead_of_new_memory(self):
        previous = task_state.checkpoint(
            self.root,
            objective="Finish release",
            progress="Tests passed",
            next_step="Build APK",
            evidence="run-123",
        )
        sup = self.make("Continue the release")
        context = sup.resume_context("كمل من حيث توقفنا")
        self.assertIn(previous["id"], context)
        self.assertIn("Build APK", context)
        self.assertIn("Tests passed", context)
        self.assertEqual(sup.previous["id"], previous["id"])

    def test_multi_step_request_gets_bounded_planning_instruction(self):
        sup = self.make()
        text = "Implement this project migration in several phases and verify the build"
        context = sup.planning_context(text)
        self.assertIn("multi-step", context)
        self.assertIn("checklist", context)
        self.assertEqual(sup.planning_context("What is 2+2?"), "")

    def test_agent_budget_exhaustion_preserves_unfinished_task(self):
        from . import agent, session, settings, circuit
        with patch.object(settings, 'HOME', self.tmp.name):
            s=session.Session(str(self.root))
            client=SimpleNamespace(id='test',spec={},on_device=False,local=False)
            a=agent.Agent(s,client=client)
            self.addCleanup(a.memory.close)
            with patch.object(circuit,'step_budget',return_value=0), \
                 patch.object(a,'_user_content',return_value=('Build project', [])):
                a.run('Build project')
            row=task_state.resume(self.root,a.supervisor.task_id)
        self.assertEqual(row['status'],'active', 'unfinished budget stop must not be called verified completion')
        self.assertIn('budget',row['checkpoint']['blocker'])

    def test_unmet_goal_after_repair_budget_does_not_complete_task(self):
        from . import agent, session, settings, providers
        with patch.object(settings,'HOME',self.tmp.name):
            s=session.Session(str(self.root));s.goal='Verified release'
            a=agent.Agent(s,client=SimpleNamespace(id='test',spec={},on_device=False,local=False))
            self.addCleanup(a.memory.close)
            comp=providers.Completion();comp.content='Progress is incomplete.'
            with patch.object(a,'_user_content',return_value=('Explain progress', [])), \
                 patch.object(a,'_maybe_compact'), patch.object(a,'_call',return_value=comp), \
                 patch.object(a,'_goal_check',return_value=(False,'Tests still fail')):
                a.run('Explain progress')
            self.assertEqual(task_state.resume(self.root,a.supervisor.task_id)['status'],'active')

    def test_failed_verification_after_repair_budget_preserves_unfinished_task(self):
        from . import agent, session, settings, providers
        with patch.object(settings, 'HOME', self.tmp.name):
            s = session.Session(str(self.root))
            a = agent.Agent(s, client=SimpleNamespace(id='test', spec={}, on_device=False, local=False))
            self.addCleanup(a.memory.close)
            a.cfg['verify'] = True
            comp = providers.Completion(); comp.content = 'Progress is incomplete.'
            original_init = agent.ToolContext.__init__
            def changed_context(ctx, owner, *args, **kwargs):
                original_init(ctx, owner, *args, **kwargs)
                ctx.changed.add('changed.py')
            with patch.object(a, '_user_content', return_value=('Explain progress', [])), \
                 patch.object(a, '_maybe_compact'), patch.object(a, '_call', return_value=comp), \
                 patch.object(agent.ToolContext, '__init__', autospec=True) as context_init, \
                 patch.object(a, '_verify', return_value=(False, 'Tests still fail', 'pytest')):
                context_init.side_effect = changed_context
                a.run('Explain progress')
            self.assertEqual(task_state.resume(self.root, a.supervisor.task_id)['status'], 'active')


if __name__ == "__main__":
    unittest.main()

class SupervisorRecoveryTests(unittest.TestCase):
    setUp = TaskSupervisorTests.setUp
    make = TaskSupervisorTests.make
    def test_continue_reuses_task_and_keeps_verified_progress_on_stop(self):
        first = self.make('Build calculator', 1)
        first.start()
        first.observe({'type': 'tool_end', 'name': 'write', 'ok': True, 'step': 2})
        first.request_stop(); first.finish(error='interrupted')
        self.user_cancel.clear()
        resumed = self.make('كمل', 2)
        self.assertEqual(resumed.task_id, first.task_id)
        self.assertEqual(resumed.objective, 'Build calculator')
        self.assertIn('Completed write', resumed.resume_context('كمل'))
        resumed.start(); resumed.finish(answer='done')
        self.assertEqual(task_state.resume(self.root, first.task_id)['status'], 'complete')

    def test_another_conversation_is_not_resumed(self):
        other = task_supervisor.TaskSupervisor(SimpleNamespace(id='other-session', root=str(self.root)),
                    threading.Event(), None, 'Other project task', 1)
        other.start(); other.finish(error='interrupted')
        sup = self.make('كمل', 2)
        self.assertEqual(sup.resume_context('كمل'), '')

    def test_output_and_tool_arguments_are_progress_without_checkpoint_spam(self):
        sup = self.make()
        for kind in ('terminal_output', 'tool_args'):
            sup.last_progress_at = 0
            with patch.object(task_state, 'checkpoint') as save:
                sup.observe({'type': kind, 'text': 'stream data'})
            self.assertGreater(sup.last_progress_at, 0)
            save.assert_not_called()

    def test_nonzero_command_is_not_saved_as_completed(self):
        sup = self.make(); sup.start()
        sup.observe({'type': 'tool_end', 'name': 'bash', 'ok': True, 'meta': {'exit': 1}, 'step': 2})
        row = task_state.resume(self.root, sup.task_id)
        self.assertNotIn('Completed bash', row['checkpoint']['progress'])
        self.assertIn('bash', row['checkpoint']['blocker'])
        sup.finish(error='cleanup')
