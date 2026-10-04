import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from factory.tools import burst


class H(BaseHTTPRequestHandler):
    arrivals = []
    lock = threading.Lock()
    created = set()

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        self.rfile.read(n)
        key = self.headers.get("x-key")
        with H.lock:
            H.arrivals.append(time.monotonic())
            first = key not in H.created
            H.created.add(key)
        code = 201 if first else 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    H.arrivals.clear()
    H.created.clear()
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()
    s.server_close()


@pytest.fixture
def silent_server():
    """Accepts TCP connections (via the listen backlog) but never answers."""
    s = socket.create_server(("127.0.0.1", 0), backlog=16)
    yield f"http://127.0.0.1:{s.getsockname()[1]}"
    s.close()


def test_same_key_burst(server):
    reqs = [burst.Req("POST", server + "/x", {"x-key": "k1", "content-type": "application/json"}, b"{}")
            for _ in range(20)]
    s = burst.summarize(burst.fire(reqs))
    assert s["n"] == 20 and s["statuses"] == {"201": 1, "200": 19} and s["errors"] == []
    assert max(H.arrivals) - min(H.arrivals) < 1.0   # released together


def test_connection_refused_is_recorded():
    s = burst.summarize(burst.fire([burst.Req("GET", "http://127.0.0.1:1/", {}, None)], timeout=2))
    assert s["n"] == 1 and len(s["errors"]) == 1


def test_cli_plan(server, tmp_path, capsys):
    plan = [{"method": "POST", "url": server + "/x", "headers": {"x-key": f"k{i}"}, "body": "{}"} for i in range(5)]
    f = tmp_path / "p.json"
    f.write_text(json.dumps(plan))
    burst.main(["--plan", str(f)])
    out = json.loads(capsys.readouterr().out)
    assert out["statuses"] == {"201": 5}


# --- Review Focus #4: the probe reports, it does not judge -------------------

def test_timeout_is_recorded(silent_server):
    resps = burst.fire([burst.Req("GET", silent_server + "/", {}, None) for _ in range(3)], timeout=0.5)
    assert [r.index for r in resps] == [0, 1, 2]
    assert all(r.status is None and r.error for r in resps)
    s = burst.summarize(resps)
    assert s["n"] == 3 and len(s["errors"]) == 3 and s["statuses"] == {}
    assert s["max_ms"] is not None


def test_cli_unreachable_still_summarises_and_exits_zero(capsys):
    code = burst.main(["--url", "http://127.0.0.1:1/", "--n", "3", "--timeout", "2"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n"] == 3 and len(out["errors"]) == 3 and out["statuses"] == {}


def test_cli_timeout_still_summarises_and_exits_zero(silent_server, capsys):
    code = burst.main(["--url", silent_server + "/", "--n", "2", "--timeout", "0.5"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n"] == 2 and len(out["errors"]) == 2


def test_mixed_reachable_and_unreachable(server):
    reqs = [burst.Req("POST", server + "/x", {"x-key": "a"}, b"{}"),
            burst.Req("GET", "http://127.0.0.1:1/", {}, None)]
    s = burst.summarize(burst.fire(reqs, timeout=2))
    assert s["n"] == 2 and s["statuses"] == {"201": 1} and len(s["errors"]) == 1
    assert s["errors"][0]["index"] == 1


# --- CLI single-URL mode ------------------------------------------------------

def test_cli_single_url_replay(server, capsys):
    code = burst.main(["--url", server + "/x", "--method", "POST", "--n", "4",
                       "--header", "x-key: same", "--body", "{}"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n"] == 4 and out["statuses"] == {"201": 1, "200": 3}
    assert out["distinct_bodies"] == 1 and out["bodies"] == ['{"ok":true}']


def test_cli_body_file(server, tmp_path, capsys):
    f = tmp_path / "b.json"
    f.write_bytes(b'{"a": 1}')
    burst.main(["--url", server + "/x", "--method", "POST", "--n", "2", "--body-file", str(f)])
    out = json.loads(capsys.readouterr().out)
    assert out["n"] == 2 and out["errors"] == []


# --- summarize on synthetic input --------------------------------------------

def test_summarize_shapes():
    resps = [burst.Resp(i, 200, b"x" * 600 if i == 0 else f"b{i % 4}".encode(), float(i * 10), None)
             for i in range(10)]
    resps.append(burst.Resp(10, None, b"", 5.0, "TimeoutError()"))
    s = burst.summarize(resps)
    assert s["n"] == 11
    assert s["statuses"] == {"200": 10}
    assert s["errors"] == [{"index": 10, "error": "TimeoutError()"}]
    assert s["distinct_bodies"] == 5            # "x"*600, b1, b2, b3, b0
    assert len(s["bodies"]) == 3 and len(s["bodies"][0]) == 500
    assert s["max_ms"] == 90.0 and s["p50_ms"] is not None


def test_empty_burst():
    assert burst.fire([]) == []
    s = burst.summarize([])
    assert s["n"] == 0 and s["statuses"] == {} and s["errors"] == []
    assert s["p50_ms"] is None and s["max_ms"] is None and s["bodies"] == []
