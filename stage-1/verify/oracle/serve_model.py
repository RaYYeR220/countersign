"""Serve the reference model over HTTP so the acceptance suite can be validated against it.

    python serve_model.py --port 18400

Every request is handled under one lock (the model is a single serialised state machine).
Responses are application/json; charset=utf-8; 204 responses carry no body.
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from model import Model

LOCK = threading.Lock()
MODEL = Model()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):  # quiet
        pass

    def _dispatch(self):
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()}
        with LOCK:
            status, out = MODEL.handle(self.command, parts.path, parts.query, headers, body)
        if status == 204 or out is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = json.dumps(out).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PATCH = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18400)
    a = ap.parse_args()
    ThreadingHTTPServer.request_queue_size = 256  # bursts of 50 must not be refused by the backlog
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"oracle model listening on {a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
