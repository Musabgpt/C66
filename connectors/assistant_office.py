"""Word, Excel and PowerPoint files, written and read with nothing but the standard library.

The phone's engine has no office packages, and these formats are zip archives of XML: a file is written here part
by part, as small as a valid one can be, and read back by walking its XML. What the assistant writes is plain
text with a few marks, the same for every format:

    # Title            a heading (## and ### are smaller ones); in a presentation, a new slide
    - point            a list item (1. for a numbered one)
    | a | b |          a table row (the first row is the header)
    **bold**           bold text
    ---                in a presentation, a new slide without a title

Arabic and other right-to-left text is marked as such, so Word and Excel lay it out from the right.
"""
import csv
import io
import json
import re
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape

RTL = re.compile(r"[֐-ࣿיִ-﷿ﹰ-ﻼ]")
LTR = re.compile(r"[A-Za-zÀ-ɏЀ-ӿ]")
STAMP = (2024, 1, 1, 0, 0, 0)                 # a fixed date inside the archive: the same text gives the same file


def rtl(text):
    """Whether the text reads from the right: more right-to-left letters than left-to-right ones."""
    text = str(text or "")
    return len(RTL.findall(text)) > len(LTR.findall(text))


def _zip(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts:
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)
    return out.getvalue()


def _x(text):
    """Text as XML content: escaped, and without the control characters XML does not allow."""
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text)))


XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _rels(pairs):
    return XML + '<Relationships xmlns="%s">%s</Relationships>' % (PKG_REL, "".join(
        '<Relationship Id="%s" Type="%s/%s" Target="%s"/>' % (rid, REL, kind, target) for rid, kind, target in pairs))


# ---------------------------------------------------------------------------------------------- the marks

def blocks(text):
    """The text as blocks: ("h", level, text), ("p", text), ("li", ordered, text), ("table", rows), ("break",)."""
    out, table = [], None
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("|") and line.count("|") >= 2:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells):
                continue                                   # the |---|---| line under a header
            if table is None:
                table = []
                out.append(("table", table))
            table.append(cells)
            continue
        table = None
        if not line:
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            out.append(("h", len(heading.group(1)), heading.group(2).strip()))
        elif re.fullmatch(r"-{3,}|\*{3,}|_{3,}", line):
            out.append(("break",))
        elif re.match(r"^[-*•]\s+", line):
            out.append(("li", False, re.sub(r"^[-*•]\s+", "", line)))
        elif re.match(r"^\d{1,3}[.)]\s+", line):
            out.append(("li", True, re.sub(r"^\d{1,3}[.)]\s+", "", line)))
        else:
            out.append(("p", line))
    return out


def spans(text):
    """[(text, bold)] for a line with **bold** in it; other marks (*, _, `) are dropped."""
    out = []
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", str(text))       # a link keeps its address
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        part = re.sub(r"(?<!\w)[*_`](.+?)[*_`](?!\w)", r"\1", part)
        if part:
            out.append((part, i % 2 == 1))
    return out or [("", False)]


def plain(text):
    return "".join(t for t, _ in spans(text))


# ---------------------------------------------------------------------------------------------- Word

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _run(text, bold=False, right=False, size=0):
    props = '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Arial"/>'
    if bold:
        props += "<w:b/><w:bCs/>"
    if size:
        props += '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size, size)
    if right:
        props += "<w:rtl/>"
    return '<w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r>' % (props, _x(text))


def _para(text, style="", numbering=0, bold=False, size=0):
    right = rtl(text)
    props = ('<w:pStyle w:val="%s"/>' % style if style else "")
    if numbering:
        props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="%d"/></w:numPr>' % numbering
    if right:
        props += "<w:bidi/>"
    runs = "".join(_run(t, bold or b, right, size) for t, b in spans(text))
    return "<w:p><w:pPr>%s</w:pPr>%s</w:p>" % (props, runs)


def _word_table(rows):
    width = max(len(r) for r in rows)
    right = rtl(" ".join(" ".join(r) for r in rows))
    borders = "".join('<w:%s w:val="single" w:sz="4" w:space="0" w:color="999999"/>' % side
                      for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = ['<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/>%s<w:tblBorders>%s</w:tblBorders></w:tblPr>'
           % ("<w:bidiVisual/>" if right else "", borders)]
    for n, row in enumerate(rows):
        cells = "".join("<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>%s</w:tc>"
                        % _para(row[i] if i < len(row) else "", bold=n == 0) for i in range(width))
        out.append("<w:tr>%s</w:tr>" % cells)
    return "".join(out) + "</w:tbl><w:p/>"


WORD_STYLES = XML + '''<w:styles xmlns:w="%s"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Arial"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:bCs/><w:sz w:val="52"/><w:szCs w:val="52"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="320" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="1F3864"/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="2F5496"/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:bCs/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="60"/><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>
</w:styles>''' % W

WORD_NUMBERING = XML + '''<w:numbering xmlns:w="%s">
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%%1."/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>%s</w:numbering>'''


def docx(text, title=""):
    """A Word document from marked text."""
    body, lists, ordered_run = [], 0, False
    if title:
        body.append(_para(title, "Title"))
    for block in blocks(text):
        kind = block[0]
        if kind == "li" and block[1]:
            if not ordered_run:                           # each numbered list counts from 1
                lists += 1
            ordered_run = True
            body.append(_para(block[2], "ListParagraph", 1 + lists))
            continue
        ordered_run = False
        if kind == "h":
            body.append(_para(block[2], "Title" if block[1] == 1 and not title and not body else "Heading%d" % block[1]))
        elif kind == "p":
            body.append(_para(block[1]))
        elif kind == "li":
            body.append(_para(block[2], "ListParagraph", 1))
        elif kind == "table":
            body.append(_word_table(block[1]))
        elif kind == "break":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    numbers = "".join('<w:num w:numId="%d"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/>'
                      '</w:lvlOverride></w:num>' % (2 + i) for i in range(lists))
    document = XML + ('<w:document xmlns:w="%s"><w:body>%s<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                      '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" '
                      'w:gutter="0"/>%s</w:sectPr></w:body></w:document>'
                      % (W, "".join(body) or "<w:p/>", "<w:bidi/>" if rtl(text) else ""))
    types = XML + ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                   '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                   '</Types>')
    return _zip([("[Content_Types].xml", types),
                 ("_rels/.rels", _rels([("rId1", "officeDocument", "word/document.xml")])),
                 ("word/document.xml", document),
                 ("word/_rels/document.xml.rels", _rels([("rId1", "styles", "styles.xml"), ("rId2", "numbering", "numbering.xml")])),
                 ("word/styles.xml", WORD_STYLES),
                 ("word/numbering.xml", WORD_NUMBERING % (W, numbers))])


# ---------------------------------------------------------------------------------------------- Excel

def sheets_of(content):
    """[(sheet name, rows)] from what the model wrote: JSON, marked tables (a heading names a sheet) or CSV."""
    text = content if isinstance(content, str) else json.dumps(content)
    stripped = text.strip()
    if stripped[:1] in "[{":
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict) and data and all(isinstance(v, list) for v in data.values()):
            return [(str(k), _rows(v)) for k, v in data.items()]
        if isinstance(data, list):
            return [("Sheet1", _rows(data))]
    marked, name, found = [], "", []
    for block in blocks(text):
        if block[0] == "h":
            name = plain(block[2])
        elif block[0] == "table":
            found.append((name or "Sheet%d" % (len(found) + 1), [[plain(c) for c in row] for row in block[1]]))
            name = ""
        else:
            marked.append(block)
    if found:
        return found
    sample = stripped.split("\n", 1)[0]
    delimiter = "\t" if sample.count("\t") > sample.count(",") and sample.count("\t") >= sample.count(";") else \
        ";" if sample.count(";") > sample.count(",") else ","
    return [("Sheet1", [row for row in csv.reader(io.StringIO(stripped), delimiter=delimiter)])]


def _rows(data):
    if data and all(isinstance(r, dict) for r in data):
        keys = []
        for r in data:
            keys += [k for k in r if k not in keys]
        return [list(keys)] + [[r.get(k, "") for k in keys] for r in data]
    return [list(r) if isinstance(r, (list, tuple)) else [r] for r in data]


def _column(n):
    name = ""
    while True:
        name = chr(65 + n % 26) + name
        n = n // 26 - 1
        if n < 0:
            return name


def _cell(ref, value, header):
    style = ' s="1"' if header else ""
    if isinstance(value, bool):
        return '<c r="%s"%s t="b"><v>%d</v></c>' % (ref, style, value)
    if isinstance(value, (int, float)):
        return '<c r="%s"%s><v>%r</v></c>' % (ref, style, value)
    text = "" if value is None else str(value)
    if not text:
        return ""
    if not header and re.fullmatch(r"-?\d{1,15}(\.\d{1,10})?", text.strip()) and not re.match(r"^-?0\d", text.strip()):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, style, text.strip())      # a number (not a phone number or a code)
    if text.startswith("=") and len(text) > 1 and not header:
        return '<c r="%s"%s><f>%s</f></c>' % (ref, style, _x(text[1:]))
    return '<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, _x(text))


def _sheet_name(name, used):
    name = re.sub(r"[\[\]:*?/\\]", " ", str(name)).strip()[:31] or "Sheet"
    base, n = name, 2
    while name.lower() in used:
        name = "%s %d" % (base[:28], n)
        n += 1
    used.add(name.lower())
    return name


def xlsx(content):
    """An Excel workbook: one sheet per table. Numbers are numbers, "=..." is a formula, the first row is the header."""
    sheets = [(n, r) for n, r in sheets_of(content) if r] or [("Sheet1", [[""]])]
    parts, used, names = [], set(), []
    for index, (name, rows) in enumerate(sheets, 1):
        names.append(_sheet_name(name, used))
        width = max(len(r) for r in rows)
        widths = [min(60, max([10] + [len(str(r[c])) + 2 for r in rows if c < len(r) and r[c] is not None])) for c in range(width)]
        cols = "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (c + 1, c + 1, w) for c, w in enumerate(widths))
        data = "".join('<row r="%d">%s</row>' % (r + 1, "".join(_cell("%s%d" % (_column(c), r + 1), v, r == 0 and len(rows) > 1)
                                                                    for c, v in enumerate(row)))
                       for r, row in enumerate(rows))
        right = rtl(" ".join(str(v) for row in rows[:20] for v in row))
        freeze = '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>' if len(rows) > 1 else ""
        parts.append(("xl/worksheets/sheet%d.xml" % index, XML + (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews>'
            '<sheetView workbookViewId="0"%s>%s</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>'
            '<cols>%s</cols><sheetData>%s</sheetData></worksheet>'
            % (' rightToLeft="1"' if right else "", freeze, cols, data))))
    count = len(names)
    workbook = XML + ('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="%s"><sheets>%s</sheets>'
                      '</workbook>' % (REL, "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_x(n).replace('"', "&quot;"), i, i)
                                                    for i, n in enumerate(names, 1))))
    styles = XML + ('<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                    '<fill><patternFill patternType="solid"><fgColor rgb="FFE2F0EC"/><bgColor indexed="64"/></patternFill></fill></fills>'
                    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
                    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    types = XML + ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>%s</Types>'
                   % "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % i
                             for i in range(1, count + 1)))
    rels = _rels([("rId%d" % i, "worksheet", "worksheets/sheet%d.xml" % i) for i in range(1, count + 1)]
                 + [("rId%d" % (count + 1), "styles", "styles.xml")])
    return _zip([("[Content_Types].xml", types), ("_rels/.rels", _rels([("rId1", "officeDocument", "xl/workbook.xml")])),
                 ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", rels), ("xl/styles.xml", styles)] + parts)


# ---------------------------------------------------------------------------------------------- PowerPoint

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = 'xmlns:a="%s" xmlns:r="%s" xmlns:p="%s"' % (A, REL, P)
EMPTY_TREE = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>'
              '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def slides_of(text, title=""):
    """[(title, [(text, is a list item)])]: a heading or --- starts a slide."""
    out, current = [], None
    for block in blocks(text):
        kind = block[0]
        if kind == "h" or kind == "break":
            current = [plain(block[2]) if kind == "h" else "", []]
            out.append(current)
            continue
        if current is None:
            current = [title, []]
            out.append(current)
        if kind == "p":
            current[1].append((plain(block[1]), False))
        elif kind == "li":
            current[1].append((plain(block[2]), True))
        elif kind == "table":
            current[1] += [(" — ".join(plain(c) for c in row if c), True) for row in block[1]]
    return [(t, lines) for t, lines in out] or [(title, [])]


def _text_body(lines, size, bold=False, centre=False):
    paras = []
    for text, bullet in lines or [("", False)]:
        right = rtl(text)
        props = '<a:pPr%s%s>%s</a:pPr>' % (' algn="ctr"' if centre else (' algn="r"' if right else ""), ' rtl="1"' if right else "",
                                            '<a:buFont typeface="Arial"/><a:buChar char="•"/>' if bullet else "<a:buNone/>")
        if bullet:
            props = props.replace("<a:pPr", '<a:pPr marL="342900" indent="-342900"', 1)
        run = '<a:r><a:rPr lang="%s" sz="%d"%s dirty="0"/><a:t>%s</a:t></a:r>' % ("ar-SA" if right else "en-US", size, ' b="1"' if bold else "", _x(text))
        paras.append("<a:p>%s%s</a:p>" % (props, run if text else '<a:endParaRPr lang="en-US" sz="%d"/>' % size))
    return '<p:txBody><a:bodyPr wrap="square" rtlCol="0"><a:normAutofit/></a:bodyPr><a:lstStyle/>%s</p:txBody>' % "".join(paras)


def _shape(sid, name, x, y, w, h, body):
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/>'
            '<a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>%s</p:sp>'
            % (sid, name, x, y, w, h, body))


def _slide(title, lines, first):
    wide, tall = 12192000, 6858000
    if first and len(lines) <= 2 and all(not b for _, b in lines):       # a title slide
        shapes = _shape(2, "Title", 838200, 2130425, wide - 1676400, 1470025, _text_body([(title, False)], 4400, True, True))
        if lines:
            shapes += _shape(3, "Subtitle", 1524000, 3886200, wide - 3048000, 1200000, _text_body(lines, 2400, False, True))
    else:
        size = 2400 if len(lines) <= 6 else 2000 if len(lines) <= 9 else 1600
        shapes = _shape(2, "Title", 838200, 365125, wide - 1676400, 1143000, _text_body([(title, False)], 3600, True))
        shapes += _shape(3, "Content", 838200, 1600200, wide - 1676400, tall - 1965325, _text_body(lines, size))
    return XML + ('<p:sld %s><p:cSld><p:spTree>%s%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
                  % (NS, EMPTY_TREE, shapes))


THEME = XML + ('<a:theme xmlns:a="%s" name="Office"><a:themeElements><a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
               '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F3864"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
               '<a:accent1><a:srgbClr val="0E8A6F"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>'
               '<a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
               '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
               '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface="Arial"/></a:majorFont>'
               '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface="Arial"/></a:minorFont></a:fontScheme>'
               '<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
               '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst>'
               '<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
               '<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>'
               '<a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst>'
               '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
               '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>') % A

TEXT_STYLES = ('<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="3600" b="1"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mj-lt"/>'
               '<a:cs typeface="+mj-cs"/></a:defRPr></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz="2400"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
               '<a:latin typeface="+mn-lt"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz="1800">'
               '<a:solidFill><a:schemeClr val="tx1"/></a:solidFill></a:defRPr></a:lvl1pPr></p:otherStyle></p:txStyles>')


def pptx(text, title=""):
    """A presentation: each heading (or ---) is a slide, the lines under it its points."""
    slides = slides_of(text, title)
    count = len(slides)
    master = XML + ('<p:sldMaster %s><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>%s</p:spTree></p:cSld>'
                    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" '
                    'accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
                    '</p:sldLayoutIdLst>%s</p:sldMaster>' % (NS, EMPTY_TREE, TEXT_STYLES))
    layout = XML + ('<p:sldLayout %s type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/>'
                    '</p:clrMapOvr></p:sldLayout>' % (NS, EMPTY_TREE))
    presentation = XML + ('<p:presentation %s><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>%s</p:sldIdLst>'
                          '<p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
                          % (NS, "".join('<p:sldId id="%d" r:id="rId%d"/>' % (255 + i, 1 + i) for i in range(1, count + 1))))
    types = XML + ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                   '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
                   '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
                   '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>%s</Types>'
                   % "".join('<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % i
                             for i in range(1, count + 1)))
    parts = [("[Content_Types].xml", types), ("_rels/.rels", _rels([("rId1", "officeDocument", "ppt/presentation.xml")])),
             ("ppt/presentation.xml", presentation),
             ("ppt/_rels/presentation.xml.rels", _rels([("rId1", "slideMaster", "slideMasters/slideMaster1.xml")]
                                                        + [("rId%d" % (1 + i), "slide", "slides/slide%d.xml" % i) for i in range(1, count + 1)]
                                                        + [("rId%d" % (count + 2), "theme", "theme/theme1.xml")])),
             ("ppt/slideMasters/slideMaster1.xml", master),
             ("ppt/slideMasters/_rels/slideMaster1.xml.rels", _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                                                                     ("rId2", "theme", "../theme/theme1.xml")])),
             ("ppt/slideLayouts/slideLayout1.xml", layout),
             ("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")])),
             ("ppt/theme/theme1.xml", THEME)]
    for i, (heading, lines) in enumerate(slides, 1):
        parts.append(("ppt/slides/slide%d.xml" % i, _slide(heading, lines, i == 1)))
        parts.append(("ppt/slides/_rels/slide%d.xml.rels" % i, _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")])))
    return _zip(parts)


# ---------------------------------------------------------------------------------------------- reading

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _xml(z, name):
    with z.open(name) as f:
        return ElementTree.parse(f).getroot()


def read_docx(data):
    """The document as marked text: headings, list items and tables come back as they are written."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        body = next((e for e in _xml(z, "word/document.xml") if _local(e.tag) == "body"), None)
        for el in (body if body is not None else []):
            kind = _local(el.tag)
            if kind == "p":
                text = "".join(t.text or "" if _local(t.tag) == "t" else "\n" if _local(t.tag) in ("br", "cr") else "\t" if _local(t.tag) == "tab" else ""
                               for t in el.iter())
                style = next((v for s in el.iter() if _local(s.tag) == "pStyle" for k, v in s.attrib.items() if _local(k) == "val"), "")
                listed = any(_local(s.tag) == "numPr" for s in el.iter())
                if not text.strip():
                    continue
                level = re.search(r"(?:heading|Heading)\s?(\d)", style)
                out.append(("#" * int(level.group(1)) + " " if level else "# " if style == "Title" else "- " if listed else "") + text.strip())
            elif kind == "tbl":
                for row in (r for r in el.iter() if _local(r.tag) == "tr"):
                    cells = ["".join(t.text or "" for t in c.iter() if _local(t.tag) == "t").strip()
                             for c in row if _local(c.tag) == "tc"]
                    out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def read_xlsx(data, rows_max=200):
    """Each sheet as tab-separated rows (what a formula last showed, not the formula)."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in _xml(z, "xl/sharedStrings.xml"):
                shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
        book = _xml(z, "xl/workbook.xml")
        rels = {r.attrib.get("Id"): r.attrib.get("Target", "") for r in _xml(z, "xl/_rels/workbook.xml.rels")}
        for sheet in (s for s in book.iter() if _local(s.tag) == "sheet"):
            rid = next((v for k, v in sheet.attrib.items() if _local(k) == "id"), "")
            target = rels.get(rid, "").lstrip("/")
            path = target if target.startswith("xl/") else "xl/" + target
            if path not in names:
                continue
            out.append("## " + sheet.attrib.get("name", "Sheet"))
            count = 0
            for row in (r for r in _xml(z, path).iter() if _local(r.tag) == "row"):
                cells, at = [], 0
                for c in (c for c in row if _local(c.tag) == "c"):
                    ref = re.match(r"[A-Z]+", c.attrib.get("r", ""))
                    col = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(ref.group(0)))) - 1 if ref else at
                    cells += [""] * (col - len(cells))
                    kind = c.attrib.get("t", "")
                    value = next((v.text or "" for v in c if _local(v.tag) == "v"), "")
                    if not value and kind != "inlineStr":          # a formula that was never calculated: the formula itself
                        value = next(("=" + (f.text or "") for f in c if _local(f.tag) == "f"), "")
                    if kind == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter() if _local(t.tag) == "t")
                    elif kind == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                    cells.append(value.replace("\t", " ").replace("\n", " "))
                    at = len(cells)
                if any(cells):
                    count += 1
                    if count <= rows_max:
                        out.append("\t".join(cells))
            if count > rows_max:
                out.append("(%d more rows)" % (count - rows_max))
    return "\n".join(out)


def read_pptx(data):
    """The slides in order, each with its texts."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        order = []
        try:
            rels = {r.attrib.get("Id"): r.attrib.get("Target", "") for r in _xml(z, "ppt/_rels/presentation.xml.rels")}
            for sld in (s for s in _xml(z, "ppt/presentation.xml").iter() if _local(s.tag) == "sldId"):
                rid = next((v for k, v in sld.attrib.items() if _local(k) == "id" and k != "id"), "")
                target = rels.get(rid, "").lstrip("/")
                order.append(target if target.startswith("ppt/") else "ppt/" + target)
        except (KeyError, ElementTree.ParseError):
            order = []
        order = [n for n in order if n in names] or sorted(
            (n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        for i, name in enumerate(order, 1):
            lines = []
            for para in (p for p in _xml(z, name).iter() if _local(p.tag) == "p"):
                text = "".join(t.text or "" for t in para.iter() if _local(t.tag) == "t").strip()
                if text:
                    lines.append(text)
            out.append("# Slide %d%s" % (i, ": " + lines[0] if lines else ""))
            out += ["- " + l for l in lines[1:]]
    return "\n".join(out)


def read_open_document(data):
    """The text of an OpenDocument file (.odt, .ods, .odp): paragraphs, headings and table rows."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = _xml(z, "content.xml")
    def text_of(el):
        return "".join(el.itertext()).strip()
    def walk(el):
        for child in el:
            kind = _local(child.tag)
            if kind == "h":
                out.append("# " + text_of(child))
            elif kind == "table-row":
                cells = [text_of(c) for c in child if _local(c.tag) == "table-cell"]
                if any(cells):
                    out.append("\t".join(cells))
            elif kind == "p":
                if text_of(child):
                    out.append(text_of(child))
            else:
                walk(child)
    walk(root)
    return "\n".join(out)
