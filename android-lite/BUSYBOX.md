# BusyBox on Android (MusabAI C66)

MusabAI packages [BusyBox](https://busybox.net/) as a separate executable, not as a replacement for the Python agent or Android's system shell.

## Version, build and GPLv2 source

- Upstream: BusyBox 1.38.0 (GPL-2.0-only), https://busybox.net/downloads/busybox-1.38.0.tar.bz2
- SHA-256: 34f9ea6ff8636f2c9241153b9114eefa9e65674a45318ae1ef95bb5f31c53bb2
- Build instructions and applet selection: [native/build_busybox.sh](native/build_busybox.sh)
- For each APK distribution, make the corresponding upstream source archive and build script available alongside the APK. CI uploads both into its Android build artifacts; do not publish the APK alone without GPLv2 source availability.
- The archive contains BusyBox's GPLv2 license, notices and full corresponding upstream source.

## Android execution

The Android NDK compiles separate 64-bit PIE builds for arm64-v8a and x86_64. Each binary is packaged as lib/<abi>/libbusybox.so to use the application's extracted native-library directory. Android prohibits executing downloaded binaries out of writable app data on recent releases.

Setup installs private symlinks for a curated set of applets (such as ls, grep, find, awk, sed, cat, cp, mv, rm, mkdir, xargs and others). The existing agent PATH begins with this private bin directory, so **normal shell commands automatically run through BusyBox** without the user or model adding a busybox prefix. No separate Termux installation is necessary for these tools.

The normal Android /system/bin/sh, Python, git and other runtimes are never overwritten. Installation is idempotent on every app start and preserves pre-existing regular files. NEWAL_BUSYBOX and NEWAL_BUSYBOX_AUTO are set only when the bundled native executable is available.

When Termux is connected and the project is visible to both apps, MusabAI executes simple BusyBox commands locally and routes commands requiring node, npm, gh, compilers or complex scripts to Termux; it never automatically replays a command after failure. If the Android app lacks permission to read the project, existing Termux routing is preserved. The existing permission mode, cancellation and timeouts remain active; BusyBox does not grant root or new Android privileges.

## Extended Android command suite and system Toybox

The build now enables roughly 100 user-space applets rather than only the original small set. It includes archive creation/extraction (tar, gzip, bzip2, unzip, xz), file checksums (sha256sum etc.), diff/patch, process inspection, httpd, cron applets and a small HTTP downloader. The actual applet count is established by the build and can be inspected with `toolchain_status` or `busybox --list`. It is not a complete Linux system: kernel/admin tools that require root, unavailable Bionic APIs, and unsupported applets cannot be made functional just by compiling BusyBox.

Toybox is already included with stock Android at /system/bin/toybox. MusabAI sets NEWAL_TOYBOX when it is executable; do not bundle another Toybox binary or override the system implementation. Run `toybox` to see its commands. It remains subject to the application's normal Android UID and SELinux limitations.

IMPORTANT: The bundled BusyBox wget intentionally does not claim certificate-verified HTTPS. Use the Python HTTPS client with trusted CAs or the installed Termux curl/wget for HTTPS downloads; do not fall back to insecure TLS flags.

## Chrome and Termux integration

On a phone with Chrome and the NewAl Code Accessibility service enabled, ordinary HTTP(S) links open in Chrome. The agent can inspect the numbered screen and use `phone_sequence` for an explicit short series of taps, field input, scrolling and a requested submission, with a confirmation-text check. CAPTCHA, inaccessible fields and login verification can still require user interaction. This does not bypass browser security or Android permissions; a click is not proof of a successful submission. Form typing done by phone_sequence is not saved into Android's replayable action recorder.

When Termux is connected, npm/pip/pkg/gh/SDK commands run through its authenticated loopback bridge. Even when the current app-private project path cannot be accessed by Termux, development/package commands still run in Termux's own home. Access to a project's files requires a shared workspace. Termux package installation changes Termux's own sandbox, never the Android system partition, and does not grant root.

## Automatic-use acceptance test

In a fresh MusabAI chat, ask normally: "Create a folder and three test files, search them for a keyword, and show me the results." The agent should use its ordinary file/bash tools without requesting a BusyBox prefix. In bash metadata, a simple command run locally reports runtime android-busybox. When a connected Termux is required for node/npm/gh, the existing termux runtime remains available.

## On-device smoke checks

Inside MusabAI's built-in terminal:

    busybox --help
    busybox --list
    busybox sh -c 'echo MUSAB_BUSYBOX_OK'
    busybox ls .
    printf 'hello\n' | busybox grep hello
    echo "$NEWAL_BUSYBOX"

The standalone BusyBox CI job checks native ELF architecture and PIE. This is not a substitute for testing the APK on a real Android device; smoke checks must pass on the phone.

The main Android workflow builds BusyBox after unpacking the pinned native libraries and before building the APK. The original Python engine, local shell, optional Termux link and providers are intentionally unchanged.
