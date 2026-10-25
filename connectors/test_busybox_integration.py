"""Fail closed when Android BusyBox packaging or app PATH integration is incomplete."""
import pathlib
import struct
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETUP = ROOT / "android-lite/app/src/main/java/dev/newal/code/lite/Setup.java"
APPLET_ROUTER = ROOT / "android-lite/app/src/main/java/dev/newal/code/lite/BusyBoxApplets.java"
BUILD = ROOT / "android-lite/native/build_busybox.sh"
EXPECTED = {"arm64-v8a": 183, "x86_64": 62}


class BusyBoxIntegration(unittest.TestCase):
    def test_java_link_and_env(self):
        text = SETUP.read_text()
        applets = APPLET_ROUTER.read_text()
        self.assertIn('new File(libDir, "libbusybox.so")', text)
        self.assertEqual(text.count('BusyBoxApplets.install(bin, new File(libDir, "libbusybox.so"))'), 2)
        self.assertIn('BusyBoxApplets.ready(bin, busyboxLibrary)', text)
        self.assertIn('env.put("NEWAL_BUSYBOX_AUTO", "1")', text)
        self.assertIn('env.put("NEWAL_BUSYBOX", new File(bin, "busybox").getAbsolutePath())', text)
        self.assertIn('env.put("PATH", bin.getPath() + ":"', text)
        self.assertIn('Files.createSymbolicLink(link, target)', applets)
        self.assertIn('Files.isSymbolicLink(link)', applets)
        self.assertNotIn('"sh",', applets)  # Do not replace Android's shell.
        self.assertNotIn('"git",', applets)  # Preserve the app's Python-backed git.

    def test_pinned_sources_and_security(self):
        source = BUILD.read_text()
        self.assertIn('VERSION=1.38.0', source)
        self.assertIn('SHA256=34f9ea6ff8636f2c9241153b9114eefa9e65674a45318ae1ef95bb5f31c53bb2', source)
        self.assertIn('sha256sum -c --status', source)
        self.assertIn('EXTRA_LDFLAGS="-pie -Wl,-z,max-page-size=16384"', source)
        self.assertIn('libbusybox.so', source)

    def test_auto_applets_are_present_in_pinned_build(self):
        code = APPLET_ROUTER.read_text()
        build = BUILD.read_text()
        for name in ("AWK", "FIND", "GREP", "SED", "LS", "CP", "MV", "RM", "MKDIR", "XARGS"):
            self.assertIn('"' + name.lower() + '"', code)
            self.assertIn(' ' + name + ' ', build)
        self.assertIn('NEWAL_BUSYBOX_AUTO', (ROOT / "connectors/termux_loop.py").read_text())
        self.assertIn('busybox_auto', (ROOT / "connectors/agent_policy.py").read_text())
        self.assertIn('NEWAL_TOYBOX', SETUP.read_text())
        self.assertIn('toolchain_status', (ROOT / "connectors/lean.py").read_text())
        self.assertIn('phone_sequence', (ROOT / "connectors/phone_fast.py").read_text())
        for name in ("TAR", "UNZIP", "GZIP", "HTTPD", "CROND", "CRONTAB",
                     "WGET", "PS", "TOP", "SHA256SUM", "DIFF", "PATCH"):
            self.assertIn(' ' + name + ' ', build)
            self.assertIn('"' + name.lower() + '"', code)
        self.assertNotIn('"toybox",', code)  # System Toybox stays on /system/bin.
        self.assertNotIn('"sh",', code)      # Never replace Android's /system/bin/sh.

    def test_one_tap_overlay_microphone_and_secure_nvidia_integration(self):
        manifest = (ROOT / "android-lite/app/src/main/AndroidManifest.xml").read_text()
        main = (ROOT / "android-lite/app/src/main/java/dev/newal/code/lite")
        bridge = (main / "WebBridge.java").read_text()
        wizard = (main / "FirstRunSetup.java").read_text()
        overlay = (main / "FloatingAssistantService.java").read_text()
        self.assertIn('android.permission.SYSTEM_ALERT_WINDOW', manifest)
        self.assertIn('android.permission.RECORD_AUDIO', manifest)
        self.assertIn('android.permission.FOREGROUND_SERVICE_MICROPHONE', manifest)
        self.assertIn('android:foregroundServiceType="microphone|specialUse"', manifest)
        self.assertIn('Settings.canDrawOverlays', wizard)
        self.assertIn('A11y.enabled', wizard)
        self.assertIn('requestPermissions', wizard)
        self.assertIn('Termux.allowed', wizard)
        self.assertIn('ProviderSecrets.handle', wizard)
        self.assertIn('window.openMusabConnectors', (main / "MainActivity.java").read_text())
        self.assertIn('FirstRunSetup.startOverlay', bridge)
        self.assertIn('SpeechRecognizer.createSpeechRecognizer', overlay)
        self.assertIn('ui.postDelayed(restartSpeech', overlay)
        self.assertIn('X-NewAl-Key', overlay)
        self.assertIn('/api/sessions/', overlay)
        self.assertIn('FloatingAssistantService.ACTION_STOP', bridge)
        self.assertIn('nvidia_models.models()', (ROOT / "connectors/apply.py").read_text())
        self.assertNotIn('nvapi-', (ROOT / "connectors/nvidia_models.py").read_text())
        self.assertNotIn('nvapi-', overlay)

    def test_actual_android_elf_binaries(self):
        for abi, machine in EXPECTED.items():
            with self.subTest(abi=abi):
                binary = ROOT / "android-lite/app/src/main/jniLibs" / abi / "libbusybox.so"
                self.assertTrue(binary.is_file(), f"Missing built BusyBox for {abi}")
                data = binary.read_bytes()
                self.assertGreater(len(data), 10000)
                self.assertEqual(data[:4], b"\x7fELF")
                self.assertEqual(data[4], 2, "64-bit binary required")
                self.assertEqual(data[5], 1, "Little endian ELF required")
                self.assertEqual(struct.unpack_from("<H", data, 16)[0], 3, "Android PIE must be ET_DYN")
                self.assertEqual(struct.unpack_from("<H", data, 18)[0], machine)


if __name__ == "__main__":
    unittest.main()
