# Official OAuth registrations (2026-10-04)

MusabAI has registered its own clients with these providers' public dynamic client
registration endpoints. These are independent MusabAI registrations. No ChatGPT
tokens, provider passwords, or personal API tokens are involved.

| Provider ID | Official MCP endpoint | Registration | Server environment prefix | Client authentication |
| --- | --- | --- | --- | --- |
| `notionmcp` | `https://mcp.notion.com/mcp` | `https://mcp.notion.com/register` | `NOTION_MCP` | PKCE public client (`none`) |
| `netlify` | `https://mcp.netlify.com/mcp` | `https://mcp.netlify.com/oauth-server/reg` | `NETLIFY_MCP` | PKCE public client (`none`) |
| `miro` | `https://mcp.miro.com/` | `https://mcp.miro.com/register` | `MIRO_MCP` | `client_secret_post` |
| `huggingface` | `https://huggingface.co/mcp` | `https://huggingface.co/oauth/register` | `HF_MCP` | `client_secret_post` |
| `gitlabmcp` | `https://gitlab.com/api/v4/mcp` | `https://gitlab.com/oauth/register` | `GITLAB_MCP` | PKCE public client (`none`) |

Each client has the exact redirect URI
`https://musabai-connector-server.netnyaho.chatgpt.site/oauth/callback/<provider ID>`.
Registrations persist in Sites production configuration as `<PREFIX>_CLIENT_ID`
and, for confidential clients, `<PREFIX>_CLIENT_SECRET`. Never re-register on app
startup or deployment: changing client IDs can orphan existing refresh tokens.

OAuth metadata and registration responses were checked live. GitLab restricts
dynamically registered clients to `mcp` scope and public-client authentication;
that credential cannot be substituted for the existing REST `api` connector.
Likewise, Notion MCP credentials are not Notion REST API tokens.

The Android bridge sends account tokens only to a fixed official endpoint, handles
JSON and SSE replies, initializes the MCP protocol, and paginates the tool catalog.
Only a successful authenticated catalog with at least one tool marks an account
connected. The agent receives actual provider tool schemas, never account tokens.
All dynamic tools use the existing account-write permission policy; untrusted
read-only annotations do not bypass that policy. Writes are never automatically
replayed after a timeout, HTTP error, or tool error.

Registration is not account consent. The user still signs in and approves each
provider in their browser. This environment cannot complete those account-specific
consent and on-device acceptance checks for the user. Provider plan limits and
workspace policies still apply. GitLab groups must enable MCP access.

Google's discovered OAuth metadata does not provide dynamic client registration.
Google services therefore still require the owner's Google Cloud web OAuth client,
enabled APIs and appropriate consent configuration/review. Figma discovery could
not be reached from this execution environment; no Figma registration is claimed.
Services without a confirmed official flow remain unconfigured.

References checked:

- https://developers.notion.com/guides/mcp/build-mcp-client
- https://mcp.netlify.com/.well-known/oauth-authorization-server
- https://mcp.miro.com/.well-known/oauth-authorization-server
- https://developers.miro.com/docs/miro-mcp
- https://huggingface.co/.well-known/openid-configuration
- https://huggingface.co/.well-known/oauth-protected-resource/mcp
- https://gitlab.com/.well-known/oauth-authorization-server
- https://docs.gitlab.com/user/model_context_protocol/mcp_server/
- https://accounts.google.com/.well-known/openid-configuration
