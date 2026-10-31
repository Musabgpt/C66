"""Model requests reuse their HTTPS connections and one TLS context.

Measured on a Galaxy A16 (Android 16, the app's own Python 3.14): building a TLS context and reading the CA list took
88 ms, and a new connection 130-170 ms more (DNS, TCP, TLS). Every model call of every step paid both. Now a response
read to its end leaves its connection here for the next request to the same host. A connection idle too long, closed
by the server or holding unread data is dropped, and a request that meets a connection the server closed as it went
out is sent once more on a new one (providers.Stream.resp)."""
import os
import select
import ssl
import threading
import time

IDLE_SECONDS = 45          # servers commonly drop idle keep-alive connections after a minute or more
PER_HOST = 4

_lock = threading.Lock()
_idle = {}
_contexts = {}


def ssl_context():
    """One verified TLS context per CA file (the Android app passes its own list in SSL_CERT_FILE)."""
    ca = os.environ.get("SSL_CERT_FILE") or ""
    with _lock:
        ctx = _contexts.get(ca)
        if ctx is None:
            ctx = ssl.create_default_context()
            if ca and os.path.exists(ca):
                ctx.load_verify_locations(ca)
            _contexts[ca] = ctx
        return ctx


def _usable(conn):
    sock = getattr(conn, "sock", None)
    if sock is None:
        return False
    try:
        if getattr(sock, "pending", None) and sock.pending():
            return False
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return False
    return not readable        # an idle connection with something to read was closed by the server


def _drop(conn):
    try:
        conn.close()
    except OSError:
        pass


def take(key):
    """A parked connection for `key` that still looks open, or None."""
    now = time.monotonic()
    with _lock:
        parked = _idle.get(key) or []
        while parked:
            conn, since = parked.pop()
            if now - since <= IDLE_SECONDS and _usable(conn):
                return conn
            _drop(conn)
    return None


def give(key, conn):
    """Parks a connection whose last response was read to its end."""
    if getattr(conn, "sock", None) is None:
        return
    with _lock:
        parked = _idle.setdefault(key, [])
        parked.append((conn, time.monotonic()))
        while len(parked) > PER_HOST:
            _drop(parked.pop(0)[0])


def parked(key=None):
    with _lock:
        if key is not None:
            return len(_idle.get(key) or [])
        return sum(len(v) for v in _idle.values())


def clear():
    with _lock:
        for conns in _idle.values():
            for conn, _ in conns:
                _drop(conn)
        _idle.clear()
