"""The task memory reaches the agent (task_memory.py): a phone request that fits a procedure comes with it, and
phone_routine runs a tm: procedure through the app, searches the memory and shows one procedure."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from . import tools  # noqa: F401 - registers every tool first, as the engine does

NOTE = ("(Task memory tm:cur.facebook.post — MusabAI curated, Facebook: \"Publish a text post on Facebook\"; blanks "
        "{1} = نص المنشور (this request: \"صباح الخير\"). Steps: open_app \"Facebook\" → tap \"What's on your mind?\" "
        "→ type \"{1}\" → tap \"POST\" (publishes/sends). If it fits, call phone_routine with id tm:cur.facebook.post "
        "and the values for this request.)")


class FakeApp:
    """The app's phone server with its task memory."""
    def __init__(self):
        self.calls = []
        self.confident = True

    def __call__(self, action, timeout=90, **args):
        self.calls.append((action, args))
        if action == "memory_match":
            if "فيسبوك" not in args.get("text", ""):
                return {"ok": True, "confident": False, "text": "no procedure fits"}
            return {"ok": True, "confident": self.confident, "text": NOTE,
                    "match": {"id": "tm:cur.facebook.post", "values": ["صباح الخير"], "executable": True}}
        if action == "memory_run":
            if args.get("id") == "tm:cur.facebook.post":
                return {"ok": True, "done": True, "stopped_at": -1,
                        "text": "Done: 5 steps; checked: \"%s\" is on the screen." % (args.get("values") or ["?"])[0],
                        "screen": "App: Facebook (com.facebook.katana)\n[1] %s" % (args.get("values") or [""])[0]}
            return {"ok": True, "done": False, "stopped_at": 1, "text": "Stopped at step 2 of 4 (tap \"Send\"): "
                    "nothing on the screen says \"Send\"", "screen": "App: Other (x)\n[1] Something"}
        if action == "memory_search":
            return {"ok": True, "text": "tm:cur.facebook.post [MusabAI curated, runnable] Facebook: Publish a post"}
        if action == "memory_get":
            return {"ok": True, "text": "tm:cur.facebook.post (MusabAI curated): Publish a text post on Facebook",
                    "procedure": {"executable": True, "blanks": ["نص المنشور"]}}
        if action == "screen":
            return {"ok": True, "text": "App: Launcher (l)\n[1] Facebook"}
        return {"ok": True, "text": "done"}


class TaskMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        from . import settings
        for target, value in (("HOME", os.path.join(self.root, "home")),
                              ("CONFIG", os.path.join(self.root, "home", "config.json"))):
            p = mock.patch.object(settings, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.app = FakeApp()
        from . import phone
        for patch in (mock.patch.object(phone, "call", self.app), mock.patch.object(phone, "available", lambda: True)):
            patch.start()
            self.addCleanup(patch.stop)

    def agent(self):
        from . import agent, session
        s = session.Session(self.root)
        a = agent.Agent(s, client=SimpleNamespace(spec={}, local=False, on_device=False, context=lambda: 128000))
        self.addCleanup(a.memory.close)
        return a

    def ctx(self, a):
        return __import__(__package__ + ".agent", fromlist=["ToolContext"]).ToolContext(a)

    def test_a_request_that_fits_comes_with_the_procedure(self):
        a = self.agent()
        a.session.turn += 1
        content, _ = a._user_content("افتح فيسبوك وانشر منشور يقول صباح الخير", [])
        self.assertIn("Task memory tm:cur.facebook.post", content)
        self.assertIn("phone_routine", content)
        self.assertEqual(self.app.calls[-1][0], "memory_match")

    def test_nothing_is_added_when_the_memory_is_not_sure(self):
        self.app.confident = False
        a = self.agent()
        a.session.turn += 1
        content, _ = a._user_content("افتح فيسبوك وانشر منشور", [])
        self.assertNotIn("Task memory", content)

    def test_a_learned_routine_still_comes_first(self):
        from . import phone_fast
        phone_fast.save([{"id": "r1", "request": "افتح فيسبوك وانشر منشور", "pattern": "افتح فيسبوك وانشر منشور",
                          "values": [], "steps": [{"action": "open_app", "name": "Facebook"},
                                                  {"action": "tap", "text": "Post"}], "uses": 0}])
        a = self.agent()
        a.session.turn += 1
        content, _ = a._user_content("افتح فيسبوك وانشر منشور", [])
        self.assertIn("Saved phone routine r1", content)
        self.assertNotIn("Task memory", content)

    def test_phone_routine_runs_a_memory_procedure_through_the_app(self):
        a = self.agent()
        out, meta = tools.call(self.ctx(a), "phone_routine", {"id": "tm:cur.facebook.post", "values": ["صباح الخير"]})
        self.assertIn("Task memory tm:cur.facebook.post done", out)
        self.assertIn("Screen now:", out)
        self.assertIn("nothing is left to do", out)
        self.assertTrue(meta["done"])
        self.assertEqual(self.app.calls[-1], ("memory_run", {"id": "tm:cur.facebook.post", "values": ["صباح الخير"]}))

    def test_the_values_of_the_note_are_used_when_none_are_given(self):
        a = self.agent()
        a.session.turn += 1
        a._user_content("افتح فيسبوك وانشر منشور يقول صباح الخير", [])
        tools.call(self.ctx(a), "phone_routine", {"id": "tm:cur.facebook.post"})
        self.assertEqual(self.app.calls[-1][1]["values"], ["صباح الخير"])

    def test_a_stopped_procedure_says_where_to_continue(self):
        out, meta = tools.call(self.ctx(self.agent()), "phone_routine", {"id": "tm:cur.whatsapp.send", "values": ["x", "y"]})
        self.assertIn("did not finish", out)
        self.assertIn("Continue by hand", out)
        self.assertFalse(meta["done"])

    def test_search_and_show(self):
        a = self.agent()
        out, _ = tools.call(self.ctx(a), "phone_routine", {"action": "search", "query": "post on facebook"})
        self.assertIn("tm:cur.facebook.post", out)
        self.assertEqual(self.app.calls[-1], ("memory_search", {"text": "post on facebook", "limit": 8}))
        out, _ = tools.call(self.ctx(a), "phone_routine", {"action": "show", "id": "tm:cur.facebook.post"})
        self.assertIn("runnable with phone_routine", out)
        self.assertIn("{1} = نص المنشور", out)

    def test_without_the_app_there_is_no_memory(self):
        from . import phone, task_memory
        with mock.patch.object(phone, "available", lambda: False):
            self.assertEqual(task_memory.note(self.agent(), "افتح فيسبوك وانشر منشور"), "")


if __name__ == "__main__":
    unittest.main()
