"""Pictures in the chat: a phone screenshot, or an image the agent looks at, shows in its tool card; a tap enlarges it.

* session_images.py keeps a copy of each picture with its session. /api/shot/<session>/<name> serves those copies only:
  raster files, with nosniff and a no-script policy, so nothing served there can run on the app's own origin.
* deleting a session deletes its pictures.
The model's input does not change: the picture reference rides in the tool's meta, which only the chat reads.
"""
import shutil

ROUTE = '''            if path.startswith("/api/shot/"):
                from . import session_images
                hit = session_images.read(path)
                if hit is None:
                    return self._json({"error": "no such picture"}, 404)
                data, mime = hit
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=86400")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(data)
                return
'''


def apply(src, package, replace):
    shutil.copyfile(src / "session_images.py", package / "session_images.py")
    replace(package / "server.py", '            if path == "/api/file":\n',
            ROUTE + '            if path == "/api/file":\n')
    replace(package / "session.py",
            '    shutil.rmtree(os.path.join(settings.CHECKPOINTS, sid), ignore_errors=True)\n',
            '    shutil.rmtree(os.path.join(settings.CHECKPOINTS, sid), ignore_errors=True)\n'
            '    from . import session_images\n    session_images.drop(sid)\n')
