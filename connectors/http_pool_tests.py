"""Model requests reuse connections (http_pool.py, providers.Stream) against a real local HTTP/1.1 server."""
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import http_pool, providers


class _Model(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    connections = 0
    requests = 0
    behaviour = "normal"           # normal | close_after | drop_next | slow

    def log_message(self, *a):
        pass

    def setup(self):
        super().setup()
        type(self).connections += 1
        self.served = 0

    def do_POST(self):
        cls = type(self)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        cls.requests += 1
        self.served += 1
        if cls.behaviour == "drop_next" and self.served > 1:
            self.close_connection = True           # the server dropped the kept connection as the request arrived
            return
        if body.get("error"):
            text = b'{"error": {"message": "bad request"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        events = [{"choices": [{"index": 0, "delta": {"content": "مرحبا"}}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}, "[DONE]"]
        for ev in events:
            if cls.behaviour == "slow":
                time.sleep(0.3)
            data = ("data: %s\n\n" % (ev if isinstance(ev, str) else json.dumps(ev))).encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        if cls.behaviour == "close_after":
            self.close_connection = True


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass                                       # a cancelled client closes its socket mid-answer, on purpose


class StreamReuseTests(unittest.TestCase):
    def setUp(self):
        http_pool.clear()
        _Model.connections = _Model.requests = 0
        _Model.behaviour = "normal"
        self.server = _Server(("127.0.0.1", 0), _Model)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d/v1" % self.server.server_address[1]
        self.client = providers.OpenAICompat(self.base, "k")

    def tearDown(self):
        http_pool.clear()
        self.server.shutdown()
        self.server.server_close()

    def ask(self, **kw):
        return self.client.chat("m", [{"role": "user", "content": "hi"}], **kw)

    def test_second_request_reuses_the_connection(self):
        self.assertEqual(self.ask().content, "مرحبا")
        self.assertEqual(http_pool.parked(), 1)
        self.assertEqual(self.ask().content, "مرحبا")
        self.assertEqual((_Model.requests, _Model.connections), (2, 1))

    def test_connection_closed_by_server_is_replaced(self):
        _Model.behaviour = "close_after"
        self.assertEqual(self.ask().content, "مرحبا")
        time.sleep(0.2)                            # the server's close has reached the parked socket
        self.assertEqual(self.ask().content, "مرحبا")
        self.assertEqual((_Model.requests, _Model.connections), (2, 2))

    def test_close_racing_the_request_resends_once(self):
        _Model.behaviour = "drop_next"
        self.assertEqual(self.ask().content, "مرحبا")
        self.assertEqual(self.ask().content, "مرحبا")      # the dropped request went again on a new connection
        self.assertEqual(_Model.connections, 2)

    def test_cancelled_stream_is_not_kept(self):
        _Model.behaviour = "slow"
        cancel = threading.Event()
        threading.Timer(0.1, cancel.set).start()
        with self.assertRaises(providers.Cancelled):
            self.ask(cancel=cancel)
        self.assertEqual(http_pool.parked(), 0)

    def test_error_response_body_is_read_and_connection_kept(self):
        with self.assertRaises(providers.ProviderError) as e:
            self.client.chat("m", [{"role": "user", "content": "hi"}], extra={"error": True})
        self.assertEqual(e.exception.status, 400)
        self.assertEqual(self.ask().content, "مرحبا")
        self.assertEqual(_Model.connections, 1)

    def test_post_json_reuses_too(self):
        url = self.base + "/chat/completions"
        with self.assertRaises(providers.ProviderError):
            providers.post_json(url, {"error": True})
        with self.assertRaises(providers.ProviderError):
            providers.post_json(url, {"error": True})
        self.assertEqual(_Model.connections, 1)

    def test_one_tls_context(self):
        self.assertIs(http_pool.ssl_context(), http_pool.ssl_context())

    def test_idle_connection_expires(self):
        self.ask()
        old = http_pool.IDLE_SECONDS
        http_pool.IDLE_SECONDS = 0
        try:
            time.sleep(0.01)
            self.ask()
        finally:
            http_pool.IDLE_SECONDS = old
        self.assertEqual(_Model.connections, 2)


class ParkedSocketTests(unittest.TestCase):
    def test_closed_peer_is_not_usable(self):
        a, b = socket.socketpair()

        class Conn:
            sock = a

            def close(self):
                a.close()

        try:
            self.assertTrue(http_pool._usable(Conn()))
            b.close()
            self.assertFalse(http_pool._usable(Conn()))
        finally:
            a.close()


if __name__ == "__main__":
    unittest.main()
