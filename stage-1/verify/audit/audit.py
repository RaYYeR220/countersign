#!/usr/bin/env python3
"""Auditor attack battery for Tablekeeper stage 1 (written from the specification only).

Stdlib only. Runs inside a runner container on the same internal Docker network as the
candidate, because Docker Desktop does not forward ports of internal networks.

    python audit.py --base http://auditor-s1-a:8080 [--dest http://auditor-s1-b:8080] \
        [--groups core,auth,...] [--rounds 3] [--out /out/attacks.json]

Every check records: group, name, spec section, request, expected, actual. A "soft" check
covers a reading the specification leaves open; it is reported but does not fail the run.
Exit code: 0 when every hard check passed, 1 otherwise, 2 on harness error.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve()
sys.path.insert(0, os.environ.get("BURST_DIR") or str(HERE.parents[3] / "factory" / "tools"))
import burst  # noqa: E402  (factory/tools/burst.py)

NO_BODY = object()
REF_RE = re.compile(r"^[A-Z0-9]{6,12}$")
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$")
RFC3339_OFFSET_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# --------------------------------------------------------------------------- recording

class Checker:
    def __init__(self):
        self.results: list[dict] = []
        self.group = "?"

    def check(self, name, ok, expected=None, actual=None, req=None, section=None, soft=False):
        clause = clause_for(self.group, name, section)
        r = {"group": self.group, "name": name, "ok": bool(ok), "soft": soft, "section": section, "clause": clause}
        if not ok:
            r.update(expected=expected, actual=actual, request=req)
        self.results.append(r)
        if not ok:
            tag = "SOFT" if soft else "FAIL"
            print(f"  [{tag}] {self.group}/{name} ({clause}) expected={_short(expected)} "
                  f"actual={_short(actual)} req={_short(req, 300)}", flush=True)
        return bool(ok)

    def expect(self, name, resp, status, code=None, section=None, soft=False):
        statuses = status if isinstance(status, (tuple, list, set)) else (status,)
        ok = resp.status in statuses and (code is None or resp.code() == code)
        return self.check(name, ok, {"status": list(statuses), "code": code},
                          {"status": resp.status, "body": resp.text(300)}, resp.req, section, soft)


# Master-ledger clause ids (evidence/stage-1/ledger.md @ 564f21e) and rulings, by group and check name.
# First matching pattern wins; a check whose section is already a "C1." id keeps it.
CROSS_RULES = [(r"^no-5xx", "C1.46"), (r"^per-request-timeout", "C1.8"), (r"^error-envelope", "C1.32"),
               (r"^content-type-json", "C1.14"), (r"group ran to completion", "harness"), (r"^setup", "harness")]
CLAUSE_RULES = {
    "core": [(r"^health", "C1.11"), (r"R-8|R-20|reset rejects|reset accepts", "C1.12,C1.18,C1.26 (R-8,R-20)"),
             (r"reset", "C1.12,C1.13"), (r"405|unknown path", "C1.32,C1.38 (R-9)"),
             (r"Content-Type", "C1.14 (R-17)"), (r"ignores Authorization", "C1.53,C1.68 (R-10)"),
             (r"GET /restaurants public", "C1.68,C1.69 (R-11)"), (r"restaurants/\{id\}|restaurants/unknown", "C1.70"),
             (r"65 chars", "C1.18"), (r"seeded .*created_at|trusted", "C1.30 (R-16)"), (r"seeded reservation visible", "C1.30"),
             (r"hidden", "C1.90"), (r"seeded user", "C1.29"), (r"unknown query", "C1.17"), (r"unknown body", "C1.16")],
    "auth": [(r"wrong type|unparseable|JSON array|R-19", "C1.34 (R-19)"), (r"missing", "C1.40 (R-19)"),
             (r"signup 201", "C1.47"), (r"list is", "C1.89"), (r"login 200", "C1.48"), (r"token", "C1.54"),
             (r"taken|case-insensitive", "C1.49 (R-4)"), (r"password", "C1.50 (R-4)"), (r"display_name", "C1.47 (R-4)"),
             (r"email", "C1.51 (R-4)"), (r"login wrong|login unknown", "C1.52"), (r"401", "C1.36,C1.53"), (r"public", "C1.68")],
    "availability": [(r"envelope", "C1.72"), (r"slots = every|fri closes", "C1.74"), (r"offset", "C1.79,C1.104"),
                     (r"half-open", "C1.4,C1.75"), (r"party \d+ ->", "C1.75,C1.76"), (r"closed day", "C1.77"),
                     (r"missing", "C1.71"), (r"date=", "C1.41,C1.71 (R-12)"), (r"party_size=", "C1.43 (R-12)"),
                     (r"huge", "C1.46,C1.71"), (r"before 404|unknown restaurant", "C1.71 (R-12)")],
    "create": [(r"^R-7", "C1.83,C1.84,C1.85,C1.87,C1.88 (R-7)"), (r"JSON string", "C1.34 (R-1)"), (r"created_at", "C1.15,C1.80 (R-21)"), (r"starts_at/ends_at offset", "C1.15 (R-21)"),
               (r"create 201|create response", "C1.80"), (r"GET /reservations/\{ref\}", "C1.90,C1.80"),
               (r"integral", "C1.86 (R-3)"), (r"overlap|back-to-back 20:30", "C1.4,C1.82"), (r"off grid", "C1.83"),
               (r"outside_opening_hours|ends exactly", "C1.84 (R-7)"), (r"party_exceeds", "C1.85"),
               (r"party_size=", "C1.42,C1.86 (R-2,R-3)"), (r"starts_at_local=.*422", "C1.42"),
               (r"R-19|number 400|null|wrong type", "C1.34 (R-2,R-19)"), (r"missing", "C1.40 (R-19)"),
               (r"unknown restaurant|unknown table|another restaurant", "C1.88"),
               (r"Idempotency-Key|R-1", "C1.35,C1.45,C1.59 (R-1)"), (r"unparseable|array", "C1.34 (R-1)"),
               (r"past start|inside cutoff", "C1.31"), (r"references unique", "C1.81")],
    "reads": [(r"404", "C1.90"), (r"tie", "C1.89 (R-11)"), (r".", "C1.89")],
    "cancel": [(r"body", "C1.91 (R-13)"), (r"twice", "C1.93"), (r"cutoff|past|still confirmed", "C1.94 (R-6)"),
               (r"other's|unknown", "C1.95"), (r"frees|rebook|not offered", "C1.92"), (r".", "C1.91")],
    "patch": [(r"own overlapping|released", "C1.98 (R-14)"), (r"keeps reference", "C1.99"), (r"ends_at", "C1.22"),
              (r"table only", "C1.96"), (r"onto other's booking", "C1.82,C1.98"),
              (r"within cutoff|past booking|far booking", "C1.97 (R-6,R-14)"), (r"cancelled", "C1.97 (R-14)"),
              (r"unchanged|untouched", "C1.98,C1.5"), (r"\{\}", "C1.96 (R-14)"), (r"unparseable|null|number|JSON array", "C1.34 (R-2,R-14)"),
              (r"other's 404|unknown 404", "C1.132,C1.38"), (r"nonexistent", "C1.87,C1.97 (R-7)"), (r".", "C1.97 (R-14)")],
    "dst": [(r"R-23|end-of-day", "C1.74,C1.84 (R-23)"), (r"slots once", "C1.101,C1.102 (R-5)"),
            (r"offset", "C1.104"), (r"nonexistent", "C1.87,C1.101"), (r"absolute duration", "C1.103"),
            (r"availability 02:00", "C1.103,C1.75"), (r".", "C1.103,C1.4")],
    "idem": [(r"first use 201", "C1.61"), (r"integral|2\.0", "C1.65 (R-3)"), (r"replay 200|reordered", "C1.62,C1.65"),
             (r"created nothing", "C1.5"), (r"different body|different invalid|body \{\}", "C1.63,C1.59 (R-1)"),
             (r"unparseable|no token|R-1", "C1.59 (R-1)"), (r"other user", "C1.57"), (r"not a replay", "C1.58 (R-18)"),
             (r"after PATCH|after cancel", "C1.67"), (r"255", "C1.45"), (r".", "C1.64")],
    "moves": [(r"swap two|chain", "C1.119"), (r"keeps identity", "C1.116"), (r"replay", "C1.122"),
              (r"same key", "C1.63,C1.59"), (r"colliding|overlap among", "C1.119"),
              (r"changed nothing|kept old|unchanged after", "C1.120"), (r"reusable", "C1.120,C1.64"),
              (r"no-op item", "C1.117 (R-22)"), (r"no-op", "C1.123"), (r"input order", "C1.118 (R-22)"),
              (r"^item ", "C1.34,C1.116 (R-22b)"), (r"8 moves|structure|moves (empty|missing|9|duplicate|item|not array|object|reference number)", "C1.114 (R-22)"),
              (r"unknown reference|other owner", "C1.115 (R-22)"), (r"across restaurants", "C1.115 (R-22)"),
              (r"cutoff", "C1.117,C1.118 (R-22)"), (r"input order", "C1.118 (R-22)"),
              (r"wrong-type|null", "C1.34,C1.116 (R-22)"), (r"capacity|outside hours", "C1.116,C1.118"),
              (r"cancelled", "C1.117"), (r"no token", "C1.115,C1.36"), (r"no key|key 256", "C1.35,C1.45"),
              (r"unparseable", "C1.34"), (r"independent", "C1.58")],
    "burst": [(r"B1 ", "C1.3"), (r"B2 ", "C1.3"), (r"B3 ", "C1.66"), (r"B4 ", "C1.66,C1.63"), (r"B5 ", "C1.66,C1.122"),
              (r"B6 ", "C1.3,C1.98"), (r"B7 ", "C1.120,C1.3"), (r"B8 ", "C1.49"), (r"B9 ", "C1.46"), (r"B10 ", "C1.8"),
              (r"references unique", "C1.81"), (r".", "C1.3")],
    "export": [(r"setup", "harness"), (r"export 200", "C1.106"), (r"plaintext", "C1.55"), (r"reflects", "C1.110"),
               (r"state \{\}|state garbage", "C1.109 (R-24)"), (r"import .* -> |destination unchanged|import unparseable", "C1.109"),
               (r"import export 204", "C1.107"), (r"previous destination|only existed", "C1.112"),
               (r"batch replay", "C1.124"), (r"token|login works|config|create replay|still 409|failed key", "C1.111"),
               (r"collide", "C1.112"), (r"occupancy", "C1.107"), (r"repeated import", "C1.108"),
               (r"reset after import", "C1.112"), (r"under load|concurrent exports", "C1.110")],
}


def clause_for(group, name, section):
    if isinstance(section, str) and section.startswith("C1."):
        return section
    for pat, ids in CROSS_RULES + CLAUSE_RULES.get(group, []):
        if re.search(pat, name):
            return ids
    return section or "unmapped"


def _short(v, n=200):
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[:n] + "..."


# --------------------------------------------------------------------------- http

class Resp:
    def __init__(self, status, headers, raw, req, elapsed_ms):
        self.status, self.headers, self.raw, self.req, self.elapsed_ms = status, headers, raw, req, elapsed_ms
        try:
            self.json = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            self.json = None

    def code(self):
        try:
            return self.json["error"]["code"]
        except Exception:
            return None

    def text(self, n=2000):
        return self.raw.decode("utf-8", errors="replace")[:n]


class Client:
    def __init__(self, base: str, chk: Checker, label: str = "A"):
        self.base = base.rstrip("/")
        self.chk = chk
        self.label = label
        p = urlsplit(self.base)
        self.host, self.port = p.hostname, p.port or 80

    def req(self, method, path, body=NO_BODY, token=None, key=None, headers=None, raw=None,
            query=None, timeout=15.0) -> Resp:
        h = {"Accept": "application/json"}
        data = None
        if raw is not None:
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
            h["Content-Type"] = "application/json; charset=utf-8"
        elif body is not NO_BODY:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json; charset=utf-8"
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        if key is not None:
            h["Idempotency-Key"] = key
        h.update(headers or {})
        if query is not None:
            path = path + "?" + (query if isinstance(query, str) else urlencode(query))
        shown = {k: ("<token>" if k == "Authorization" else v) for k, v in h.items() if k != "Accept"}
        reqrepr = {"server": self.label, "method": method, "path": path, "headers": shown,
                   "body": (data or b"").decode("utf-8", errors="replace")[:600]}
        started = time.perf_counter()
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            raw_body = r.read()
            resp = Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, raw_body, reqrepr,
                        (time.perf_counter() - started) * 1000)
        except Exception as e:
            resp = Resp(599, {}, repr(e).encode(), reqrepr, (time.perf_counter() - started) * 1000)
        finally:
            conn.close()
        self._contract(resp)
        return resp

    def _contract(self, r: Resp):
        """Cross-cutting rules applied to every response (§2 timeout, §3.4, §5)."""
        c = self.chk
        if r.status >= 500:
            c.check("no-5xx", False, "status < 500 (§5: requests must not produce 5xx)",
                    {"status": r.status, "body": r.text(300)}, r.req, "§5")
        limit = 10000 if r.req["path"].startswith("/_test/") else 5000
        if r.elapsed_ms > limit:
            c.check("per-request-timeout", False, f"<= {limit} ms", round(r.elapsed_ms), r.req, "§2")
        if 400 <= r.status < 599:
            err = r.json.get("error") if isinstance(r.json, dict) else None
            ok = isinstance(err, dict) and isinstance(err.get("code"), str) and isinstance(err.get("message"), str)
            if not ok:
                c.check("error-envelope", False, '{"error":{"code":str,"message":str}}', r.text(300), r.req, "§5")
        if r.status != 204 and r.status < 599 and r.raw:
            ct = r.headers.get("content-type", "")
            if "application/json" not in ct:
                c.check("content-type-json", False, "application/json; charset=utf-8", ct, r.req, "§3.4")


# --------------------------------------------------------------------------- time helpers

def pick_daytime_zone(now_utc: datetime) -> str:
    """A zone without DST in which local time is between 06:00 and 17:59 right now."""
    for z in ("UTC", "Asia/Tokyo", "America/Phoenix", "Asia/Kolkata", "Pacific/Honolulu", "Asia/Shanghai",
              "America/Bogota", "Asia/Dubai"):
        if 6 <= now_utc.astimezone(ZoneInfo(z)).hour <= 17:
            return z
    return "UTC"


def ceil_grid(dt: datetime, minutes: int) -> datetime:
    dt = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    m = dt.hour * 60 + dt.minute
    up = (-m) % minutes
    return dt + timedelta(minutes=up)


def loc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def resolve(local: str, tz: str) -> datetime:
    """First occurrence (fold=0) as the specification requires."""
    return datetime.strptime(local, "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(tz), fold=0)


def rfc(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse_rfc(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def same_instant_and_text(actual, dt: datetime) -> bool:
    """Equal instant and equal local wall clock + offset as written (e.g. +02:00)."""
    a = parse_rfc(actual) if isinstance(actual, str) else None
    # compare in UTC: PEP 495 makes inter-zone comparisons of fold/gap times unequal
    return a is not None and a.astimezone(timezone.utc) == dt.astimezone(timezone.utc) and a.utcoffset() == dt.utcoffset() and \
        a.replace(tzinfo=None) == dt.replace(tzinfo=None)


def next_weekday(d: date, wd: int) -> date:
    return d + timedelta(days=(wd - d.weekday()) % 7)


# --------------------------------------------------------------------------- context / fixture

class Ctx:
    def __init__(self):
        self.now = datetime.now(timezone.utc)
        today = self.now.date()
        self.thu = next_weekday(today + timedelta(days=21), 3)      # bookable, far beyond cutoff
        self.fri = self.thu + timedelta(days=1)
        self.wed = self.thu - timedelta(days=1)                      # closed
        self.thu2 = self.thu + timedelta(days=7)                     # seeded bookings
        self.now_tz = pick_daytime_zone(self.now)
        nl = self.now.astimezone(ZoneInfo(self.now_tz))
        g = ceil_grid(nl, 15)
        self.near = loc(g + timedelta(minutes=15))     # starts in 16..30 min: inside the 120-min cutoff
        self.near2 = loc(g + timedelta(minutes=45))
        self.far = loc(g + timedelta(minutes=240))     # outside the cutoff
        self.far2 = loc(g + timedelta(minutes=300))
        self.past = loc((nl - timedelta(days=1)).replace(hour=12, minute=0))

    def D(self, d: date, hhmm: str) -> str:
        return f"{d.isoformat()}T{hhmm}"


SEED_CREATED_AT = "2026-01-02T03:04:05+00:00"
ADA = {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}
BOB = {"id": "u_bob", "email": "bob@example.com", "password": "battery staple", "display_name": "Bob"}

ANKER_HOURS = [{"weekday": "thu", "opens": "18:00", "closes": "23:00"},
               {"weekday": "fri", "opens": "18:00", "closes": "23:30"}]
ANKER_TABLES = [{"id": "t_1", "label": "1", "capacity": 2},
                {"id": "t_2", "label": "2", "capacity": 4},
                {"id": "t_3", "label": "3", "capacity": 6}]


def fixture(ctx: Ctx, extra_users=()) -> dict:
    all_days = [{"weekday": w, "opens": "00:00", "closes": "23:45"} for w in WEEKDAYS]
    sunday_night = [{"weekday": "sun", "opens": "00:00", "closes": "06:00"}]
    seeded = []
    n = 0
    for t in ("t_1", "t_2", "t_3"):
        for hhmm in ("18:00", "19:30", "21:00"):
            n += 1
            seeded.append({"id": f"s_{n}", "reference": f"SEED0{n}", "user_id": "u_ada", "restaurant_id": "r_anker",
                           "table_id": t, "starts_at_local": ctx.D(ctx.thu2, hhmm), "party_size": 2})
    seeded[0]["created_at"] = SEED_CREATED_AT
    seeded.append({"id": "s_b1", "reference": "SEEDB1", "user_id": "u_bob", "restaurant_id": "r_anker",
                   "table_id": "t_3", "starts_at_local": ctx.D(ctx.fri, "22:00"), "party_size": 5})
    return {
        "users": [ADA, BOB, *extra_users],
        "restaurants": [
            {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
             "opening_hours": ANKER_HOURS, "tables": ANKER_TABLES},
            {"id": "r_now", "name": "Now Diner", "timezone": ctx.now_tz, "slot_minutes": 15,
             "reservation_duration_minutes": 30, "cancellation_cutoff_minutes": 120,
             "opening_hours": all_days,
             "tables": [{"id": "n_1", "label": "N1", "capacity": 4}, {"id": "n_2", "label": "N2", "capacity": 4}]},
            {"id": "r_ber", "name": "Berlin Nights", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
             "opening_hours": sunday_night,
             "tables": [{"id": "d_1", "label": "D1", "capacity": 4}, {"id": "d_2", "label": "D2", "capacity": 4}]},
            {"id": "r_ny", "name": "NY Nights", "timezone": "America/New_York", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
             "opening_hours": sunday_night,
             "tables": [{"id": "y_1", "label": "Y1", "capacity": 4}, {"id": "y_2", "label": "Y2", "capacity": 4}]},
            {"id": "r_other", "name": "Other Place", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
             "opening_hours": ANKER_HOURS, "tables": [{"id": "o_1", "label": "O1", "capacity": 8}]},
            {"id": "r_close", "name": "Early Close", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
             "opening_hours": [{"weekday": "sun", "opens": "00:00", "closes": "03:30"}],
             "tables": [{"id": "c_1", "label": "C1", "capacity": 4}]},
        ],
        "reservations": seeded,
    }


# --------------------------------------------------------------------------- session helpers

class S:
    """Per-group session: reset, log in seeded users, create bookings."""

    def __init__(self, c: Client, chk: Checker, ctx: Ctx):
        self.c, self.chk, self.ctx = c, chk, ctx

    def reset(self, fx=None):
        r = self.c.req("POST", "/_test/reset", fx or fixture(self.ctx), timeout=12)
        if r.status != 204:
            raise RuntimeError(f"reset failed: {r.status} {r.text(300)}")
        self.ada = self.login(ADA)
        self.bob = self.login(BOB)

    def login(self, u):
        r = self.c.req("POST", "/auth/login", {"email": u["email"], "password": u["password"]})
        if r.status != 200 or not isinstance(r.json, dict) or "token" not in r.json:
            raise RuntimeError(f"login {u['email']} failed: {r.status} {r.text(300)}")
        return r.json["token"]

    def signup(self, email=None, password="correct horse battery", name="Tester"):
        email = email or f"u{uuid.uuid4().hex[:10]}@example.com"
        r = self.c.req("POST", "/auth/signup", {"email": email, "password": password, "display_name": name})
        if r.status != 201:
            raise RuntimeError(f"signup failed: {r.status} {r.text(300)}")
        return r.json["token"]

    def book(self, token, rid, tid, start, party=2, key=None, expect=201):
        body = {"restaurant_id": rid, "table_id": tid, "starts_at_local": start, "party_size": party}
        r = self.c.req("POST", "/reservations", body, token=token, key=key or uuid.uuid4().hex)
        if expect is not None and r.status != expect:
            raise RuntimeError(f"setup booking {body} gave {r.status} {r.text(300)}")
        return r.json

    def get(self, token, ref):
        return self.c.req("GET", f"/reservations/{ref}", token=token)

    def mine(self, token):
        r = self.c.req("GET", "/reservations", token=token)
        return r.json.get("reservations", []) if r.status == 200 and isinstance(r.json, dict) else []

    def avail(self, rid, d, party):
        return self.c.req("GET", "/availability", query={"restaurant_id": rid, "date": str(d), "party_size": party})

    def avail_tables(self, rid, d, party, hhmm):
        r = self.avail(rid, d, party)
        for s in (r.json or {}).get("slots", []) if r.status == 200 else []:
            if s.get("starts_at_local") == f"{d}T{hhmm}":
                return s.get("available_table_ids")
        return None


def check_invariant(s: S, tokens: list[str], label: str, section="§1"):
    """No two confirmed reservations on one table overlap; references are unique."""
    seen, rows = {}, []
    for t in tokens:
        for r in s.mine(t):
            ref = r.get("reference")
            if ref in seen:
                continue
            seen[ref] = r
            rows.append(r)
    refs = [r.get("reference") for r in rows]
    s.chk.check(f"{label}: references unique", len(refs) == len(set(refs)), "unique", refs, None, "§8")
    by_table = {}
    for r in rows:
        if r.get("status") != "confirmed":
            continue
        a, b = parse_rfc(r.get("starts_at")), parse_rfc(r.get("ends_at"))
        if a is None or b is None:
            continue
        by_table.setdefault((r.get("restaurant_id"), r.get("table_id")), []).append((a, b, r.get("reference")))
    clashes = []
    for k, iv in by_table.items():
        iv.sort()
        for (a1, b1, r1), (a2, b2, r2) in zip(iv, iv[1:]):
            if a2 < b1:
                clashes.append([k, r1, r2])
    s.chk.check(f"{label}: no overlapping confirmed bookings", not clashes, [], clashes, None, section)
    return rows


# --------------------------------------------------------------------------- groups

def g_core(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    r = c.req("GET", "/health")
    chk.check("health 200 {status: ok}", r.status == 200 and r.json == {"status": "ok"},
              {"status": 200, "body": {"status": "ok"}}, {"status": r.status, "body": r.text(200)}, r.req, "§3.2")
    r = c.req("POST", "/_test/reset", fixture(ctx), timeout=12)
    chk.expect("reset 204", r, 204, section="§3.3")
    chk.check("reset 204 has empty body", r.raw == b"", b"", r.text(100), r.req, "§3.3")
    s.reset()
    r = c.req("POST", "/_test/reset", fixture(ctx), timeout=12)
    chk.expect("repeated reset 204", r, 204, section="§3.3")
    s.ada, s.bob = s.login(ADA), s.login(BOB)

    r = c.req("GET", "/restaurants")
    exp = [{"id": x["id"], "name": x["name"], "timezone": x["timezone"]} for x in fixture(ctx)["restaurants"]]
    got = r.json.get("restaurants") if r.status == 200 and isinstance(r.json, dict) else None
    ok = isinstance(got, list) and sorted([{k: g.get(k) for k in ("id", "name", "timezone")} for g in got],
                                          key=lambda x: x["id"]) == sorted(exp, key=lambda x: x["id"])
    chk.check("GET /restaurants public, lists fixture restaurants", ok, exp, r.text(600), r.req, "§8")
    r = c.req("GET", "/restaurants/r_anker")
    j = r.json if isinstance(r.json, dict) else {}
    ok = r.status == 200 and j.get("id") == "r_anker" and j.get("slot_minutes") == 30 and \
        j.get("reservation_duration_minutes") == 90 and j.get("cancellation_cutoff_minutes") == 120 and \
        [{k: h.get(k) for k in ("weekday", "opens", "closes")} for h in j.get("opening_hours") or []] == ANKER_HOURS and \
        [{k: t.get(k) for k in ("id", "label", "capacity")} for t in j.get("tables") or []] == ANKER_TABLES
    chk.check("GET /restaurants/{id} fixture shape, public", ok, "fixture config", r.text(800), r.req, "§8")
    chk.expect("GET /restaurants/unknown 404", c.req("GET", "/restaurants/nope"), 404, "not_found", "§8")
    chk.expect("GET /restaurants/<65 chars> 404", c.req("GET", "/restaurants/" + "x" * 65), 404, "not_found", "§3.4")

    # seeded bookings
    r = s.get(s.ada, "SEED01")
    j = r.json if isinstance(r.json, dict) else {}
    st = resolve(ctx.D(ctx.thu2, "18:00"), "Europe/Berlin")
    ok = r.status == 200 and j.get("reference") == "SEED01" and j.get("reservation_id") == "s_1" and \
        j.get("status") == "confirmed" and same_instant_and_text(j.get("starts_at"), st) and \
        same_instant_and_text(j.get("ends_at"), st + timedelta(minutes=90)) and j.get("party_size") == 2
    chk.check("seeded reservation visible to owner with derived times", ok, "SEED01 confirmed", r.text(600), r.req, "§4")
    chk.expect("seeded reservation hidden from other user", s.get(s.bob, "SEED01"), 404, "not_found", "§8")
    chk.check("seeded user logs in with fixture password", True, section="§4")

    # unknown query params & unknown body fields
    r = c.req("GET", "/availability", query={"restaurant_id": "r_anker", "date": str(ctx.thu), "party_size": 2, "zzz": "1"})
    chk.expect("unknown query parameter ignored", r, 200, section="§3.4")
    r = c.req("POST", "/reservations", {"restaurant_id": "r_anker", "table_id": "t_1", "party_size": 2,
                                         "starts_at_local": ctx.D(ctx.thu, "18:00"), "zzz": {"a": [1]}},
              token=s.ada, key=uuid.uuid4().hex)
    chk.expect("unknown body field ignored", r, 201, section="§3.4")
    r = c.req("GET", "/restaurants")
    got = [g.get("id") for g in (r.json or {}).get("restaurants", [])] if r.status == 200 else None
    chk.check("GET /restaurants public, in fixture order", got == [x["id"] for x in exp], [x["id"] for x in exp], got, r.req)
    r = s.get(s.ada, "SEED01")
    chk.check("seeded created_at kept from fixture", (r.json or {}).get("created_at") == SEED_CREATED_AT, SEED_CREATED_AT,
              (r.json or {}).get("created_at"), r.req)

    # R-9 routing, R-10 public endpoints ignore Authorization, R-17 Content-Type not enforced
    chk.expect("unknown path 404", c.req("GET", "/nope/zzz"), 404, "not_found")
    chk.expect("known path, wrong method 405", c.req("DELETE", "/restaurants"), 405, "method_not_allowed")
    chk.expect("known path, wrong method 405 (PUT /reservations)", c.req("PUT", "/reservations", {}, token=s.ada), 405, "method_not_allowed")
    junk = {"Authorization": "Bearer junk-token"}
    for m, p, b, st in (("GET", "/health", NO_BODY, 200), ("GET", "/restaurants", NO_BODY, 200), ("GET", "/restaurants/r_anker", NO_BODY, 200),
                        ("GET", "/availability?" + urlencode({"restaurant_id": "r_anker", "date": str(ctx.thu), "party_size": 2}), NO_BODY, 200),
                        ("POST", "/auth/login", {"email": ADA["email"], "password": ADA["password"]}, 200),
                        ("GET", "/_test/export", NO_BODY, 200)):
        chk.expect(f"{m} {p.split('?')[0]} ignores Authorization: Bearer junk", c.req(m, p, b, headers=junk), st)
    r = c.req("POST", "/reservations", headers={"Content-Type": "text/plain"}, token=s.ada, key=uuid.uuid4().hex,
              raw=json.dumps(booking_body("r_anker", "t_3", ctx.D(ctx.thu, "18:00"), 2)))
    chk.expect("JSON body with Content-Type text/plain accepted (R-17)", r, 201)

    # R-8 / R-20: refused fixtures change nothing
    base_fx = fixture(ctx)

    def variant(fn):
        fx = json.loads(json.dumps(base_fx))
        fn(fx)
        return fx

    R0 = lambda fx: fx["restaurants"][0]  # noqa: E731
    bad_fixtures = [
        ("unparseable", None, 400),
        ("restaurant id 65 chars", variant(lambda fx: R0(fx).update(id="r" * 65)), 422),
        ("user id 65 chars", variant(lambda fx: fx["users"][0].update(id="u" * 65)), 422),
        ("unknown timezone", variant(lambda fx: R0(fx).update(timezone="Mars/Olympus")), 422),
        ("opens not HH:MM", variant(lambda fx: R0(fx)["opening_hours"][0].update(opens="6pm")), 422),
        ("weekday not mon..sun", variant(lambda fx: R0(fx)["opening_hours"][0].update(weekday="thursday")), 422),
        ("closes not later than opens", variant(lambda fx: R0(fx)["opening_hours"][0].update(closes="17:00")), 422),
        ("duplicate weekday", variant(lambda fx: R0(fx)["opening_hours"].append({"weekday": "thu", "opens": "11:00", "closes": "14:00"})), 422),
        ("duplicate user id", variant(lambda fx: fx["users"].append({**fx["users"][0], "email": "x@example.com"})), 422),
        ("duplicate email (case-insensitive)", variant(lambda fx: fx["users"].append({**fx["users"][0], "id": "u_x", "email": "ADA@example.com"})), 422),
        ("duplicate restaurant id", variant(lambda fx: fx["restaurants"].append({**fx["restaurants"][1], "id": "r_anker"})), 422),
        ("duplicate table id in a restaurant", variant(lambda fx: R0(fx)["tables"].append({"id": "t_1", "label": "x", "capacity": 2})), 422),
        ("duplicate reservation id", variant(lambda fx: fx["reservations"].append({**fx["reservations"][0], "reference": "SEEDXX"})), 422),
        ("duplicate reference", variant(lambda fx: fx["reservations"].append({**fx["reservations"][0], "id": "s_x"})), 422),
        ("reservation of unknown user", variant(lambda fx: fx["reservations"][0].update(user_id="u_ghost")), 422),
        ("reservation of unknown restaurant", variant(lambda fx: fx["reservations"][0].update(restaurant_id="r_ghost")), 422),
        ("reservation on another restaurant's table", variant(lambda fx: fx["reservations"][0].update(table_id="o_1")), 422),
        ("capacity wrong type", variant(lambda fx: R0(fx)["tables"][0].update(capacity="2")), 400),
        ("restaurants not a list", variant(lambda fx: fx.update(restaurants={"r": 1})), 400),
        ("restaurant missing timezone", variant(lambda fx: R0(fx).pop("timezone")), 422),
    ]
    for name, fx, st in bad_fixtures:
        s.reset()
        before = c.req("GET", "/_test/export").json
        r =c.req("POST", "/_test/reset", fx, timeout=12) if fx is not None else c.req("POST", "/_test/reset", raw='{"users": [', timeout=12)
        chk.expect(f"reset rejects fixture: {name} -> {st}", r, st, "malformed_request" if st == 400 else "validation_failed")
        after = c.req("GET", "/_test/export").json
        chk.check(f"reset rejects fixture: {name}: state unchanged", after == before, "unchanged", "changed", r.req)
    ok_fx = variant(lambda fx: (R0(fx)["opening_hours"][0].update(closes="24:00"),
                                fx["reservations"].append({"id": "s_t", "reference": "SEEDT1", "user_id": "u_bob", "restaurant_id": "r_anker",
                                                           "table_id": "t_1", "starts_at_local": ctx.D(ctx.thu2, "18:10"), "party_size": 2})))
    r = c.req("POST", "/_test/reset", ok_fx, timeout=12)
    chk.expect("reset accepts closes 24:00 and an off-grid/overlapping seeded booking (seeds trusted)", r, 204)
    s.reset()


def g_auth(s: S):
    c, chk = s.c, s.chk
    s.reset()
    email = f"new{uuid.uuid4().hex[:8]}@example.com"
    r = c.req("POST", "/auth/signup", {"email": email, "password": "correct horse", "display_name": "Neo"})
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("signup 201 {user_id, display_name, token}", r.status == 201 and isinstance(j.get("user_id"), str)
              and j.get("display_name") == "Neo" and isinstance(j.get("token"), str) and j.get("token"),
              "201 with fields", r.text(300), r.req, "§6")
    uid, t1 = j.get("user_id"), j.get("token")
    r = c.req("GET", "/reservations", token=t1)
    chk.check("new user's list is {reservations: []}", r.status == 200 and r.json == {"reservations": []},
              {"reservations": []}, r.text(200), r.req, "§8")
    r = c.req("POST", "/auth/login", {"email": email, "password": "correct horse"})
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("login 200 same user_id", r.status == 200 and j.get("user_id") == uid and j.get("display_name") == "Neo"
              and isinstance(j.get("token"), str), {"user_id": uid}, r.text(300), r.req, "§6")
    t2 = j.get("token")
    chk.expect("first token still valid after second login", c.req("GET", "/reservations", token=t1), 200, section="§6")
    chk.expect("second token valid", c.req("GET", "/reservations", token=t2), 200, section="§6")
    chk.expect("duplicate email 409 email_taken",
               c.req("POST", "/auth/signup", {"email": email, "password": "other password", "display_name": "X"}),
               409, "email_taken", "§6")
    chk.expect("seeded email taken 409",
               c.req("POST", "/auth/signup", {"email": ADA["email"], "password": "other password", "display_name": "X"}),
               409, "email_taken", "§6")
    for pw in ("1234567", "", "short"):
        chk.expect(f"password {len(pw)} chars 422",
                   c.req("POST", "/auth/signup", {"email": f"p{uuid.uuid4().hex[:8]}@ex.com", "password": pw,
                                                  "display_name": "P"}), 422, "validation_failed", "§6")
    chk.expect("password exactly 8 chars 201",
               c.req("POST", "/auth/signup", {"email": f"p{uuid.uuid4().hex[:8]}@ex.com", "password": "12345678",
                                              "display_name": "P"}), 201, section="§6")
    for em in ("noat", "a@", "@b", "", "a b@", "two@@x"):
        chk.expect(f"email {em!r} 422",
                   c.req("POST", "/auth/signup", {"email": em, "password": "correct horse", "display_name": "E"}),
                   422, "validation_failed", "§6")
    for em in ("a b@x.com", "two@@x.com", "a@b@c.com", " a@b.com"):
        chk.expect(f"email {em!r} 422 (R-4: one @, no whitespace)",
                   c.req("POST", "/auth/signup", {"email": em, "password": "correct horse", "display_name": "E"}),
                   422, "validation_failed")
    chk.expect("email taken case-insensitively 409",
               c.req("POST", "/auth/signup", {"email": "ADA@Example.COM", "password": "correct horse", "display_name": "A2"}), 409, "email_taken")
    r = c.req("POST", "/auth/login", {"email": "Ada@EXAMPLE.com", "password": ADA["password"]})
    chk.check("login email case-insensitive 200 (R-4)", r.status == 200 and (r.json or {}).get("user_id") == "u_ada", 200, r.text(200), r.req)
    for dn in ("", "   "):
        chk.expect(f"display_name {dn!r} 422 (R-4)",
                   c.req("POST", "/auth/signup", {"email": f"d{uuid.uuid4().hex[:8]}@ex.com", "password": "correct horse", "display_name": dn}),
                   422, "validation_failed")
    chk.expect("password 7 code points (multi-byte) 422 (R-4)",
               c.req("POST", "/auth/signup", {"email": f"m{uuid.uuid4().hex[:8]}@ex.com", "password": "ééééééé", "display_name": "M"}),
               422, "validation_failed")
    chk.expect("password 8 code points (multi-byte) 201 (R-4)",
               c.req("POST", "/auth/signup", {"email": f"m{uuid.uuid4().hex[:8]}@ex.com", "password": "éééééééé", "display_name": "M"}), 201)
    chk.expect("R-19: wrong type beats missing field (email missing, password number) 400",
               c.req("POST", "/auth/signup", {"password": 12345678, "display_name": "X"}), 400, "malformed_request")
    chk.expect("R-19: wrong type beats bad value (email 'bad', display_name number) 400",
               c.req("POST", "/auth/signup", {"email": "bad", "password": "correct horse", "display_name": 3}), 400, "malformed_request")
    chk.expect("R-19: missing beats bad value (email 'bad', password missing) 422",
               c.req("POST", "/auth/signup", {"email": "bad", "display_name": "X"}), 422, "validation_failed")
    chk.expect("R-4: 422 beats 409 (taken email, short password)",
               c.req("POST", "/auth/signup", {"email": ADA["email"], "password": "short", "display_name": "X"}), 422, "validation_failed")
    chk.expect("signup email null is a wrong type 400 (R-2)", c.req("POST", "/auth/signup", {"email": None, "password": "correct horse", "display_name": "X"}),
               400, "malformed_request")
    base = {"email": "t@example.com", "password": "correct horse", "display_name": "T"}
    for f, bad in (("email", 5), ("password", 12345678), ("display_name", 7), ("email", ["a@b"]), ("password", True)):
        chk.expect(f"signup {f}={bad!r} wrong type 400", c.req("POST", "/auth/signup", {**base, f: bad}),
                   400, "malformed_request", "§5")
    for f in ("email", "password"):
        b = dict(base); b.pop(f)
        chk.expect(f"signup missing {f} 422", c.req("POST", "/auth/signup", b), 422, "validation_failed", "§5")
    b = dict(base); b.pop("display_name")
    chk.expect("signup missing display_name 422", c.req("POST", "/auth/signup", b), 422, "validation_failed", "§5")
    chk.expect("signup unparseable body 400", c.req("POST", "/auth/signup", raw='{"email": "x@y.z",'),
               400, "malformed_request", "§5")
    chk.expect("signup body is a JSON array 400", c.req("POST", "/auth/signup", raw='[1,2]'), 400, "malformed_request", "§5")
    chk.expect("login wrong password 401", c.req("POST", "/auth/login", {"email": ADA["email"], "password": "wrong pass"}),
               401, "unauthenticated", "§6")
    chk.expect("login unknown email 401", c.req("POST", "/auth/login", {"email": "ghost@example.com", "password": "whatever1"}),
               401, "unauthenticated", "§6")
    chk.expect("login email wrong type 400", c.req("POST", "/auth/login", {"email": 1, "password": "x"}),
               400, "malformed_request", "§5")
    chk.expect("login missing password 422", c.req("POST", "/auth/login", {"email": ADA["email"]}),
               422, "validation_failed", "§5")
    chk.expect("login unparseable 400", c.req("POST", "/auth/login", raw="{nope"), 400, "malformed_request", "§5")
    for label, hdr in (("no header", None), ("unknown token", "Bearer deadbeefdeadbeef"), ("Basic scheme", "Basic YTpi"),
                       ("empty bearer", "Bearer "), ("bare token", t1 or "x")):
        r = c.req("GET", "/reservations", headers={"Authorization": hdr} if hdr is not None else None)
        chk.expect(f"GET /reservations {label} 401", r, 401, "unauthenticated", "§6")
    for m, p, b in (("POST", "/reservations", {}), ("POST", "/reservation-moves", {"moves": []}),
                    ("PATCH", "/reservations/SEED01", {}), ("POST", "/reservations/SEED01/cancel", NO_BODY),
                    ("GET", "/reservations/SEED01", NO_BODY)):
        chk.expect(f"{m} {p} without token 401", c.req(m, p, b, key="k-unauth"), 401, "unauthenticated", "§6")
    for p in ("/restaurants", "/restaurants/r_anker"):
        chk.expect(f"{p} public", c.req("GET", p), 200, section="§8")


def g_availability(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s.reset()
    thu_slots = [f"{h:02d}:{m:02d}" for h in range(18, 22) for m in (0, 30)]      # 18:00..21:30
    fri_slots = thu_slots + ["22:00"]
    r = s.avail("r_anker", ctx.thu, 4)
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("availability envelope", r.status == 200 and j.get("restaurant_id") == "r_anker" and
              j.get("date") == str(ctx.thu) and j.get("timezone") == "Europe/Berlin" and isinstance(j.get("slots"), list),
              "restaurant_id/date/timezone/slots", r.text(300), r.req, "§8")
    slots = j.get("slots") or []
    chk.check("thu slots = every 30 min from opens with slot+90 <= closes",
              [x.get("starts_at_local") for x in slots] == [ctx.D(ctx.thu, h) for h in thu_slots],
              thu_slots, [x.get("starts_at_local") for x in slots], r.req, "§8")
    bad = [x for x in slots if not same_instant_and_text(x.get("starts_at"), resolve(x.get("starts_at_local", "1970-01-01T00:00"), "Europe/Berlin"))]
    chk.check("slot starts_at = local time with IANA offset", not bad, "matching offsets", bad[:3], r.req, "§9")
    chk.check("party 4 -> tables with capacity>=4 in fixture order",
              all(x.get("available_table_ids") == ["t_2", "t_3"] for x in slots) and slots, ["t_2", "t_3"],
              [x.get("available_table_ids") for x in slots][:3], r.req, "§8")
    for party, exp in ((1, ["t_1", "t_2", "t_3"]), (2, ["t_1", "t_2", "t_3"]), (5, ["t_3"]), (6, ["t_3"]), (7, [])):
        r = s.avail("r_anker", ctx.thu, party)
        sl = (r.json or {}).get("slots", []) if r.status == 200 else []
        chk.check(f"party {party} -> {exp} on every slot (empty list still listed)",
                  len(sl) == 8 and all(x.get("available_table_ids") == exp for x in sl), exp,
                  [x.get("available_table_ids") for x in sl][:2] + [len(sl)], r.req, "§8")
    r = s.avail("r_anker", ctx.fri, 2)
    chk.check("fri closes 23:30 -> last slot 22:00",
              [x.get("starts_at_local") for x in (r.json or {}).get("slots", [])] == [ctx.D(ctx.fri, h) for h in fri_slots],
              fri_slots, r.text(300), r.req, "§8")
    r = s.avail("r_anker", ctx.wed, 2)
    chk.check("closed day -> slots []", r.status == 200 and (r.json or {}).get("slots") == [], [], r.text(200), r.req, "§8")
    # occupancy, half-open
    s.book(s.ada, "r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 4)
    exp_t2 = {"18:00": False, "18:30": False, "19:00": False, "19:30": False, "20:00": False,
              "17:30": None, "20:30": True, "21:00": True, "21:30": True}
    r = s.avail("r_anker", ctx.thu, 2)
    sl = {x.get("starts_at_local", "")[-5:]: x.get("available_table_ids") for x in (r.json or {}).get("slots", [])}
    wrong = {h: sl.get(h) for h, want in exp_t2.items() if want is not None and (("t_2" in (sl.get(h) or [])) != want)}
    chk.check("booked 19:00-20:30 hides t_2 only on overlapping slots (half-open)", not wrong,
              {h: ("t_2 listed" if w else "t_2 absent") for h, w in exp_t2.items() if w is not None}, wrong, r.req, "§1/§8")
    # parameter validation
    for miss in ("restaurant_id", "date", "party_size"):
        q = {"restaurant_id": "r_anker", "date": str(ctx.thu), "party_size": "2"}
        q.pop(miss)
        chk.expect(f"availability missing {miss} 422", c.req("GET", "/availability", query=q), 422, "validation_failed", "§8")
    for d in ("2026-02-30", "2026-13-01", "20261001", "tomorrow", "", "2026-1-5", "2026-10-01T00:00"):
        chk.expect(f"availability date={d!r} 422",
                   c.req("GET", "/availability", query={"restaurant_id": "r_anker", "date": d, "party_size": "2"}),
                   422, "validation_failed", "§5")
    for p in ("0", "-1", "1e9", "4.0", "+4", "abc", "", " 4", "0x4", "true"):
        chk.expect(f"availability party_size={p!r} 422",
                   c.req("GET", "/availability", query={"restaurant_id": "r_anker", "date": str(ctx.thu), "party_size": p}),
                   422, "validation_failed", "§5")
    r = c.req("GET", "/availability", query={"restaurant_id": "r_anker", "date": str(ctx.thu), "party_size": "99999999999999999999999"})
    chk.check("availability huge party_size: no 5xx (200 with empty lists or 422)", r.status in (200, 422),
              "200|422", r.status, r.req, "§5")
    chk.expect("availability unknown restaurant 404",
               c.req("GET", "/availability", query={"restaurant_id": "nope", "date": str(ctx.thu), "party_size": "2"}),
               404, "not_found", "§5")
    chk.expect("parameter error before 404: unknown restaurant + party_size=0 -> 422",
               c.req("GET", "/availability", query={"restaurant_id": "nope", "date": str(ctx.thu), "party_size": "0"}), 422, "validation_failed")
    chk.expect("parameter error before 404: unknown restaurant + bad date -> 422",
               c.req("GET", "/availability", query={"restaurant_id": "nope", "date": "2026-02-30", "party_size": "2"}), 422, "validation_failed")
    r = s.avail("r_anker", ctx.fri, 99)
    sl = (r.json or {}).get("slots", []) if r.status == 200 else []
    chk.check("party 99 -> every slot listed with []", len(sl) == 9 and all(x.get("available_table_ids") == [] for x in sl), "9 x []",
              r.text(200), r.req)


def booking_body(rid, tid, start, party):
    return {"restaurant_id": rid, "table_id": tid, "starts_at_local": start, "party_size": party}


def g_create(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s.reset()
    key = uuid.uuid4().hex
    body = booking_body("r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 4)
    before = datetime.now(timezone.utc)
    r = c.req("POST", "/reservations", body, token=s.ada, key=key)
    j = r.json if isinstance(r.json, dict) else {}
    st = resolve(body["starts_at_local"], "Europe/Berlin")
    chk.expect("create 201", r, 201, section="§8")
    checks = {
        "reservation_id str<=64": isinstance(j.get("reservation_id"), str) and 0 < len(j["reservation_id"]) <= 64,
        "reference A-Z0-9 6..12": isinstance(j.get("reference"), str) and bool(REF_RE.match(j["reference"])),
        "restaurant_id/table_id/party_size echo": (j.get("restaurant_id"), j.get("table_id"), j.get("party_size")) == ("r_anker", "t_2", 4),
        "status confirmed": j.get("status") == "confirmed",
        "starts_at_local echo": j.get("starts_at_local") == body["starts_at_local"],
        "starts_at with offset": same_instant_and_text(j.get("starts_at"), st),
        "ends_at = starts_at + 90 min": same_instant_and_text(j.get("ends_at"), (st + timedelta(minutes=90)).astimezone(ZoneInfo("Europe/Berlin"))),
        "created_at RFC3339 with offset": isinstance(j.get("created_at"), str) and bool(RFC3339_OFFSET_RE.match(j["created_at"])),
        "created_at UTC '+00:00', whole seconds (R-21)": isinstance(j.get("created_at"), str) and
            bool(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$", j["created_at"])),
        "starts_at/ends_at offset numeric, no Z (R-21)": all(isinstance(j.get(k), str) and bool(RFC3339_OFFSET_RE.match(j[k])) for k in ("starts_at", "ends_at")),
    }
    for n, ok in checks.items():
        chk.check(f"create response: {n}", ok, n, r.text(600), r.req, "§8/§3.4")
    ca = parse_rfc(j.get("created_at") or "")
    chk.check("created_at is the server's now", ca is not None and abs((ca - before).total_seconds()) < 300,
              "within 5 min of now", j.get("created_at"), r.req, "§8", soft=True)
    ref = j.get("reference")
    r2 = s.get(s.ada, ref)
    chk.check("GET /reservations/{ref} same shape and values as create", r2.status == 200 and r2.json == j, j, r2.text(600), r2.req, "§8")

    def post(b, k=None, tok=None, raw=None):
        return c.req("POST", "/reservations", b if raw is None else NO_BODY, token=tok or s.ada,
                     key=k if k is not None else uuid.uuid4().hex, raw=raw)

    chk.expect("overlap (19:30 vs 19:00-20:30) 409 table_unavailable",
               post(booking_body("r_anker", "t_2", ctx.D(ctx.thu, "19:30"), 2), tok=s.bob), 409, "table_unavailable", "§8")
    chk.expect("overlap (18:00 ends 19:30 > 19:00) 409", post(booking_body("r_anker", "t_2", ctx.D(ctx.thu, "18:00"), 2), tok=s.bob),
               409, "table_unavailable", "§8")
    chk.expect("back-to-back 20:30 allowed (half-open)", post(booking_body("r_anker", "t_2", ctx.D(ctx.thu, "20:30"), 2), tok=s.bob),
               201, section="§1")
    chk.expect("17:30 before opens 422 outside_opening_hours", post(booking_body("r_anker", "t_1", ctx.D(ctx.thu, "17:30"), 2)),
               422, "outside_opening_hours", "§8")
    for hhmm in ("19:10", "19:15", "18:01"):
        chk.expect(f"{hhmm} off grid 422 not_on_slot_grid", post(booking_body("r_anker", "t_1", ctx.D(ctx.thu, hhmm), 2)),
                   422, "not_on_slot_grid", "§8")
    for d, hhmm, why in ((ctx.thu, "17:00", "before opens"), (ctx.thu, "22:00", "ends 23:30 after closes 23:00"),
                         (ctx.thu, "23:00", "at closes"), (ctx.wed, "19:00", "closed day"), (ctx.fri, "22:30", "ends after 23:30")):
        chk.expect(f"{why} 422 outside_opening_hours", post(booking_body("r_anker", "t_1", ctx.D(d, hhmm), 2)),
                   422, "outside_opening_hours", "§8")
    chk.expect("ends exactly at closes (21:30+90=23:00) 201", post(booking_body("r_anker", "t_1", ctx.D(ctx.thu, "21:30"), 2)),
               201, section="§8")
    chk.expect("party 3 > capacity 2 422 party_exceeds_capacity", post(booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 3)),
               422, "party_exceeds_capacity", "§8")
    for p in (0, -1, "2", True, 2.5, None, [2], {"n": 2}):
        chk.expect(f"party_size={p!r} 422 validation_failed", post(booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), p)),
                   422, "validation_failed", "§5/§8", soft=isinstance(p, (list, dict)))
    b = booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 2); b.pop("party_size")
    chk.expect("party_size missing 422", post(b), 422, "validation_failed", "§5")
    d = str(ctx.fri)
    for sl in (f"{d}T19:00:00", f"{d}T19:00Z", f"{d}T19:00+02:00", f"{d} 19:00", "2026-02-30T19:00", f"{d}T24:00",
               f"{d}T19:0", "", "19:00", f"{d}t19:00", f"{d}T7:00"):
        chk.expect(f"starts_at_local={sl!r} 422 validation_failed", post(booking_body("r_anker", "t_1", sl, 2)),
                   422, "validation_failed", "§5")
    chk.expect("starts_at_local number 400 malformed_request", post(booking_body("r_anker", "t_1", 1900, 2)),
               400, "malformed_request", "§5")
    chk.expect("starts_at_local null is a wrong type 400 (R-2)", post(booking_body("r_anker", "t_1", None, 2)),
               400, "malformed_request", "§5")
    b = booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 2); b.pop("starts_at_local")
    chk.expect("starts_at_local missing 422", post(b), 422, "validation_failed", "§5")
    for f in ("restaurant_id", "table_id"):
        for bad in (5, True, ["x"], {"id": "x"}, None):
            chk.expect(f"{f}={bad!r} wrong type 400", post({**booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 2), f: bad}),
                       400, "malformed_request", "§5")
        b = booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 2); b.pop(f)
        chk.expect(f"{f} missing 422", post(b), 422, "validation_failed", "§5")
    chk.expect("unknown restaurant 404", post(booking_body("nope", "t_1", ctx.D(ctx.fri, "18:00"), 2)), 404, "not_found", "§8")
    chk.expect("unknown table 404", post(booking_body("r_anker", "nope", ctx.D(ctx.fri, "18:00"), 2)), 404, "not_found", "§8")
    chk.expect("table of another restaurant 404", post(booking_body("r_anker", "o_1", ctx.D(ctx.fri, "18:00"), 2)), 404, "not_found", "§8")
    good = booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 2)
    chk.expect("no Idempotency-Key 400", c.req("POST", "/reservations", good, token=s.ada), 400, "missing_idempotency_key", "§7")
    chk.expect("empty Idempotency-Key 400", c.req("POST", "/reservations", good, token=s.ada, key=""), 400, "missing_idempotency_key", "§7")
    chk.expect("Idempotency-Key 256 chars 422", c.req("POST", "/reservations", good, token=s.ada, key="k" * 256),
               422, "validation_failed", "§5")
    chk.expect("Idempotency-Key 255 chars 201", c.req("POST", "/reservations", good, token=s.ada, key="k" * 255), 201, section="§5")
    chk.expect("unparseable body 400", post(None, raw='{"restaurant_id": '), 400, "malformed_request", "§5")
    chk.expect("body is JSON array 400", post(None, raw='[]'), 400, "malformed_request", "§5")
    chk.expect("body is JSON string 400", post(None, raw='"x"'), 400, "malformed_request")
    chk.expect("party_size 2.0 is integral -> 201 (R-3)", post(booking_body("r_anker", "t_2", ctx.D(ctx.fri, "21:00"), 2.0)), 201)
    # R-1 precedence on the keyed path
    chk.expect("R-1: no token and no key -> 401", c.req("POST", "/reservations", good), 401, "unauthenticated")
    chk.expect("R-1: unparseable body and no key -> 400 malformed_request", c.req("POST", "/reservations", token=s.ada, raw="{"), 400, "malformed_request")
    chk.expect("R-1: non-object body and no key -> 400 malformed_request", c.req("POST", "/reservations", token=s.ada, raw="[1]"), 400, "malformed_request")
    chk.expect("R-1: no key and wrong-type field -> 400 missing_idempotency_key",
               c.req("POST", "/reservations", {**good, "table_id": 5}, token=s.ada), 400, "missing_idempotency_key")
    chk.expect("R-1: 256-char key and wrong-type field -> 422", c.req("POST", "/reservations", {**good, "table_id": 5}, token=s.ada, key="z" * 256),
               422, "validation_failed")
    chk.expect("R-19: wrong type beats missing field ({table_id: 5}, rest missing) -> 400", post({"table_id": 5}), 400, "malformed_request")
    chk.expect("R-19: missing beats bad value (party_size 0, restaurant_id missing) -> 422", post({"table_id": "t_1", "starts_at_local": "x", "party_size": 0}),
               422, "validation_failed")
    # R-7 order of domain checks
    chk.expect("R-7: unknown restaurant before off-grid -> 404", post(booking_body("nope", "t_1", ctx.D(ctx.thu, "19:10"), 2)), 404, "not_found")
    chk.expect("R-7: invalid_local_time before outside_opening_hours (closed Sunday) -> invalid_local_time",
               post(booking_body("r_anker", "t_1", "2026-03-29T02:30", 2)), 422, "invalid_local_time")
    chk.expect("R-7: outside_opening_hours before not_on_slot_grid (17:10)", post(booking_body("r_anker", "t_1", ctx.D(ctx.thu, "17:10"), 2)),
               422, "outside_opening_hours")
    chk.expect("R-7: not_on_slot_grid before party_exceeds_capacity", post(booking_body("r_anker", "t_1", ctx.D(ctx.thu, "19:10"), 5)),
               422, "not_on_slot_grid")
    chk.expect("R-7: party_exceeds_capacity before table_unavailable", post(booking_body("r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 5)),
               422, "party_exceeds_capacity")
    chk.expect("past start allowed (no cutoff on create)", post(booking_body("r_now", "n_1", ctx.past, 2)), 201, section="§4")
    chk.expect("start inside cutoff allowed on create", post(booking_body("r_now", "n_1", ctx.near, 2)), 201, section="§4")
    # references unique across many bookings
    refs = set()
    for i, hhmm in enumerate(["18:00", "19:30", "21:00"]):
        for t in ("t_1", "t_2", "t_3"):
            r = post(booking_body("r_other", "o_1", ctx.D(ctx.fri if t == "t_1" else ctx.thu + timedelta(days=14 if t == "t_2" else 21), hhmm), 2))
            if r.status == 201:
                refs.add(r.json.get("reference"))
    chk.check("references unique and well-formed", len(refs) == 9 and all(REF_RE.match(x or "") for x in refs), 9, sorted(refs), None, "§8")


def g_reads(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s.reset()
    a1 = s.book(s.ada, "r_anker", "t_1", ctx.D(ctx.thu, "18:00"))
    a2 = s.book(s.ada, "r_anker", "t_1", ctx.D(ctx.fri, "21:00"))
    a3 = s.book(s.ada, "r_now", "n_1", ctx.far)
    s.book(s.bob, "r_anker", "t_2", ctx.D(ctx.thu, "18:00"))
    tie = [s.book(s.ada, "r_anker", t, ctx.D(ctx.fri, "18:00")) for t in ("t_3", "t_2")]
    c.req("POST", f"/reservations/{a3['reference']}/cancel", token=s.ada)
    rows = s.mine(s.ada)
    want = sorted(rows, key=lambda r: (-(parse_rfc(r.get("starts_at") or "") or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                                       (parse_rfc(r.get("created_at") or "") or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                                       r.get("reference") or ""))
    chk.check("ties on starts_at ordered by created_at asc, then reference asc (R-11)",
              [r.get("reference") for r in rows] == [r.get("reference") for r in want] and len(tie) == 2,
              [r.get("reference") for r in want], [r.get("reference") for r in rows], None)
    refs = [r.get("reference") for r in rows]
    starts = [parse_rfc(r.get("starts_at") or "") for r in rows]
    chk.check("GET /reservations: own only, confirmed + cancelled", set(refs) == {a1["reference"], a2["reference"], a3["reference"]} | {f"SEED0{i}" for i in range(1, 10)} | {t["reference"] for t in tie},
              "ada's 3 + 9 seeded", refs, None, "§8")
    chk.check("GET /reservations ordered starts_at descending", None not in starts and starts == sorted(starts, reverse=True),
              "descending", [r.get("starts_at") for r in rows], None, "§8")
    st = {r.get("reference"): r.get("status") for r in rows}
    chk.check("cancelled reservation listed as cancelled", st.get(a3["reference"]) == "cancelled", "cancelled", st.get(a3["reference"]), None, "§8")
    keys = {"reservation_id", "reference", "restaurant_id", "table_id", "party_size", "status", "starts_at_local", "starts_at", "ends_at", "created_at"}
    miss = [r.get("reference") for r in rows if not keys <= set(r)]
    chk.check("list entries have create-response shape", not miss, sorted(keys), miss, None, "§8")
    chk.expect("GET other's reservation 404", s.get(s.bob, a1["reference"]), 404, "not_found", "§8")
    chk.expect("GET unknown reference 404", s.get(s.ada, "ZZZZZZ"), 404, "not_found", "§8")


def g_cancel(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s.reset()
    far = s.book(s.ada, "r_now", "n_1", ctx.far)
    near = s.book(s.ada, "r_now", "n_1", ctx.near)
    past = s.book(s.ada, "r_now", "n_2", ctx.past)
    anker = s.book(s.ada, "r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 4)
    r = c.req("POST", f"/reservations/{far['reference']}/cancel", token=s.ada)
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("cancel 200 status cancelled, same reference", r.status == 200 and j.get("status") == "cancelled" and j.get("reference") == far["reference"],
              "200 cancelled", r.text(300), r.req, "§8")
    chk.check("cancel response keeps the rest of the reservation", all(j.get(k) == far.get(k) for k in ("reservation_id", "starts_at", "table_id", "created_at")),
              far, j, r.req, "§8")
    r = c.req("POST", f"/reservations/{far['reference']}/cancel", token=s.ada)
    chk.check("cancel twice 200 with current state", r.status == 200 and (r.json or {}).get("status") == "cancelled", "200 cancelled", r.text(300), r.req, "§8")
    for name, b in (("start within cutoff", near), ("start in the past", past)):
        r = c.req("POST", f"/reservations/{b['reference']}/cancel", token=s.ada)
        chk.expect(f"cancel {name} 409 cutoff_passed", r, 409, "cutoff_passed", "§8")
        chk.check(f"cancel {name}: still confirmed", (s.get(s.ada, b["reference"]).json or {}).get("status") == "confirmed",
                  "confirmed", None, None, "§8")
    chk.expect("cancel other's 404", c.req("POST", f"/reservations/{anker['reference']}/cancel", token=s.bob), 404, "not_found", "§8")
    victim = s.book(s.ada, "r_anker", "t_1", ctx.D(ctx.fri, "18:00"))
    chk.expect("cancel with unparseable body 400 (R-13)", c.req("POST", f"/reservations/{victim['reference']}/cancel", token=s.ada, raw="{"),
               400, "malformed_request")
    chk.expect("cancel other's within cutoff: 404 first (R-13)", c.req("POST", f"/reservations/{near['reference']}/cancel", token=s.bob), 404, "not_found")
    chk.check("cancel attempts with bad body left booking confirmed", (s.get(s.ada, victim["reference"]).json or {}).get("status") == "confirmed",
              "confirmed", None, None)
    chk.expect("cancel unknown 404", c.req("POST", "/reservations/ZZZZZZ/cancel", token=s.ada), 404, "not_found", "§8")
    chk.check("anker table t_2 not offered at 19:00 before cancel", "t_2" not in (s.avail_tables("r_anker", ctx.thu, 2, "19:00") or []),
              "absent", None, None, "§8")
    c.req("POST", f"/reservations/{anker['reference']}/cancel", token=s.ada)
    chk.check("cancel frees the table immediately", "t_2" in (s.avail_tables("r_anker", ctx.thu, 2, "19:00") or []), "t_2 listed",
              s.avail_tables("r_anker", ctx.thu, 2, "19:00"), None, "§8")
    chk.expect("rebook the freed slot 201", c.req("POST", "/reservations", booking_body("r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 4),
                                                 token=s.bob, key=uuid.uuid4().hex), 201, section="§8")


def g_patch(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s.reset()
    A = s.book(s.ada, "r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 4)
    B = s.book(s.bob, "r_anker", "t_1", ctx.D(ctx.thu, "19:00"), 2)
    ref = A["reference"]

    def patch(b, tok=None, rf=None, raw=None):
        return c.req("PATCH", f"/reservations/{rf or ref}", b if raw is None else NO_BODY, token=tok or s.ada, raw=raw)

    r = patch({"starts_at_local": ctx.D(ctx.thu, "19:30")})
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("PATCH onto own overlapping interval 200 (old slot released together)", r.status == 200 and j.get("starts_at_local") == ctx.D(ctx.thu, "19:30"),
              "200 19:30", r.text(300), r.req, "§8")
    chk.check("PATCH keeps reference, reservation_id, created_at", all(j.get(k) == A.get(k) for k in ("reference", "reservation_id", "created_at")),
              {k: A.get(k) for k in ("reference", "reservation_id", "created_at")}, {k: j.get(k) for k in ("reference", "reservation_id", "created_at")}, r.req, "§8")
    chk.check("PATCH recomputes ends_at", same_instant_and_text(j.get("ends_at"), resolve(ctx.D(ctx.thu, "21:00"), "Europe/Berlin")),
              "21:00", j.get("ends_at"), r.req, "§8")
    chk.check("old interval released (t_2 at 18:00 offered)", "t_2" in (s.avail_tables("r_anker", ctx.thu, 2, "18:00") or []),
              "t_2 listed", s.avail_tables("r_anker", ctx.thu, 2, "18:00"), None, "§8")
    r = patch({"table_id": "t_3"})
    chk.check("PATCH table only 200", r.status == 200 and (r.json or {}).get("table_id") == "t_3" and (r.json or {}).get("starts_at_local") == ctx.D(ctx.thu, "19:30"),
              "t_3 19:30", r.text(300), r.req, "§8")
    snap = s.get(s.ada, ref).json
    fails = [
        ("party 7 > capacity 6", {"party_size": 7}, 422, "party_exceeds_capacity"),
        ("party '3'", {"party_size": "3"}, 422, "validation_failed"),
        ("party 0", {"party_size": 0}, 422, "validation_failed"),
        ("party true", {"party_size": True}, 422, "validation_failed"),
        ("off grid", {"starts_at_local": ctx.D(ctx.thu, "19:10")}, 422, "not_on_slot_grid"),
        ("outside hours", {"starts_at_local": ctx.D(ctx.thu, "22:00")}, 422, "outside_opening_hours"),
        ("closed day", {"starts_at_local": ctx.D(ctx.wed, "19:00")}, 422, "outside_opening_hours"),
        ("bad local format", {"starts_at_local": ctx.D(ctx.thu, "19:30") + ":00"}, 422, "validation_failed"),
        ("onto other's booking", {"table_id": "t_1", "party_size": 2}, 409, "table_unavailable"),
        ("unknown table", {"table_id": "nope"}, 404, "not_found"),
        ("other restaurant's table", {"table_id": "o_1"}, 404, "not_found"),
        ("table_id number", {"table_id": 5}, 400, "malformed_request"),
        ("starts_at_local number", {"starts_at_local": 5}, 400, "malformed_request"),
        ("new table too small for party", {"table_id": "t_1", "starts_at_local": ctx.D(ctx.thu, "21:30")}, 422, "party_exceeds_capacity"),
        ("nonexistent local time", {"starts_at_local": "2026-03-29T02:30"}, 422, "invalid_local_time"),
        ("table_id null", {"table_id": None}, 400, "malformed_request"),
        ("party_size null", {"party_size": None}, 422, "validation_failed"),
        ("party_size 2.5", {"party_size": 2.5}, 422, "validation_failed"),
        ("body is a JSON array", None, 400, "malformed_request"),
    ]
    for name, b, st, code in fails:
        r = patch(b) if b is not None else patch(None, raw="[1]")
        chk.expect(f"PATCH {name} {st} {code}", r, st, code, "§8")
        chk.check(f"PATCH {name}: booking unchanged", s.get(s.ada, ref).json == snap, snap, s.get(s.ada, ref).text(300), r.req, "§8")
    chk.check("bob's booking untouched", s.get(s.bob, B["reference"]).json == B, B, None, None, "§8")
    chk.expect("PATCH unparseable 400", patch(None, raw="{"), 400, "malformed_request", "§5")
    chk.expect("PATCH other's 404", patch({"party_size": 1}, tok=s.bob), 404, "not_found", "§8")
    chk.expect("PATCH unknown 404", patch({"party_size": 1}, rf="ZZZZZZ"), 404, "not_found", "§8")
    r = patch({})
    chk.check("PATCH {} 200, booking unchanged (R-14)", r.status == 200 and r.json == snap and s.get(s.ada, ref).json == snap, "200 unchanged",
              r.text(300), r.req, "§8")
    r = patch({"party_size": snap.get("party_size"), "table_id": snap.get("table_id")})
    chk.check("PATCH with current values 200 unchanged (R-14)", r.status == 200 and s.get(s.ada, ref).json == snap, "200 unchanged",
              r.text(300), r.req)
    # cutoff & cancelled
    near = s.book(s.ada, "r_now", "n_1", ctx.near)
    past = s.book(s.ada, "r_now", "n_2", ctx.past)
    far = s.book(s.ada, "r_now", "n_2", ctx.far)
    chk.expect("PATCH within cutoff 409 cutoff_passed", patch({"starts_at_local": ctx.far2}, rf=near["reference"]), 409, "cutoff_passed", "§8")
    chk.expect("PATCH past booking 409 cutoff_passed", patch({"party_size": 1}, rf=past["reference"]), 409, "cutoff_passed", "§8")
    r = patch({"starts_at_local": ctx.near2}, rf=far["reference"])
    chk.check("PATCH far booking to a start inside cutoff: allowed (cutoff measured on current start)", r.status == 200,
              200, {"status": r.status, "body": r.text(200)}, r.req, "§8")
    chk.expect("PATCH {} within cutoff 409 cutoff_passed (R-14 no-op still checked)", patch({}, rf=near["reference"]), 409, "cutoff_passed")
    chk.expect("PATCH within cutoff + party_size 0: cutoff first (R-14)", patch({"party_size": 0}, rf=near["reference"]), 409, "cutoff_passed")
    chk.expect("PATCH within cutoff + table_id number: 400 first (R-14)", patch({"table_id": 5}, rf=near["reference"]), 400, "malformed_request")
    chk.expect("PATCH other's booking within cutoff: 404 first (R-14)", patch({"party_size": 1}, tok=s.bob, rf=near["reference"]), 404, "not_found")
    c.req("POST", f"/reservations/{ref}/cancel", token=s.ada)
    chk.expect("PATCH cancelled 409 reservation_cancelled", patch({"party_size": 1}), 409, "reservation_cancelled", "§8")
    chk.expect("PATCH cancelled + party_size 0: cancelled first (R-14)", patch({"party_size": 0}), 409, "reservation_cancelled")
    chk.expect("PATCH {} on cancelled 409 (R-14)", patch({}), 409, "reservation_cancelled")


def g_dst(s: S):
    c, chk = s.c, s.chk
    s.reset()
    BER, NY = "Europe/Berlin", "America/New_York"

    def slots(rid, d):
        r = s.avail(rid, d, 2)
        return r, (r.json or {}).get("slots", []) if r.status == 200 else []

    def wall(d, hm_list):
        return [f"{d}T{h}" for h in hm_list]

    all_hm = [f"{h:02d}:{m:02d}" for h in range(0, 5) for m in (0, 30)]  # 00:00..04:30 (+90 <= 06:00)
    cases = (("r_ber", BER, "2026-03-29", ["02:00", "02:30"]), ("r_ny", NY, "2026-03-08", ["02:00", "02:30"]),
             ("r_ber", BER, "2026-10-25", []), ("r_ny", NY, "2026-11-01", []))
    for rid, tz, d, skipped in cases:
        r, sl = slots(rid, d)
        exp = wall(d, [h for h in all_hm if h not in skipped])
        got = [x.get("starts_at_local") for x in sl]
        chk.check(f"{tz} {d}: slots once each, skipped hour absent", got == exp, exp, got, r.req, "§9")
        bad = [x for x in sl if not same_instant_and_text(x.get("starts_at"), resolve(x.get("starts_at_local", "1970-01-01T00:00"), tz))]
        chk.check(f"{tz} {d}: slot offsets follow IANA, repeated hour = first occurrence", not bad, "first-occurrence offsets",
                  [(x.get("starts_at_local"), x.get("starts_at")) for x in bad][:4], r.req, "§9")

    def post(rid, tid, local):
        return c.req("POST", "/reservations", booking_body(rid, tid, local, 2), token=s.ada, key=uuid.uuid4().hex)

    for rid, local in (("r_ber", "2026-03-29T02:00"), ("r_ber", "2026-03-29T02:30"), ("r_ny", "2026-03-08T02:00"), ("r_ny", "2026-03-08T02:30")):
        chk.expect(f"book nonexistent {local} ({rid}) 422 invalid_local_time", post(rid, "d_1" if rid == "r_ber" else "y_1", local), 422, "invalid_local_time", "§9")

    def times(r, start, end):
        j = r.json if isinstance(r.json, dict) else {}
        return r.status == 201 and j.get("starts_at") == start and j.get("ends_at") == end, j

    expectations = [
        ("r_ber", "d_1", "2026-03-29T01:30", "2026-03-29T01:30:00+01:00", "2026-03-29T04:00:00+02:00"),
        ("r_ber", "d_2", "2026-03-29T03:00", "2026-03-29T03:00:00+02:00", "2026-03-29T04:30:00+02:00"),
        ("r_ny", "y_1", "2026-11-01T01:30", "2026-11-01T01:30:00-04:00", "2026-11-01T02:00:00-05:00"),
        ("r_ny", "y_2", "2026-11-01T01:00", "2026-11-01T01:00:00-04:00", "2026-11-01T01:30:00-05:00"),
        ("r_ber", "d_1", "2026-10-25T02:30", "2026-10-25T02:30:00+02:00", "2026-10-25T03:00:00+01:00"),
        ("r_ber", "d_2", "2026-10-25T01:30", "2026-10-25T01:30:00+02:00", "2026-10-25T02:00:00+01:00"),
        ("r_ny", "y_1", "2026-03-08T01:30", "2026-03-08T01:30:00-05:00", "2026-03-08T04:00:00-04:00"),
    ]
    made = {}
    for rid, tid, local, st, en in expectations:
        r = post(rid, tid, local)
        ok, j = times(r, st, en)
        chk.check(f"book {local} {rid}: starts {st} ends {en} (absolute duration)", ok, {"starts_at": st, "ends_at": en},
                  {"status": r.status, "starts_at": j.get("starts_at"), "ends_at": j.get("ends_at")}, r.req, "§9")
        made[(rid, tid, local)] = j
    # overlap uses absolute time
    chk.expect("BER spring: 03:30 overlaps 01:30+90min (ends 04:00 CEST) 409", post("r_ber", "d_1", "2026-03-29T03:30"), 409, "table_unavailable", "§9")
    chk.expect("BER spring: 04:00 after 01:30 booking 201", post("r_ber", "d_1", "2026-03-29T04:00"), 201, section="§9")
    chk.expect("NY fall: 02:00 EST right after 01:30 EDT+90min 201", post("r_ny", "y_1", "2026-11-01T02:00"), 201, section="§9")
    chk.expect("NY fall: 01:30 on y_2 overlaps 01:00 EDT booking 409", post("r_ny", "y_2", "2026-11-01T01:30"), 409, "table_unavailable", "§9")
    chk.expect("NY fall: 02:00 on y_2 after 01:00 EDT booking (ends 01:30 EST) 201", post("r_ny", "y_2", "2026-11-01T02:00"), 201, section="§9")
    chk.expect("BER fall: 02:00 on d_1 overlaps 02:30 first occurrence 409", post("r_ber", "d_1", "2026-10-25T02:00"), 409, "table_unavailable", "§9")
    chk.expect("BER fall: 03:00 CET on d_1 after 02:30 CEST+90 201", post("r_ber", "d_1", "2026-10-25T03:00"), 201, section="§9")
    # availability reflects absolute occupancy on fall-back night
    t = s.avail_tables("r_ber", "2026-10-25", 2, "02:00")
    chk.check("BER fall availability 02:00: d_1 and d_2 occupied", t == [], [], t, None, "§9")
    # ordinary offsets
    for tz, rid, d, off in ((BER, "r_ber", "2026-12-06", "+01:00"), (NY, "r_ny", "2026-07-05", "-04:00"), (NY, "r_ny", "2026-12-06", "-05:00")):
        r, sl = slots(rid, d)
        chk.check(f"{tz} {d} offset {off}", bool(sl) and all((x.get("starts_at") or "").endswith(off) for x in sl), off,
                  [x.get("starts_at") for x in sl][:2], r.req, "§9")
    # R-23: end-of-day compares absolute instants (r_close: Berlin, sun 00:00-03:30, slot 30, duration 90)
    r, sl = slots("r_close", "2026-03-29")
    got = [x.get("starts_at_local") for x in sl]
    exp = wall("2026-03-29", ["00:00", "00:30", "01:00"])
    chk.check("R-23 spring: slot offered iff instant(start)+90min <= instant(closes 03:30 CEST)", got == exp, exp, got, r.req)
    chk.expect("R-23 spring: 01:30 CET + 90 min = 04:00 CEST > closes -> outside_opening_hours", post("r_close", "c_1", "2026-03-29T01:30"),
               422, "outside_opening_hours")
    chk.expect("R-23 spring: 01:00 CET + 90 min = 03:30 CEST = closes -> 201", post("r_close", "c_1", "2026-03-29T01:00"), 201)
    r, sl = slots("r_close", "2026-10-25")
    got = [x.get("starts_at_local") for x in sl]
    exp = wall("2026-10-25", ["00:00", "00:30", "01:00", "01:30", "02:00", "02:30"])
    chk.check("R-23 fall: 02:30 CEST + 90 min = 03:00 CET <= closes 03:30 -> offered", got == exp, exp, got, r.req)
    r = post("r_close", "c_1", "2026-10-25T02:30")
    ok, j = times(r, "2026-10-25T02:30:00+02:00", "2026-10-25T03:00:00+01:00")
    chk.check("R-23 fall: book 02:30 -> 201 ending 03:00+01:00", ok, "201", r.text(300), r.req)
    chk.expect("R-23 fall: 03:00 CET + 90 = 04:30 > closes -> outside_opening_hours", post("r_close", "c_1", "2026-10-25T03:00"),
               422, "outside_opening_hours")
    # PATCH into a nonexistent local time
    future = made.get(("r_ber", "d_1", "2026-10-25T02:30"))
    if future and future.get("reference") and datetime.now(timezone.utc) < datetime(2026, 10, 24, tzinfo=timezone.utc):
        r = c.req("PATCH", f"/reservations/{future['reference']}", {"starts_at_local": "2026-03-29T02:30"}, token=s.ada)
        chk.expect("PATCH to nonexistent local time 422 invalid_local_time", r, 422, "invalid_local_time", "§9")


def g_idem(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s.reset()
    key = "idem-" + uuid.uuid4().hex
    body = booking_body("r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 4)
    r1 = c.req("POST", "/reservations", body, token=s.ada, key=key)
    chk.expect("first use 201", r1, 201, section="§7")
    r2 = c.req("POST", "/reservations", body, token=s.ada, key=key)
    chk.check("replay 200 identical JSON", r2.status == 200 and r2.json == r1.json, {"status": 200, "body": r1.json}, r2.text(400), r2.req, "§7")
    reordered = '{ "party_size":4 ,\n "starts_at_local": "%s", "table_id":"t_2","restaurant_id":"r_anker"}' % ctx.D(ctx.thu, "19:00")
    r3 = c.req("POST", "/reservations", token=s.ada, key=key, raw=reordered)
    chk.check("replay with reordered keys/whitespace 200 identical", r3.status == 200 and r3.json == r1.json, 200, r3.text(300), r3.req, "§7")
    chk.check("replays created nothing", sum(1 for x in s.mine(s.ada) if x.get("starts_at_local") == body["starts_at_local"]) == 1,
              1, None, None, "§7")
    chk.expect("same key different body 409 idempotency_key_reuse",
               c.req("POST", "/reservations", {**body, "party_size": 3}, token=s.ada, key=key), 409, "idempotency_key_reuse", "§7")
    chk.expect("same key, different invalid body 409 (idempotency before validation)",
               c.req("POST", "/reservations", {**body, "party_size": 0, "table_id": "nope"}, token=s.ada, key=key), 409, "idempotency_key_reuse", "§7")
    chk.expect("same key, body {} 409", c.req("POST", "/reservations", {}, token=s.ada, key=key), 409, "idempotency_key_reuse", "§7")
    chk.expect("same key, unparseable body 400 (parse precedes idempotency)",
               c.req("POST", "/reservations", token=s.ada, key=key, raw="{"), 400, "malformed_request", "§7")
    chk.expect("same key, no token 401", c.req("POST", "/reservations", body, key=key), 401, "unauthenticated", "§7")
    chk.expect("other user, same key, own body 201 (key scoped to user)",
               c.req("POST", "/reservations", booking_body("r_anker", "t_3", ctx.D(ctx.thu, "19:00"), 4), token=s.bob, key=key), 201, section="§7")
    chk.expect("same key+body on /reservation-moves is not a replay (validated normally: 422)",
               c.req("POST", "/reservation-moves", body, token=s.ada, key=key), 422, "validation_failed", "§7")
    # replay after change
    ref = r1.json.get("reference") if isinstance(r1.json, dict) else None
    c.req("PATCH", f"/reservations/{ref}", {"starts_at_local": ctx.D(ctx.thu, "20:00")}, token=s.ada)
    r4 = c.req("POST", "/reservations", body, token=s.ada, key=key)
    chk.check("replay after PATCH returns original response", r4.status == 200 and r4.json == r1.json, r1.json, r4.text(400), r4.req, "§7")
    c.req("POST", f"/reservations/{ref}/cancel", token=s.ada)
    r5 = c.req("POST", "/reservations", body, token=s.ada, key=key)
    chk.check("replay after cancel returns original (confirmed) response 200", r5.status == 200 and r5.json == r1.json, r1.json, r5.text(400), r5.req, "§7")
    chk.check("replay after cancel made no state change", (s.get(s.ada, ref).json or {}).get("status") == "cancelled", "cancelled", None, None, "§7")
    # failed keys are reusable
    k2 = "fail-" + uuid.uuid4().hex
    chk.expect("first attempt 422", c.req("POST", "/reservations", {**body, "party_size": 9}, token=s.ada, key=k2), 422, "party_exceeds_capacity", "§7")
    chk.expect("key reused after 4xx with different body -> first use 201",
               c.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 2), token=s.ada, key=k2), 201, section="§7")
    blocker = s.book(s.bob, "r_anker", "t_2", ctx.D(ctx.fri, "19:00"), 2)
    k3 = "conf-" + uuid.uuid4().hex
    b3 = booking_body("r_anker", "t_2", ctx.D(ctx.fri, "19:00"), 2)
    chk.expect("409 table_unavailable attempt", c.req("POST", "/reservations", b3, token=s.ada, key=k3), 409, "table_unavailable", "§7")
    c.req("POST", f"/reservations/{blocker['reference']}/cancel", token=s.bob)
    chk.expect("same key same body after the blocker is cancelled 201", c.req("POST", "/reservations", b3, token=s.ada, key=k3), 201, section="§7")
    k4 = "miss-" + uuid.uuid4().hex
    chk.expect("first attempt 404", c.req("POST", "/reservations", booking_body("r_anker", "zz", ctx.D(ctx.fri, "18:00"), 2), token=s.ada, key=k4), 404, "not_found", "§7")
    chk.expect("reuse after 404 -> 201", c.req("POST", "/reservations", booking_body("r_anker", "t_3", ctx.D(ctx.fri, "18:00"), 2), token=s.ada, key=k4), 201, section="§7")
    k5 = "long-" + "x" * 250
    chk.expect("255-char key first use 201", c.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.fri, "20:00"), 2), token=s.ada, key=k5), 201, section="§7")
    k6 = "num-" + uuid.uuid4().hex
    r6 = c.req("POST", "/reservations", booking_body("r_anker", "t_2", ctx.D(ctx.fri, "21:00"), 2), token=s.ada, key=k6)
    r7 = c.req("POST", "/reservations", booking_body("r_anker", "t_2", ctx.D(ctx.fri, "21:00"), 2.0), token=s.ada, key=k6)
    chk.check("replay with party_size 2.0 for 2 is the same body: 200 identical (R-3)", r6.status == 201 and r7.status == 200 and r7.json == r6.json,
              200, r7.text(300), r7.req)
    chk.expect("255-char key replay 200",c.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.fri, "20:00"), 2), token=s.ada, key=k5), 200, section="§7")


def g_moves(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s.reset()
    T = lambda h: ctx.D(ctx.thu, h)  # noqa: E731
    A = s.book(s.ada, "r_anker", "t_1", T("19:00"), 2)
    B = s.book(s.ada, "r_anker", "t_2", T("19:00"), 2)
    C = s.book(s.bob, "r_anker", "t_3", T("21:00"), 2)

    def mv(moves, tok=None, key=None, raw=None):
        return c.req("POST", "/reservation-moves", {"moves": moves} if raw is None else NO_BODY, token=tok or s.ada,
                     key=key or uuid.uuid4().hex, raw=raw)

    def state(*refs):
        return [s.get(s.ada, x["reference"]).json for x in refs]

    k_swap = "swap-" + uuid.uuid4().hex
    swap = [{"reference": A["reference"], "table_id": "t_2"}, {"reference": B["reference"], "table_id": "t_1"}]
    r = mv(swap, key=k_swap)
    rs = (r.json or {}).get("reservations") if isinstance(r.json, dict) else None
    ok = r.status == 201 and isinstance(rs, list) and len(rs) == 2 and rs[0].get("reference") == A["reference"] and \
        rs[0].get("table_id") == "t_2" and rs[1].get("table_id") == "t_1"
    chk.check("swap two tables in one batch 201, input order", ok, "201 [A->t_2, B->t_1]", r.text(500), r.req, "§11")
    chk.check("swap keeps identity/created_at", isinstance(rs, list) and len(rs) == 2 and all(
        x.get(k) == y.get(k) for x, y in zip(rs, (A, B)) for k in ("reservation_id", "reference", "created_at")),
        "unchanged ids", rs, r.req, "§11")
    swap_resp = r.json
    r = mv(swap, key=k_swap)
    chk.check("moves replay 200 identical", r.status == 200 and r.json == swap_resp, swap_resp, r.text(300), r.req, "§11/§7")
    chk.expect("moves same key different body 409", mv([{"reference": A["reference"]}], key=k_swap), 409, "idempotency_key_reuse", "§7")
    chk.expect("moves same key, invalid body 409", mv([], key=k_swap), 409, "idempotency_key_reuse", "§7")
    # chain: A (t_2) moves to t_3@19:00 vacating; B (t_1) moves into t_2 -- listed in "wrong" order
    r = mv([{"reference": B["reference"], "table_id": "t_2"}, {"reference": A["reference"], "table_id": "t_3"}])
    chk.check("chain move into a slot vacated by a later item 201", r.status == 201, 201, r.text(300), r.req, "§11")
    snap = state(A, B)
    # atomic failure: first item fine, second collides with unlisted booking C
    k_fail = "mvfail-" + uuid.uuid4().hex
    bad = [{"reference": A["reference"], "starts_at_local": T("18:00"), "table_id": "t_1"},
           {"reference": B["reference"], "table_id": "t_3", "starts_at_local": T("21:00")}]
    chk.expect("move colliding with unlisted booking 409 table_unavailable", mv(bad, key=k_fail), 409, "table_unavailable", "§11")
    chk.check("failed batch changed nothing", state(A, B) == snap, snap, state(A, B), None, "§11")
    t19 = s.avail_tables("r_anker", ctx.thu, 2, "19:00")
    chk.check("failed batch kept old occupancy (A still holds t_3 at 19:00)", t19 is not None and "t_3" not in t19,
              "t_3 absent", t19, None, "§11")
    c.req("POST", f"/reservations/{C['reference']}/cancel", token=s.bob)
    r = mv(bad, key=k_fail)
    chk.expect("failed batch key is reusable: same key, same body after blocker cancelled 201", r, 201, section="§11/§7")
    snap = state(A, B)
    # overlap among the resulting bookings
    r = mv([{"reference": A["reference"], "table_id": "t_2", "starts_at_local": T("19:00")},
            {"reference": B["reference"], "table_id": "t_2", "starts_at_local": T("19:30")}])
    chk.expect("overlap among resulting bookings 409", r, 409, "table_unavailable", "§11")
    chk.check("unchanged after internal overlap", state(A, B) == snap, snap, state(A, B), None, "§11")
    # no-op and unchanged items
    r = mv([{"reference": A["reference"]}, {"reference": B["reference"], "zzz": 1}])
    rs = (r.json or {}).get("reservations") if isinstance(r.json, dict) else None
    chk.check("no-op moves 201 returning unchanged bookings in input order", r.status == 201 and rs == snap, snap, r.text(400), r.req, "§11")
    # validation matrix
    D = [s.book(s.ada, "r_anker", t, ctx.D(ctx.fri, h)) for t in ("t_1", "t_2", "t_3") for h in ("18:00", "20:00")]
    nine = [{"reference": f"SEED0{i}"} for i in range(1, 10)]
    shapes = [
        ("moves empty", {"moves": []}), ("moves missing", {}), ("9 moves", {"moves": nine}),
        ("duplicate references", {"moves": [{"reference": A["reference"]}, {"reference": A["reference"]}]}),
        ("item without reference", {"moves": [{"table_id": "t_1"}]}),
    ]
    for name, b in shapes:
        chk.expect(f"moves {name} 422", c.req("POST", "/reservation-moves", b, token=s.ada, key=uuid.uuid4().hex), 422, "validation_failed", "§11")
    for name, b in (("moves not array", {"moves": "x"}), ("moves object", {"moves": {"reference": "x"}}),
                    ("item not object", {"moves": ["SEED01"]}), ("reference number", {"moves": [{"reference": 7}]})):
        chk.expect(f"moves {name} 422 structure (R-22a)", c.req("POST", "/reservation-moves", b, token=s.ada, key=uuid.uuid4().hex), 422, "validation_failed")
    for name, b in (("moves null", {"moves": None}), ("reference null", {"moves": [{"reference": None}]})):
        chk.expect(f"{name} 422 structure (R-22a)", c.req("POST", "/reservation-moves", b, token=s.ada, key=uuid.uuid4().hex), 422, "validation_failed")
    r = c.req("POST", "/reservation-moves", {"moves": [{"reference": f"SEED0{i}"} for i in range(1, 9)]}, token=s.ada, key=uuid.uuid4().hex)
    chk.expect("8 moves allowed 201", r, 201, section="§11")
    chk.expect("moves unknown reference 404", mv([{"reference": A["reference"]}, {"reference": "ZZZZZZ"}]), 404, "not_found", "§11")
    chk.expect("moves other owner's reference 404", mv([{"reference": A["reference"]}, {"reference": C["reference"]}]), 404, "not_found", "§11")
    near = s.book(s.ada, "r_now", "n_1", ctx.near)
    far = s.book(s.ada, "r_now", "n_2", ctx.far)
    chk.expect("moves across restaurants 422", mv([{"reference": A["reference"]}, {"reference": far["reference"]}]), 422, "validation_failed", "§11")
    chk.expect("moves within cutoff 409 cutoff_passed", mv([{"reference": far["reference"], "party_size": 3}, {"reference": near["reference"], "table_id": "n_2"}]),
               409, "cutoff_passed", "§11")
    chk.check("far booking untouched after cutoff failure", s.get(s.ada, far["reference"]).json == far, far, None, None, "§11")
    chk.expect("cutoff precedes that booking's off-grid error", mv([{"reference": near["reference"], "starts_at_local": ctx.near[:-2] + "07"}]), 409, "cutoff_passed", "§11")
    chk.expect("cutoff precedes that booking's unknown-table error", mv([{"reference": near["reference"], "table_id": "nope"}]), 409, "cutoff_passed", "§11")
    chk.expect("cutoff precedes that booking's party_size=0 error", mv([{"reference": near["reference"], "party_size": 0}]), 409, "cutoff_passed", "§11")
    chk.expect("no-op item within cutoff 409 cutoff_passed (R-22)", mv([{"reference": near["reference"]}]), 409, "cutoff_passed")
    chk.expect("input order: first item's 422 wins over later 404",
               mv([{"reference": D[0]["reference"], "party_size": 0}, {"reference": D[1]["reference"], "table_id": "nope"}]), 422, "validation_failed", "§11")
    chk.expect("input order: first item's 404 wins over later not_on_slot_grid",
               mv([{"reference": D[0]["reference"], "table_id": "nope"}, {"reference": D[1]["reference"], "starts_at_local": ctx.D(ctx.fri, "18:10")}]), 404, "not_found", "§11")
    chk.expect("input order: first item's 404 wins over later party_size=0",
               mv([{"reference": D[0]["reference"], "table_id": "nope"}, {"reference": D[1]["reference"], "party_size": 0}]), 404, "not_found", "§11")
    chk.expect("input order: first item's party_size=0 wins over later unknown reference (R-22c)",
               mv([{"reference": D[0]["reference"], "party_size": 0}, {"reference": "ZZZZZZ"}]), 422, "validation_failed")
    chk.expect("structure 422 beats item wrong type (duplicate refs + table_id number) (R-22a)",
               mv([{"reference": D[0]["reference"], "table_id": 5}, {"reference": D[0]["reference"]}]), 422, "validation_failed")
    chk.expect("item wrong type 400 beats earlier item's unknown reference (R-22b)",
               mv([{"reference": "ZZZZZZ"}, {"reference": D[0]["reference"], "table_id": 5}]), 400, "malformed_request")
    chk.expect("item starts_at_local number 400 (R-22b)", mv([{"reference": D[0]["reference"], "starts_at_local": 1900}]), 400, "malformed_request")
    chk.expect("item table_id null 400 (R-22b, R-2)", mv([{"reference": D[0]["reference"], "table_id": None}]), 400, "malformed_request")
    chk.expect("item party_size '2' 422 not 400 (R-22)", mv([{"reference": D[0]["reference"], "party_size": "2"}]), 422, "validation_failed")
    chk.expect("input order: off-grid first beats capacity second",
               mv([{"reference": D[2]["reference"], "starts_at_local": ctx.D(ctx.fri, "18:10")}, {"reference": D[0]["reference"], "party_size": 5}]),
               422, "not_on_slot_grid", "§11")
    chk.expect("moves party exceeds capacity 422", mv([{"reference": D[0]["reference"], "party_size": 5}]), 422, "party_exceeds_capacity", "§11")
    chk.expect("moves outside hours 422", mv([{"reference": D[0]["reference"], "starts_at_local": ctx.D(ctx.fri, "22:30")}]), 422, "outside_opening_hours", "§11")
    chk.expect("moves wrong-type table_id 400", mv([{"reference": D[0]["reference"], "table_id": 3}]), 400, "malformed_request", "§11/§5")
    c.req("POST", f"/reservations/{D[5]['reference']}/cancel", token=s.ada)
    chk.expect("moves cancelled 409 reservation_cancelled", mv([{"reference": D[5]["reference"], "party_size": 1}]), 409, "reservation_cancelled", "§11")
    chk.expect("moves no token 401", c.req("POST", "/reservation-moves", {"moves": [{"reference": A["reference"]}]}, key="x"), 401, "unauthenticated", "§11")
    chk.expect("moves no key 400", c.req("POST", "/reservation-moves", {"moves": [{"reference": A["reference"]}]}, token=s.ada), 400, "missing_idempotency_key", "§7")
    chk.expect("moves key 256 422", mv([{"reference": A["reference"]}], key="m" * 256), 422, "validation_failed", "§5")
    chk.expect("moves unparseable 400", mv(None, raw='{"moves": ['), 400, "malformed_request", "§5")
    # replay after cancellation
    c.req("POST", f"/reservations/{A['reference']}/cancel", token=s.ada)
    r = mv(swap, key=k_swap)
    chk.check("moves replay after cancellation returns original 201 body with 200", r.status == 200 and r.json == swap_resp, swap_resp, r.text(300), r.req, "§11")
    kr = "path-" + uuid.uuid4().hex
    r = c.req("POST", "/reservations", booking_body("r_other", "o_1", ctx.D(ctx.fri, "21:00"), 2), token=s.ada, key=kr)
    chk.check("setup: keyed booking for path-scope check", r.status == 201, 201, r.status, r.req, "harness")
    chk.expect("key already used on /reservations is independent on /reservation-moves", mv([{"reference": B["reference"]}], key=kr), 201, section="§7")


# --------------------------------------------------------------------------- bursts

def plan_entry(base, method, path, body, token, key=None):
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    if key is not None:
        h["Idempotency-Key"] = key
    return burst.Req(method, base + path, h, None if body is None else json.dumps(body).encode())


def statuses(resps):
    out = {}
    for r in resps:
        out[str(r.status)] = out.get(str(r.status), 0) + 1
    return out


def codes(resps):
    out = []
    for r in resps:
        try:
            out.append(json.loads(r.body).get("error", {}).get("code"))
        except Exception:
            out.append(None)
    return out


def g_burst(s: S, rounds: int):
    c, chk, ctx = s.c, s.chk, s.ctx
    base = c.base
    for rnd in range(rounds):
        s.reset()
        users = [s.signup() for _ in range(10)] + [s.ada, s.bob]
        # B1: 50 conflicting creates, same table + slot
        body = booking_body("r_anker", "t_1", ctx.D(ctx.fri, "19:00"), 2)
        rs = burst.fire([plan_entry(base, "POST", "/reservations", body, users[i % len(users)], f"b1-{rnd}-{i}") for i in range(50)])
        st = statuses(rs)
        chk.check(f"r{rnd} B1 50 conflicting creates: exactly one 201, rest 409 table_unavailable",
                  st.get("201") == 1 and st.get("409") == 49 and codes([r for r in rs if r.status == 409]).count("table_unavailable") == 49,
                  {"201": 1, "409": 49}, st, {"burst": "B1", "body": body}, "§1")
        # B2: overlapping different starts on t_2
        starts = [ctx.D(ctx.fri, f"{h:02d}:{m:02d}") for h in range(18, 23) for m in (0, 30)][:9]
        rs = burst.fire([plan_entry(base, "POST", "/reservations", booking_body("r_anker", "t_2", starts[i % 9], 2), users[i % len(users)], f"b2-{rnd}-{i}")
                         for i in range(45)])
        st = statuses(rs)
        chk.check(f"r{rnd} B2 overlapping creates: only 201/409", set(st) <= {"201", "409"}, "201|409", st, {"burst": "B2"}, "§1")
        # B3: identical concurrent replays
        k = f"b3-{rnd}-{uuid.uuid4().hex}"
        b3 = booking_body("r_anker", "t_3", ctx.D(ctx.fri, "18:00"), 2)
        rs = burst.fire([plan_entry(base, "POST", "/reservations", b3, s.ada, k) for _ in range(30)])
        st = statuses(rs)
        bodies = {json.dumps(json.loads(r.body), sort_keys=True) for r in rs if r.status in (200, 201)}
        chk.check(f"r{rnd} B3 30 concurrent identical keyed creates: one 201, 29x200, one body", st == {"201": 1, "200": 29} and len(bodies) == 1,
                  {"201": 1, "200": 29, "distinct": 1}, {"statuses": st, "distinct": len(bodies)}, {"burst": "B3", "body": b3}, "§7")
        n3 = sum(1 for x in s.mine(s.ada) if x.get("starts_at_local") == b3["starts_at_local"] and x.get("table_id") == "t_3")
        chk.check(f"r{rnd} B3 operation took effect once", n3 == 1, 1, n3, None, "§7")
        # B4: same key, two different bodies
        k = f"b4-{rnd}-{uuid.uuid4().hex}"
        bx = booking_body("r_anker", "t_3", ctx.D(ctx.fri, "20:00"), 2)
        by = booking_body("r_other", "o_1", ctx.D(ctx.fri, "18:00"), 2)
        rs = burst.fire([plan_entry(base, "POST", "/reservations", bx if i % 2 else by, s.bob, k) for i in range(30)])
        st = statuses(rs)
        chk.check(f"r{rnd} B4 same key two bodies: one 201, others 200 (same body) or 409 reuse", st.get("201") == 1 and set(st) <= {"201", "200", "409"},
                  "one 201", st, {"burst": "B4"}, "§7")
        made = [x for x in s.mine(s.bob) if (x.get("table_id"), x.get("starts_at_local")) in
                {(bx["table_id"], bx["starts_at_local"]), (by["table_id"], by["starts_at_local"])}]
        chk.check(f"r{rnd} B4 exactly one booking created", len(made) == 1, 1, len(made), None, "§7")
        # B5: concurrent moves replays
        m1 = s.book(s.ada, "r_anker", "t_1", ctx.D(ctx.thu, "18:00"))
        m2 = s.book(s.ada, "r_anker", "t_2", ctx.D(ctx.thu, "18:00"))
        k = f"b5-{rnd}-{uuid.uuid4().hex}"
        mb = {"moves": [{"reference": m1["reference"], "table_id": "t_2"}, {"reference": m2["reference"], "table_id": "t_1"}]}
        rs = burst.fire([plan_entry(base, "POST", "/reservation-moves", mb, s.ada, k) for _ in range(20)])
        st = statuses(rs)
        bodies = {json.dumps(json.loads(r.body), sort_keys=True) for r in rs if r.status in (200, 201)}
        chk.check(f"r{rnd} B5 20 concurrent identical moves: one 201, 19x200, one body", st == {"201": 1, "200": 19} and len(bodies) == 1,
                  {"201": 1, "200": 19}, {"statuses": st, "distinct": len(bodies)}, {"burst": "B5"}, "§11/§7")
        chk.check(f"r{rnd} B5 swap applied once", (s.get(s.ada, m1["reference"]).json or {}).get("table_id") == "t_2", "t_2", None, None, "§11")
        # B6: concurrent PATCHes onto one slot
        spots = [(t, h) for t in ("t_1", "t_2", "t_3") for h in ("18:00", "19:30", "21:00")][:8]
        movers = [s.book(users[i], "r_anker", t, ctx.D(ctx.thu2 + timedelta(days=7), h)) for i, (t, h) in enumerate(spots)]
        rs = burst.fire([plan_entry(base, "PATCH", f"/reservations/{m['reference']}", {"table_id": "t_3", "starts_at_local": ctx.D(ctx.thu, "20:00"), "party_size": 2}, users[i], None)
                         for i, m in enumerate(movers)])
        st = statuses(rs)
        chk.check(f"r{rnd} B6 8 concurrent PATCHes to one slot: one 200, rest 409", st.get("200") == 1 and st.get("409") == 7, {"200": 1, "409": 7},
                  st, {"burst": "B6"}, "§1/§8")
        # B7: competing batches. Item 1 of every batch is uncontested, item 2 targets one shared slot:
        # exactly one batch may commit, and every loser's item 1 must stay where it was.
        contested = ctx.D(ctx.thu + timedelta(days=21), "19:00")
        pairs = []
        for i, u in enumerate(users[:10]):
            day = ctx.thu + timedelta(days=7 * (5 + i))
            pairs.append((u, s.book(u, "r_anker", "t_1", ctx.D(day, "18:00")), s.book(u, "r_anker", "t_2", ctx.D(day, "18:00")), day))
        rs = burst.fire([plan_entry(base, "POST", "/reservation-moves",
                                    {"moves": [{"reference": p["reference"], "starts_at_local": ctx.D(day, "21:00")},
                                               {"reference": q["reference"], "table_id": "t_3", "starts_at_local": contested}]}, u, f"b7-{rnd}-{i}")
                         for i, (u, p, q, day) in enumerate(pairs)])
        st = statuses(rs)
        partial = []
        for (u, p, q, day), resp in zip(pairs, rs):
            p_now = (s.get(u, p["reference"]).json or {}).get("starts_at_local")
            q_now = (s.get(u, q["reference"]).json or {}).get("starts_at_local")
            moved = (p_now == ctx.D(day, "21:00"), q_now == contested)
            if (resp.status == 201 and moved != (True, True)) or (resp.status != 201 and moved != (False, False)):
                partial.append({"status": resp.status, "p": p_now, "q": q_now})
        chk.check(f"r{rnd} B7 10 competing batches: one 201, rest 409, every batch all-or-nothing",
                  st == {"201": 1, "409": 9} and not partial, {"201": 1, "409": 9, "partial": []},
                  {"statuses": st, "partial": partial[:3]}, {"burst": "B7"}, "§11")
        # B8: signup race
        email = f"race{rnd}{uuid.uuid4().hex[:6]}@example.com"
        rs = burst.fire([burst.Req("POST", base + "/auth/signup", {"Content-Type": "application/json"},
                                   json.dumps({"email": email, "password": f"password{i:03d}", "display_name": "R"}).encode()) for i in range(20)])
        st = statuses(rs)
        chk.check(f"r{rnd} B8 20 concurrent signups same email: one 201, 19 email_taken", st == {"201": 1, "409": 19}, {"201": 1, "409": 19}, st, {"burst": "B8"}, "§6")
        # B9: mixed chaos then invariant
        mix = []
        for i in range(50):
            u = users[i % len(users)]
            if i % 5 == 0:
                mix.append(plan_entry(base, "GET", "/availability?" + urlencode({"restaurant_id": "r_anker", "date": str(ctx.thu), "party_size": 2}), None, u))
            else:
                mix.append(plan_entry(base, "POST", "/reservations", booking_body("r_anker", ["t_1", "t_2", "t_3"][i % 3], ctx.D(ctx.thu + timedelta(days=14), ["18:00", "18:30", "19:00", "19:30", "20:00"][i % 5]), 2), u, f"b9-{rnd}-{i}"))
        rs = burst.fire(mix)
        st = statuses(rs)
        chk.check(f"r{rnd} B9 mixed load: no 5xx/timeouts", all(r.status is not None and r.status < 500 for r in rs), "<500", st, {"burst": "B9"}, "§5")
        check_invariant(s, users, f"r{rnd} after bursts")
        # B10: 50 in flight reads latency
        rs = burst.fire([burst.Req("GET", base + "/availability?" + urlencode({"restaurant_id": "r_anker", "date": str(ctx.fri), "party_size": 2}))
                         for _ in range(50)], timeout=10)
        summ = burst.summarize(rs)
        chk.check(f"r{rnd} B10 50 concurrent reads: all 200, max < 5 s", summ["statuses"] == {"200": 50} and (summ["max_ms"] or 0) < 5000,
                  {"200": 50, "max_ms": "<5000"}, {k: summ[k] for k in ("statuses", "max_ms", "p50_ms")}, {"burst": "B10"}, "§2")


# --------------------------------------------------------------------------- export / import

def g_export(s: S, dest: Client | None):
    c, chk, ctx = s.c, s.chk, s.ctx
    s.reset()
    tok_new = s.signup(email="porter@example.com", password="portable pass", name="Porter")
    k1, k2, kfail = "ex-" + uuid.uuid4().hex, "exmv-" + uuid.uuid4().hex, "exfail-" + uuid.uuid4().hex
    b1 = booking_body("r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 2)
    r1 = c.req("POST", "/reservations", b1, token=tok_new, key=k1)
    x2 = s.book(tok_new, "r_anker", "t_1", ctx.D(ctx.thu, "19:00"), 2)
    rm = c.req("POST", "/reservation-moves", {"moves": [{"reference": r1.json["reference"], "table_id": "t_1"}, {"reference": x2["reference"], "table_id": "t_2"}]},
               token=tok_new, key=k2)
    fail_body = booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 9)
    c.req("POST", "/reservations", fail_body, token=tok_new, key=kfail)
    c.req("POST", f"/reservations/{x2['reference']}/cancel", token=tok_new)
    chk.check("export setup ok", r1.status == 201 and rm.status == 201, 201, [r1.status, rm.status], None, "§10")
    list_src = s.mine(tok_new)
    ada_src = s.mine(s.ada)
    rest_src = c.req("GET", "/restaurants/r_anker").json
    r = c.req("GET", "/_test/export")
    E = r.json if isinstance(r.json, dict) else {}
    chk.check("export 200 {track: tablekeeper, format_version: 1, state: object}", r.status == 200 and E.get("track") == "tablekeeper"
              and E.get("format_version") == 1 and isinstance(E.get("state"), dict), "shape", r.text(200), r.req, "§10")
    chk.check("export does not contain plaintext passwords", all(p not in r.text(10 ** 9) for p in ("correct horse", "battery staple", "portable pass")),
              "no plaintext", [p for p in ("correct horse", "battery staple", "portable pass") if p in r.text(10 ** 9)], r.req, "§6")
    # snapshot is stable: later writes don't alter an earlier export
    s.book(tok_new, "r_anker", "t_3", ctx.D(ctx.fri, "18:00"))
    r_again = c.req("GET", "/_test/export")
    chk.check("export reflects later writes (new snapshot differs)", r_again.json != E, "different", None, None, "§10", soft=True)

    targets = [("same server", c)] + ([("fresh container", dest)] if dest else [])
    for label, d in targets:
        sd = S(d, chk, ctx)
        # destination has other data and credentials before import
        d.req("POST", "/_test/reset", fixture(ctx, extra_users=[{"id": "u_zed", "email": "zed@example.com", "password": "zed password", "display_name": "Zed"}]), timeout=12)
        zed = sd.login({"email": "zed@example.com", "password": "zed password"})
        sd.signup(email="ghost@example.com", password="ghost password")
        sd.book(zed, "r_anker", "t_3", ctx.D(ctx.thu, "21:00"))
        snapshot_before = d.req("GET", "/_test/export").json
        for name, body, st in (("{} (missing fields)", {}, 422), ("wrong track", {**E, "track": "other"}, 422),
                               ("format_version 2", {**E, "format_version": 2}, 422), ("format_version '1'", {**E, "format_version": "1"}, (400, 422)),
                               ("state missing", {"track": "tablekeeper", "format_version": 1}, 422),
                               ("state not object", {**E, "state": "x"}, (400, 422)), ("state {}", {**E, "state": {}}, 422),
                               ("state garbage", {**E, "state": {"zzz": [1, 2, 3]}}, 422)):
            rr = d.req("POST", "/_test/import", body, timeout=12)
            chk.expect(f"[{label}] import {name} -> {st}", rr, st, None, "§10")
            chk.check(f"[{label}] import {name}: destination unchanged", d.req("GET", "/_test/export").json == snapshot_before, "unchanged", None, rr.req, "§10")
        rr = d.req("POST", "/_test/import", raw='{"track": "tablekeeper", ', timeout=12)
        chk.expect(f"[{label}] import unparseable 400", rr, 400, "malformed_request", "§10")
        rr = d.req("POST", "/_test/import", E, timeout=12)
        chk.expect(f"[{label}] import export 204", rr, 204, section="§10")
        chk.expect(f"[{label}] previous destination token rejected", d.req("GET", "/reservations", token=zed), 401, "unauthenticated", "§10")
        chk.expect(f"[{label}] previous destination account gone", d.req("POST", "/auth/login", {"email": "zed@example.com", "password": "zed password"}),
                   401, "unauthenticated", "§10")
        chk.expect(f"[{label}] signup of an account that only existed at destination is free", d.req("POST", "/auth/signup", {"email": "ghost@example.com", "password": "ghost password", "display_name": "G"}),
                   201, section="§10")
        r = d.req("GET", "/reservations", token=tok_new)
        chk.check(f"[{label}] existing bearer token works; reservations identical", r.status == 200 and (r.json or {}).get("reservations") == list_src,
                  list_src, r.text(400), r.req, "§10")
        r = d.req("GET", "/reservations", token=s.ada)
        chk.check(f"[{label}] seeded user's token and reservations preserved", r.status == 200 and (r.json or {}).get("reservations") == ada_src, len(ada_src), r.text(200), r.req, "§10")
        r = d.req("POST", "/auth/login", {"email": "porter@example.com", "password": "portable pass"})
        chk.expect(f"[{label}] hashed-password login works after import", r, 200, section="§10")
        r = d.req("POST", "/auth/login", {"email": ADA["email"], "password": ADA["password"]})
        chk.expect(f"[{label}] seeded user login works after import", r, 200, section="§10")
        chk.check(f"[{label}] restaurant config preserved", d.req("GET", "/restaurants/r_anker").json == rest_src, rest_src, None, None, "§10")
        r = d.req("POST", "/reservations", b1, token=tok_new, key=k1)
        chk.check(f"[{label}] create replay after import 200 original body", r.status == 200 and r.json == r1.json, r1.json, r.text(400), r.req, "§10")
        chk.expect(f"[{label}] create key with different body still 409", d.req("POST", "/reservations", {**b1, "party_size": 1}, token=tok_new, key=k1), 409, "idempotency_key_reuse", "§10")
        r = d.req("POST", "/reservation-moves", {"moves": [{"reference": r1.json["reference"], "table_id": "t_1"}, {"reference": x2["reference"], "table_id": "t_2"}]}, token=tok_new, key=k2)
        chk.check(f"[{label}] batch replay after import 200 original body", r.status == 200 and r.json == rm.json, rm.json, r.text(400), r.req, "§10")
        r = d.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.fri, "18:00"), 2), token=tok_new, key=kfail)
        chk.expect(f"[{label}] failed key reusable after import 201", r, 201, section="§10")
        newref = (r.json or {}).get("reference")
        allrefs = {x.get("reference") for x in list_src + ada_src}
        chk.check(f"[{label}] new reference does not collide with imported ones", newref and newref not in allrefs, "fresh", newref, r.req, "§8/§10")
        chk.expect(f"[{label}] imported occupancy enforced (t_1 19:00 held by moved booking)",
                   d.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.thu, "19:00"), 2), token=s.bob, key=uuid.uuid4().hex), 409, "table_unavailable", "§10")
        # repeat import -> replacement, no duplicates
        d.req("POST", "/_test/import", E, timeout=12)
        rr = d.req("POST", "/_test/import", E, timeout=12)
        chk.expect(f"[{label}] repeated import 204", rr, 204, section="§10")
        r = d.req("GET", "/reservations", token=tok_new)
        chk.check(f"[{label}] repeated import restores exported state without duplicates", (r.json or {}).get("reservations") == list_src, list_src, r.text(300), r.req, "§10")
        # reset clears imported state
        d.req("POST", "/_test/reset", fixture(ctx), timeout=12)
        chk.expect(f"[{label}] reset after import clears imported tokens", d.req("GET", "/reservations", token=tok_new), 401, "unauthenticated", "§10")
        chk.expect(f"[{label}] reset after import clears imported accounts", d.req("POST", "/auth/login", {"email": "porter@example.com", "password": "portable pass"}),
                   401, "unauthenticated", "§10")

    # export is an atomic snapshot under concurrent writes
    s.reset()
    users = [s.signup() for _ in range(5)]
    plan = [plan_entry(c.base, "POST", "/reservations", booking_body("r_anker", ["t_1", "t_2", "t_3"][i % 3], ctx.D(ctx.fri, ["18:00", "19:30", "21:00"][i % 3]), 2), users[i % 5], f"snap-{i}")
            for i in range(30)] + [burst.Req("GET", c.base + "/_test/export") for _ in range(5)]
    rs = burst.fire(plan, timeout=12)
    exports = [r for r in rs[30:] if r.status == 200]
    chk.check("concurrent exports during writes all 200", len(exports) == 5, 5, [r.status for r in rs[30:]], {"burst": "export-under-write"}, "§10")
    if dest and exports:
        sd = S(dest, chk, ctx)
        for i, ex in enumerate(exports):
            rr = dest.req("POST", "/_test/import", raw=ex.body, timeout=12)
            chk.expect(f"export #{i} taken under load imports cleanly", rr, 204, section="§10")
            check_invariant(sd, users, f"export #{i} under load")


# --------------------------------------------------------------------------- main

GROUPS = ["core", "auth", "availability", "create", "reads", "cancel", "patch", "dst", "idem", "moves", "burst", "export"]


def wait_health(c: Client, seconds: float) -> float | None:
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        try:
            conn = http.client.HTTPConnection(c.host, c.port, timeout=2)
            conn.request("GET", "/health")
            r = conn.getresponse()
            r.read()
            conn.close()
            if r.status == 200:
                return round(time.monotonic() - start, 2)
        except Exception:
            pass
        time.sleep(0.25)
    return None


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--dest", help="second, fresh candidate container for export/import")
    p.add_argument("--groups", default=",".join(GROUPS))
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--out")
    p.add_argument("--wait", type=float, default=60.0)
    a = p.parse_args(argv)
    chk = Checker()
    ctx = Ctx()
    c = Client(a.base, chk, "A")
    dest = Client(a.dest, chk, "B") if a.dest else None
    meta = {"base": a.base, "dest": a.dest, "now": ctx.now.isoformat(), "now_tz": ctx.now_tz, "thu": str(ctx.thu),
            "near": ctx.near, "far": ctx.far, "past": ctx.past}
    for label, cl in (("A", c), ("B", dest)):
        if cl is not None:
            meta[f"health_{label}_s"] = wait_health(cl, a.wait)
            if meta[f"health_{label}_s"] is None:
                print(f"server {label} never became healthy", file=sys.stderr)
                return 2
    print(json.dumps(meta), flush=True)
    errors = []
    for g in [x for x in a.groups.split(",") if x]:
        chk.group = g
        print(f"== {g}", flush=True)
        s = S(c, chk, ctx)
        try:
            if g == "burst":
                g_burst(s, a.rounds)
            elif g == "export":
                g_export(s, dest)
            else:
                globals()[f"g_{g}"](s)
        except Exception as e:
            errors.append({"group": g, "error": repr(e), "trace": traceback.format_exc()[-1500:]})
            chk.check("group ran to completion", False, "no exception", repr(e), None, "harness")
    hard = [r for r in chk.results if not r["ok"] and not r["soft"]]
    soft = [r for r in chk.results if not r["ok"] and r["soft"]]
    per = {}
    for r in chk.results:
        g = per.setdefault(r["group"], {"pass": 0, "fail": 0, "soft": 0})
        g["pass" if r["ok"] else ("soft" if r["soft"] else "fail")] += 1
    summary = {"meta": meta, "checks": len(chk.results), "hard_failures": len(hard), "soft_failures": len(soft), "per_group": per,
               "errors": errors}
    if a.out:
        Path(a.out).write_text(json.dumps({**summary, "failures": hard, "soft": soft, "results": chk.results}, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
