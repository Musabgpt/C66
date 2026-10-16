# MusabAI Phase 7 — Observability

Phase 7 closes the original implementation plan with privacy-first local tracing and deterministic CI evaluation.

## Local observability is the default

MusabAI writes bounded JSONL operational metadata outside the user's project.

It records only fields such as:

- event type;
- hashed session id;
- tool/model name;
- success/failure;
- duration and exit status;
- verification progress.

It deliberately does **not** record prompts, tool arguments, tool output, credentials, project file contents, or full free-form messages.

There is no automatic external telemetry.

## Optional OpenTelemetry export

The user can explicitly call `observability_export` after configuring a backend.

Supported targets:

- **Langfuse** through OTLP/HTTP `/api/public/otel/v1/traces`, using ingestion v4.
- **Phoenix** through OTLP/HTTP `/v1/traces`.

Only the same sanitized metadata is exported. HTTPS is required for non-loopback endpoints.

## Promptfoo

CI runs a deterministic Promptfoo suite through a local `exec:` provider. It requires no model API key and checks routing contracts such as:

- Android/Termux tasks expose the Termux and execution routes;
- Git tasks expose bounded Git inspection;
- web + project tasks expose search + local RAG.

## Verification

The Android workflow still requires all previous connector, memory, RAG, orchestration, execution and Git tests, then builds the real release APK and uploads the artifact.
