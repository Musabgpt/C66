"""Faster model calls (http_pool.py): one TLS context, and connections kept for the next request to the same host."""
import shutil


def apply(src, package, replace):
    for name in ("http_pool.py", "http_pool_tests.py"):
        shutil.copyfile(src / name, package / name)

    replace(package / "providers.py",
            "    def recv_into(self, buf, nbytes=0, flags=0):\n"
            "        while not self._ready():\n"
            "            if self._stream.closed:\n"
            "                raise ConnectionAbortedError(\"cancelled\")\n",
            "    deadline = 0.0             # reading a finished response's last bytes (Stream._park) waits no longer\n"
            "\n"
            "    def recv_into(self, buf, nbytes=0, flags=0):\n"
            "        while not self._ready():\n"
            "            if self._stream.closed or (self.deadline and time.monotonic() > self.deadline):\n"
            "                raise ConnectionAbortedError(\"cancelled\")\n")

    replace(package / "providers.py", '''class Stream:
    """One streaming POST. close() from another thread aborts a blocked read (within 0.25 s)."""

    def __init__(self, url, body, headers, timeout=1800):
        u = urllib.parse.urlsplit(url)
        self.scheme, host = u.scheme, u.hostname
        port = u.port or (443 if u.scheme == "https" else 80)
        path = u.path + ("?" + u.query if u.query else "")
        proxy = _proxy_for(u.scheme, host)
        ctx = ssl.create_default_context() if u.scheme == "https" else None
        if ca := os.environ.get("SSL_CERT_FILE"):
            if ctx and os.path.exists(ca):
                ctx.load_verify_locations(ca)
        if proxy:
            conn_host, conn_port = proxy.hostname, proxy.port or 80
        else:
            conn_host, conn_port = host, port
        if u.scheme == "https":
            self.conn = http.client.HTTPSConnection(conn_host, conn_port, timeout=timeout, context=ctx)
        else:
            self.conn = http.client.HTTPConnection(conn_host, conn_port, timeout=timeout)
        if proxy:
            if u.scheme == "https":
                self.conn.set_tunnel(host, port)
            else:
                path = url
        self.closed = False
        self._resp = None
        data = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "NewAl-Code"}
        hdrs.update(headers or {})
        self.conn.request("POST", path, body=data, headers=hdrs)
        self.conn.sock = _Watched(self.conn.sock, self)
        self._owner = threading.get_ident()

    @property
    def resp(self):
        """The response (waits for its headers: for a local model, until the prompt is read). A close() from another
        thread meanwhile ends the wait with Cancelled."""
        if self._resp is None:
            try:
                self._resp = self.conn.getresponse()
            except (OSError, ValueError, http.client.HTTPException, AttributeError):
                if self.closed:
                    raise Cancelled()
                raise
        return self._resp

    def lines(self):
        while True:
            try:
                raw = self.resp.readline()
            except (OSError, ValueError, http.client.HTTPException):
                if self.closed:
                    raise Cancelled()
                raise
            if not raw:
                if self.closed:
                    raise Cancelled()
                return
            yield raw.decode("utf-8", "replace").rstrip("\\r\\n")

    def read_all(self):
        try:
            return self.resp.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            return ""

    def close(self):
        """Ends the request. From another thread (a cancel), the reading thread stops within 0.25 s and closes
        the connection itself: closing it here could wait on the lock that thread's read holds."""
        self.closed = True
        sock = getattr(self.conn, "sock", None)
        real = getattr(sock, "_s", sock)
        if real is not None:
            try:
                real.shutdown(socket.SHUT_RDWR)          # tells the server at once (it stops generating)
            except OSError:
                pass
        if threading.get_ident() == getattr(self, "_owner", None):
            try:
                self.conn.close()
            except OSError:
                pass
''', '''class Stream:
    """One streaming POST. close() from another thread aborts a blocked read (within 0.25 s). The reading thread's
    own close of a response read to its end leaves the connection to the next request to the same host (http_pool)."""

    def __init__(self, url, body, headers, timeout=1800):
        from . import http_pool
        u = urllib.parse.urlsplit(url)
        self.scheme, host = u.scheme, u.hostname
        port = u.port or (443 if u.scheme == "https" else 80)
        path = u.path + ("?" + u.query if u.query else "")
        proxy = _proxy_for(u.scheme, host)
        if proxy:
            conn_host, conn_port = proxy.hostname, proxy.port or 80
            if u.scheme != "https":
                path = url
        else:
            conn_host, conn_port = host, port
        self._key = (u.scheme, conn_host, conn_port, host if proxy else "", port if proxy else 0)
        self._timeout = timeout
        self.closed = False
        self._done = False
        self._resp = None
        data = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "NewAl-Code"}
        hdrs.update(headers or {})
        self._request = (path, data, hdrs)
        self._owner = threading.get_ident()
        self._send(http_pool.take(self._key))

    def _connection(self):
        from . import http_pool
        scheme, conn_host, conn_port, tunnel_host, tunnel_port = self._key
        if scheme != "https":
            return http.client.HTTPConnection(conn_host, conn_port, timeout=self._timeout)
        conn = http.client.HTTPSConnection(conn_host, conn_port, timeout=self._timeout, context=http_pool.ssl_context())
        if tunnel_host:
            conn.set_tunnel(tunnel_host, tunnel_port)
        return conn

    def _send(self, parked):
        """The request, on a parked connection or a new one. A parked connection the server has closed meanwhile can
        fail the write: the request then goes once on a new connection."""
        path, data, hdrs = self._request
        self._reused = parked is not None
        self.conn = parked or self._connection()
        try:
            if self._reused:
                self.conn.timeout = self._timeout
                self.conn.sock.settimeout(self._timeout)
            self.conn.request("POST", path, body=data, headers=hdrs)
        except (OSError, http.client.HTTPException):
            if not self._reused:
                raise
            self._discard()
            self._reused = False
            self.conn = self._connection()
            self.conn.request("POST", path, body=data, headers=hdrs)
        self.conn.sock = _Watched(self.conn.sock, self)

    def _discard(self):
        try:
            self.conn.close()
        except OSError:
            pass

    @property
    def resp(self):
        """The response (waits for its headers: for a local model, until the prompt is read). A close() from another
        thread meanwhile ends the wait with Cancelled."""
        if self._resp is None:
            try:
                self._resp = self.conn.getresponse()
            except (OSError, ValueError, http.client.HTTPException, AttributeError) as e:
                if self.closed:
                    raise Cancelled()
                # a parked connection the server closed as the request went out: nothing came back, so the request
                # goes once more on a new connection
                if not self._reused or not isinstance(e, (ConnectionError, http.client.BadStatusLine)):
                    raise
                self._discard()
                self._send(None)
                try:
                    self._resp = self.conn.getresponse()
                except (OSError, ValueError, http.client.HTTPException, AttributeError):
                    if self.closed:
                        raise Cancelled()
                    raise
        return self._resp

    def lines(self):
        while True:
            try:
                raw = self.resp.readline()
            except (OSError, ValueError, http.client.HTTPException):
                if self.closed:
                    raise Cancelled()
                raise
            if not raw:
                if self.closed:
                    raise Cancelled()
                return
            line = raw.decode("utf-8", "replace").rstrip("\\r\\n")
            if line in ("data: [DONE]", "data:[DONE]", "event: message_stop"):
                self._done = True              # the answer is complete; only the body's end can follow
            yield line

    def read_all(self):
        try:
            return self.resp.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            return ""

    def _park(self):
        """True when the response was read to its end and its connection went to http_pool. After the last event
        only the chunked body's end is left, normally received with it: it is read within half a second."""
        from . import http_pool
        resp, sock = self._resp, getattr(self.conn, "sock", None)
        if resp is None or not isinstance(sock, _Watched) or not (resp.isclosed() or self._done):
            return False
        try:
            if not resp.isclosed():
                sock.deadline = time.monotonic() + 0.5
                resp.read(65536)
            if not resp.isclosed():
                return False
        except (OSError, ValueError, http.client.HTTPException):
            return False
        conn, self.conn = self.conn, None
        self.closed = True
        conn.sock = sock._s
        http_pool.give(self._key, conn)
        return True

    def close(self):
        """Ends the request. From another thread (a cancel), the reading thread stops within 0.25 s and closes
        the connection itself: closing it here could wait on the lock that thread's read holds."""
        owner = threading.get_ident() == getattr(self, "_owner", None)
        if owner and not self.closed and self._park():
            return
        self.closed = True
        sock = getattr(self.conn, "sock", None)
        real = getattr(sock, "_s", sock)
        if real is not None:
            try:
                real.shutdown(socket.SHUT_RDWR)          # tells the server at once (it stops generating)
            except OSError:
                pass
        if owner and self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
''')
