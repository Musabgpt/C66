"""MusabAI OAuth broker. Tokens leave only through a one-use, PKCE-bound HTTPS handoff.

No account passwords or OAuth secrets belong in the APK. Run behind HTTPS with
MUSAB_PUBLIC_URL and provider CLIENT_ID / CLIENT_SECRET environment variables.
Pending grants are held in memory for ten minutes; restart requires reconnecting.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GOOGLE = ("https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token")
PROVIDERS = json.loads((Path(__file__).parent / "hosted/providers.json").read_text(encoding="utf-8"))


def options(provider):
    spec = PROVIDERS[provider]
    return spec[4] if len(spec) > 4 else {}


def configured(provider):
    prefix = PROVIDERS[provider][0]
    return bool(os.environ.get(prefix + "_CLIENT_ID") and
                (options(provider).get("auth") == "none" or os.environ.get(prefix + "_CLIENT_SECRET")))


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


class OAuthError(Exception):
    pass


def exchange(provider, fields):
    prefix, _, endpoint, _ = PROVIDERS[provider][:4]
    client = os.environ.get(prefix + "_CLIENT_ID", "")
    secret = os.environ.get(prefix + "_CLIENT_SECRET", "")
    if not configured(provider):
        raise OAuthError("Provider application is not configured")
    fields = dict(fields)
    if options(provider).get("resource"):
        fields["resource"] = options(provider)["resource"]
    headers = {"Accept": "application/json"}
    if provider == "notion":
        headers["Authorization"] = "Basic " + base64.b64encode((client + ":" + secret).encode()).decode()
        headers["Content-Type"] = "application/json"
        headers["Notion-Version"] = "2022-06-28"
        data = json.dumps(fields).encode()
    else:
        fields["client_id"] = client
        if options(provider).get("auth") != "none":
            fields["client_secret"] = secret
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(endpoint, data, headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read(1 << 20))
    except (OSError, ValueError):
        # Provider error bodies can contain codes and secrets: never relay/log them.
        raise OAuthError("Provider rejected the token exchange") from None
    if result.get("error") or not result.get("access_token"):
        raise OAuthError("Provider did not issue an access token")
    return {k: result[k] for k in ("access_token", "refresh_token", "expires_in", "refresh_token_expires_in", "scope", "token_type") if k in result}


class Broker:
    def __init__(self, public_url):
        u = urllib.parse.urlsplit(public_url)
        if u.scheme != "https" or not u.netloc or u.query or u.fragment or u.path not in ("", "/"):
            raise ValueError("MUSAB_PUBLIC_URL must be an HTTPS origin")
        self.url = public_url.rstrip("/")
        self.pending = {}
        self.lock = threading.RLock()

    def prune(self):
        now = time.time()
        for key in list(self.pending):
            if self.pending[key]["expires"] < now:
                del self.pending[key]

    def catalog(self):
        return {p: configured(p) for p in PROVIDERS}

    def start(self, provider, proof):
        if provider not in PROVIDERS or not self.catalog()[provider]:
            raise OAuthError("Provider application is not configured")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", proof):
            raise OAuthError("Invalid handoff challenge")
        prefix, authorize, _, scope = PROVIDERS[provider][:4]
        sid = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        redirect = self.url + "/oauth/callback/" + provider
        rec = {"provider": provider, "proof": proof, "verifier": verifier, "redirect": redirect,
               "expires": time.time() + 600, "status": "pending"}
        with self.lock:
            self.prune()
            if len(self.pending) >= 256:
                raise OAuthError("Too many pending connections; retry later")
            self.pending[sid] = rec
        params = dict(client_id=os.environ[prefix + "_CLIENT_ID"], redirect_uri=redirect, response_type="code", state=sid)
        if scope:
            params["scope"] = scope
        if options(provider).get("resource"):
            params["resource"] = options(provider)["resource"]
        if provider != "notion":
            params.update(code_challenge=challenge(verifier), code_challenge_method="S256")
        if prefix == "GOOGLE":
            params.update(access_type="offline", prompt="consent")
        if provider == "notion":
            params["owner"] = "user"
        return {"id": sid, "authorization_url": authorize + "?" + urllib.parse.urlencode(params), "expires_in": 600}

    def callback(self, provider, query):
        sid = query.get("state", "")
        with self.lock:
            self.prune()
            rec = self.pending.get(sid)
            if not rec or rec["provider"] != provider or rec["status"] != "pending":
                raise OAuthError("Invalid, expired or already used authorization state")
            rec["status"] = "exchanging"
        try:
            if query.get("error") or not query.get("code"):
                raise OAuthError("Authorization was declined")
            fields = dict(grant_type="authorization_code", code=query["code"], redirect_uri=rec["redirect"])
            if provider != "notion":
                fields["code_verifier"] = rec["verifier"]
            tokens = exchange(provider, fields)
            with self.lock:
                rec.update(tokens=tokens, status="ready")
                rec.pop("verifier", None)
        except OAuthError as e:
            with self.lock:
                rec.update(status="failed", error=str(e))
                rec.pop("verifier", None)
            raise

    def poll(self, sid, verifier):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", verifier):
            raise OAuthError("Invalid handoff proof")
        with self.lock:
            self.prune()
            rec = self.pending.get(sid)
            if not rec or not hmac.compare_digest(rec["proof"], challenge(verifier)):
                raise OAuthError("Invalid or expired connection")
            if rec["status"] == "ready":
                del self.pending[sid]
                return dict(status="ready", tokens=rec["tokens"])
            if rec["status"] == "failed":
                del self.pending[sid]
                return dict(status="failed", error=rec["error"])
            return {"status": "pending"}


class Handler(BaseHTTPRequestHandler):
    broker = None

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # OAuth callback query strings must never reach logs.

    def reply(self, data, status=200, html=False):
        body = data.encode() if html else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/v1/catalog":
            return self.reply({"providers": self.broker.catalog()})
        match = re.fullmatch(r"/oauth/callback/([a-z]+)", parsed.path)
        if not match:
            return self.reply({"error": "Not found"}, 404)
        try:
            self.broker.callback(match[1], dict(urllib.parse.parse_qsl(parsed.query)))
            self.reply("<!doctype html><meta name='viewport' content='width=device-width'><title>MusabAI</title>"
                       "<h1>Authorization received</h1><p>Return to MusabAI. It will test your connection automatically.</p>"
                       "<a href='musabai://connectors'>Return to MusabAI</a>", html=True)
        except OAuthError:
            self.reply("<!doctype html><title>MusabAI</title><h1>Authorization did not complete</h1>"
                       "<p>Return to MusabAI and try connecting again.</p>", 400, html=True)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= 65536:
                raise OAuthError("Invalid request size")
            b = json.loads(self.rfile.read(n))
            if not isinstance(b, dict):
                raise OAuthError("Invalid request")
            if self.path == "/v1/start":
                result = self.broker.start(b.get("provider", ""), b.get("challenge", ""))
            elif self.path == "/v1/poll":
                result = self.broker.poll(b.get("id", ""), b.get("verifier", ""))
            elif self.path == "/v1/refresh":
                provider = b.get("provider", "")
                if provider not in PROVIDERS or not b.get("refresh_token"):
                    raise OAuthError("Refresh token required")
                result = exchange(provider, dict(grant_type="refresh_token", refresh_token=b["refresh_token"]))
            else:
                return self.reply({"error": "Not found"}, 404)
            self.reply(result)
        except (OAuthError, ValueError, TypeError, KeyError):
            self.reply({"error": "Connection request failed; check app registration or reconnect"}, 400)


class BrokerServer(ThreadingHTTPServer):
    daemon_threads = True

    def service_actions(self):
        with self.RequestHandlerClass.broker.lock:
            self.RequestHandlerClass.broker.prune()


if __name__ == "__main__":
    Handler.broker = Broker(os.environ["MUSAB_PUBLIC_URL"])
    server = BrokerServer(("127.0.0.1", int(os.environ.get("PORT", "8766"))), Handler)
    server.serve_forever()
