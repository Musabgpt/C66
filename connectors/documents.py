"""Document tools and authenticated file-library API. No shell is needed for text/ZIP work."""
import base64
import html
import io
import json
import os
from pathlib import Path
import secrets
import tempfile
import urllib.request
import zipfile
from types import SimpleNamespace
from . import tools, phone, settings

FORMATS = {'.html': 'text/html', '.md': 'text/markdown', '.txt': 'text/plain', '.pdf': 'application/pdf', '.zip': 'application/zip'}
LIMIT = 32 * 1024 * 1024
NAMES = ['document_create', 'document_read', 'document_download', 'archive_pack', 'archive_extract']

def target(ctx, path):
    p = Path(tools.resolve(ctx, path, new=True)).resolve()
    if not tools.inside(ctx, str(p)):
        raise tools.ToolError('Choose a path inside the workspace or an explicitly added folder')
    if p.suffix.lower() not in FORMATS:
        raise tools.ToolError('Supported formats: HTML, MD, TXT, PDF, ZIP')
    return p

def atomic(ctx, path, data):
    if len(data) > LIMIT: raise tools.ToolError('File exceeds 32 MB')
    ctx.before_change(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            tmp = f.name; f.write(data)
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp): os.unlink(tmp)
    ctx.after_change(str(path))

def result(ctx, p):
    return 'Saved %s (%d bytes)' % (tools.rel(ctx, str(p)), p.stat().st_size), {'path': tools.rel(ctx, str(p)), 'document': True, 'bytes': p.stat().st_size}

@tools.tool('document_create', 'Create a UTF-8 HTML, Markdown or TXT file, or a paginated Arabic/English PDF on Android. For ZIP use archive_pack. HTML content is saved as supplied; PDF content is plain text.', {'path': tools._s('destination file with extension'), 'content': tools._s('complete document text'), 'title': tools._s('optional PDF title')}, ['path', 'content'], 'edit')
def create(ctx, path, content, title=''):
    p = target(ctx, path)
    if p.suffix.lower() == '.zip': raise tools.ToolError('Use archive_pack for ZIP files')
    if p.suffix.lower() == '.pdf':
        if not phone.available(): raise tools.ToolError('PDF creation uses the Android renderer; use HTML on this host')
        p.parent.mkdir(parents=True, exist_ok=True)
        ctx.before_change(str(p))
        out = phone.call('document_pdf', path=str(p), content=str(content), title=str(title), timeout=90)
        if not out.get('ok'): raise tools.ToolError(out.get('error', 'PDF rendering failed'))
        ctx.after_change(str(p))
    else:
        atomic(ctx, p, str(content).encode('utf-8'))
    return result(ctx, p)

@tools.tool('document_read', 'Read HTML, MD, TXT, PDF text or the ZIP file list. PDF image scans need OCR and may have no extractable text.', {'path': tools._s('document path'), 'offset': tools._i('first text line or PDF page, 1-based'), 'limit': tools._i('maximum lines or PDF pages')}, ['path'], 'read')
def read(ctx, path, offset=1, limit=100):
    p = target(ctx, path)
    if p.stat().st_size > LIMIT: raise tools.ToolError('File exceeds 32 MB')
    offset = max(1, int(offset)); limit = max(1, min(int(limit), 400))
    if p.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        pdf = PdfReader(str(p))
        if pdf.is_encrypted and not pdf.decrypt(''): raise tools.ToolError('This PDF is password protected')
        text = '\n\n'.join('[Page %d]\n%s' % (i+1, pdf.pages[i].extract_text() or '(image-only page; OCR required)') for i in range(offset-1, min(len(pdf.pages), offset-1+min(limit, 20))))
        meta = {'pages': len(pdf.pages)}
    elif p.suffix.lower() == '.zip':
        with zipfile.ZipFile(p) as z:
            info = z.infolist()
            text = '\n'.join('%s (%d bytes)' % (x.filename, x.file_size) for x in info[offset-1:offset-1+limit])
            meta = {'entries': len(info)}
    else:
        text = p.read_text(encoding='utf-8-sig')
        lines = text.splitlines(); text = '\n'.join(lines[offset-1:offset-1+limit]); meta = {'lines': len(lines)}
    return tools.clip(text, 16000), dict(meta, path=tools.rel(ctx, str(p)), document=True)

@tools.tool('document_download', 'Download a document from an HTTPS URL into this workspace. Refuses redirects; choose the final HTTPS link. Maximum 32 MB.', {'url': tools._s('HTTPS document URL'), 'path': tools._s('destination HTML/MD/TXT/PDF/ZIP path')}, ['url','path'], 'edit')
def download(ctx, url, path):
    from urllib.parse import urlsplit
    u = urlsplit(url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password: raise tools.ToolError('Use an HTTPS URL without credentials')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args): return None
    p = target(ctx, path)
    request = urllib.request.Request(url, headers={'User-Agent':'MusabAI-Documents/1'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        data = response.read(LIMIT+1)
    if p.suffix.lower() == '.pdf' and not data.startswith(b'%PDF-'): raise tools.ToolError('The response is not a PDF')
    if p.suffix.lower() == '.zip' and not zipfile.is_zipfile(io.BytesIO(data)): raise tools.ToolError('The response is not a ZIP archive')
    atomic(ctx, p, data)
    return result(ctx, p)

@tools.tool('archive_pack', 'Create ZIP from explicit workspace files or folders, preserving relative names. Skips symlinks and development cache folders.', {'path': tools._s('destination .zip file'), 'files': {'type':'array','items':{'type':'string'},'description':'workspace paths to include'}}, ['path','files'], 'edit')
def pack(ctx, path, files):
    p = target(ctx, path)
    if p.suffix.lower() != '.zip': raise tools.ToolError('Destination must end with .zip')
    if not files: raise tools.ToolError('Choose at least one file')
    selected = {}; total = 0
    for name in files:
        source = Path(tools.resolve(ctx, name)).resolve()
        if not tools.inside(ctx, str(source)): raise tools.ToolError('Archive inputs must be inside the workspace')
        paths = source.rglob('*') if source.is_dir() else [source]
        for f in paths:
            if f.is_symlink() or not f.is_file() or f.resolve() == p: continue
            if any(part in tools.IGNORED_DIRS for part in f.relative_to(source.parent).parts): continue
            if not tools.inside(ctx, str(f.resolve())): raise tools.ToolError('Archive input leaves the workspace')
            name = tools.rel(ctx, str(f))
            if name.startswith('../') or os.path.isabs(name): name = f.name
            if name in selected and selected[name] != f: raise tools.ToolError('Duplicate archive path')
            if name not in selected: total += f.stat().st_size
            selected[name] = f
            if total > LIMIT or len(selected) > 1000: raise tools.ToolError('Archive exceeds 32 MB or 1000 files')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, f in sorted(selected.items()): z.write(f, name)
    atomic(ctx, p, buffer.getvalue())
    return result(ctx, p)

@tools.tool('archive_extract', 'Extract ZIP into a new workspace folder. Validates all entries first; refuses traversal, symlinks, duplicates, oversized archives and overwriting an existing folder.', {'path': tools._s('source .zip file'), 'folder': tools._s('new destination folder')}, ['path','folder'], 'edit')
def extract(ctx, path, folder):
    p = target(ctx, path)
    dest = Path(tools.resolve(ctx, folder, new=True)).resolve()
    if not tools.inside(ctx, str(dest)) or dest.exists(): raise tools.ToolError('Choose a new folder inside the workspace')
    if p.stat().st_size > LIMIT: raise tools.ToolError('Archive exceeds 32 MB')
    with zipfile.ZipFile(p) as z:
        entries = z.infolist(); seen=set(); total=0
        if len(entries)>1000: raise tools.ToolError('Too many archive entries')
        for e in entries:
            parts = e.filename.replace('\\','/').split('/')
            if e.filename.startswith(('/', '\\')) or '..' in parts or any(':' in part for part in parts) or '\x00' in e.filename:
                raise tools.ToolError('Unsafe ZIP path')
            if (e.external_attr >> 16) & 0o170000 == 0o120000: raise tools.ToolError('ZIP symlinks are not allowed')
            out = (dest / e.filename).resolve()
            if not out.is_relative_to(dest) or out in seen: raise tools.ToolError('Duplicate or unsafe ZIP entry')
            seen.add(out); total+=e.file_size
            if total>LIMIT or e.file_size>200*max(e.compress_size,1024): raise tools.ToolError('ZIP expands beyond limits')
        dest.parent.mkdir(parents=True,exist_ok=True)
        staging=Path(tempfile.mkdtemp(dir=dest.parent))
        try:
            for e in entries:
                if e.is_dir(): (staging/e.filename).mkdir(parents=True,exist_ok=True); continue
                f=staging/e.filename; f.parent.mkdir(parents=True,exist_ok=True)
                f.write_bytes(z.read(e))
            os.rename(staging,dest)
        finally:
            if staging.exists(): __import__('shutil').rmtree(staging)
    for e in entries:
        if not e.is_dir(): ctx.after_change(str(dest/e.filename))
    return 'Extracted %d entries into %s' % (len(entries),dest), {'path':str(dest),'entries':len(entries)}

def api_context(handler, sid):
    if sid:
        session = handler.service.get(sid)
        root = session.root
    else:
        session = None
        root = os.path.join(settings.HOME,'documents'); os.makedirs(root,exist_ok=True)
    return SimpleNamespace(root=root, session=session, before_change=lambda p: session.checkpoints.save_original(session.turn,p) if session else None, after_change=lambda p: None)

def route(handler, method, path, body=None):
    if not path.startswith('/api/documents'): return False
    try:
        q = handler._query() if method=='GET' else (body or {})
        ctx = api_context(handler,q.get('session',''))
        if method=='GET' and path=='/api/documents':
            files=[]
            for p in Path(ctx.root).rglob('*'):
                if len(files)>=300: break
                if p.is_symlink() or p.suffix.lower() not in FORMATS or not p.is_file(): continue
                relative=p.relative_to(ctx.root)
                if any(x in tools.IGNORED_DIRS for x in relative.parts): continue
                files.append({'path':relative.as_posix(),'name':p.name,'bytes':p.stat().st_size,'format':p.suffix[1:].lower()})
            handler._json({'root':ctx.root,'files':sorted(files,key=lambda x:x['path'])})
        elif method=='GET' and path=='/api/documents/download':
            p=target(ctx,q.get('path',''))
            if p.stat().st_size>LIMIT: raise tools.ToolError('File exceeds 32 MB')
            data=p.read_bytes()
            handler.send_response(200)
            handler.send_header('Content-Type', FORMATS[p.suffix.lower()])
            from urllib.parse import quote
            handler.send_header('Content-Disposition', "attachment; filename*=UTF-8''"+quote(p.name))
            handler.send_header('Content-Length',str(len(data)))
            handler.send_header('X-Content-Type-Options','nosniff'); handler.end_headers(); handler.wfile.write(data)
        elif method=='POST' and path=='/api/documents/import':
            p=target(ctx,q.get('path',''))
            encoded=q.get('data','')
            if len(encoded)>LIMIT*4//3+4: raise tools.ToolError('File exceeds 32 MB')
            data=base64.b64decode(encoded,validate=True)
            atomic(ctx,p,data); handler._json({'ok':True,'path':tools.rel(ctx,str(p))})
        elif method=='POST' and path=='/api/documents/read':
            text,meta=read(ctx,q.get('path',''),q.get('offset',1),q.get('limit',100))
            handler._json({'text':text,**meta})
        else: handler._json({'error':'Not found'},404)
    except Exception as e: handler._json({'error':str(e)},400)
    return True
