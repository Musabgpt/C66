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

Setup creates a private bin/busybox symlink that points to the extracted binary. The existing agent PATH includes the private bin and exports NEWAL_BUSYBOX. The system shell, permissions and cancellation remain unchanged. BusyBox is not a Linux kernel and does not grant any additional Android privileges.

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
