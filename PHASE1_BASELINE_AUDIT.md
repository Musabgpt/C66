# Phase 1 — Baseline Audit and Freeze

Date: 2026-10-06  
Repository: `Musabgpt/NewAl-Cloud`  
Phase branch: `phase1/baseline-audit`

## Frozen starting point

- Working baseline commit: `fe9eeb4230a915aed819e927694b6c7cd44ec881` (`feat/task-checkpoints-phase8`).
- Verified workflow: **MusabAI — Android**, run **#270**, run ID `37526094740`, conclusion **success**.
- Verified artifact: `MusabAI-Connectors`, artifact ID `11443190062`, size `39,973,550` bytes.
- Verified artifact digest: `sha256:bdefd3883cac3d1545aa40dcc52f01600c9efa49ff56df4ee4bd17a7bdea8a8e`.
- Pinned upstream NewAl engine source used by CI: `Musabgpt/NewAl@0bf36a3b3a813dbac424ee0c4dc6341f9e3fe0d3`.
- Pinned Android native baseline used by CI: Action #125, run ID `37110519785`, artifact `NewAl-Cloud-Action43`.
- `main` is not the development baseline: Phase 8 is **215 commits ahead** of `main`. Future repair phases must start from this Phase 1 branch or its final commit, not from `main`.

## What the current build actually does

The workflow:
1. checks out the pinned NewAl source;
2. overlays the autonomy/memory layer;
3. applies connector/MCP/runtime modules;
4. runs JS, Python, Promptfoo and Android unit tests;
5. downloads the exact Action #125 native APK baseline;
6. packages the tested Python engine into that baseline;
7. runs `:app:testReleaseUnitTest` and `:app:assembleRelease`;
8. uploads the APK artifact.

Run #270 confirms every workflow step completed successfully, including connector tests, durable memory tests, routing contract tests, authorization/original-feature tests and Android release build.

## Architecture audit relevant to the ten-phase repair

### AI provider path

`connectors/provider_pool.py` already has:
- a free-provider pool;
- retryable HTTP handling;
- one short retry;
- cooldown/circuit-breaker state;
- failover before visible streaming output;
- a health snapshot.

However it does **not** model request/provider capabilities such as Vision, Tools, JSON, long context or coding. Selection is still provider/model oriented rather than capability oriented. This is the architectural gap behind image requests being able to reach a text-only free endpoint.

The current error aggregation can also surface backend-style strings such as provider cooldown details after the pool is exhausted. Phase 2/3 must replace this with capability-aware selection and user-safe error reporting without deleting the existing working failover behavior.

### Long-running task path

Phase 8 added durable task checkpoints and resume support. That provides persistence, but it is **not** a task supervisor.

`EngineReadiness.java` only controls Android engine startup/health readiness. It does not supervise a long model/tool task, provide a task heartbeat, detect a stuck inference/tool loop, or implement a real cancellation lifecycle for the large-task case described by the user.

Therefore the infinite “working/thinking” symptom requires a separate Task Supervisor phase rather than another checkpoint patch.

### Termux / runtime path

The Android layer already contains useful working pieces that must be preserved:

- `Termux.java` checks a local service on `127.0.0.1:8791`.
- Termux commands are dispatched with Termux `RUN_COMMAND`.
- `TermuxJobs.java` stores real job state and receives callbacks rather than replaying timed-out commands.

The MCP bundle layer is where the important mismatch exists. `connectors/mcp_bundles.py` determines requirements with host-process checks such as `shutil.which("npx")`. In the packaged Android/Python process this checks the app/runtime environment, **not Termux's environment**. Therefore a real `npx` installed in Termux can still be reported missing.

This confirms the later fix should introduce explicit runtime ownership (`ANDROID_NATIVE`, `TERMUX`, `REMOTE`) and query Termux for Termux-owned dependencies instead of trying to expose Termux binaries through the Android app PATH.

### ADB audit

The native Android capability path itself is not implemented as an ADB dependency. ADB appears as a requirement for the separate Android/ADB MCP bundle. The later MCP/runtime work must keep ADB limited to integrations that genuinely need it and must not treat missing ADB as proof that the phone-native bridge is unavailable.

### MCP / Musab Hub audit

The vetted MCP catalog is centralized in `connectors/mcp_bundles.py`, which is good and should be retained.

The current readiness model is nevertheless runtime-location blind: a bundle can be marked `runtime_missing` because the packaged host cannot see a tool that exists in Termux. Musab Hub therefore needs runtime-aware dependency status rather than duplicated UI-side patches.

### Android package and signing audit

Current Android configuration:
- namespace: `dev.newal.code.lite`
- applicationId: `dev.newal.code.lite.connectors`
- versionCode: GitHub `NEWAL_BUILD` / workflow run number
- release build signing: **debug signing config**

The fixed application ID and increasing run-number version code are useful. The release signing is not suitable as the permanent update identity because the repository/workflow contains no persistent release keystore configuration. A stable release signing identity must be introduced in the dedicated signing/self-update phase; otherwise Android may reject an APK as an update when the signing certificate differs.

No signing key is added in Phase 1.

### Self-update audit

There is currently no complete signed self-update manager with staging, health verification, activation and automatic rollback.

Existing “improve/evolution” behavior is engine-oriented, and the current README explicitly describes restarting the engine after activation. The later self-update phase must distinguish hot-reloadable MusabAI components from native APK replacement, which remains subject to Android package-security rules.

## Files/areas identified for later phases

- `connectors/provider_pool.py`
- provider/model client definitions copied from the pinned NewAl engine
- `connectors/orchestrator.py`
- `connectors/task_state.py`
- `connectors/mcp_bundles.py`
- `connectors/mcp_config.py`
- `connectors/runtime.py`
- `connectors/mcp_ui.js`
- `android-lite/app/src/main/java/dev/newal/code/lite/Termux.java`
- `android-lite/app/src/main/java/dev/newal/code/lite/TermuxJobs.java`
- `android-lite/app/src/main/java/dev/newal/code/lite/WebBridge.java`
- `android-lite/app/src/main/java/dev/newal/code/lite/EngineReadiness.java`
- `android-lite/app/build.gradle.kts`
- `.github/workflows/android.yml`

## Phase 1 rules established

1. Do not restart from `main`.
2. Do not replace the Action #125 native baseline casually.
3. Preserve the working Termux RUN_COMMAND/job callback implementation.
4. Preserve Phase 8 checkpoints; add supervision around them later.
5. Do not add fake providers, fake MCP states, fake “connected” states or placeholder buttons.
6. Do not hard-code credentials or signing secrets.
7. Every later phase must run the full workflow, not only compile its own files.
8. A green build is necessary but not sufficient for later behavioral acceptance.

## Phase 1 acceptance status

- [x] Exact development baseline identified and frozen.
- [x] Successful reference build/run verified.
- [x] Reference artifact and SHA-256 digest verified.
- [x] Upstream engine commit and native baseline identified.
- [x] AI-provider root architectural gap located.
- [x] long-task/checkpoint boundary located.
- [x] Termux/npx environment mismatch located.
- [x] ADB scope identified.
- [x] MCP readiness path located.
- [x] APK package/version/signing state audited.
- [x] Self-update current boundary documented.
- [x] No functional subsystem replaced during the audit.

The only repository changes in this phase are this audit/freeze document and enabling CI on the Phase 1 branch so the unchanged baseline can be rebuilt and re-verified.
