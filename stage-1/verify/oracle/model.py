"""Tablekeeper stage-1 reference model (Oracle seat).

The smallest program that computes the specified results for a sequence of HTTP operations.
It models behaviour, not performance: one in-memory state machine, every request handled
atomically. Written from the stage-1 specification (evidence/stage-1/ledger.md, 134 clauses) and
the Foreman's rulings R-1 … R-24 (evidence/stage-1/rulings.md); each ruling is cited where applied.

Entry point: Model.handle(method, path, query, headers, body) -> (status, json_value_or_None)

* `query` is the raw query string (may be ""), `headers` a dict (case-insensitive keys),
  `body` the raw request body as bytes/str (or None).
* reservation ids and references are deterministic from counters; tokens are random (C1.112: a token
  issued by one instance must never collide with an imported one); `created_at` comes from the
  injected clock. A differential runner must alias those against the implementation's values.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
LOCAL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")
REFERENCE_RE = re.compile(r"^[A-Z0-9]{6,12}$")
DIGITS_RE = re.compile(r"^[0-9]+$")
ID_MAX = 64
TRACK = "tablekeeper"
FORMAT_VERSION = 1
STATE_MARKER = "oracle-model-v1"
_SEQ = itertools.count(1)   # process-wide: ids and references are never reissued after a reset or import (C1.81, C1.112)


class Err(Exception):
    """An HTTP error outcome: status + code (+ optional message)."""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code} {message}")
        self.status = status
        self.code = code
        self.message = message or code

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


# ============================================================ small helpers
def is_json_int(v: Any) -> bool:
    """R-3: a JSON number with an integral value (4 and 4.0), never a bool/str/null."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and v.is_integer()


def valid_email(s: str) -> bool:
    """R-4: exactly one '@', non-empty local part and domain, no whitespace."""
    if s.count("@") != 1 or any(ch.isspace() for ch in s):
        return False
    local, domain = s.split("@")
    return bool(local) and bool(domain)


def hhmm_to_minutes(s: str) -> Optional[int]:
    """Local HH:MM -> minutes; "24:00" is accepted as end of day (R-8)."""
    m = HHMM_RE.match(s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h == 24 and mi == 0:
        return 24 * 60
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def parse_local(s: str) -> Optional[datetime]:
    """Bare local YYYY-MM-DDTHH:MM -> naive datetime, or None when not that format."""
    m = LOCAL_RE.match(s)
    if not m:
        return None
    y, mo, d, h, mi = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def parse_date(s: str) -> Optional[date]:
    m = DATE_RE.match(s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_rfc3339(s: Any) -> Optional[datetime]:
    """A timestamp with an offset (R-16 accepts a valid RFC 3339 created_at in fixtures)."""
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def resolve_local(naive: datetime, zone: ZoneInfo) -> Optional[datetime]:
    """Resolve a wall-clock time in `zone` to a UTC instant (R-5).

    None for a non-existent local time (spring-forward gap). An ambiguous local time (fall-back
    repeat) resolves to the first occurrence (fold=0, the pre-transition offset).
    """
    aware = naive.replace(tzinfo=zone, fold=0)
    utc = aware.astimezone(UTC)
    if utc.astimezone(zone).replace(tzinfo=None) != naive:  # round trip moved the wall clock: gap
        return None
    return utc


def closes_instant(naive: datetime, zone: ZoneInfo) -> datetime:
    """R-23: instant of a local closing time; in a skipped hour it is the transition instant."""
    utc = resolve_local(naive, zone)
    if utc is not None:
        return utc
    lo = naive.replace(tzinfo=zone, fold=1).astimezone(UTC)   # post-transition offset: before the gap
    hi = naive.replace(tzinfo=zone, fold=0).astimezone(UTC)   # pre-transition offset: after the gap
    post = naive.replace(tzinfo=zone, fold=1).utcoffset()
    while hi - lo > timedelta(minutes=1):                     # smallest instant with the new offset
        mid = lo + (hi - lo) / 2
        if mid.astimezone(zone).utcoffset() == post:
            hi = mid
        else:
            lo = mid
    return hi.replace(second=0, microsecond=0)


def fmt(utc: datetime, zone: ZoneInfo) -> str:
    """RFC 3339 with the zone's numeric offset at that instant (R-21)."""
    return utc.astimezone(zone).replace(microsecond=0).isoformat()


def fmt_utc(utc: datetime) -> str:
    return utc.astimezone(UTC).replace(microsecond=0).isoformat()


def hash_password(pw: str) -> str:
    # The model never stores plaintext (C1.55); a salted hash is enough for a reference model.
    return "sha256$" + hashlib.sha256(("tablekeeper-oracle$" + pw).encode("utf-8")).hexdigest()


# ============================================================ field checking (R-19)
# A field spec: (name, required, kind) with kind in {"str", "party"}.
def check_fields(obj: dict, spec: list[tuple[str, bool, str]]) -> None:
    """Three passes in documented field order: wrong type 400 -> missing 422 -> (values checked by caller)."""
    for name, _, kind in spec:                       # pass 1: wrong JSON type (R-2: null is a wrong type)
        if name in obj and kind == "str" and not isinstance(obj[name], str):
            raise Err(400, "malformed_request", f"{name} must be a string")
    for name, required, _ in spec:                   # pass 2: missing required
        if required and name not in obj:
            raise Err(422, "validation_failed", f"{name} is required")


def party_value(v: Any) -> int:
    """Pass 3 for party_size: any invalid value is 422 (C1.42, C1.86, R-2, R-3)."""
    if not is_json_int(v) or int(v) < 1:
        raise Err(422, "validation_failed", "party_size must be an integer >= 1")
    return int(v)


def local_value(v: str) -> datetime:
    naive = parse_local(v)
    if naive is None:
        raise Err(422, "validation_failed", "starts_at_local must be bare YYYY-MM-DDTHH:MM")
    return naive


# ============================================================ routing table (R-9)
ROUTES = [  # (method, pattern as list of segments; "*" matches one segment)
    ("GET", ["health"]), ("POST", ["_test", "reset"]), ("GET", ["_test", "export"]), ("POST", ["_test", "import"]),
    ("POST", ["auth", "signup"]), ("POST", ["auth", "login"]),
    ("GET", ["restaurants"]), ("GET", ["restaurants", "*"]), ("GET", ["availability"]),
    ("GET", ["reservations"]), ("POST", ["reservations"]),
    ("GET", ["reservations", "*"]), ("PATCH", ["reservations", "*"]),
    ("POST", ["reservations", "*", "cancel"]), ("POST", ["reservation-moves"]),
]


def match_route(method: str, parts: list[str]) -> Optional[bool]:
    """True: method+path known. False: path known, method not. None: unknown path.

    R-25: segments are taken literally; an empty segment (trailing slash, doubled slash, empty id) never matches.
    """
    path_known = False
    for m, pat in ROUTES:
        if len(pat) == len(parts) and all((p == "*" and q != "") or p == q for p, q in zip(pat, parts)):
            path_known = True
            if m == method:
                return True
    return False if path_known else None


class Model:
    def __init__(self, now: Optional[Callable[[], datetime]] = None):
        self.now = now or (lambda: datetime.now(UTC))
        self._blank()

    # ------------------------------------------------------------------ state
    def _blank(self) -> None:
        self.users: dict[str, dict] = {}          # user_id -> {id,email,password_hash,display_name}
        self.user_order: list[str] = []
        self.tokens: dict[str, str] = {}          # token -> user_id
        self.restaurants: dict[str, dict] = {}    # id -> fixture-shaped dict
        self.restaurant_order: list[str] = []
        self.reservations: dict[str, dict] = {}   # reservation_id -> record
        self.reservation_order: list[str] = []
        self.references: set[str] = set()
        self.idem: dict[str, dict] = {}           # "user|METHOD|path|key" -> {"body","status","response"}
        self.counters = {"user": 0, "reservation": 0}

    def _adopt(self, other: "Model") -> None:
        for attr in ("users", "user_order", "tokens", "restaurants", "restaurant_order",
                     "reservations", "reservation_order", "references", "idem", "counters"):
            setattr(self, attr, getattr(other, attr))

    # ------------------------------------------------------------------ id generation
    def _next(self, kind: str) -> int:
        self.counters[kind] += 1          # kept in the exported state for information only
        return next(_SEQ)

    def _new_user_id(self) -> str:
        while True:
            uid = f"u_{self._next('user')}"
            if uid not in self.users:
                return uid

    @staticmethod
    def _new_token() -> str:
        return "tok_" + secrets.token_hex(16)   # C1.112: never collides with imported tokens

    def _new_reservation_id(self) -> str:
        while True:
            rid = f"res_{self._next('reservation')}"
            if rid not in self.reservations:
                return rid

    def _new_reference(self) -> str:
        n = next(_SEQ)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        while True:
            s, k_ = "", n
            while k_:
                s = alphabet[k_ % 36] + s
                k_ //= 36
            ref = ("R" + s).rjust(6, "0")
            if ref not in self.references:
                return ref
            n += 1

    def _user_by_email(self, email: str) -> Optional[dict]:
        e = email.lower()                                  # R-4: case-insensitive
        return next((u for u in self.users.values() if u["email"].lower() == e), None)

    # ------------------------------------------------------------------ public entry
    def handle(self, method: str, path: str, query: str = "", headers: Optional[dict] = None,
               body: Any = None) -> tuple[int, Any]:
        headers = {k_.lower(): v for k_, v in (headers or {}).items()}
        try:
            return self._route(method.upper(), path, query or "", headers, body)
        except Err as e:
            return e.status, e.body()

    # ------------------------------------------------------------------ routing
    def _route(self, method: str, path: str, query: str, headers: dict, body: Any):
        if not path.startswith("/"):
            raise Err(404, "not_found", "no such route")                     # R-25
        parts = path[1:].split("/")
        known = match_route(method, parts)
        if known is None:
            raise Err(404, "not_found", "no such route")                     # R-9
        if known is False:
            raise Err(405, "method_not_allowed", "method not allowed")        # R-9
        if parts == ["health"]:
            return 200, {"status": "ok"}
        if parts == ["_test", "reset"]:
            return self.reset(self._json_object(body))
        if parts == ["_test", "export"]:
            return 200, self.export()
        if parts == ["_test", "import"]:
            return self.import_(self._json_object(body))
        if parts == ["auth", "signup"]:
            return self.signup(self._json_object(body))
        if parts == ["auth", "login"]:
            return self.login(self._json_object(body))
        if parts == ["restaurants"]:
            return self.list_restaurants()
        if parts[0] == "restaurants":
            return self.get_restaurant(parts[1])
        if parts == ["availability"]:
            return self.availability(self._query(query))
        # protected endpoints: 401 first (R-1, C1.36), then body parsing
        user_id = self._auth(headers)
        if method == "POST" and parts == ["reservations"]:
            return self._keyed(user_id, "POST", "/reservations", headers, body, self.create_reservation)
        if method == "GET" and parts == ["reservations"]:
            return self.list_reservations(user_id)
        if method == "GET" and len(parts) == 2:
            return self.get_reservation(user_id, parts[1])
        if method == "PATCH":
            return self.patch(user_id, parts[1], self._json_object(body))
        if len(parts) == 3:
            self._cancel_body(body)
            return self.cancel(user_id, parts[1])
        return self._keyed(user_id, "POST", "/reservation-moves", headers, body, self.moves)

    def _keyed(self, user_id: str, method: str, path: str, headers: dict, body: Any, fn):
        """R-1 / R-18: parse body -> key header -> replay/reuse -> endpoint; store 2xx outcomes only."""
        obj = self._json_object(body)
        key = self._idem_key(headers)
        slot = f"{user_id}|{method}|{path}|{key}"
        rec = self.idem.get(slot)
        if rec is not None:
            if rec["body"] == obj:                         # R-3: numbers compared by value
                return 200, json.loads(json.dumps(rec["response"]))
            raise Err(409, "idempotency_key_reuse", "key already used with a different body")
        status, out = fn(user_id, obj)
        if 200 <= status < 300:
            self.idem[slot] = {"body": json.loads(json.dumps(obj)), "status": status,
                               "response": json.loads(json.dumps(out))}
        return status, out

    # ------------------------------------------------------------------ request plumbing
    @staticmethod
    def _decode(body: Any) -> Any:
        if isinstance(body, (bytes, bytearray)):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError:
                raise Err(400, "malformed_request", "body is not UTF-8")
        if isinstance(body, str):
            try:
                return json.loads(body)
            except ValueError:
                raise Err(400, "malformed_request", "body is not JSON")
        return body

    @classmethod
    def _json_object(cls, body: Any) -> dict:
        if body is None or body == b"" or body == "":
            raise Err(400, "malformed_request", "body missing")
        v = cls._decode(body)
        if not isinstance(v, dict):
            raise Err(400, "malformed_request", "body is not a JSON object")
        return v

    @classmethod
    def _cancel_body(cls, body: Any) -> None:
        """R-13: an absent or empty body is ignored; a non-empty body that does not parse is 400."""
        if body is None or body == b"" or body == "":
            return
        cls._decode(body)

    @staticmethod
    def _query(query: str) -> dict[str, str]:
        qs = parse_qs(query, keep_blank_values=True)
        return {k_: v[0] for k_, v in qs.items()}

    def _auth(self, headers: dict) -> str:
        raw = headers.get("authorization")
        if not isinstance(raw, str):
            raise Err(401, "unauthenticated", "missing bearer token")
        parts = raw.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise Err(401, "unauthenticated", "malformed bearer token")
        token = parts[1].strip()
        if token not in self.tokens:
            raise Err(401, "unauthenticated", "unknown bearer token")
        return self.tokens[token]

    @staticmethod
    def _idem_key(headers: dict) -> str:
        key = headers.get("idempotency-key")
        if key is None or key == "":
            raise Err(400, "missing_idempotency_key", "Idempotency-Key header required")
        if len(key) > 255:
            raise Err(422, "validation_failed", "Idempotency-Key longer than 255 characters")
        return key

    # ------------------------------------------------------------------ reset / fixture (R-8, R-16, R-20)
    def reset(self, fx: dict):
        new = Model(self.now)
        new._load_fixture(fx)
        self._adopt(new)
        return 204, None

    @staticmethod
    def _fstr(obj: dict, field: str, max_len: Optional[int] = None) -> str:
        if field not in obj:
            raise Err(422, "validation_failed", f"{field} is required")
        v = obj[field]
        if not isinstance(v, str):
            raise Err(400, "malformed_request", f"{field} must be a string")
        if v == "":
            raise Err(422, "validation_failed", f"{field} must not be empty")
        if max_len is not None and len(v) > max_len:
            raise Err(422, "validation_failed", f"{field} longer than {max_len}")
        return v

    @staticmethod
    def _flist(obj: dict, field: str) -> list:
        v = obj.get(field, [])
        if not isinstance(v, list):
            raise Err(400, "malformed_request", f"{field} must be an array")
        return v

    @staticmethod
    def _fobj(v: Any, what: str) -> dict:
        if not isinstance(v, dict):
            raise Err(400, "malformed_request", f"{what} must be an object")
        return v

    @staticmethod
    def _fint(obj: dict, field: str, minimum: int) -> int:
        if field not in obj:
            raise Err(422, "validation_failed", f"{field} is required")
        v = obj[field]
        if not is_json_int(v):
            raise Err(400, "malformed_request", f"{field} must be an integer")
        if int(v) < minimum:
            raise Err(422, "validation_failed", f"{field} must be >= {minimum}")
        return int(v)

    def _load_fixture(self, fx: dict) -> None:
        for u in self._flist(fx, "users"):
            self._fobj(u, "user")
            uid = self._fstr(u, "id", max_len=ID_MAX)
            email = self._fstr(u, "email")
            if not valid_email(email):
                raise Err(422, "validation_failed", "email must be local@domain")
            pw = self._fstr(u, "password")
            name = self._fstr(u, "display_name")
            if uid in self.users or self._user_by_email(email) is not None:
                raise Err(422, "validation_failed", "duplicate user id or email")      # R-20
            self.users[uid] = {"id": uid, "email": email, "password_hash": hash_password(pw),
                               "display_name": name}
            self.user_order.append(uid)
        for r in self._flist(fx, "restaurants"):
            self._fobj(r, "restaurant")
            rid = self._fstr(r, "id", max_len=ID_MAX)
            name = self._fstr(r, "name")
            tz = self._fstr(r, "timezone")
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                raise Err(422, "validation_failed", "unknown timezone")
            slot = self._fint(r, "slot_minutes", 1)
            dur = self._fint(r, "reservation_duration_minutes", 1)
            cutoff = self._fint(r, "cancellation_cutoff_minutes", 0)
            hours, seen_wd = [], set()
            for h in self._flist(r, "opening_hours"):
                self._fobj(h, "opening_hours entry")
                wd = self._fstr(h, "weekday")
                if wd not in WEEKDAYS:
                    raise Err(422, "validation_failed", "weekday must be mon..sun")
                if wd in seen_wd:
                    raise Err(422, "validation_failed", "duplicate weekday")              # R-20
                seen_wd.add(wd)
                o, c = self._fstr(h, "opens"), self._fstr(h, "closes")
                om, cm = hhmm_to_minutes(o), hhmm_to_minutes(c)
                if om is None or cm is None or om == 24 * 60:
                    raise Err(422, "validation_failed", "opens/closes must be HH:MM")
                if cm <= om:
                    raise Err(422, "validation_failed", "closes must be later than opens")  # R-20
                hours.append({"weekday": wd, "opens": o, "closes": c})
            tables, seen_t = [], set()
            for t in self._flist(r, "tables"):
                self._fobj(t, "table")
                tid = self._fstr(t, "id", max_len=ID_MAX)
                label = self._fstr(t, "label")
                cap = self._fint(t, "capacity", 1)
                if tid in seen_t:
                    raise Err(422, "validation_failed", "duplicate table id")             # R-20
                seen_t.add(tid)
                tables.append({"id": tid, "label": label, "capacity": cap})
            if rid in self.restaurants:
                raise Err(422, "validation_failed", "duplicate restaurant id")            # R-20
            self.restaurants[rid] = {
                "id": rid, "name": name, "timezone": tz, "slot_minutes": slot,
                "reservation_duration_minutes": dur, "cancellation_cutoff_minutes": cutoff,
                "opening_hours": hours, "tables": tables,
            }
            self.restaurant_order.append(rid)
        for s in self._flist(fx, "reservations"):
            self._fobj(s, "reservation")
            rid = self._fstr(s, "id", max_len=ID_MAX)
            ref = self._fstr(s, "reference", max_len=ID_MAX)   # R-26: a fixture reference is an opaque id
            uid = self._fstr(s, "user_id")
            restaurant_id = self._fstr(s, "restaurant_id")
            table_id = self._fstr(s, "table_id")
            local = self._fstr(s, "starts_at_local")
            if "party_size" not in s:
                raise Err(422, "validation_failed", "party_size is required")
            party = party_value(s["party_size"])
            if uid not in self.users:
                raise Err(422, "validation_failed", "unknown user_id")                    # R-20
            r = self.restaurants.get(restaurant_id)
            if r is None or not any(t["id"] == table_id for t in r["tables"]):
                raise Err(422, "validation_failed", "unknown restaurant/table")           # R-20
            naive = parse_local(local)
            if naive is None:
                raise Err(422, "validation_failed", "starts_at_local must be YYYY-MM-DDTHH:MM")
            zone = ZoneInfo(r["timezone"])
            start = resolve_local(naive, zone)
            if start is None:
                raise Err(422, "validation_failed", "starts_at_local does not exist")
            if rid in self.reservations or ref in self.references:
                raise Err(422, "validation_failed", "duplicate reservation id/reference")  # R-20
            created = parse_rfc3339(s.get("created_at")) or self.now()                   # R-16
            self._add_reservation(rid, ref, uid, restaurant_id, table_id, party, local, start,
                                  start + timedelta(minutes=r["reservation_duration_minutes"]), created)

    def _add_reservation(self, rid, ref, uid, restaurant_id, table_id, party, local, start, end, created):
        self.reservations[rid] = {
            "reservation_id": rid, "reference": ref, "user_id": uid, "restaurant_id": restaurant_id,
            "table_id": table_id, "party_size": party, "status": "confirmed",
            "starts_at_local": local, "start": start, "end": end, "created": created,
        }
        self.reservation_order.append(rid)
        self.references.add(ref)

    # ------------------------------------------------------------------ export / import (R-24)
    def export(self) -> dict:
        state = {
            "schema": STATE_MARKER,
            "users": [self.users[u] for u in self.user_order],
            "tokens": [{"token": t, "user_id": u} for t, u in self.tokens.items()],
            "restaurants": [self.restaurants[r] for r in self.restaurant_order],
            "reservations": [
                {**{k_: v for k_, v in rec.items() if k_ not in ("start", "end", "created")},
                 "start": fmt_utc(rec["start"]), "end": fmt_utc(rec["end"]),
                 "created": fmt_utc(rec["created"])}
                for rec in (self.reservations[r] for r in self.reservation_order)
            ],
            "idempotency": [{"key": k_, **v} for k_, v in self.idem.items()],
            "counters": dict(self.counters),
        }
        return json.loads(json.dumps({"track": TRACK, "format_version": FORMAT_VERSION, "state": state}))

    def import_(self, doc: dict):
        if doc.get("track") != TRACK or doc.get("format_version") != FORMAT_VERSION:
            raise Err(422, "validation_failed", "wrong track or format_version")
        state = doc.get("state")
        if not isinstance(state, dict) or state.get("schema") != STATE_MARKER:
            raise Err(422, "validation_failed", "state is not one this service produced")   # R-24
        new = self._validated_state(state)
        self._adopt(new)
        return 204, None

    def _validated_state(self, state: dict) -> "Model":
        """R-24: every collection present, every record well-formed and referentially consistent, else 422."""
        bad = Err(422, "validation_failed", "invalid state")

        def need(cond: bool) -> None:
            if not cond:
                raise bad

        def alist(name: str) -> list:
            need(isinstance(state.get(name), list))
            return state[name]

        new = Model(self.now)
        seen_emails: set[str] = set()
        for u in alist("users"):
            need(isinstance(u, dict))
            for f in ("id", "email", "password_hash", "display_name"):
                need(isinstance(u.get(f), str) and u[f] != "")
            need(len(u["id"]) <= ID_MAX and valid_email(u["email"]) and u["password_hash"].startswith("sha256$"))
            need(u["id"] not in new.users and u["email"].lower() not in seen_emails)
            seen_emails.add(u["email"].lower())
            new.users[u["id"]] = {f: u[f] for f in ("id", "email", "password_hash", "display_name")}
            new.user_order.append(u["id"])
        for t in alist("tokens"):
            need(isinstance(t, dict) and isinstance(t.get("token"), str) and t["token"] != "")
            need(t.get("user_id") in new.users and t["token"] not in new.tokens)
            new.tokens[t["token"]] = t["user_id"]
        try:   # restaurants: the same shape and referential checks as a reset fixture, every failure 422
            new._load_fixture({"users": [], "restaurants": alist("restaurants"), "reservations": []})
        except Err:
            raise bad
        for s_ in alist("reservations"):
            need(isinstance(s_, dict))
            for f in ("reservation_id", "reference", "user_id", "restaurant_id", "table_id", "status",
                      "starts_at_local", "start", "end", "created"):
                need(isinstance(s_.get(f), str) and s_[f] != "")
            need(len(s_["reservation_id"]) <= ID_MAX and len(s_["reference"]) <= ID_MAX)   # R-26: opaque
            need(s_["status"] in ("confirmed", "cancelled") and s_["user_id"] in new.users)
            r = new.restaurants.get(s_["restaurant_id"])
            need(r is not None and any(t["id"] == s_["table_id"] for t in r["tables"]))
            need(is_json_int(s_.get("party_size")) and int(s_["party_size"]) >= 1)
            need(parse_local(s_["starts_at_local"]) is not None)
            start, end, created = (parse_rfc3339(s_[f]) for f in ("start", "end", "created"))
            need(start is not None and end is not None and created is not None and end > start)
            need(min(start.year, end.year, created.year) >= 1000)     # a zero timestamp is not a value we produced
            need(s_["reservation_id"] not in new.reservations and s_["reference"] not in new.references)
            new._add_reservation(s_["reservation_id"], s_["reference"], s_["user_id"], s_["restaurant_id"],
                                 s_["table_id"], int(s_["party_size"]), s_["starts_at_local"], start, end, created)
            new.reservations[s_["reservation_id"]]["status"] = s_["status"]
        for i in alist("idempotency"):
            need(isinstance(i, dict) and isinstance(i.get("key"), str) and i["key"] not in new.idem)
            need(isinstance(i.get("body"), dict) and is_json_int(i.get("status")) and i.get("response") is not None)
            new.idem[i["key"]] = {"body": i["body"], "status": int(i["status"]), "response": i["response"]}
        counters = state.get("counters")
        need(isinstance(counters, dict))
        for k_ in ("user", "reservation"):
            need(is_json_int(counters.get(k_)) and int(counters[k_]) >= 0)
        new.counters = {k_: int(counters[k_]) for k_ in ("user", "reservation")}
        return new

    # ------------------------------------------------------------------ auth (R-4, R-19)
    def signup(self, obj: dict):
        check_fields(obj, [("email", True, "str"), ("password", True, "str"), ("display_name", True, "str")])
        if not valid_email(obj["email"]):
            raise Err(422, "validation_failed", "email must be local@domain")
        if len(obj["password"]) < 8:
            raise Err(422, "validation_failed", "password shorter than 8 characters")
        if obj["display_name"].strip() == "":
            raise Err(422, "validation_failed", "display_name must not be blank")
        if self._user_by_email(obj["email"]) is not None:
            raise Err(409, "email_taken", "email already registered")
        uid = self._new_user_id()
        self.users[uid] = {"id": uid, "email": obj["email"], "password_hash": hash_password(obj["password"]),
                           "display_name": obj["display_name"]}
        self.user_order.append(uid)
        token = self._new_token()
        self.tokens[token] = uid
        return 201, {"user_id": uid, "display_name": obj["display_name"], "token": token}

    def login(self, obj: dict):
        check_fields(obj, [("email", True, "str"), ("password", True, "str")])
        user = self._user_by_email(obj["email"])
        if user is None or user["password_hash"] != hash_password(obj["password"]):
            raise Err(401, "unauthenticated", "wrong email or password")
        token = self._new_token()
        self.tokens[token] = user["id"]
        return 200, {"user_id": user["id"], "display_name": user["display_name"], "token": token}

    # ------------------------------------------------------------------ restaurants
    def list_restaurants(self):
        return 200, {"restaurants": [
            {"id": r["id"], "name": r["name"], "timezone": r["timezone"]}
            for r in (self.restaurants[i] for i in self.restaurant_order)
        ]}

    def get_restaurant(self, rid: str):
        r = self.restaurants.get(rid)
        if r is None:
            raise Err(404, "not_found", "no such restaurant")
        return 200, json.loads(json.dumps(r))

    # ------------------------------------------------------------------ availability (R-5, R-12, R-23)
    def _window(self, r: dict, day: date) -> Optional[tuple[int, int, datetime]]:
        """(opens_minutes, closes_minutes, closes_instant) for the local day, or None when closed."""
        wd = WEEKDAYS[day.weekday()]
        h = next((h for h in r["opening_hours"] if h["weekday"] == wd), None)
        if h is None:
            return None
        opens, closes = hhmm_to_minutes(h["opens"]), hhmm_to_minutes(h["closes"])
        zone = ZoneInfo(r["timezone"])
        midnight = datetime(day.year, day.month, day.day)
        return opens, closes, closes_instant(midnight + timedelta(minutes=closes), zone)

    def _slots_for(self, r: dict, day: date) -> list[tuple[str, datetime]]:
        """(starts_at_local, utc_start) for every grid slot of `day`: wall-clock grid, absolute end check."""
        w = self._window(r, day)
        if w is None:
            return []
        opens, closes, closes_at = w
        zone = ZoneInfo(r["timezone"])
        dur = timedelta(minutes=r["reservation_duration_minutes"])
        midnight = datetime(day.year, day.month, day.day)
        out: list[tuple[str, datetime]] = []
        t = opens
        while t < closes:
            naive = midnight + timedelta(minutes=t)
            utc = resolve_local(naive, zone)
            if utc is not None and utc + dur <= closes_at:                       # R-23
                out.append((naive.strftime("%Y-%m-%dT%H:%M"), utc))
            t += r["slot_minutes"]
        return out

    def availability(self, q: dict[str, str]):
        for name in ("restaurant_id", "date", "party_size"):
            if name not in q or q[name] == "":
                raise Err(422, "validation_failed", f"{name} is required")
        day = parse_date(q["date"])
        if day is None:
            raise Err(422, "validation_failed", "date must be YYYY-MM-DD")
        if not DIGITS_RE.match(q["party_size"]) or int(q["party_size"]) < 1:
            raise Err(422, "validation_failed", "party_size must be plain decimal digits >= 1")
        party = int(q["party_size"])
        r = self.restaurants.get(q["restaurant_id"])
        if r is None:
            raise Err(404, "not_found", "no such restaurant")
        zone = ZoneInfo(r["timezone"])
        dur = timedelta(minutes=r["reservation_duration_minutes"])
        slots = []
        for local, start in self._slots_for(r, day):
            ids = [t["id"] for t in r["tables"]
                   if t["capacity"] >= party and not self._table_busy(t["id"], start, start + dur, None)]
            slots.append({"starts_at_local": local, "starts_at": fmt(start, zone), "available_table_ids": ids})
        return 200, {"restaurant_id": r["id"], "date": q["date"], "timezone": r["timezone"], "slots": slots}

    def _table_busy(self, table_id: str, start: datetime, end: datetime, exclude: Optional[set[str]]) -> bool:
        for rec in self.reservations.values():
            if rec["status"] != "confirmed" or rec["table_id"] != table_id:
                continue
            if exclude and rec["reservation_id"] in exclude:
                continue
            if rec["start"] < end and start < rec["end"]:
                return True
        return False

    # ------------------------------------------------------------------ booking rules (R-7)
    def _resolve_booking(self, r: dict, table_id: str, naive: datetime, party: int,
                         exclude: Optional[set[str]] = None, check_overlap: bool = True):
        """404 table -> invalid_local_time -> outside_opening_hours -> not_on_slot_grid -> capacity -> overlap."""
        table = next((t for t in r["tables"] if t["id"] == table_id), None)
        if table is None:
            raise Err(404, "not_found", "no such table at this restaurant")
        zone = ZoneInfo(r["timezone"])
        start = resolve_local(naive, zone)
        if start is None:
            raise Err(422, "invalid_local_time", "local time does not exist")
        dur = timedelta(minutes=r["reservation_duration_minutes"])
        w = self._window(r, naive.date())
        minutes = naive.hour * 60 + naive.minute
        if w is None or not (w[0] <= minutes < w[1]) or start + dur > w[2]:        # R-23
            raise Err(422, "outside_opening_hours", "slot outside opening hours")
        if (minutes - w[0]) % r["slot_minutes"] != 0:
            raise Err(422, "not_on_slot_grid", "starts_at_local is not on the slot grid")
        if party > table["capacity"]:
            raise Err(422, "party_exceeds_capacity", "party_size exceeds table capacity")
        end = start + dur
        if check_overlap and self._table_busy(table_id, start, end, exclude):
            raise Err(409, "table_unavailable", "table is taken for an overlapping interval")
        return start, end

    def _view(self, rec: dict) -> dict:
        zone = ZoneInfo(self.restaurants[rec["restaurant_id"]]["timezone"])
        return {
            "reservation_id": rec["reservation_id"], "reference": rec["reference"],
            "restaurant_id": rec["restaurant_id"], "table_id": rec["table_id"],
            "party_size": rec["party_size"], "status": rec["status"],
            "starts_at_local": rec["starts_at_local"], "starts_at": fmt(rec["start"], zone),
            "ends_at": fmt(rec["end"], zone), "created_at": fmt_utc(rec["created"]),
        }

    # ------------------------------------------------------------------ reservations
    BOOKING_FIELDS = [("restaurant_id", True, "str"), ("table_id", True, "str"),
                      ("starts_at_local", True, "str"), ("party_size", True, "party")]

    def create_reservation(self, user_id: str, obj: dict):
        check_fields(obj, self.BOOKING_FIELDS)                       # R-19 passes 1 and 2
        for f in ("restaurant_id", "table_id"):
            if obj[f] == "":
                raise Err(422, "validation_failed", f"{f} must not be empty")
        naive = local_value(obj["starts_at_local"])                  # pass 3
        party = party_value(obj["party_size"])
        r = self.restaurants.get(obj["restaurant_id"])
        if r is None:
            raise Err(404, "not_found", "no such restaurant")
        start, end = self._resolve_booking(r, obj["table_id"], naive, party)
        rid = self._new_reservation_id()
        ref = self._new_reference()
        self._add_reservation(rid, ref, user_id, obj["restaurant_id"], obj["table_id"], party,
                              obj["starts_at_local"], start, end, self.now())
        return 201, self._view(self.reservations[rid])

    def list_reservations(self, user_id: str):
        mine = [self.reservations[r] for r in self.reservation_order if self.reservations[r]["user_id"] == user_id]
        mine.sort(key=lambda rec: (rec["created"], rec["reference"]))     # R-11 tie order
        mine.sort(key=lambda rec: rec["start"], reverse=True)              # stable: starts_at descending
        return 200, {"reservations": [self._view(rec) for rec in mine]}

    def _mine(self, user_id: str, reference: str) -> dict:
        for rec in self.reservations.values():
            if rec["reference"] == reference and rec["user_id"] == user_id:
                return rec
        raise Err(404, "not_found", "no such reservation")

    def get_reservation(self, user_id: str, reference: str):
        return 200, self._view(self._mine(user_id, reference))

    def _cutoff_passed(self, rec: dict) -> bool:
        cutoff = self.restaurants[rec["restaurant_id"]]["cancellation_cutoff_minutes"]
        return rec["start"] - self.now() <= timedelta(minutes=cutoff)       # R-6: boundary is "within"

    def cancel(self, user_id: str, reference: str):                       # R-13
        rec = self._mine(user_id, reference)
        if rec["status"] == "cancelled":
            return 200, self._view(rec)
        if self._cutoff_passed(rec):
            raise Err(409, "cutoff_passed", "within the cancellation cutoff")
        rec["status"] = "cancelled"
        return 200, self._view(rec)

    AMEND_FIELDS = [("table_id", False, "str"), ("starts_at_local", False, "str"), ("party_size", False, "party")]

    def _amend_values(self, rec: dict, obj: dict) -> tuple[str, str, datetime, int]:
        """Pass 3 of R-19 for an amendment: values of the supplied fields; omitted keep current."""
        table_id = obj["table_id"] if "table_id" in obj else rec["table_id"]
        if table_id == "":
            raise Err(422, "validation_failed", "table_id must not be empty")
        local = obj["starts_at_local"] if "starts_at_local" in obj else rec["starts_at_local"]
        naive = local_value(local)
        party = party_value(obj["party_size"]) if "party_size" in obj else rec["party_size"]
        return table_id, local, naive, party

    def patch(self, user_id: str, reference: str, obj: dict):             # R-14
        check_fields(obj, self.AMEND_FIELDS)                              # wrong types 400 before 404
        rec = self._mine(user_id, reference)
        if rec["status"] == "cancelled":
            raise Err(409, "reservation_cancelled", "reservation is cancelled")
        if self._cutoff_passed(rec):
            raise Err(409, "cutoff_passed", "within the amendment cutoff")
        table_id, local, naive, party = self._amend_values(rec, obj)
        r = self.restaurants[rec["restaurant_id"]]
        start, end = self._resolve_booking(r, table_id, naive, party, exclude={rec["reservation_id"]})
        rec.update({"table_id": table_id, "starts_at_local": local, "party_size": party, "start": start, "end": end})
        return 200, self._view(rec)

    # ------------------------------------------------------------------ atomic moves (R-22)
    def moves(self, user_id: str, obj: dict):
        moves = obj.get("moves")                                           # (a) structure -> 422
        if not isinstance(moves, list) or not 1 <= len(moves) <= 8:
            raise Err(422, "validation_failed", "moves must contain 1..8 objects")
        refs: list[str] = []
        for m in moves:
            if not isinstance(m, dict) or not isinstance(m.get("reference"), str):
                raise Err(422, "validation_failed", "each move needs a string reference")
            if m["reference"] in refs:
                raise Err(422, "validation_failed", "duplicate reference")
            refs.append(m["reference"])
        for m in moves:                                                    # (b) item field types -> 400
            check_fields(m, self.AMEND_FIELDS)
        resolved = []                                                      # (c) per item, input order
        first_restaurant: Optional[str] = None
        for m in moves:
            rec = self._mine(user_id, m["reference"])
            if first_restaurant is None:
                first_restaurant = rec["restaurant_id"]
            elif rec["restaurant_id"] != first_restaurant:
                raise Err(422, "validation_failed", "bookings belong to different restaurants")
            if rec["status"] == "cancelled":
                raise Err(409, "reservation_cancelled", "reservation is cancelled")
            if self._cutoff_passed(rec):
                raise Err(409, "cutoff_passed", "within the amendment cutoff")
            table_id, local, naive, party = self._amend_values(rec, m)
            r = self.restaurants[rec["restaurant_id"]]
            start, end = self._resolve_booking(r, table_id, naive, party, check_overlap=False)
            resolved.append((rec, table_id, local, party, start, end))
        listed = {rec["reservation_id"] for rec, *_ in resolved}          # (d) occupancy
        for i, (rec, table_id, _, _, start, end) in enumerate(resolved):
            if self._table_busy(table_id, start, end, exclude=listed):
                raise Err(409, "table_unavailable", "overlap with an unlisted booking")
            for j, (_, table2, _, _, start2, end2) in enumerate(resolved):
                if i != j and table_id == table2 and start < end2 and start2 < end:
                    raise Err(409, "table_unavailable", "overlap among listed bookings")
        for rec, table_id, local, party, start, end in resolved:
            rec.update({"table_id": table_id, "starts_at_local": local, "party_size": party,
                        "start": start, "end": end})
        return 201, {"reservations": [self._view(rec) for rec, *_ in resolved]}
