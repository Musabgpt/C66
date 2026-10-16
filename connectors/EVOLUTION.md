# Action 216 continuation: applied recommendations

Baseline: successful Action 216, commit
`d8a270382c51e009148fc2ae034beb40788fdf6c`.

Reviewed available upstream documents: `docs/musabai.md`, `docs/platform.md`,
and the September 22 local-coder-v2 specification/plan. Phone-local files were
not available in this workspace.

## Applied recommendations

- Keep knowledge outside model weights: project memory persists in SQLite across
  engine restarts with additive migration of existing lessons.
- Retrieve relevant evidence: indexed Arabic/English search replaces unrelated
  fallback. Recalled context includes supporting evidence within a bounded size.
- Learn from execution: a failed call followed by a corrected successful call can
  create a lesson. This proves a tool outcome, not whole-task correctness.
- Make memory reviewable: one Workspace panel contains Files, Memory and Improve.
  Search, forget, confirmed clear and persistent enable controls use project scope.
  Clearing memory preserves project files.
- Verify self-improvement: one candidate flow runs required packaged tests. The
  checked digest and app build are validated at activation and Android startup.
  Explicit restart applies a selection. Restore original selects the packaged code.
- Remove duplicate paths: root and Android Gradle build the same app. Obsolete
  basic-chat source is removed and recoverable through Git history.
- Adopt the uploaded prompt's portable work/context/delivery instructions with
  MusabAI identity and real tool names. Old external session/account details and
  tool definitions are excluded. This changes behavior instructions, not the model.

## Separate work

The local-coder-v2 plan targets a different offline GGUF app. Its native sampler,
KV and llama.cpp replacement is not transplanted into the retained native baseline.
Existing local-model, Termux, skills, plugins, terminal and review features remain.

These changes do not provide universal automation, unlimited service access, model
fine-tuning or a measured intelligence/speed increase. Tests below establish code
contracts; physical-phone lifecycle and live account consent remain device checks.
Candidate verification is local process execution, not an OS security sandbox.

## Verification

The Actions workflow runs memory persistence/retrieval/redaction/migration tests,
pinned agent-loop tests, API/UI controls, native candidate/readiness tests, uploaded
prompt integration, original engine regression tests, OAuth/connector contracts,
packaged-feature checks, Java compilation and full APK assembly.
