"""Pictures the agent looked at, kept with their session so the chat can show them again.

A phone screenshot (or an image in the project that the agent looked at) is copied into the app's own folder, under the
session's id: the phone clears its screenshot cache when the next screenshot is taken, so the copy is what the chat
keeps.

* keep(sid, path): the copy's {"sid", "name"}, or None when the file is not a picture we show, is missing, or is bigger
  than MAX_BYTES. The answer never waits on this: a failed copy only means no picture in the chat.
* read(url): (bytes, mime) for /api/shot/<sid>/<name>, or None. Only names keep() made are served, and only raster
  types: never HTML or SVG, which could run on the app's own origin.
* drop(sid): removes a deleted session's pictures.

Nothing here reaches the model: the reference rides in a tool's meta, which only the chat reads.
Standard library only, so the tests load this file without the engine.
"""
import os
import re
import secrets
import shutil

TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MAX_BYTES = 6 * 1024 * 1024
SID = r"[A-Za-z0-9_\-]{1,80}"
NAME = r"[0-9a-f]{16}\.(?:jpg|png|webp)"
URL = re.compile(r"/api/shot/(%s)/(%s)" % (SID, NAME))


def folder():
    from . import settings          # the engine's folder; the tests pass their own
    return os.path.join(settings.HOME, "shots")


def keep(sid, path, root=None):
    sid, path = str(sid or ""), str(path or "")
    ext = os.path.splitext(path)[1].lower()[1:].replace("jpeg", "jpg")
    if ext not in TYPES or not re.fullmatch(SID, sid):
        return None
    try:
        if not 0 < os.path.getsize(path) <= MAX_BYTES:
            return None
        target = os.path.join(root or folder(), sid)
        os.makedirs(target, exist_ok=True)
        name = "%s.%s" % (secrets.token_hex(8), ext)
        shutil.copyfile(path, os.path.join(target, name))
    except OSError:
        return None
    return {"sid": sid, "name": name}


def read(url, root=None):
    m = URL.fullmatch(str(url or "").split("?", 1)[0])
    if not m:
        return None
    try:
        with open(os.path.join(root or folder(), m.group(1), m.group(2)), "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError:
        return None
    if not data or len(data) > MAX_BYTES:
        return None
    return data, TYPES[m.group(2).rsplit(".", 1)[1]]


def drop(sid, root=None):
    if re.fullmatch(SID, str(sid or "")):
        shutil.rmtree(os.path.join(root or folder(), str(sid)), ignore_errors=True)
