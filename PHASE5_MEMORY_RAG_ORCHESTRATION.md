# MusabAI Phase 5 — Memory + Project RAG + Orchestration

This phase builds on the existing project-scoped durable memory instead of replacing it.

## Added

- **Project RAG**: dependency-free SQLite lexical index for readable project code/text files.
- Incremental refresh based on file metadata and content digest.
- Bounded chunks with file path and line references.
- Index data is stored outside the user project and is never uploaded by the RAG layer.
- Build outputs, VCS data, virtual environments and `node_modules` are excluded.
- **Orchestrator**: advisory tool planner that can recommend only tools exposed to the current session.
- Orchestration never executes actions, grants permissions, or claims unavailable backends.

## Agent integration

The packaged Action #43 engine exposes:

- `project_rag_index`
- `project_rag_search`
- `orchestrator_plan`

The system profile tells MusabAI to retrieve project evidence before broad edits and to use orchestration only when cross-tool routing is not obvious.

## Verification

Android CI runs the existing connector, memory, autonomy, browser, search and document tests plus:

- `newal_code.project_rag_tests`
- `newal_code.orchestrator_tests`

The APK is uploaded only after the full test and Gradle build pipeline succeeds.
