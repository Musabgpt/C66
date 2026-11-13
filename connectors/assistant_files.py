"""Files inside the conversation: the assistant makes them, reads them and hands them to the user.

file_create writes a file in the format its name asks for (a Word document, an Excel sheet, a presentation, a PDF,
text, a table, a web page, a calendar event, a contact card...), file_read opens what the user shared or what
was made (the same formats, and more: OpenDocument, e-books, saved emails, pictures through a vision model), and
file_list says what the conversation holds. The page shows a made file as a card that opens it on the phone or
shares it; the same file can be attached to an email (gmail_send attach) or given to a web form (browser upload).

Everything lives in two folders of the app's own home: Files (made here) and Shared (what the user gave).
"""
import email
import html
import json
import os
import re
import time
import zipfile
from email import policy

from . import assistant_office as office
from . import tools

FOLDER, SHARED = "Files", "Shared"
MAKE_MAX = 4 * 1024 * 1024               # characters of content a file is made from
READ_MAX = 32 * 1024 * 1024
PART = 12000                             # characters of a file given to the model at once
TEXT = ("txt", "md", "html", "htm", "csv", "tsv", "json", "xml", "svg", "ics", "vcf", "srt", "log", "ini", "yaml", "yml",
        "py", "js", "ts", "css", "sql", "sh", "tex")
OFFICE = ("docx", "xlsx", "pptx", "pdf")
PICTURES = ("png", "jpg", "jpeg", "webp", "gif", "bmp", "heic")
MAKES = OFFICE + TEXT
NAMES = ["file_create", "file_read", "file_list"]
MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown", "html": "text/html", "htm": "text/html",
        "csv": "text/csv", "tsv": "text/tab-separated-values", "json": "application/json", "xml": "application/xml",
        "svg": "image/svg+xml", "ics": "text/calendar", "vcf": "text/vcard", "png": "image/png", "jpg": "image/jpeg",
        "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif", "zip": "application/zip", "epub": "application/epub+zip"}


def kind(path):
    return os.path.splitext(str(path))[1].lstrip(".").lower()


def mime(path):
    return MIME.get(kind(path), "text/plain" if kind(path) in TEXT else "application/octet-stream")


def folder(root, name=FOLDER):
    return os.path.join(os.path.abspath(root), name)


def clean_name(name):
    """A file name of the conversation: the last part of what was given, with nothing that leaves the folder."""
    name = os.path.basename(str(name or "").replace("\\", "/").strip().strip("/"))
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "", name).strip(" .")
    if not name or name.startswith("."):
        raise tools.ToolError("give the file a name with its type, like report.docx, budget.xlsx or notes.txt")
    stem, ext = os.path.splitext(name)
    return stem[:100] + ext[:10]


def _size(n):
    return "%d B" % n if n < 1024 else "%.0f KB" % (n / 1024.0) if n < 1024 * 1024 else "%.1f MB" % (n / 1048576.0)


def card(path, root):
    """What the page needs to show a file: its name, kind, size and where it is."""
    return {"name": os.path.basename(path), "path": os.path.abspath(path), "kind": kind(path), "bytes": os.path.getsize(path),
            "mime": mime(path), "shared": os.path.abspath(path).startswith(folder(root, SHARED) + os.sep)}


def _plain(text):
    """Marked text as plain text for a PDF: headings without their marks, table rows as lines."""
    lines = []
    for block in office.blocks(text):
        if block[0] == "h":
            lines += ["", office.plain(block[2]).upper() if block[1] == 1 else office.plain(block[2]), ""]
        elif block[0] == "p":
            lines.append(office.plain(block[1]))
        elif block[0] == "li":
            lines.append("• " + office.plain(block[2]))
        elif block[0] == "table":
            lines += ["  |  ".join(office.plain(c) for c in row) for row in block[1]] + [""]
        elif block[0] == "break":
            lines.append("")
    return "\n".join(lines).strip()


def make(root, name, content, title=""):
    """Writes the file into the conversation's Files folder; its path."""
    name = clean_name(name)
    ext = kind(name)
    if ext not in MAKES:
        raise tools.ToolError("a .%s file cannot be made here. These can: %s" % (ext or "?", ", ".join("." + k for k in MAKES)))
    content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=1)
    if len(content) > MAKE_MAX:
        raise tools.ToolError("the content is too long for one file: split it")
    if not content.strip():
        raise tools.ToolError("the file would be empty: give its content")
    where = folder(root)
    os.makedirs(where, exist_ok=True)
    path = os.path.join(where, name)
    if ext == "pdf":
        from . import phone
        if not phone.available():
            raise tools.ToolError("a PDF is made by the phone's own renderer, which is not here: make a .docx or .html instead")
        out = phone.call("document_pdf", path=path, content=_plain(content), title=str(title or ""), timeout=90)
        if not out.get("ok"):
            raise tools.ToolError(out.get("error", "the PDF could not be made"))
        return path
    if ext == "docx":
        data = office.docx(content, str(title or ""))
    elif ext == "xlsx":
        data = office.xlsx(content)
    elif ext == "pptx":
        data = office.pptx(content, str(title or ""))
    elif ext == "csv":
        data = ("\ufeff" + content).encode("utf-8")       # the mark Excel needs to read Arabic in a CSV
    elif ext == "json":
        try:
            data = json.dumps(json.loads(content), ensure_ascii=False, indent=2).encode("utf-8")
        except ValueError:
            raise tools.ToolError("the content of a .json file has to be valid JSON")
    else:
        data = content.encode("utf-8")
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def find(ctx, name):
    """The file the model means: a path inside the conversation's folders, or just a name in Files or Shared."""
    raw = str(name or "").strip().strip('"\'')
    if not raw:
        raise tools.ToolError("which file? file_list shows the files of this conversation")
    root = os.path.abspath(ctx.root)
    tries = []
    if os.path.isabs(os.path.expanduser(raw)):
        tries.append(os.path.abspath(os.path.expanduser(raw)))
    else:
        tries += [os.path.join(root, raw), os.path.join(folder(root), os.path.basename(raw)),
                  os.path.join(folder(root, SHARED), os.path.basename(raw))]
    for path in tries:
        if os.path.isfile(path) and tools.inside(ctx, path):
            return path
    wanted = os.path.basename(raw).lower()
    for path in sorted(listing(root), key=lambda p: -os.path.getmtime(p)):      # a name a little off: the newest that fits
        base = os.path.basename(path).lower()
        if base == wanted or os.path.splitext(base)[0] == os.path.splitext(wanted)[0]:
            return path
    raise tools.ToolError("no file %s in this conversation. file_list shows what there is" % raw)


def listing(root):
    out = []
    for name in (FOLDER, SHARED):
        base = folder(root, name)
        if not os.path.isdir(base):
            continue
        for entry in os.listdir(base):
            path = os.path.join(base, entry)
            if os.path.isfile(path) and not entry.startswith(".") and not entry.endswith(".part"):
                out.append(path)
    return out


def _strip_tags(markup):
    markup = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(li|tr)>", "\n", markup)
    markup = re.sub(r"(?i)</(p|div|h\d|section|article|table|ul|ol)>", "\n\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    text = re.sub(r" *\n *", "\n", re.sub(r"[ \t\r\f\v]+", " ", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _epub(data):
    import io
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if re.search(r"\.(x?html?|xml)$", name, re.I) and not name.lower().endswith(("container.xml", ".opf", "toc.ncx")):
                text = _strip_tags(z.read(name).decode("utf-8", "replace"))
                if len(text) > 40:
                    out.append(text)
    return "\n\n".join(out)


def _eml(data):
    msg = email.message_from_bytes(data, policy=policy.default)
    lines = ["%s: %s" % (h, msg[h]) for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]
    body = msg.get_body(("plain", "html"))
    text = body.get_content() if body is not None else ""
    if body is not None and body.get_content_type() == "text/html":
        text = _strip_tags(text)
    attached = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    return "\n".join(lines) + "\n\n" + str(text).strip() + ("\n\nAttachments: " + ", ".join(attached) if attached else "")


def _rtf(text):
    text = re.sub(r"\\'[0-9a-fA-F]{2}", "", text)
    text = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), text)
    text = re.sub(r"\\par[d]?\b ?", "\n", text)
    text = re.sub(r"\{\\\*[^{}]*\}|\\[a-zA-Z]+-?\d* ?|[{}]", "", text)
    return text.strip()


def text_of(ctx, path, question=""):
    """What the file says, as text. A picture is described by a vision model (question: what to look for)."""
    ext = kind(path)
    size = os.path.getsize(path)
    if size > READ_MAX:
        raise tools.ToolError("the file is %s: too big to read here" % _size(size))
    if ext in PICTURES:
        from . import phone_ext
        try:
            seen = phone_ext.describe(ctx.agent, path, question or "Read everything written in it, then say what it shows.", "a file the user shared")
        except Exception as e:  # noqa: BLE001 - no model that sees answered
            seen = ""
            reason = str(e)
        else:
            reason = "no model that can see pictures answered"
        if not seen:
            raise tools.ToolError("the picture could not be looked at now (%s): try again, or ask the user what it shows" % reason)
        return seen
    if ext == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise tools.ToolError("PDF text cannot be read on this installation")
        pdf = PdfReader(path)
        if pdf.is_encrypted and not pdf.decrypt(""):
            raise tools.ToolError("this PDF is protected by a password: ask the user for an open copy")
        pages = ["[Page %d]\n%s" % (i + 1, (page.extract_text() or "").strip() or "(a picture of a page: no text in it)")
                 for i, page in enumerate(pdf.pages)]
        return "\n\n".join(pages)
    with open(path, "rb") as f:
        data = f.read()
    try:
        if ext == "docx":
            return office.read_docx(data)
        if ext in ("xlsx", "xlsm"):
            return office.read_xlsx(data)
        if ext == "pptx":
            return office.read_pptx(data)
        if ext in ("odt", "ods", "odp"):
            return office.read_open_document(data)
        if ext == "epub":
            return _epub(data)
        if ext == "zip":
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "\n".join("%s (%s)" % (i.filename, _size(i.file_size)) for i in z.infolist()[:400])
    except (zipfile.BadZipFile, KeyError, ValueError, SyntaxError) as e:
        raise tools.ToolError("the file is damaged or is not what its name says (%s)" % e)
    if ext == "eml":
        return _eml(data)
    if ext in ("doc", "xls", "ppt"):
        raise tools.ToolError("this is the old .%s format: ask the user to save it as .%sx, or to share it as PDF" % (ext, ext))
    for encoding in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            continue
    if text.count("\x00") > len(text) // 20:
        raise tools.ToolError("a .%s file is not text and has no reader here: the user can open it on the phone" % (ext or "?"))
    if ext in ("html", "htm"):
        return _strip_tags(text)
    if ext == "rtf":
        return _rtf(text)
    return text


def read(ctx, path, question="", part=1):
    full = find(ctx, path)
    text = text_of(ctx, full, question)
    part = max(1, int(part or 1))
    parts = max(1, -(-len(text) // PART))
    if part > parts:
        raise tools.ToolError("the file has %d part(s)" % parts)
    body = text[(part - 1) * PART: part * PART]
    head = "%s (%s, %s)%s" % (os.path.basename(full), kind(full) or "file", _size(os.path.getsize(full)),
                               " — part %d of %d: part=%d for the next" % (part, parts, part + 1) if parts > 1 and part < parts
                               else " — part %d of %d, the last" % (part, parts) if parts > 1 else "")
    return head + "\n\n" + (body or "(the file is empty)"), {"path": full, "name": os.path.basename(full), "parts": parts}


def take(root, name, data):
    """A file the user attached, put into Shared under its own name (a second one of that name gets a number)."""
    name = clean_name(name)
    if len(data) > READ_MAX:
        raise tools.ToolError("the file is over 32 MB")
    where = folder(root, SHARED)
    os.makedirs(where, exist_ok=True)
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(where, name), 2
    while os.path.exists(path):
        path = os.path.join(where, "%s-%d%s" % (stem, n, ext))
        n += 1
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return path


def route(handler, method, path, body=None):
    """GET /api/assistant/files (what the app's home holds), GET /api/assistant/file?path= (one of them, as it
    is) and POST /api/assistant/upload {name, data: base64} (a file the user attaches to their message)."""
    root = os.path.expanduser("~")
    if method == "POST" and path == "/api/assistant/upload":
        import base64
        import binascii
        try:
            data = base64.b64decode(str((body or {}).get("data") or ""), validate=True)
            saved = take(root, (body or {}).get("name"), data)
        except (tools.ToolError, binascii.Error, ValueError) as e:
            handler._json({"ok": False, "error": str(e)}, 400)
            return True
        handler._json({"ok": True, "file": card(saved, root)})
        return True
    if method != "GET" or path not in ("/api/assistant/files", "/api/assistant/file"):
        return False
    if path == "/api/assistant/files":
        files = sorted(listing(root), key=lambda p: -os.path.getmtime(p))[:200]
        handler._json({"ok": True, "files": [card(p, root) for p in files]})
        return True
    wanted = os.path.abspath(str(handler._query().get("path") or ""))
    allowed = [folder(root) + os.sep, folder(root, SHARED) + os.sep]
    if not any(wanted.startswith(a) for a in allowed) or not os.path.isfile(wanted) or os.path.getsize(wanted) > READ_MAX:
        handler._json({"ok": False, "error": "no such file"}, 404)
        return True
    with open(wanted, "rb") as f:
        data = f.read()
    from urllib.parse import quote
    handler.send_response(200)
    handler.send_header("Content-Type", mime(wanted))
    handler.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(os.path.basename(wanted)))
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)
    return True


@tools.tool("file_create",
            "Make a file in the conversation; the user gets it as a card they can open or share. The name's ending "
            "chooses the format: .docx (Word), .xlsx (Excel), .pptx (PowerPoint), .pdf, .txt, .md, .html, .csv, "
            ".json, .ics (a calendar event), .vcf (a contact) and other text files. Write the content as plain text "
            "with these marks, the same for every format: '# Heading' ('##' smaller; in a .pptx each heading is a "
            "new slide and the lines under it its points), '- item' or '1. item' for lists, '| a | b |' rows for a "
            "table (first row the header), '**bold**'. For .xlsx give the table as rows like that, as CSV, or as "
            "JSON ({\"Sheet name\": [[...], ...]}); a cell that starts with = is a formula. Making it again under "
            "the same name replaces it.",
            {"name": tools._s("the file's name with its ending, e.g. trip-plan.docx"),
             "content": tools._s("the whole content, as marked plain text (or CSV/JSON for a sheet, HTML for .html)"),
             "title": tools._s("optional title shown at the top (.docx, .pdf) or used for an untitled first slide")},
            ["name", "content"], "write")
def t_file_create(ctx, name, content, title=""):
    path = make(ctx.root, name, content, title)
    info = card(path, ctx.root)
    return ("Made %s (%s). The user sees it in the conversation and can open or share it. To send it: gmail_send attach=%s; "
            "to give it to a web form: browser op=upload path=%s."
            % (info["name"], _size(info["bytes"]), info["name"], info["name"])), {"file": info}


@tools.tool("file_read",
            "Read a file of the conversation: one the user shared, or one made here. Word, Excel, PowerPoint, PDF, "
            "OpenDocument, e-books, saved emails, text, tables, web pages, zip lists; a picture or a photo of a "
            "document is looked at and read (question: what to look for). A long file comes in parts: part=2 for "
            "the next.",
            {"path": tools._s("the file's name or path"), "question": tools._s("for a picture: what to look for (optional)"),
             "part": tools._i("which part of a long file (default 1)")}, ["path"], "read")
def t_file_read(ctx, path, question="", part=1):
    return read(ctx, path, question, part)


@tools.tool("file_list", "The files of this conversation: what the user shared and what was made, newest first.", {}, [], "read")
def t_file_list(ctx):
    root = os.path.abspath(ctx.root)
    files = sorted(listing(root), key=lambda p: -os.path.getmtime(p))
    if not files:
        return "There are no files yet. The user can share one into the app, or attach one to their message.", {"files": []}
    lines = []
    for p in files[:60]:
        info = card(p, root)
        lines.append("%s — %s, %s, %s%s" % (info["name"], info["kind"] or "file", _size(info["bytes"]),
                                           time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))),
                                           " (shared by the user)" if info["shared"] else ""))
    return "\n".join(lines), {"files": [card(p, root) for p in files[:60]]}
