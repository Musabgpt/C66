"""Hermetic tests for the files of a conversation (made, read back, listed, served to the page)."""

import io
import json
import os
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock
from xml.etree import ElementTree

from . import assistant_files as files, assistant_office as office, phone, phone_ext, tools

PLAN = """# خطة الرحلة إلى إسطنبول
هذه **خطة** مختصرة، والتفاصيل في [الموقع](https://example.com/trip).

## الأيام
- اليوم الأول: الوصول
- اليوم الثاني: جولة
1. حجز الطيران
2. حجز الفندق

## Budget
| Item | Cost | Notes |
|------|------|-------|
| Flight | 1200 | direct |
| فندق | 900.5 | 3 ليال |
| Total | =B2+B3 | |
"""


def parts(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {name: z.read(name) for name in z.namelist()}


class AssistantFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.ctx = SimpleNamespace(root=self.root, session=SimpleNamespace(dirs=[]), agent=SimpleNamespace())

    def make(self, name, content, title=""):
        return files.t_file_create(self.ctx, name, content, title)

    def test_every_part_of_an_office_file_is_well_formed(self):
        for data, needed in ((office.docx(PLAN), ("word/document.xml", "word/styles.xml", "word/numbering.xml")),
                             (office.xlsx(PLAN), ("xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml")),
                             (office.pptx(PLAN), ("ppt/presentation.xml", "ppt/slides/slide1.xml", "ppt/slideMasters/slideMaster1.xml",
                                                  "ppt/slideLayouts/slideLayout1.xml", "ppt/theme/theme1.xml"))):
            inside = parts(data)
            for name in ("[Content_Types].xml", "_rels/.rels") + needed:
                self.assertIn(name, inside)
            for name, raw in inside.items():
                ElementTree.fromstring(raw)                                # every part parses
            types = inside["[Content_Types].xml"].decode()
            for name in inside:
                if name.endswith(".xml") and not name.startswith("["):
                    self.assertIn('PartName="/%s"' % name, types, name)        # and is declared
        self.assertEqual(office.docx(PLAN), office.docx(PLAN))                 # the same text gives the same file

    def test_a_word_file_keeps_its_structure_and_its_direction(self):
        data = office.docx(PLAN)
        back = office.read_docx(data)
        for line in ("# خطة الرحلة إلى إسطنبول", "هذه خطة مختصرة، والتفاصيل في الموقع (https://example.com/trip).", "## الأيام",
                     "- اليوم الأول: الوصول", "- حجز الفندق", "## Budget", "| Item | Cost | Notes |", "| فندق | 900.5 | 3 ليال |"):
            self.assertIn(line, back)
        doc = parts(data)["word/document.xml"].decode()
        self.assertIn("<w:bidi/>", doc)                                        # Arabic paragraphs are laid out from the right
        self.assertIn("<w:b/><w:bCs/>", doc)                                   # **bold**
        self.assertEqual(doc.count('<w:numId w:val="2"/>'), 2)                 # the numbered list counts by itself
        self.assertNotIn("**", back)

    def test_a_sheet_has_numbers_formulas_and_a_header(self):
        sheet = parts(office.xlsx(PLAN))["xl/worksheets/sheet1.xml"].decode()
        self.assertIn('<c r="B2"><v>1200</v></c>', sheet)                      # a number, not text
        self.assertIn('<c r="B4"><f>B2+B3</f></c>', sheet)
        self.assertIn('<c r="A1" s="1" t="inlineStr">', sheet)                  # the header row is styled
        self.assertIn("frozen", sheet)
        self.assertIn('name="Budget"', parts(office.xlsx(PLAN))["xl/workbook.xml"].decode())
        book = office.xlsx('{"المبيعات": [{"الشهر": "يناير", "المبلغ": 1200, "مدفوع": true}], "Codes": [["code", "phone"], ["007", "0551234567"]]}')
        back = office.read_xlsx(book)
        self.assertEqual(back, "## المبيعات\nالشهر\tالمبلغ\tمدفوع\nيناير\t1200\tTRUE\n## Codes\ncode\tphone\n007\t0551234567")
        self.assertIn('rightToLeft="1"', parts(book)["xl/worksheets/sheet1.xml"].decode())
        self.assertIn("<is><t", parts(book)["xl/worksheets/sheet2.xml"].decode().split('r="A2"')[1][:60])   # 007 stays text
        self.assertEqual(office.read_xlsx(office.xlsx("name,age\nMusab,31\n")), "## Sheet1\nname\tage\nMusab\t31")
        self.assertEqual(office.sheets_of("a;b\n1;2")[0][1], [["a", "b"], ["1", "2"]])

    def test_a_presentation_is_one_slide_per_heading(self):
        deck = office.pptx("# Quarterly review\nPrepared by Musab\n# Results\n- Revenue up 12%\n- Costs down 3%\n---\nThank you")
        self.assertEqual(office.read_pptx(deck), "# Slide 1: Quarterly review\n- Prepared by Musab\n# Slide 2: Results\n"
                                                 "- Revenue up 12%\n- Costs down 3%\n# Slide 3: Thank you")
        self.assertEqual(len([n for n in parts(deck) if n.startswith("ppt/slides/slide")]), 3)
        arabic = parts(office.pptx("# الخطة\n- توظيف مهندس"))["ppt/slides/slide1.xml"].decode()
        self.assertIn('rtl="1"', arabic)

    def test_files_are_made_in_the_conversation_and_shown_as_cards(self):
        text, meta = self.make("../../etc/خطة الرحلة.docx", PLAN)
        path = os.path.join(self.root, "Files", "خطة الرحلة.docx")
        self.assertTrue(os.path.isfile(path))                                  # only the name counts: nothing leaves the folder
        self.assertEqual((meta["file"]["name"], meta["file"]["kind"], meta["file"]["shared"]), ("خطة الرحلة.docx", "docx", False))
        self.assertEqual(meta["file"]["mime"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertIn("Made خطة الرحلة.docx", text)
        self.assertIn("gmail_send attach=خطة الرحلة.docx", text)
        self.make("data.csv", "name,city\nمصعب,الرياض\n")
        with open(os.path.join(self.root, "Files", "data.csv"), "rb") as f:
            self.assertTrue(f.read().startswith(b"\xef\xbb\xbf"))              # Excel reads Arabic in it
        self.make("event.ics", "BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        self.make("a.json", '{"b": [1, 2]}')
        for name, content in (("report", "x"), ("virus.exe", "x"), ("empty.txt", "  "), (".hidden", "x"), ("bad.json", "{not json")):
            with self.assertRaises(tools.ToolError):
                self.make(name, content)

    def test_a_pdf_is_made_by_the_phone(self):
        calls = []

        def call(action, timeout=90, **args):
            calls.append((action, args))
            with open(args["path"], "wb") as f:
                f.write(b"%PDF-1.4")
            return {"ok": True}
        with mock.patch.object(phone, "available", lambda: True), mock.patch.object(phone, "call", call):
            text, meta = self.make("plan.pdf", "# Plan\n- **one**\n| a | b |\n| 1 | 2 |", title="My plan")
        self.assertEqual(calls[0][0], "document_pdf")
        self.assertEqual(calls[0][1]["content"], "PLAN\n\n• one\na  |  b\n1  |  2")     # the marks are not printed
        self.assertEqual(calls[0][1]["title"], "My plan")
        self.assertEqual(meta["file"]["kind"], "pdf")
        with mock.patch.object(phone, "available", lambda: False), self.assertRaises(tools.ToolError):
            self.make("other.pdf", "text")

    def test_what_was_made_or_shared_is_read_back(self):
        self.make("plan.docx", PLAN)
        self.make("budget.xlsx", PLAN)
        self.make("deck.pptx", "# One\n- a\n# Two\n- b")
        self.make("page.html", "<html><head><style>p{}</style></head><body><h1>Title</h1><p>Hello <b>there</b></p><script>x()</script></body></html>")
        shared = os.path.join(self.root, "Shared")
        os.makedirs(shared)
        with open(os.path.join(shared, "letter.eml"), "wb") as f:
            f.write(b"From: Sara <sara@x.org>\r\nSubject: Meeting\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSee you at ten.\r\n")
        with open(os.path.join(shared, "notes.rtf"), "w") as f:
            f.write(r"{\rtf1\ansi Hello \b world\b0\par Second line}")
        with open(os.path.join(shared, "old.doc"), "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0")
        with open(os.path.join(shared, "arabic.txt"), "wb") as f:
            f.write("مرحبا".encode("cp1256"))
        read = lambda name, **kw: files.t_file_read(self.ctx, name, **kw)[0]
        self.assertIn("# خطة الرحلة إلى إسطنبول", read("plan.docx"))
        self.assertIn("Flight\t1200\tdirect", read("budget.xlsx"))
        self.assertIn("# Slide 2: Two\n- b", read("deck.pptx"))
        self.assertTrue(read("page.html").endswith("Title\n\nHello there"))     # the words, not the markup or the script
        self.assertIn("From: Sara <sara@x.org>\nSubject: Meeting\n\nSee you at ten.", read(os.path.join(shared, "letter.eml")))
        self.assertIn("Hello world\nSecond line", read("notes.rtf"))
        self.assertIn("مرحبا", read("arabic.txt"))                              # an old Arabic encoding is understood
        self.assertIn("plan.docx (docx, ", read("PLAN"))                        # a name a little off still finds it
        for bad in ("old.doc", "missing.pdf", "/etc/passwd", ""):
            with self.assertRaises(tools.ToolError):
                read(bad)

    def test_a_long_file_comes_in_parts_and_a_picture_is_looked_at(self):
        self.make("long.txt", "\n".join("line %d of the report" % i for i in range(2000)))
        text, meta = files.t_file_read(self.ctx, "long.txt")
        self.assertIn("part 1 of 4: part=2 for the next", text.split("\n")[0])
        self.assertEqual(meta["parts"], 4)
        self.assertIn("part 4 of 4, the last", files.t_file_read(self.ctx, "long.txt", part=4)[0])
        with self.assertRaises(tools.ToolError):
            files.t_file_read(self.ctx, "long.txt", part=5)
        os.makedirs(os.path.join(self.root, "Shared"))
        with open(os.path.join(self.root, "Shared", "receipt.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff")
        seen = []
        with mock.patch.object(phone_ext, "describe", lambda agent, path, question, what: seen.append((path, question)) or "Total: 45 SAR"):
            self.assertIn("Total: 45 SAR", files.t_file_read(self.ctx, "receipt.jpg", question="what is the total?")[0])
        self.assertEqual(seen[0][1], "what is the total?")
        with mock.patch.object(phone_ext, "describe", lambda *a: ""), self.assertRaises(tools.ToolError):
            files.t_file_read(self.ctx, "receipt.jpg")

    def test_the_conversations_files_are_listed_newest_first(self):
        self.assertIn("There are no files yet", files.t_file_list(self.ctx)[0])
        self.make("a.txt", "one")
        os.makedirs(os.path.join(self.root, "Shared"))
        shared = os.path.join(self.root, "Shared", "cv.pdf")
        with open(shared, "wb") as f:
            f.write(b"%PDF")
        os.utime(shared, (2000000000, 2000000000))
        text, meta = files.t_file_list(self.ctx)
        self.assertEqual([f["name"] for f in meta["files"]], ["cv.pdf", "a.txt"])
        self.assertIn("cv.pdf — pdf, 4 B, ", text)
        self.assertIn("(shared by the user)", text.split("\n")[0])

    def test_the_page_gets_only_the_conversations_files(self):
        self.make("a.txt", "hello")

        class Handler:
            def __init__(self, path):
                self.path, self.status, self.headers, self.sent, self.body = path, None, {}, None, []
                self.wfile = SimpleNamespace(write=self.body.append)

            def _query(self):
                from urllib.parse import parse_qs, urlsplit
                return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

            def _json(self, data, status=200):
                self.status, self.sent = status, data

            def send_response(self, status):
                self.status = status

            def send_header(self, name, value):
                self.headers[name] = value

            def end_headers(self):
                pass
        with mock.patch.object(os.path, "expanduser", lambda p: self.root if p == "~" else p):
            h = Handler("/api/assistant/files")
            self.assertTrue(files.route(h, "GET", "/api/assistant/files"))
            self.assertEqual([f["name"] for f in h.sent["files"]], ["a.txt"])
            from urllib.parse import quote
            h = Handler("/api/assistant/file?path=" + quote(os.path.join(self.root, "Files", "a.txt")))
            files.route(h, "GET", "/api/assistant/file")
            self.assertEqual((h.status, h.body, h.headers["Content-Type"]), (200, [b"hello"], "text/plain"))
            with open(os.path.join(self.root, "secret.txt"), "w") as f:
                f.write("no")
            for outside in (os.path.join(self.root, "secret.txt"), "/etc/passwd", os.path.join(self.root, "Files", "..", "secret.txt")):
                h = Handler("/api/assistant/file?path=" + quote(outside))
                files.route(h, "GET", "/api/assistant/file")
                self.assertEqual(h.status, 404, outside)
        self.assertFalse(files.route(Handler("/x"), "POST", "/api/assistant/file"))


if __name__ == "__main__":
    unittest.main()
