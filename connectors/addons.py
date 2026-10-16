"""Real, free extension tools that need no vendor account.

External services stay in the connector panel and are enabled only after their
OAuth/API test succeeds. These tools are local or use documented public HTTPS
endpoints; they never pretend that an unconfigured integration is connected.
"""
import difflib
import json
import os
from pathlib import Path
import socket
import urllib.parse
import urllib.request

from . import tools

NAMES = ["extension_catalog", "dns_lookup", "web_fetch", "git_diff_patch", "static_site_build"]
DIRECTORY = [
    {"id":"dns_inspector", "name":"DNS Inspector", "kind":"built_in", "status":"ready", "tools":["dns_lookup"], "description":"Public DNS-over-HTTPS lookup; no account required."},
    {"id":"git_diff_patcher", "name":"Git Diff Patcher", "kind":"built_in", "status":"ready", "tools":["git_diff_patch"], "description":"Review a patch before applying it; no account required."},
    {"id":"web_fetch", "name":"HTTPS Web Fetch", "kind":"built_in", "status":"ready", "tools":["web_fetch"], "description":"Fetch a public HTTPS page with size and redirect limits."},
    {"id":"static_site_builder", "name":"Static Website Builder", "kind":"built_in", "status":"ready", "tools":["static_site_build"], "description":"Build a real HTML/CSS/JS site inside the workspace."},
    {"id":"github", "name":"GitHub", "kind":"oauth", "status":"external", "requires":"OAuth app and account"},
    {"id":"gitlab", "name":"GitLab", "kind":"oauth", "status":"external", "requires":"OAuth app and account"},
    {"id":"google", "name":"Google Drive, Gmail, Calendar, Docs and Sheets", "kind":"oauth", "status":"external", "requires":"Google OAuth client"},
    {"id":"notion", "name":"Notion", "kind":"oauth", "status":"external", "requires":"Connect through official Notion MCP OAuth"},
    {"id":"figma", "name":"Figma", "kind":"oauth", "status":"external", "requires":"Figma OAuth app"},
    {"id":"openai", "name":"OpenAI Developers", "kind":"api", "status":"external", "requires":"OpenAI API key"},
    {"id":"resend", "name":"Resend", "kind":"api", "status":"external", "requires":"Resend API key"},
    {"id":"cloudinary", "name":"Cloudinary", "kind":"api", "status":"external", "requires":"Cloudinary credentials"},
    {"id":"alchemy", "name":"Alchemy", "kind":"api", "status":"external", "requires":"Alchemy API key"},
    {"id":"namecheap", "name":"Namecheap", "kind":"api", "status":"external", "requires":"Namecheap API credentials"},
    {"id":"netlify", "name":"Netlify", "kind":"oauth", "status":"external", "requires":"Connect through official Netlify MCP OAuth"},
    {"id":"railway", "name":"Railway", "kind":"api", "status":"external", "requires":"Railway token"},
    {"id":"render", "name":"Render", "kind":"api", "status":"external", "requires":"Render API key"},
    {"id":"wordpress", "name":"WordPress.com", "kind":"oauth", "status":"external", "requires":"WordPress application"},
    {"id":"huggingface", "name":"Hugging Face", "kind":"oauth", "status":"external", "requires":"Connect through official Hugging Face MCP OAuth"},
    {"id":"miro", "name":"Miro", "kind":"oauth", "status":"external", "requires":"Connect through official Miro MCP OAuth"},
    {"id":"temporal", "name":"Temporal", "kind":"self_hosted", "status":"external", "requires":"Temporal server address"},
]

def _host(value):
    host = str(value or "").strip().lower().rstrip(".")
    if not host or len(host) > 253 or ".." in host or any(not (c.isalnum() or c in ".-") for c in host):
        raise tools.ToolError("Enter a valid hostname")
    try: socket.getaddrinfo(host, None)
    except OSError: pass  # DNS-over-HTTPS can resolve names unavailable to the phone resolver.
    return host

@tools.tool("extension_catalog", "List real built-in addons and external integrations. External entries are never claimed connected without a successful credential test.", {}, [], "read")
def catalog(ctx):
    return json.dumps(DIRECTORY, ensure_ascii=False), {"extensions": len(DIRECTORY)}

@tools.tool("dns_lookup", "Look up A, AAAA, MX, TXT, CNAME or NS records using Cloudflare's public DNS-over-HTTPS API; no account is required.", {"hostname": tools._s("hostname"), "record": tools._s("DNS record type, for example A or MX")}, ["hostname"], "read")
def dns_lookup(ctx, hostname, record="A"):
    host = _host(hostname); typ = str(record or "A").upper()
    if typ not in {"A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "CAA"}: raise tools.ToolError("Unsupported DNS record type")
    q = urllib.parse.urlencode({"name": host, "type": typ})
    req = urllib.request.Request("https://cloudflare-dns.com/dns-query?" + q, headers={"Accept":"application/dns-json", "User-Agent":"MusabAI-Extensions/1"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response: data = json.loads(response.read(256 * 1024).decode("utf-8"))
    except Exception as e: raise tools.ToolError("DNS lookup failed: " + str(e))
    answers = [{"name": a.get("name"), "type": a.get("type"), "data": a.get("data"), "ttl": a.get("TTL")} for a in data.get("Answer", [])]
    return json.dumps({"hostname":host,"record":typ,"status":data.get("Status"),"answers":answers}, ensure_ascii=False), {"answers":len(answers)}

@tools.tool("web_fetch", "Fetch a public HTTPS URL for research or code work. Refuses credentials, redirects and responses larger than 2 MB.", {"url": tools._s("public HTTPS URL"), "max_chars": tools._i("maximum returned characters")}, ["url"], "read")
def web_fetch(ctx, url, max_chars=12000):
    u = urllib.parse.urlsplit(str(url))
    if u.scheme != "https" or not u.hostname or u.username or u.password: raise tools.ToolError("Use a public HTTPS URL without credentials")
    if not 1 <= int(max_chars) <= 50000: raise tools.ToolError("max_chars must be between 1 and 50000")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args): return None
    req = urllib.request.Request(str(url), headers={"User-Agent":"MusabAI-Extensions/1", "Accept":"text/html,text/plain,application/json;q=0.9,*/*;q=0.1"})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024: raise tools.ToolError("Response exceeds 2 MB")
            charset = response.headers.get_content_charset() or "utf-8"
    except tools.ToolError: raise
    except Exception as e: raise tools.ToolError("HTTPS fetch failed: " + str(e))
    text = data.decode(charset, errors="replace")
    return tools.clip(text, int(max_chars)), {"url":str(url),"bytes":len(data),"truncated":len(text)>int(max_chars)}

@tools.tool("git_diff_patch", "Create a unified Git diff from old and new text so the change can be reviewed before applying it.", {"path": tools._s("relative file path"), "old_text": tools._s("current text"), "new_text": tools._s("proposed text")}, ["path","old_text","new_text"], "read")
def git_diff_patch(ctx, path, old_text, new_text):
    name = str(path).replace("\\", "/").strip("/") or "file"
    if ".." in Path(name).parts: raise tools.ToolError("Path must be relative")
    diff = "".join(difflib.unified_diff(str(old_text).splitlines(True), str(new_text).splitlines(True), fromfile="a/"+name, tofile="b/"+name))
    return diff or "(no changes)", {"changed": bool(diff), "path":name}

@tools.tool("static_site_build", "Build a static HTML/CSS/JS website from explicit files into a new workspace folder. Does not deploy or publish anything.", {"folder": tools._s("new output folder"), "files": {"type":"object","description":"mapping of relative file names to UTF-8 contents"}}, ["folder","files"], "edit")
def static_site_build(ctx, folder, files):
    if not isinstance(files, dict) or not files or len(files)>200: raise tools.ToolError("Provide 1–200 website files")
    dest = Path(tools.resolve(ctx, folder, new=True)).resolve()
    if not tools.inside(ctx, str(dest)) or dest.exists(): raise tools.ToolError("Choose a new folder inside the workspace")
    total=0; prepared=[]
    for name, content in files.items():
        rel=str(name).replace("\\", "/")
        parts=Path(rel).parts
        if not rel or Path(rel).is_absolute() or ".." in parts or Path(rel).suffix.lower() not in {".html",".htm",".css",".js",".json",".svg",".txt",".md"}: raise tools.ToolError("Website paths must be safe HTML/CSS/JS/JSON/SVG/TXT/MD files")
        if not isinstance(content,str) or len(content.encode("utf-8"))>2*1024*1024: raise tools.ToolError("Each website file must be UTF-8 and <=2 MB")
        total += len(content.encode("utf-8")); prepared.append((rel,content))
    if total>16*1024*1024: raise tools.ToolError("Website exceeds 16 MB")
    dest.mkdir(parents=True)
    for rel, content in prepared:
        out=dest/rel; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(content,encoding="utf-8"); ctx.after_change(str(out))
    if not (dest/"index.html").is_file(): raise tools.ToolError("Website must include index.html")
    return "Built static site in " + tools.rel(ctx,str(dest)), {"path":tools.rel(ctx,str(dest)),"files":len(prepared),"bytes":total}

def route(handler, method, path, body=None):
    if path != "/api/extensions": return False
    # The original engine owns /api/extensions for its built-in marketplace.
    # The MusabAI directory uses an explicit query flag so that endpoint stays intact.
    if method == "GET" and handler._query().get("directory") != "1": return False
    if method != "GET": handler._json({"error":"Not found"},404); return True
    handler._json({"ok":True,"extensions":DIRECTORY,"policy":"External services are shown as unavailable until a real credential/API test succeeds."})
    return True
