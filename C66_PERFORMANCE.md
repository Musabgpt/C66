# C66 performance changes

Source: Musabgpt/NewAl-Cloud at `4e8fd61a133502e7d339f9d9404585168451bd15`.

## Changes

- Connections maintenance now ignores streamed chat updates. It still handles inserted dialogs, refreshed connector cards and language changes. Previously every chat DOM mutation scheduled a full connections tidy pass.
- Provider fallback selection checks capability requirements and cooldowns before fetching Android vault credentials. Previously excluded providers still incurred phone bridge requests (each bridge request has a 30-second timeout).
- Android CI accepts `main` and `c66-performance` so the new repository's branch can build using the existing pinned native baseline. No signing key is included. Without the release secret, the existing workflow builds a development APK.

## Evidence (2026-10-23, Linux)

- `node connectors/benchmark_lean.mjs`: 100 simulated chat updates schedule **0** connector maintenance frames, versus **100** with the original source. This measures eliminated work, not overall response latency or physical-phone frame rate.
- `npm test --prefix connectors`: **83 passed**, 0 failed.
- Assembled pinned Python engine (`0bf36a3b3a813dbac424ee0c4dc6341f9e3fe0d3`) with autonomy and connector/parity patches, then ran `provider_pool_tests`, `provider_keys_tests`, `claude_parity_tests`: **134 tests, OK, 3 skipped**. An existing ResourceWarning for an unclosed reader in `service.py` was emitted.
- Signing/update publication tests: **8 passed**.
- Regression demonstrated before the fix: two excluded providers still read credentials. After the fix: no credential reads for those routes.
- New UI regression tests failed on the original implementation (unnecessary scheduled work and missing language-change refresh) and pass after the change.
- `git diff --check`: clean.

The first temporary engine assembly lacked the Android source/assets layout and could not run the lean module tests. The harness was rebuilt with the current Android sources; full connector/parity patch application then succeeded. No APK build or installation of these changes has been performed. Live provider response time remains dependent on the selected provider, network and request size.

## Reproduce UI benchmark

```sh
npm ci --prefix connectors --ignore-scripts --no-audit --no-fund
git show 4e8fd61a133502e7d339f9d9404585168451bd15:connectors/lean_ui.js > /tmp/c66-original-lean.js
node connectors/benchmark_lean.mjs /tmp/c66-original-lean.js
node connectors/benchmark_lean.mjs
npm test --prefix connectors
```

The original remote is named `upstream`; changes are on `main`. Publishing to `Musabgpt/C66` requires GitHub authentication with repository creation access. The original repository has not been modified.
