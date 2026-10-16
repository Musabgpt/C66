import unittest
from types import SimpleNamespace

from . import document_engine


class DocumentEngineTest(unittest.TestCase):
    def test_simple_pdf_uses_native_reader_when_ocr_not_requested(self):
        result = document_engine.select({"document_read"}, "paper.pdf", "read")
        self.assertEqual(result["route"], "native")
        self.assertEqual(result["tools"], ["document_read"])

    def test_scanned_pdf_prefers_verified_docling(self):
        names = {"document_read", "mcp__docling__convert_document_into_docling_document"}
        result = document_engine.select(names, "scan.pdf", "read", needs_ocr=True)
        self.assertEqual(result["route"], "docling")
        self.assertTrue(result["availability"]["docling"])

    def test_structured_pdf_without_docling_is_not_silently_downgraded(self):
        result = document_engine.select({"document_read"}, "paper.pdf", "read", structured=True)
        self.assertEqual(result["route"], "unavailable")

    def test_office_document_never_fakes_native_support(self):
        result = document_engine.select({"document_read"}, "report.docx", "read")
        self.assertEqual(result["route"], "unavailable")
        self.assertFalse(result["availability"]["docling"])

    def test_office_document_routes_to_docling_when_connected(self):
        names = {
            "mcp__docling__convert_document_into_docling_document",
            "mcp__docling__export_docling_document_to_markdown",
        }
        result = document_engine.select(names, "slides.pptx", "convert", preserve_layout=True)
        self.assertEqual(result["route"], "docling")
        self.assertEqual(len(result["tools"]), 2)

    def test_zip_create_does_not_claim_document_create_support(self):
        result = document_engine.select({"document_create"}, "files.zip", "create")
        self.assertEqual(result["route"], "unavailable")

    def test_zip_stays_on_native_archive_tools(self):
        names = {"archive_pack", "archive_extract", "mcp__docling__convert_document_into_docling_document"}
        result = document_engine.select(names, "files.zip", "archive")
        self.assertEqual(result["route"], "native")
        self.assertIn("archive_pack", result["tools"])

    def test_route_uses_only_session_tools(self):
        class Handler:
            def __init__(self):
                self.service = SimpleNamespace(get=lambda sid: SimpleNamespace(
                    tool_names=["document_read", "mcp__docling__convert_document_into_docling_document"]
                ))
            def _query(self):
                return {"session": "abc", "path": "scan.pdf", "needs_ocr": "1"}
            def _json(self, data, status=200):
                self.response = (data, status)

        handler = Handler()
        self.assertTrue(document_engine.route(handler, "GET", "/api/document-engine"))
        self.assertEqual(handler.response[1], 200)
        self.assertEqual(handler.response[0]["route"], "docling")


if __name__ == "__main__":
    unittest.main()
