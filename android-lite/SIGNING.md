# MusabAI Android signing continuity

Phase 9 requires a persistent Android signing identity for every publishable APK.

## Why this exists

Phase 8 Action #304 and Phase 9 Action #306 were both built with Android debug signing, but their certificate SHA256 fingerprints were different because separate GitHub-hosted runners generated different debug keystores. An APK signed by a different certificate cannot update an already installed APK in place.

The private signing key cannot be recovered from an APK certificate.

## Persistent release identity

The publishable workflow expects exactly one GitHub Actions Secret:

- `MUSABAI_RELEASE_SIGNING_BUNDLE`

That Secret is a base64-encoded JSON bundle containing the release keystore (itself base64-encoded), store password, key alias, and key password. The bundle must be generated and backed up outside the repository.

The approved certificate SHA256 fingerprint is intentionally not secret. It is pinned in:

- `android-lite/signing-cert-sha256.txt`

Keeping the public fingerprint in version-controlled code makes an identity change visible in repository history and prevents replacing both the private key and its expected fingerprint only through GitHub Secret administration.

The workflow materializes signing data only under `RUNNER_TEMP`, uses restrictive file permissions, never enables shell tracing, masks derived password values before invoking Gradle, passes signing values through environment variables rather than command-line arguments, verifies the APK with `apksigner`, compares the extracted certificate SHA256 fingerprint with the pinned value, and deletes temporary signing files before the build step exits.

## CI policy

A publishable `MusabAI-Connectors` artifact is uploaded only when `MUSABAI_RELEASE_SIGNING_BUNDLE` is present, the bundle is valid, the APK passes `apksigner verify`, and its certificate fingerprint exactly matches the pinned fingerprint.

After first provisioning or rotation of the signing Secret, start a fresh workflow run so the job receives the current protected value from the beginning.

If the release-signing Secret is absent, CI may still build `MusabAI-Connectors-Development` using an explicitly opted-in debug identity. That artifact is development-only and is not an update-signing baseline.

If the signing bundle is malformed, incomplete, or signs the APK with a certificate different from the pinned fingerprint, CI fails and no publishable release artifact is uploaded.

Tracked `.jks`, `.keystore`, `.p12`, `.pfx`, `keystore.properties`, `signing.properties`, and PEM private-key material are rejected by CI.

## Local builds

Without release-signing environment variables, the release variant is unsigned by default. A local developer can opt into a debug-signed release-shaped APK with:

`MUSABAI_ALLOW_DEBUG_SIGNING=true`

That opt-in build is for development only and must not be distributed as the MusabAI update baseline.

## One-time migration

Unless the private key used by Phase 8 Action #304 is found in an existing secure secret store, the first APK built with the new persistent release identity cannot update #304 in place. Devices with #304 installed need one uninstall/reinstall migration to the new persistent baseline. After that migration, future APKs can update in place as long as the same persistent signing key is retained and the CI fingerprint gate remains unchanged.

Keep at least two encrypted backups of the release keystore in separate secure locations. Losing the private key permanently breaks update continuity for installs signed by it.
