# Phase 8 — Durable task checkpoints and resume

Phase 8 adds a small local project-scoped task ledger so MusabAI can continue long work from verified checkpoints after an app or session restart.

## Added

- `task_checkpoint` stores a bounded summary of objective, verified progress, next step, blocker and short evidence.
- `task_resume` returns a named checkpoint or the latest active task for the current project.
- `task_complete` closes a checkpoint only after verification.
- `task_list` lists bounded project task summaries.
- Checkpoints are written atomically under the NewAl data directory, outside the project workspace.
- Task IDs are validated and each project uses a hashed storage path.
- The store is bounded to 64 tasks, 20 history entries per task, and bounded text fields.
- The orchestrator recognizes continue/resume requests and recommends `task_resume` when available.
- Promptfoo adds a deterministic continuation-routing contract.
- Checkpoints are summaries only. They do not execute background work or bypass permissions.

## Verification

CI compiles the packaged engine, runs `newal_code.task_state_tests`, re-runs the full original/connector suite, then rebuilds and verifies the APK.
