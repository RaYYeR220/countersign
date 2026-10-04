"""Minimal stdlib HTTP client for the Oracle suites. One fresh connection per request."""
from __future__ import annotations

import http.client
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit


class ServerError(AssertionError):
    pass


@dataclass
class Resp:
    status: int
    headers: dict
    text: str
    json: Any = None
    raw_json_ok: bool = False

    @property
    def code(self) -> Optional[str]:
        try:
            return self.json["error"]["code"]
        except (TypeError, KeyError):
            return None

    def __repr__(self) -> str:
        return f"Resp({self.status}, {self.text[:300]!r})"


@dataclass
class Client:
    base_url: str
    timeout: float = 5.0
    statuses: list = field(default_factory=list)
    fail_on_5xx: bool = True

    def request(self, method: str, path: str, *, json_body: Any = None, raw: Optional[bytes] = None,
                headers: Optional[dict] = None, token: Optional[str] = None, key: Optional[str] = None,
                timeout: Optional[float] = None) -> Resp:
        u = urlsplit(self.base_url)
        hdrs = {}
        body = None
        if raw is not None:
            body = raw
            hdrs["Content-Type"] = "application/json; charset=utf-8"
        elif json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json; charset=utf-8"
        if token is not None:
            hdrs["Authorization"] = f"Bearer {token}"
        if key is not None:
            hdrs["Idempotency-Key"] = key
        if headers:
            hdrs.update(headers)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout or self.timeout)
        try:
            conn.request(method, (u.path.rstrip("/") + path) if u.path else path, body=body, headers=hdrs)
            r = conn.getresponse()
            data = r.read()
            resp = Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, data.decode("utf-8", "replace"))
        finally:
            conn.close()
        try:
            resp.json = json.loads(resp.text) if resp.text else None
            resp.raw_json_ok = True
        except ValueError:
            resp.json = None
        self.statuses.append(resp.status)
        if self.fail_on_5xx and resp.status >= 500:
            raise ServerError(f"{method} {path} -> {resp.status}: {resp.text[:300]}")
        return resp

    # convenience ----------------------------------------------------------
    def get(self, path: str, **kw) -> Resp:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw) -> Resp:
        return self.request("POST", path, json_body=body, **kw)

    def patch(self, path: str, body: Any = None, **kw) -> Resp:
        return self.request("PATCH", path, json_body=body, **kw)

    def reset(self, fixture: dict) -> Resp:
        return self.request("POST", "/_test/reset", json_body=fixture, timeout=10)

    def export(self) -> Resp:
        return self.request("GET", "/_test/export", timeout=10)

    def import_(self, doc: Any) -> Resp:
        return self.request("POST", "/_test/import", json_body=doc, timeout=10)

    def login(self, email: str, password: str) -> str:
        r = self.post("/auth/login", {"email": email, "password": password})
        assert r.status == 200, r
        return r.json["token"]

    def signup(self, email: str, password: str = "long enough password", display_name: str = "X") -> Resp:
        return self.post("/auth/signup", {"email": email, "password": password, "display_name": display_name})

    def book(self, token: str, key: str, restaurant_id: str, table_id: str, starts_at_local: str,
             party_size: Any = 2, **extra) -> Resp:
        body = {"restaurant_id": restaurant_id, "table_id": table_id, "starts_at_local": starts_at_local,
                "party_size": party_size, **extra}
        return self.post("/reservations", body, token=token, key=key)

    def availability(self, restaurant_id: str, date: str, party_size: Any, extra: str = "") -> Resp:
        return self.get(f"/availability?restaurant_id={restaurant_id}&date={date}&party_size={party_size}{extra}")


def burst(fn_list, timeout: float = 30.0):
    """Run each callable on its own thread, released together by a barrier. Returns results in order."""
    n = len(fn_list)
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def run(i, fn):
        try:
            barrier.wait(timeout)
            results[i] = fn()
        except BaseException as e:  # noqa: BLE001 - report, do not hide
            results[i] = e

    threads = [threading.Thread(target=run, args=(i, fn), daemon=True) for i, fn in enumerate(fn_list)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    return results
