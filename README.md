# MusabAI

Android coding assistant with persistent project memory, document tools, official OAuth/MCP connections, Termux execution and tested engine candidates.

This development line starts from successful Action 216 (`d8a270382c51e009148fc2ae034beb40788fdf6c`). It keeps that build's pinned Action 43 Python engine and byte-verified Action 125 native libraries.

## Using the app

- Start or open a project conversation, then use **Workspace / مساحة العمل** for Files, Memory and Improve.
- Files supports HTML, Markdown, PDF, ZIP and TXT import, creation, reading and export.
- Memory is local and project-scoped. Search its Arabic/English lessons and evidence, delete them or disable capture. Observed corrected tool calls can become lessons; this does not train model weights.
- Improve prepares a separate Python engine candidate. Packaged regression tests run before activation. Explicitly restart the engine after activation; Restore original selects the packaged engine. Android rechecks the code and app build before startup.
- Connections uses the deployed broker and native account vault. Current provider status and remaining registration requirements are in [REGISTRATION.md](connectors/hosted/REGISTRATION.md).
- The agent's `bash` commands run in the project and stream into the existing terminal pane as well as the conversation. Background servers use `bash(background=true)` and `job`. Shell permission checks and cancellation remain active; installing Termux is not required for the built-in shell.
- Each model request includes the actual terminal availability, permission mode and connected service tool counts, refreshed within existing conversations. The behavior profile requires relevant tools for execution and live account tasks, with build/test/repair guidance. This does not guarantee compliance by every model or install missing SDKs.
- **Connections → Add MCP server / إضافة خادم MCP** adds a remote Streamable HTTP endpoint to the current project, with an optional Bearer token. Test and add performs real initialization and paginated tool discovery before saving. Re-test and removal are supported; the running conversation refreshes its tools on its next request. Credentials live in a private app configuration file (mode 0600), outside project files and API listings. Custom browser OAuth and stdio installation are not part of this form; official OAuth services keep their existing connection cards.
- Android built-ins now include the existing Accessibility UI reader plus `screenshot`, `install_apk` (Android confirmation remains required), notification reading after the user enables Notification Access, and `automation_start/stop/list/replay`. `MusabTestBridge` exposes crash, test and ANR-report files for debug APKs; it does not pretend to read another app's Logcat. These are local phone capabilities and do not require wireless debugging or MCP.
- Model transport uses a real fallback pool. Kilo Auto Free is attempted without a key; OpenRouter, Groq, Cerebras, Gemini and Hugging Face free tiers are used when their own environment key is present. Overload, timeout, network and HTTP failures move to the next configured provider and the successful provider is recorded. Free-tier quotas and model names can change, so unconfigured entries are skipped instead of shown as connected.
- The built-in MCP catalog includes the official Microsoft Playwright MCP repository and the official MCP reference server templates (filesystem, memory and fetch). `/api/mcp-bundles` reports actual local runtime availability. Playwright/reference servers are started only where `node`/`npx` exists; no fake tools or hidden downloads are claimed. MusabAI's native files, memory and phone capabilities remain available when those runtimes are absent.

## Behavior profile

[agent_prompt.md](connectors/agent_prompt.md) adapts the user's uploaded `claude-code-cloud-fable-5.1.md` behavior instructions to MusabAI's actual tools and identity. The upload also contained another person's account/session context, repository directions and tool catalogs; those are not runtime configuration for this application. They are not included in the APK or this repository.

Source upload SHA-256: `a51e2376a001d5708b00762440d065e689a264b4de4f7f3ba55cd72f2b1ba863`.

The profile applies to default cloud/local conversations, including reopened conversations using the old built-in prompt. Messages and project instructions are preserved.

## Build and test

Root Gradle and `android-lite` build the same application: `dev.newal.code.lite.connectors`. Obsolete standalone chat source was removed and remains recoverable in Git history.

Run **MusabAI — Android** (`.github/workflows/android.yml`). It tests pinned agent integration, memory, Python/JavaScript/Java contracts and original engine features; then packages the verified native baseline and builds the APK. Download `MusabAI-Connectors` from the successful run.

Local UI tests: `npm ci --prefix connectors && npm test --prefix connectors`.
Memory tests: `python3 -m unittest discover -s desktop/autonomy -p 'test_*.py' -v`.
Set `NEWAL_UPSTREAM` to a Git checkout of pinned Musabgpt/NewAl source for agent integration tests.

The free default model endpoint can change availability and rate limits. Recalled memories enter the selected model's context like conversation text. Candidate checks execute with the application's OS permissions. Development signing and physical-phone acceptance are documented in [DEPLOYMENT.md](connectors/DEPLOYMENT.md).

See [applied recommendations and verification scope](connectors/EVOLUTION.md).

