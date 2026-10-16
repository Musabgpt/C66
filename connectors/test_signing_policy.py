import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GRADLE = (ROOT / "android-lite/app/build.gradle.kts").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")
FINGERPRINT = (ROOT / "android-lite/signing-cert-sha256.txt").read_text(encoding="utf-8").strip()


class SigningPolicyTests(unittest.TestCase):
    def test_release_signing_comes_from_environment_and_debug_is_opt_in(self):
        for name in (
            "MUSABAI_RELEASE_STORE_FILE",
            "MUSABAI_RELEASE_STORE_PASSWORD",
            "MUSABAI_RELEASE_KEY_ALIAS",
            "MUSABAI_RELEASE_KEY_PASSWORD",
        ):
            self.assertIn(name, GRADLE)
        self.assertIn('allowDebugSigning -> signingConfigs.getByName("debug")', GRADLE)
        self.assertIn("else -> null", GRADLE)
        self.assertNotIn(
            'signingConfig = signingConfigs.getByName("debug")',
            GRADLE,
        )

    def test_release_workflow_uses_one_bundled_secret(self):
        self.assertIn("secrets.MUSABAI_RELEASE_SIGNING_BUNDLE", WORKFLOW)
        for old_secret in (
            "MUSABAI_RELEASE_KEYSTORE_B64",
            "MUSABAI_RELEASE_STORE_PASSWORD",
            "MUSABAI_RELEASE_KEY_ALIAS",
            "MUSABAI_RELEASE_KEY_PASSWORD",
            "MUSABAI_RELEASE_CERT_SHA256",
        ):
            self.assertNotIn(f"secrets.{old_secret}", WORKFLOW)
        self.assertIn("base64 --decode", WORKFLOW)
        self.assertIn("json.loads", WORKFLOW)

    def test_apksigner_and_pinned_fingerprint_gate_are_mandatory(self):
        self.assertRegex(FINGERPRINT, r"^[0-9a-f]{64}$")
        self.assertIn("android-lite/signing-cert-sha256.txt", WORKFLOW)
        self.assertIn('verify --verbose --print-certs MusabAI-Connectors.apk', WORKFLOW)
        self.assertIn('if [ "$actual" != "$expected" ]', WORKFLOW)
        self.assertIn("APK signing identity does not match the approved persistent fingerprint", WORKFLOW)

    def test_keystore_is_materialized_only_in_runner_temp_and_cleaned(self):
        self.assertIn('SIGNING_DIR="$RUNNER_TEMP/musabai-signing"', WORKFLOW)
        self.assertIn('KEYSTORE_FILE="$SIGNING_DIR/release.jks"', WORKFLOW)
        self.assertIn("base64 --decode", WORKFLOW)
        self.assertIn('rm -rf "$SIGNING_DIR"', WORKFLOW)
        self.assertIn("::add-mask::", WORKFLOW)
        self.assertNotIn("set -x", WORKFLOW)

    def test_missing_release_identity_produces_only_development_named_apk(self):
        self.assertIn("MusabAI-Connectors-DEV.apk", WORKFLOW)
        self.assertIn("MusabAI-Connectors-Development", WORKFLOW)
        self.assertIn("Development APK only", WORKFLOW)

    def test_repository_history_is_scanned_for_signing_material(self):
        self.assertIn("fetch-depth: 0", WORKFLOW)
        self.assertIn("git log --all --name-only", WORKFLOW)
        self.assertIn("git rev-list --all", WORKFLOW)
        self.assertIn("Private-key text exists in reachable Git history", WORKFLOW)

    def test_signing_material_patterns_are_ignored(self):
        for pattern in (
            "*.jks",
            "*.keystore",
            "*.p12",
            "*.pfx",
            "keystore.properties",
            "signing.properties",
        ):
            self.assertIn(pattern, GITIGNORE)


if __name__ == "__main__":
    unittest.main()
