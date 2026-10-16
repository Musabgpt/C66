"""Connector tools for the unchanged Action #43 agent. Credentials stay in Android Keystore.

Each operation has a fixed provider/method/path and a permission kind. API URLs,
authorization headers and access tokens are never supplied by the model.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from email.message import EmailMessage
from . import phone, settings, tools

CATALOG = {"github": "GitHub", "gitlab": "GitLab", "drive": "Google Drive", "gmail": "Gmail",
           "calendar": "Google Calendar", "docs": "Google Docs", "sheets": "Google Sheets",
           "notion": "Notion API", "figma": "Figma", "termux": "Termux",
           "notionmcp": "Notion", "netlify": "Netlify", "miro": "Miro", "huggingface": "Hugging Face", "gitlabmcp": "GitLab",
           "activepieces": "Activepieces Automation Hub"}
_cache = (0, [])
MCP_PROVIDERS = {"notionmcp", "netlify", "miro", "huggingface", "gitlabmcp", "activepieces"}
_mcp_cache = {}
_mcp_lock = threading.RLock()


def native(op, **args):
    result = phone.call("connector", timeout=110, op=op, **args)
    if not result.get("ok", False):
        raise tools.ToolError(result.get("error", "Connector request failed"))
    return result


def status():
    if not phone.available():
        return {"ok": True, "connectors": [{"id": k, "name": v, "status": "unavailable",
                                            "error": "Android connector host required"} for k, v in CATALOG.items()]}
    result = native("status")
    from .automation import DEPENDENCIES
    for item in result.get("connectors", []):
        if item.get("id") == "termux":
            item["dependencies"] = DEPENDENCIES.status()
    return result


def names():
    global _cache
    if not phone.available():
        return []
    if time.monotonic() - _cache[0] > 5:
        try:
            accounts = [c for c in status()["connectors"] if c["status"] == "connected"]
            connected = {c["id"] for c in accounts}
            native_names = [n for n, spec in list(OPERATIONS.items()) if spec[0] in connected and spec[0] not in MCP_PROVIDERS]
            for account in accounts:
                if account["id"] in MCP_PROVIDERS:
                    try:
                        native_names.extend(mcp_names(account))
                    except Exception:
                        # One unavailable service must not hide working GitHub/Termux tools.
                        continue
            _cache = (time.monotonic(), native_names)
        except Exception:
            _cache = (time.monotonic(), [])
    return list(_cache[1])


def request(provider, method, path, body=None, query=None, raw=None, mime=None):
    if query:
        path += ("&" if "?" in path else "?") + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    args = dict(provider=provider, method=method, path=path)
    if body is not None:
        args["body"] = body
    if raw is not None:
        args.update(raw=raw, mime=mime or "text/plain; charset=utf-8")
    result = native("request", **args)
    return result.get("data", {})


def segment(value):
    return urllib.parse.quote(str(value), safe="")


# name: provider, HTTP method, route, description, parameter schemas, required.
S = lambda description: {"type": "string", "description": description}
OBJ = {"type": "object", "description": "Fields accepted by the service's official API"}
OPERATIONS = {}


class OfficialMcpTool(tools.Tool):
    def __init__(self, name, provider, definition):
        self.input_schema = definition["inputSchema"]
        remote_name = definition["name"]

        def run(ctx, **arguments):
            data = native("mcp_call", provider=provider, name=remote_name, arguments=arguments)["data"]
            if data.get("isError"):
                raise tools.ToolError(tools.clip(json.dumps(data, ensure_ascii=False), 16000))
            return tools.clip(json.dumps(data, ensure_ascii=False), 32000), {"connector": provider, "operation": remote_name}

        # Remote annotations are hints, never permission grants. Existing account-write policy applies.
        super().__init__(name, (CATALOG[provider] + ": " + definition.get("description", remote_name))[:4000],
                         self.input_schema.get("properties", {}), self.input_schema.get("required", []), "connector_write", run)

    def schema(self):
        result = super().schema()
        result["function"]["parameters"] = self.input_schema
        return result


def mcp_names(account):
    provider = account["id"]
    if provider not in MCP_PROVIDERS:
        raise tools.ToolError("Unknown official MCP provider")
    generation = (account.get("generation", ""), account.get("tested_at", 0))
    with _mcp_lock:
        cached = _mcp_cache.get(provider)
        if cached and cached[0] == generation:
            return list(cached[1])
        catalog = native("mcp_tools", provider=provider)["data"].get("tools")
        if not isinstance(catalog, list) or not 1 <= len(catalog) <= 512:
            raise tools.ToolError("Connected service returned an invalid tool catalog")
        prepared = {}
        for definition in catalog:
            if not isinstance(definition, dict) or not isinstance(definition.get("name"), str) or not definition["name"]:
                raise tools.ToolError("Invalid remote tool name")
            if not isinstance(definition.get("inputSchema"), dict) or definition["inputSchema"].get("type") != "object":
                raise tools.ToolError("Invalid remote tool input schema")
            remote = definition["name"]
            short = re.sub(r"[^a-zA-Z0-9_]", "_", remote)[:24]
            name = "connected_" + provider + "_" + short + "_" + hashlib.sha256(remote.encode()).hexdigest()[:10]
            if name in prepared:
                raise tools.ToolError("Duplicate remote tool name")
            prepared[name] = OfficialMcpTool(name, provider, definition)
        for name, tool in prepared.items():
            tools.REGISTRY[name] = tool
            OPERATIONS[name] = (provider, "POST", "tools/call")
        _mcp_cache[provider] = (generation, list(prepared))
        return list(prepared)


def operation(name, provider, method, route, description, params=None, required=None):
    params = params or {}
    required = required if required is not None else list(params)
    OPERATIONS[name] = (provider, method, route)

    def run(ctx, **args):
        path = route
        for key in params:
            path = path.replace("{" + key + "}", segment(args.get(key, "")))
        data = request(provider, method, path, args.get("body"), args.get("query"))
        return json.dumps(data, ensure_ascii=False)[:24000], {"connector": provider, "operation": name}
    tools.tool(name, description, params, required, "read" if method == "GET" else "connector_write")(run)


def install():
    operation("github_list_repos", "github", "GET", "/user/repos", "List connected GitHub account repositories", {"query": OBJ}, [])
    operation("github_create_repo", "github", "POST", "/user/repos", "Create GitHub repo; body: name, private, description", {"body": OBJ})
    for name, method, route, desc in [
        ("github_read_file", "GET", "/contents/{path}", "Read repository file; query ref selects branch"),
        ("github_put_file", "PUT", "/contents/{path}", "Create/update file; body: message, base64 content, branch, sha for update"),
        ("github_list_issues", "GET", "/issues", "List issues"),
        ("github_create_issue", "POST", "/issues", "Create issue; body title, body"),
        ("github_create_pr", "POST", "/pulls", "Create pull request; body title, head, base, body"),
    ]:
        params = {"owner": S("owner"), "repo": S("repository")}
        if "{path}" in route:
            params["path"] = S("file path; slashes will be preserved by provider URL handling")
        req = list(params)
        params["query" if method == "GET" else "body"] = OBJ
        if method != "GET":
            req.append("body")
        operation(name, "github", method, "/repos/{owner}/{repo}" + route, desc, params, req)
    operation("gitlab_list_projects", "gitlab", "GET", "/projects", "List GitLab projects; query membership=true", {"query": OBJ}, [])
    operation("gitlab_create_project", "gitlab", "POST", "/projects", "Create GitLab project; body name, visibility", {"body": OBJ})
    for name, method, route, desc in [
        ("gitlab_read_file", "GET", "/repository/files/{path}", "Read file; query ref=branch"),
        ("gitlab_commit", "POST", "/repository/commits", "Commit files atomically; body branch, commit_message, actions[]"),
        ("gitlab_create_issue", "POST", "/issues", "Create issue; body title, description"),
        ("gitlab_create_mr", "POST", "/merge_requests", "Create merge request; body source_branch, target_branch, title"),
    ]:
        params = {"project": S("numeric project ID or namespace/project")}
        if "{path}" in route:
            params["path"] = S("file path")
        req = list(params)
        params["query" if method == "GET" else "body"] = OBJ
        if method != "GET":
            req.append("body")
        operation(name, "gitlab", method, "/projects/{project}" + route, desc, params, req)
    operation("drive_search", "drive", "GET", "/drive/v3/files", "Search files accessible to this app; query q, fields, pageToken", {"query": OBJ}, [])
    operation("drive_read", "drive", "GET", "/drive/v3/files/{id}", "Read metadata; query alt=media reads content, or use drive_export for Google docs", {"id": S("file ID"), "query": OBJ}, ["id"])
    operation("drive_export", "drive", "GET", "/drive/v3/files/{id}/export", "Export Google file; query mimeType required", {"id": S("file ID"), "query": OBJ})
    operation("drive_create", "drive", "POST", "/drive/v3/files", "Create file metadata/folder; body name, mimeType, parents", {"body": OBJ})
    operation("drive_update", "drive", "PATCH", "/drive/v3/files/{id}", "Rename/update metadata; query addParents/removeParents moves file", {"id": S("file ID"), "body": OBJ, "query": OBJ}, ["id", "body"])
    operation("gmail_search", "gmail", "GET", "/gmail/v1/users/me/messages", "Search messages; query q, maxResults, pageToken", {"query": OBJ}, [])
    operation("gmail_read", "gmail", "GET", "/gmail/v1/users/me/messages/{id}", "Read message and MIME parts; query format=full", {"id": S("message ID"), "query": OBJ}, ["id"])
    operation("gmail_create_draft", "gmail", "POST", "/gmail/v1/users/me/drafts", "Create draft; body message.raw is base64url RFC822", {"body": OBJ})
    operation("calendar_list", "calendar", "GET", "/calendar/v3/calendars/{calendar}/events", "Read appointments; query timeMin/timeMax, singleEvents=true", {"calendar": S("primary or calendar ID"), "query": OBJ}, ["calendar"])
    operation("calendar_create", "calendar", "POST", "/calendar/v3/calendars/{calendar}/events", "Create event; body summary, start, end, timezone", {"calendar": S("primary or calendar ID"), "body": OBJ})
    operation("calendar_update", "calendar", "PATCH", "/calendar/v3/calendars/{calendar}/events/{id}", "Update event fields", {"calendar": S("calendar ID"), "id": S("event ID"), "body": OBJ})
    for provider, base, resource in [("docs", "/v1/documents", "document"), ("sheets", "/v4/spreadsheets", "spreadsheet")]:
        operation(provider + "_create", provider, "POST", base, "Create " + resource + " using official API fields", {"body": OBJ})
        operation(provider + "_read", provider, "GET", base + "/{id}", "Read " + resource, {"id": S(resource + " ID"), "query": OBJ}, ["id"])
        operation(provider + "_update", provider, "POST", base + "/{id}:batchUpdate", "Apply requests[] to " + resource, {"id": S(resource + " ID"), "body": OBJ})
    operation("sheets_read_values", "sheets", "GET", "/v4/spreadsheets/{id}/values/{range}", "Read cell range", {"id": S("spreadsheet ID"), "range": S("Sheet1!A1:D10")})
    operation("sheets_write_values", "sheets", "PUT", "/v4/spreadsheets/{id}/values/{range}", "Write values; body values[][]; query valueInputOption=RAW", {"id": S("spreadsheet ID"), "range": S("range"), "body": OBJ, "query": OBJ})
    operation("notion_search", "notion", "POST", "/v1/search", "Search shared Notion pages; read only despite HTTP POST", {"body": OBJ}, [])
    tools.REGISTRY["notion_search"].kind = "read"
    operation("notion_read_page", "notion", "GET", "/v1/pages/{id}", "Read page properties", {"id": S("page ID")})
    operation("notion_read_blocks", "notion", "GET", "/v1/blocks/{id}/children", "Read page/block contents", {"id": S("block/page ID"), "query": OBJ}, ["id"])
    operation("notion_create_page", "notion", "POST", "/v1/pages", "Create page; body parent, properties, children", {"body": OBJ})
    operation("notion_append_blocks", "notion", "PATCH", "/v1/blocks/{id}/children", "Append blocks; body children[]", {"id": S("block/page ID"), "body": OBJ})
    operation("figma_read_file", "figma", "GET", "/v1/files/{key}", "Read Figma file design tree", {"key": S("file key"), "query": OBJ}, ["key"])
    operation("figma_comments", "figma", "GET", "/v1/files/{key}/comments", "Read Figma comments", {"key": S("file key")})
    operation("figma_post_comment", "figma", "POST", "/v1/files/{key}/comments", "Add Figma comment; body message and optional client_meta", {"key": S("file key"), "body": OBJ})

    @tools.tool("gmail_send", "Send an email when authorized by user; builds valid MIME for Arabic/English", {"to": S("recipient"), "subject": S("subject"), "text": S("body")}, ["to", "subject", "text"], "connector_write")
    def gmail_send(ctx, to, subject, text):
        msg = EmailMessage()
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(text)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
        data = request("gmail", "POST", "/gmail/v1/users/me/messages/send", {"raw": raw})
        return json.dumps(data), {"connector": "gmail"}
    OPERATIONS["gmail_send"] = ("gmail", "POST", "")

    @tools.tool("drive_upload_text", "Upload text into an existing Drive file (create metadata with drive_create first)", {"id": S("file ID"), "text": S("file contents"), "mime": S("MIME type")}, ["id", "text"], "connector_write")
    def drive_upload(ctx, id, text, mime="text/plain; charset=utf-8"):
        data = request("drive", "PATCH", "/upload/drive/v3/files/" + segment(id), query={"uploadType": "media"}, raw=text, mime=mime)
        return json.dumps(data), {"connector": "drive"}
    OPERATIONS["drive_upload_text"] = ("drive", "PATCH", "")

    @tools.tool("github_push", "Publish multiple UTF-8 files as one GitHub commit on an existing branch; no force push", {"owner": S("owner"), "repo": S("repo"), "branch": S("existing branch"), "message": S("commit message"), "files": {"type": "object", "description": "mapping of relative paths to UTF-8 content"}}, ["owner", "repo", "branch", "message", "files"], "connector_write")
    def github_push(ctx, owner, repo, branch, message, files):
        if not isinstance(files, dict) or not files or len(files) > 100:
            raise tools.ToolError("Provide 1–100 files")
        base = "/repos/" + segment(owner) + "/" + segment(repo)
        ref = request("github", "GET", base + "/git/ref/heads/" + segment(branch))
        parent = ref["object"]["sha"]
        commit = request("github", "GET", base + "/git/commits/" + parent)
        tree = []
        for path, content in files.items():
            if not isinstance(content, str) or path.startswith("/") or ".." in path.split("/"):
                raise tools.ToolError("Files must use safe relative paths and text contents")
            tree.append({"path": path, "mode": "100644", "type": "blob", "content": content})
        new_tree = request("github", "POST", base + "/git/trees", {"base_tree": commit["tree"]["sha"], "tree": tree})
        new_commit = request("github", "POST", base + "/git/commits", {"message": message, "tree": new_tree["sha"], "parents": [parent]})
        result = request("github", "PATCH", base + "/git/refs/heads/" + segment(branch), {"sha": new_commit["sha"], "force": False})
        return json.dumps(result), {"connector": "github", "commit": new_commit["sha"]}
    OPERATIONS["github_push"] = ("github", "POST", "")

    @tools.tool("termux_exec", "Execute bash in connected Termux and return durable job ID, stdout, stderr, exit code. Never repeat a timed out command; use termux_result.", {"command": S("bash command")}, ["command"], "exec")
    def termux_exec(ctx, command):
        result = native("termux_exec", command=command)
        return json.dumps(result, ensure_ascii=False), {"connector": "termux", "job": result.get("id"),
                "exit": result.get("exit_code"), "output": result.get("stdout", "") + result.get("stderr", ""),
                "pending": result.get("status") == "running"}
    OPERATIONS["termux_exec"] = ("termux", "POST", "")

    @tools.tool("termux_result", "Read previously started Termux job; does not re-execute it", {"id": S("job ID")}, ["id"], "read")
    def termux_result(ctx, id):
        result = native("termux_result", id=id)
        return json.dumps(result, ensure_ascii=False), {"connector": "termux"}
    OPERATIONS["termux_result"] = ("termux", "GET", "")


def route(handler, method, path, body=None):
    if not path.startswith("/api/connectors") and path not in ("/api/workspaces", "/api/mcp-oauth/callback"):
        return False
    try:
        if method == "GET" and path == "/api/mcp-oauth/callback":
            q = handler._query()
            # This loopback-only callback is deliberately usable without the app cookie.
            # Android validates the one-time OAuth state before redeeming the code.
            try:
                result = native("oauth_complete", provider="activepieces", code=q.get("code", ""),
                                state=q.get("state", ""), iss=q.get("iss", ""), error=q.get("error", ""))
                ok = bool(result.get("ok"))
            except Exception:
                ok = False
            title = "Activepieces connected" if ok else "Activepieces authorization failed"
            text = "Return to MusabAI. The connection has been tested." if ok else "Return to MusabAI and connect again."
            html = ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
                    "<title>" + title + "</title><body style='font-family:sans-serif;padding:32px'>"
                    "<h1>" + title + "</h1><p>" + text + "</p>"
                    "<p><a href='musabai://connectors'>Return to MusabAI</a></p></body>")
            handler._text(html, "text/html; charset=utf-8")
        elif method == "GET" and path == "/api/connectors":
            handler._json(status())
        elif method == "POST" and path in ("/api/connectors/connect", "/api/connectors/disconnect", "/api/connectors/test"):
            op = path.rsplit("/", 1)[-1]
            provider = (body or {}).get("provider", "")
            if provider not in CATALOG:
                raise tools.ToolError("Unknown connector")
            if provider == "termux" and op == "connect":
                result = native("termux_test")
                from .automation import DEPENDENCIES
                DEPENDENCIES.retry()
            elif provider == "activepieces" and op == "connect":
                result = native(op, provider=provider, url=(body or {}).get("url", ""))
            else:
                result = native(op, provider=provider)
            global _cache
            _cache = (0, [])
            handler._json(result)
        elif method == "POST" and path == "/api/workspaces":
            # Independent workspace on this execution host. It is not a remote worker.
            root = os.path.join(settings.HOME, "workspaces", secrets.token_hex(12))
            os.makedirs(root, exist_ok=False)
            s = handler.service.create(root, model=(body or {}).get("model"), mode=(body or {}).get("mode"))
            handler._json({"id": s.id, "root": root, "meta": s.meta(), "execution": "this_host", "remote_worker": False})
        else:
            handler._json({"error": "Not found"}, 404)
    except Exception as e:
        handler._json({"error": str(e)}, 400)
    return True


install()
