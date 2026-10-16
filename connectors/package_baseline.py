"""Reuse the exact native programs/libraries from Action 125, replacing only the engine archive.

The original APK's digest is pinned. This avoids building a different/latest
llama.cpp while claiming an Action 125 baseline and makes connector builds faster.
"""
import argparse
import hashlib
from pathlib import Path
import zipfile

APK_SHA256 = "c323e9e75ca29db3a11ab9d93d1f3de8457c03f41c1ee9fe53b541c7d61114ae"
ASSETS = ("cacert.pem", "python-stdlib.zip", "python-extra.zip", "python-dynload-arm64-v8a.zip", "python-dynload-x86_64.zip")
LIBRARIES = ("libcrypto_python.so", "libllama-server.so", "libnewalpy.so", "libpython3.14.so", "libsqlite3_python.so", "libssl_python.so")


def package(apk_path, root):
    if hashlib.sha256(apk_path.read_bytes()).hexdigest() != APK_SHA256:
        raise SystemExit("Action 125 APK digest does not match; refusing a different native baseline")
    assets = root / "android-lite/app/build/generated/newal-assets"
    assets.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk_path) as apk:
        for name in ASSETS:
            (assets / name).write_bytes(apk.read("assets/" + name))
        for abi in ("arm64-v8a", "x86_64"):
            dest = root / "android-lite/app/src/main/jniLibs" / abi
            dest.mkdir(parents=True, exist_ok=True)
            libs = LIBRARIES + (("libllama-server-dotprod.so",) if abi == "arm64-v8a" else ())
            for name in libs:
                (dest / name).write_bytes(apk.read("lib/" + abi + "/" + name))
    source = root / "desktop/newal_code"
    with zipfile.ZipFile(assets / "newal_code.zip", "w", zipfile.ZIP_DEFLATED) as engine:
        for file in sorted(source.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                engine.write(file, "newal_code/" + file.relative_to(source).as_posix())
        vendor = Path("/tmp/newal-pdf/pypdf")
        if not vendor.is_dir():
            raise SystemExit("Pinned PDF reader must be installed before packaging")
        for file in sorted(vendor.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                engine.write(file, "pypdf/" + file.relative_to(vendor).as_posix())
    print("Exact Action 125 native baseline retained; current tested engine packaged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    package(args.apk, args.root)
