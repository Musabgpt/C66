"""Hermetic tests for the conversation's own memory (what survives when a long conversation is shortened)."""

import unittest
from types import SimpleNamespace

from . import assistant_memory as memory

EVENTS = [
    {"type": "turn_start", "turn": 1, "text": "اسمي مصعب، ورحلتي إلى إسطنبول يوم 20 نوفمبر"},
    {"type": "assistant", "final": True, "text": "تمام، حفظت أن رحلتك إلى إسطنبول يوم 20 نوفمبر."},
    {"type": "turn_end", "turn": 1, "answer": "تمام"},
    {"type": "turn_start", "turn": 2, "text": "ابحث عن فندق قريب من تقسيم\n\nFiles attached to this message (open them with file_read):\n/home/Shared/passport.jpg"},
    {"type": "tool_start", "id": "a", "name": "browser", "args": {"op": "open", "url": "https://www.booking.com/"}},
    {"type": "tool_end", "id": "a", "name": "browser", "ok": True, "meta": {"url": "https://www.booking.com/", "title": "Booking.com"}},
    {"type": "tool_start", "id": "b", "name": "browser", "args": {"op": "click", "target": "Search"}},
    {"type": "tool_end", "id": "b", "name": "browser", "ok": True, "meta": {}},
    {"type": "tool_start", "id": "c", "name": "browser", "args": {"op": "fill", "fields": {"City": "Istanbul"}, "target": "Search"}},
    {"type": "tool_end", "id": "c", "name": "browser", "ok": True, "meta": {"title": "Istanbul hotels"}},
    {"type": "tool_start", "id": "x", "name": "web_fetch", "args": {"url": "https://x"}},
    {"type": "tool_end", "id": "x", "name": "web_fetch", "ok": False, "text": "timeout"},
    {"type": "tool_start", "id": "s1", "name": "browser", "args": {"op": "open", "url": "https://helper.example"}, "sub": "s-sub1"},
    {"type": "tool_end", "id": "s1", "name": "browser", "ok": True, "meta": {}, "sub": "s-sub1"},
    {"type": "assistant", "final": True, "text": "أرخص فندق قريب من تقسيم: Taksim Inn بـ 210 ر.س لليلة."},
    {"type": "turn_end", "turn": 2, "answer": "أرخص"},
    {"type": "turn_start", "turn": 3, "text": "اكتب الخطة في ملف وأرسلها لعمر"},
    {"type": "tool_start", "id": "d", "name": "file_create", "args": {"name": "plan.docx", "content": "# Plan"}},
    {"type": "tool_end", "id": "d", "name": "file_create", "ok": True, "meta": {"file": {"name": "plan.docx", "path": "/home/Files/plan.docx"}}},
    {"type": "tool_start", "id": "e", "name": "gmail_send", "args": {"to": "omar@x.org", "subject": "خطة الرحلة", "text": "..."}},
    {"type": "tool_end", "id": "e", "name": "gmail_send", "ok": True, "meta": {"id": "m1"}},
    {"type": "tool_start", "id": "f", "name": "phone", "args": {"action": "notification_reply", "target": "Sara", "text": "وصلت"}},
    {"type": "tool_end", "id": "f", "name": "phone", "ok": True, "meta": {}},
    {"type": "tool_start", "id": "g", "name": "scheduled_task_create", "args": {"title": "موعد الطيران", "when": "2026-11-20T06:00+03:00"}},
    {"type": "tool_end", "id": "g", "name": "scheduled_task_create", "ok": True, "meta": {}},
    {"type": "tool_start", "id": "h", "name": "my_info", "args": {"action": "set", "fields": {"passport": "N123", "name": "Musab"}}},
    {"type": "tool_end", "id": "h", "name": "my_info", "ok": True, "meta": {}},
    {"type": "turn_end", "turn": 3, "answer": "أرسلت الخطة لعمر."},
]


def conversation():
    """Messages as the engine keeps them: users, tool calls and results, final answers."""
    return [
        {"role": "user", "content": "اسمي مصعب، ورحلتي إلى إسطنبول يوم 20 نوفمبر"},
        {"role": "assistant", "content": "تمام، حفظت أن رحلتك إلى إسطنبول يوم 20 نوفمبر."},
        {"role": "user", "content": "ابحث عن فندق قريب من تقسيم"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "type": "function", "function": {"name": "browser", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "page text " * 400},
        {"role": "assistant", "content": "أرخص فندق قريب من تقسيم: Taksim Inn بـ 210 ر.س لليلة."},
        {"role": "user", "content": "اكتب الخطة في ملف وأرسلها لعمر"},
        {"role": "assistant", "content": "أرسلت الخطة لعمر."},
        {"role": "user", "content": "وماذا عن الطيران؟"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "z", "type": "function", "function": {"name": "browser", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "z", "content": "flights page"},
    ]


class AssistantMemoryTests(unittest.TestCase):
    def test_every_turn_is_recorded_from_what_the_conversation_showed(self):
        turns = memory.record(EVENTS)
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[0]["asked"], "اسمي مصعب، ورحلتي إلى إسطنبول يوم 20 نوفمبر")
        self.assertEqual(turns[0]["did"], [])
        self.assertEqual(turns[1]["did"], ["opened https://www.booking.com/ (Booking.com)",
                                           "filled a form on Istanbul hotels and clicked Search"])   # a click alone is not kept
        self.assertEqual(turns[1]["failed"], 1)
        self.assertNotIn("helper.example", str(turns))                          # a helper agent's own steps are not turns
        self.assertEqual(turns[2]["did"], ["made the file plan.docx", "emailed omar@x.org: خطة الرحلة", "replied to Sara: وصلت",
                                           "saved a reminder: موعد الطيران (2026-11-20T06:00+03:00)",
                                           "saved the user's details: name, passport"])
        self.assertEqual(turns[2]["answer"], "أرسلت الخطة لعمر.")              # from turn_end when no final event came

    def test_the_record_reads_turn_by_turn_and_keeps_the_newest_when_long(self):
        text = memory.record_text(EVENTS)
        self.assertTrue(text.startswith("1. The user asked: اسمي مصعب، ورحلتي إلى إسطنبول يوم 20 نوفمبر\n   Answered: تمام، حفظت"))
        self.assertIn("2. The user asked: ابحث عن فندق قريب من تقسيم\n   With the files: passport.jpg\n   Done: opened https://www.booking.com/", text)
        self.assertNotIn("/home/Shared", text)
        self.assertIn("3. The user asked: اكتب الخطة في ملف وأرسلها لعمر\n   Done: made the file plan.docx; emailed omar@x.org", text)
        many = []
        for n in range(100):
            many += [{"type": "turn_start", "text": "request number %d %s" % (n, "x" * 300)},
                     {"type": "assistant", "final": True, "text": "answer %d" % n}]
        text = memory.record_text(many)
        self.assertLessEqual(len(text), memory.RECORD_MAX + 100)
        self.assertIn("request number 99", text)
        self.assertNotIn("request number 0 ", text)
        self.assertTrue(text.startswith("("))
        self.assertIn("earlier turn(s) are not listed", text.split("\n")[0])
        self.assertEqual(memory.record_text([]), "")

    def test_the_last_exchanges_are_kept_word_for_word(self):
        msgs = conversation()
        pairs = memory.exchanges(msgs, 8)
        self.assertEqual([p[0]["content"] for p in pairs], ["اسمي مصعب، ورحلتي إلى إسطنبول يوم 20 نوفمبر", "ابحث عن فندق قريب من تقسيم",
                                                            "اكتب الخطة في ملف وأرسلها لعمر"])
        self.assertEqual(pairs[1][1], {"role": "assistant", "content": "أرخص فندق قريب من تقسيم: Taksim Inn بـ 210 ر.س لليلة."})
        self.assertEqual(len(memory.exchanges(msgs, 8, count=2)), 2)

    def test_a_shortened_conversation_keeps_the_record_the_summary_and_the_last_exchanges(self):
        session = SimpleNamespace(messages=conversation(), events=EVENTS, todo=[], goal="", active_objective="")
        out = memory.compact_messages(session, "Musab flies to Istanbul on 20 November.", "وماذا عن الطيران؟")
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user", "assistant", "user", "assistant", "user"])
        self.assertEqual(out[0]["content"], "اسمي مصعب، ورحلتي إلى إسطنبول يوم 20 نوفمبر")       # the first request
        note = out[1]["content"]
        self.assertIn("Musab flies to Istanbul on 20 November.", note)                         # the model's summary
        self.assertIn("[What this conversation asked and did, turn by turn", note)
        self.assertIn("emailed omar@x.org: خطة الرحلة", note)                                   # the record
        self.assertLess(note.index("turn by turn"), note.index("[Durable task anchor"))
        self.assertEqual([m["content"] for m in out[2:6]], ["ابحث عن فندق قريب من تقسيم", "أرخص فندق قريب من تقسيم: Taksim Inn بـ 210 ر.س لليلة.",
                                                           "اكتب الخطة في ملف وأرسلها لعمر", "أرسلت الخطة لعمر."])
        self.assertEqual(out[-1]["content"], "وماذا عن الطيران؟")                               # the message being worked on
        self.assertFalse(any(m.get("tool_calls") or m.get("role") == "tool" for m in out))     # no half of a tool exchange

    def test_an_empty_summary_loses_nothing(self):
        session = SimpleNamespace(messages=conversation(), events=EVENTS, todo=[], goal="", active_objective="")
        note = memory.compact_messages(session, "", "")[1]["content"]
        self.assertIn("The model gave no summary: the record below is complete.", note)
        self.assertIn("made the file plan.docx", note)
        one = SimpleNamespace(messages=[{"role": "user", "content": "hello"}], events=EVENTS[:3], todo=[], goal="", active_objective="")
        out = memory.compact_messages(one, "s", "")
        self.assertEqual([m["role"] for m in out], ["assistant", "user"])                     # a single turn: as the engine does it
        self.assertIn("1. The user asked:", out[0]["content"])

    def test_the_summary_has_room_and_is_asked_again_when_empty(self):
        asked = []

        class Client:
            def __init__(self, answers):
                self.answers = list(answers)

            def chat(self, msgs, **kw):
                asked.append((msgs[-1]["content"], kw["max_tokens"]))
                answer = self.answers.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return SimpleNamespace(content=answer)

        def agent(answers):
            return SimpleNamespace(session=SimpleNamespace(id="s1"), cancel=None, client=Client(answers),
                                   request_messages=lambda: [{"role": "system", "content": "x"}])
        self.assertEqual(memory.summarize(agent(["", "  notes  "]), note="the flight"), "notes")
        self.assertEqual(len(asked), 2)
        self.assertEqual(asked[0][1], memory.SUMMARY_TOKENS)
        self.assertTrue(asked[0][0].startswith(memory.COMPACT))
        self.assertTrue(asked[0][0].endswith(" Focus: the flight"))
        self.assertEqual(memory.summarize(agent(["", ""])), "")
        self.assertEqual(memory.summarize(agent([RuntimeError("rate limited")])), "")


if __name__ == "__main__":
    unittest.main()
