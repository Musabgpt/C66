"""A long programming thread sent to a remote model (request_context.py): older results and long arguments are
shortened in place, the calls keep their structure, nothing is written in the assistant's voice, the latest results
stay whole, and a model that copies a shortened value into a new call is refused before it runs."""
import json
import unittest

from . import providers, request_context, tools

BIG_FILE = "\n".join("line %d: double value = parseUnary(); // calculator code" % n for n in range(400))


def thread(reads=12):
    """A coding thread: the request, then many large file reads, each answered."""
    messages = [{"role": "system", "content": "System instructions"},
                {"role": "user", "content": "Fix the calculator's Evaluator.java"}]
    for n in range(reads):
        cid = "r%d" % n
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": cid, "type": "function", "function": {
                             "name": "read", "arguments": json.dumps({"path": "src/File%d.java" % n})}}]})
        messages.append({"role": "tool", "tool_call_id": cid, "content": "file %d\n%s" % (n, BIG_FILE)})
    return messages


class ProjectionTests(unittest.TestCase):
    def project(self, messages, definitions=()):
        return request_context.project({"provider": "openai"}, messages, list(definitions))

    def test_nothing_is_written_in_the_assistants_voice(self):
        original = thread()
        before = json.dumps(original)
        projected = self.project(original)
        self.assertEqual(json.dumps(original), before)                    # the thread on disk is untouched
        self.assertLess(len(json.dumps(projected, ensure_ascii=False)), len(before))
        self.assertNotIn("Historical tool records", json.dumps(projected, ensure_ascii=False))
        for m in projected:
            if m["role"] == "assistant":
                self.assertTrue(m.get("tool_calls"), m)                  # every older call is still a call
                self.assertEqual(m["content"], "")
        self.assertEqual([m["role"] for m in projected], [m["role"] for m in original])
        self.assertEqual(projected[-1]["role"], "tool")                   # the request never ends on an assistant text

    def test_the_latest_results_stay_whole_and_older_ones_are_shortened(self):
        original = thread()
        projected = self.project(original)
        results = [m for m in projected if m["role"] == "tool"]
        whole = results[-request_context.PROTECT:]
        for old, new in zip([m for m in original if m["role"] == "tool"][-request_context.PROTECT:], whole):
            self.assertEqual(old["content"], new["content"])
        self.assertIn("characters of this earlier value omitted", results[0]["content"])
        self.assertIn("read the file or run the command again", results[0]["content"])
        self.assertTrue(results[0]["content"].startswith("file 0\n"))

    def test_shortened_arguments_stay_valid_json_with_the_same_keys(self):
        original = [{"role": "user", "content": "Build my game"}]
        for n in range(15):
            original += [{"role": "assistant", "content": "", "tool_calls": [{"id": "w%d" % n, "type": "function",
                          "function": {"name": "write", "arguments": json.dumps(
                              {"path": "src/%d.js" % n, "content": "x" * 20000})}}]},
                         {"role": "tool", "tool_call_id": "w%d" % n, "content": "wrote src/%d.js" % n}]
        projected = self.project(original, tools.schemas(["write"]))
        self.assertLess(len(json.dumps(projected)), 96000)
        for m in projected:
            for c in m.get("tool_calls") or []:
                args = json.loads(c["function"]["arguments"])
                self.assertEqual(set(args), {"path", "content"})
                self.assertTrue(args["path"].startswith("src/"))
        ids = [c["id"] for m in projected for c in m.get("tool_calls") or []]
        self.assertEqual(ids, ["w%d" % n for n in range(15)])

    def test_a_short_thread_is_sent_as_it_is(self):
        original = thread(reads=1)
        self.assertEqual(self.project(original), original)

    def test_a_copied_placeholder_is_refused_before_any_tool_runs(self):
        from .tool_protocol import validate_completion, InvalidToolResponse
        completion = providers.Completion()
        copied = "class A {\n" + request_context.OMITTED % 1234 + "\n}"
        completion.tool_calls = [{"id": "a", "name": "write",
                                  "arguments": json.dumps({"path": "A.java", "content": copied})}]
        with self.assertRaises(InvalidToolResponse):
            validate_completion(completion, tools.schemas(["write"]))
        completion.tool_calls = [{"id": "a", "name": "write",
                                  "arguments": json.dumps({"path": "A.java", "content": copied}, ensure_ascii=True)}]
        with self.assertRaises(InvalidToolResponse):
            validate_completion(completion, tools.schemas(["write"]))
        completion.tool_calls = [{"id": "a", "name": "write",
                                  "arguments": json.dumps({"path": "A.java", "content": "class A {}"})}]
        validate_completion(completion, tools.schemas(["write"]))
