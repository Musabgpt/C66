"""Phase 4 DocumentEngine routing for MusabAI.

The built-in document tools remain the lightweight, safe path for text/HTML,
ordinary PDFs and ZIPs. A verified Docling MCP server is preferred when the
task needs OCR, layout/table understanding, Office formats or structured
Docling conversion. The selector reports only tools actually exposed to the
current session; it never claims Docling is present because a catalog card
exists.
"""
import json
import os
import re
from urllib.parse import urlsplit

from . import tools

NAMES = ["document_engine_selector"]
DOCLING_PREFIX = "mcp__docling__"
NATIVE = {
    "document_create",
    "document_read",
    "document_download",
    "archive_pack",
    "archive_extract",
}
SIMPLE_READ = {".md", ".txt", ".html", ".pdf", ".zip"}
SIMPLE_CREATE = {".md", ".txt", ".html", ".pdf"}
DOCLING_FORMATS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".csv",
    ".xml", ".json", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp",
}


def _extension(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        path = urlsplit(text).path if "://" in text else text
    except ValueError:
        path = text
    return os.path.splitext(path.lower())[1]


def _docling_tools(names):
    return sorted(name for name in names if name.startswith(DOCLING_PREFIX))


def _native_tools(names):
    return sorted(name for name in names if name in NATIVE)


def _has(names, value):
    return value in names


def select(names, path="", task="read", needs_ocr=False, preserve_layout=False, structured=False):
    names = set(names or ())
    task = str(task or "read").strip().lower()
    if task not in {"read", "convert", "extract", "create", "edit", "archive"}:
        task = "read"
    ext = _extension(path)
    docling = _docling_tools(names)
    native = _native_tools(names)

    complex_doc = bool(needs_ocr or preserve_layout or structured or (
        ext and ext not in SIMPLE_READ and ext in DOCLING_FORMATS
    ))
    if task in {"convert", "extract"} and (needs_ocr or preserve_layout or structured):
        complex_doc = True

    if docling and (
        complex_doc
        or ext in {".docx", ".pptx", ".xlsx", ".csv", ".xml", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
        or task in {"convert", "extract"} and ext in DOCLING_FORMATS
    ):
        route = {
            "route": "docling",
            "label": "Docling MCP",
            "tools": docling,
            "reason": "A verified Docling MCP server is exposed and this task benefits from structured document understanding.",
        }
    elif task == "archive" and (_has(names, "archive_pack") or _has(names, "archive_extract")):
        route = {
            "route": "native",
            "label": "MusabAI native documents",
            "tools": [name for name in ("archive_pack", "archive_extract") if name in names],
            "reason": "ZIP handling is already supported safely inside the workspace.",
        }
    elif task in {"read", "extract"} and not complex_doc and ext in SIMPLE_READ and _has(names, "document_read"):
        route = {
            "route": "native",
            "label": "MusabAI native documents",
            "tools": ["document_read"],
            "reason": "The built-in reader is the lightweight path for this format.",
        }
    elif task == "create" and not complex_doc and ext in SIMPLE_CREATE and _has(names, "document_create"):
        route = {
            "route": "native",
            "label": "MusabAI native documents",
            "tools": ["document_create"],
            "reason": "The built-in creator supports this output format.",
        }
    elif docling and (not ext or ext in DOCLING_FORMATS):
        route = {
            "route": "docling",
            "label": "Docling MCP",
            "tools": docling,
            "reason": "Docling is connected and no lighter built-in route satisfies the requested document operation.",
        }
    elif _has(names, "document_read") and task == "read" and not ext:
        route = {
            "route": "native",
            "label": "MusabAI native documents",
            "tools": ["document_read"],
            "reason": "No format was supplied; use the built-in reader after resolving the file path.",
        }
    else:
        route = {
            "route": "unavailable",
            "label": "No document backend",
            "tools": [],
            "reason": "This document task needs Docling or a built-in document tool that is not currently exposed.",
        }

    return dict(route, format=ext.lstrip("."), requirements={
        "ocr": bool(needs_ocr),
        "preserve_layout": bool(preserve_layout),
        "structured": bool(structured),
        "task": task,
    }, availability={
        "docling": bool(docling),
        "native": bool(native),
    })


def install():
    @tools.tool(
        "document_engine_selector",
        "Choose the document backend that is actually available: lightweight native tools for simple files, verified Docling MCP for OCR/layout/Office/structured conversion.",
        {
            "path": {"type": "string", "description": "document path or URL, if known"},
            "task": {"type": "string", "description": "read, convert, extract, create, edit, or archive"},
            "needs_ocr": {"type": "boolean", "description": "image/scanned document needs OCR"},
            "preserve_layout": {"type": "boolean", "description": "layout/tables/structure matter"},
            "structured": {"type": "boolean", "description": "request structured Docling output"},
        },
        [],
        "meta",
    )
    def document_engine_selector(ctx, path="", task="read", needs_ocr=False, preserve_layout=False, structured=False):
        session = getattr(ctx, "session", None)
        names = set(getattr(session, "tool_names", None) or ())
        result = select(names, path, task, needs_ocr, preserve_layout, structured)
        return json.dumps(result, ensure_ascii=False), {"document_route": result["route"]}


def route(handler, method, path, body=None):
    if path != "/api/document-engine":
        return False
    if method != "GET":
        handler._json({"error": "Method not allowed"}, 405)
        return True
    try:
        data = handler._query()
        sid = data.get("session", "")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
            raise ValueError("Open a project conversation first")
        session = handler.service.get(sid)
        names = set(getattr(session, "tool_names", None) or ())
        handler._json(select(
            names,
            data.get("path", ""),
            data.get("task", "read"),
            str(data.get("needs_ocr", "")).lower() in {"1", "true", "yes"},
            str(data.get("preserve_layout", "")).lower() in {"1", "true", "yes"},
            str(data.get("structured", "")).lower() in {"1", "true", "yes"},
        ))
    except (ValueError, TypeError) as error:
        handler._json({"error": str(error)[:300]}, 400)
    return True
