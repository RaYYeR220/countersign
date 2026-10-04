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


def apply_mutant(n: int) -> None:
    import model as M
    if n == 1:
        orig = M.resolve_local

        def second_occurrence(naive, zone):
            utc = orig(naive, zone)
            if utc is None:
                return None
            alt = naive.replace(tzinfo=zone, fold=1).astimezone(M.UTC)
            return alt if alt.astimezone(zone).replace(tzinfo=None) == naive else utc
        M.resolve_local = second_occurrence
    elif n == 2:
        orig_lookup = M.Model._idem_lookup

        def lookup(self, user_id, path, key, obj):
            hit = orig_lookup(self, user_id, path, key, obj)
            return (201, hit[1]) if hit else None
        M.Model._idem_lookup = lookup
    elif n == 3:
        orig_list = M.Model.list_reservations

        def ascending(self, user_id):
            st, out = orig_list(self, user_id)
            out["reservations"].reverse()
            return st, out
        M.Model.list_reservations = ascending
    elif n == 4:
        M.Model._cutoff_passed = lambda self, rec: False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18400)
    ap.add_argument("--mutant", type=int, default=0,
                    help="deliberately deviate from the spec (self-test of the differential runner): "
                         "1 = fall-back resolves to the second occurrence, 2 = replays answer 201, "
                         "3 = GET /reservations ascending, 4 = cancel ignores the cutoff")
    a = ap.parse_args()
    if a.mutant:
        apply_mutant(a.mutant)
    ThreadingHTTPServer.request_queue_size = 256  # bursts of 50 must not be refused by the backlog
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"oracle model listening on {a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
