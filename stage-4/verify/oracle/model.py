"""Tablekeeper stage-4 reference model (Oracle seat).

Stage 4 adds seating replans after a table closure (preview with a brute-force optimal planner, atomic apply with
stale_plan / plan_already_applied), closures that block availability, explanations and bookings, series amendments,
the observable restaurant revision, and the upgrade path from stage-1/2/3 model states (schema v4).

Stage 3 adds dated booking policies (publication, selection by local start date, accepted terms and revisions on
every reservation), availability explanations, reservation history and decision, expected_revision, recurring
series, and the upgrade path from stage-1 (v1) and stage-2 (v2) model states. Precedence defaults are the Q1..Q25
of evidence/stage-3/ledger-B.md until ruled.

Stage 2 adds combinable table pairs (`table_ids`, `available_options`, `combination_not_allowed`), seeded
`status`, and the upgrade path: an export of the stage-1 model (schema oracle-model-v1) imports into this model.

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
STATE_MARKER = "oracle-model-v4"
STATE_MARKER_V3 = "oracle-model-v3"
STATE_MARKER_V2 = "oracle-model-v2"
STATE_MARKER_V1 = "oracle-model-v1"
TERMS_KEYS = ("policy_version", "slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes",
              "opening_hours", "capacities")
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
        if name in obj and kind == "list" and (not isinstance(obj[name], list)
                                               or any(not isinstance(x, str) for x in obj[name])):
            raise Err(400, "malformed_request", f"{name} must be an array of strings")
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
    # stage 3
    ("POST", ["restaurants", "*", "policies"]), ("GET", ["restaurants", "*", "policies"]),
    ("GET", ["reservations", "*", "history"]), ("GET", ["reservations", "*", "decision"]),
    ("POST", ["series"]), ("GET", ["series", "*"]),
    # stage 4
    ("POST", ["restaurants", "*", "replans"]), ("POST", ["restaurants", "*", "replans", "*", "apply"]),
    ("POST", ["series", "*", "amend"]),
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
        self.series: dict[str, dict] = {}         # series_id -> {series_id,user_id,revision,interval_weeks,count,occurrences}
        self.series_order: list[str] = []
        self.closures: list[dict] = []            # {restaurant_id, table_id, from, to (UTC datetimes), plan_id}
        self.plans: dict[str, dict] = {}          # plan_id -> stored preview
        self.plan_order: list[str] = []
        self.counters = {"user": 0, "reservation": 0}

    def _adopt(self, other: "Model") -> None:
        for attr in ("users", "user_order", "tokens", "restaurants", "restaurant_order",
                     "reservations", "reservation_order", "references", "idem", "counters", "series", "series_order",
                     "closures", "plans", "plan_order"):
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
        if method == "GET" and len(parts) == 2 and parts[0] == "restaurants":
            return self.get_restaurant(parts[1])
        if parts == ["availability"]:
            return self.availability(self._query(query))
        if method == "GET" and len(parts) == 3 and parts[0] == "restaurants":
            return self.list_policies(parts[1])                                     # public (B40)
        if method == "GET" and len(parts) == 3 and parts[0] == "reservations":        # history / decision: 404, never 401 (B55)
            user_id = self._auth_or_404(headers)
            return (self.history if parts[2] == "history" else self.decision)(user_id, parts[1])
        if method == "GET" and parts[0] == "series":
            return self.get_series(self._auth_or_404(headers), parts[1])             # B69
        # protected endpoints: 401 first (R-1, C1.36), then body parsing
        user_id = self._auth(headers)
        if method == "POST" and parts[0] == "restaurants" and parts[2] == "policies":
            rid = parts[1]
            return self._keyed(user_id, "POST", f"/restaurants/{rid}/policies", headers, body,
                               lambda u, o: self.publish_policy(u, rid, o))
        if method == "POST" and parts[0] == "restaurants" and len(parts) == 3:              # stage 4: preview
            rid = parts[1]
            return self._keyed(user_id, "POST", f"/restaurants/{rid}/replans", headers, body,
                               lambda u, o: self.replan_preview(u, rid, o))
        if method == "POST" and parts[0] == "restaurants" and len(parts) == 5:              # stage 4: apply
            rid, pid = parts[1], parts[3]
            return self._keyed(user_id, "POST", f"/restaurants/{rid}/replans/{pid}/apply", headers, body,
                               lambda u, o: self.replan_apply(u, rid, pid, o))
        if method == "POST" and len(parts) == 3 and parts[0] == "series":                   # stage 4: amend
            sid = parts[1]
            return self._keyed(user_id, "POST", f"/series/{sid}/amend", headers, body,
                               lambda u, o: self.amend_series(u, sid, o))
        if method == "POST" and parts == ["series"]:
            return self._keyed(user_id, "POST", "/series", headers, body, self.create_series)
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

    def _auth_or_404(self, headers: dict) -> str:
        try:
            return self._auth(headers)
        except Err:
            raise Err(404, "not_found", "no such reservation")

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
            combinable = []
            for pair in self._flist(r, "combinable"):                                     # R-34
                if (not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(x, str) for x in pair)
                        or pair[0] == pair[1] or any(x not in seen_t for x in pair)):
                    raise Err(422, "validation_failed", "combinable entries are pairs of distinct tables of this restaurant")
                if any({a, b} == set(pair) for a, b in combinable):
                    raise Err(422, "validation_failed", "duplicate combinable pair")
                combinable.append([pair[0], pair[1]])
            managers = self._flist(r, "manager_user_ids")                                  # stage 3 (B22)
            for m in managers:
                if not isinstance(m, str):
                    raise Err(400, "malformed_request", "manager_user_ids must be strings")
                if m == "" or len(m) > ID_MAX or m not in self.users:
                    raise Err(422, "validation_failed", "unknown manager user id")         # R-59
            if len(set(managers)) != len(managers):
                raise Err(422, "validation_failed", "duplicate manager user id")
            revision = r.get("revision", 0)
            if not is_json_int(revision) or int(revision) < 0:
                raise Err(422, "validation_failed", "revision must be a non-negative integer")
            self.restaurants[rid] = {
                "id": rid, "name": name, "timezone": tz, "slot_minutes": slot,
                "reservation_duration_minutes": dur, "cancellation_cutoff_minutes": cutoff,
                "opening_hours": hours, "tables": tables, "combinable": combinable,
                "manager_user_ids": list(managers), "policies": [], "revision": int(revision),
            }
            for i, pol in enumerate(self._flist(r, "policies")):                           # only from imported state
                self._fobj(pol, "policy")
                norm = self._validate_policy(self.restaurants[rid], pol)
                if not is_json_int(pol.get("policy_version")) or int(pol["policy_version"]) != i + 1:
                    raise Err(422, "validation_failed", "policy versions must be dense from 1")
                norm["policy_version"] = i + 1
                self.restaurants[rid]["policies"].append(norm)
            self.restaurant_order.append(rid)
        for s in self._flist(fx, "reservations"):
            self._fobj(s, "reservation")
            rid = self._fstr(s, "id", max_len=ID_MAX)
            ref = self._fstr(s, "reference")
            if not REFERENCE_RE.match(ref):                     # R-28: every reference is 6..12 of A-Z0-9
                raise Err(422, "validation_failed", "reference must be 6..12 of A-Z0-9")
            uid = self._fstr(s, "user_id")
            restaurant_id = self._fstr(s, "restaurant_id")
            check_fields(s, [("table_id", False, "str"), ("table_ids", False, "list")])
            table_ids = self._table_set(s, required=True)
            local = self._fstr(s, "starts_at_local")
            if "party_size" not in s:
                raise Err(422, "validation_failed", "party_size is required")
            party = party_value(s["party_size"])
            status = s.get("status", "confirmed")                                        # stage 2 (B65)
            if not isinstance(status, str):
                raise Err(400, "malformed_request", "status must be a string")
            if status not in ("confirmed", "cancelled"):
                raise Err(422, "validation_failed", "status must be confirmed or cancelled")
            if uid not in self.users:
                raise Err(422, "validation_failed", "unknown user_id")                    # R-20
            r = self.restaurants.get(restaurant_id)
            if r is None or any(not any(t["id"] == x for t in r["tables"]) for x in table_ids):
                raise Err(422, "validation_failed", "unknown restaurant/table")           # R-20
            if len(table_ids) > 2 or (len(table_ids) == 2 and not any({a, b} == set(table_ids) for a, b in r["combinable"])):
                raise Err(422, "validation_failed", "a seeded set is one table or a declared pair")   # R-34
            table_ids = self._declared_order(r, table_ids)
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
            self._add_reservation(rid, ref, uid, restaurant_id, table_ids, party, local, start,
                                  start + timedelta(minutes=r["reservation_duration_minutes"]), created,
                                  self._policy0(r))                                         # B45: revision 1, policy 0
            self.reservations[rid]["status"] = status                                       # R-51: one created entry

    def _add_reservation(self, rid, ref, uid, restaurant_id, table_ids, party, local, start, end, created,
                         terms: dict, history: Optional[list] = None):
        rec = {
            "reservation_id": rid, "reference": ref, "user_id": uid, "restaurant_id": restaurant_id,
            "table_ids": list(table_ids), "party_size": party, "status": "confirmed",
            "starts_at_local": local, "start": start, "end": end, "created": created,
            "revision": 1, "terms": json.loads(json.dumps(terms)), "history": [],
            "series_id": None, "series_index": None,
        }
        self.reservations[rid] = rec
        self.reservation_order.append(rid)
        self.references.add(ref)
        if history is not None:
            rec["history"] = history
        else:
            tf = "table_id" if len(table_ids) == 1 else "table_ids"                       # B17, B78
            tv = table_ids[0] if len(table_ids) == 1 else list(table_ids)
            self._record(rec, "created", [{"field": tf, "from": None, "to": tv},
                                          {"field": "starts_at_local", "from": None, "to": local},
                                          {"field": "party_size", "from": None, "to": party}], created)

    def _record(self, rec: dict, event: str, changes: list, at: datetime) -> None:
        zone = ZoneInfo(self.restaurants[rec["restaurant_id"]]["timezone"])
        rec["history"].append({"seq": len(rec["history"]) + 1, "at": fmt(at, zone), "event": event,    # Q4: zone
                               "changes": changes, "revision": rec["revision"],
                               "accepted_terms": json.loads(json.dumps(rec["terms"]))})

    def _cancel_record(self, rec: dict, at: datetime) -> None:
        rec["status"] = "cancelled"
        rec["revision"] += 1                                                               # B51
        self._record(rec, "cancelled", [], at)
        self._bump_series(rec, exception=False)

    def _bump_series(self, rec: dict, exception: bool) -> None:
        sid = rec.get("series_id")
        if sid and sid in self.series:
            ser = self.series[sid]
            ser["revision"] += 1
            if exception:
                ser["occurrences"][rec["series_index"]]["exception"] = True

    @staticmethod
    def _diff(ids_before: list, ids_after: list, local_before: str, local_after: str, party_before: int, party_after: int) -> list:
        """B18/B78: changed fields in the order table(s), starts_at_local, party_size."""
        changes = []
        if list(ids_before) != list(ids_after):
            if len(ids_before) == 1 and len(ids_after) == 1:
                changes.append({"field": "table_id", "from": ids_before[0], "to": ids_after[0]})
            else:
                changes.append({"field": "table_ids", "from": list(ids_before), "to": list(ids_after)})
        if local_before != local_after:
            changes.append({"field": "starts_at_local", "from": local_before, "to": local_after})
        if party_before != party_after:
            changes.append({"field": "party_size", "from": party_before, "to": party_after})
        return changes

    # ------------------------------------------------------------------ policies (stage 3)
    @staticmethod
    def _policy0(r: dict) -> dict:
        return {"policy_version": 0, "slot_minutes": r["slot_minutes"],
                "reservation_duration_minutes": r["reservation_duration_minutes"],
                "cancellation_cutoff_minutes": r["cancellation_cutoff_minutes"],
                "opening_hours": json.loads(json.dumps(r["opening_hours"])),
                "capacities": {t["id"]: t["capacity"] for t in r["tables"]}}

    def _policy_for(self, r: dict, day: date) -> dict:
        """B32: greatest effective_from not later than the local date; ties -> greatest policy_version; else policy 0."""
        best = None
        key = day.isoformat()
        for pol in r["policies"]:
            if pol["effective_from"] <= key and (best is None or (pol["effective_from"], pol["policy_version"]) >
                                                 (best["effective_from"], best["policy_version"])):
                best = pol
        if best is None:
            return self._policy0(r)
        return {k_: json.loads(json.dumps(best[k_])) for k_ in TERMS_KEYS}

    def _validate_policy(self, r: dict, obj: dict) -> dict:
        """B35-B37 (Q9): types 400 (booleans in integer fields 422) -> missing 422 -> values 422. Returns a normalised policy."""
        int_fields = ("slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes")
        bad = Err(422, "validation_failed", "invalid policy")                                       # R-47: every field problem
        for f, kind in (("effective_from", str), ("opening_hours", list), ("capacities", dict)):
            if f in obj and not isinstance(obj[f], kind):
                raise bad
        for f in ("effective_from",) + int_fields + ("opening_hours", "capacities"):
            if f not in obj:
                raise bad
        if parse_date(obj["effective_from"]) is None:                                                # pass 3
            raise Err(422, "validation_failed", "effective_from must be a real YYYY-MM-DD date")
        out = {"effective_from": obj["effective_from"]}
        for f, lo, hi in (("slot_minutes", 1, 1440), ("reservation_duration_minutes", 1, 1440),
                          ("cancellation_cutoff_minutes", 0, 10080)):
            v = obj[f]
            if not is_json_int(v) or not lo <= int(v) <= hi:
                raise Err(422, "validation_failed", f"{f} must be an integer {lo}..{hi}")
            out[f] = int(v)
        hours, seen_wd = [], set()
        for h in obj["opening_hours"]:
            if not isinstance(h, dict) or any(not isinstance(h.get(x), str) for x in ("weekday", "opens", "closes")):
                raise bad
            wd = h["weekday"]
            if wd not in WEEKDAYS or wd in seen_wd:
                raise Err(422, "validation_failed", "weekday must be mon..sun and unique")
            seen_wd.add(wd)
            o, c = h["opens"], h["closes"]
            om, cm = hhmm_to_minutes(o), hhmm_to_minutes(c)
            if om is None or cm is None or om == 24 * 60 or cm <= om:
                raise Err(422, "validation_failed", "opens/closes must be HH:MM with closes later than opens")
            hours.append({"weekday": wd, "opens": o, "closes": c})
        out["opening_hours"] = hours
        caps = obj["capacities"]
        if set(caps) != {t["id"] for t in r["tables"]}:
            raise Err(422, "validation_failed", "capacities must name exactly the restaurant's tables")
        out["capacities"] = {}
        for t in r["tables"]:
            v = caps[t["id"]]
            if not is_json_int(v) or not 1 <= int(v) <= 100:
                raise Err(422, "validation_failed", "capacities must be integers 1..100")
            out["capacities"][t["id"]] = int(v)
        return out

    def publish_policy(self, user_id: str, rid: str, obj: dict):
        r = self.restaurants.get(rid)
        if r is None:
            raise Err(404, "not_found", "no such restaurant")                                      # B23
        if user_id not in r["manager_user_ids"]:
            raise Err(403, "forbidden", "not a manager of this restaurant")
        pol = self._validate_policy(r, obj)
        pol["policy_version"] = len(r["policies"]) + 1                                             # B27, B28
        r["policies"].append(pol)
        r["revision"] += 1                                                                          # R-54
        return 201, json.loads(json.dumps(pol))

    def list_policies(self, rid: str):
        r = self.restaurants.get(rid)
        if r is None:
            raise Err(404, "not_found", "no such restaurant")                                      # Q22
        return 200, {"policies": json.loads(json.dumps(r["policies"]))}

    def _restaurant_detail(self, r: dict) -> dict:
        """B41: the original fixture configuration (policies and the revision counter are not part of it)."""
        return json.loads(json.dumps({k_: r[k_] for k_ in ("id", "name", "timezone", "slot_minutes",
                                                            "reservation_duration_minutes", "cancellation_cutoff_minutes",
                                                            "opening_hours", "tables", "combinable", "manager_user_ids")}))

    # ------------------------------------------------------------------ table sets (stage 2)
    @staticmethod
    def _both_table_fields(obj: dict) -> None:
        """R-43: both `table_id` and `table_ids` present -> 422 whatever their JSON types."""
        if "table_id" in obj and "table_ids" in obj:
            raise Err(422, "validation_failed", "send table_id or table_ids, not both")

    @staticmethod
    def _table_set(obj: dict, required: bool) -> Optional[list[str]]:
        """Pass-3 value rules for `table_id` / `table_ids` (types were checked in pass 1).

        Both present -> 422; neither (when required) -> 422; empty id / empty list / duplicates -> 422.
        Returns the requested ids in request order, or None when neither field is present and not required.
        """
        has_one, has_many = "table_id" in obj, "table_ids" in obj
        if has_one and has_many:
            raise Err(422, "validation_failed", "send table_id or table_ids, not both")
        if not has_one and not has_many:
            if required:
                raise Err(422, "validation_failed", "table_ids is required")
            return None
        ids = [obj["table_id"]] if has_one else list(obj["table_ids"])
        if not ids:
            raise Err(422, "validation_failed", "table_ids must not be empty")
        if any(x == "" or len(x) > ID_MAX for x in ids):                          # R-42
            raise Err(422, "validation_failed", "table ids must be 1..64 characters")
        if len(set(ids)) != len(ids):
            raise Err(422, "validation_failed", "duplicate table id in the set")
        if len(ids) > 2:                                                          # R-35: before any 404
            raise Err(422, "combination_not_allowed", "more than two tables")
        return ids

    @staticmethod
    def _declared_order(r: dict, ids: list[str]) -> list[str]:
        """A pair is reported in its `combinable` order (Q5); singles unchanged."""
        if len(ids) == 2:
            for a, b in r["combinable"]:
                if {a, b} == set(ids):
                    return [a, b]
        return list(ids)

    def _check_combination(self, r: dict, ids: list[str]) -> list[str]:
        """R-35: (more than two was refused in _table_set) 404 for an unknown table; then an undeclared pair ->
        combination_not_allowed (before the time rules). Returns declared order."""
        for x in ids:
            if not any(t["id"] == x for t in r["tables"]):
                raise Err(404, "not_found", "no such table at this restaurant")
        if len(ids) == 2 and not any({a, b} == set(ids) for a, b in r["combinable"]):
            raise Err(422, "combination_not_allowed", "pair is not combinable")
        return self._declared_order(r, ids)

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
            "series": [self.series[s_] for s_ in self.series_order],
            "closures": [{**c, "from": fmt_utc(c["from"]), "to": fmt_utc(c["to"])} for c in self.closures],
            "plans": [self._plan_export(self.plans[p_]) for p_ in self.plan_order],
        }
        return json.loads(json.dumps({"track": TRACK, "format_version": FORMAT_VERSION, "state": state}))

    def import_(self, doc: dict):
        if doc.get("track") != TRACK or doc.get("format_version") != FORMAT_VERSION:
            raise Err(422, "validation_failed", "wrong track or format_version")
        state = doc.get("state")
        if not isinstance(state, dict) or state.get("schema") not in (STATE_MARKER, STATE_MARKER_V3, STATE_MARKER_V2, STATE_MARKER_V1):
            raise Err(422, "validation_failed", "state is not one this service produced")   # R-24
        if state.get("schema") == STATE_MARKER_V1:
            state = self._migrate_v1(state)                                                  # stage 2 (B52)
        if state.get("schema") == STATE_MARKER_V2:
            state = json.loads(json.dumps(state))
            state["schema"] = STATE_MARKER_V3
            state.setdefault("series", [])        # stage-3 (B76): records without revision/terms/history are filled in
        if state.get("schema") == STATE_MARKER_V3:
            state = json.loads(json.dumps(state))
            state["schema"] = STATE_MARKER
            state.setdefault("closures", [])      # stage-4 (B41): no closures, no plans
            state.setdefault("plans", [])
        new = self._validated_state(state)
        self._adopt(new)
        return 204, None

    @staticmethod
    def _migrate_v1(state: dict) -> dict:
        """Stage-1 model state -> stage-2: single `table_id` becomes `table_ids`, restaurants gain `combinable`."""
        state = json.loads(json.dumps(state))
        for r in state.get("restaurants") or []:
            if isinstance(r, dict):
                r.setdefault("combinable", [])
        for rec in state.get("reservations") or []:
            if isinstance(rec, dict) and "table_id" in rec and "table_ids" not in rec:
                rec["table_ids"] = [rec.pop("table_id")]
        state["schema"] = STATE_MARKER_V2
        return state

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
            for f in ("reservation_id", "reference", "user_id", "restaurant_id", "status",
                      "starts_at_local", "start", "end", "created"):
                need(isinstance(s_.get(f), str) and s_[f] != "")
            need(len(s_["reservation_id"]) <= ID_MAX and REFERENCE_RE.match(s_["reference"]))   # R-28
            need(s_["status"] in ("confirmed", "cancelled") and s_["user_id"] in new.users)
            r = new.restaurants.get(s_["restaurant_id"])
            ids = s_.get("table_ids")
            need(r is not None and isinstance(ids, list) and 1 <= len(ids) <= 2 and len(set(ids)) == len(ids))
            need(all(isinstance(x, str) and any(t["id"] == x for t in r["tables"]) for x in ids))
            need(len(ids) == 1 or any({a, b} == set(ids) for a, b in r["combinable"]))   # R-34: declared pair only
            need(is_json_int(s_.get("party_size")) and int(s_["party_size"]) >= 1)
            need(parse_local(s_["starts_at_local"]) is not None)
            start, end, created = (parse_rfc3339(s_[f]) for f in ("start", "end", "created"))
            need(start is not None and end is not None and created is not None and end > start)
            need(min(start.year, end.year, created.year) >= 1000)     # a zero timestamp is not a value we produced
            need(s_["reservation_id"] not in new.reservations and s_["reference"] not in new.references)
            if "revision" in s_ or "terms" in s_ or "history" in s_:                      # stage-3 record
                need(is_json_int(s_.get("revision")) and int(s_["revision"]) >= 1)
                terms = s_.get("terms")
                need(isinstance(terms, dict) and set(terms) == set(TERMS_KEYS))
                need(is_json_int(terms["policy_version"]) and 0 <= int(terms["policy_version"]) <= len(r["policies"]))
                for f_, lo, hi in (("slot_minutes", 1, 1440), ("reservation_duration_minutes", 1, 1440),
                                   ("cancellation_cutoff_minutes", 0, 10080)):
                    need(is_json_int(terms[f_]) and lo <= int(terms[f_]) <= hi)
                need(isinstance(terms["opening_hours"], list) and isinstance(terms["capacities"], dict))
                need(set(terms["capacities"]) == {t["id"] for t in r["tables"]}
                     and all(is_json_int(v) and 1 <= int(v) <= 100 for v in terms["capacities"].values()))
                hist = s_.get("history")
                need(isinstance(hist, list) and len(hist) >= 1)
                for i_, h in enumerate(hist):
                    need(isinstance(h, dict) and h.get("seq") == i_ + 1
                         and h.get("event") in ("created", "changed", "cancelled", "reassigned"))
                    need(h.get("event") != "reassigned" or (isinstance(h.get("plan_id"), str) and h["plan_id"] != ""))   # stage 4
                    need(isinstance(h.get("changes"), list) and parse_rfc3339(h.get("at")) is not None)
                    need(is_json_int(h.get("revision")) and 1 <= int(h["revision"]) <= int(s_["revision"]))
                    need(isinstance(h.get("accepted_terms"), dict) and set(h["accepted_terms"]) == set(TERMS_KEYS))
                need(hist[0]["event"] == "created" and all(h["event"] != "created" for h in hist[1:]))
                need(all(h["event"] != "cancelled" for h in hist[:-1]))                          # nothing follows cancelled
                need(int(hist[-1]["revision"]) == int(s_["revision"]))                            # last entry = current revision
                need((hist[-1]["event"] == "cancelled") == (s_["status"] == "cancelled") or hist[-1]["event"] != "cancelled")
                need(all(int(hist[j]["revision"]) <= int(hist[j + 1]["revision"]) for j in range(len(hist) - 1)))
                new._add_reservation(s_["reservation_id"], s_["reference"], s_["user_id"], s_["restaurant_id"],
                                     new._declared_order(r, ids), int(s_["party_size"]), s_["starts_at_local"], start, end,
                                     created, s_["terms"], history=json.loads(json.dumps(s_["history"])))
                rec = new.reservations[s_["reservation_id"]]
                rec["revision"] = int(s_["revision"])
                rec["series_id"], rec["series_index"] = s_.get("series_id"), s_.get("series_index")
                rec["status"] = s_["status"]
            else:                                                                          # v1/v2 record: B76, Q19
                new._add_reservation(s_["reservation_id"], s_["reference"], s_["user_id"], s_["restaurant_id"],
                                     new._declared_order(r, ids), int(s_["party_size"]), s_["starts_at_local"], start, end,
                                     created, new._policy0(r))
                new.reservations[s_["reservation_id"]]["status"] = s_["status"]            # R-55: revision 1, created only
        for i in alist("idempotency"):
            need(isinstance(i, dict) and isinstance(i.get("key"), str) and i["key"] not in new.idem)
            need(isinstance(i.get("body"), dict) and is_json_int(i.get("status")) and i.get("response") is not None)
            need(200 <= int(i["status"]) <= 299)                       # O-9: only 2xx receipts exist (R-18)
            new.idem[i["key"]] = {"body": i["body"], "status": int(i["status"]), "response": i["response"]}
        counters = state.get("counters")
        need(isinstance(counters, dict))
        for k_ in ("user", "reservation"):
            need(is_json_int(counters.get(k_)) and int(counters[k_]) >= 0)
        new.counters = {k_: int(counters[k_]) for k_ in ("user", "reservation")}
        for ser in alist("series"):
            need(isinstance(ser, dict) and isinstance(ser.get("series_id"), str) and ser["series_id"] not in new.series)
            need(0 < len(ser["series_id"]) <= ID_MAX)
            need(ser.get("user_id") in new.users and is_json_int(ser.get("revision")) and int(ser["revision"]) >= 1)
            need(is_json_int(ser.get("interval_weeks")) and 1 <= int(ser["interval_weeks"]) <= 4)
            occ = ser.get("occurrences")
            need(isinstance(occ, list) and 2 <= len(occ) <= 12 and is_json_int(ser.get("count")) and int(ser["count"]) == len(occ))
            for i_, o in enumerate(occ):
                need(isinstance(o, dict) and o.get("index") == i_ and o.get("reservation_id") in new.reservations
                     and isinstance(o.get("exception"), bool))
                rec = new.reservations[o["reservation_id"]]
                need(rec["user_id"] == ser["user_id"] and rec.get("series_id") == ser["series_id"] and rec.get("series_index") == i_)
            seen_res = [o["reservation_id"] for o in occ]
            need(len(set(seen_res)) == len(seen_res))
            new.series[ser["series_id"]] = {"series_id": ser["series_id"], "user_id": ser["user_id"], "revision": int(ser["revision"]),
                                            "interval_weeks": int(ser["interval_weeks"]), "count": int(ser["count"]),
                                            "occurrences": [{"index": o["index"], "reservation_id": o["reservation_id"],
                                                             "exception": o["exception"]} for o in occ]}
            new.series_order.append(ser["series_id"])
        for rec in new.reservations.values():                                      # a record claiming a series must be in it
            sid = rec.get("series_id")
            need(sid is None or (sid in new.series and any(o["reservation_id"] == rec["reservation_id"]
                                                             for o in new.series[sid]["occurrences"])))
        for c in alist("closures"):                                                 # stage 4
            need(isinstance(c, dict) and c.get("restaurant_id") in new.restaurants and isinstance(c.get("table_id"), str))
            need(any(t["id"] == c["table_id"] for t in new.restaurants[c["restaurant_id"]]["tables"]))
            frm, to = parse_rfc3339(c.get("from")), parse_rfc3339(c.get("to"))
            need(frm is not None and to is not None and frm < to and isinstance(c.get("plan_id"), str))
            new.closures.append({"restaurant_id": c["restaurant_id"], "table_id": c["table_id"], "from": frm, "to": to,
                                 "plan_id": c["plan_id"]})
        for pl in alist("plans"):
            need(isinstance(pl, dict) and isinstance(pl.get("plan_id"), str) and 0 < len(pl["plan_id"]) <= ID_MAX)
            need(pl["plan_id"] not in new.plans and pl.get("restaurant_id") in new.restaurants)
            need(is_json_int(pl.get("revision_at_preview")) and isinstance(pl.get("applied"), bool))
            cl = pl.get("closure")
            need(isinstance(cl, dict) and isinstance(cl.get("table_id"), str))
            frm, to = parse_rfc3339(cl.get("from")), parse_rfc3339(cl.get("to"))
            need(frm is not None and to is not None and frm < to)
            need(isinstance(pl.get("assignments"), list) and isinstance(pl.get("response"), dict))
            for a in pl["assignments"]:
                need(isinstance(a, dict) and a.get("reservation_id") in new.reservations and isinstance(a.get("table_ids"), list)
                     and isinstance(a.get("changed"), bool))
            new.plans[pl["plan_id"]] = {"plan_id": pl["plan_id"], "restaurant_id": pl["restaurant_id"],
                                        "revision_at_preview": int(pl["revision_at_preview"]), "applied": pl["applied"],
                                        "closure": {"table_id": cl["table_id"], "from": frm, "to": to},
                                        "assignments": [dict(a) for a in pl["assignments"]], "response": pl["response"]}
            new.plan_order.append(pl["plan_id"])
        return new

    @staticmethod
    def _plan_export(pl: dict) -> dict:
        return {**pl, "closure": {"table_id": pl["closure"]["table_id"], "from": fmt_utc(pl["closure"]["from"]),
                                  "to": fmt_utc(pl["closure"]["to"])}}

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
        return 200, self._restaurant_detail(r)

    # ------------------------------------------------------------------ availability (R-5, R-12, R-23)
    def _window(self, r: dict, pol: dict, day: date) -> Optional[tuple[int, int, datetime]]:
        """(opens_minutes, closes_minutes, closes_instant) for the local day under `pol`, or None when closed."""
        wd = WEEKDAYS[day.weekday()]
        h = next((h for h in pol["opening_hours"] if h["weekday"] == wd), None)
        if h is None:
            return None
        opens, closes = hhmm_to_minutes(h["opens"]), hhmm_to_minutes(h["closes"])
        zone = ZoneInfo(r["timezone"])
        midnight = datetime(day.year, day.month, day.day)
        return opens, closes, closes_instant(midnight + timedelta(minutes=closes), zone)

    def _slots_for(self, r: dict, pol: dict, day: date) -> list[tuple[str, datetime]]:
        """(starts_at_local, utc_start) for every grid slot of `day`: wall-clock grid, absolute end check."""
        w = self._window(r, pol, day)
        if w is None:
            return []
        opens, closes, closes_at = w
        zone = ZoneInfo(r["timezone"])
        dur = timedelta(minutes=pol["reservation_duration_minutes"])
        midnight = datetime(day.year, day.month, day.day)
        out: list[tuple[str, datetime]] = []
        t = opens
        while t < closes:
            naive = midnight + timedelta(minutes=t)
            utc = resolve_local(naive, zone)
            if utc is not None and utc + dur <= closes_at:                       # R-23
                out.append((naive.strftime("%Y-%m-%dT%H:%M"), utc))
            t += pol["slot_minutes"]
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
        explain = "explain" in q                                                            # B5 (Q2, Q3)
        if explain and q["explain"] != "true":
            raise Err(422, "validation_failed", "explain accepts only true")
        r = self.restaurants.get(q["restaurant_id"])
        if r is None:
            raise Err(404, "not_found", "no such restaurant")
        pol = self._policy_for(r, day)                                                      # B41, Q12
        zone = ZoneInfo(r["timezone"])
        dur = timedelta(minutes=pol["reservation_duration_minutes"])
        slots = []
        caps = pol["capacities"]
        for local, start in self._slots_for(r, pol, day):
            free = {t["id"] for t in r["tables"] if not self._table_busy(t["id"], start, start + dur, None)}
            ids = [t["id"] for t in r["tables"] if caps[t["id"]] >= party and t["id"] in free]
            options = [{"table_ids": [x], "capacity": caps[x]} for x in ids]
            for a, b in r["combinable"]:                                                   # stage 2 (B67, B68)
                if a in free and b in free and caps[a] + caps[b] >= party:
                    options.append({"table_ids": [a, b], "capacity": caps[a] + caps[b]})
            slot = {"starts_at_local": local, "starts_at": fmt(start, zone),
                    "available_table_ids": ids, "available_options": options}
            if explain:                                                                     # B7-B10
                slot["explain"] = [{"table_id": t["id"], "policy_version": pol["policy_version"],
                                    "available": caps[t["id"]] >= party and t["id"] in free,
                                    "rules": [{"rule": "capacity", "holds": caps[t["id"]] >= party},
                                              {"rule": "no_overlap", "holds": t["id"] in free}]} for t in r["tables"]]
            slots.append(slot)
        return 200, {"restaurant_id": r["id"], "date": q["date"], "timezone": r["timezone"], "slots": slots}

    def _table_closed(self, table_id: str, start: datetime, end: datetime) -> bool:
        return any(c["table_id"] == table_id and c["from"] < end and start < c["to"] for c in self.closures)

    def _table_busy(self, table_id: str, start: datetime, end: datetime, exclude: Optional[set[str]]) -> bool:
        if self._table_closed(table_id, start, end):                       # stage 4 (B25): closures block like bookings
            return True
        for rec in self.reservations.values():
            if rec["status"] != "confirmed" or table_id not in rec["table_ids"]:
                continue
            if exclude and rec["reservation_id"] in exclude:
                continue
            if rec["start"] < end and start < rec["end"]:
                return True
        return False

    # ------------------------------------------------------------------ booking rules (R-7)
    def _resolve_booking(self, r: dict, pol: dict, table_ids: list[str], naive: datetime, party: int,
                         exclude: Optional[set[str]] = None, check_overlap: bool = True):
        """404 table -> combination rules -> invalid_local_time -> outside_opening_hours -> not_on_slot_grid ->
        capacity (summed, from `pol`) -> overlap on any member. Returns (start, end, ids in declared order)."""
        table_ids = self._check_combination(r, table_ids)
        caps = pol["capacities"]
        zone = ZoneInfo(r["timezone"])
        start = resolve_local(naive, zone)
        if start is None:
            raise Err(422, "invalid_local_time", "local time does not exist")
        dur = timedelta(minutes=pol["reservation_duration_minutes"])
        w = self._window(r, pol, naive.date())
        minutes = naive.hour * 60 + naive.minute
        if w is None or not (w[0] <= minutes < w[1]) or start + dur > w[2]:        # R-23
            raise Err(422, "outside_opening_hours", "slot outside opening hours")
        if (minutes - w[0]) % pol["slot_minutes"] != 0:
            raise Err(422, "not_on_slot_grid", "starts_at_local is not on the slot grid")
        if party > sum(caps[x] for x in table_ids):
            raise Err(422, "party_exceeds_capacity", "party_size exceeds the capacity of the selection")
        end = start + dur
        if check_overlap and any(self._table_busy(x, start, end, exclude) for x in table_ids):
            raise Err(409, "table_unavailable", "a table is taken for an overlapping interval")
        return start, end, table_ids

    def _view(self, rec: dict) -> dict:
        zone = ZoneInfo(self.restaurants[rec["restaurant_id"]]["timezone"])
        out = {
            "reservation_id": rec["reservation_id"], "reference": rec["reference"],
            "restaurant_id": rec["restaurant_id"], "table_ids": list(rec["table_ids"]),
            "party_size": rec["party_size"], "status": rec["status"],
            "starts_at_local": rec["starts_at_local"], "starts_at": fmt(rec["start"], zone),
            "ends_at": fmt(rec["end"], zone), "created_at": fmt_utc(rec["created"]),
        }
        if len(rec["table_ids"]) == 1:                                     # stage 2 (B71): table_id iff one member
            out["table_id"] = rec["table_ids"][0]
        out["revision"] = rec["revision"]                                   # stage 3 (B43)
        out["accepted_terms"] = json.loads(json.dumps(rec["terms"]))
        return out

    # ------------------------------------------------------------------ reservations
    BOOKING_FIELDS = [("restaurant_id", True, "str"), ("table_id", False, "str"), ("table_ids", False, "list"),
                      ("starts_at_local", True, "str"), ("party_size", True, "party")]

    def create_reservation(self, user_id: str, obj: dict):
        self._both_table_fields(obj)                                 # R-43: before the type pass
        check_fields(obj, self.BOOKING_FIELDS)                       # R-19 passes 1 and 2
        if "table_id" not in obj and "table_ids" not in obj:
            raise Err(422, "validation_failed", "table_ids is required")
        if obj["restaurant_id"] == "" or len(obj["restaurant_id"]) > ID_MAX:      # R-42
            raise Err(422, "validation_failed", "restaurant_id must be 1..64 characters")
        table_ids = self._table_set(obj, required=True)              # pass 3
        naive = local_value(obj["starts_at_local"])
        party = party_value(obj["party_size"])
        r = self.restaurants.get(obj["restaurant_id"])
        if r is None:
            raise Err(404, "not_found", "no such restaurant")
        pol = self._policy_for(r, naive.date())                              # B41: the selected policy decides
        start, end, table_ids = self._resolve_booking(r, pol, table_ids, naive, party)
        rid = self._new_reservation_id()
        ref = self._new_reference()
        self._add_reservation(rid, ref, user_id, obj["restaurant_id"], table_ids, party,
                              obj["starts_at_local"], start, end, self.now(), pol)
        r["revision"] += 1                                                   # R-54
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
        cutoff = rec["terms"]["cancellation_cutoff_minutes"]               # B48: the accepted cutoff
        return rec["start"] - self.now() <= timedelta(minutes=cutoff)       # R-6: boundary is "within"

    def cancel(self, user_id: str, reference: str):                       # R-13
        rec = self._mine(user_id, reference)
        if rec["status"] == "cancelled":
            return 200, self._view(rec)
        if self._cutoff_passed(rec):
            raise Err(409, "cutoff_passed", "within the cancellation cutoff")
        self._cancel_record(rec, self.now())                                 # B51, B71
        self.restaurants[rec["restaurant_id"]]["revision"] += 1               # R-54
        return 200, self._view(rec)

    AMEND_FIELDS = [("table_id", False, "str"), ("table_ids", False, "list"), ("starts_at_local", False, "str"),
                    ("party_size", False, "party")]

    def _amend_values(self, rec: dict, obj: dict) -> tuple[list[str], str, datetime, int]:
        """Pass 3 of R-19 for an amendment: values of the supplied fields; omitted keep current."""
        table_ids = self._table_set(obj, required=False)
        if table_ids is None:
            table_ids = list(rec["table_ids"])
        local = obj["starts_at_local"] if "starts_at_local" in obj else rec["starts_at_local"]
        naive = local_value(local)
        party = party_value(obj["party_size"]) if "party_size" in obj else rec["party_size"]
        return table_ids, local, naive, party

    @staticmethod
    def _expected_revision(rec: dict, obj: dict) -> None:
        """B52 (Q8): invalid type/range -> 422; a positive integer differing from the current revision -> 409."""
        if "expected_revision" not in obj:
            return
        v = obj["expected_revision"]
        if not is_json_int(v) or int(v) < 1:
            raise Err(422, "validation_failed", "expected_revision must be a positive integer")
        if int(v) != rec["revision"]:
            raise Err(409, "stale_revision", "expected_revision does not match the current revision")

    def _plan_amendment(self, rec: dict, obj: dict):
        """Shared by PATCH and move items: returns None for a no-op, else (ids, local, party, start, end, pol)."""
        table_ids, local, naive, party = self._amend_values(rec, obj)
        r = self.restaurants[rec["restaurant_id"]]
        same_set = set(table_ids) == set(rec["table_ids"]) and len(table_ids) == len(rec["table_ids"])
        if same_set and local == rec["starts_at_local"] and party == rec["party_size"]:
            return None                                                     # B50: no-op
        pol = self._policy_for(r, naive.date())                              # B49: the resulting date's policy
        start, end, table_ids = self._resolve_booking(r, pol, table_ids, naive, party,
                                                      exclude={rec["reservation_id"]}, check_overlap=False)
        return table_ids, local, party, start, end, pol

    def _apply_amendment(self, rec: dict, plan, at: datetime) -> None:
        table_ids, local, party, start, end, pol = plan
        changes = self._diff(rec["table_ids"], table_ids, rec["starts_at_local"], local, rec["party_size"], party)
        rec.update({"table_ids": table_ids, "starts_at_local": local, "party_size": party, "start": start, "end": end,
                    "terms": json.loads(json.dumps(pol)), "revision": rec["revision"] + 1})
        self._record(rec, "changed", changes, at)
        self._bump_series(rec, exception=True)                              # B70

    def patch(self, user_id: str, reference: str, obj: dict):             # R-14, Q8
        self._both_table_fields(obj)                                      # R-43
        check_fields(obj, self.AMEND_FIELDS)                              # wrong types 400 before 404
        rec = self._mine(user_id, reference)
        self._expected_revision(rec, obj)                                 # R-49: 422 invalid -> 409 stale, before cancelled
        if rec["status"] == "cancelled":
            raise Err(409, "reservation_cancelled", "reservation is cancelled")
        if self._cutoff_passed(rec):
            raise Err(409, "cutoff_passed", "within the amendment cutoff")
        plan = self._plan_amendment(rec, obj)
        if plan is None:
            return 200, self._view(rec)
        table_ids, _, _, start, end, _ = plan
        if any(self._table_busy(x, start, end, {rec["reservation_id"]}) for x in table_ids):
            raise Err(409, "table_unavailable", "a table is taken for an overlapping interval")
        self._apply_amendment(rec, plan, self.now())
        self.restaurants[rec["restaurant_id"]]["revision"] += 1               # R-54
        return 200, self._view(rec)

    # ------------------------------------------------------------------ atomic moves (R-22)
    def moves(self, user_id: str, obj: dict):
        moves = obj.get("moves")                                           # (a) structure -> 422
        if not isinstance(moves, list) or not 1 <= len(moves) <= 8:
            raise Err(422, "validation_failed", "moves must contain 1..8 objects")
        refs: list[str] = []
        for m in moves:
            if not isinstance(m, dict) or not isinstance(m.get("reference"), str) or m["reference"] == "":   # R-42
                raise Err(422, "validation_failed", "each move needs a string reference")
            if m["reference"] in refs:
                raise Err(422, "validation_failed", "duplicate reference")
            refs.append(m["reference"])
        for m in moves:                                                    # (b) item field types -> 400
            self._both_table_fields(m)                                     # R-43, per item in input order
            check_fields(m, self.AMEND_FIELDS)
        resolved = []                                                      # (c) per item, input order
        first_restaurant: Optional[str] = None
        for m in moves:
            rec = self._mine(user_id, m["reference"])
            if first_restaurant is None:
                first_restaurant = rec["restaurant_id"]
            elif rec["restaurant_id"] != first_restaurant:
                raise Err(422, "validation_failed", "bookings belong to different restaurants")
            self._expected_revision(rec, m)                                 # R-57: before cancelled
            if rec["status"] == "cancelled":
                raise Err(409, "reservation_cancelled", "reservation is cancelled")
            if self._cutoff_passed(rec):
                raise Err(409, "cutoff_passed", "within the amendment cutoff")
            plan = self._plan_amendment(rec, m)                              # B79, B81
            if plan is None:
                resolved.append((rec, list(rec["table_ids"]), rec["start"], rec["end"], None))
            else:
                resolved.append((rec, plan[0], plan[3], plan[4], plan))
        listed = {rec["reservation_id"] for rec, *_ in resolved}          # (d) occupancy over every member (B85)
        for i, (rec, ids, start, end, _) in enumerate(resolved):
            if any(self._table_busy(x, start, end, exclude=listed) for x in ids):
                raise Err(409, "table_unavailable", "overlap with an unlisted booking")
            for j, (_, ids2, start2, end2, _) in enumerate(resolved):
                if i != j and set(ids) & set(ids2) and start < end2 and start2 < end:
                    raise Err(409, "table_unavailable", "overlap among listed bookings")
        at = self.now()
        changed_series: set[str] = set()
        any_change = False
        for rec, _, _, _, plan in resolved:
            if plan is not None:                                            # B83: one revision and one entry per change
                any_change = True
                sid = rec.get("series_id")
                self._apply_amendment_in_batch(rec, plan, at, changed_series)
        if any_change:
            self.restaurants[first_restaurant]["revision"] += 1             # B83 (Q1)
        for sid in changed_series:                                          # B84: once per affected series
            self.series[sid]["revision"] += 1
        return 201, {"reservations": [self._view(rec) for rec, *_ in resolved]}

    def _apply_amendment_in_batch(self, rec: dict, plan, at: datetime, changed_series: set) -> None:
        table_ids, local, party, start, end, pol = plan
        changes = self._diff(rec["table_ids"], table_ids, rec["starts_at_local"], local, rec["party_size"], party)
        rec.update({"table_ids": table_ids, "starts_at_local": local, "party_size": party, "start": start, "end": end,
                    "terms": json.loads(json.dumps(pol)), "revision": rec["revision"] + 1})
        self._record(rec, "changed", changes, at)
        sid = rec.get("series_id")
        if sid and sid in self.series:
            self.series[sid]["occurrences"][rec["series_index"]]["exception"] = True
            changed_series.add(sid)

    # ------------------------------------------------------------------ history and decision (stage 3)
    def history(self, user_id: str, reference: str):
        rec = self._mine(user_id, reference)
        return 200, {"reference": rec["reference"], "entries": json.loads(json.dumps(rec["history"]))}

    def decision(self, user_id: str, reference: str):
        rec = self._mine(user_id, reference)
        return 200, {"reference": rec["reference"], "revision": rec["revision"],
                     "accepted_terms": json.loads(json.dumps(rec["terms"]))}

    # ------------------------------------------------------------------ recurring series (stage 3)
    def _series_view(self, ser: dict) -> dict:
        return {"series_id": ser["series_id"], "revision": ser["revision"], "interval_weeks": ser["interval_weeks"],
                "occurrences": [{"index": o["index"], "reference": self.reservations[o["reservation_id"]]["reference"],
                                 "exception": o["exception"],
                                 "reservation": self._view(self.reservations[o["reservation_id"]])}
                                for o in ser["occurrences"]]}

    def create_series(self, user_id: str, obj: dict):
        """Q13/Q14: types (anchor 400) -> missing 422 -> values 422 -> 404 anchor -> 409 cancelled -> 409 in series ->
        409 cutoff -> occurrences in index order (first failure decides)."""
        if "anchor_reference" in obj and not isinstance(obj["anchor_reference"], str):
            raise Err(400, "malformed_request", "anchor_reference must be a string")
        for f in ("anchor_reference", "count", "interval_weeks"):
            if f not in obj:
                raise Err(422, "validation_failed", f"{f} is required")
        if obj["anchor_reference"] == "" or len(obj["anchor_reference"]) > ID_MAX:            # R-60
            raise Err(422, "validation_failed", "anchor_reference must be 1..64 characters")
        count, interval = obj["count"], obj["interval_weeks"]
        if not is_json_int(count) or not 2 <= int(count) <= 12:
            raise Err(422, "validation_failed", "count must be an integer 2..12")
        if not is_json_int(interval) or not 1 <= int(interval) <= 4:
            raise Err(422, "validation_failed", "interval_weeks must be an integer 1..4")
        count, interval = int(count), int(interval)
        anchor = self._mine(user_id, obj["anchor_reference"])
        if anchor["status"] == "cancelled":
            raise Err(409, "reservation_cancelled", "anchor is cancelled")
        if anchor.get("series_id"):
            raise Err(409, "already_in_series", "anchor already belongs to a series")
        if self._cutoff_passed(anchor):
            raise Err(409, "cutoff_passed", "anchor is within its cutoff")
        r = self.restaurants[anchor["restaurant_id"]]
        base = parse_local(anchor["starts_at_local"])
        planned = []
        for i in range(1, count):                                              # B60-B65
            naive = base + timedelta(days=7 * i * interval)
            pol = self._policy_for(r, naive.date())
            start, end, ids = self._resolve_booking(r, pol, list(anchor["table_ids"]), naive, anchor["party_size"])
            for _, _, s2, e2, ids2 in planned:                                  # Q16: siblings too
                if set(ids) & set(ids2) and start < e2 and s2 < end:
                    raise Err(409, "table_unavailable", "occurrences overlap")
            planned.append((naive, pol, start, end, ids))
        now = self.now()
        sid = f"ser_{next(_SEQ)}"
        ser = {"series_id": sid, "user_id": user_id, "revision": 1, "interval_weeks": interval, "count": count,
               "occurrences": [{"index": 0, "reservation_id": anchor["reservation_id"], "exception": False}]}
        anchor["series_id"], anchor["series_index"] = sid, 0
        for i, (naive, pol, start, end, ids) in enumerate(planned, start=1):
            rid = self._new_reservation_id()
            ref = self._new_reference()
            self._add_reservation(rid, ref, user_id, anchor["restaurant_id"], ids, anchor["party_size"],
                                  naive.strftime("%Y-%m-%dT%H:%M"), start, end, now, pol)
            self.reservations[rid]["series_id"], self.reservations[rid]["series_index"] = sid, i
            ser["occurrences"].append({"index": i, "reservation_id": rid, "exception": False})
        self.series[sid] = ser
        self.series_order.append(sid)
        r["revision"] += 1                                                     # B73
        return 201, self._series_view(ser)

    def get_series(self, user_id: str, sid: str):
        ser = self.series.get(sid)
        if ser is None or ser["user_id"] != user_id:
            raise Err(404, "not_found", "no such series")
        return 200, self._series_view(ser)

    # ------------------------------------------------------------------ seating replans (stage 4)
    @staticmethod
    def _instant(v: Any) -> datetime:
        """B6 (Q1): an RFC 3339 instant with a numeric offset; `Z` is not an explicit offset."""
        if not isinstance(v, str):
            raise Err(400, "malformed_request", "instants must be strings")
        if v.endswith("Z") or v.endswith("z"):
            raise Err(422, "validation_failed", "instants need an explicit numeric offset")
        dt = parse_rfc3339(v)
        if dt is None or not re.search(r"[+-]\d{2}:\d{2}$", v):
            raise Err(422, "validation_failed", "instants must be RFC 3339 with an explicit offset")
        return dt

    def _manager(self, user_id: str, rid: str) -> dict:
        r = self.restaurants.get(rid)
        if r is None:
            raise Err(404, "not_found", "no such restaurant")
        if user_id not in r["manager_user_ids"]:
            raise Err(403, "forbidden", "not a manager of this restaurant")
        return r

    def _options(self, r: dict) -> list[list[str]]:
        """Option ranks (B12): singles in fixture order, then declared pairs."""
        return [[t["id"]] for t in r["tables"]] + [list(p_) for p_ in r["combinable"]]

    def replan_preview(self, user_id: str, rid: str, obj: dict):
        r = self._manager(user_id, rid)
        if "table_id" in obj and not isinstance(obj["table_id"], str):
            raise Err(400, "malformed_request", "table_id must be a string")
        for f in ("table_id", "from", "to"):
            if f not in obj:
                raise Err(422, "validation_failed", f"{f} is required")
        if obj["table_id"] == "" or len(obj["table_id"]) > ID_MAX:
            raise Err(422, "validation_failed", "table_id must be 1..64 characters")
        frm, to = self._instant(obj["from"]), self._instant(obj["to"])
        if not frm < to:
            raise Err(422, "validation_failed", "from must be before to")
        if not any(t["id"] == obj["table_id"] for t in r["tables"]):
            raise Err(404, "not_found", "no such table at this restaurant")
        closed = obj["table_id"]
        considered = sorted((rec for rec in self.reservations.values()
                             if rec["status"] == "confirmed" and rec["restaurant_id"] == rid
                             and rec["start"] < to and frm < rec["end"]), key=lambda x: x["reference"])
        if len(r["tables"]) > 6 or len(r["combinable"]) > 4 or len(considered) > 6:
            raise Err(422, "planning_limit", "input too large to plan")
        considered_ids = {rec["reservation_id"] for rec in considered}
        options = self._options(r)

        def feasible(rec: dict, ids: list[str]) -> bool:
            caps = rec["terms"]["capacities"]
            if closed in ids or sum(caps.get(x, 0) for x in ids) < rec["party_size"]:
                return False
            return not any(self._table_busy(x, rec["start"], rec["end"], exclude=considered_ids) for x in ids)

        per = []
        for rec in considered:
            cands = [(rank, ids) for rank, ids in enumerate(options) if feasible(rec, ids)]
            if not cands:
                raise Err(409, "no_feasible_plan", "a considered booking cannot be seated")
            per.append(cands)
        best = None
        best_key = None

        def search(i: int, chosen: list):
            nonlocal best, best_key
            if i == len(considered):
                moved = sum(1 for rec, (_, ids) in zip(considered, chosen) if set(ids) != set(rec["table_ids"]))
                unused = sum(sum(rec["terms"]["capacities"][x] for x in ids) - rec["party_size"]
                             for rec, (_, ids) in zip(considered, chosen))
                key = (moved, unused, tuple(rank for rank, _ in chosen))
                if best_key is None or key < best_key:
                    best_key, best = key, list(chosen)
                return
            rec = considered[i]
            for rank, ids in per[i]:
                clash = False
                for j in range(i):
                    other, (_, ids2) = considered[j], chosen[j]
                    if set(ids) & set(ids2) and rec["start"] < other["end"] and other["start"] < rec["end"]:
                        clash = True
                        break
                if not clash:
                    chosen.append((rank, ids))
                    search(i + 1, chosen)
                    chosen.pop()

        search(0, [])
        if best is None:
            raise Err(409, "no_feasible_plan", "no conflict-free assignment exists")
        zone = ZoneInfo(r["timezone"])
        pid = f"plan_{next(_SEQ)}"
        assignments = [{"reference": rec["reference"], "reservation_id": rec["reservation_id"], "table_ids": list(ids),
                        "changed": set(ids) != set(rec["table_ids"])} for rec, (_, ids) in zip(considered, best)]
        response = {"plan_id": pid, "restaurant_revision": r["revision"],
                    "closure": {"table_id": closed, "from": fmt(frm, zone), "to": fmt(to, zone)},
                    "assignments": [{k_: a[k_] for k_ in ("reference", "table_ids", "changed")} for a in assignments],
                    "moved_count": best_key[0], "unused_seats": best_key[1]}
        self.plans[pid] = {"plan_id": pid, "restaurant_id": rid, "revision_at_preview": r["revision"], "applied": False,
                           "closure": {"table_id": closed, "from": frm, "to": to}, "assignments": assignments,
                           "response": json.loads(json.dumps(response))}
        self.plan_order.append(pid)
        return 201, json.loads(json.dumps(response))

    def replan_apply(self, user_id: str, rid: str, pid: str, obj: dict):
        r = self._manager(user_id, rid)
        pl = self.plans.get(pid)
        if pl is None or pl["restaurant_id"] != rid:
            raise Err(404, "not_found", "no such plan")                                      # B19
        if pl["applied"]:
            raise Err(409, "plan_already_applied", "plan was already applied")               # B21 (Q11)
        if pl["revision_at_preview"] != r["revision"]:
            raise Err(409, "stale_plan", "the restaurant changed since the preview")         # B20
        at = self.now()
        self.closures.append({"restaurant_id": rid, "table_id": pl["closure"]["table_id"], "from": pl["closure"]["from"],
                              "to": pl["closure"]["to"], "plan_id": pid})
        touched_series: set[str] = set()
        for a in pl["assignments"]:
            rec = self.reservations[a["reservation_id"]]
            if a["changed"]:
                before = list(rec["table_ids"])
                rec["table_ids"] = list(a["table_ids"])
                rec["revision"] += 1                                                         # B23
                self._record(rec, "reassigned", [{"field": "table_ids", "from": before, "to": list(a["table_ids"])}], at)
                rec["history"][-1]["plan_id"] = pid
                if rec.get("series_id"):
                    touched_series.add(rec["series_id"])
        for sid in touched_series:                                                           # B39
            self.series[sid]["revision"] += 1
        r["revision"] += 1                                                                   # B24
        pl["applied"] = True
        out = {"plan_id": pid, "restaurant_revision": r["revision"],
               "reservations": [self._view(self.reservations[a["reservation_id"]]) for a in pl["assignments"]]}
        return 201, out

    # ------------------------------------------------------------------ series amendment (stage 4)
    def amend_series(self, user_id: str, sid: str, obj: dict):
        ser = self.series.get(sid)
        if ser is None or ser["user_id"] != user_id:
            raise Err(404, "not_found", "no such series")                                    # B29
        for f in ("expected_revision", "from_index", "local_time"):                          # B30: every problem 422
            if f not in obj:
                raise Err(422, "validation_failed", f"{f} is required")
        ev, fi, lt = obj["expected_revision"], obj["from_index"], obj["local_time"]
        if not is_json_int(ev) or int(ev) < 1:
            raise Err(422, "validation_failed", "expected_revision must be a positive integer")
        if not is_json_int(fi) or not 0 <= int(fi) <= ser["count"] - 1:
            raise Err(422, "validation_failed", "from_index must be an integer in 0..count-1")
        if not isinstance(lt, str) or hhmm_to_minutes(lt) is None or hhmm_to_minutes(lt) == 24 * 60:
            raise Err(422, "validation_failed", "local_time must be HH:MM")
        if int(ev) != ser["revision"]:
            raise Err(409, "stale_revision", "expected_revision does not match the series revision")
        plans = []
        for occ in ser["occurrences"][int(fi):]:                                             # B31, index order
            rec = self.reservations[occ["reservation_id"]]
            if rec["status"] != "confirmed" or occ["exception"]:
                continue
            new_local = rec["starts_at_local"][:11] + lt
            if new_local == rec["starts_at_local"]:
                continue                                                                     # B32: no-op
            if self._cutoff_passed(rec):
                raise Err(409, "cutoff_passed", "an occurrence is within its cutoff")       # B33
            r = self.restaurants[rec["restaurant_id"]]
            naive = local_value(new_local)
            pol = self._policy_for(r, naive.date())
            start, end, ids = self._resolve_booking(r, pol, list(rec["table_ids"]), naive, rec["party_size"],
                                                    check_overlap=False)
            plans.append((rec, (ids, new_local, rec["party_size"], start, end, pol)))
        changed_ids = {rec["reservation_id"] for rec, _ in plans}
        for rec, (ids, _, _, start, end, _) in plans:                                        # B34
            if any(self._table_busy(x, start, end, exclude=changed_ids) for x in ids):
                raise Err(409, "table_unavailable", "a resulting occurrence conflicts")
            for rec2, (ids2, _, _, s2, e2, _) in plans:
                if rec2 is not rec and set(ids) & set(ids2) and start < e2 and s2 < end:
                    raise Err(409, "table_unavailable", "resulting occurrences conflict")
        at = self.now()
        for rec, plan in plans:
            table_ids, local, party, start, end, pol = plan
            changes = self._diff(rec["table_ids"], table_ids, rec["starts_at_local"], local, rec["party_size"], party)
            rec.update({"starts_at_local": local, "start": start, "end": end, "terms": json.loads(json.dumps(pol)),
                        "revision": rec["revision"] + 1})
            self._record(rec, "changed", changes, at)                                        # B36: no exception flag
        if plans:
            ser["revision"] += 1
            self.restaurants[self.reservations[ser["occurrences"][0]["reservation_id"]]["restaurant_id"]]["revision"] += 1
        return 201, self._series_view(ser)
