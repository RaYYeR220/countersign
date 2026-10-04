"""Fire a set of HTTP requests at the same instant and summarise what came back.

Two common uses:

* Concurrency burst -- many requests that compete for the same thing, released
  together, to see whether the service still gives one consistent answer under a
  race (for example exactly one "created" and the rest "conflict" or "replayed").
* Replay check -- the same request repeated with the same client-chosen key
  header, to confirm the service treats repeats as one operation: one creating
  status, identical bodies on the repeats, no duplicates.

Every request runs on its own thread and its own fresh connection. Each thread
connects first, then all of them wait on one barrier and send together, so the
requests reach the server as close to simultaneously as the machine allows.

The probe reports, it does not judge: refused connections, timeouts and other
failures are recorded per request, a summary is always printed, and the exit code
is 0 whatever the server did. Deciding what the numbers mean is the caller's job.

Usage (stdlib only):

    python burst.py --url http://127.0.0.1:8000/items --method POST --n 50 \\
        --header "Content-Type: application/json" --header "Idempotency-Key: k1" \\
        --body '{"name": "a"}'

    python burst.py --plan plan.json --timeout 10

`--plan` takes a JSON list of {"method", "url", "headers", "body"} objects that
are all released together, for bursts that mix different requests. `body` may be
a string, null, or any other JSON value (sent as its JSON text).

Output is one JSON object: n, statuses (status -> count), distinct_bodies,
errors ([{index, error}]), p50_ms, max_ms, and bodies (the first three distinct
response bodies, decoded as UTF-8, at most 500 characters each).
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

BODY_PREVIEW_CHARS = 500
BODY_PREVIEW_COUNT = 3


@dataclass
class Req:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass
class Resp:
    index: int
    status: int | None
    body: bytes
    elapsed_ms: float
    error: str | None


def _connection(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {parts.scheme!r}")
    if not parts.hostname:
        raise ValueError(f"no host in URL: {url!r}")
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port, timeout=timeout)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return conn, target


def _worker(index: int, req: Req, timeout: float, barrier: threading.Barrier,
            out: list[Resp | None]) -> None:
    conn = None
    error = None
    started = time.perf_counter()
    try:
        conn, target = _connection(req.url, timeout)
        conn.connect()
    except Exception as e:  # recorded, never raised: one bad target must not stall the rest
        error = repr(e)
    elapsed_before = (time.perf_counter() - started) * 1000.0

    try:
        barrier.wait()
    except threading.BrokenBarrierError:
        pass  # still send; the burst is merely less tight

    if error is not None:
        out[index] = Resp(index, None, b"", elapsed_before, error)
        if conn is not None:
            conn.close()
        return

    started = time.perf_counter()
    try:
        conn.request(req.method, target, body=req.body, headers=dict(req.headers))
        r = conn.getresponse()
        body = r.read()
        out[index] = Resp(index, r.status, body, (time.perf_counter() - started) * 1000.0, None)
    except Exception as e:
        out[index] = Resp(index, None, b"", (time.perf_counter() - started) * 1000.0, repr(e))
    finally:
        conn.close()


def fire(reqs: list[Req], timeout: float = 10.0) -> list[Resp]:
    """Send every request at once (one thread and one fresh connection each).

    Returns one Resp per Req, in input order. Never raises for network failures;
    they appear as Resp.error with status None.
    """
    if not reqs:
        return []
    barrier = threading.Barrier(len(reqs))
    out: list[Resp | None] = [None] * len(reqs)
    threads = [threading.Thread(target=_worker, args=(i, r, timeout, barrier, out), daemon=True)
               for i, r in enumerate(reqs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [r if r is not None else Resp(i, None, b"", 0.0, "worker produced no result")
            for i, r in enumerate(out)]


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    rank = max(1, math.ceil(q * len(sorted_values)))
    return round(sorted_values[rank - 1], 2)


def summarize(resps: list[Resp]) -> dict:
    statuses: dict[str, int] = {}
    errors = []
    distinct: list[bytes] = []
    seen: set[bytes] = set()
    for r in resps:
        if r.error is not None or r.status is None:
            errors.append({"index": r.index, "error": r.error or "no status"})
            continue
        key = str(r.status)
        statuses[key] = statuses.get(key, 0) + 1
        if r.body not in seen:
            seen.add(r.body)
            distinct.append(r.body)
    timings = sorted(r.elapsed_ms for r in resps)
    return {
        "n": len(resps),
        "statuses": statuses,
        "distinct_bodies": len(distinct),
        "errors": errors,
        "p50_ms": _percentile(timings, 0.5),
        "max_ms": round(timings[-1], 2) if timings else None,
        "bodies": [b.decode("utf-8", errors="replace")[:BODY_PREVIEW_CHARS]
                   for b in distinct[:BODY_PREVIEW_COUNT]],
    }


def _encode_body(value) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value).encode("utf-8")


def _parse_header(text: str) -> tuple[str, str]:
    name, sep, value = text.partition(":")
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError(f"header must look like 'Name: value', got {text!r}")
    return name.strip(), value.strip()


def _load_plan(path: str) -> list[Req]:
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        raise ValueError("plan must be a JSON list")
    reqs = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or "url" not in e:
            raise ValueError(f"plan entry {i} needs at least a url")
        headers = {str(k): str(v) for k, v in (e.get("headers") or {}).items()}
        reqs.append(Req(str(e.get("method", "GET")).upper(), str(e["url"]), headers,
                        _encode_body(e.get("body"))))
    return reqs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Release N HTTP requests together and summarise the answers.")
    p.add_argument("--url", help="target URL (single-request mode)")
    p.add_argument("--method", default="GET")
    p.add_argument("--n", type=int, default=50, help="copies of the request to send together")
    p.add_argument("--header", action="append", default=[], type=_parse_header,
                   help="'Name: value'; repeatable")
    body = p.add_mutually_exclusive_group()
    body.add_argument("--body", help="request body as a string")
    body.add_argument("--body-file", help="read the request body from this file (bytes as-is)")
    p.add_argument("--plan", help="JSON list of {method,url,headers,body} sent together")
    p.add_argument("--timeout", type=float, default=10.0, help="per-connection timeout, seconds")
    a = p.parse_args(argv)

    if a.plan:
        try:
            reqs = _load_plan(a.plan)
        except (OSError, ValueError) as e:
            p.error(f"cannot use plan {a.plan!r}: {e}")
    elif a.url:
        if a.n < 1:
            p.error("--n must be at least 1")
        if a.body_file:
            try:
                with open(a.body_file, "rb") as f:
                    payload = f.read()
            except OSError as e:
                p.error(f"cannot read body file {a.body_file!r}: {e}")
        else:
            payload = _encode_body(a.body)
        headers = dict(a.header)
        reqs = [Req(a.method.upper(), a.url, dict(headers), payload) for _ in range(a.n)]
    else:
        p.error("give --url or --plan")

    print(json.dumps(summarize(fire(reqs, timeout=a.timeout)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
