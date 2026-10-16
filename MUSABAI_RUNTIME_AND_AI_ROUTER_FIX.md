# MusabAI Phase 10 — implementation and acceptance evidence

Scope: `phase10/final-validation` only. This report compares the owner's uploaded
`MUSABAI_10_PHASE_PLAN.md` with the applied code and available evidence. It does not
close Phase 10 or start Phase 11. CI and Linux checks are distinguished from the
Samsung screenshots and owner reports.

## Comparison with the ten-phase plan

| Plan area | Applied implementation | Evidence / remaining acceptance |
| --- | --- | --- |
| Baseline and signing | Pinned Action 43 engine and Action 125 native baseline; persistent certificate gate | Owner: #314 installed; #318 upgraded without uninstall. #332 CI succeeded. New build must pass the same gate. |
| Capability router | `provider_pool.py` request requirements, provider capability checks | Regression checks cover text, vision rejection, tools and compatible fallback; new phone behavior remains open. |
| Free pool and health | Independent adapters, credentials filter, cooldown/Retry-After, bounded request recovery, configured downloaded local last resort | Contract tests cover rate-limit recovery, Stop, no retry after partial output, and mocked local connection. Actual alternative provider/local inference on Samsung remains open. |
| Supervisor | Existing heartbeat/watchdog/checkpoints plus correct unfinished-task outcomes | Budget, unmet goal and failing-verification integration tests exercise `Agent.run`; phone long-task, stop/resume and recovery remain open. |
| Runtime | Android native, authenticated Termux and configured remote routes | Owner proved Termux connection/probing. Actual shell nonzero results and transport errors have separate tests. |
| Termux bridge | v3 separates command outcome from transport and retains authentication | Real localhost HTTP/shell checks pass on Linux; Samsung managed discovery screenshots support the corrected installation path. |
| MCP preparation | Managed direct-node pinned installs, real initialize/initialized/tools/list, new agent provisioning tools | Real npm Memory/Playwright integration on Linux; Samsung shows 9/25 verified tools. Browser execution, Memory task and reconnect still open. |
| Hub state | Live lifecycle states, concrete errors; first installation error is `health_failed` | Screenshot sharp failure was diagnosed honestly; no fake Android MCP support is introduced. |
| Self-update | Existing staging/verification/health/rollback, new automatic GitHub channel and idle activation | Contract tests exercise integrity rejection, busy task deferral, restart selection and rollback blocking. Actual phone automatic update/rollback remains open. |
| Final acceptance | Real APK workflow and this report plus `PHASE10_ACCEPTANCE.md` | Phase 10 cannot close from CI alone. Remaining Samsung tests are listed in the ledger. |

## Root causes and changes

1. **Missing npm entry prevented installation.** `test -f` exited 1, bridge v2
   reported `ok=false`, and runtime raised a transport error. The earlier #332 fix
   uses bridge v3 with handled command results (`status`, `exit_code`,
   `command_success`, stdout/stderr); auth/transport errors still raise. The npm
   installer distinguishes missing entry, successful installation and timeout.
   Memory/Filesystem pin published 0.6.2; 0.6.3 did not exist. Runtime npx is not
   reinstated.
2. **One usable free tools provider was exhausted.** Screenshots show
   `kilo-auto/free` rate limiting then cooling down. Anonymous AI Horde text
   generation cannot replace a tools-capable request. The runtime now preserves
   prior tool results, tries compatible credentialed alternatives, tries an
   already downloaded configured capable local model, and retries only the model
   request after cooldown within a bounded budget. Stop interrupts the wait.
   Partial streamed output is never replayed. User-facing aggregate failures are
   Arabic; sanitized raw provider details remain in diagnostics. This cannot
   guarantee unlimited free quota or invent provider credentials.
3. **Provisioning was available primarily through the UI.** `automation.py`
   exposes `capability_catalog`, `capability_ensure`, `plugin_install`,
   `skill_create`, and MCP Registry discovery/installation to the agent. Compatible
   npm MCP installation and initialization happen inside the app. A newly created
   skill is callable in the current session. Plugins are restricted to the shipped
   marketplace; Registry auto-install supports verified anonymous literal HTTPS
   remotes, not arbitrary package commands. Skills contain instructions; they do
   not train model weights. Dependencies or account consent cannot be fabricated.
4. **Bounded repair could be saved as completion.** Exhausted step budgets, open
   circuit breakers, unmet goals and still-failing project verification now retain
   an unfinished checkpoint and blocker. Repairs remain bounded and evidence
   driven; the app does not run endlessly or declare a failed goal successful.
5. **Verified improvements required manual activation, and GitHub artifacts had
   no automatic feed.** `self_evolve_activate` queues only unchanged verified
   candidates. Successful signed Phase 10 CI publishes versioned engine/APK assets
   before a channel JSON pointer. `auto_update.py` checks exact repository/branch,
   version, source commit, archive hash and native compatibility fingerprint;
   performs packaged verification; waits for active tasks; and selects the engine.
   `automatic_updates.js` invokes the existing Android restart bridge. Startup
   health either confirms the revision or restores the previous one. Invalid
   releases are blocked; interrupted downloads have at most three attempts. The
   Improve panel displays actual automatic state and errors.

## Changed files and architecture

- Runtime correction from #332: `runtime_manager.py`, `termux_bridge_server.py`,
  managed npm setup/tests and bridge regression tests.
- Agent preparation: `automation.py`, `automation_tests.py`, `agent_prompt.md`,
  `agent_policy.py`, `mcp_bundles.py`, `mcp_bundles_tests.py`.
- Recovery: `provider_pool.py`, `provider_pool_tests.py`,
  `task_supervisor_tests.py`; `apply.py` applies changes to the pinned original
  agent/model/service/server instead of replacing the original engine.
- Updating: `auto_update.py`, `auto_update_tests.py`, `automatic_updates.js`,
  `evolution.py`, `workspace.js` and UI tests, `publish_update_channel.py`,
  `test_update_publication.py`, `verify_package.py`, and the Android workflow.
- Real integration: `phase10_mcp_validation.py` now invokes the agent-accessible
  Memory provisioning path before real MCP write/read and stop/start checks.

The update worker runs only in the packaged app, polls the channel roughly every
60 seconds, and activates only when no task is running/reserved. Python changes
need an engine restart; the WebView requests it after verified idle activation.
UI-only allowed resources use the existing hot-update mechanism. User model
responses still determine repair choices; this is a stronger execution harness,
not a claim to reproduce Codex/Claude model quality.

## Verification and limits

Tests reproduced missing automatic APIs, incorrect unfinished-task completion,
and the inaccessible newly created skill before their fixes. The applied engine's
trusted regression suite, UI tests and signing/publication contracts are run
locally; the workflow repeats regression checks, real npm/MCP checks, package
validation, Android unit tests, APK build and certificate verification. Exact
Action/commit/artifact and publication success are reported in the handoff only
after the new workflow finishes. No new CI result is assumed in this file.

The real Linux harness returned Memory 9 tools and Playwright 25 tools, exercised
Memory write/read persistence and stop/start, and successfully invoked
`capability_ensure` with real npm installation. It does not launch Termux Chromium
or substitute for Samsung tests. Local-model fallback uses a mocked model connection
in regression checks; actual local inference has not been demonstrated.

Samsung screenshots show installed/discovered Memory/Playwright, stopped after
verification, and Termux connected. They do not prove completed browser/Memory
tasks, automatic GitHub update/rollback, long-task recovery or crash recovery.
Filesystem remains intentionally blocked at an app-private root. Optional
external Android MCP's sharp dependency remains unsupported on this tested
Android runtime; the native phone tool is a separate working route. Browser Use,
Docling, open-browser-use and GitHub MCP need compatible dependencies/configuration;
Google services need owner-side service enablement/OAuth. None is marked successful
by these changes without verification.

A first installation of the new successful APK is required to obtain this updater.
Following compatible published **engine** updates can apply automatically. A
native APK change still needs Android's installation confirmation on an ordinary
Samsung phone. The channel does not automatically publish the phone's generated
code to GitHub or obtain GitHub credentials; local verified self-improvements can
activate locally, and CI-published repository changes can download automatically.

## Phase 10 follow-up: application-wide stability and task workspace

The post-#343 revision adds conversation-owned resumable checkpoints and a Tasks
workspace tab, preserves successful work on Stop, and counts terminal/tool JSON
streaming as progress. Nonzero shell outcomes cannot be recorded as completed
steps. Per-bundle lifecycle serialization prevents duplicate MCP processes and
coordinates with automatic updates. Real HTTP Retry-After headers reach provider
health; key replacement resets only the affected route, and the Hub exposes
sanitized cooldown state. Partial provider streams are protected from replay even
when no UI callback is installed.

Hub refresh no longer fails as a whole when one catalog is unavailable. Operation
feedback remains near its card, and refreshing providers retains unsaved drafts
without persisting their values. See `PHASE10_ACCEPTANCE.md` for evidence and the
remaining Samsung acceptance gates. No Phase 11 work or new device-pass claim is
included.

## Phase 10 follow-up: upstream idle timeout recovery

Screenshot 95182 exposed a request-boundary bug: partial reasoning/tool argument
events disabled failover before any tool had executed. The agent now discards
only that uncommitted draft and retries through the available free-provider pool
within a three-attempt bound. Completed tool history/checkpoints remain intact;
Stop cancels recovery. Idle timeouts no longer repeat the same slow request first.
SSE errors retain their status/retry metadata and unfinished streams cannot
return executable tool calls. Exhausted recovery shows an Arabic explanation;
the original provider error remains available in diagnostics.

Changed: `provider_pool.py`, `tool_protocol.py`, checked upstream patches in
`apply.py`, and the small `stream_ui.js` draft renderer. Regressions cover actual
HTTP/SSE interruptions, exactly-once completed file writes, discarded partial
calls, Stop, retry bounds, stream termination and UI draft isolation.

Local validation: 279 trusted engine tests and 65 UI tests passed; the original
146-test suite passed with its 19 existing skips. A live anonymous free API test
recovered from a controlled SSE idle timeout after partial output, selected
`nvidia/nemotron-3-ultra-550b-a55b:free`, and returned a complete write call that
wrote the expected file once on the Linux host. The primary failure was injected;
the fallback response was live. Samsung validation of this revision remains open.

## #346 device failures and the next correction

The user rejected #346 on-device: recovery still failed with large context, and
incomplete write/bash calls reached the ordinary three-failure circuit breaker.
The router had treated a syntactically completed response as provider success
without checking its tool arguments. Remote requests also resent old reasoning
and entire prior source writes until the model's late compaction threshold.

`request_context.py` now projects older completed tool groups into labeled
records without modifying stored messages or files. Required instructions and
images remain intact. `tool_protocol.validate_completion` checks all remote
agent tool calls before any are executed; `provider_pool` treats a rejected
completion as a model-quality failure and applies cooldown/failover. Missing
arguments are never fabricated. Checked patches in `apply.py` wire this into the
agent. Prompt v9 reaches existing built-in sessions, and Hub can display invalid
tool responses as a distinct provider state.

See `PHASE10_ACCEPTANCE.md` for the reproducible malformed-batch/large-history
tests and the live high-reasoning fallback result. An actual upstream 503 was
also observed and recorded. The new fix is not claimed device-accepted before
Samsung evidence is supplied, and no Phase 11 work is included.

### Build 348 follow-up research (2026-10-09)

Samsung screenshots still reject the stability claim. Request-scoped route
exclusion fixes retrying an interrupted model after its global cooldown expired.
Successful completed tools now reset consecutive stalls, and exhausted stall
recovery no longer impersonates an absolute task timeout.

The live official Kilo catalog at https://api.kilo.ai/api/gateway/models lists
additional zero-price tool models beyond the previous six-route cap. Discovery
now keeps up to 16 and rejects nonzero cache/search prices. Models on this gateway
still share its availability and rate limits. Google explicitly documents RPM,
TPM and RPD quotas at https://ai.google.dev/gemini-api/docs/rate-limits . Attempts
to fetch Groq/OpenRouter rate-limit documentation returned HTTP 403 here; no
claims of unlimited access or tested independent fallback are inferred.

The Improve screen now controls the existing automatic verified GitHub updater.
This is software update automation, not training hosted models. Downloading tests
does not update a third-party model's weights. Indefinite autonomous self-training,
credential-free access to arbitrary providers, and automatic activation of every
external integration are not implemented or claimed.
