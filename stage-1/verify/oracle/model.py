"""Tablekeeper stage-1 reference model (Oracle seat).

The smallest program that computes the specified results for a sequence of HTTP operations.
It models behaviour, not performance: one in-memory state machine, every request handled
atomically, written from the stage-1 specification alone (see evidence/stage-1/ledger-B.md).

Entry point: Model.handle(method, path, query, headers, body) -> (status, json_value_or_None)

* `query` is the raw query string (may be ""), `headers` a dict (case-insensitive keys),
  `body` the raw request body as bytes/str (or None).
* Ids, references, tokens are deterministic from counters; `created_at` comes from the injected
  clock. A differential runner must alias those against the implementation's values.

Precedence defaults for the open questions Q1..Q10 in ledger-B are implemented here and marked
with `# Qn` comments so a ruling can be applied in one place.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
LOCAL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
REFERENCE_RE = re.compile(r"^[A-Z0-9]{6,12}$")
DIGITS_RE = re.compile(r"^[0-9]+$")
ID_MAX = 64
TRACK = "tablekeeper"
FORMAT_VERSION = 1

MOVE_FIELDS = ("table_id", "starts_at_local", "party_size")


class Err(Exception):
    """An HTTP error outcome: status + code (+ optional message)."""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code} {message}")
        self.status = status
        self.code = code
        self.message = message or code

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def is_int(v: Any) -> bool:
    """A JSON integer: Python int but not bool. A float with an integral value counts (Q6)."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, float) and v.is_integer():  # Q6
        return True
    return False


def hhmm_to_minutes(s: str) -> Optional[int]:
    m = HHMM_RE.match(s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
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


def resolve_local(naive: datetime, zone: ZoneInfo) -> Optional[datetime]:
    """Resolve a wall-clock time in `zone` to a UTC instant.

    Returns None for a non-existent local time (spring-forward gap). An ambiguous local time
    (fall-back repeat) resolves to the first occurrence (fold=0, the pre-transition offset).
    """
    aware = naive.replace(tzinfo=zone, fold=0)
    utc = aware.astimezone(UTC)
    back = utc.astimezone(zone)
    if back.replace(tzinfo=None) != naive:  # round trip moved the wall clock: gap
        return None
    return utc


def fmt(utc: datetime, zone: ZoneInfo) -> str:
    """RFC 3339 with the zone's explicit offset at that instant."""
    local = utc.astimezone(zone)
    return local.replace(microsecond=0).isoformat()


def fmt_utc(utc: datetime) -> str:
    return utc.astimezone(UTC).replace(microsecond=0).isoformat()


def hash_password(pw: str) -> str:
    # The model never stores plaintext (B83); a salted hash is enough for a reference model.
    return "sha256$" + hashlib.sha256(("tablekeeper-oracle$" + pw).encode("utf-8")).hexdigest()


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
        self.idem: dict[str, dict] = {}           # "user|path|key" -> {"body":..., "status":..., "response":...}
        self.counters = {"user": 0, "token": 0, "reservation": 0}

    # ------------------------------------------------------------------ id generation
    def _next(self, kind: str) -> int:
        self.counters[kind] += 1
        return self.counters[kind]

    def _new_user_id(self) -> str:
        while True:
            uid = f"u_{self._next('user')}"
            if uid not in self.users:
                return uid

    def _new_token(self) -> str:
        return f"tok_{self._next('token')}"

    def _new_reservation_id(self) -> str:
        while True:
            rid = f"res_{self._next('reservation')}"
            if rid not in self.reservations:
                return rid

    def _new_reference(self) -> str:
        n = self.counters["reservation"]
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        while True:
            s, k = "", n
            while k:
                s = alphabet[k % 36] + s
                k //= 36
            ref = ("R" + s).rjust(6, "0")
            if ref not in self.references:
                return ref
            n += 1

    # ------------------------------------------------------------------ public entry
    def handle(self, method: str, path: str, query: str = "", headers: Optional[dict] = None,
               body: Any = None) -> tuple[int, Any]:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        try:
            status, out = self._route(method.upper(), path, query or "", headers, body)
            return status, out
        except Err as e:
            return e.status, e.body()

    # ------------------------------------------------------------------ routing
    def _route(self, method: str, path: str, query: str, headers: dict, body: Any):
        parts = [p for p in path.split("/") if p != ""]
        if method == "GET" and parts == ["health"]:
            return 200, {"status": "ok"}
        if method == "POST" and parts == ["_test", "reset"]:
            return self.reset(self._json_object(body))
        if method == "GET" and parts == ["_test", "export"]:
            return 200, self.export()
        if method == "POST" and parts == ["_test", "import"]:
            return self.import_(self._json_object(body))
        if method == "POST" and parts == ["auth", "signup"]:
            return self.signup(self._json_object(body))
        if method == "POST" and parts == ["auth", "login"]:
            return self.login(self._json_object(body))
        if method == "GET" and parts == ["restaurants"]:
            return self.list_restaurants()
        if method == "GET" and len(parts) == 2 and parts[0] == "restaurants":
            return self.get_restaurant(parts[1])
        if method == "GET" and parts == ["availability"]:
            return self.availability(self._query(query))
        # everything below needs a bearer token (Q1: 401 before body parsing)
        if parts and parts[0] in ("reservations", "reservation-moves"):
            user_id = self._auth(headers)
            if method == "POST" and parts == ["reservations"]:
                obj = self._json_object(body)
                key = self._idem_key(headers)
                hit = self._idem_lookup(user_id, "/reservations", key, obj)
                if hit is not None:
                    return hit
                status, out = self.create_reservation(user_id, obj)
                self._idem_store(user_id, "/reservations", key, obj, status, out)
                return status, out
            if method == "GET" and parts == ["reservations"]:
                return self.list_reservations(user_id)
            if method == "GET" and len(parts) == 2 and parts[0] == "reservations":
                return self.get_reservation(user_id, parts[1])
            if method == "POST" and len(parts) == 3 and parts[0] == "reservations" and parts[2] == "cancel":
                return self.cancel(user_id, parts[1])
            if method == "PATCH" and len(parts) == 2 and parts[0] == "reservations":
                return self.patch(user_id, parts[1], self._json_object(body))
            if method == "POST" and parts == ["reservation-moves"]:
                obj = self._json_object(body)
                key = self._idem_key(headers)
                hit = self._idem_lookup(user_id, "/reservation-moves", key, obj)
                if hit is not None:
                    return hit
                status, out = self.moves(user_id, obj)
                self._idem_store(user_id, "/reservation-moves", key, obj, status, out)
                return status, out
        raise Err(404, "not_found", "no such route")

    # ------------------------------------------------------------------ request plumbing
    @staticmethod
    def _json_object(body: Any) -> dict:
        if isinstance(body, (bytes, bytearray)):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError:
                raise Err(400, "malformed_request", "body is not UTF-8")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                raise Err(400, "malformed_request", "body is not JSON")
        if body is None:
            raise Err(400, "malformed_request", "body missing")
        if not isinstance(body, dict):
            raise Err(400, "malformed_request", "body is not a JSON object")
        return body

    @staticmethod
    def _query(query: str) -> dict[str, str]:
        qs = parse_qs(query, keep_blank_values=True)
        return {k: v[0] for k, v in qs.items()}

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

    def _idem_lookup(self, user_id: str, path: str, key: str, obj: dict):
        rec = self.idem.get(f"{user_id}|{path}|{key}")
        if rec is None:
            return None
        if rec["body"] == obj:
            return 200, json.loads(json.dumps(rec["response"]))
        raise Err(409, "idempotency_key_reuse", "key already used with a different body")

    def _idem_store(self, user_id: str, path: str, key: str, obj: dict, status: int, out: Any) -> None:
        if status == 201:
            self.idem[f"{user_id}|{path}|{key}"] = {
                "body": json.loads(json.dumps(obj)), "status": status,
                "response": json.loads(json.dumps(out)),
            }

    # ------------------------------------------------------------------ reset / fixture
    def reset(self, fx: dict):
        new = Model(self.now)
        new._load_fixture(fx)
        self._adopt(new)
        return 204, None

    def _adopt(self, other: "Model") -> None:
        for attr in ("users", "user_order", "tokens", "restaurants", "restaurant_order",
                     "reservations", "reservation_order", "references", "idem", "counters"):
            setattr(self, attr, getattr(other, attr))

    @staticmethod
    def _str(obj: dict, field: str, required: bool = True, max_len: Optional[int] = None) -> Optional[str]:
        if field not in obj or obj[field] is None:
            if required:
                raise Err(422, "validation_failed", f"{field} is required")
            return None
        v = obj[field]
        if not isinstance(v, str):
            raise Err(400, "malformed_request", f"{field} must be a string")
        if max_len is not None and len(v) > max_len:
            raise Err(422, "validation_failed", f"{field} longer than {max_len}")
        if required and v == "":
            raise Err(422, "validation_failed", f"{field} must not be empty")
        return v

    @staticmethod
    def _list(obj: dict, field: str) -> list:
        v = obj.get(field, [])
        if v is None:
            v = []
        if not isinstance(v, list):
            raise Err(400, "malformed_request", f"{field} must be an array")
        return v

    @staticmethod
    def _obj(v: Any, what: str) -> dict:
        if not isinstance(v, dict):
            raise Err(400, "malformed_request", f"{what} must be an object")
        return v

    @staticmethod
    def _int(obj: dict, field: str, minimum: int) -> int:
        if field not in obj or obj[field] is None:
            raise Err(422, "validation_failed", f"{field} is required")
        v = obj[field]
        if not is_int(v):
            raise Err(400, "malformed_request", f"{field} must be an integer")
        v = int(v)
        if v < minimum:
            raise Err(422, "validation_failed", f"{field} must be >= {minimum}")
        return v

    def _load_fixture(self, fx: dict) -> None:
        for u in self._list(fx, "users"):
            self._obj(u, "user")
            uid = self._str(u, "id", max_len=ID_MAX)
            email = self._str(u, "email")
            if not EMAIL_RE.match(email):
                raise Err(422, "validation_failed", "email must be local@domain")
            pw = self._str(u, "password")
            name = self._str(u, "display_name")
            if uid in self.users or any(x["email"] == email for x in self.users.values()):
                raise Err(422, "validation_failed", "duplicate user")
            self.users[uid] = {"id": uid, "email": email, "password_hash": hash_password(pw),
                               "display_name": name}
            self.user_order.append(uid)
        for r in self._list(fx, "restaurants"):
            self._obj(r, "restaurant")
            rid = self._str(r, "id", max_len=ID_MAX)
            name = self._str(r, "name")
            tz = self._str(r, "timezone")
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                raise Err(422, "validation_failed", "unknown timezone")
            slot = self._int(r, "slot_minutes", 1)
            dur = self._int(r, "reservation_duration_minutes", 1)
            cutoff = self._int(r, "cancellation_cutoff_minutes", 0)
            hours = []
            for h in self._list(r, "opening_hours"):
                self._obj(h, "opening_hours entry")
                wd = self._str(h, "weekday")
                if wd not in WEEKDAYS:
                    raise Err(422, "validation_failed", "weekday must be mon..sun")
                o, c = self._str(h, "opens"), self._str(h, "closes")
                om, cm = hhmm_to_minutes(o), hhmm_to_minutes(c)
                if om is None or cm is None:
                    raise Err(422, "validation_failed", "opens/closes must be HH:MM")
                if cm <= om:
                    raise Err(422, "validation_failed", "closes must be later than opens")
                hours.append({"weekday": wd, "opens": o, "closes": c})
            tables = []
            seen_t: set[str] = set()
            for t in self._list(r, "tables"):
                self._obj(t, "table")
                tid = self._str(t, "id", max_len=ID_MAX)
                label = self._str(t, "label")
                cap = self._int(t, "capacity", 1)
                if tid in seen_t:
                    raise Err(422, "validation_failed", "duplicate table id")
                seen_t.add(tid)
                tables.append({"id": tid, "label": label, "capacity": cap})
            if rid in self.restaurants:
                raise Err(422, "validation_failed", "duplicate restaurant id")
            self.restaurants[rid] = {
                "id": rid, "name": name, "timezone": tz, "slot_minutes": slot,
                "reservation_duration_minutes": dur, "cancellation_cutoff_minutes": cutoff,
                "opening_hours": hours, "tables": tables,
            }
            self.restaurant_order.append(rid)
        for s in self._list(fx, "reservations"):
            self._obj(s, "reservation")
            rid = self._str(s, "id", max_len=ID_MAX)
            ref = self._str(s, "reference")
            if not REFERENCE_RE.match(ref):
                raise Err(422, "validation_failed", "reference must be 6..12 of A-Z0-9")
            uid = self._str(s, "user_id")
            if uid not in self.users:
                raise Err(422, "validation_failed", "unknown user_id")
            restaurant_id = self._str(s, "restaurant_id")
            table_id = self._str(s, "table_id")
            local = self._str(s, "starts_at_local")
            party = self._party_size(s)
            r = self.restaurants.get(restaurant_id)
            if r is None or not any(t["id"] == table_id for t in r["tables"]):
                raise Err(422, "validation_failed", "unknown restaurant/table")
            naive = parse_local(local)
            if naive is None:
                raise Err(422, "validation_failed", "starts_at_local must be YYYY-MM-DDTHH:MM")
            zone = ZoneInfo(r["timezone"])
            start = resolve_local(naive, zone)
            if start is None:
                raise Err(422, "validation_failed", "starts_at_local does not exist")
            if rid in self.reservations or ref in self.references:
                raise Err(422, "validation_failed", "duplicate reservation id/reference")
            self._add_reservation(rid, ref, uid, restaurant_id, table_id, party, local, start,
                                  start + timedelta(minutes=r["reservation_duration_minutes"]),
                                  self.now())

    def _add_reservation(self, rid, ref, uid, restaurant_id, table_id, party, local, start, end, created):
        self.reservations[rid] = {
            "reservation_id": rid, "reference": ref, "user_id": uid, "restaurant_id": restaurant_id,
            "table_id": table_id, "party_size": party, "status": "confirmed",
            "starts_at_local": local, "start": start, "end": end, "created": created,
        }
        self.reservation_order.append(rid)
        self.references.add(ref)

    # ------------------------------------------------------------------ export / import
    def export(self) -> dict:
        state = {
            "users": [self.users[u] for u in self.user_order],
            "tokens": [{"token": t, "user_id": u} for t, u in self.tokens.items()],
            "restaurants": [self.restaurants[r] for r in self.restaurant_order],
            "reservations": [
                {**{k: v for k, v in rec.items() if k not in ("start", "end", "created")},
                 "start": fmt_utc(rec["start"]), "end": fmt_utc(rec["end"]),
                 "created": fmt_utc(rec["created"])}
                for rec in (self.reservations[r] for r in self.reservation_order)
            ],
            "idempotency": [{"key": k, **v} for k, v in self.idem.items()],
            "counters": dict(self.counters),
        }
        return json.loads(json.dumps({"track": TRACK, "format_version": FORMAT_VERSION, "state": state}))

    def import_(self, doc: dict):
        if doc.get("track") != TRACK or doc.get("format_version") != FORMAT_VERSION:
            raise Err(422, "validation_failed", "wrong track or format_version")
        state = doc.get("state")
        if not isinstance(state, dict):
            raise Err(422, "validation_failed", "state must be an object")
        new = Model(self.now)
        try:
            for u in state["users"]:
                new.users[u["id"]] = {"id": u["id"], "email": u["email"],
                                      "password_hash": u["password_hash"], "display_name": u["display_name"]}
                new.user_order.append(u["id"])
            for t in state["tokens"]:
                new.tokens[t["token"]] = t["user_id"]
            for r in state["restaurants"]:
                new.restaurants[r["id"]] = json.loads(json.dumps(r))
                new.restaurant_order.append(r["id"])
            for s in state["reservations"]:
                rec = dict(s)
                rec["start"] = datetime.fromisoformat(s["start"]).astimezone(UTC)
                rec["end"] = datetime.fromisoformat(s["end"]).astimezone(UTC)
                rec["created"] = datetime.fromisoformat(s["created"]).astimezone(UTC)
                new.reservations[rec["reservation_id"]] = rec
                new.reservation_order.append(rec["reservation_id"])
                new.references.add(rec["reference"])
            for i in state["idempotency"]:
                new.idem[i["key"]] = {"body": i["body"], "status": i["status"], "response": i["response"]}
            new.counters = {k: int(state["counters"][k]) for k in ("user", "token", "reservation")}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise Err(422, "validation_failed", "invalid state")
        self._adopt(new)
        return 204, None

    # ------------------------------------------------------------------ auth
    def signup(self, obj: dict):
        email = self._str(obj, "email")
        password = self._str(obj, "password")
        name = self._str(obj, "display_name")
        if not EMAIL_RE.match(email):
            raise Err(422, "validation_failed", "email must be local@domain")
        if len(password) < 8:
            raise Err(422, "validation_failed", "password shorter than 8 characters")
        if any(u["email"] == email for u in self.users.values()):
            raise Err(409, "email_taken", "email already registered")
        uid = self._new_user_id()
        self.users[uid] = {"id": uid, "email": email, "password_hash": hash_password(password),
                           "display_name": name}
        self.user_order.append(uid)
        token = self._new_token()
        self.tokens[token] = uid
        return 201, {"user_id": uid, "display_name": name, "token": token}

    def login(self, obj: dict):
        email = self._str(obj, "email")
        password = self._str(obj, "password")
        user = next((u for u in self.users.values() if u["email"] == email), None)
        if user is None or user["password_hash"] != hash_password(password):
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

    # ------------------------------------------------------------------ availability
    def _slots_for(self, r: dict, day: date) -> list[tuple[str, datetime]]:
        """(starts_at_local, utc_start) for every grid slot of `day`, existing local times only."""
        zone = ZoneInfo(r["timezone"])
        wd = WEEKDAYS[day.weekday()]
        dur = r["reservation_duration_minutes"]
        out: list[tuple[str, datetime]] = []
        seen: set[str] = set()
        for h in r["opening_hours"]:
            if h["weekday"] != wd:
                continue
            opens, closes = hhmm_to_minutes(h["opens"]), hhmm_to_minutes(h["closes"])
            t = opens
            while t + dur <= closes:  # Q8: wall-clock minutes
                naive = datetime(day.year, day.month, day.day) + timedelta(minutes=t)
                if naive.date() == day:
                    local = naive.strftime("%Y-%m-%dT%H:%M")
                    utc = resolve_local(naive, zone)
                    if utc is not None and local not in seen:
                        seen.add(local)
                        out.append((local, utc))
                t += r["slot_minutes"]
        out.sort(key=lambda p: p[0])
        return out

    def availability(self, q: dict[str, str]):
        for name in ("restaurant_id", "date", "party_size"):
            if name not in q or q[name] == "":
                raise Err(422, "validation_failed", f"{name} is required")
        day = parse_date(q["date"])
        if day is None:
            raise Err(422, "validation_failed", "date must be YYYY-MM-DD")
        if not DIGITS_RE.match(q["party_size"]):
            raise Err(422, "validation_failed", "party_size must be plain decimal digits")
        party = int(q["party_size"])
        if party < 1:  # Q9
            raise Err(422, "validation_failed", "party_size must be >= 1")
        r = self.restaurants.get(q["restaurant_id"])
        if r is None:
            raise Err(404, "not_found", "no such restaurant")
        zone = ZoneInfo(r["timezone"])
        dur = timedelta(minutes=r["reservation_duration_minutes"])
        slots = []
        for local, start in self._slots_for(r, day):
            end = start + dur
            ids = [t["id"] for t in r["tables"]
                   if t["capacity"] >= party and not self._table_busy(t["id"], start, end, None)]
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

    # ------------------------------------------------------------------ reservations
    @staticmethod
    def _party_size(obj: dict) -> int:
        if "party_size" not in obj:
            raise Err(422, "validation_failed", "party_size is required")
        v = obj["party_size"]
        if not is_int(v) or int(v) < 1:  # B65/B119: any invalid party_size is 422
            raise Err(422, "validation_failed", "party_size must be an integer >= 1")
        return int(v)

    def _resolve_booking(self, r: dict, table_id: str, local: str, party: int,
                         exclude: Optional[set[str]] = None, check_overlap: bool = True):
        """Validate a (table, time, party) against restaurant rules. Returns (start, end, table)."""
        naive = parse_local(local)
        if naive is None:
            raise Err(422, "validation_failed", "starts_at_local must be bare YYYY-MM-DDTHH:MM")
        table = next((t for t in r["tables"] if t["id"] == table_id), None)
        if table is None:
            raise Err(404, "not_found", "no such table at this restaurant")
        zone = ZoneInfo(r["timezone"])
        start = resolve_local(naive, zone)
        if start is None:  # Q2 order: invalid_local_time first
            raise Err(422, "invalid_local_time", "local time does not exist")
        dur = r["reservation_duration_minutes"]
        minutes = naive.hour * 60 + naive.minute
        wd = WEEKDAYS[naive.date().weekday()]
        windows = [(hhmm_to_minutes(h["opens"]), hhmm_to_minutes(h["closes"]))
                   for h in r["opening_hours"] if h["weekday"] == wd]
        inside = [(o, c) for o, c in windows if o <= minutes and minutes + dur <= c]
        if not inside:
            raise Err(422, "outside_opening_hours", "slot outside opening hours")
        if not any((minutes - o) % r["slot_minutes"] == 0 for o, _ in inside):
            raise Err(422, "not_on_slot_grid", "starts_at_local is not on the slot grid")
        if party > table["capacity"]:  # Q3
            raise Err(422, "party_exceeds_capacity", "party_size exceeds table capacity")
        end = start + timedelta(minutes=dur)
        if check_overlap and self._table_busy(table_id, start, end, exclude):
            raise Err(409, "table_unavailable", "table is taken for an overlapping interval")
        return start, end, table

    def _view(self, rec: dict) -> dict:
        zone = ZoneInfo(self.restaurants[rec["restaurant_id"]]["timezone"])
        return {
            "reservation_id": rec["reservation_id"], "reference": rec["reference"],
            "restaurant_id": rec["restaurant_id"], "table_id": rec["table_id"],
            "party_size": rec["party_size"], "status": rec["status"],
            "starts_at_local": rec["starts_at_local"], "starts_at": fmt(rec["start"], zone),
            "ends_at": fmt(rec["end"], zone), "created_at": fmt_utc(rec["created"]),
        }

    def create_reservation(self, user_id: str, obj: dict):
        restaurant_id = self._str(obj, "restaurant_id")
        table_id = self._str(obj, "table_id")
        local = self._str(obj, "starts_at_local")
        party = self._party_size(obj)
        if parse_local(local) is None:  # field format (422) before resource lookup (404)
            raise Err(422, "validation_failed", "starts_at_local must be bare YYYY-MM-DDTHH:MM")
        r = self.restaurants.get(restaurant_id)
        if r is None:
            raise Err(404, "not_found", "no such restaurant")
        start, end, _ = self._resolve_booking(r, table_id, local, party)
        rid = self._new_reservation_id()
        ref = self._new_reference()
        self._add_reservation(rid, ref, user_id, restaurant_id, table_id, party, local, start, end, self.now())
        return 201, self._view(self.reservations[rid])

    def list_reservations(self, user_id: str):
        mine = [self.reservations[r] for r in self.reservation_order if self.reservations[r]["user_id"] == user_id]
        order = {rid: i for i, rid in enumerate(self.reservation_order)}
        mine.sort(key=lambda rec: (rec["start"], order[rec["reservation_id"]]), reverse=True)  # Q7 tie order
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
        return self.now() >= rec["start"] - timedelta(minutes=cutoff)

    def cancel(self, user_id: str, reference: str):
        rec = self._mine(user_id, reference)
        if rec["status"] == "cancelled":  # Q4: status before cutoff
            return 200, self._view(rec)
        if self._cutoff_passed(rec):
            raise Err(409, "cutoff_passed", "within the cancellation cutoff")
        rec["status"] = "cancelled"
        return 200, self._view(rec)

    def _check_types(self, obj: dict) -> None:
        for f in ("table_id", "starts_at_local"):
            if f in obj and not isinstance(obj[f], str):
                raise Err(400, "malformed_request", f"{f} must be a string")

    def _amend_values(self, rec: dict, obj: dict) -> tuple[str, str, int]:
        table_id = obj["table_id"] if "table_id" in obj else rec["table_id"]
        local = obj["starts_at_local"] if "starts_at_local" in obj else rec["starts_at_local"]
        party = self._party_size(obj) if "party_size" in obj else rec["party_size"]
        if table_id == "":
            raise Err(422, "validation_failed", "table_id must not be empty")
        return table_id, local, party

    def patch(self, user_id: str, reference: str, obj: dict):
        self._check_types(obj)
        rec = self._mine(user_id, reference)
        if rec["status"] == "cancelled":  # Q4
            raise Err(409, "reservation_cancelled", "reservation is cancelled")
        if self._cutoff_passed(rec):
            raise Err(409, "cutoff_passed", "within the amendment cutoff")
        table_id, local, party = self._amend_values(rec, obj)
        r = self.restaurants[rec["restaurant_id"]]
        start, end, _ = self._resolve_booking(r, table_id, local, party, exclude={rec["reservation_id"]})
        rec.update({"table_id": table_id, "starts_at_local": local, "party_size": party, "start": start, "end": end})
        return 200, self._view(rec)

    # ------------------------------------------------------------------ atomic moves
    def moves(self, user_id: str, obj: dict):
        moves = obj.get("moves")
        if not isinstance(moves, list) or not 1 <= len(moves) <= 8:  # Q5: shape errors are 422
            raise Err(422, "validation_failed", "moves must contain 1..8 objects")
        refs: list[str] = []
        for m in moves:
            if not isinstance(m, dict) or not isinstance(m.get("reference"), str) or m["reference"] == "":
                raise Err(422, "validation_failed", "each move needs a string reference")
            if m["reference"] in refs:
                raise Err(422, "validation_failed", "duplicate reference")
            refs.append(m["reference"])
        for m in moves:
            self._check_types(m)
        resolved = []  # (rec, table_id, local, party, start, end)
        first_restaurant: Optional[str] = None
        for m in moves:
            rec = self._mine(user_id, m["reference"])
            if first_restaurant is None:
                first_restaurant = rec["restaurant_id"]
            elif rec["restaurant_id"] != first_restaurant:
                raise Err(422, "validation_failed", "bookings belong to different restaurants")
            if rec["status"] == "cancelled":  # Q4
                raise Err(409, "reservation_cancelled", "reservation is cancelled")
            if self._cutoff_passed(rec):
                raise Err(409, "cutoff_passed", "within the amendment cutoff")
            table_id, local, party = self._amend_values(rec, m)
            r = self.restaurants[rec["restaurant_id"]]
            start, end, _ = self._resolve_booking(r, table_id, local, party, check_overlap=False)
            resolved.append((rec, table_id, local, party, start, end))
        listed = {rec["reservation_id"] for rec, *_ in resolved}
        for i, (rec, table_id, _, _, start, end) in enumerate(resolved):
            if self._table_busy(table_id, start, end, exclude=listed):
                raise Err(409, "table_unavailable", "overlap with an unlisted booking")
            for j, (rec2, table2, _, _, start2, end2) in enumerate(resolved):
                if i != j and table_id == table2 and start < end2 and start2 < end:
                    raise Err(409, "table_unavailable", "overlap among listed bookings")
        for rec, table_id, local, party, start, end in resolved:
            rec.update({"table_id": table_id, "starts_at_local": local, "party_size": party,
                        "start": start, "end": end})
        return 201, {"reservations": [self._view(rec) for rec, *_ in resolved]}
