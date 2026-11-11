# C66 — MusabAI

Performance development copy of `Musabgpt/NewAl-Cloud`. See [C66 performance notes](C66_PERFORMANCE.md) for changes and verification scope.

MusabAI is a personal assistant on the user's Android phone: it reads and answers email and messages, books travel, publishes posts, fills forms and keeps reminders, with free models. (The repository began as an Android coding assistant; its developer interface and tools are still in the engine source but are no longer shipped or offered.)

## The personal assistant

- **One screen** (`connectors/assistant_ui/`): a conversation, six everyday tasks (email, messages, travel, posts, forms, reminders), voice, and a short settings sheet. Arabic (right to left) and English, light and dark. It is the only page in the app's package (`package_baseline.py` leaves the developer interface out, `verify_package.py` checks it).
- **One prompt** (`connectors/assistant_prompt.md`), written whole for everyday work: how to read mail and messages, how to write and publish, how to search and prepare a booking, how to fill a form, and what must never happen without the user (paying, passwords, a recipient they did not name).
- **An assistant's tools only** (`lean.py`, set by `apply_assistant.py`): `phone`, `phone_sequence`, `phone_routine`, `phone_agent`, `screen_look`, `browser`, `browser_agent`, `web_search`, `web_fetch`, `weather`, `my_info`, reminders (`scheduled_task_*`), and Gmail and Google Calendar when connected. No file, shell, GitHub or code tools.
- **My details** (`my_info`, `connectors/assistant.py`): the user's name, email, phone, city, passport and anything else they ask to keep, in one private file on the phone. The model always knows which details are saved and reads their values only when a form or a booking needs them.
- **Ask before sending** (on by default): a reply sent from a notification and an email show an approval first, in every permission mode. Messages and calls to a number open ready for the user's own tap, and payment is always the user's.
- The engine is no longer replaced on its own from the other project's update channel (that build carries the developer interface); this app updates through its own APK. `automatic_updates: true` in the settings switches it back on.
- `connectors/apply_assistant.py` applies all of this last, on the final engine files; `connectors/assistant_tests.py` covers it.

This development line starts from successful Action 216 (`d8a270382c51e009148fc2ae034beb40788fdf6c`). It keeps that build's pinned Action 43 Python engine and byte-verified Action 125 native libraries.

## The engine underneath (developer notes from the earlier interface)

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

## Task memory (ذاكرة المهام)

The assistant has a categorized library of how tasks are done on the phone and the web, shipped in the APK as one
SQLite database (`android-lite/app/src/main/assets/task_memory/library.db.gzip`, 163,085 procedures in 21 categories):
57 hand-written procedures for Facebook, WhatsApp, Instagram, Telegram, X, Gmail, YouTube, Play Store and settings
(button labels in English and Arabic), 15,273 AndroidControl demonstrations with every tap resolved to the label it hit,
1,009 Mind2Web website tasks (train split) and 146,746 InSTA-150K website goals.

- A phone request that fits a procedure reaches the model with it ("افتح فيسبوك وانشر منشور يقول صباح الخير" comes with
  the Facebook post procedure and its text), and the model runs it with `phone_routine id=tm:...`. The app carries out
  each step, trying every label, and stops where the screen differs so the model goes on by hand.
- **Task Memory** is a screen inside the app (Settings, "Tasks I know by heart"): categories, sources, search,
  "try a request", procedure details and a Run button (it confirms before posting or sending).
  MusabAI is one app with one launcher icon.
- Rebuilding the library from the datasets and how it is tested: [tools/task_memory](tools/task_memory/README.md).

## Phone and browser engines (Mobilerun, uiautomator2, Pydoll, Browser Use)

Beside the plain phone tool and the task memory, the assistant has four automation engines, all inside the one app
and all on the free model pool (no key of the user's own):

| Tool | Engine | For |
|------|--------|-----|
| `phone_agent` | [Mobilerun](https://github.com/droidrun/mobilerun) | a whole phone goal across screens and apps: it reads the real screen and taps, types and swipes until done |
| `phone_power`, `adb_connect` | [uiautomator2](https://github.com/openatx/uiautomator2) | low-level control over this phone's own wireless ADB (no computer), for what accessibility cannot reach |
| `browser` | [Pydoll](https://github.com/autoscrape-labs/pydoll) | one step in a background Chromium: open, extract (text plus the names of fields, buttons and links), click, type, press, back |
| `browser_agent` | [Browser Use](https://github.com/browser-use/browser-use) | a whole web task in the same Chromium, returning the answer |

- These libraries are Linux-only, so they run in the managed Debian environment in Termux
  (`connectors/automation_server.py`: one localhost server, token required). `connectors/automation_runtime.py`
  installs them on first use (a few minutes, once, with progress lines and Stop), starts the server, replaces one
  left by an older app, and exposes the tools.
- Mobilerun drives the phone through MusabAI's own accessibility bridge (`MusabDriver` over the app's phone server:
  `screen_tree`, `gesture`, `tap`, `type`), so it needs neither the Portal app nor ADB, and no screenshots.
- Android lets only one automation hold the screen: while uiautomator2 is attached it unbinds the app's
  accessibility service, and on some Android versions the service comes back dead after the detach. So
  `phone_power` attaches for one call only, always detaches, then checks the bridge (`a11y_state` plus a real
  screen read) and repeats the attach/detach cycle until it is back; if it never is, the result says the user
  must switch the service off and on. It never runs while `phone_agent` is working, and no uiautomator2 server is
  left running after a call.
- On Android 9 and older that detach is not safe at all while any accessibility service is on: the two death
  handlers in `UiAutomationManager` race (Android 10 added the lock), and `system_server` can die with "Windows
  for accessibility callback already set!", restarting the whole interface (seen on Android 9). There
  `phone_power` checks the Android version and the accessibility settings over ADB first and refuses before it
  attaches; with every accessibility service off it works.
- Every model call of these engines goes to the engine's local `/v1/free` endpoint (`connectors/automation_llm.py`):
  OpenAI-compatible, streaming or not, structured output on any model, an answer or a clear error in bounded time
  (a model that stalls or answers with nothing is replaced and cools down).
- The prompt has one routing policy: a known step uses the phone tool, a routine or a task-memory procedure; a whole
  goal uses `phone_agent`; background web work uses `browser`/`browser_agent`; a page the user should see or one that
  needs their logged-in session opens in the phone's Chrome.

Installing and trying it on a phone: [android-lite/INSTALL.md](android-lite/INSTALL.md).

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

