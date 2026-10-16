# MusabAI connectors — deployment and acceptance

**OAuth update, 2026-10-04:** MusabAI clients for Notion, Netlify, Miro,
Hugging Face and GitLab are now registered through each provider's official dynamic
registration endpoint. The updated Android build uses their official MCP tools;
its native bridge must be installed before connecting these new entries. See
[registration details and remaining acceptance checks](hosted/REGISTRATION.md).
The manual-registration instructions below still apply to Google and the legacy
REST integrations. Account consent is required; client registration alone does not
mean a user's account is connected.

This continuation starts at successful NewAl-Cloud Action **216**, commit
`d8a270382c51e009148fc2ae034beb40788fdf6c`. Its native baseline originated in Action
**125**, commit `2ebea5701a432b454aeba639cbb39d756bf50da7`. Its engine is pinned to NewAl
`0bf36a3b3a813dbac424ee0c4dc6341f9e3fe0d3`, the Action 43 source.
The original agent, skills, plugins, MCP, terminal, review, model configuration
and memory/autonomy layer are retained. Only checked integration anchors are patched.

Native Python/llama.cpp libraries are copied byte-for-byte from the original Action
125 APK after verifying its pinned SHA-256. Only the tested engine archive and
Android Java integration are rebuilt. The workflow needs the original artifact to
remain downloadable; if GitHub expires it, the build fails closed until that exact
APK is retained in a durable release or another approved baseline is selected.

## What this implementation contains

- Browser authorization via an HTTPS broker, provider state validation and PKCE
  where applicable. A separate PKCE handoff binds issued tokens to the initiating
  Android installation. Callback links contain no credentials.
- Android Keystore AES-GCM token records, refresh-token rotation, no tokens in
  WebView/Python tool arguments, no browser localStorage credentials.
- Fixed-host service API requests, redirect refusal, bounded responses and explicit
  errors. Writes are not automatically replayed after network/authorization errors.
- Agent tools for GitHub, GitLab, Drive, Gmail, Calendar, Docs, Sheets, Notion and
  Figma. Connected services are discovered when the model requests tools, including
  accounts connected after a session started. Existing MCP tools remain present.
- Real Termux result callbacks: durable job IDs, stdout, stderr and exit code.
  A timed out call remains pending; reading its result does not rerun it.
- Independent workspaces without a GitHub repository or account. The selected
  cloud model can answer, but execution is **on the current host**, normally the
  phone. This does not implement a hosted worker that survives phone shutdown.

## Required external setup — not completed by a code build

Register OAuth applications in the owner's provider accounts, enable the relevant
APIs, and supply secrets to the server environment. This cannot be replaced with
ChatGPT's own account credentials or another application's client registration.

Run `python3 connectors/broker.py` on a trusted host behind an HTTPS reverse proxy.
The process binds to `127.0.0.1:8766` by default. Configure:

```
MUSAB_PUBLIC_URL=https://YOUR_CONNECTOR_DOMAIN
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
GITLAB_CLIENT_ID=...
GITLAB_CLIENT_SECRET=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
NOTION_CLIENT_ID=...
NOTION_CLIENT_SECRET=...
FIGMA_CLIENT_ID=...
FIGMA_CLIENT_SECRET=...
```

Use a Google **web** OAuth client with the broker's HTTPS redirect, not an Android
custom-scheme redirect. Register these exact callback paths on that domain:

| Registration | Redirect URI paths |
|---|---|
| GitHub | `/oauth/callback/github` |
| GitLab | `/oauth/callback/gitlab` |
| Google | `/oauth/callback/drive`, `/oauth/callback/gmail`, `/oauth/callback/calendar`, `/oauth/callback/docs`, `/oauth/callback/sheets` |
| Notion | `/oauth/callback/notion` |
| Figma | `/oauth/callback/figma` |

The broker catalog reports a provider configured only when its ID and secret exist.
Configuration alone does not prove a provider has approved the application or
granted the required scopes. Google/Gmail and public Figma apps may require provider
review. Testing-mode access can be limited to registered testers. Service rate
limits and paid-plan features still apply; no unlimited/free guarantee is made.

Set repository **variable** `MUSAB_CONNECTOR_BROKER_URL` to that HTTPS origin.
Rebuild the APK. It contains only this public URL; provider secrets remain on the
server. With no URL, OAuth cards clearly say deployment is required and Connect is
disabled. Users never paste provider tokens into the application.

Pending authorization sessions are memory-only and expire after ten minutes.
Use one broker process (or sticky routing). Restarting during authorization requires
reconnecting. Apply reverse-proxy request/body/concurrency limits and disable query
logging for `/oauth/callback/*`. Do not log token exchange request/response bodies.
Do not expose the loopback Python engine or phone server to the Internet.

Drive uses `drive.file`: files created or selected/shared with this app, rather than
all existing Drive content. Broader access requires a deliberate scope change and
any applicable Google review. Notion sees only pages shared with the integration.
Figma supports reading designs and writing comments; it does not fabricate a REST
endpoint that edits arbitrary design layers.

Disconnect removes credentials and pending handoffs locally. Provider-wide grant
revocation is done in the provider account's Connected applications settings;
the UI explicitly communicates this distinction.

## Termux

Install official Termux, grant its `RUN_COMMAND` permission to MusabAI, and enable
`allow-external-apps=true` in `~/.termux/termux.properties` (then reload Termux settings).
Android cannot silently grant these permissions or edit another app's private
configuration. Connect sends a harmless probe. Only a successful callback with
the expected stdout and exit code zero produces Connected.

## Release gates

1. Original engine tests and connector contract tests pass.
2. Java/Android build succeeds and the packaged Python archive contains original
   feature modules and both original/additive UI scripts.
3. On a real Android device: OAuth approve, decline, expiry, reconnect, process restart,
   refresh, local disconnect, Termux callback and permission refusal.
4. Verify every provider with a real authorized account and one read/write task.
5. Confirm original `/` skills/commands, plugin install/use, MCP invocation, terminal,
   review and persisted sessions on the resulting APK.

Offline mock tests are evidence for code contracts. They are **not evidence that
live OAuth registrations, remote hosting, Android device tests or all providers are
working**. Until those gates pass this is a development build, not a completed release.

## Preview installation and signing

This build uses package `dev.newal.code.lite.connectors`, label MusabAI Preview, and ports 8795 (engine), 8796 (phone bridge), 8798 (optional Termux engine). It installs beside Action #125; existing app data stays in its original package. The optional Termux setup uses `.newal-code-preview` and commands `newal-preview` / `newal-termux-preview`. No automatic data migration is performed.

The build runner debug signing certificate differs from #125. A direct update of the original package requires the original private signing key. A stable owner-controlled release signing key must be configured before distributing upgradeable releases; this preview is a test artifact.

## Hosted broker continuation

A Vinext/Cloudflare broker with the same Android protocol is deployed separately through Sites. Its source is preserved in its Sites Git repository; the service implementation and contract tests are also under `connectors/hosted/`. Pending grants persist in an R2 binding named `BUCKET`, encrypted with runtime secret `MUSAB_GRANT_KEY` (32 bytes, base64url). R2 conditional writes prevent duplicate callback exchanges and double token delivery. No provider keys are committed.

Selected project: `appgprj_6ac1aebaaa688191a23e5ec22abf7e2a`. Origin: `https://musabai-connector-server.netnyaho.chatgpt.site`. The owner explicitly approved public broker access and GitHub registration. MusabAI OAuth application `3902920` is registered in Musabgpt, with client ID `Ov23liwve9bTfAKljTdL` and the exact HTTPS callback. Its client secret is a hosted runtime secret, never an APK or repository value. Anonymous requests with the existing Android `MusabAI-Connectors/1` User-Agent returned HTTP 200 and the live catalog reports GitHub configured. Cloudflare rejects generic Python-urllib traffic (1010); use the legitimate existing application identity for native checks. Real-account consent/token exchange and physical Android acceptance still must pass. Google, GitLab, Notion and Figma registrations remain pending. Hosted Terminal/Python execution is a separate requirement and is not supplied by this broker.


## Startup and mobile-network fixes

Android readiness now uses the authenticated `/api/health` endpoint, without the
hardware/shell/project discovery performed by `/api/state`. The wait uses a
monotonic two-minute deadline, short network timeouts, activity cancellation, and
an explicit Retry button that restarts the local engine. Startup errors from an
older service invocation are cleared. Incomplete extracted installs are repaired
when the installation marker exists but core runtime files are missing.

OAuth polling retains the pending flow across network IO errors and HTTP
408/429/5xx until the original authorization deadline. Definitive rejection,
expiry, and invalid sessions still require reconnecting. This does not solve a
lost response after the broker's single-use token delivery; reconnect is required
in that case. Disconnect and superseded flows cannot restore credentials.
The connector panel preserves scroll/focus during periodic refreshes, translates
activation/failure messages, and only offers Test when account credentials exist.
Provider registration requirements remain unchanged.

Run `npm ci --prefix connectors && npm test --prefix connectors` for broker and
panel regressions, and the Python connector/original-engine tests described by
the build workflow. Physical-device startup and account consent remain separate
acceptance checks.
