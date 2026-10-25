#!/usr/bin/env bash
# Build BusyBox PIE for Android and package it as an extracted JNI library.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NDK="$(printenv ANDROID_NDK || true)"
if [[ -z "$NDK" ]]; then NDK="$(printenv ANDROID_NDK_HOME || true)"; fi
if [[ -z "$NDK" || ! -d "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin" ]]; then
  echo "Set ANDROID_NDK to a Linux Android NDK" >&2; exit 2
fi
TOOLS="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
VERSION=1.38.0
SHA256=34f9ea6ff8636f2c9241153b9114eefa9e65674a45318ae1ef95bb5f31c53bb2
WORK="$ROOT/android-lite/build/busybox-android"
mkdir -p "$WORK"
ARCHIVE="$WORK/busybox-$VERSION.tar.bz2"
if [[ ! -f "$ARCHIVE" ]]; then
  curl -fL --retry 3 --retry-delay 2 "https://busybox.net/downloads/busybox-$VERSION.tar.bz2" -o "$ARCHIVE"
fi
printf '%s  %s\n' "$SHA256" "$ARCHIVE" | sha256sum -c --status || {
  echo "BusyBox source SHA-256 mismatch" >&2; exit 1;
}
mkdir -p "$ROOT/android-lite/build/busybox-source"
cp "$ARCHIVE" "$ROOT/android-lite/build/busybox-source/"
SOURCE="$WORK/busybox-$VERSION"
if [[ ! -f "$SOURCE/Makefile" ]]; then tar -xjf "$ARCHIVE" -C "$WORK"; fi
if [[ "$#" -eq 0 ]]; then set -- arm64-v8a x86_64; fi
for abi in "$@"; do
  case "$abi" in
    arm64-v8a) clang="$TOOLS/aarch64-linux-android26-clang"; machine="AArch64"; arch=arm64 ;;
    x86_64) clang="$TOOLS/x86_64-linux-android26-clang"; machine="Advanced Micro Devices X86-64"; arch=x86_64 ;;
    *) echo "Unsupported Android ABI: $abi" >&2; exit 2 ;;
  esac
  [[ -x "$clang" ]] || { echo "Missing $clang" >&2; exit 2; }
  OUT="$WORK/build-$abi"
  mkdir -p "$OUT"
  make -s -C "$SOURCE" O="$OUT" ARCH="$arch" allnoconfig
  python3 - "$OUT/.config" ASH ASH_INTERNAL_GLOB SH_IS_ASH CAT LS GREP SED ECHO PWD MKDIR RM CP MV FIND HEAD TAIL PRINTF TEST TRUE FALSE WC SORT CUT TOUCH SLEEP UNAME TR BASENAME DIRNAME ENV AWK XARGS READLINK REALPATH <<'PY'
import pathlib
import re
import sys
path = pathlib.Path(sys.argv[1])
contents = path.read_text()
for name in sys.argv[2:]:
    key = "CONFIG_" + name
    pattern = rf"^(?:# {re.escape(key)} is not set|{re.escape(key)}=.*)$"
    if re.search(pattern, contents, re.M):
        contents = re.sub(pattern, f"{key}=y", contents, count=1, flags=re.M)
    else:
        contents += f"\n{key}=y\n"
contents = re.sub(r"^CONFIG_STATIC=y$", "# CONFIG_STATIC is not set", contents, flags=re.M)
contents = re.sub(r"^CONFIG_FEATURE_SUID=y$", "# CONFIG_FEATURE_SUID is not set", contents, flags=re.M)
path.write_text(contents)
PY
  (yes "" || true) | make -s -C "$SOURCE" O="$OUT" ARCH="$arch" oldconfig >/dev/null
  for applet in ASH CAT LS GREP SED; do
    grep -q "^CONFIG_$applet=y$" "$OUT/.config" || {
      echo "BusyBox $abi missing $applet" >&2; exit 1;
    }
  done
  make -s -C "$SOURCE" O="$OUT" ARCH="$arch" -j2 HOSTCC=cc CC="$clang" \
    EXTRA_CFLAGS="-fPIE -DANDROID -D__ANDROID__" \
    EXTRA_LDFLAGS="-pie -Wl,-z,max-page-size=16384"
  FILE="$OUT/busybox"
  [[ -s "$FILE" ]] || { echo "No BusyBox binary for $abi" >&2; exit 1; }
  "$TOOLS/llvm-readelf" -h "$FILE" | grep -q 'Type:.*DYN'
  "$TOOLS/llvm-readelf" -h "$FILE" | grep -q "$machine"
  "$TOOLS/llvm-readelf" -l "$FILE" | grep -q '/system/bin/linker64'
  DEST="$ROOT/android-lite/app/src/main/jniLibs/$abi"
  mkdir -p "$DEST"
  "$TOOLS/llvm-strip" --strip-unneeded -o "$DEST/libbusybox.so" "$FILE"
  echo "BusyBox $VERSION $abi installed ($(stat -c%s "$DEST/libbusybox.so") bytes)"
done
