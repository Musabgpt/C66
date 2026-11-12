"""Hermetic tests for the mail the assistant sends (no Gmail: the connector is a stand-in)."""

import base64
import email
import os
import tempfile
import unittest
from email import policy
from types import SimpleNamespace
from unittest import mock

from . import assistant_mail as mail, connectors, tools

ORIGINAL = {"id": "m1", "threadId": "t9", "payload": {"headers": [
    {"name": "From", "value": "Sara Ahmad <sara@firm.example>"}, {"name": "To", "value": "me@gmail.com, omar@firm.example"},
    {"name": "Cc", "value": "Lina <lina@firm.example>"}, {"name": "Subject", "value": "موعد الاجتماع"},
    {"name": "Message-ID", "value": "<abc@firm.example>"}, {"name": "References", "value": "<first@firm.example>"}]}}


class AssistantMailTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        mail._SENDER.update(at=0.0, name="", address="")

        def request(provider, method, path, body=None, query=None, raw=None, mime=None):
            if path.endswith("/profile"):
                return {"emailAddress": "me@gmail.com"}
            if path.endswith("/settings/sendAs"):
                return {"sendAs": [{"sendAsEmail": "me@gmail.com", "displayName": "مصعب علي", "isPrimary": True}]}
            if path.endswith("/messages/m1"):
                self.asked = query
                return ORIGINAL
            if path.endswith("/messages/send"):
                self.sent.append(body)
                return {"id": "sent1", "threadId": body.get("threadId", "new")}
            raise AssertionError(path)
        patch = mock.patch.object(connectors, "request", request)
        patch.start()
        self.addCleanup(patch.stop)
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.ctx = SimpleNamespace(root=self.root.name, session=SimpleNamespace(dirs=[]))

    def message(self, n=-1):
        raw = self.sent[n]["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)

    def test_a_message_is_whole(self):
        text, meta = mail.send(self.ctx, "sara@firm.example", "عرض السعر المطلوب",
                               "مرحباً سارة،\n\nأرفقت لك عرض السعر كما طلبتِ. الرابط: https://firm.example/q/7.\n\nمع التحية،\nمصعب")
        msg = self.message()
        self.assertEqual(str(msg["From"]), "مصعب علي <me@gmail.com>")              # by name, not a bare address
        self.assertEqual((str(msg["To"]), str(msg["Subject"])), ("sara@firm.example", "عرض السعر المطلوب"))
        self.assertTrue(msg["Date"])
        self.assertEqual([p.get_content_type() for p in msg.walk()], ["multipart/alternative", "text/plain", "text/html"])
        html = msg.get_body(("html",)).get_content()
        self.assertIn('<p dir="auto"', html)                                       # Arabic is not shown left to right
        self.assertIn('<a href="https://firm.example/q/7">https://firm.example/q/7</a>.', html)
        self.assertIn("مرحباً سارة،", msg.get_body(("plain",)).get_content())
        self.assertNotIn(b"\xd9", base64.urlsafe_b64decode(self.sent[-1]["raw"] + "=="))   # headers and body are encoded
        self.assertIn("Sent to sara@firm.example. Subject: عرض السعر المطلوب", text)
        self.assertNotIn("may be held as spam", text)
        self.assertNotIn("signed with", text)                                      # it was signed already
        self.assertIn("receiving service's decision", text)
        self.assertEqual(meta, {"connector": "gmail", "id": "sent1", "thread": "new"})

    def test_what_reads_as_spam_is_refused_or_said(self):
        for bad in ({"to": "", "subject": "s", "text": "t"}, {"to": "not an address", "subject": "Hello there", "text": "t" * 30},
                    {"to": "a@b.co", "subject": " ", "text": "t" * 30}, {"to": "a@b.co", "subject": "Report", "text": ""}):
            with self.assertRaises(tools.ToolError):
                mail.send(self.ctx, **bad)
        self.assertEqual(self.sent, [])
        text, _ = mail.send(self.ctx, "a@b.co", "test", "https://x.example/y")
        self.assertIn("It may be held as spam: the subject says nothing", text)
        self.assertIn("almost only a link", text)
        self.assertEqual(mail.concerns("Meeting on Sunday at ten", "Dear Omar, can we move the meeting to eleven? Thanks."), [])
        self.assertTrue(mail.concerns("BUY NOW CHEAP", "This is a normal sentence about the thing."))
        self.assertTrue(mail.concerns("Our offer", "Hurry!!! Only today, the best price for you my friend."))

    def test_a_message_ends_with_the_users_name(self):
        long = "I am writing to ask about the delivery of my order number 7712, which was due last week."
        text, _ = mail.send(self.ctx, "shop@x.example", "Order 7712 has not arrived", "Hello,\n\n" + long)
        self.assertTrue(self.message().get_body(("plain",)).get_content().rstrip().endswith("مصعب علي"))
        self.assertIn("signed with the user's name", text)
        self.assertEqual(mail.signed("Hello,\n\n" + long + "\n\nRegards,\nMusab", "مصعب علي"), "Hello,\n\n" + long + "\n\nRegards,\nMusab")
        self.assertEqual(mail.signed("Thanks a lot\nمصعب", "مصعب علي"), "Thanks a lot\nمصعب")
        self.assertEqual(mail.signed(long, ""), long)                              # no name known: nothing is invented

    def test_a_reply_goes_into_the_conversation_it_answers(self):
        text, meta = mail.reply(self.ctx, "m1", "أهلاً سارة،\n\nيناسبني يوم الأحد الساعة العاشرة.\n\nمصعب")
        self.assertEqual(self.asked, {"format": "metadata"})
        self.assertEqual(self.sent[-1]["threadId"], "t9")
        msg = self.message()
        self.assertEqual(str(msg["To"]), "Sara Ahmad <sara@firm.example>")
        self.assertIsNone(msg["Cc"])
        self.assertEqual(str(msg["Subject"]), "Re: موعد الاجتماع")
        self.assertEqual(str(msg["In-Reply-To"]), "<abc@firm.example>")
        self.assertEqual(str(msg["References"]), "<first@firm.example> <abc@firm.example>")
        self.assertIn("Replied to Sara Ahmad <sara@firm.example>. Subject: Re: موعد الاجتماع", text)
        self.assertEqual(meta["thread"], "t9")
        mail.reply(self.ctx, "m1", "Thanks all, Sunday at ten works for me.\n\nMusab", all=True)
        msg = self.message()
        self.assertEqual(str(msg["Cc"]), "omar@firm.example, Lina <lina@firm.example>")    # everyone but the user
        for bad in (("", "text"), ("m1", " ")):
            with self.assertRaises(tools.ToolError):
                mail.reply(self.ctx, *bad)

    def test_a_subject_already_a_reply_is_kept(self):
        ORIGINAL["payload"]["headers"][3]["value"] = "RE: موعد الاجتماع"
        self.addCleanup(ORIGINAL["payload"]["headers"][3].__setitem__, "value", "موعد الاجتماع")
        mail.reply(self.ctx, "m1", "Sunday at ten works for me, thank you.\n\nMusab")
        self.assertEqual(str(self.message()["Subject"]), "RE: موعد الاجتماع")

    def test_files_of_the_conversation_are_attached(self):
        path = os.path.join(self.root.name, "عرض.pdf")
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4 demo")
        text, _ = mail.send(self.ctx, "a@b.co", "The quotation you asked for", "Hello,\n\nThe quotation is attached.\n\nMusab", attach="عرض.pdf")
        parts = [(p.get_content_type(), p.get_filename()) for p in self.message().iter_attachments()]
        self.assertEqual(parts, [("application/pdf", "عرض.pdf")])
        self.assertIn("Attached: عرض.pdf", text)
        with tempfile.NamedTemporaryFile() as other:
            for bad in (other.name, "missing.pdf"):
                with self.assertRaises(tools.ToolError):
                    mail.send(self.ctx, "a@b.co", "The quotation you asked for", "Attached.", attach=bad)

    def test_addresses_however_they_are_written(self):
        self.assertEqual(mail.addresses("a@b.co; Omar Ali <omar@x.org>، c@d.io"), ["a@b.co", "Omar Ali <omar@x.org>", "c@d.io"])
        self.assertEqual(mail.addresses(""), [])
        with self.assertRaises(tools.ToolError):
            mail.addresses("omar at x.org")

    def test_the_tools_take_the_engines_place(self):
        before = dict(tools.REGISTRY), dict(connectors.OPERATIONS)
        self.addCleanup(lambda: (tools.REGISTRY.clear(), tools.REGISTRY.update(before[0]),
                                 connectors.OPERATIONS.clear(), connectors.OPERATIONS.update(before[1])))
        mail.install()
        self.assertIn("attach", tools.REGISTRY["gmail_send"].params)
        self.assertEqual(tools.REGISTRY["gmail_reply"].required, ["id", "text"])
        self.assertEqual(connectors.OPERATIONS["gmail_reply"][0], "gmail")           # offered when Gmail is connected
        text, _ = tools.REGISTRY["gmail_send"].fn(self.ctx, to="a@b.co", subject="Tomorrow's delivery time", text="Hello,\n\nPlease deliver after five.\n\nMusab")
        self.assertIn("Sent to a@b.co", text)


if __name__ == "__main__":
    unittest.main()
