"""Ships checked-hash bytecode inside the phone's engine and standard-library archives.

Run with Python 3.14, the phone's own version (the bytecode's magic number must match). Without it the phone compiles
every module the first time it runs after an install or an update: on a Galaxy A16 the engine answered after 1.8-2.6 s
instead of 0.4 s, and the first request compiled the agent too. The archives keep their sources. A checked-hash .pyc is
used only while its source is unchanged, whatever times the unzipped files get (the app's unzip gives them the
current time); a file changed later is simply compiled again.

The engine is compiled whole; of the standard library only what a start and a first request import (hot_stdlib.txt,
recorded on the phone), which keeps the APK about 2 MB larger instead of 8."""
import argparse
import os
import py_compile
import sys
import tempfile
import zipfile
from pathlib import Path

TAG = "cpython-314"
STDLIB = "lib/python3.14/"


def hot_stdlib():
    lines = (Path(__file__).with_name("hot_stdlib.txt")).read_text(encoding="utf-8").splitlines()
    return {STDLIB + x.strip() for x in lines if x.strip() and not x.startswith("#")}


def pyc_name(source):
    folder, _, name = source.rpartition("/")
    return (folder + "/" if folder else "") + "__pycache__/" + name[:-3] + "." + TAG + ".pyc"


def ship(archive, wanted):
    """Adds bytecode for the .py entries `wanted(name)` selects; returns how many were compiled."""
    with zipfile.ZipFile(archive) as z:
        entries = [(info, z.read(info)) for info in z.infolist()]
    names = {info.filename for info, _ in entries}
    compiled = []
    with tempfile.TemporaryDirectory() as tmp:
        for info, data in entries:
            name = info.filename
            if not name.endswith(".py") or not wanted(name) or pyc_name(name) in names:
                continue
            src = os.path.join(tmp, "src.py")
            out = os.path.join(tmp, "out.pyc")
            with open(src, "wb") as f:
                f.write(data)
            try:
                py_compile.compile(src, cfile=out, dfile=name, doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
            except py_compile.PyCompileError as e:
                print("skipped (does not compile): %s: %s" % (name, e.msg.splitlines()[-1]))
                continue
            with open(out, "rb") as f:
                compiled.append((pyc_name(name), f.read()))
    if not compiled:
        return 0
    rebuilt = archive.with_suffix(".tmp")
    with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in entries:
            z.writestr(info, data)
        for name, data in compiled:
            z.writestr(name, data)
    os.replace(rebuilt, archive)
    return len(compiled)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("assets", type=Path, help="android-lite/app/build/generated/newal-assets")
    args = parser.parse_args()
    if sys.implementation.cache_tag != TAG:
        raise SystemExit("run with Python 3.14 (the phone's version), not %s" % sys.version.split()[0])
    hot = hot_stdlib()
    engine = ship(args.assets / "newal_code.zip", lambda n: n.startswith("newal_code/"))
    stdlib = ship(args.assets / "python-stdlib.zip", lambda n: n in hot)
    print("checked-hash bytecode shipped: %d engine modules, %d of %d hot standard-library modules"
          % (engine, stdlib, len(hot)))
    if engine < 50 or stdlib < len(hot) // 2:
        raise SystemExit("too little bytecode shipped: the archives' layout changed?")


if __name__ == "__main__":
    main()
