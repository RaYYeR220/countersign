#!/usr/bin/env python3
"""Auditor attack battery for Tablekeeper stage 1 (written from the specification only).

Stdlib only. Runs inside a runner container on the same internal Docker network as the
candidate, because Docker Desktop does not forward ports of internal networks.

    python audit.py --base http://auditor-s4-a:8080 [--dest http://auditor-s4-b:8080] \
        [--groups core,auth,...] [--rounds 3] [--out /out/attacks.json]

Every check records: group, name, spec section, request, expected, actual. A "soft" check
covers a reading the specification leaves open; it is reported but does not fail the run.
Exit code: 0 when every hard check passed, 1 otherwise, 2 on harness error.
"""
from __future__ import annotations

import argparse
import copy
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
CLAUSE_RULES_S2 = {
    "combo": [(r"reset refuses fixture|reset accepts combinable", "C2.40,C2.43 (R-34)"), (r"^POST order:", "C2.47,C2.49,C2.50,C2.52,C2.53 (R-35)"),
              (r"available_options", "C2.44,C2.45"), (r"available_table_ids unchanged", "C2.44"), (r"without combinable", "C2.40,C2.44 (R-34)"),
              (r"^seeded", "C2.43 (R-34)"), (r"declared combinable order", "C2.48 (R-35)"),
              (r"^POST table_ids pair 201|^GET pair|^list entry", "C2.46,C2.48"), (r"occupies both|released at end", "C2.38,C2.45"),
              (r"^single on|pair sharing one", "C2.51"), (r"reverse order", "C2.40 (R-35)"),
              (r"table_ids with one member|table_id still accepted", "C2.39,C2.47,C2.48"), (r"^PATCH|after PATCH|old pair released", "C2.54 (R-36)"),
              (r"cancel frees", "C2.54"), (r"^moves|table_ids batch", "C2.58 (R-36)"), (r"not declared|undeclared", "C2.41,C2.49"),
              (r"three tables|four tables", "C2.50"), (r"duplicate", "C2.53 (R-35)"), (r"both table_id|both, consistent", "C2.47"),
              (r"summed capacity", "C2.42,C2.52"), (r"neither", "C2.46 (R-35)"), (r"a string|null|numbers", "C2.46,C1.34 (R-35)"),
              (r"empty", "C2.46 (R-35)"), (r"unknown table|another restaurant's", "C2.46,C1.88 (R-35)"),
              (r"replay|other order", "C2.46,C1.62,C1.63 (R-35)"), (r"import|export", "C2.38,C1.107"), (r".", "C2.38,C2.59")],
    "comboburst": [(r"K3 ", "C2.58,C2.59"), (r".", "C2.51,C2.59")],
    "upgrade": [(r"replay|retry", "C2.36,C1.62 (R-37)"), (r"table_ids", "C2.48 (R-37)"), (r"pairs|available_options", "C2.41,C2.44 (R-37)"),
                (r".", "C2.35,C2.36,C2.37 (R-37)")],
}
CLAUSE_RULES_S3 = {
    "explain": [(r"exactly the stage-2 fields|without explain", "C3.4 (R-46)"), (r"explain=|repeated explain|beats bad explain|before 404", "C3.3 (R-46)"),
                (r"every table once", "C3.5,C3.6,C3.7,C3.8"), (r"both false", "C3.2,C3.7"), (r"closed day|no available table", "C3.9"), (r".", "C3.2")],
    "policies": [(r"manager_user_ids|restaurant detail includes", "C3.18 (R-59)"), (r"without token|non-manager|unknown restaurant|another restaurant only|non-object",
                                                                              "C3.18,C3.19 (R-47)"),
                 (r"Idempotency-Key|replay|same key|first use failed|reusable", "C3.19,C1.62 (R-47)"), (r"invalid policy", "C3.23 (R-47)"),
                 (r"allocate no version", "C3.20,C3.23"), (r"exactly the policy fields|201: supplied|-> version", "C3.20 (R-48)"),
                 (r"GET policies|listed policies", "C3.24 (R-48)"), (r"restaurant detail still", "C3.25"),
                 (r"publication leaves|publication adds no history", "C3.29"), (r"^selection|capacity applies|hours apply", "C3.21,C3.22,C3.25"),
                 (r"pair capacity", "C3.49"), (r"availability follows", "C3.25,C3.4"), (r"explain names policy_version", "C3.26"),
                 (r"new booking carries|seeded booking", "C3.27,C3.28"), (r"real amendment|amendment validated|failed amendment", "C3.31,C3.33 (R-49)"),
                 (r"no-op", "C3.32 (R-58)"), (r"cancel still|checks the old accepted cutoff|cutoff-0|made after publication|inside the old", "C3.30,C3.31"),
                 (r".", "C3.18")],
    "history": [(r"created entry|seeded booking|cancelled seed", "C3.13,C3.28 (R-51)"), (r"envelope", "C3.10"), (r"replay records nothing", "C3.16"),
                (r"changed lists|history:", "C3.12,C3.14,C3.15 (R-58)"), (r"at order", "C3.12"), (r"keeps its history", "C3.10,C3.33"),
                (r"history read by|history of an unknown", "C3.10 (R-56)"), (r"decision", "C3.36 (R-56)"),
                (r"pair|reversed", "C3.50"), (r"old entries never", "C3.35,C3.29"), (r".", "C3.11")],
    "revision": [(r"replay keeps", "C3.28"), (r"cancel", "C3.33 (R-50)"), (r".", "C3.34 (R-49)")],
    "series": [(r"anchor history unchanged|occurrence 0 reservation", "C3.39"), (r"occurrence i on anchor|selects its date's policy", "C3.40"),
               (r"spring-forward|fall-back", "C3.41"), (r"first failing|outside the selected|collides|created nothing|anchor's history unchanged", "C3.41 (R-52)"),
               (r"adopt 201|exactly the R-53|series_id", "C3.42 (R-53)"), (r"references distinct", "C3.43"),
               (r"ordinary reservation list|occupy|created history", "C3.44"), (r"GET", "C3.45 (R-53)"),
               (r"exception|no-op and failed|cancel", "C3.46"), (r"replay|same key", "C3.47"),
               (r"count|interval|anchor_reference|missing|no token|Idempotency-Key", "C3.37 (R-52)"), (r".", "C3.38 (R-52)")],
    "s3moves": [(r"exception|series revision", "C3.51,C3.46 (R-57)"), (r".", "C3.51,C3.52 (R-57)")],
    "s3burst": [(r"R1 ", "C3.34 (R-49)"), (r"R2 ", "C3.20"), (r"R3 ", "C3.38 (R-52)"), (r"R4 ", "C3.12"), (r"R5 .*publications", "C3.19"),
                (r"R5 ", "C3.47"), (r".", "C3.1,C1.3")],
    "upgrade3": [(r"retry", "C3.28,C3.48 (R-55)"), (r".", "C3.48 (R-55)")],
}
CLAUSE_RULES = {
    "core": [(r"^health", "C1.11"), (r"R-8|R-20|reset rejects|reset accepts", "C1.12,C1.18,C1.26 (R-8,R-20)"),
             (r"reset", "C1.12,C1.13"), (r"R-25", "C1.32,C1.38 (R-25)"), (r"405|unknown path", "C1.32,C1.38 (R-9)"),
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
    if isinstance(section, str) and section.startswith(("C1.", "C2.", "C3.")):
        return section
    for pat, ids in CROSS_RULES + CLAUSE_RULES_S3.get(group, []) + CLAUSE_RULES_S2.get(group, []) + CLAUSE_RULES.get(group, []):
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
        tables = r.get("table_ids") if isinstance(r.get("table_ids"), list) else [r.get("table_id")]
        for t in tables:   # a combination occupies every member table (stage 2)
            by_table.setdefault((r.get("restaurant_id"), t), []).append((a, b, r.get("reference")))
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
    # R-25: only exact documented routes match; no normalisation, empty {reference}/{id} never matched
    for m, p, tok in (("GET", "/reservations/", s.ada), ("PATCH", "/reservations/", s.ada), ("GET", "/restaurants/", None),
                      ("GET", "//restaurants", None), ("GET", "/restaurants//r_anker", None), ("GET", "/health/", None),
                      ("POST", "/reservations//cancel", s.ada), ("GET", "/reservations/SEED01/", s.ada),
                      ("GET", "/availability/", None), ("POST", "/auth//login", None)):
        chk.expect(f"R-25 non-exact path {m} {p} -> 404", c.req(m, p, {} if m in ("POST", "PATCH") else NO_BODY, token=tok), 404, "not_found")
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
        ("timezone 'Local' (not an IANA name)", variant(lambda fx: R0(fx).update(timezone="Local")), 422),
        ("timezone empty", variant(lambda fx: R0(fx).update(timezone="")), 422),
        ("opens not HH:MM", variant(lambda fx: R0(fx)["opening_hours"][0].update(opens="6pm")), 422),
        ("weekday not mon..sun", variant(lambda fx: R0(fx)["opening_hours"][0].update(weekday="thursday")), 422),
        ("closes not later than opens", variant(lambda fx: R0(fx)["opening_hours"][0].update(closes="17:00")), 422),
        ("closes equal to opens", variant(lambda fx: R0(fx)["opening_hours"][0].update(closes="18:00")), 422),
        ("table capacity 0", variant(lambda fx: R0(fx)["tables"][0].update(capacity=0)), 422),
        ("slot_minutes 0", variant(lambda fx: R0(fx).update(slot_minutes=0)), 422),
        ("duplicate weekday", variant(lambda fx: R0(fx)["opening_hours"].append({"weekday": "thu", "opens": "11:00", "closes": "14:00"})), 422),
        ("duplicate user id", variant(lambda fx: fx["users"].append({**fx["users"][0], "email": "x@example.com"})), 422),
        ("duplicate email (case-insensitive)", variant(lambda fx: fx["users"].append({**fx["users"][0], "id": "u_x", "email": "ADA@example.com"})), 422),
        ("duplicate restaurant id", variant(lambda fx: fx["restaurants"].append({**fx["restaurants"][1], "id": "r_anker"})), 422),
        ("duplicate table id in a restaurant", variant(lambda fx: R0(fx)["tables"].append({"id": "t_1", "label": "x", "capacity": 2})), 422),
        ("duplicate reservation id", variant(lambda fx: fx["reservations"].append({**fx["reservations"][0], "reference": "SEEDXX"})), 422),
        ("duplicate reference", variant(lambda fx: fx["reservations"].append({**fx["reservations"][0], "id": "s_x"})), 422),
        ("seeded reference too short ('x')", variant(lambda fx: fx["reservations"][0].update(reference="X")), 422),
        ("seeded reference lowercase", variant(lambda fx: fx["reservations"][0].update(reference="seed01")), 422),
        ("seeded reference over 12 chars", variant(lambda fx: fx["reservations"][0].update(reference="ABCDEFGHJKLMN")), 422),
        ("seeded reference with a dash", variant(lambda fx: fx["reservations"][0].update(reference="SEED-01")), 422),
        ("reservation of unknown user",variant(lambda fx: fx["reservations"][0].update(user_id="u_ghost")), 422),
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
    for name, b in (("moves null", {"moves": None}), ("reference null", {"moves": [{"reference": None}]}),
                    ("moves item null", {"moves": [None]}), ("moves item null after a valid item", {"moves": [{"reference": "SEED01"}, None]})):
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
        # tampered export: a second reservation takes SEED01's reference (every dict carrying the
        # reference is changed, so the edit cannot land only in an opaque stored response)
        T = json.loads(json.dumps(E))
        other_ref = r1.json.get("reference") if isinstance(r1.json, dict) else None

        def retag(v):
            if isinstance(v, dict):
                if v.get("reference") == other_ref:
                    v["reference"] = "SEED01"
                for x in v.values():
                    retag(x)
            elif isinstance(v, list):
                for x in v:
                    retag(x)
        retag(T.get("state"))
        rr = d.req("POST", "/_test/import", T, timeout=12)
        chk.expect(f"[{label}] import tampered export (duplicate reference) -> 422", rr, 422, "validation_failed", "§10")
        chk.check(f"[{label}] import tampered export: destination unchanged", d.req("GET", "/_test/export").json == snapshot_before,
                  "unchanged", None, rr.req, "§10")
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


# --------------------------------------------------------------------------- stage 2: combined tables

COMBO_TABLES = [{"id": "c_1", "label": "Window", "capacity": 2}, {"id": "c_2", "label": "Booth", "capacity": 4},
                {"id": "c_3", "label": "Terrace", "capacity": 4}, {"id": "c_4", "label": "Garden", "capacity": 6}]
COMBINABLE = [["c_2", "c_1"], ["c_2", "c_3"], ["c_3", "c_4"]]   # first pair deliberately not in fixture order


def fixture2(ctx: Ctx) -> dict:
    all_days = [{"weekday": w, "opens": "12:00", "closes": "23:00"} for w in WEEKDAYS]
    D, D2 = ctx.thu, ctx.thu + timedelta(days=7)
    return {
        "users": [ADA, BOB],
        "restaurants": [
            {"id": "r_combo", "name": "Long Table", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120, "opening_hours": all_days,
             "tables": COMBO_TABLES, "combinable": COMBINABLE},
            {"id": "r_solo", "name": "Solo", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120, "opening_hours": all_days,
             "tables": [{"id": "s_1", "label": "Only", "capacity": 4}]},          # no "combinable" key (stage-1 shape)
        ],
        "reservations": [
            {"id": "s_c1", "reference": "COMBO1", "user_id": "u_ada", "restaurant_id": "r_combo", "table_ids": ["c_3", "c_4"],
             "starts_at_local": f"{D}T13:00", "party_size": 8},
            {"id": "s_c2", "reference": "CANCL1", "user_id": "u_ada", "restaurant_id": "r_combo", "table_id": "c_1",
             "starts_at_local": f"{D}T19:00", "party_size": 2, "status": "cancelled"},
            {"id": "s_c3", "reference": "SINGL1", "user_id": "u_bob", "restaurant_id": "r_combo", "table_ids": ["c_1"],
             "starts_at_local": f"{D2}T12:00", "party_size": 2},
        ],
    }


def body2(rid, tids, start, party):
    return {"restaurant_id": rid, "table_ids": tids, "starts_at_local": start, "party_size": party}


def opts(slot):
    return [(tuple(o.get("table_ids") or []), o.get("capacity")) for o in (slot or {}).get("available_options", [])]


ALL_OPTS = [(("c_1",), 2), (("c_2",), 4), (("c_3",), 4), (("c_4",), 6), (("c_2", "c_1"), 6), (("c_2", "c_3"), 8), (("c_3", "c_4"), 10)]


def _slot(s: S, rid, d, party, hhmm):
    r = s.avail(rid, d, party)
    for x in (r.json or {}).get("slots", []) if r.status == 200 else []:
        if x.get("starts_at_local") == f"{d}T{hhmm}":
            return x, r
    return None, r


def _filter(party, free):
    return [o for o in ALL_OPTS if o[1] >= party and all(t in free for t in o[0])]


def g_combo(s: S, dest: Client | None = None):
    c, chk, ctx = s.c, s.chk, s.ctx
    r = c.req("POST", "/_test/reset", fixture2(ctx), timeout=12)
    chk.expect("reset accepts combinable pairs, seeded table_ids and status", r, 204, section="S2 Model")
    if r.status != 204:
        return
    s.ada, s.bob = s.login(ADA), s.login(BOB)
    D, D2, D3 = ctx.thu, ctx.thu + timedelta(days=7), ctx.thu + timedelta(days=14)
    ALL = {"c_1", "c_2", "c_3", "c_4"}

    def post(body, tok=None, key=None):
        return c.req("POST", "/reservations", body, token=tok or s.ada, key=key or uuid.uuid4().hex)

    # R-35: reset refuses malformed combinable / seeds with 422 and leaves state unchanged
    def fx_with(fn):
        fx = json.loads(json.dumps(fixture2(ctx)))
        fn(fx)
        return fx
    RC = lambda fx: fx["restaurants"][0]  # noqa: E731
    for name, fx in (("combinable names an unknown table", fx_with(lambda f: RC(f)["combinable"].append(["c_1", "zz"]))),
                     ("combinable pair of one", fx_with(lambda f: RC(f)["combinable"].append(["c_4"]))),
                     ("combinable pair of three", fx_with(lambda f: RC(f)["combinable"].append(["c_1", "c_3", "c_4"]))),
                     ("table paired with itself", fx_with(lambda f: RC(f)["combinable"].append(["c_4", "c_4"]))),
                     ("duplicate pair, same order", fx_with(lambda f: RC(f)["combinable"].append(["c_2", "c_3"]))),
                     ("duplicate pair, other order", fx_with(lambda f: RC(f)["combinable"].append(["c_3", "c_2"]))),
                     ("seeded table_ids names an undeclared pair", fx_with(lambda f: f["reservations"][0].update(table_ids=["c_1", "c_4"]))),
                     ("seed with both table_id and table_ids", fx_with(lambda f: f["reservations"][0].update(table_id="c_3"))),
                     ("seed with neither table_id nor table_ids", fx_with(lambda f: f["reservations"][0].pop("table_ids"))),
                     ("seed status other than confirmed/cancelled", fx_with(lambda f: f["reservations"][1].update(status="pending"))),
                     ("seeded starts_at_local is not a calendar date", fx_with(lambda f: f["reservations"][0].update(starts_at_local="2027-02-30T13:00"))),
                     ("seeded starts_at_local malformed", fx_with(lambda f: f["reservations"][0].update(starts_at_local="2027-02-03 13:00")))):
        c.req("POST", "/_test/reset", fixture2(ctx), timeout=12)
        before = c.req("GET", "/_test/export").json
        rr = c.req("POST", "/_test/reset", fx, timeout=12)
        chk.expect(f"reset refuses fixture: {name} -> 422 (R-35)", rr, 422, "validation_failed", "S2 Model")
        chk.check(f"reset refuses fixture: {name}: state unchanged", c.req("GET", "/_test/export").json == before, "unchanged", None, rr.req, "S2 Model")
    c.req("POST", "/_test/reset", fixture2(ctx), timeout=12)
    s.ada, s.bob = s.login(ADA), s.login(BOB)

    # availability options
    for party, hhmm, free, why in ((2, "19:00", ALL, "cancelled seed does not block"), (7, "19:00", ALL, "pairs only above single capacity"),
                                   (5, "19:00", ALL, "capacity sum filters pairs"), (2, "13:00", {"c_1", "c_2"}, "seeded pair occupies both members"),
                                   (2, "12:00", {"c_1", "c_2"}, "overlap with seeded pair"), (2, "14:30", ALL, "half-open after seeded pair"),
                                   (11, "19:00", ALL, "no option fits"),
                                   (6, "19:00", ALL, "capacity exactly equal to the party (single and pair)"),
                                   (10, "19:00", ALL, "pair whose summed capacity equals the party"),
                                   (8, "19:00", ALL, "middle pair exactly at capacity")):
        sl, rr = _slot(s, "r_combo", D, party, hhmm)
        want = _filter(party, free)
        chk.check(f"available_options party {party} {hhmm}: {why}", sl is not None and opts(sl) == want, want, opts(sl), rr.req, "S2 API availability")
        want_ids = [t["id"] for t in COMBO_TABLES if t["capacity"] >= party and t["id"] in free]
        chk.check(f"available_table_ids unchanged (singles only) party {party} {hhmm}", sl is not None and sl.get("available_table_ids") == want_ids,
                  want_ids, (sl or {}).get("available_table_ids"), rr.req, "S2 API availability")
    sl, rr = _slot(s, "r_solo", D, 2, "19:00")
    chk.check("restaurant without combinable: options are singles only", sl is not None and opts(sl) == [(("s_1",), 4)], [(("s_1",), 4)], opts(sl), rr.req, "S2 Model")

    # seeded shapes
    r = s.get(s.ada, "COMBO1")
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("seeded table_ids: table_ids carried, table_id omitted", r.status == 200 and sorted(j.get("table_ids") or []) == ["c_3", "c_4"]
              and "table_id" not in j and j.get("status") == "confirmed", "pair, no table_id", r.text(300), r.req, "S2 Model")
    r = s.get(s.ada, "CANCL1")
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("seeded status cancelled kept; single carries table_id and table_ids", r.status == 200 and j.get("status") == "cancelled"
              and j.get("table_id") == "c_1" and j.get("table_ids") == ["c_1"], "cancelled c_1", r.text(300), r.req, "S2 Model")
    r = s.get(s.bob, "SINGL1")
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("seeded single given as table_ids: response has table_id", r.status == 200 and j.get("table_id") == "c_1" and j.get("table_ids") == ["c_1"],
              "c_1", r.text(300), r.req, "S2 Model")

    # create a pair
    r = post(body2("r_combo", ["c_2", "c_1"], f"{D}T19:00", 6))
    j = r.json if isinstance(r.json, dict) else {}
    st = resolve(f"{D}T19:00", "Europe/Berlin")
    chk.check("POST table_ids pair 201: table_ids set, table_id omitted, times", r.status == 201 and sorted(j.get("table_ids") or []) == ["c_1", "c_2"]
              and "table_id" not in j and same_instant_and_text(j.get("ends_at"), st + timedelta(minutes=90)), "201 pair", r.text(400), r.req, "S2 API POST")
    chk.check("pair response lists table_ids in declared combinable order (R-34)", j.get("table_ids") == ["c_2", "c_1"], ["c_2", "c_1"], j.get("table_ids"),
              r.req, "S2 API POST")
    pair_ref = j.get("reference")
    g = s.get(s.ada, pair_ref)
    chk.check("GET pair reservation equals create response", g.status == 200 and g.json == j, j, g.text(300), g.req, "S2 API POST")
    lst = [x for x in s.mine(s.ada) if x.get("reference") == pair_ref]
    chk.check("list entry for pair has the same shape", lst == [j], [j], lst, None, "S2 API POST")
    sl, rr = _slot(s, "r_combo", D, 2, "19:00")
    chk.check("pair occupies both tables: singles and every pair touching them gone", sl is not None and opts(sl) == _filter(2, {"c_3", "c_4"})
              and sl.get("available_table_ids") == ["c_3", "c_4"], _filter(2, {"c_3", "c_4"}), opts(sl), rr.req, "S2 Combined tables")
    sl, rr = _slot(s, "r_combo", D, 2, "20:30")
    chk.check("pair released at end (half-open)", sl is not None and opts(sl) == _filter(2, ALL), _filter(2, ALL), opts(sl), rr.req, "S2 Combined tables")
    chk.expect("single on a pair member, overlapping -> 409", post(booking_body("r_combo", "c_2", f"{D}T19:30", 2), tok=s.bob), 409, "table_unavailable", "S2 API POST")
    chk.expect("single on the other member -> 409", post(booking_body("r_combo", "c_1", f"{D}T20:00", 2), tok=s.bob), 409, "table_unavailable", "S2 API POST")
    chk.expect("pair sharing one taken member -> 409", post(body2("r_combo", ["c_2", "c_3"], f"{D}T19:00", 4), tok=s.bob), 409, "table_unavailable", "S2 API POST")
    r = post(body2("r_combo", ["c_3", "c_2"], f"{D}T21:00", 5))
    chk.check("pair given in reverse order is the same declared pair -> 201", r.status == 201 and sorted((r.json or {}).get("table_ids") or []) == ["c_2", "c_3"],
              201, r.text(300), r.req, "S2 Model")
    r = post(body2("r_combo", ["c_4"], f"{D}T21:00", 2))
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("table_ids with one member: response has table_id and table_ids", r.status == 201 and j.get("table_id") == "c_4" and j.get("table_ids") == ["c_4"],
              "c_4", r.text(300), r.req, "S2 API POST")
    r = post(booking_body("r_combo", "c_1", f"{D}T21:00", 2))
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("table_id still accepted: response carries table_ids [t]", r.status == 201 and j.get("table_id") == "c_1" and j.get("table_ids") == ["c_1"],
              "c_1", r.text(300), r.req, "S2 API POST")

    # error matrix
    T = f"{D2}T19:00"
    for name, body, st_, code, soft in (
            ("pair not declared (non-transitive c_1+c_3)", body2("r_combo", ["c_1", "c_3"], T, 2), 422, "combination_not_allowed", False),
            ("pair not declared (c_1+c_4)", body2("r_combo", ["c_1", "c_4"], T, 2), 422, "combination_not_allowed", False),
            ("three tables", body2("r_combo", ["c_1", "c_2", "c_3"], T, 2), 422, "combination_not_allowed", False),
            ("four tables", body2("r_combo", ["c_1", "c_2", "c_3", "c_4"], T, 2), 422, "combination_not_allowed", False),
            ("duplicate table id", body2("r_combo", ["c_2", "c_2"], T, 2), 422, "validation_failed", False),
            ("both table_id and table_ids", {**body2("r_combo", ["c_2", "c_1"], T, 2), "table_id": "c_2"}, 422, "validation_failed", False),
            ("both, consistent single", {**body2("r_combo", ["c_2"], T, 2), "table_id": "c_2"}, 422, "validation_failed", False),
            ("party exceeds summed capacity", body2("r_combo", ["c_2", "c_3"], T, 9), 422, "party_exceeds_capacity", False),
            ("neither table_id nor table_ids", {"restaurant_id": "r_combo", "starts_at_local": T, "party_size": 2}, 422, "validation_failed", False),
            ("table_ids a string", body2("r_combo", "c_1", T, 2), 400, "malformed_request", False),
            ("table_ids null", body2("r_combo", None, T, 2), 400, "malformed_request", False),
            ("table_ids numbers", body2("r_combo", [1, 2], T, 2), 400, "malformed_request", False),
            ("table_ids with a null item", body2("r_combo", ["c_2", None], T, 2), 400, "malformed_request", False),
            ("table_ids empty", body2("r_combo", [], T, 2), 422, "validation_failed", False),
            ("pair with an unknown table", body2("r_combo", ["c_2", "zz"], T, 2), 404, "not_found", False),
            ("pair with another restaurant's table", body2("r_combo", ["c_2", "s_1"], T, 2), 404, "not_found", False),
            ("duplicate on a restaurant without combinable", body2("r_solo", ["s_1", "s_1"], T, 2), 422, "validation_failed", False),
            # R-34 order: both → [] → duplicate → >2 → 404 → undeclared → time rules → capacity → 409
            ("order: both fields beat []", {**body2("r_combo", [], T, 2), "table_id": "c_1"}, 422, "validation_failed", False),
            ("order: >2 ids beat unknown table", body2("r_combo", ["c_1", "c_2", "zz"], T, 2), 422, "combination_not_allowed", False),
            ("order: duplicate beats >2 ids", body2("r_combo", ["c_1", "c_1", "c_2"], T, 2), 422, "validation_failed", False),
            ("order: unknown table beats undeclared pair", body2("r_combo", ["c_1", "zz"], T, 2), 404, "not_found", False),
            ("order: undeclared pair beats off-grid time", body2("r_combo", ["c_1", "c_3"], f"{D2}T19:10", 2), 422, "combination_not_allowed", False),
            ("order: time rules beat summed capacity", body2("r_combo", ["c_2", "c_1"], f"{D2}T19:10", 9), 422, "not_on_slot_grid", False),
            ("order: capacity beats 409", body2("r_combo", ["c_2", "c_3"], f"{D}T19:00", 9), 422, "party_exceeds_capacity", False)):
        chk.expect(f"POST {name} -> {st_} {code or ''}", post(body), st_, code, "S2 API POST", soft=soft)
    chk.expect("party equal to summed capacity -> 201", post(body2("r_combo", ["c_2", "c_3"], T, 8)), 201, section="S2 Model")
    # R-42: empty / over-64 ids are invalid values -> 422 before any 404 (POST, PATCH, move items)
    L65 = "x" * 65
    for name, body in (("restaurant_id ''", booking_body("", "c_1", T, 2)), ("table_id ''", booking_body("r_combo", "", T, 2)),
                       ("table_ids ['']", body2("r_combo", [""], T, 2)), ("table_ids ['', 'c_1']", body2("r_combo", ["", "c_1"], T, 2)),
                       ("restaurant_id 65 chars", booking_body(L65, "c_1", T, 2)), ("table_id 65 chars", booking_body("r_combo", L65, T, 2)),
                       ("table_ids member 65 chars", body2("r_combo", ["c_1", L65], T, 2)),
                       ("order: empty member beats duplicate", body2("r_combo", ["", ""], T, 2))):
        chk.expect(f"R-42 POST {name} -> 422 validation_failed", post(body), 422, "validation_failed", "C2.46,C1.41 (R-42)")
    for name, body, st_, code in (("both fields, table_ids an object", {**body2("r_combo", {"id": "c_1"}, T, 2), "table_id": "c_1"}, 422, "validation_failed"),
                                  ("both fields, table_id a number", {**body2("r_combo", ["c_1"], T, 2), "table_id": 5}, 422, "validation_failed"),
                                  ("both fields, table_id null", {**body2("r_combo", ["c_1"], T, 2), "table_id": None}, 422, "validation_failed"),
                                  ("both fields, table_ids null", {**body2("r_combo", None, T, 2), "table_id": "c_1"}, 422, "validation_failed"),
                                  ("only table_ids, an object", body2("r_combo", {"id": "c_1"}, T, 2), 400, "malformed_request"),
                                  ("only table_id, a number", booking_body("r_combo", 5, T, 2), 400, "malformed_request")):
        chk.expect(f"R-43 POST {name} -> {st_} {code}", post(body), st_, code, "C2.47 (R-43)")
    chk.expect("R-42 order: empty set beats empty member", post({**body2("r_combo", [], T, 2)}), 422, "validation_failed", "C2.46 (R-42)")
    pe = s.book(s.ada, "r_combo", "c_4", f"{D3}T21:30", 2)
    for name, b_ in (("table_id ''", {"table_id": ""}), ("table_ids ['c_2', '']", {"table_ids": ["c_2", ""]}), ("table_id 65 chars", {"table_id": L65})):
        chk.expect(f"R-42 PATCH {name} -> 422 validation_failed", c.req("PATCH", f"/reservations/{pe['reference']}", b_, token=s.ada), 422,
                   "validation_failed", "C2.54 (R-42)")
    for name, b_ in (("both fields, table_id a number", {"table_id": 5, "table_ids": ["c_4"]}), ("both fields, table_id null", {"table_id": None, "table_ids": ["c_4"]}),
                     ("both fields, table_ids an object", {"table_id": "c_4", "table_ids": {"x": 1}})):
        chk.expect(f"R-43 PATCH {name} -> 422 validation_failed", c.req("PATCH", f"/reservations/{pe['reference']}", b_, token=s.ada), 422,
                   "validation_failed", "C2.54 (R-43)")
    for name, item in (("both fields, table_id a number", {"reference": pe["reference"], "table_id": 5, "table_ids": ["c_4"]}),
                       ("both fields, table_ids an object", {"reference": pe["reference"], "table_id": "c_4", "table_ids": {"x": 1}})):
        chk.expect(f"R-43 move {name} -> 422 validation_failed", c.req("POST", "/reservation-moves", {"moves": [item]}, token=s.ada, key=uuid.uuid4().hex),
                   422, "validation_failed", "C2.58 (R-43)")
    # R-44: both fields -> 422 where the type pass runs: before the 404 ownership check (PATCH) / per-item 404 (moves)
    for name, path, tok, b_ in (("PATCH another user's booking", "/reservations/COMBO1", s.bob, {"table_id": "c_3", "table_ids": ["c_3", "c_4"]}),
                                ("PATCH unknown reference", "/reservations/ZZZZZZ", s.ada, {"table_id": "c_3", "table_ids": ["c_3"]})):
        chk.expect(f"R-44 {name} with both fields -> 422 before 404", c.req("PATCH", path, b_, token=tok), 422, "validation_failed", "C2.54 (R-44)")
    for name, moves in (("move item on another user's booking", [{"reference": "COMBO1", "table_id": "c_3", "table_ids": ["c_3"]}]),
                        ("unknown reference first, both fields in item 2", [{"reference": "ZZZZZZ"}, {"reference": pe["reference"], "table_id": "c_4", "table_ids": ["c_4"]}])):
        chk.expect(f"R-44 {name} -> 422 (step b before per-item 404)", c.req("POST", "/reservation-moves", {"moves": moves}, token=s.bob if "another" in name else s.ada,
                   key=uuid.uuid4().hex), 422, "validation_failed", "C2.58 (R-44)")
    for name, item in (("move table_id ''", {"reference": pe["reference"], "table_id": ""}),
                       ("move table_ids ['']", {"reference": pe["reference"], "table_ids": [""]}),
                       ("move reference ''", {"reference": ""})):
        chk.expect(f"R-42 {name} -> 422 validation_failed", c.req("POST", "/reservation-moves", {"moves": [item]}, token=s.ada, key=uuid.uuid4().hex),
                   422, "validation_failed", "C2.58 (R-42)")

    # idempotency with table_ids
    k = "pair-" + uuid.uuid4().hex
    b = body2("r_combo", ["c_3", "c_4"], f"{D2}T21:00", 7)
    r1 = post(b, key=k)
    r2 = post(b, key=k)
    chk.check("pair booking replay 200 identical", r1.status == 201 and r2.status == 200 and r2.json == r1.json, 200, r2.text(200), r2.req, "S2 API POST/§7")
    r3 = post({**b, "table_ids": ["c_4", "c_3"]}, key=k)
    chk.expect("same key, pair listed in the other order (different JSON) -> 409 (R-35)", r3, 409, "idempotency_key_reuse", "§7")

    # PATCH with table_ids
    P = s.book(s.ada, "r_combo", "c_1", f"{D2}T15:00", 2)
    pref = P["reference"]

    def patch(b_, rf=None):
        return c.req("PATCH", f"/reservations/{rf or pref}", b_, token=s.ada)

    r = patch({"table_ids": ["c_2", "c_1"], "party_size": 5})
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("PATCH single -> pair 200: table_ids set, table_id omitted", r.status == 200 and sorted(j.get("table_ids") or []) == ["c_1", "c_2"]
              and "table_id" not in j and j.get("party_size") == 5 and j.get("reference") == pref, "pair", r.text(300), r.req, "S2 API PATCH")
    sl, rr = _slot(s, "r_combo", D2, 2, "15:00")
    chk.check("after PATCH both members occupied", sl is not None and "c_1" not in (sl.get("available_table_ids") or []) and "c_2" not in (sl.get("available_table_ids") or []),
              "c_1, c_2 absent", (sl or {}).get("available_table_ids"), rr.req, "S2 API PATCH")
    snap = s.get(s.ada, pref).json
    for name, b_, st_, code in (("undeclared pair", {"table_ids": ["c_1", "c_3"]}, 422, "combination_not_allowed"),
                                ("both table_id and table_ids", {"table_id": "c_4", "table_ids": ["c_4"]}, 422, "validation_failed"),
                                ("party over summed capacity", {"party_size": 7}, 422, "party_exceeds_capacity"),
                                ("duplicate ids", {"table_ids": ["c_2", "c_2"]}, 422, "validation_failed"),
                                ("three tables", {"table_ids": ["c_1", "c_2", "c_3"]}, 422, "combination_not_allowed"),
                                ("table_ids a string", {"table_ids": "c_2"}, 400, "malformed_request")):
        r = patch(b_)
        chk.expect(f"PATCH {name} -> {st_} {code}", r, st_, code, "S2 API PATCH")
        chk.check(f"PATCH {name}: booking unchanged", s.get(s.ada, pref).json == snap, snap, None, r.req, "S2 API PATCH")
    r = patch({"starts_at_local": f"{D2}T15:30"})
    chk.check("PATCH pair onto its own overlapping interval 200", r.status == 200 and sorted((r.json or {}).get("table_ids") or []) == ["c_1", "c_2"],
              200, r.text(200), r.req, "S2 API PATCH")
    r = patch({"table_id": "c_4"})
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("PATCH pair -> single via table_id: table_id back, table_ids [t]", r.status == 200 and j.get("table_id") == "c_4" and j.get("table_ids") == ["c_4"],
              "c_4", r.text(200), r.req, "S2 API PATCH")
    sl, rr = _slot(s, "r_combo", D2, 2, "15:30")
    chk.check("old pair released after PATCH to single", sl is not None and {"c_1", "c_2"} <= set(sl.get("available_table_ids") or []),
              "c_1, c_2 listed", (sl or {}).get("available_table_ids"), rr.req, "S2 API PATCH")

    # cancel frees every member
    Q = post(body2("r_combo", ["c_3", "c_4"], f"{D2}T17:00", 3)).json
    c.req("POST", f"/reservations/{Q['reference']}/cancel", token=s.ada)
    sl, rr = _slot(s, "r_combo", D2, 2, "17:00")
    chk.check("cancel frees every table in the set", sl is not None and ("c_3", "c_4") in [o[0] for o in opts(sl)]
              and {"c_3", "c_4"} <= set(sl.get("available_table_ids") or []), "c_3, c_4 and pair", opts(sl), rr.req, "S2 API cancel")

    # moves with table_ids
    A = s.book(s.ada, "r_combo", "c_1", f"{D3}T18:00", 2)
    B = post(body2("r_combo", ["c_3", "c_4"], f"{D3}T18:00", 2)).json

    def mv(moves, key=None):
        return c.req("POST", "/reservation-moves", {"moves": moves}, token=s.ada, key=key or uuid.uuid4().hex)

    r = mv([{"reference": A["reference"], "table_ids": ["c_3", "c_4"]}, {"reference": B["reference"], "table_ids": ["c_1"]}])
    rs = (r.json or {}).get("reservations") if isinstance(r.json, dict) else None
    ok = r.status == 201 and isinstance(rs, list) and len(rs) == 2 and sorted(rs[0].get("table_ids") or []) == ["c_3", "c_4"] \
        and "table_id" not in rs[0] and rs[1].get("table_id") == "c_1"
    chk.check("moves swap single <-> pair with table_ids 201", ok, "swap", r.text(400), r.req, "S2 moves")
    snapA, snapB = s.get(s.ada, A["reference"]).json, s.get(s.ada, B["reference"]).json
    r = mv([{"reference": A["reference"], "table_ids": ["c_2", "c_1"]}, {"reference": B["reference"], "table_ids": ["c_2", "c_3"]}])
    chk.expect("moves whose results share a table -> 409", r, 409, "table_unavailable", "S2 moves")
    chk.check("failed table_ids batch changed nothing", (s.get(s.ada, A["reference"]).json, s.get(s.ada, B["reference"]).json) == (snapA, snapB),
              "unchanged", None, r.req, "S2 moves")
    chk.expect("moves undeclared pair -> 422", mv([{"reference": A["reference"], "table_ids": ["c_1", "c_3"]}]), 422, "combination_not_allowed", "S2 moves")
    chk.expect("moves both table_id and table_ids -> 422", mv([{"reference": A["reference"], "table_id": "c_2", "table_ids": ["c_2"]}]), 422,
               "validation_failed", "S2 moves")
    chk.expect("moves table_ids a string -> 400", mv([{"reference": A["reference"], "table_ids": "c_2"}]), 400, "malformed_request", "S2 moves")

    # export -> import keeps pairs
    if dest is not None:
        E = c.req("GET", "/_test/export").json
        r = dest.req("POST", "/_test/import", E, timeout=12)
        chk.expect("import of an export holding pairs -> 204", r, 204, section="S2 Model/§10")
        g = dest.req("GET", f"/reservations/{pair_ref}", token=s.ada)
        chk.check("pair reservation survives export/import unchanged", g.status == 200 and g.json == s.get(s.ada, pair_ref).json, "identical",
                  g.text(200), g.req, "S2 Model/§10")
        sl1, _ = _slot(s, "r_combo", D, 2, "19:00")
        r = dest.req("GET", "/availability", query={"restaurant_id": "r_combo", "date": str(D), "party_size": 2})
        sl2 = next((x for x in (r.json or {}).get("slots", []) if x.get("starts_at_local") == f"{D}T19:00"), None)
        chk.check("available_options identical after import", opts(sl1) == opts(sl2), opts(sl1), opts(sl2), r.req, "S2 Model/§10")
    check_invariant(s, [s.ada, s.bob], "combo group", "S2 Combined tables")


def g_comboburst(s: S, rounds: int):
    c, chk, ctx = s.c, s.chk, s.ctx
    base = c.base
    for rnd in range(rounds):
        r = c.req("POST", "/_test/reset", fixture2(ctx), timeout=12)
        if r.status != 204:
            raise RuntimeError(f"reset fixture2 failed: {r.status}")
        s.ada, s.bob = s.login(ADA), s.login(BOB)
        users = [s.signup() for _ in range(12)]
        D = ctx.thu + timedelta(days=21)
        # K1: everything shares c_2
        kinds = [(["c_2", "c_1"], 5), (["c_2", "c_3"], 6), (["c_2"], 3)]
        rs = burst.fire([plan_entry(base, "POST", "/reservations", body2("r_combo", kinds[i % 3][0], f"{D}T20:00", kinds[i % 3][1]),
                                    users[i % 12], f"k1-{rnd}-{i}") for i in range(48)])
        st = statuses(rs)
        chk.check(f"r{rnd} K1 48 concurrent bookings all sharing c_2: exactly one 201", st.get("201") == 1 and st.get("409") == 47,
                  {"201": 1, "409": 47}, st, {"burst": "K1"}, "S2 Concurrent")
        # K2: two disjoint pairs contested
        rs = burst.fire([plan_entry(base, "POST", "/reservations", body2("r_combo", ["c_2", "c_1"] if i % 2 else ["c_3", "c_4"], f"{D}T14:00", 2),
                                    users[i % 12], f"k2-{rnd}-{i}") for i in range(40)])
        st = statuses(rs)
        chk.check(f"r{rnd} K2 two disjoint pairs, 20 bids each: exactly two 201", st.get("201") == 2 and st.get("409") == 38,
                  {"201": 2, "409": 38}, st, {"burst": "K2"}, "S2 Concurrent")
        # K3: single-item moves competing for one pair
        own = [s.book(users[i], "r_combo", "c_1", f"{ctx.thu + timedelta(days=28 + 7 * i)}T18:00", 2) for i in range(8)]
        rs = burst.fire([plan_entry(base, "POST", "/reservation-moves",
                                    {"moves": [{"reference": own[i]["reference"], "table_ids": ["c_2", "c_3"], "starts_at_local": f"{D}T17:00"}]},
                                    users[i], f"k3-{rnd}-{i}") for i in range(8)])
        st = statuses(rs)
        moved = sum(1 for i in range(8) if (s.get(users[i], own[i]["reference"]).json or {}).get("starts_at_local") == f"{D}T17:00")
        chk.check(f"r{rnd} K3 8 moves onto one pair: one 201, rest 409, exactly one booking moved", st == {"201": 1, "409": 7} and moved == 1,
                  {"201": 1, "409": 7, "moved": 1}, {"statuses": st, "moved": moved}, {"burst": "K3"}, "S2 Concurrent/§11")
        # K4: PATCHes onto overlapping pairs sharing c_3
        own = [s.book(users[i], "r_combo", "c_1", f"{ctx.thu + timedelta(days=84 + 7 * i)}T18:00", 2) for i in range(8)]
        rs = burst.fire([plan_entry(base, "PATCH", f"/reservations/{own[i]['reference']}",
                                    {"table_ids": ["c_2", "c_3"] if i % 2 else ["c_3", "c_4"], "starts_at_local": f"{D + timedelta(days=1)}T21:00"},
                                    users[i], None)
                         for i in range(8)])
        st = statuses(rs)
        chk.check(f"r{rnd} K4 8 PATCHes onto pairs sharing c_3: exactly one 200", st.get("200") == 1 and st.get("409") == 7,
                  {"200": 1, "409": 7}, st, {"burst": "K4"}, "S2 Concurrent")
        check_invariant(s, users + [s.ada, s.bob], f"r{rnd} after combination bursts", "S2 Concurrent")


def g_upgrade(s: S, prev: Client | None, dest: Client | None):
    """Export from the accepted previous-stage service, import into the candidate (S2 'Existing clients after an upgrade')."""
    c, chk, ctx = s.c, s.chk, s.ctx
    if prev is None:
        chk.check("upgrade source given (--prev)", False, "--prev URL of the previous stage image", None, None, "harness")
        return
    sp = S(prev, chk, ctx)
    sp.reset()
    tok = sp.signup(email="legacy@example.com", password="legacy pass 1", name="Legacy")
    k1, k2, k3, kf = ("up1-" + uuid.uuid4().hex, "up2-" + uuid.uuid4().hex, "up3-" + uuid.uuid4().hex, "upf-" + uuid.uuid4().hex)
    b1 = booking_body("r_anker", "t_2", ctx.D(ctx.thu, "19:00"), 2)
    b3 = booking_body("r_anker", "t_3", ctx.D(ctx.fri, "18:00"), 4)
    r1 = prev.req("POST", "/reservations", b1, token=tok, key=k1)
    x2 = sp.book(tok, "r_anker", "t_1", ctx.D(ctx.thu, "19:00"), 2)
    mb = {"moves": [{"reference": r1.json["reference"], "table_id": "t_1"}, {"reference": x2["reference"], "table_id": "t_2"}]}
    rm = prev.req("POST", "/reservation-moves", mb, token=tok, key=k2)
    r3 = prev.req("POST", "/reservations", b3, token=tok, key=k3)          # its response is "lost" by the client
    prev.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.fri, "21:00"), 9), token=tok, key=kf)
    prev.req("POST", f"/reservations/{x2['reference']}/cancel", token=tok)
    chk.check("upgrade setup on previous stage ok", r1.status == 201 and rm.status == 201 and r3.status == 201, 201,
              [r1.status, rm.status, r3.status], None, "harness")
    old_list = sp.mine(tok)
    E = prev.req("GET", "/_test/export").json
    c.req("POST", "/_test/reset", fixture2(ctx), timeout=12)
    r = c.req("POST", "/_test/import", E, timeout=12)
    chk.expect("candidate imports the previous stage's export -> 204", r, 204, section="S2 Upgrade")
    r = c.req("GET", "/reservations", token=tok)
    new_list = (r.json or {}).get("reservations", []) if r.status == 200 else None
    chk.expect("token issued by the previous stage still valid", r, 200, section="S2 Upgrade")
    keys1 = ("reservation_id", "reference", "restaurant_id", "table_id", "party_size", "status", "starts_at_local", "starts_at", "ends_at", "created_at")
    same = new_list is not None and [{k: x.get(k) for k in keys1} for x in new_list] == [{k: x.get(k) for k in keys1} for x in old_list]
    chk.check("reservations identical after upgrade (stage-1 fields)", same, len(old_list), r.text(300), r.req, "S2 Upgrade")
    chk.check("upgraded reservations carry table_ids [table_id]", new_list is not None and all(x.get("table_ids") == [x.get("table_id")] for x in new_list),
              "table_ids", [x.get("table_ids") for x in (new_list or [])][:3], r.req, "S2 Upgrade")
    g = c.req("GET", f"/reservations/{r1.json['reference']}", token=tok)
    chk.expect("lookup of a retained reference works after upgrade", g, 200, section="S2 Upgrade")
    rr = c.req("POST", "/reservations", b1, token=tok, key=k1)
    chk.check("replay of a pre-upgrade booking -> 200 with the original body", rr.status == 200 and rr.json == r1.json, r1.json, rr.text(300), rr.req, "S2 Upgrade/§7")
    rr = c.req("POST", "/reservations", b3, token=tok, key=k3)
    chk.check("booking whose response was lost before export: retry -> 200, original reference", rr.status == 200
              and (rr.json or {}).get("reference") == (r3.json or {}).get("reference"), r3.json, rr.text(300), rr.req, "S2 Upgrade")
    rr = c.req("POST", "/reservation-moves", mb, token=tok, key=k2)
    chk.check("batch receipt replay after upgrade -> 200 original", rr.status == 200 and rr.json == rm.json, rm.json, rr.text(300), rr.req, "S2 Upgrade/§11")
    chk.expect("pre-upgrade key with a different body -> 409", c.req("POST", "/reservations", {**b1, "party_size": 1}, token=tok, key=k1), 409,
               "idempotency_key_reuse", "S2 Upgrade")
    chk.expect("pre-upgrade failed key is reusable -> 201", c.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.fri, "21:00"), 2),
                                                                    token=tok, key=kf), 201, section="S2 Upgrade")
    chk.expect("hashed password login works after upgrade", c.req("POST", "/auth/login", {"email": "legacy@example.com", "password": "legacy pass 1"}),
               200, section="S2 Upgrade")
    r = c.req("POST", "/reservations", body2("r_anker", ["t_3"], ctx.D(ctx.thu, "21:00"), 2), token=tok, key=uuid.uuid4().hex)
    chk.check("new single booking with table_ids on an upgraded restaurant -> 201, fresh reference", r.status == 201
              and (r.json or {}).get("reference") not in {x.get("reference") for x in old_list}, 201, r.text(200), r.req, "S2 Upgrade")
    chk.expect("upgraded restaurant has no declared pairs -> 422 combination_not_allowed",
               c.req("POST", "/reservations", body2("r_anker", ["t_1", "t_2"], ctx.D(ctx.thu2, "18:00"), 2), token=tok, key=uuid.uuid4().hex),
               422, "combination_not_allowed", "S2 Upgrade")
    sl, rr = _slot(s, "r_anker", ctx.fri, 2, "18:00")
    chk.check("availability on upgraded restaurant has available_options (singles only)", sl is not None and all(len(o[0]) == 1 for o in opts(sl))
              and [o[0][0] for o in opts(sl)] == (sl or {}).get("available_table_ids"), "singles", opts(sl), rr.req, "S2 Upgrade")
    snap = c.req("GET", "/_test/export").json
    r = c.req("POST", "/_test/import", E, timeout=12)
    chk.expect("importing the previous-stage export again -> 204", r, 204, section="S2 Upgrade")
    r = c.req("GET", "/reservations", token=tok)
    chk.check("re-import restores the exported state (no duplicates)", [x.get("reference") for x in (r.json or {}).get("reservations", [])]
              == [x.get("reference") for x in old_list], len(old_list), r.text(200), r.req, "S2 Upgrade")
    if dest is not None:
        r = dest.req("POST", "/_test/import", snap, timeout=12)
        chk.expect("current-format export (after upgrade) imports into a fresh candidate", r, 204, section="S2 Upgrade")


# --------------------------------------------------------------------------- stage 3: policies, history, series

MGR = {"id": "u_mgr", "email": "mgr@example.com", "password": "manager pass", "display_name": "Manager"}
POL_TABLES = [{"id": "p_1", "label": "Nook", "capacity": 2}, {"id": "p_2", "label": "Bay", "capacity": 4},
              {"id": "p_3", "label": "Hall", "capacity": 6}]


def _hours(o, c):
    return [{"weekday": w, "opens": o, "closes": c} for w in WEEKDAYS]


def fixture3(ctx: Ctx) -> dict:
    D = ctx.thu
    return {
        "users": [ADA, BOB, MGR],
        "restaurants": [
            {"id": "r_pol", "name": "Policy House", "timezone": "Europe/Berlin", "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 120, "opening_hours": _hours("12:00", "23:00"), "tables": POL_TABLES,
             "combinable": [["p_1", "p_2"]], "manager_user_ids": ["u_mgr"]},
            {"id": "r_ser", "name": "Weekly Table", "timezone": "Europe/Berlin", "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 120, "opening_hours": _hours("12:00", "23:00"),
             "tables": [{"id": "s_1", "label": "Ess", "capacity": 2}, {"id": "s_2", "label": "Zwei", "capacity": 4}],
             "combinable": [["s_1", "s_2"]], "manager_user_ids": ["u_mgr"]},
            {"id": "r_near3", "name": "Corner Now", "timezone": ctx.now_tz, "slot_minutes": 15, "reservation_duration_minutes": 30,
             "cancellation_cutoff_minutes": 120, "opening_hours": _hours("00:00", "23:45"),
             "tables": [{"id": "q_1", "label": "Quick", "capacity": 4}, {"id": "q_2", "label": "Quicker", "capacity": 4}],
             "manager_user_ids": ["u_mgr"]},
            {"id": "r_dst3", "name": "Night Owl", "timezone": "Europe/Berlin", "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 120, "opening_hours": _hours("00:00", "06:00"),
             "tables": [{"id": "d_1", "label": "Dawn", "capacity": 4}]},
            {"id": "r_sat", "name": "Saturday Club", "timezone": "Europe/Berlin", "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 120, "opening_hours": [{"weekday": "sat", "opens": "18:00", "closes": "22:00"}],
             "tables": [{"id": "z_1", "label": "Zed", "capacity": 4}]},
        ],
        "reservations": [
            {"id": "s_p1", "reference": "SEEDP1", "user_id": "u_ada", "restaurant_id": "r_pol", "table_id": "p_3",
             "starts_at_local": f"{D + timedelta(days=3)}T18:00", "party_size": 4},
            {"id": "s_c1", "reference": "SEEDC1", "user_id": "u_ada", "restaurant_id": "r_pol", "table_id": "p_1",
             "starts_at_local": f"{D + timedelta(days=3)}T18:00", "party_size": 2, "status": "cancelled"},
        ],
    }


def pol(eff, slot=30, dur=90, cut=120, opens="12:00", closes="23:00", caps=None):
    return {"effective_from": str(eff), "slot_minutes": slot, "reservation_duration_minutes": dur, "cancellation_cutoff_minutes": cut,
            "opening_hours": _hours(opens, closes), "capacities": caps or {"p_1": 2, "p_2": 4, "p_3": 6}}


def terms_of(version, p):
    return {"policy_version": version, **{k: p[k] for k in ("slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes",
                                                              "opening_hours", "capacities")}}


T0_POL = {"policy_version": 0, "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
          "opening_hours": _hours("12:00", "23:00"), "capacities": {"p_1": 2, "p_2": 4, "p_3": 6}}


def _norm_terms(t):
    if not isinstance(t, dict):
        return t
    t = dict(t)
    if isinstance(t.get("opening_hours"), list):
        t["opening_hours"] = sorted(((h.get("weekday"), h.get("opens"), h.get("closes")) for h in t["opening_hours"] if isinstance(h, dict)))
    return t


def terms_eq(a, b):
    return _norm_terms(a) == _norm_terms(b)


class S3:
    """Stage-3 helpers on top of S (manager token, publication, history)."""

    def __init__(self, s: S):
        self.s, self.c, self.chk, self.ctx = s, s.c, s.chk, s.ctx

    def reset(self):
        r = self.c.req("POST", "/_test/reset", fixture3(self.ctx), timeout=12)
        if r.status != 204:
            raise RuntimeError(f"reset fixture3 failed: {r.status} {r.text(300)}")
        self.s.ada, self.s.bob, self.mgr = self.s.login(ADA), self.s.login(BOB), self.s.login(MGR)

    def publish(self, rid, body, key=None, tok=None):
        return self.c.req("POST", f"/restaurants/{rid}/policies", body, token=tok or self.mgr, key=key or uuid.uuid4().hex)

    def history(self, ref, tok):
        return self.c.req("GET", f"/reservations/{ref}/history", token=tok)

    def entries(self, ref, tok):
        r = self.history(ref, tok)
        return (r.json or {}).get("entries") if r.status == 200 and isinstance(r.json, dict) else None

    def standard_policies(self):
        """v1..v4 on r_pol: publication order differs from effective order; v3 ties v1's date; v4 is in the past."""
        D = self.ctx.thu
        ps = [pol(D + timedelta(days=7), 30, 120, 60, "12:00", "22:00", {"p_1": 3, "p_2": 4, "p_3": 6}),
              pol(D + timedelta(days=14), 15, 60, 30, "10:00", "20:00", {"p_1": 2, "p_2": 2, "p_3": 8}),
              pol(D + timedelta(days=7), 30, 100, 90, "12:00", "23:00", {"p_1": 5, "p_2": 5, "p_3": 5}),
              pol(D - timedelta(days=60), 30, 90, 120, "12:00", "23:00", {"p_1": 2, "p_2": 3, "p_3": 6})]
        out = []
        for p in ps:
            r = self.publish("r_pol", p)
            if r.status != 201:
                raise RuntimeError(f"publish failed: {r.status} {r.text(300)}")
            out.append(p)
        return out


def g_explain(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s3 = S3(s)
    s3.reset()
    D = ctx.thu
    s.book(s.ada, "r_pol", "p_2", f"{D}T19:00", 2)
    s.book(s.bob, "r_pol", "p_1", f"{D}T19:00", 2)

    def av(q):
        return c.req("GET", "/availability", query=q)

    base = {"restaurant_id": "r_pol", "date": str(D), "party_size": "3"}
    r = av(base)
    sl = (r.json or {}).get("slots", []) if r.status == 200 else []
    chk.check("without explain the response keeps stage 1's shape (no explain field)", r.status == 200 and sl and all("explain" not in x for x in sl),
              "no explain", [sorted(x) for x in sl[:1]], r.req, "S3 Explanations")
    r = av({**base, "explain": "true"})
    sl = (r.json or {}).get("slots", []) if r.status == 200 else []
    bad = []
    for x in sl:
        start = resolve(x["starts_at_local"], "Europe/Berlin")
        ex = x.get("explain")
        if not isinstance(ex, list) or [e.get("table_id") for e in ex] != ["p_1", "p_2", "p_3"]:
            bad.append((x["starts_at_local"], "tables", ex))
            continue
        for e in ex:
            cap = {"p_1": 2, "p_2": 4, "p_3": 6}[e["table_id"]] >= 3
            busy = e["table_id"] in ("p_1", "p_2") and start < resolve(f"{D}T20:30", "Europe/Berlin") and \
                start + timedelta(minutes=90) > resolve(f"{D}T19:00", "Europe/Berlin")
            want = {"table_id": e["table_id"], "policy_version": 0, "available": cap and not busy,
                    "rules": [{"rule": "capacity", "holds": cap}, {"rule": "no_overlap", "holds": not busy}]}
            if {k: e.get(k) for k in want} != want:
                bad.append((x["starts_at_local"], want, e))
        if [e["table_id"] for e in ex if e.get("available")] != x.get("available_table_ids"):
            bad.append((x["starts_at_local"], "available ids", x.get("available_table_ids")))
    chk.check("explain=true: every table once in fixture order, both rules in order, available iff both hold, ids == available_table_ids",
              bool(sl) and not bad, "consistent explanations", bad[:4], r.req, "S3 Explanations")
    both_false = next((e for x in sl if x["starts_at_local"].endswith("19:00") for e in x.get("explain", []) if e.get("table_id") == "p_1"), None)
    chk.check("a table failing both rules reports both false", both_false is not None and [rr.get("holds") for rr in both_false.get("rules", [])] == [False, False],
              [False, False], both_false, r.req, "S3 Explanations")
    r = av({**base, "party_size": "7", "explain": "true"})
    sl = (r.json or {}).get("slots", []) if r.status == 200 else []
    chk.check("slot with no available table still appears with a full explain", bool(sl) and all(x.get("available_table_ids") == [] and len(x.get("explain") or []) == 3
              for x in sl), "3 explanations, none available", sl[:1], r.req, "S3 Explanations")
    r = av({"restaurant_id": "r_sat", "date": str(D), "party_size": "2", "explain": "true"})
    chk.check("closed day with explain=true still returns slots []", r.status == 200 and (r.json or {}).get("slots") == [], [], r.text(200), r.req, "S3 Explanations")
    for v in ("false", "1", "", "TRUE", "yes", "True"):
        chk.expect(f"explain={v!r} -> 422", av({**base, "explain": v}), 422, "validation_failed", "S3 Explanations")
    chk.expect("invalid explain with unknown restaurant -> 422 before 404 (R-46)", av({**base, "restaurant_id": "nope", "explain": "no"}), 422,
               "validation_failed", "S3 Explanations")
    chk.expect("bad party_size beats bad explain (R-46 after R-12)", av({**base, "party_size": "0", "explain": "no"}), 422, "validation_failed", "S3 Explanations")
    r = c.req("GET", "/availability", query=f"restaurant_id=r_pol&date={D}&party_size=3&explain=true&explain=no")
    chk.check("repeated explain: first value counts (true, then no -> 200 with explain) (R-46)", r.status == 200
              and all("explain" in x for x in (r.json or {}).get("slots", [])), 200, r.status, r.req, "S3 Explanations")
    chk.expect("repeated explain: first value counts (no, then true -> 422) (R-46)",
               c.req("GET", "/availability", query=f"restaurant_id=r_pol&date={D}&party_size=3&explain=no&explain=true"), 422, "validation_failed",
               "S3 Explanations")
    r = av(base)
    keys = {k for x in (r.json or {}).get("slots", []) for k in x}
    chk.check("without explain slots carry exactly the stage-2 fields (R-46)", keys == {"starts_at_local", "starts_at", "available_table_ids", "available_options"},
              ["starts_at_local", "starts_at", "available_table_ids", "available_options"], sorted(keys), r.req, "S3 Explanations")


def g_policies(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s3 = S3(s)
    for name, val, st_ in (("not an array", "u_mgr", 400), ("member not a string", [5], 400), ("empty id", [""], 422), ("65-char id", ["u" * 65], 422),
                           ("not a fixture user", ["u_ghost"], 422), ("duplicate id", ["u_mgr", "u_mgr"], 422)):
        s3.reset()
        before = c.req("GET", "/_test/export").json
        fx = fixture3(ctx)
        fx["restaurants"][0]["manager_user_ids"] = val
        r = c.req("POST", "/_test/reset", fx, timeout=12)
        chk.expect(f"reset refuses manager_user_ids {name} -> {st_} (R-59)", r, st_, "malformed_request" if st_ == 400 else "validation_failed", "S3 Policies")
        chk.check(f"reset refuses manager_user_ids {name}: state unchanged", c.req("GET", "/_test/export").json == before, "unchanged", None, r.req, "S3 Policies")
    fx = fixture3(ctx)
    fx["restaurants"][0].pop("manager_user_ids")
    chk.expect("manager_user_ids is optional (absent -> [])", c.req("POST", "/_test/reset", fx, timeout=12), 204, section="S3 Policies")
    s3.reset()
    D = ctx.thu
    D14 = D + timedelta(days=14)
    B0 = s.book(s.ada, "r_pol", "p_3", f"{D14}T19:00", 2)
    B1 = s.book(s.ada, "r_pol", "p_1", f"{D14}T20:30", 1)
    chk.check("new booking carries revision 1 and accepted_terms = policy 0 snapshot", B0.get("revision") == 1 and terms_eq(B0.get("accepted_terms"), T0_POL),
              T0_POL, B0.get("accepted_terms"), None, "S3 Policies")
    g = s.get(s.ada, "SEEDP1")
    chk.check("seeded booking: revision 1 under policy 0", g.status == 200 and (g.json or {}).get("revision") == 1
              and terms_eq((g.json or {}).get("accepted_terms"), T0_POL), T0_POL, g.text(300), g.req, "S3 Policies")
    base = pol(D + timedelta(days=7))
    # auth
    chk.expect("publish without token -> 401", c.req("POST", "/restaurants/r_pol/policies", base, key=uuid.uuid4().hex), 401, "unauthenticated", "S3 Policies")
    chk.expect("publish as a non-manager -> 403", s3.publish("r_pol", base, tok=s.ada), 403, "forbidden", "S3 Policies")
    chk.expect("publish on an unknown restaurant -> 404", s3.publish("nope", base), 404, "not_found", "S3 Policies")
    chk.expect("manager of another restaurant only (r_dst3 has none) -> 403", s3.publish("r_dst3", {**base, "capacities": {"d_1": 4}}), 403, "forbidden", "S3 Policies")
    chk.expect("publish without Idempotency-Key -> 400", c.req("POST", "/restaurants/r_pol/policies", base, token=s3.mgr), 400, "missing_idempotency_key", "S3 Policies")
    r = c.req("GET", "/restaurants/r_pol/policies")
    chk.check("GET policies public, empty before publication (policy 0 omitted)", r.status == 200 and r.json == {"policies": []}, {"policies": []},
              r.text(200), r.req, "S3 Policies")
    # validation matrix: nothing allocated
    caps = base["capacities"]
    bad_values = [
        ("effective_from not a date", {"effective_from": "2026-02-30"}), ("effective_from short", {"effective_from": "2026-9-1"}),
        ("effective_from empty", {"effective_from": ""}), ("slot 0", {"slot_minutes": 0}), ("slot 1441", {"slot_minutes": 1441}),
        ("duration 0", {"reservation_duration_minutes": 0}), ("duration 1441", {"reservation_duration_minutes": 1441}),
        ("cutoff -1", {"cancellation_cutoff_minutes": -1}), ("cutoff 10081", {"cancellation_cutoff_minutes": 10081}),
        ("slot 1.5", {"slot_minutes": 1.5}), ("duplicate weekday", {"opening_hours": _hours("12:00", "23:00") + [{"weekday": "mon", "opens": "09:00", "closes": "10:00"}]}),
        ("closes before opens", {"opening_hours": [{"weekday": "mon", "opens": "20:00", "closes": "18:00"}]}),
        ("weekday unknown", {"opening_hours": [{"weekday": "funday", "opens": "18:00", "closes": "20:00"}]}),
        ("opens not HH:MM", {"opening_hours": [{"weekday": "mon", "opens": "6pm", "closes": "23:00"}]}),
        ("capacities missing a table", {"capacities": {"p_1": 2, "p_2": 4}}), ("capacities extra table", {"capacities": {**caps, "p_9": 2}}),
        ("capacity 0", {"capacities": {**caps, "p_1": 0}}), ("capacity 101", {"capacities": {**caps, "p_1": 101}}),
        ("capacity 2.5", {"capacities": {**caps, "p_1": 2.5}})]
    for f in ("effective_from", "slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes", "opening_hours", "capacities"):
        b = dict(base); b.pop(f)
        bad_values.append((f"missing {f}", b))
    for name, change in bad_values:
        body = change if name.startswith("missing") else {**base, **change}
        chk.expect(f"invalid policy: {name} -> 422", s3.publish("r_pol", body), 422, "validation_failed", "S3 Policies")
    for name, change in (("slot a boolean", {"slot_minutes": True}), ("capacity a boolean", {"capacities": {**caps, "p_1": True}}),
                         ("opening_hours a string", {"opening_hours": "mon"}), ("capacities a list", {"capacities": [2, 4, 6]}),
                         ("effective_from a number", {"effective_from": 20260928})):
        chk.expect(f"invalid policy type: {name} -> 422 (R-47)", s3.publish("r_pol", {**base, **change}), 422, "validation_failed", "S3 Policies")
    for name, change in (("slot null", {"slot_minutes": None}), ("capacities a string", {"capacities": "p_1"}), ("capacity a string", {"capacities": {**caps, "p_1": "2"}})):
        chk.expect(f"invalid policy type: {name} -> 422 (R-47)", s3.publish("r_pol", {**base, **change}), 422, "validation_failed", "S3 Policies")
    chk.expect("non-manager without key -> 400 (key before 403, R-47)", c.req("POST", "/restaurants/r_pol/policies", base, token=s.ada), 400,
               "missing_idempotency_key", "S3 Policies")
    chk.expect("non-manager with an invalid policy -> 403 (permission before validation, R-47)", s3.publish("r_pol", {**base, "slot_minutes": 0}, tok=s.ada),
               403, "forbidden", "S3 Policies")
    chk.expect("unknown restaurant with an invalid policy -> 404 (R-47)", s3.publish("nope", {**base, "slot_minutes": 0}), 404, "not_found", "S3 Policies")
    chk.expect("non-object body -> 400", c.req("POST", "/restaurants/r_pol/policies", token=s3.mgr, key=uuid.uuid4().hex, raw="[1]"), 400,
               "malformed_request", "S3 Policies")
    chk.expect("GET policies of an unknown restaurant -> 404 (R-48)", c.req("GET", "/restaurants/nope/policies"), 404, "not_found", "S3 Policies")
    r = c.req("GET", "/restaurants/r_pol/policies")
    chk.check("failed publications allocate no version", r.status == 200 and r.json == {"policies": []}, [], r.text(200), r.req, "S3 Policies")
    # idempotency and versions
    k1 = "pol-" + uuid.uuid4().hex
    kf = "polf-" + uuid.uuid4().hex
    chk.expect("failed write first (422) with key kf", s3.publish("r_pol", {**base, "slot_minutes": 0}, key=kf), 422, "validation_failed", "S3 Policies")
    v1 = pol(D + timedelta(days=7), 30, 120, 60, "12:00", "22:00", {"p_1": 3, "p_2": 4, "p_3": 6})
    r1 = s3.publish("r_pol", {**v1, "zzz": "ignored"}, key=k1)
    j = r1.json if isinstance(r1.json, dict) else {}
    chk.check("publish 201 body is exactly the policy fields + policy_version, unknown field not echoed (R-48)",
              set(j) == {"effective_from", "slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes", "opening_hours", "capacities",
                         "policy_version"}, "exact keys", sorted(j), r1.req, "S3 Policies")
    chk.check("publish 201: supplied policy + policy_version 1 (unknown field ignored)", r1.status == 201 and j.get("policy_version") == 1
              and all(j.get(k) == v1[k] or (k == "opening_hours" and terms_eq({"opening_hours": j.get(k)}, {"opening_hours": v1[k]})) for k in v1),
              {**v1, "policy_version": 1}, r1.text(400), r1.req, "S3 Policies")
    r2 = s3.publish("r_pol", {**v1, "zzz": "ignored"}, key=k1)
    chk.check("publish replay 200 identical, no new version", r2.status == 200 and r2.json == r1.json, 200, r2.text(200), r2.req, "S3 Policies/§7")
    chk.expect("publish same key, different body -> 409", s3.publish("r_pol", {**v1, "slot_minutes": 60}, key=k1), 409, "idempotency_key_reuse", "S3 Policies")
    v2 = pol(D14, 15, 60, 30, "10:00", "20:00", {"p_1": 2, "p_2": 2, "p_3": 8})
    r = s3.publish("r_pol", v2, key=kf)
    chk.check("key whose first use failed is reusable -> 201 version 2", r.status == 201 and (r.json or {}).get("policy_version") == 2, 2, r.text(200), r.req, "S3 Policies")
    v3 = pol(D + timedelta(days=7), 30, 100, 90, "12:00", "23:00", {"p_1": 5, "p_2": 5, "p_3": 5})
    v4 = pol(D - timedelta(days=60), 30, 90, 120, "12:00", "23:00", {"p_1": 2, "p_2": 3, "p_3": 6})
    for i, p in ((3, {**v3, "slot_minutes": 30.0}), (4, v4)):      # 30.0 counts as an integer (R-47)
        r = s3.publish("r_pol", p)
        chk.check(f"publish -> version {i}", r.status == 201 and (r.json or {}).get("policy_version") == i, i, r.text(200), r.req, "S3 Policies")
    r = c.req("GET", "/restaurants/r_pol/policies")
    got = [p.get("policy_version") for p in (r.json or {}).get("policies", [])] if r.status == 200 else None
    chk.check("GET policies in publication order, versions 1..4, policy 0 omitted", got == [1, 2, 3, 4], [1, 2, 3, 4], got, r.req, "S3 Policies")
    effs = [p.get("effective_from") for p in (r.json or {}).get("policies", [])]
    chk.check("listed policies keep their effective_from", effs == [v1["effective_from"], v2["effective_from"], v3["effective_from"], v4["effective_from"]],
              "as published", effs, r.req, "S3 Policies")
    r = c.req("GET", "/restaurants/r_pol")
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("restaurant detail includes manager_user_ids and combinable, never policies (R-59)", j.get("manager_user_ids") == ["u_mgr"]
              and j.get("combinable") == [["p_1", "p_2"]] and "policies" not in j, "fixture shape", sorted(j), r.req, "S3 Policies")
    chk.check("restaurant detail still returns the original fixture configuration", j.get("slot_minutes") == 30 and j.get("reservation_duration_minutes") == 90
              and [t.get("capacity") for t in j.get("tables", [])] == [2, 4, 6], "fixture config", r.text(300), r.req, "S3 Policies")
    # existing bookings untouched
    for name, B in (("B0", B0), ("B1", B1)):
        g = s.get(s.ada, B["reference"]).json or {}
        chk.check(f"publication leaves existing booking {name} unchanged (terms, revision, ends_at)", g.get("revision") == 1 and terms_eq(g.get("accepted_terms"), T0_POL)
                  and g.get("ends_at") == B.get("ends_at"), "unchanged", g, None, "S3 Policies")
        e = s3.entries(B["reference"], s.ada)
        chk.check(f"publication adds no history to {name}", e is not None and len(e) == 1, 1, e, None, "S3 Policies/History")
    # selection by local start date
    def bk(tid, local, party):
        return c.req("POST", "/reservations", booking_body("r_pol", tid, local, party), token=s.bob, key=uuid.uuid4().hex)
    for name, tid, local, party, ver, p, st in (
            ("past effective date (v4) applies to D", "p_2", f"{D}T19:00", 3, 4, v4, 201),
            ("same effective date: greater version (v3) wins", "p_1", f"{D + timedelta(days=7)}T19:00", 5, 3, v3, 201),
            ("v3 also covers D+10", "p_1", f"{D + timedelta(days=10)}T13:00", 5, 3, v3, 201),
            ("v2 from D+14: 15-minute grid from 10:00", "p_3", f"{D14}T10:15", 7, 2, v2, 201),
            ("before v4's effective date -> policy 0 (past booking)", "p_1", f"{D - timedelta(days=90)}T19:00", 2, 0, None, 201)):
        r = bk(tid, local, party)
        j = r.json if isinstance(r.json, dict) else {}
        want = T0_POL if p is None else terms_of(ver, p)
        dur = want["reservation_duration_minutes"]
        st0 = resolve(local, "Europe/Berlin")
        chk.check(f"selection: {name}", r.status == st and terms_eq(j.get("accepted_terms"), want)
                  and same_instant_and_text(j.get("ends_at"), (st0 + timedelta(minutes=dur)).astimezone(ZoneInfo("Europe/Berlin"))),
                  {"policy_version": ver, "duration": dur}, {"status": r.status, "terms": (j.get("accepted_terms") or {}).get("policy_version"),
                                                              "ends_at": j.get("ends_at")}, r.req, "S3 Policies")
    chk.expect("v4 capacity applies on D: party 4 on p_2 (cap 3) -> 422", bk("p_2", f"{D}T21:00", 4), 422, "party_exceeds_capacity", "S3 Policies")
    chk.expect("v2 capacity applies from D+14: party 3 on p_2 (cap 2) -> 422", bk("p_2", f"{D14}T12:00", 3), 422, "party_exceeds_capacity", "S3 Policies")
    chk.expect("v2 hours apply: 20:00 is past closes-duration -> 422", bk("p_2", f"{D14}T19:30", 1), 422, "outside_opening_hours", "S3 Policies")
    chk.expect("pair capacity is the sum of the selected policy's capacities (v3: 5+5=10)", c.req("POST", "/reservations",
               body2("r_pol", ["p_1", "p_2"], f"{D + timedelta(days=8)}T19:00", 10), token=s.bob, key=uuid.uuid4().hex), 201, section="S3 Combined history")
    r = c.req("GET", "/availability", query={"restaurant_id": "r_pol", "date": str(D14), "party_size": 2, "explain": "true"})
    sl = (r.json or {}).get("slots", []) if r.status == 200 else []
    want_starts = [f"{D14}T{h:02d}:{m:02d}" for h in range(10, 20) for m in (0, 15, 30, 45) if (h, m) <= (19, 0)]
    chk.check("availability follows the selected policy (v2: 10:00-19:00 every 15 min)", [x.get("starts_at_local") for x in sl] == want_starts,
              f"{len(want_starts)} slots", [x.get("starts_at_local") for x in sl][:3] + [len(sl)], r.req, "S3 Policies")
    chk.check("explain names policy_version 2 for every table", bool(sl) and all(e.get("policy_version") == 2 for x in sl for e in x.get("explain", [])),
              2, sl[:1], r.req, "S3 Explanations")
    # amendments under policies
    r = c.req("PATCH", f"/reservations/{B0['reference']}", {"party_size": 3}, token=s.ada)
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("real amendment adopts the resulting date's policy: terms v2, end recomputed, revision 2",
              r.status == 200 and j.get("revision") == 2 and terms_eq(j.get("accepted_terms"), terms_of(2, v2))
              and same_instant_and_text(j.get("ends_at"), resolve(f"{D14}T20:00", "Europe/Berlin")), "v2, 20:00, rev 2", r.text(300), r.req, "S3 Policies")
    snap = s.get(s.ada, B1["reference"]).json
    r = c.req("PATCH", f"/reservations/{B1['reference']}", {"party_size": 2}, token=s.ada)
    chk.expect("amendment validated against the new policy: 20:30 outside v2 hours -> 422", r, 422, "outside_opening_hours", "S3 Policies")
    chk.check("failed amendment changes nothing", s.get(s.ada, B1["reference"]).json == snap, snap, None, r.req, "S3 Policies")
    r = c.req("PATCH", f"/reservations/{B1['reference']}", {"party_size": 1}, token=s.ada)
    chk.check("no-op amendment keeps terms (policy 0), end time and revision even where the new policy would refuse it",
              r.status == 200 and (r.json or {}).get("revision") == 1 and terms_eq((r.json or {}).get("accepted_terms"), T0_POL), "rev 1, v0",
              r.text(300), r.req, "S3 Policies")
    chk.check("no-op amendment records no history", len(s3.entries(B1["reference"], s.ada) or []) == 1, 1, s3.entries(B1["reference"], s.ada), None, "S3 History")
    # cutoff from the accepted terms
    nt_today = datetime.now(ZoneInfo(ctx.now_tz)).date()
    N = s.book(s.ada, "r_near3", "q_1", ctx.near, 2)
    r = s3.publish("r_near3", {**pol(nt_today, 15, 30, 0, "00:00", "23:45"), "capacities": {"q_1": 4, "q_2": 4}})
    chk.expect("publish a cutoff-0 policy effective today", r, 201, section="S3 Policies")
    chk.expect("cancel still uses the accepted cutoff (120) -> 409", c.req("POST", f"/reservations/{N['reference']}/cancel", token=s.ada), 409,
               "cutoff_passed", "S3 Policies")
    chk.expect("amendment checks the old accepted cutoff first -> 409", c.req("PATCH", f"/reservations/{N['reference']}", {"party_size": 3}, token=s.ada),
               409, "cutoff_passed", "S3 Policies")
    N2 = s.book(s.ada, "r_near3", "q_2", ctx.near2, 2)
    chk.check("a booking made after publication accepts cutoff 0", (N2.get("accepted_terms") or {}).get("cancellation_cutoff_minutes") == 0, 0,
              N2.get("accepted_terms"), None, "S3 Policies")
    chk.expect("…and can be cancelled inside the old 120-minute window", c.req("POST", f"/reservations/{N2['reference']}/cancel", token=s.ada), 200,
               section="S3 Policies")


def g_history(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s3 = S3(s)
    s3.reset()
    D = ctx.thu
    key = "hist-" + uuid.uuid4().hex
    body = booking_body("r_pol", "p_2", f"{D}T19:00", 2)
    r1 = c.req("POST", "/reservations", body, token=s.ada, key=key)
    ref = (r1.json or {}).get("reference")
    e = s3.entries(ref, s.ada)
    want = [{"field": "table_id", "from": None, "to": "p_2"}, {"field": "starts_at_local", "from": None, "to": f"{D}T19:00"},
            {"field": "party_size", "from": None, "to": 2}]
    chk.check("created entry: seq 1, all three fields from null, revision 1, accepted_terms", e is not None and len(e) == 1 and e[0].get("seq") == 1
              and e[0].get("event") == "created" and e[0].get("changes") == want and e[0].get("revision") == 1 and terms_eq(e[0].get("accepted_terms"), T0_POL)
              and bool(RFC3339_OFFSET_RE.match(e[0].get("at") or "")), want, e, None, "S3 History")
    at0 = parse_rfc(e[0].get("at") or "") if e else None
    ca = parse_rfc((r1.json or {}).get("created_at") or "")
    berlin = ZoneInfo("Europe/Berlin")
    chk.check("created entry 'at' = created_at, written in the restaurant's offset (R-51)", at0 is not None and ca is not None and at0 == ca
              and at0.utcoffset() == at0.astimezone(berlin).utcoffset(), "same instant, Berlin offset",
              [e[0].get("at") if e else None, (r1.json or {}).get("created_at")], None, "S3 History")
    sc = s3.entries("SEEDC1", s.ada) or []
    chk.check("cancelled seed: exactly one created entry, revision 1, no cancelled entry (R-51)", len(sc) == 1 and sc[0].get("event") == "created"
              and sc[0].get("revision") == 1 and (s.get(s.ada, "SEEDC1").json or {}).get("revision") == 1, 1, sc, None, "S3 History")
    se = s3.entries("SEEDP1", s.ada) or []
    chk.check("seeded booking: exactly one created entry, revision 1, policy-0 terms (R-51)", len(se) == 1 and se[0].get("event") == "created"
              and se[0].get("revision") == 1 and terms_eq(se[0].get("accepted_terms"), T0_POL), 1, se, None, "S3 History")
    r = s3.history(ref, s.ada)
    chk.check("history envelope names the reference", r.status == 200 and (r.json or {}).get("reference") == ref, ref, r.text(200), r.req, "S3 History")
    c.req("POST", "/reservations", body, token=s.ada, key=key)
    chk.check("idempotent replay records nothing", len(s3.entries(ref, s.ada) or []) == 1, 1, None, None, "S3 History")

    def patch(b):
        return c.req("PATCH", f"/reservations/{ref}", b, token=s.ada)

    patch({"table_id": "p_3"})
    patch({"party_size": 3, "starts_at_local": f"{D}T19:30"})
    patch({"party_size": 3})                          # no-op
    patch({"table_ids": ["p_3"]})                     # same set: no-op
    patch({"party_size": 99})                         # failure
    c.req("POST", f"/reservations/{ref}/cancel", token=s.ada)
    c.req("POST", f"/reservations/{ref}/cancel", token=s.ada)
    e = s3.entries(ref, s.ada) or []
    got = [(x.get("seq"), x.get("event"), x.get("changes"), x.get("revision")) for x in e]
    want = [(1, "created", None, 1),
            (2, "changed", [{"field": "table_id", "from": "p_2", "to": "p_3"}], 2),
            (3, "changed", [{"field": "starts_at_local", "from": f"{D}T19:00", "to": f"{D}T19:30"}, {"field": "party_size", "from": 2, "to": 3}], 3),
            (4, "cancelled", [], 4)]
    ok = len(got) == 4 and all(g[:2] == w[:2] and (w[2] is None or g[2] == w[2]) and g[3] == w[3] for g, w in zip(got, want))
    chk.check("history: changed lists only changed fields in order; no-op, same-set and failed PATCH record nothing; cancelled empty and last",
              ok, want, got, None, "S3 History")
    ats = [parse_rfc(x.get("at") or "") for x in e]
    chk.check("entries in seq order are also in at order", None not in ats and ats == sorted(ats), "non-decreasing", [x.get("at") for x in e], None, "S3 History")
    chk.check("cancelled reservation keeps its history; revision 4", s.get(s.ada, ref).json.get("revision") == 4, 4, s.get(s.ada, ref).json.get("revision"), None, "S3 History")
    # owner only, 404 even without token
    for name, tok in (("another user", s.bob), ("no token", None), ("the manager", s3.mgr), ("an invalid token", "not-a-token")):
        chk.expect(f"history read by {name} -> 404", c.req("GET", f"/reservations/{ref}/history", token=tok), 404, "not_found", "S3 History")
        chk.expect(f"decision read by {name} -> 404", c.req("GET", f"/reservations/{ref}/decision", token=tok), 404, "not_found", "S3 Policies")
    chk.expect("history of an unknown reference -> 404", c.req("GET", "/reservations/ZZZZZZ/history", token=s.ada), 404, "not_found", "S3 History")
    r = c.req("GET", f"/reservations/{ref}/decision", token=s.ada)
    chk.check("decision after cancellation: reference, revision, accepted_terms", r.status == 200 and (r.json or {}).get("reference") == ref
              and (r.json or {}).get("revision") == 4 and isinstance((r.json or {}).get("accepted_terms"), dict), "rev 4", r.text(300), r.req, "S3 Policies")
    # pair history
    rp = c.req("POST", "/reservations", body2("r_pol", ["p_2", "p_1"], f"{D}T13:00", 5), token=s.ada, key=uuid.uuid4().hex)
    pref = (rp.json or {}).get("reference")
    e = s3.entries(pref, s.ada) or []
    chk.check("pair creation: table_ids from null to the pair in declared order, no table_id change", len(e) == 1 and
              e[0].get("changes", [{}])[0] == {"field": "table_ids", "from": None, "to": ["p_1", "p_2"]}
              and all(ch.get("field") != "table_id" for ch in e[0].get("changes", [])), "table_ids null -> [p_1,p_2]", e, None, "S3 Combined history")
    c.req("PATCH", f"/reservations/{pref}", {"table_ids": ["p_2", "p_1"]}, token=s.ada)
    chk.check("reversed pair is not an amendment (no entry, revision 1)", len(s3.entries(pref, s.ada) or []) == 1
              and (s.get(s.ada, pref).json or {}).get("revision") == 1, 1, s3.entries(pref, s.ada), None, "S3 Combined history")
    c.req("PATCH", f"/reservations/{pref}", {"table_id": "p_3"}, token=s.ada)
    e = s3.entries(pref, s.ada) or []
    chk.check("pair -> single change uses table_ids with complete before/after lists", len(e) == 2 and
              e[1].get("changes", [{}])[0] == {"field": "table_ids", "from": ["p_1", "p_2"], "to": ["p_3"]}, "table_ids [p_1,p_2] -> [p_3]", e[1:], None,
              "S3 Combined history")
    s3.standard_policies()
    e2 = s3.entries(pref, s.ada) or []
    chk.check("old entries never acquire newer terms (publication after the fact)", e2 == e, e, e2, None, "S3 History")


def g_revision(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s3 = S3(s)
    s3.reset()
    D = ctx.thu
    B = s.book(s.ada, "r_pol", "p_2", f"{D}T19:00", 2)
    ref = B["reference"]

    def patch(b, rf=None, tok=None):
        return c.req("PATCH", f"/reservations/{rf or ref}", b, token=tok or s.ada)

    r = patch({"party_size": 3, "expected_revision": 1})
    chk.check("expected_revision equal to current -> real change, revision 2", r.status == 200 and (r.json or {}).get("revision") == 2, 2, r.text(200), r.req,
              "S3 Revisions")
    snap = s.get(s.ada, ref).json
    chk.expect("stale expected_revision -> 409 stale_revision", patch({"party_size": 4, "expected_revision": 1}), 409, "stale_revision", "S3 Revisions")
    chk.expect("stale beats validation (party 99)", patch({"party_size": 99, "expected_revision": 1}), 409, "stale_revision", "S3 Revisions")
    chk.check("stale write changed nothing", s.get(s.ada, ref).json == snap, snap, None, None, "S3 Revisions")
    for v in (0, -1, 1.5, True, "2", None, [2]):
        chk.expect(f"expected_revision {v!r} -> 422 (R-49)", patch({"party_size": 3, "expected_revision": v}), 422, "validation_failed", "S3 Revisions")
    chk.expect("another user's booking + invalid expected_revision -> 404 first (R-49)", patch({"expected_revision": 0}, tok=s.bob), 404, "not_found",
               "S3 Revisions")
    chk.expect("wrong-type field beats invalid expected_revision -> 400 (R-49)", patch({"table_id": 5, "expected_revision": 0}), 400, "malformed_request",
               "S3 Revisions")
    chk.expect("invalid expected_revision beats stale/validation -> 422", patch({"party_size": 99, "expected_revision": "x"}), 422, "validation_failed",
               "S3 Revisions")
    r = patch({"expected_revision": 2})
    chk.check("only a matching expected_revision is a no-op 200 (R-58)", r.status == 200 and (r.json or {}).get("revision") == 2, 2, r.text(200), r.req,
              "S3 Revisions")
    r = patch({"party_size": 3, "expected_revision": 2})
    chk.check("no-op with current expected_revision -> 200, revision unchanged", r.status == 200 and (r.json or {}).get("revision") == 2, 2, r.text(200), r.req, "S3 Revisions")
    chk.expect("no-op with stale expected_revision -> 409 (R-49)", patch({"party_size": 3, "expected_revision": 1}), 409, "stale_revision", "S3 Revisions")
    near = s.book(s.ada, "r_near3", "q_1", ctx.near, 2)
    chk.expect("stale_revision comes before cutoff", patch({"party_size": 3, "expected_revision": 5}, rf=near["reference"]), 409, "stale_revision", "S3 Revisions")
    chk.expect("current revision inside cutoff -> cutoff_passed", patch({"party_size": 3, "expected_revision": 1}, rf=near["reference"]), 409, "cutoff_passed", "S3 Revisions")
    r = c.req("POST", f"/reservations/{ref}/cancel", {"expected_revision": 99}, token=s.ada)
    chk.check("cancel increments revision once (expected_revision ignored, R-50)", r.status == 200 and (r.json or {}).get("revision") == 3, 3, r.text(200), r.req, "S3 Revisions")
    r = c.req("POST", f"/reservations/{ref}/cancel", token=s.ada)
    chk.check("repeated cancel does not increment", r.status == 200 and (r.json or {}).get("revision") == 3, 3, r.text(200), r.req, "S3 Revisions")
    chk.expect("cancelled booking + stale expected_revision -> stale first (R-49)", patch({"party_size": 2, "expected_revision": 1}), 409, "stale_revision",
               "S3 Revisions")
    chk.expect("cancelled booking + current expected_revision -> reservation_cancelled (R-49)", patch({"party_size": 2, "expected_revision": 3}), 409,
               "reservation_cancelled", "S3 Revisions")
    k = "rev-" + uuid.uuid4().hex
    r1 = c.req("POST", "/reservations", booking_body("r_pol", "p_3", f"{D}T13:00", 2), token=s.ada, key=k)
    c.req("PATCH", f"/reservations/{r1.json['reference']}", {"party_size": 4}, token=s.ada)
    r2 = c.req("POST", "/reservations", booking_body("r_pol", "p_3", f"{D}T13:00", 2), token=s.ada, key=k)
    chk.check("replay keeps the original revision and terms", r2.status == 200 and r2.json == r1.json and (r2.json or {}).get("revision") == 1, r1.json,
              r2.text(200), r2.req, "S3 Revisions/§7")


def g_series(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s3 = S3(s)
    s3.reset()
    D = ctx.thu

    def adopt(anchor, count=4, interval=1, key=None, tok=None, extra=None):
        return c.req("POST", "/series", {"anchor_reference": anchor, "count": count, "interval_weeks": interval, **(extra or {})},
                     token=tok or s.ada, key=key or uuid.uuid4().hex)

    A = s.book(s.ada, "r_ser", "s_2", f"{D}T19:00", 2)
    a_hist = s3.entries(A["reference"], s.ada)
    k = "ser-" + uuid.uuid4().hex
    r = adopt(A["reference"], 4, 1, key=k, extra={"zzz": 1})
    j = r.json if isinstance(r.json, dict) else {}
    occ = j.get("occurrences") or []
    ok = r.status == 201 and isinstance(j.get("series_id"), str) and j.get("revision") == 1 and j.get("interval_weeks") == 1 and len(occ) == 4 \
        and [o.get("index") for o in occ] == [0, 1, 2, 3] and occ[0].get("reference") == A["reference"] and all(o.get("exception") is False for o in occ)
    chk.check("adopt 201: series_id, revision 1, 4 occurrences in index order, anchor is occurrence 0", ok, "shape", r.text(500), r.req, "S3 Series")
    sid = j.get("series_id")
    refs = [o.get("reference") for o in occ]
    chk.check("occurrence references distinct", len(set(refs)) == 4, 4, refs, r.req, "S3 Series")
    chk.check("occurrence 0 reservation equals the unchanged anchor", occ and occ[0].get("reservation") == A, A, occ[0].get("reservation") if occ else None,
              r.req, "S3 Series")
    chk.check("anchor history unchanged by adoption", s3.entries(A["reference"], s.ada) == a_hist, a_hist, None, None, "S3 Series")
    bad = [o for i, o in enumerate(occ[1:], 1) if (o.get("reservation") or {}).get("starts_at_local") != f"{D + timedelta(days=7 * i)}T19:00"
           or (o.get("reservation") or {}).get("table_id") != "s_2" or (o.get("reservation") or {}).get("party_size") != 2]
    chk.check("occurrence i on anchor date + 7i days, same clock time, table and party", not bad, "weekly", bad[:2], r.req, "S3 Series")
    mine = {x.get("reference") for x in s.mine(s.ada)}
    chk.check("occurrences appear in the ordinary reservation list", set(refs) <= mine, refs, None, None, "S3 Series")
    t = s.avail_tables("r_ser", D + timedelta(days=7), 2, "19:00")
    chk.check("occurrences occupy their tables", t is not None and "s_2" not in t, "s_2 absent", t, None, "S3 Series")
    chk.check("each occurrence has an ordinary created history", all(len(s3.entries(x, s.ada) or []) == 1 for x in refs[1:]), 1, None, None, "S3 Series")
    g = c.req("GET", f"/series/{sid}", token=s.ada)
    chk.check("GET /series/{id} owner -> same shape", g.status == 200 and [o.get("reference") for o in (g.json or {}).get("occurrences", [])] == refs, refs,
              g.text(300), g.req, "S3 Series")
    chk.expect("GET series by another user -> 404", c.req("GET", f"/series/{sid}", token=s.bob), 404, "not_found", "S3 Series")
    chk.expect("GET series without token -> 404", c.req("GET", f"/series/{sid}"), 404, "not_found", "S3 Series")
    rr = adopt(A["reference"], 4, 1, key=k, extra={"zzz": 1})
    chk.check("series replay 200 identical", rr.status == 200 and rr.json == r.json, 200, rr.text(200), rr.req, "S3 Series/§7")
    chk.expect("series same key, different body -> 409", adopt(A["reference"], 5, 1, key=k), 409, "idempotency_key_reuse", "S3 Series")
    chk.expect("adopting the anchor again -> 409 already_in_series", adopt(A["reference"]), 409, "already_in_series", "S3 Series")
    chk.expect("adopting a generated occurrence -> 409 already_in_series", adopt(refs[1]), 409, "already_in_series", "S3 Series")
    # validation and anchor errors
    X = s.book(s.ada, "r_ser", "s_1", f"{D}T13:00", 2)
    for name, cnt, iv, st_, code in (("count 1", 1, 1, 422, "validation_failed"), ("count 13", 13, 1, 422, "validation_failed"),
                                     ("count true", True, 1, 422, "validation_failed"), ("count '4'", "4", 1, 422, "validation_failed"),
                                     ("count null", None, 1, 422, "validation_failed"),
                                     ("count 2.5", 2.5, 1, 422, "validation_failed"), ("interval 0", 2, 0, 422, "validation_failed"),
                                     ("interval 5", 2, 5, 422, "validation_failed"), ("interval true", 2, True, 422, "validation_failed"),
                                     ("interval 1.5", 2, 1.5, 422, "validation_failed")):
        chk.expect(f"series {name} -> {st_}", adopt(X["reference"], cnt, iv), st_, code, "S3 Series")
    Mx = s.book(s.ada, "r_ser", "s_2", f"{D}T15:00", 2)
    r = adopt(Mx["reference"], 12, 1)
    chk.expect("count 12 (maximum) accepted -> 201", r, 201, section="C3.37")
    My = s.book(s.ada, "r_ser", "s_1", f"{D}T15:00", 2)
    chk.expect("interval_weeks 4 (maximum) accepted -> 201", adopt(My["reference"], 2, 4), 201, section="C3.37")
    chk.expect("series missing anchor_reference -> 422", c.req("POST", "/series", {"count": 2, "interval_weeks": 1}, token=s.ada, key=uuid.uuid4().hex), 422,
               "validation_failed", "S3 Series")
    chk.expect("series no token -> 401", c.req("POST", "/series", {"anchor_reference": X["reference"], "count": 2, "interval_weeks": 1}, key=uuid.uuid4().hex),
               401, "unauthenticated", "S3 Series")
    chk.expect("series without Idempotency-Key -> 400", c.req("POST", "/series", {"anchor_reference": X["reference"], "count": 2, "interval_weeks": 1},
                                                              token=s.ada), 400, "missing_idempotency_key", "S3 Series")
    chk.expect("unknown anchor -> 404", adopt("ZZZZZZ"), 404, "not_found", "S3 Series")
    chk.expect("anchor_reference a number -> 400 (R-52)", c.req("POST", "/series", {"anchor_reference": 5, "count": 2, "interval_weeks": 1}, token=s.ada,
                                                                    key=uuid.uuid4().hex), 400, "malformed_request", "S3 Series")
    chk.expect("invalid count beats unknown anchor -> 422 (R-52)", adopt("ZZZZZZ", 1), 422, "validation_failed", "S3 Series")
    for name, ref_ in (("empty anchor_reference", ""), ("65-character anchor_reference", "A" * 65)):
        chk.expect(f"{name} -> 422 before 404 (R-60)", adopt(ref_), 422, "validation_failed", "C3.37 (R-60)")
    chk.expect("GET unknown series id -> 404 (R-53)", c.req("GET", "/series/nope", token=s.ada), 404, "not_found", "S3 Series")
    chk.check("series_id is at most 64 characters (R-53)", isinstance(sid, str) and 0 < len(sid) <= 64, "<=64", sid, None, "S3 Series")
    chk.check("series body has exactly the R-53 keys", set(j) == {"series_id", "revision", "interval_weeks", "occurrences"}
              and all(set(o) == {"index", "reference", "exception", "reservation"} for o in occ), "exact keys", sorted(j), None, "S3 Series")
    chk.expect("another user's anchor -> 404", adopt(X["reference"], tok=s.bob), 404, "not_found", "S3 Series")
    Y = s.book(s.ada, "r_ser", "s_1", f"{D}T21:00", 2)
    c.req("POST", f"/reservations/{Y['reference']}/cancel", token=s.ada)
    chk.expect("cancelled anchor -> 409 reservation_cancelled", adopt(Y["reference"]), 409, "reservation_cancelled", "S3 Series")
    Nr = s.book(s.ada, "r_near3", "q_1", ctx.near, 2)
    chk.expect("anchor inside its cutoff -> 409 cutoff_passed", adopt(Nr["reference"], 2, 1), 409, "cutoff_passed", "S3 Series")
    # occupancy failure: atomic, key reusable
    before = sorted(x.get("reference") for x in s.mine(s.ada))
    blocker = s.book(s.bob, "r_ser", "s_1", f"{D + timedelta(days=14)}T13:00", 2)
    kf = "serf-" + uuid.uuid4().hex
    chk.expect("occurrence 2 collides -> 409 table_unavailable", adopt(X["reference"], 4, 1, key=kf), 409, "table_unavailable", "S3 Series")
    chk.check("failed adoption created nothing", sorted(x.get("reference") for x in s.mine(s.ada)) == before, len(before), None, None, "S3 Series")
    chk.check("failed adoption left the anchor's history unchanged", len(s3.entries(X["reference"], s.ada) or []) == 1, 1, None, None, "S3 Series")
    c.req("POST", f"/reservations/{blocker['reference']}/cancel", token=s.bob)
    r = adopt(X["reference"], 4, 1, key=kf)
    chk.expect("same key after the failure is a first use -> 201", r, 201, section="S3 Series")
    # DST
    sp = s.book(s.ada, "r_dst3", "d_1", "2027-03-21T02:30", 2)
    chk.expect("occurrence on spring-forward night (02:30 missing) -> 422 invalid_local_time", adopt(sp["reference"], 2, 1), 422, "invalid_local_time", "S3 Series")
    fb = s.book(s.ada, "r_dst3", "d_1", "2027-10-24T02:30", 2)
    r = adopt(fb["reference"], 2, 1)
    o1 = ((r.json or {}).get("occurrences") or [{}, {}])[1].get("reservation") or {}
    chk.check("fall-back repeated time resolves to the first occurrence (+02:00)", r.status == 201 and o1.get("starts_at") == "2027-10-31T02:30:00+02:00",
              "2027-10-31T02:30:00+02:00", o1.get("starts_at"), r.req, "S3 Series")
    # exception flags and revisions
    def series():
        return c.req("GET", f"/series/{sid}", token=s.ada).json or {}
    c.req("PATCH", f"/reservations/{refs[1]}", {"party_size": 3}, token=s.ada)
    sj = series()
    chk.check("real PATCH marks the occurrence exception and increments series revision once", sj.get("revision") == 2
              and [o.get("exception") for o in sj.get("occurrences", [])] == [False, True, False, False], "rev 2, [F,T,F,F]",
              {"rev": sj.get("revision"), "ex": [o.get("exception") for o in sj.get("occurrences", [])]}, None, "S3 Series")
    c.req("PATCH", f"/reservations/{refs[1]}", {"party_size": 3}, token=s.ada)
    c.req("PATCH", f"/reservations/{refs[2]}", {"party_size": 99}, token=s.ada)
    chk.check("no-op and failed PATCH change neither flag nor revision", series().get("revision") == 2
              and [o.get("exception") for o in series().get("occurrences", [])] == [False, True, False, False], 2, series().get("revision"), None, "S3 Series")
    c.req("POST", f"/reservations/{refs[2]}/cancel", token=s.ada)
    c.req("POST", f"/reservations/{refs[2]}/cancel", token=s.ada)
    sj = series()
    st = [(o.get("reservation") or {}).get("status") for o in sj.get("occurrences", [])]
    chk.check("cancel increments series revision once, keeps the occurrence, no exception; repeated cancel nothing", sj.get("revision") == 3
              and st[2] == "cancelled" and [o.get("exception") for o in sj.get("occurrences", [])][2] is False, "rev 3", {"rev": sj.get("revision"), "st": st},
              None, "S3 Series")
    c.req("POST", f"/reservations/{refs[0]}/cancel", token=s.ada)
    st = [(o.get("reservation") or {}).get("status") for o in series().get("occurrences", [])]
    chk.check("cancelling the anchor does not cancel its siblings", st == ["cancelled", "confirmed", "cancelled", "confirmed"], "siblings confirmed", st, None,
              "S3 Series")
    rr = adopt(A["reference"], 4, 1, key=k, extra={"zzz": 1})
    chk.check("replay after later changes returns the original series response", rr.status == 200 and rr.json == j,
              "original", rr.text(200), rr.req, "S3 Series")
    # per-occurrence policy
    s3.standard_policies()
    Z = s.book(s.ada, "r_pol", "p_3", f"{D}T13:00", 2)
    r = adopt(Z["reference"], 3, 1)
    vers = [((o.get("reservation") or {}).get("accepted_terms") or {}).get("policy_version") for o in (r.json or {}).get("occurrences", [])]
    ends = [(o.get("reservation") or {}).get("ends_at") for o in (r.json or {}).get("occurrences", [])]
    chk.check("each occurrence selects its date's policy (v4, v3, v2) incl. duration", r.status == 201 and vers == [4, 3, 2]
              and ends[1:] == [rfc(resolve(f"{D + timedelta(days=7)}T13:00", "Europe/Berlin") + timedelta(minutes=100)),
                               rfc(resolve(f"{D + timedelta(days=14)}T13:00", "Europe/Berlin") + timedelta(minutes=60))],
              [4, 3, 2], {"vers": vers, "ends": ends}, r.req, "S3 Series")
    W = s.book(s.ada, "r_pol", "p_3", f"{D}T21:00", 6)
    chk.expect("first failing occurrence decides: occ 1 capacity (v3 cap 5) before occ 2 hours (v2)", adopt(W["reference"], 3, 1), 422,
               "party_exceeds_capacity", "S3 Series")
    W2 = s.book(s.ada, "r_pol", "p_2", f"{D}T21:30", 2)
    chk.expect("occurrence outside the selected policy's hours -> 422", adopt(W2["reference"], 3, 1), 422, "outside_opening_hours", "S3 Series")


def g_s3moves(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s3 = S3(s)
    s3.reset()
    D = ctx.thu
    A = s.book(s.ada, "r_ser", "s_2", f"{D}T19:00", 2)
    r = c.req("POST", "/series", {"anchor_reference": A["reference"], "count": 3, "interval_weeks": 1}, token=s.ada, key=uuid.uuid4().hex)
    refs = [o.get("reference") for o in (r.json or {}).get("occurrences", [])]
    sid = (r.json or {}).get("series_id")
    if r.status != 201 or len(refs) != 3:
        chk.check("setup: series adoption", False, 201, r.text(200), r.req, "harness")
        return

    def mv(moves, key=None):
        return c.req("POST", "/reservation-moves", {"moves": moves}, token=s.ada, key=key or uuid.uuid4().hex)

    snap = [s.get(s.ada, x).json for x in refs]
    chk.expect("move item with an invalid expected_revision -> 422 (R-57)", mv([{"reference": refs[1], "table_id": "s_1", "expected_revision": 0}]), 422,
               "validation_failed", "S3 Moves")
    chk.expect("move with a stale per-move expected_revision -> 409", mv([{"reference": refs[1], "table_id": "s_1", "expected_revision": 9}]), 409,
               "stale_revision", "S3 Moves")
    chk.check("stale batch changed nothing", [s.get(s.ada, x).json for x in refs] == snap, "unchanged", None, None, "S3 Moves")
    blocker = s.book(s.bob, "r_ser", "s_1", f"{D + timedelta(days=14)}T19:00", 2)
    chk.expect("batch failing on occupancy -> 409", mv([{"reference": refs[1], "table_id": "s_1"}, {"reference": refs[2], "table_id": "s_1"}]), 409,
               "table_unavailable", "S3 Moves")
    sj = c.req("GET", f"/series/{sid}", token=s.ada).json or {}
    chk.check("failed batch changes no revisions, histories or exception flags", [s.get(s.ada, x).json for x in refs] == snap and sj.get("revision") == 1
              and not any(o.get("exception") for o in sj.get("occurrences", [])), "unchanged", sj, None, "S3 Moves")
    c.req("POST", f"/reservations/{blocker['reference']}/cancel", token=s.bob)
    k = "mvs-" + uuid.uuid4().hex
    r = mv([{"reference": refs[1], "table_id": "s_1", "expected_revision": 1}, {"reference": refs[2], "table_id": "s_1"}, {"reference": refs[0]}], key=k)
    chk.expect("batch on two occurrences + a no-op anchor -> 201", r, 201, section="S3 Moves")
    sj = c.req("GET", f"/series/{sid}", token=s.ada).json or {}
    chk.check("each affected series revision increases once; changed occurrences become exceptions; no-op anchor untouched",
              sj.get("revision") == 2 and [o.get("exception") for o in sj.get("occurrences", [])] == [False, True, True], "rev 2, [F,T,T]",
              {"rev": sj.get("revision"), "ex": [o.get("exception") for o in sj.get("occurrences", [])]}, None, "S3 Moves")
    revs = [(s.get(s.ada, x).json or {}).get("revision") for x in refs]
    chk.check("every changed booking gains one revision; the no-op keeps its own", revs == [1, 2, 2], [1, 2, 2], revs, None, "S3 Moves")
    hist = [len(s3.entries(x, s.ada) or []) for x in refs]
    chk.check("one changed entry per changed booking, none for the no-op", hist == [1, 2, 2], [1, 2, 2], hist, None, "S3 Moves")
    r2 = mv([{"reference": refs[1], "table_id": "s_1", "expected_revision": 1}, {"reference": refs[2], "table_id": "s_1"}, {"reference": refs[0]}], key=k)
    chk.check("batch replay 200 original, changes nothing", r2.status == 200 and r2.json == r.json and
              (c.req("GET", f"/series/{sid}", token=s.ada).json or {}).get("revision") == 2, 200, r2.text(200), r2.req, "S3 Moves")


def g_s3burst(s: S, rounds: int):
    c, chk, ctx = s.c, s.chk, s.ctx
    base = c.base
    s3 = S3(s)
    for rnd in range(rounds):
        s3.reset()
        D = ctx.thu + timedelta(days=7 * (rnd % 3))
        # R1: same expected_revision, different real changes
        B = s.book(s.ada, "r_ser", "s_2", f"{D}T13:00", 1)
        rs = burst.fire([plan_entry(base, "PATCH", f"/reservations/{B['reference']}", {"party_size": 2 + (i % 2), "expected_revision": 1}, s.ada, None)
                         for i in range(10)])
        st = statuses(rs)
        e = s3.entries(B["reference"], s.ada) or []
        chk.check(f"r{rnd} R1 10 PATCHes sharing expected_revision 1: one 200, nine stale; one changed entry, revision 2",
                  st == {"200": 1, "409": 9} and len(e) == 2 and (s.get(s.ada, B["reference"]).json or {}).get("revision") == 2,
                  {"200": 1, "409": 9}, {"st": st, "entries": len(e)}, {"burst": "R1"}, "S3 Revisions")
        # R2: concurrent publications -> dense versions
        rs = burst.fire([plan_entry(base, "POST", "/restaurants/r_pol/policies", pol(D + timedelta(days=i)), s3.mgr, f"r2-{rnd}-{i}") for i in range(20)])
        vers = sorted(json.loads(r.body).get("policy_version") for r in rs if r.status == 201)
        lst = [p.get("policy_version") for p in (c.req("GET", "/restaurants/r_pol/policies").json or {}).get("policies", [])]
        chk.check(f"r{rnd} R2 20 concurrent publications: versions exactly 1..20, list in publication order", vers == list(range(1, 21)) and lst == list(range(1, 21)),
                  list(range(1, 21)), {"vers": vers, "list": lst}, {"burst": "R2"}, "S3 Policies")
        # R3: concurrent adoption of one anchor
        A = s.book(s.ada, "r_ser", "s_1", f"{D}T19:00", 2)
        n_before = len(s.mine(s.ada))
        rs = burst.fire([plan_entry(base, "POST", "/series", {"anchor_reference": A["reference"], "count": 3, "interval_weeks": 1}, s.ada, f"r3-{rnd}-{i}")
                         for i in range(10)])
        st = statuses(rs)
        codes_ = [json.loads(r.body).get("error", {}).get("code") for r in rs if r.status == 409]
        chk.check(f"r{rnd} R3 10 concurrent adoptions of one anchor: one 201, nine already_in_series, 2 new bookings",
                  st == {"201": 1, "409": 9} and set(codes_) == {"already_in_series"} and len(s.mine(s.ada)) == n_before + 2,
                  {"201": 1, "409": 9}, {"st": st, "new": len(s.mine(s.ada)) - n_before}, {"burst": "R3"}, "S3 Series")
        # R4: concurrent writes on one booking -> dense history
        C = s.book(s.ada, "r_ser", "s_2", f"{D}T21:00", 1)
        plan = [plan_entry(base, "PATCH", f"/reservations/{C['reference']}", {"party_size": 1 + (i % 4)}, s.ada, None) for i in range(15)]
        plan.append(plan_entry(base, "POST", f"/reservations/{C['reference']}/cancel", None, s.ada, None))
        burst.fire(plan)
        e = s3.entries(C["reference"], s.ada) or []
        seqs = [x.get("seq") for x in e]
        last_rev = (s.get(s.ada, C["reference"]).json or {}).get("revision")
        cancel_last = all(x.get("event") != "cancelled" for x in e[:-1])
        chk.check(f"r{rnd} R4 16 concurrent writes on one booking: seq 1..n dense, revisions follow, nothing after cancelled",
                  seqs == list(range(1, len(e) + 1)) and [x.get("revision") for x in e] == list(range(1, len(e) + 1)) and last_rev == len(e) and cancel_last,
                  "dense", {"seqs": seqs, "revs": [x.get("revision") for x in e], "current": last_rev}, {"burst": "R4"}, "S3 History")
        # R5: concurrent identical replays on the policies and series paths
        kp = f"r5p-{rnd}-{uuid.uuid4().hex}"
        bp = pol(D + timedelta(days=40))
        rs = burst.fire([plan_entry(base, "POST", "/restaurants/r_pol/policies", bp, s3.mgr, kp) for _ in range(15)])
        st = statuses(rs)
        bodies = {json.dumps(json.loads(r.body), sort_keys=True) for r in rs if r.status in (200, 201)}
        lst = [p.get("policy_version") for p in (c.req("GET", "/restaurants/r_pol/policies").json or {}).get("policies", [])]
        chk.check(f"r{rnd} R5 15 identical keyed publications: one 201, 14 identical 200, one version", st == {"201": 1, "200": 14} and len(bodies) == 1
                  and len(lst) == 21, {"201": 1, "200": 14, "versions": 21}, {"st": st, "versions": len(lst)}, {"burst": "R5"}, "S3 Policies")
        E = s.book(s.ada, "r_ser", "s_2", f"{D}T15:00", 2)
        ks = f"r5s-{rnd}-{uuid.uuid4().hex}"
        bs = {"anchor_reference": E["reference"], "count": 2, "interval_weeks": 1}
        rs = burst.fire([plan_entry(base, "POST", "/series", bs, s.ada, ks) for _ in range(15)])
        st = statuses(rs)
        bodies = {json.dumps(json.loads(r.body), sort_keys=True) for r in rs if r.status in (200, 201)}
        chk.check(f"r{rnd} R5 15 identical keyed adoptions: one 201, 14 identical 200", st == {"201": 1, "200": 14} and len(bodies) == 1,
                  {"201": 1, "200": 14}, st, {"burst": "R5"}, "S3 Series")
        check_invariant(s, [s.ada, s.bob], f"r{rnd} after stage-3 bursts", "S3 Concurrent")


def g_upgrade3(s: S, prev: Client | None, prev2: Client | None):
    c, chk, ctx = s.c, s.chk, s.ctx
    for label, src, fx, rid, tid in (("stage 1", prev, fixture, "r_anker", "t_2"), ("stage 2", prev2, fixture2, "r_combo", "c_4")):
        if src is None:
            chk.check(f"upgrade source {label} given", False, "--prev/--prev2", None, None, "harness")
            continue
        sp = S(src, chk, ctx)
        r = src.req("POST", "/_test/reset", fx(ctx), timeout=12)
        tok = sp.signup(email=f"legacy{label[-1]}@example.com", password="legacy pass 1", name="Legacy")
        k = "up3-" + uuid.uuid4().hex
        b = booking_body(rid, tid, ctx.D(ctx.thu, "19:00"), 2)
        r1 = src.req("POST", "/reservations", b, token=tok, key=k)
        E = src.req("GET", "/_test/export").json
        r = c.req("POST", "/_test/import", E, timeout=12)
        chk.expect(f"[{label}] candidate imports the export -> 204", r, 204, section="S3 Upgrade")
        ref = (r1.json or {}).get("reference")
        g = c.req("GET", f"/reservations/{ref}", token=tok)
        j = g.json if isinstance(g.json, dict) else {}
        chk.check(f"[{label}] old token and lookup work; imported booking has revision 1 under policy 0", g.status == 200 and j.get("revision") == 1
                  and (j.get("accepted_terms") or {}).get("policy_version") == 0, "rev 1, v0", g.text(300), g.req, "S3 Upgrade")
        e = c.req("GET", f"/reservations/{ref}/history", token=tok)
        ent = (e.json or {}).get("entries", []) if e.status == 200 else None
        chk.check(f"[{label}] imported booking has a history entry", ent is not None and len(ent) >= 1 and ent[0].get("event") == "created", "created",
                  e.text(300), e.req, "S3 Upgrade")
        rr = c.req("POST", "/reservations", b, token=tok, key=k)
        chk.check(f"[{label}] original booking retry -> 200 original body", rr.status == 200 and rr.json == r1.json, r1.json, rr.text(300), rr.req, "S3 Upgrade")
        # two-week interval: the stage-1 fixture seeds every table one week after ctx.thu
        r = c.req("POST", "/series", {"anchor_reference": ref, "count": 2, "interval_weeks": 2}, token=tok, key=uuid.uuid4().hex)
        chk.expect(f"[{label}] adoption works on an imported reservation", r, 201, section="S3 Upgrade")
        r = c.req("GET", f"/restaurants/{rid}/policies")
        chk.check(f"[{label}] imported restaurant has no published policies", r.status == 200 and r.json == {"policies": []}, [], r.text(200), r.req, "S3 Upgrade")



# --------------------------------------------------------------------------- stage 4: replans, closures, series amend

import itertools as _it
import random as _random

REP_TABLES = [{"id": "a_1", "label": "Alder", "capacity": 2}, {"id": "a_2", "label": "Birch", "capacity": 2},
              {"id": "a_3", "label": "Cedar", "capacity": 4}, {"id": "a_4", "label": "Damson", "capacity": 4},
              {"id": "a_5", "label": "Elm", "capacity": 6}]
REP_PAIRS = [["a_1", "a_2"], ["a_3", "a_4"], ["a_2", "a_3"]]


def fixture4(ctx: Ctx, tables=None, pairs=None, extra_restaurants=()) -> dict:
    fx = fixture3(ctx)
    fx["restaurants"].append({"id": "r_rep", "name": "Replan Room", "timezone": "Europe/Berlin", "slot_minutes": 30,
                              "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
                              "opening_hours": _hours("12:00", "23:00"), "tables": tables or REP_TABLES,
                              "combinable": REP_PAIRS if pairs is None else pairs, "manager_user_ids": ["u_mgr"]})
    fx["restaurants"].extend(extra_restaurants)
    return fx


def iso(local, tz="Europe/Berlin"):
    return rfc(resolve(local, tz))


class Planner:
    """Independent brute-force optimiser for the stage-4 seating repair (three-level objective)."""

    def __init__(self, tables, pairs):
        self.tables = [t["id"] for t in tables]
        self.options = [[t] for t in self.tables] + [list(pr) for pr in pairs]   # rank = index

    def plan(self, considered, fixed, closures, closure):
        """considered/fixed: dicts {reference, table_ids, start, end, party, caps}; closures: [(table, start, end)]."""
        all_closures = list(closures) + [closure]
        cand = []
        for b in considered:
            opts = []
            for rank, o in enumerate(self.options):
                cap = sum(b["caps"].get(t, 0) for t in o)
                if cap < b["party"]:
                    continue
                if any(t in o and cs < b["end"] and b["start"] < ce for t, cs, ce in all_closures):
                    continue
                if any(set(o) & set(f["table_ids"]) and f["start"] < b["end"] and b["start"] < f["end"] for f in fixed):
                    continue
                opts.append((rank, o, cap))
            if not opts:
                return None
            cand.append(opts)
        best = None
        order = sorted(range(len(considered)), key=lambda i: considered[i]["reference"])
        for combo in _it.product(*cand):
            ok = True
            for i in range(len(combo)):
                for j in range(i + 1, len(combo)):
                    bi, bj = considered[i], considered[j]
                    if set(combo[i][1]) & set(combo[j][1]) and bi["start"] < bj["end"] and bj["start"] < bi["end"]:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            moved = sum(1 for b, c in zip(considered, combo) if set(c[1]) != set(b["table_ids"]))
            unused = sum(c[2] - b["party"] for b, c in zip(considered, combo))
            key = (moved, unused, [combo[i][0] for i in order])
            if best is None or key < best[0]:
                best = (key, combo)
        if best is None:
            return None
        key, combo = best
        return {"moved_count": key[0], "unused_seats": key[1],
                "assignments": [{"reference": considered[i]["reference"], "table_ids": combo[i][1],
                                 "changed": set(combo[i][1]) != set(considered[i]["table_ids"])} for i in order]}


def _booking_view(res):
    return {"reference": res["reference"], "table_ids": res.get("table_ids") or [res.get("table_id")],
            "start": parse_rfc(res["starts_at"]), "end": parse_rfc(res["ends_at"]), "party": res["party_size"],
            "caps": (res.get("accepted_terms") or {}).get("capacities") or {}}


class S4(S3):
    def reset4(self, **kw):
        r = self.c.req("POST", "/_test/reset", fixture4(self.ctx, **kw), timeout=12)
        if r.status != 204:
            raise RuntimeError(f"reset fixture4 failed: {r.status} {r.text(300)}")
        self.s.ada, self.s.bob, self.mgr = self.s.login(ADA), self.s.login(BOB), self.s.login(MGR)

    def preview(self, rid, table, frm, to, key=None, tok=None, extra=None):
        return self.c.req("POST", f"/restaurants/{rid}/replans", {"table_id": table, "from": frm, "to": to, **(extra or {})},
                          token=tok or self.mgr, key=key or uuid.uuid4().hex)

    def apply(self, rid, plan_id, key=None, tok=None, body=None):
        return self.c.req("POST", f"/restaurants/{rid}/replans/{plan_id}/apply", {} if body is None else body,
                          token=tok or self.mgr, key=key or uuid.uuid4().hex)

    def bookings(self, tokens, rid):
        out = {}
        for t in tokens:
            for r in self.s.mine(t):
                if r.get("restaurant_id") == rid and r.get("status") == "confirmed":
                    out[r["reference"]] = r
        return out


def _expected_plan(s4, tokens, rid, table, frm, to, tables, pairs, closures=()):
    rows = [_booking_view(r) for r in s4.bookings(tokens, rid).values()]
    f, t = parse_rfc(frm), parse_rfc(to)
    considered = [b for b in rows if b["start"] < t and f < b["end"]]
    fixed = [b for b in rows if not (b["start"] < t and f < b["end"])]
    if len(considered) > 6 or len(tables) > 6 or len(pairs) > 4:
        return "planning_limit"
    return Planner(tables, pairs).plan(considered, fixed, list(closures), (table, f, t))


def _plan_matches(r, exp):
    j = r.json if isinstance(r.json, dict) else {}
    got = [{"reference": a.get("reference"), "table_ids": a.get("table_ids"), "changed": a.get("changed")} for a in j.get("assignments", [])]
    want = [{"reference": a["reference"], "table_ids": a["table_ids"], "changed": a["changed"]} for a in exp["assignments"]]
    return r.status == 201 and got == want and j.get("moved_count") == exp["moved_count"] and j.get("unused_seats") == exp["unused_seats"], got


def g_replan(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s4 = S4(s)
    s4.reset4()
    D = ctx.thu
    L = lambda h: f"{D}T{h}"  # noqa: E731
    b1 = s.book(s.ada, "r_rep", "a_3", L("18:00"), 4)
    b2 = s.book(s.bob, "r_rep", "a_4", L("19:00"), 3)
    b3 = s.book(s.ada, "r_rep", "a_1", L("20:00"), 2)
    b4 = s.book(s.bob, "r_rep", "a_3", L("20:30"), 2)
    f1 = s.book(s.ada, "r_rep", "a_2", L("21:30"), 2)        # starts at the closure's end: fixed, and blocks a_2 for b4
    frm, to = iso(L("18:00")), iso(L("21:30"))
    snap = {r: s.get(s.ada if r in (b1["reference"], b3["reference"], f1["reference"]) else s.bob, r).json for r in
            (b1["reference"], b2["reference"], b3["reference"], b4["reference"], f1["reference"])}
    hist0 = {r: len(s4.entries(r, s.ada if r in (b1["reference"], b3["reference"], f1["reference"]) else s.bob) or []) for r in snap}
    # auth / order
    chk.expect("replan without token -> 401", c.req("POST", "/restaurants/r_rep/replans", {"table_id": "a_3", "from": frm, "to": to}, key="k"), 401,
               "unauthenticated", "S4 Replans")
    chk.expect("replan by a non-manager -> 403", s4.preview("r_rep", "a_3", frm, to, tok=s.ada), 403, "forbidden", "S4 Replans")
    chk.expect("replan without key -> 400", c.req("POST", "/restaurants/r_rep/replans", {"table_id": "a_3", "from": frm, "to": to}, token=s4.mgr), 400,
               "missing_idempotency_key", "S4 Replans")
    chk.expect("replan on an unknown restaurant -> 404", s4.preview("nope", "a_3", frm, to), 404, "not_found", "S4 Replans")
    chk.expect("replan on an unknown table -> 404", s4.preview("r_rep", "zz", frm, to), 404, "not_found", "S4 Replans")
    chk.expect("replan on another restaurant's table -> 404", s4.preview("r_rep", "p_1", frm, to), 404, "not_found", "S4 Replans")
    for name, f_, t_ in (("from == to", frm, frm), ("from after to", to, frm), ("no offset", f"{D}T18:00:00", to), ("Z offset form", frm, to.replace("+01:00", "Z")),
                         ("not a time", "tonight", to), ("date only", str(D), to), ("empty from", "", to)):
        exp_st = 201 if name == "Z offset form" else 422
        chk.expect(f"replan interval {name} -> {exp_st} (R-62)", s4.preview("r_rep", "a_3", f_, t_), exp_st, None if exp_st == 201 else "validation_failed",
                   "S4 Replans")
    for name, body in (("missing table_id", {"from": frm, "to": to}), ("missing from", {"table_id": "a_3", "to": to}), ("missing to", {"table_id": "a_3", "from": frm})):
        chk.expect(f"replan {name} -> 422", c.req("POST", "/restaurants/r_rep/replans", body, token=s4.mgr, key=uuid.uuid4().hex), 422, "validation_failed",
                   "S4 Replans")
    for name, body, st_, code in (("table_id a number", {"table_id": 3, "from": frm, "to": to}, 400, "malformed_request"),
                                  ("from a number", {"table_id": "a_3", "from": 5, "to": to}, 422, "validation_failed"),
                                  ("table_id empty", {"table_id": "", "from": frm, "to": to}, 422, "validation_failed"),
                                  ("table_id 65 chars", {"table_id": "t" * 65, "from": frm, "to": to}, 422, "validation_failed"),
                                  ("bad interval beats unknown table", {"table_id": "zz", "from": to, "to": frm}, 422, "validation_failed")):
        chk.expect(f"replan {name} -> {st_} (R-62)", c.req("POST", "/restaurants/r_rep/replans", body, token=s4.mgr, key=uuid.uuid4().hex), st_, code,
                   "S4 Replans")
    chk.expect("non-manager with an invalid body -> 403 (R-62)", c.req("POST", "/restaurants/r_rep/replans", {"table_id": 3}, token=s.ada, key=uuid.uuid4().hex),
               403, "forbidden", "S4 Replans")
    r0 = s4.preview("r_rep", "a_5", iso(f"{D}T13:00"), iso(f"{D}T14:00"))
    j0 = r0.json if isinstance(r0.json, dict) else {}
    chk.check("preview with no considered bookings is feasible: empty assignments, 0, 0 (R-62)", r0.status == 201 and j0.get("assignments") == []
              and j0.get("moved_count") == 0 and j0.get("unused_seats") == 0, "empty plan", r0.text(200), r0.req, "S4 Replans")
    # preview vs brute force
    k = "rp-" + uuid.uuid4().hex
    r = s4.preview("r_rep", "a_3", frm, to, key=k)
    exp = _expected_plan(s4, [s.ada, s.bob], "r_rep", "a_3", frm, to, REP_TABLES, REP_PAIRS)
    ok, got = _plan_matches(r, exp)
    chk.check("preview equals the brute-force optimum (assignments in reference order, moved_count, unused_seats)", ok, exp, {"status": r.status, "got": got,
              "body": r.text(300)}, r.req, "S4 Replans")
    j = r.json if isinstance(r.json, dict) else {}
    chk.check("preview shape: plan_id, restaurant_revision, closure echo", isinstance(j.get("plan_id"), str) and isinstance(j.get("restaurant_revision"), int)
              and (j.get("closure") or {}).get("table_id") == "a_3" and parse_rfc((j.get("closure") or {}).get("from") or "") == parse_rfc(frm)
              and parse_rfc((j.get("closure") or {}).get("to") or "") == parse_rfc(to), "shape", r.text(300), r.req, "S4 Replans")
    chk.check("preview body has exactly the R-65 keys; plan_id <= 64", set(j) == {"plan_id", "restaurant_revision", "closure", "assignments", "moved_count",
              "unused_seats"} and 0 < len(j.get("plan_id") or "") <= 64 and all(set(a) == {"reference", "table_ids", "changed"} for a in j.get("assignments", [])),
              "exact keys", sorted(j), r.req, "S4 Replans")
    chk.check("closure from/to written in the restaurant offset, whole seconds, never Z (R-65)", (j.get("closure") or {}).get("from") == frm
              and (j.get("closure") or {}).get("to") == to, [frm, to], j.get("closure"), r.req, "S4 Replans")
    rz = s4.preview("r_rep", "a_3", frm, rfc(parse_rfc(to).astimezone(timezone.utc)).replace("+00:00", "Z"))
    chk.check("a Z instant is echoed in the restaurant offset (R-65)", rz.status == 201 and (rz.json or {}).get("closure", {}).get("to") == to, to,
              (rz.json or {}).get("closure"), rz.req, "S4 Replans")
    chk.check("restaurant_revision counts the 5 successful bookings since reset", j.get("restaurant_revision") == 5, 5, j.get("restaurant_revision"), r.req,
              "S4 Replans")
    plan_id = j.get("plan_id")
    rr = s4.preview("r_rep", "a_3", frm, to, key=k)
    chk.check("preview replay -> 200 identical (same plan_id)", rr.status == 200 and rr.json == r.json, 200, rr.text(200), rr.req, "S4 Replans/§7")
    # preview changes nothing
    now = {ref: s.get(s.ada if ref in (b1["reference"], b3["reference"], f1["reference"]) else s.bob, ref).json for ref in snap}
    chk.check("preview changes no booking", now == snap, "unchanged", None, None, "S4 Replans")
    chk.check("preview adds no history", all(len(s4.entries(ref, s.ada if ref in (b1["reference"], b3["reference"], f1["reference"]) else s.bob) or []) == n
                                               for ref, n in hist0.items()), "unchanged", None, None, "S4 Replans")
    t = s.avail_tables("r_rep", D, 2, "20:30")
    chk.check("preview records no closure (a_3 still offered where free)", t is not None and "a_3" in (s.avail_tables("r_rep", D, 2, "13:00") or []), "a_3",
              t, None, "S4 Replans")
    r2 = s4.preview("r_rep", "a_3", frm, to)
    chk.check("another preview: revision unchanged by previews", (r2.json or {}).get("restaurant_revision") == 5, 5, r2.text(200), r2.req, "S4 Replans")
    # apply errors
    chk.expect("apply by a non-manager -> 403", s4.apply("r_rep", plan_id, tok=s.ada), 403, "forbidden", "S4 Apply")
    chk.expect("apply an unknown plan -> 404", s4.apply("r_rep", "nope"), 404, "not_found", "S4 Apply")
    chk.expect("apply a plan through another restaurant -> 404", s4.apply("r_pol", plan_id), 404, "not_found", "S4 Apply")
    chk.expect("apply without key -> 400", c.req("POST", f"/restaurants/r_rep/replans/{plan_id}/apply", {}, token=s4.mgr), 400, "missing_idempotency_key",
               "S4 Apply")
    # stale plan
    stale = (s4.preview("r_rep", "a_3", frm, to).json or {}).get("plan_id")
    s.book(s.bob, "r_rep", "a_5", L("13:00"), 2)                   # unrelated write: revision 6
    before = {ref: s.get(s.ada if ref in (b1["reference"], b3["reference"], f1["reference"]) else s.bob, ref).json for ref in snap}
    chk.expect("apply after an intervening restaurant revision -> 409 stale_plan", s4.apply("r_rep", stale), 409, "stale_plan", "S4 Apply")
    chk.expect("the original plan is stale too", s4.apply("r_rep", plan_id), 409, "stale_plan", "S4 Apply")
    chk.check("stale apply changed nothing", {ref: s.get(s.ada if ref in (b1["reference"], b3["reference"], f1["reference"]) else s.bob, ref).json
                                               for ref in snap} == before, "unchanged", None, None, "S4 Apply")
    # a write at another restaurant does not invalidate
    fresh = s4.preview("r_rep", "a_3", frm, to)
    pid = (fresh.json or {}).get("plan_id")
    exp = _expected_plan(s4, [s.ada, s.bob], "r_rep", "a_3", frm, to, REP_TABLES, REP_PAIRS)
    s.book(s.ada, "r_pol", "p_1", L("13:00"), 2)
    ka = "ap-" + uuid.uuid4().hex
    ra = s4.apply("r_rep", pid, key=ka)
    ja = ra.json if isinstance(ra.json, dict) else {}
    chk.check("a write at another restaurant does not invalidate; apply -> 201 with revision +1", ra.status == 201 and ja.get("plan_id") == pid
              and ja.get("restaurant_revision") == 7, {"revision": 7}, ra.text(300), ra.req, "S4 Apply")
    refs = [a["reference"] for a in exp["assignments"]]
    chk.check("apply lists every considered booking in reference order", [x.get("reference") for x in ja.get("reservations", [])] == refs, refs,
              [x.get("reference") for x in ja.get("reservations", [])], ra.req, "S4 Apply")
    for a in exp["assignments"]:
        tok = s.ada if a["reference"] in (b1["reference"], b3["reference"], f1["reference"]) else s.bob
        g = s.get(tok, a["reference"]).json or {}
        old = before[a["reference"]]
        e = s4.entries(a["reference"], tok) or []
        if a["changed"]:
            chk.check(f"moved {a['reference']}: new tables, times/party/terms unchanged, revision +1",
                      sorted(g.get("table_ids") or []) == sorted(a["table_ids"]) and g.get("starts_at") == old["starts_at"] and g.get("ends_at") == old["ends_at"]
                      and g.get("party_size") == old["party_size"] and g.get("accepted_terms") == old["accepted_terms"]
                      and g.get("revision") == old["revision"] + 1, a, g, None, "S4 Apply")
            last = e[-1] if e else {}
            chk.check(f"moved {a['reference']}: one reassigned entry with plan_id and a table_ids change", len(e) == hist0[a["reference"]] + 1
                      and last.get("event") == "reassigned" and last.get("plan_id") == pid
                      and [ch.get("field") for ch in last.get("changes", [])] == ["table_ids"], "reassigned", last, None, "S4 Apply")
            ch = (last.get("changes") or [{}])[0]
            chk.check(f"moved {a['reference']}: reassigned from/to are the full old and new sets; entry revision and terms (R-67)",
                      ch.get("from") == (old.get("table_ids") or [old.get("table_id")]) and ch.get("to") == a["table_ids"] and last.get("revision") == old["revision"] + 1
                      and last.get("accepted_terms") == old.get("accepted_terms"), {"from": old.get("table_ids"), "to": a["table_ids"]}, ch, None, "S4 Apply")
        else:
            chk.check(f"unmoved {a['reference']} gains nothing", g == old and len(e) == hist0[a["reference"]], "unchanged", g, None, "S4 Apply")
    chk.expect("same plan, second key -> 409 plan_already_applied", s4.apply("r_rep", pid), 409, "plan_already_applied", "S4 Apply")
    rr = s4.apply("r_rep", pid, key=ka)
    chk.check("apply replay -> 200 original response", rr.status == 200 and rr.json == ra.json, 200, rr.text(200), rr.req, "S4 Apply")
    # closure effects
    sl, rq = _slot(s, "r_rep", D, 2, "20:30")
    chk.check("closure excludes the table from available_table_ids during the closure", sl is not None and "a_3" not in (sl.get("available_table_ids") or []),
              "a_3 absent", (sl or {}).get("available_table_ids"), rq.req, "S4 Closures")
    chk.check("closure excludes pairs containing the table", sl is not None and not any("a_3" in o[0] for o in opts(sl)), "no a_3 pairs", opts(sl), rq.req, "S4 Closures")
    sl2, _ = _slot(s, "r_rep", D, 2, "13:00")
    chk.check("outside the closure the table is offered again", sl2 is not None and "a_3" in (sl2.get("available_table_ids") or []), "a_3", sl2, None, "S4 Closures")
    r = c.req("GET", "/availability", query={"restaurant_id": "r_rep", "date": str(D), "party_size": 2, "explain": "true"})
    ex = next((e for x in (r.json or {}).get("slots", []) if x["starts_at_local"].endswith("17:00") for e in x.get("explain", []) if e.get("table_id") == "a_3"), None)
    chk.check("explain: no_overlap false under the closure", ex is not None and [x.get("holds") for x in ex.get("rules", [])][1] is False, False, ex, r.req, "S4 Closures")
    chk.expect("create on the closed table -> 409", c.req("POST", "/reservations", booking_body("r_rep", "a_3", L("19:30"), 2), token=s.bob, key=uuid.uuid4().hex),
               409, "table_unavailable", "S4 Closures")
    chk.expect("create a pair containing the closed table -> 409", c.req("POST", "/reservations", body2("r_rep", ["a_3", "a_4"], L("21:00"), 5), token=s.bob,
                                                                         key=uuid.uuid4().hex), 409, "table_unavailable", "S4 Closures")
    chk.expect("amend onto the closed table -> 409", c.req("PATCH", f"/reservations/{f1['reference']}", {"table_id": "a_3", "starts_at_local": L("20:30")},
                                                           token=s.ada), 409, "table_unavailable", "S4 Closures")
    r3 = s4.preview("r_rep", "a_3", iso(L("19:00")), iso(L("21:00")))
    exp3 = _expected_plan(s4, [s.ada, s.bob], "r_rep", "a_3", iso(L("19:00")), iso(L("21:00")), REP_TABLES, REP_PAIRS, closures=[("a_3", parse_rfc(frm), parse_rfc(to))])
    chk.check("a closure on an already-closed table is planned normally (R-73)", (r3.status == 409 and exp3 is None) or (exp3 is not None and _plan_matches(r3, exp3)[0]),
              exp3, r3.text(300), r3.req, "S4 Replans")
    r = s4.preview("r_rep", "a_4", frm, to)
    chk.check("restaurant_revision after the plan is 7 (once for the whole plan)", (r.json or {}).get("restaurant_revision") == 7, 7, r.text(200), r.req, "S4 Replans")
    exp2 = _expected_plan(s4, [s.ada, s.bob], "r_rep", "a_4", frm, to, REP_TABLES, REP_PAIRS, closures=[("a_3", parse_rfc(frm), parse_rfc(to))])
    if exp2 is None:
        chk.expect("second closure with no feasible plan -> 409 no_feasible_plan", r, 409, "no_feasible_plan", "S4 Replans")
    else:
        ok, got = _plan_matches(r, exp2)
        chk.check("second preview honours the applied closure (brute force)", ok, exp2, got, r.req, "S4 Replans")


def g_limits(s: S):
    """R-63: 422 planning_limit for > 6 tables, > 4 pairs or > 6 considered bookings; exactly at the limits it plans."""
    chk, ctx = s.chk, s.ctx
    s4 = S4(s)
    D = ctx.thu
    frm, to = iso(f"{D}T18:00"), iso(f"{D}T22:00")
    t7 = [{"id": f"y_{i}", "label": f"Y{i}", "capacity": 4} for i in range(1, 8)]
    s4.reset4(tables=t7, pairs=[])
    chk.expect("7 tables -> 422 planning_limit (R-63)", s4.preview("r_rep", "y_1", frm, to), 422, "planning_limit", "S4 Replans")
    t6 = t7[:6]
    p5 = [["y_1", "y_2"], ["y_3", "y_4"], ["y_5", "y_6"], ["y_1", "y_3"], ["y_2", "y_4"]]
    s4.reset4(tables=t6, pairs=p5)
    chk.expect("5 declared pairs -> 422 planning_limit (R-63)", s4.preview("r_rep", "y_1", frm, to), 422, "planning_limit", "S4 Replans")
    s4.reset4(tables=t6, pairs=p5[:4])
    for i, t in enumerate(["y_1", "y_2", "y_3", "y_4", "y_5", "y_6"]):
        s.book(s.ada, "r_rep", t, f"{D}T{['18:00', '18:30', '19:00', '19:30', '20:00', '20:30'][i]}", 2)
    r = s4.preview("r_rep", "y_1", frm, to)
    exp = _expected_plan(s4, [s.ada, s.bob], "r_rep", "y_1", frm, to, t6, p5[:4])
    if exp is None:
        chk.expect("exactly 6 tables / 4 pairs / 6 considered: planned (409 no_feasible_plan per brute force)", r, 409, "no_feasible_plan", "S4 Replans")
    else:
        ok, got = _plan_matches(r, exp)
        chk.check("exactly 6 tables / 4 pairs / 6 considered: optimal plan (R-63)", ok, exp, got, r.req, "S4 Replans")
    s.book(s.bob, "r_rep", "y_6", f"{D}T18:00", 2)
    chk.expect("7 considered bookings -> 422 planning_limit (R-63)", s4.preview("r_rep", "y_1", frm, to), 422, "planning_limit", "S4 Replans")


def g_optimal(s: S, rounds: int):
    """Random spot-checks of preview optimality against the brute-force planner."""
    c, chk, ctx = s.c, s.chk, s.ctx
    s4 = S4(s)
    rng = _random.Random(int(os.environ.get("AUDIT_SEED", "4242")))
    D = ctx.thu
    for n in range(max(6, rounds * 4)):
        nt = rng.randint(3, 6)
        tables = [{"id": f"x_{i}", "label": f"T{i}", "capacity": rng.choice([2, 2, 4, 4, 6])} for i in range(1, nt + 1)]
        ids = [t["id"] for t in tables]
        allp = [list(p) for p in _it.combinations(ids, 2)]
        rng.shuffle(allp)
        pairs = allp[:rng.randint(0, min(4, len(allp)))]
        s4.reset4(tables=tables, pairs=pairs)
        if rng.random() < 0.4:                                     # a policy changes capacities for later bookings
            caps = {t["id"]: rng.choice([2, 4, 6, 8]) for t in tables}
            s4.publish("r_rep", {**pol(D, 30, 90, 120, "12:00", "23:00", caps)})
        made = 0
        for _ in range(rng.randint(3, 9)):
            o = rng.choice([[t] for t in ids] + pairs)
            h = rng.choice(["17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00"])
            party = rng.randint(1, 6)
            r = c.req("POST", "/reservations", body2("r_rep", o, f"{D}T{h}", party), token=rng.choice([s.ada, s.bob]), key=uuid.uuid4().hex)
            made += r.status == 201
        closed = rng.choice(ids)
        frm, to = iso(f"{D}T{rng.choice(['17:00', '18:00', '19:00'])}"), iso(f"{D}T{rng.choice(['20:00', '21:00', '22:30'])}")
        exp = _expected_plan(s4, [s.ada, s.bob], "r_rep", closed, frm, to, tables, pairs)
        r = s4.preview("r_rep", closed, frm, to)
        if exp == "planning_limit":
            chk.expect(f"spot {n}: more than 6 considered bookings -> 422 planning_limit", r, 422, "planning_limit", "S4 Replans")
        elif exp is None:
            chk.expect(f"spot {n}: infeasible -> 409 no_feasible_plan", r, 409, "no_feasible_plan", "S4 Replans")
        else:
            ok, got = _plan_matches(r, exp)
            chk.check(f"spot {n}: preview equals brute force ({nt} tables, {len(pairs)} pairs, {len(exp['assignments'])} considered, {made} booked)",
                      ok, exp, {"status": r.status, "got": got, "moved": (r.json or {}).get("moved_count"), "unused": (r.json or {}).get("unused_seats"),
                                "body": r.text(200)}, r.req, "S4 Replans")


def g_samend(s: S):
    c, chk, ctx = s.c, s.chk, s.ctx
    s4 = S4(s)
    s4.reset4()
    D = ctx.thu
    A = s.book(s.ada, "r_ser", "s_2", f"{D}T19:00", 2)
    r = c.req("POST", "/series", {"anchor_reference": A["reference"], "count": 5, "interval_weeks": 1}, token=s.ada, key=uuid.uuid4().hex)
    sid = (r.json or {}).get("series_id")
    refs = [o.get("reference") for o in (r.json or {}).get("occurrences", [])]
    if r.status != 201 or len(refs) != 5:
        chk.check("setup: series of 5", False, 201, r.text(200), r.req, "harness")
        return

    def amend(body, key=None, tok=None):
        return c.req("POST", f"/series/{sid}/amend", body, token=tok or s.ada, key=key or uuid.uuid4().hex)

    def series():
        return c.req("GET", f"/series/{sid}", token=s.ada).json or {}

    # validation
    chk.expect("amend without token -> 401", c.req("POST", f"/series/{sid}/amend", {"expected_revision": 1, "from_index": 0, "local_time": "20:00"},
                                                   key="k"), 401, "unauthenticated", "S4 Amend")
    chk.expect("amend another owner's series -> 404", amend({"expected_revision": 1, "from_index": 0, "local_time": "20:00"}, tok=s.bob), 404, "not_found", "S4 Amend")
    chk.expect("amend unknown series -> 404", c.req("POST", "/series/nope/amend", {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}, token=s.ada,
                                                    key=uuid.uuid4().hex), 404, "not_found", "S4 Amend")
    chk.expect("amend without key -> 400", c.req("POST", f"/series/{sid}/amend", {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}, token=s.ada),
               400, "missing_idempotency_key", "S4 Amend")
    good = {"expected_revision": 1, "from_index": 1, "local_time": "20:00"}
    for name, ch in (("expected_revision 0", {"expected_revision": 0}), ("expected_revision true", {"expected_revision": True}),
                     ("expected_revision '1'", {"expected_revision": "1"}), ("expected_revision 1.5", {"expected_revision": 1.5}),
                     ("from_index -1", {"from_index": -1}), ("from_index 5 (= count)", {"from_index": 5}), ("from_index true", {"from_index": True}),
                     ("local_time 24:00", {"local_time": "24:00"}), ("local_time 7:00", {"local_time": "7:00"}), ("local_time 20:00:00", {"local_time": "20:00:00"}),
                     ("local_time 20:60", {"local_time": "20:60"}), ("local_time number", {"local_time": 2000})):
        chk.expect(f"amend {name} -> 422", amend({**good, **ch}), 422, "validation_failed", "S4 Amend")
    for f in ("expected_revision", "from_index", "local_time"):
        b = dict(good); b.pop(f)
        chk.expect(f"amend missing {f} -> 422", amend(b), 422, "validation_failed", "S4 Amend")
    chk.expect("stale series revision -> 409 stale_revision", amend({**good, "expected_revision": 9}), 409, "stale_revision", "S4 Amend")
    chk.expect("stale beats an invalid local time for the occurrences (outside hours)", amend({**good, "expected_revision": 9, "local_time": "23:30"}), 409,
               "stale_revision", "S4 Amend")
    # exception and cancelled occurrences are not eligible
    c.req("PATCH", f"/reservations/{refs[2]}", {"party_size": 3}, token=s.ada)          # exception, series rev 2
    c.req("POST", f"/reservations/{refs[3]}/cancel", token=s.ada)                       # cancelled, series rev 3
    snap = {x: s.get(s.ada, x).json for x in refs}
    hist = {x: len(s4.entries(x, s.ada) or []) for x in refs}
    def rrev():
        return (s4.preview("r_ser", "s_1", iso(f"{D}T12:00"), iso(f"{D}T12:30")).json or {}).get("restaurant_revision")
    rev_before = rrev()
    k = "am-" + uuid.uuid4().hex
    r = amend({"expected_revision": 3, "from_index": 1, "local_time": "20:00", "zzz": 1}, key=k)
    j = r.json if isinstance(r.json, dict) else {}
    st = {o.get("reference"): (o.get("reservation") or {}).get("starts_at_local") for o in j.get("occurrences", [])}
    want = {refs[0]: f"{D}T19:00", refs[1]: f"{D + timedelta(days=7)}T20:00", refs[2]: f"{D + timedelta(days=14)}T19:00",
            refs[3]: f"{D + timedelta(days=21)}T19:00", refs[4]: f"{D + timedelta(days=28)}T20:00"}
    chk.check("amend 201: eligible occurrences (1, 4) move to 20:00 on their scheduled dates; exception (2), cancelled (3) and index 0 untouched",
              r.status == 201 and st == want, want, {"status": r.status, "st": st}, r.req, "S4 Amend")
    chk.check("amend: series revision +1 once; no exceptions marked", j.get("revision") == 4 and [o.get("exception") for o in j.get("occurrences", [])]
              == [False, False, True, False, False], "rev 4", {"rev": j.get("revision"), "ex": [o.get("exception") for o in j.get("occurrences", [])]}, r.req, "S4 Amend")
    for i in (1, 4):
        g = s.get(s.ada, refs[i]).json or {}
        e = s4.entries(refs[i], s.ada) or []
        chk.check(f"occurrence {i}: one changed entry (starts_at_local) and revision +1", g.get("revision") == snap[refs[i]]["revision"] + 1
                  and len(e) == hist[refs[i]] + 1 and e[-1].get("event") == "changed" and [ch.get("field") for ch in e[-1].get("changes", [])] == ["starts_at_local"],
                  "changed", e[-1:] if e else None, None, "S4 Amend")
    for i in (0, 2, 3):
        chk.check(f"occurrence {i} unchanged", s.get(s.ada, refs[i]).json == snap[refs[i]], "unchanged", None, None, "S4 Amend")
    chk.check("a changing amend increments the restaurant revision once (R-69)", rev_before is not None and rrev() == rev_before + 1, (rev_before or 0) + 1,
              rrev(), None, "S4 Amend")
    rr = amend({"expected_revision": 3, "from_index": 1, "local_time": "20:00", "zzz": 1}, key=k)
    chk.check("amend replay -> 200 original", rr.status == 200 and rr.json == r.json, 200, rr.text(200), rr.req, "S4 Amend")
    r = amend({"expected_revision": 4, "from_index": 1, "local_time": "20:00"})
    chk.check("all-no-op amend -> 201 without changing revisions (R-71)", r.status == 201 and (r.json or {}).get("revision") == 4, 4, r.text(200), r.req, "S4 Amend")
    # failure atomicity: occupancy on a later occurrence
    snap = {x: s.get(s.ada, x).json for x in refs}
    blocker = s.book(s.bob, "r_ser", "s_2", f"{D + timedelta(days=28)}T21:30", 2)
    r = amend({"expected_revision": 4, "from_index": 0, "local_time": "21:00"})
    chk.expect("amend colliding at occurrence 4 -> 409 table_unavailable", r, 409, "table_unavailable", "S4 Amend")
    chk.check("failed amend changes nothing", {x: s.get(s.ada, x).json for x in refs} == snap and series().get("revision") == 4, "unchanged", None, None, "S4 Amend")
    c.req("POST", f"/reservations/{blocker['reference']}/cancel", token=s.bob)
    r = amend({"expected_revision": 4, "from_index": 0, "local_time": "22:00"})
    chk.expect("non-occupancy error (22:00 + 90 > closes) -> 422 outside_opening_hours", r, 422, "outside_opening_hours", "S4 Amend")
    r = amend({"expected_revision": 4, "from_index": 0, "local_time": "19:10"})
    chk.expect("off-grid local time -> 422 not_on_slot_grid", r, 422, "not_on_slot_grid", "S4 Amend")


def g_s4burst(s: S, rounds: int):
    c, chk, ctx = s.c, s.chk, s.ctx
    base = c.base
    s4 = S4(s)
    for rnd in range(rounds):
        s4.reset4()
        D = ctx.thu + timedelta(days=7 * (rnd % 3))
        L = lambda h: f"{D}T{h}"  # noqa: E731
        s.book(s.ada, "r_rep", "a_3", L("18:00"), 4)
        s.book(s.bob, "r_rep", "a_4", L("19:00"), 3)
        s.book(s.bob, "r_rep", "a_3", L("20:30"), 2)
        frm, to = iso(L("18:00")), iso(L("22:00"))
        pid = (s4.preview("r_rep", "a_3", frm, to).json or {}).get("plan_id")
        before = {r: x for r, x in s4.bookings([s.ada, s.bob], "r_rep").items()}
        rs = burst.fire([plan_entry(base, "POST", f"/restaurants/r_rep/replans/{pid}/apply", {}, s4.mgr, f"q1-{rnd}-{i}") for i in range(12)])
        st = statuses(rs)
        codes_ = {json.loads(r.body).get("error", {}).get("code") for r in rs if r.status == 409}
        after = s4.bookings([s.ada, s.bob], "r_rep")
        moved = [r for r in after if (after[r].get("table_ids") or [after[r].get("table_id")]) != (before[r].get("table_ids") or [before[r].get("table_id")])]
        revs_ok = all(after[r]["revision"] == before[r]["revision"] + (1 if r in moved else 0) for r in after)
        chk.check(f"r{rnd} Q1 12 concurrent applications of one plan: one 201, rest 409 (already_applied/stale), each moved booking +1 once",
                  st.get("201") == 1 and set(st) <= {"201", "409"} and codes_ <= {"plan_already_applied", "stale_plan"} and revs_ok,
                  "one application", {"st": st, "codes": sorted(codes_ or []), "moved": moved}, {"burst": "Q1"}, "S4 Apply")
        # two different plans from one revision
        s4.reset4()
        s.book(s.ada, "r_rep", "a_3", L("18:00"), 4)
        s.book(s.bob, "r_rep", "a_4", L("19:00"), 3)
        p1 = (s4.preview("r_rep", "a_3", frm, to).json or {}).get("plan_id")
        p2 = (s4.preview("r_rep", "a_4", frm, to).json or {}).get("plan_id")
        rs = burst.fire([plan_entry(base, "POST", f"/restaurants/r_rep/replans/{p1 if i % 2 else p2}/apply", {}, s4.mgr, f"q2-{rnd}-{i}") for i in range(10)])
        st = statuses(rs)
        codes_ = {json.loads(r.body).get("error", {}).get("code") for r in rs if r.status == 409}
        chk.check(f"r{rnd} Q2 two plans from one revision applied concurrently: exactly one 201, the other plan stale",
                  st.get("201") == 1 and "stale_plan" in codes_, "one plan", {"st": st, "codes": sorted(codes_)}, {"burst": "Q2"}, "S4 Apply")
        check_invariant(s, [s.ada, s.bob], f"r{rnd} after plan bursts", "S4 Apply")
        # concurrent series amendments from one expected revision
        A = s.book(s.ada, "r_ser", "s_1", L("13:00"), 2)
        r = c.req("POST", "/series", {"anchor_reference": A["reference"], "count": 3, "interval_weeks": 1}, token=s.ada, key=uuid.uuid4().hex)
        sid = (r.json or {}).get("series_id")
        rs = burst.fire([plan_entry(base, "POST", f"/series/{sid}/amend", {"expected_revision": 1, "from_index": 0,
                                                                            "local_time": ["14:00", "14:30", "15:00", "15:30"][i % 4]}, s.ada, f"q3-{rnd}-{i}")
                         for i in range(12)])
        st = statuses(rs)
        sj = c.req("GET", f"/series/{sid}", token=s.ada).json or {}
        chk.check(f"r{rnd} Q3 12 concurrent amends from revision 1: one real change (series revision 2), others 409 stale",
                  st.get("201") == 1 and st.get("409") == 11 and sj.get("revision") == 2, {"201": 1, "409": 11}, {"st": st, "rev": sj.get("revision")},
                  {"burst": "Q3"}, "S4 Amend")
        # replayed previews
        kp = f"q4-{rnd}-{uuid.uuid4().hex}"
        rs = burst.fire([plan_entry(base, "POST", "/restaurants/r_rep/replans", {"table_id": "a_5", "from": frm, "to": to}, s4.mgr, kp) for _ in range(10)])
        st = statuses(rs)
        bodies = {json.dumps(json.loads(r.body), sort_keys=True) for r in rs if r.status in (200, 201)}
        chk.check(f"r{rnd} Q4 10 identical keyed previews: one 201, nine identical 200", st == {"201": 1, "200": 9} and len(bodies) == 1, {"201": 1, "200": 9}, st,
                  {"burst": "Q4"}, "S4 Replans")


def g_upgrade4(s: S, prev: Client | None, prev2: Client | None, prev3: Client | None):
    c, chk, ctx = s.c, s.chk, s.ctx
    for label, src, fx, rid, tid in (("stage 1", prev, fixture, "r_anker", "t_2"), ("stage 2", prev2, fixture2, "r_combo", "c_4"),
                                     ("stage 3", prev3, fixture3, "r_ser", "s_2")):
        if src is None:
            chk.check(f"upgrade source {label} given", False, "--prev/--prev2/--prev3", None, None, "harness")
            continue
        sp = S(src, chk, ctx)
        src.req("POST", "/_test/reset", fx(ctx), timeout=12)
        tok = sp.signup(email=f"legacy4{label[-1]}@example.com", password="legacy pass 1", name="Legacy")
        k = "up4-" + uuid.uuid4().hex
        b = booking_body(rid, tid, ctx.D(ctx.thu, "19:00"), 2)
        r1 = src.req("POST", "/reservations", b, token=tok, key=k)
        ref = (r1.json or {}).get("reference")
        sid = None
        if label == "stage 3":
            rs = src.req("POST", "/series", {"anchor_reference": ref, "count": 3, "interval_weeks": 2}, token=tok, key=uuid.uuid4().hex)
            sid = (rs.json or {}).get("series_id")
            occ = [o.get("reference") for o in (rs.json or {}).get("occurrences", [])]
            if len(occ) == 3:
                src.req("POST", f"/reservations/{occ[2]}/cancel", token=tok)
        E = src.req("GET", "/_test/export").json
        if SAVE_EXPORTS is not None:
            SAVE_EXPORTS[label] = {"export": E, "token": tok, "reference": ref, "key": k, "body": b, "first": r1.json}
        r = c.req("POST", "/_test/import", E, timeout=12)
        chk.expect(f"[{label}] candidate imports the export -> 204", r, 204, section="S4 Upgrade")
        g = c.req("GET", f"/reservations/{ref}", token=tok)
        chk.expect(f"[{label}] old token and lookup work", g, 200, section="S4 Upgrade")
        rr = c.req("POST", "/reservations", b, token=tok, key=k)
        chk.check(f"[{label}] original retry -> 200 original body", rr.status == 200 and rr.json == r1.json, r1.json, rr.text(200), rr.req, "S4 Upgrade")
        if sid:
            sj = c.req("GET", f"/series/{sid}", token=tok)
            rev = (sj.json or {}).get("revision")
            ra = c.req("POST", f"/series/{sid}/amend", {"expected_revision": rev, "from_index": 0, "local_time": "20:00"}, token=tok, key=uuid.uuid4().hex)
            chk.check(f"[{label}] imported series (with a cancelled occurrence) can be amended", ra.status == 201, 201, ra.text(200), ra.req, "S4 Upgrade")
        mgr = c.req("POST", "/auth/login", {"email": MGR["email"], "password": MGR["password"]})
        if mgr.status == 200 and label == "stage 3":
            pv = c.req("POST", f"/restaurants/{rid}/replans", {"table_id": tid, "from": iso(ctx.D(ctx.thu, "18:00")), "to": iso(ctx.D(ctx.thu, "22:00"))},
                       token=mgr.json["token"], key=uuid.uuid4().hex)
            chk.check(f"[{label}] replan on an imported restaurant works; revision observable", pv.status in (201, 409) and
                      (pv.status == 409 or isinstance((pv.json or {}).get("restaurant_revision"), int)), "201|409", pv.text(200), pv.req, "S4 Upgrade")

def g_s4rulings(s: S, prev: Client | None, prev2: Client | None):
    """R-75 (fixture integers up to 2^31-1 computed exactly) and R-76 (bookings migrated from stage 1/2, confirmed or
    cancelled: revision 1, exactly one created entry)."""
    c, chk, ctx = s.c, s.chk, s.ctx
    big = 2**31 - 1
    start = ctx.D(ctx.thu, "19:00")

    def other(**kw):
        fx = fixture(ctx)
        r = next(x for x in fx["restaurants"] if x["id"] == "r_other")
        cap = kw.pop("capacity", None)
        r.update(kw)
        if cap is not None:
            r["tables"] = [{"id": "o_1", "label": "O1", "capacity": cap}]
        return fx

    s.reset(other(reservation_duration_minutes=big))
    r = c.req("POST", "/reservations", booking_body("r_other", "o_1", start, 2), token=s.ada, key=uuid.uuid4().hex)
    chk.expect("R-75 duration 2^31-1: no slot fits -> 422 outside_opening_hours", r, 422, "outside_opening_hours", section="C1.22,C1.74 (R-75)")
    av = s.avail("r_other", ctx.thu, 2)
    slots = (av.json or {}).get("slots") if isinstance(av.json, dict) else None
    chk.check("R-75 duration 2^31-1: availability offers no slot", av.status == 200 and slots == [], "slots []", av.text(300), av.req,
              "C1.22,C1.74 (R-75)")
    s.reset(other(cancellation_cutoff_minutes=big))
    b = s.book(s.ada, "r_other", "o_1", start, 2)
    r = c.req("POST", f"/reservations/{b['reference']}/cancel", token=s.ada)
    chk.expect("R-75 cutoff 2^31-1: future booking cannot be cancelled -> 409 cutoff_passed", r, 409, "cutoff_passed", section="C1.23 (R-75)")
    s.reset(other(capacity=big))
    r = c.req("POST", "/reservations", booking_body("r_other", "o_1", start, big), token=s.ada, key=uuid.uuid4().hex)
    chk.expect("R-75 capacity 2^31-1 seats a party of 2^31-1 -> 201", r, 201, section="C1.26 (R-75)")
    # exactness: a seeded booking (not revalidated, C1.30) under a 2^31-1-minute duration ends exactly 2^31-1 minutes later
    fx = other(reservation_duration_minutes=big)
    fx["reservations"].append({"id": "s_big", "reference": "SEEDBG", "user_id": "u_ada", "restaurant_id": "r_other",
                               "table_id": "o_1", "starts_at_local": start, "party_size": 2})
    s.reset(fx)
    g = s.get(s.ada, "SEEDBG")
    j = g.json if isinstance(g.json, dict) else {}
    try:
        exact = parse_rfc(j["ends_at"]) - parse_rfc(j["starts_at"]) == timedelta(minutes=big)
    except Exception:
        exact = False
    chk.check("R-75 seeded booking under duration 2^31-1 ends exactly 2^31-1 minutes after its start", g.status == 200 and exact,
              "ends_at - starts_at == 2147483647 min", g.text(300), g.req, "C1.4,C1.22 (R-75)")
    s.reset(other(reservation_duration_minutes=90, slot_minutes=big))
    av = s.avail("r_other", ctx.thu, 2)
    sl = [x.get("starts_at_local", "")[-5:] for x in ((av.json or {}).get("slots") or [])] if isinstance(av.json, dict) else None
    opens = next((h["opens"] for h in ANKER_HOURS if h["weekday"] == WEEKDAYS[ctx.thu.weekday()]), None)
    chk.check("R-75 slot_minutes 2^31-1: the only slot of a day is its opening time", av.status == 200 and sl == [opens], [opens], sl,
              av.req, "C1.21,C1.72 (R-75)")
    for field in ("reservation_duration_minutes", "cancellation_cutoff_minutes", "slot_minutes", "capacity"):
        r = c.req("POST", "/_test/reset", other(**{field: big + 1}), timeout=12)
        chk.expect(f"R-75 {field} 2^31 -> reset 422", r, 422, "validation_failed", section="C1.26 (R-75)")
    for label, src, fx, rid, tid in (("stage 1", prev, fixture, "r_anker", "t_2"), ("stage 2", prev2, fixture2, "r_combo", "c_4")):
        if src is None:
            chk.check(f"R-76 upgrade source {label} given", False, "--prev/--prev2", None, None, "harness")
            continue
        sp = S(src, chk, ctx)
        src.req("POST", "/_test/reset", fx(ctx), timeout=12)
        tok = sp.signup(email=f"r76{label[-1]}@example.com", password="legacy pass 1", name="Legacy")
        refs = {}
        for kind, hhmm in (("confirmed", "19:00"), ("cancelled", "21:00")):
            rb = src.req("POST", "/reservations", booking_body(rid, tid, ctx.D(ctx.thu, hhmm), 2), token=tok, key=uuid.uuid4().hex)
            if rb.status != 201:
                raise RuntimeError(f"R-76 setup {label} {kind}: {rb.status} {rb.text(200)}")
            refs[kind] = rb.json["reference"]
        rc = src.req("POST", f"/reservations/{refs['cancelled']}/cancel", token=tok)
        if rc.status != 200:
            raise RuntimeError(f"R-76 setup {label} cancel: {rc.status} {rc.text(200)}")
        r = c.req("POST", "/_test/import", src.req("GET", "/_test/export").json, timeout=12)
        chk.expect(f"R-76 [{label}] import -> 204", r, 204, section="C3.48 (R-76)")
        for kind, ref in refs.items():
            g = c.req("GET", f"/reservations/{ref}", token=tok)
            j = g.json if isinstance(g.json, dict) else {}
            h = c.req("GET", f"/reservations/{ref}/history", token=tok)
            ent = (h.json or {}).get("entries") if h.status == 200 and isinstance(h.json, dict) else None
            chk.check(f"R-76 [{label}] {kind} booking migrates with revision 1 and exactly one created entry",
                      j.get("status") == kind and j.get("revision") == 1 and isinstance(ent, list) and len(ent) == 1
                      and ent[0].get("event") == "created" and ent[0].get("revision") == 1,
                      f"{kind}, rev 1, [created]", {"res": g.text(200), "history": h.text(300)}, h.req, "C3.28,C3.48,C4.24 (R-76)")
        bad, seen = [], 0
        for u in fx(ctx)["users"]:
            lg = c.req("POST", "/auth/login", {"email": u["email"], "password": u["password"]})
            if lg.status != 200:
                continue
            ut = lg.json["token"]
            for res in (c.req("GET", "/reservations", token=ut).json or {}).get("reservations", []):
                seen += 1
                h = c.req("GET", f"/reservations/{res['reference']}/history", token=ut)
                ent = (h.json or {}).get("entries") if h.status == 200 and isinstance(h.json, dict) else None
                if not (res.get("revision") == 1 and isinstance(ent, list) and len(ent) == 1 and ent[0].get("event") == "created"):
                    bad.append({"ref": res["reference"], "status": res.get("status"), "revision": res.get("revision"),
                                "events": [e.get("event") for e in ent or []]})
        chk.check(f"R-76 [{label}] every seeded booking ({seen}, any status) migrates with revision 1 and exactly one created entry",
                  seen > 0 and not bad, "all rev 1 [created]", bad[:5], None, "C3.28,C3.48,C4.24 (R-76)")


def _closure_records(state):
    """Every stored closure record: restaurants[*].closures[*] and the closure of every stored plan (list or map of plans)."""
    out = []
    for r in state.get("restaurants") or []:
        out += [x for x in (r.get("closures") or []) if isinstance(x, dict)]
    plans = state.get("plans") or {}
    for p in (plans.values() if isinstance(plans, dict) else plans):
        if isinstance(p, dict) and isinstance(p.get("closure"), dict):
            out.append(p["closure"])
    return out


def g_r77(s: S):
    """R-77: imported restaurant and plan closures must name a known table and have two non-null bounds with from < to; otherwise
    422 and the destination is unchanged (C1.109)."""
    c, chk, ctx = s.c, s.chk, s.ctx
    s4 = S4(s)
    s4.reset4()
    L = lambda h: f"{ctx.thu}T{h}"  # noqa: E731
    s.book(s.ada, "r_rep", "a_3", L("18:00"), 4)
    pv = s4.preview("r_rep", "a_3", iso(L("18:00")), iso(L("21:30")))
    ap = s4.apply("r_rep", (pv.json or {}).get("plan_id"))
    if ap.status != 201:
        raise RuntimeError(f"R-77 setup apply: {ap.status} {ap.text(200)}")
    E = c.req("GET", "/_test/export").json
    recs = _closure_records(E["state"])
    chk.check("R-77 setup: the export holds the applied closure (restaurant and plan copies)", len(recs) >= 2, ">= 2 records",
              len(recs), None, "harness")

    def swap(x):
        x["from"], x["to"] = x["to"], x["from"]
    cases = (("from null", lambda x: x.__setitem__("from", None)), ("to null", lambda x: x.__setitem__("to", None)),
             ("from missing", lambda x: x.pop("from", None)), ("to missing", lambda x: x.pop("to", None)),
             ("from == to", lambda x: x.__setitem__("from", x["to"])), ("from > to", swap),
             ("from not a time", lambda x: x.__setitem__("from", "yesterday")),
             ("unknown table", lambda x: x.__setitem__("table_id", "a_9")), ("table missing", lambda x: x.pop("table_id", None)))
    for scope in ("restaurant", "every copy"):
        for name, fn in cases:
            e = copy.deepcopy(E)
            rs = _closure_records(e["state"])
            for x in (rs[:1] if scope == "restaurant" else rs):
                fn(x)
            c.req("POST", "/_test/import", E, timeout=12)
            before = c.req("GET", "/_test/export").json
            r = c.req("POST", "/_test/import", e, timeout=12)
            after = c.req("GET", "/_test/export").json
            chk.check(f"R-77 imported closure, {name} ({scope}) -> 422, destination unchanged",
                      r.status == 422 and r.code() == "validation_failed" and after == before, "422 validation_failed, unchanged",
                      {"status": r.status, "body": r.text(160), "unchanged": after == before}, r.req, "C1.109 (R-77)")
    chk.expect("R-77 the untampered export still imports -> 204", c.req("POST", "/_test/import", E, timeout=12), 204, section="C1.109 (R-77)")


SAVE_EXPORTS: dict | None = None
STATIC_EXPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "static-exports.json")


def g_staticupgrade(s: S):
    """Upgrade from exports captured once from the accepted stage-1/2/3 images (fixtures/static-exports.json, made with
    --save-exports), so runs without the previous-stage images (mutation, race proofs) still cover the schema 1-3 migrations."""
    c, chk = s.c, s.chk
    with open(STATIC_EXPORTS, encoding="utf-8") as f:
        saved = json.load(f)
    for label in ("stage 1", "stage 2", "stage 3"):
        d = saved.get(label)
        if not d:
            chk.check(f"static export {label} present", False, "present", None, None, "harness")
            continue
        r = c.req("POST", "/_test/import", d["export"], timeout=12)
        chk.expect(f"[static {label}] candidate imports the export -> 204", r, 204, section="S4 Upgrade")
        g = c.req("GET", f"/reservations/{d['reference']}", token=d["token"])
        chk.expect(f"[static {label}] old token and lookup work", g, 200, section="S4 Upgrade")
        rr = c.req("POST", "/reservations", d["body"], token=d["token"], key=d["key"])
        chk.check(f"[static {label}] original retry -> 200 original body", rr.status == 200 and rr.json == d["first"], d["first"],
                  rr.text(200), rr.req, "S4 Upgrade")
        e = c.req("GET", "/_test/export")
        ri = c.req("POST", "/_test/import", e.json, timeout=12) if e.status == 200 else e
        chk.expect(f"[static {label}] the upgraded state re-exports and re-imports -> 204", ri, 204, section="S4 Upgrade")

# --------------------------------------------------------------------------- main

GROUPS = ["core", "auth", "availability", "create", "reads", "cancel", "patch", "dst", "idem", "moves", "burst", "export",
          "combo", "comboburst", "upgrade", "explain", "policies", "history", "revision", "series", "s3moves", "s3burst", "upgrade3",
          "replan", "limits", "optimal", "samend", "s4burst", "upgrade4", "staticupgrade", "s4rulings", "r77"]


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
    p.add_argument("--prev", help="previous-stage service (accepted image) for the upgrade group")
    p.add_argument("--prev2", help="second previous-stage service (stage 2) for upgrade3")
    p.add_argument("--prev3", help="third previous-stage service (stage 3) for upgrade4")
    p.add_argument("--groups", default=",".join(GROUPS))
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--out")
    p.add_argument("--wait", type=float, default=60.0)
    p.add_argument("--save-exports", help="write the upgrade4 source exports (+ token, reference, original request) to this JSON file")
    a = p.parse_args(argv)
    global SAVE_EXPORTS
    if a.save_exports:
        SAVE_EXPORTS = {}
    chk = Checker()
    ctx = Ctx()
    c = Client(a.base, chk, "A")
    dest = Client(a.dest, chk, "B") if a.dest else None
    prev = Client(a.prev, chk, "P") if a.prev else None
    prev2 = Client(a.prev2, chk, "Q") if a.prev2 else None
    prev3 = Client(a.prev3, chk, "R") if a.prev3 else None
    meta = {"base": a.base, "dest": a.dest, "prev": a.prev, "now": ctx.now.isoformat(), "now_tz": ctx.now_tz, "thu": str(ctx.thu),
            "near": ctx.near, "far": ctx.far, "past": ctx.past}
    for label, cl in (("A", c), ("B", dest), ("P", prev), ("Q", prev2), ("R", prev3)):
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
            elif g == "combo":
                g_combo(s, dest)
            elif g == "comboburst":
                g_comboburst(s, a.rounds)
            elif g == "upgrade":
                g_upgrade(s, prev, dest)
            elif g == "s3burst":
                g_s3burst(s, a.rounds)
            elif g == "upgrade3":
                g_upgrade3(s, prev, prev2)
            elif g == "optimal":
                g_optimal(s, a.rounds)
            elif g == "s4burst":
                g_s4burst(s, a.rounds)
            elif g == "upgrade4":
                g_upgrade4(s, prev, prev2, prev3)
            elif g == "s4rulings":
                g_s4rulings(s, prev, prev2)
            else:
                globals()[f"g_{g}"](s)
        except Exception as e:
            errors.append({"group": g, "error": repr(e), "trace": traceback.format_exc()[-1500:]})
            chk.check("group ran to completion", False, "no exception", repr(e), None, "harness")
    if a.save_exports:
        with open(a.save_exports, "w", encoding="utf-8", newline="\n") as f:
            json.dump(SAVE_EXPORTS, f, indent=1, sort_keys=True)
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
