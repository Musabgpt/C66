# MusabAI Phase 6 — Execution

Phase 6 adds a local-first execution layer without making a paid cloud service or ADB a runtime requirement.

## Added

- **Execution router** that selects only tools actually exposed to the current session.
- **Local scratch execution** with:
  - a fresh temporary working directory per call;
  - explicit input files only;
  - bounded input size, runtime and captured output;
  - scrubbed environment;
  - no implicit project mutation.
- The local scratch runner is deliberately described as **not an OS/container security boundary**.
- **Termux-first Android execution** when a connected Termux tool exists.
- **Optional Appium/E2B routing** only when those MCP tools are genuinely connected; they are never required for the APK and availability is never invented.
- **Bounded local Git tools** for status, diff, log and explicit-path local commits.
  - no push;
  - no force;
  - no history rewrite;
  - no remote mutation;
  - no credential storage.

## Why this is local-first

The original architecture reference names E2B, Appium and isomorphic-git. MusabAI must remain useful without paid services or extra runtimes:

- E2B remains an optional connected backend rather than a hard dependency.
- Appium remains optional for device automation; Android-native controls and Termux continue to work without ADB.
- The packaged Python engine uses the Git executable already available on the current host or Termux. Bundling isomorphic-git would require a JavaScript runtime/dependency inside the APK, so Phase 6 keeps the same Git capability without making Node a mandatory APK dependency.

## Verification

CI runs:

- `newal_code.execution_tests`
- `newal_code.git_workspace_tests`
- all earlier connector, memory, RAG, orchestration and Android tests
- the real release APK build

The artifact is uploaded only after the tests and Gradle build succeed.
