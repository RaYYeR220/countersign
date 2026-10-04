"""Differential runner: seeded random operation sequences against the reference model and the service.

    python diff_runner.py --base-url http://127.0.0.1:18401 --seed 1 --runs 50 --ops 60
    python diff_runner.py --base-url http://127.0.0.1:18401 --replay .bg/diff-fail-seed7.json

Every response (status + body) is compared after normalisation: implementation-chosen values (tokens,
user ids, reservation ids, references, created_at, export state) are replaced by labels assigned by the
operation that first produced them, error messages are masked, and ties in GET /reservations ordering
are made stable. A mismatch is shrunk greedily to the shortest sequence that still mismatches, written
to a JSON file, and printed with the expected (model) and actual (service) responses.

Exit code 0 when every run matches, 1 on a mismatch, 2 on a usage/transport error.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import sys
import time
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client import Client  # noqa: E402
from fixtures import (BERLIN_FALL, BERLIN_SPRING, FUT_DAY, FUT_DAY2, FUT_FRI, FUT_THU, FUT_WED, NY_FALL,  # noqa: E402
                      NY_SPRING, PAST_DAY, PAST_THU, base_fixture, other_fixture)
from model import Model  # noqa: E402

RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")

DATES = [FUT_THU, FUT_FRI, FUT_WED, FUT_DAY, FUT_DAY2, PAST_DAY, PAST_THU,
         BERLIN_SPRING, BERLIN_FALL, NY_SPRING, NY_FALL]
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
RESTAURANTS = {"r_anker": ["t_1", "t_2"], "r_all": ["a_1", "a_2", "a_3"], "r_ny": ["n_1"]}
FIXTURE_USERS = [("ada@example.com", "correct horse"), ("bob@example.com", "bob secret 1")]
CAPS = {t["id"]: t["capacity"] for r_ in base_fixture()["restaurants"] for t in r_["tables"]}


# ============================================================ symbolic values
class Sym:
    """A placeholder resolved per target: ("token", "t3"), ("ref", "b2"), ("export", 1) ..."""

    def __init__(self, kind: str, label: str):
        self.kind, self.label = kind, label

    def __repr__(self):
        return f"<{self.kind}:{self.label}>"

    def to_json(self):
        return {"$sym": [self.kind, self.label]}


def sym_from_json(v):
    if isinstance(v, dict) and "$sym" in v:
        return Sym(*v["$sym"])
    if isinstance(v, dict):
        return {k: sym_from_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [sym_from_json(x) for x in v]
    return v


def sym_to_json(v):
    if isinstance(v, Sym):
        return v.to_json()
    if isinstance(v, dict):
        return {k: sym_to_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [sym_to_json(x) for x in v]
    return v


# ============================================================ targets
class Target:
    """One side of the comparison. Keeps the alias maps (label <-> concrete value)."""

    def __init__(self, name: str):
        self.name = name
        self.values: dict[tuple[str, str], Any] = {}   # (kind, label) -> concrete
        self.labels: dict[tuple[str, Any], str] = {}   # (kind, concrete) -> label

    def bind(self, kind: str, label: str, value: Any) -> None:
        # The newest binding wins on both maps: after an import (or reset) an implementation may hand out an
        # id it used before, and both targets see the same op order, so overwriting stays symmetric.
        self.values[(kind, label)] = value
        self.labels[(kind, json.dumps(value, sort_keys=True))] = label

    def resolve(self, v):
        if isinstance(v, Sym):
            return self.values.get((v.kind, v.label), f"MISSING-{v.kind}-{v.label}")
        if isinstance(v, dict):
            return {k: self.resolve(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.resolve(x) for x in v]
        return v

    def label_of(self, kind: str, value: Any) -> Optional[str]:
        return self.labels.get((kind, json.dumps(value, sort_keys=True)))

    def raw_call(self, method, path, query, headers, body) -> tuple[int, Any]:
        raise NotImplementedError


class ModelTarget(Target):
    def __init__(self):
        super().__init__("model")
        self.model = Model()

    def raw_call(self, method, path, query, headers, body):
        return self.model.handle(method, path, query, headers, body)


_CLIENTS: dict[str, Client] = {}


class HttpTarget(Target):
    def __init__(self, base_url: str):
        super().__init__("service")
        # one kept-alive client per base URL for the whole process: shrinking runs hundreds of sequences and a
        # fresh connection per sequence exhausts the host's ephemeral ports
        self.client = _CLIENTS.setdefault(base_url, Client(base_url, timeout=10, fail_on_5xx=False))

    def raw_call(self, method, path, query, headers, body):
        full = path + ("?" + query if query else "")
        raw = body.encode("utf-8") if isinstance(body, str) else (json.dumps(body).encode() if body is not None else None)
        r = self.client.request(method, full, raw=raw, headers=headers)
        return r.status, (r.json if r.raw_json_ok else {"$unparseable": r.text[:200]})


# ============================================================ operations
def gen_sequence(rng: random.Random, n_ops: int) -> list[dict]:
    """A list of ops. Each op is a dict with "op" and parameters; Sym values are resolved per target.

    The generator is model-guided: it executes every op against a private model instance as it goes, so it
    knows which tokens are valid, which bookings exist and are confirmed, and can bias amendments towards
    values that are valid for the booking's restaurant. The sequence itself stays a plain data structure.
    """
    probe = ModelTarget()
    ops: list[dict] = []
    users: list[str] = []          # token labels that are valid right now
    bookings: list[str] = []       # booking labels that exist right now (confirmed or cancelled)
    owner: dict[str, str] = {}     # booking label -> token label of the creator
    keys: list[tuple[str, str, dict]] = []   # (token label, key, body) used on /reservations
    mkeys: list[tuple[str, str, dict]] = []  # same for /reservation-moves
    exports = 0
    n_sign = 0

    def emit(op: dict) -> tuple[int, Any]:
        ops.append(op)
        return execute(op, probe)

    def tok() -> Sym:
        if users and rng.random() > 0.05:
            return Sym("token", rng.choice(users))
        return Sym("token", "none")

    def record(label: str) -> Optional[dict]:
        ref = probe.values.get(("ref", label))
        return next((r for r in probe.model.reservations.values() if r["reference"] == ref), None)

    def some_ref(for_token: Sym, prefer_confirmed: bool = True):
        r = rng.random()
        mine = [b for b in bookings if owner.get(b) == for_token.label]
        if prefer_confirmed and rng.random() < 0.7:
            live = [b for b in mine if (record(b) or {}).get("status") == "confirmed"]
            mine = live or mine
        if mine and r < 0.8:
            return Sym("ref", rng.choice(mine))
        if bookings and r < 0.9:
            return Sym("ref", rng.choice(bookings))
        if r < 0.96:
            return "SEED01"
        return rng.choice(["NOPE01", "", "zzz"])

    def local_time(restaurant: str) -> str:
        r = rng.random()
        if restaurant == "r_anker":
            if r < 0.25:   # DST nights, restaurant open 00:00-06:00 on Sundays
                return rng.choice([BERLIN_SPRING, BERLIN_FALL]) + "T" + rng.choice(
                    ["00:00", "01:00", "01:30", "02:00", "02:30", "03:00", "03:30", "04:30", "05:00"])
            dates = [FUT_THU, FUT_THU, FUT_FRI, FUT_FRI, PAST_THU, FUT_WED]
            times = ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30", "22:00", "17:30",
                     "18:15", "23:00"]
        elif restaurant == "r_ny":
            if r < 0.3:
                return rng.choice([NY_SPRING, NY_FALL]) + "T" + rng.choice(
                    ["00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "03:00", "04:30"])
            dates = [FUT_THU, FUT_DAY, FUT_DAY2, PAST_DAY]
            times = ["18:00", "18:30", "19:00", "20:00", "21:00", "21:30", "22:00", "19:15"]
        else:
            dates = [FUT_DAY, FUT_DAY, FUT_DAY2, PAST_DAY, FUT_THU]
            times = ["10:00", "10:15", "11:00", "12:00", "12:30", "13:00", "15:30", "18:00", "20:45", "21:00",
                     "12:07", "21:15", "22:00", "09:45"]
        if r < 0.02:
            return rng.choice(["2027-06-15T12:00:00+02:00", "2027-06-15 12:00", "2027-06-15", "garbage", ""])
        if r < 0.04:
            return rng.choice(DATES) + "T" + rng.choice(TIMES)
        return rng.choice(dates) + "T" + rng.choice(times)

    def party(cap: Optional[int] = None):
        r = rng.random()
        if r < 0.05:
            return rng.choice([0, -1, "4", True, 2.5, None])
        if cap is not None and r < 0.8:
            return rng.randint(1, cap)
        return rng.choice([1, 2, 2, 3, 4, 4, 5, 6, 7])

    def table(restaurant: str) -> str:
        if rng.random() < 0.04:
            return rng.choice(["nope", "t_1", "a_1", "n_1"])
        return rng.choice(RESTAURANTS[restaurant])

    def amendment(ref, n_fields_p: float = 0.5) -> dict:
        """Fields for a PATCH/move item, biased to the booking's own restaurant when it is known."""
        rec = record(ref.label) if isinstance(ref, Sym) else None
        rest = rec["restaurant_id"] if rec and rec["restaurant_id"] in RESTAURANTS else rng.choice(list(RESTAURANTS))
        body: dict = {}
        for f in ("table_id", "starts_at_local", "party_size"):
            if rng.random() < n_fields_p:
                if f == "table_id":
                    body[f] = table(rest)
                elif f == "starts_at_local":
                    body[f] = local_time(rest)
                else:
                    tid = body.get("table_id", rec["table_id"] if rec else None)
                    body[f] = party(CAPS.get(tid))
        return body

    def booking_body(restaurant: Optional[str] = None) -> dict:
        restaurant = restaurant or rng.choice(list(RESTAURANTS))
        tid = table(restaurant)
        body = {"restaurant_id": restaurant, "table_id": tid,
                "starts_at_local": local_time(restaurant), "party_size": party(CAPS.get(tid))}
        r = rng.random()
        if r < 0.03:
            body.pop(rng.choice(list(body)))
        elif r < 0.05:
            body["restaurant_id"] = "nope"
        elif r < 0.07:
            body["extra"] = {"ignored": True}
        return body

    emit({"op": "reset", "fixture": "base"})
    for i in range(n_ops):
        r = rng.random()
        if r < 0.06 or not users:
            if rng.random() < 0.5 and len(users) < 6:
                n_sign += 1
                label = f"s{n_sign}"
                email = f"user{n_sign}@example.com" if rng.random() > 0.08 else rng.choice(["ada@example.com", "bad", ""])
                pw = "password-%d" % n_sign if rng.random() > 0.08 else "short"
                st, _ = emit({"op": "signup", "label": label, "email": email, "password": pw,
                              "display_name": f"User {n_sign}"})
                if st == 201:
                    users.append(label)
            else:
                n_sign += 1
                label = f"l{n_sign}"
                email, pw = rng.choice(FIXTURE_USERS)
                if rng.random() < 0.1:
                    pw = "wrong password"
                st, _ = emit({"op": "login", "label": label, "email": email, "password": pw})
                if st == 200:
                    users.append(label)
        elif r < 0.13:
            rest = rng.choice(list(RESTAURANTS) + ["nope"])
            ps = rng.choice(["1", "2", "3", "4", "5", "7", "0", "-1", "4.0", "1e9", "+4", "", "x"])
            emit({"op": "availability", "restaurant_id": rest, "date": rng.choice(DATES + ["2027-02-30", "bad"]),
                  "party_size": ps, "extra": "&foo=1" if rng.random() < 0.1 else ""})
        elif r < 0.40:
            t = tok()
            body = booking_body()
            key = f"k{i}"
            mode = rng.random()
            if keys and mode < 0.12:   # replay same body
                tl, key, body = rng.choice(keys)
                t = Sym("token", tl)
            elif keys and mode < 0.20:   # same key, different body
                tl, key, _ = rng.choice(keys)
                t = Sym("token", tl)
            elif keys and mode < 0.23:   # same key, other user
                _, key, body = rng.choice(keys)
            elif mode < 0.25:
                key = ""
            elif mode < 0.27:
                key = "K" * rng.choice([255, 256])
            label = f"b{i}"
            st, _ = emit({"op": "create", "label": label, "token": t, "key": key, "body": body,
                          "raw": rng.random() < 0.02})
            keys.append((t.label, key, copy.deepcopy(body)))
            if st == 201:
                bookings.append(label)
                owner[label] = t.label
        elif r < 0.47:
            t = tok()
            emit({"op": "get", "token": t, "reference": some_ref(t, prefer_confirmed=False)})
        elif r < 0.53:
            emit({"op": "list", "token": tok()})
        elif r < 0.62:
            t = tok()
            emit({"op": "cancel", "token": t, "reference": some_ref(t)})
        elif r < 0.77:
            t = tok()
            ref = some_ref(t)
            body = amendment(ref)
            if rng.random() < 0.03:
                body["table_id"] = 5
            emit({"op": "patch", "token": t, "reference": ref, "body": body})
        elif r < 0.88:
            t = tok()
            n = rng.choice([0, 9, 1, 2, 3]) if rng.random() < 0.1 else rng.choice([1, 2, 2, 3])
            moves = []
            for _ in range(n):
                ref = some_ref(t)
                m: dict = {"reference": ref, **amendment(ref, 0.4)}
                moves.append(m)
            if moves and rng.random() < 0.05:
                moves.append(dict(moves[0]))
            key = f"m{i}"
            body: Any = {"moves": moves}
            mode = rng.random()
            if mkeys and mode < 0.15:
                tl, key, body = rng.choice(mkeys)
                t = Sym("token", tl)
            elif mkeys and mode < 0.22:
                tl, key, _ = rng.choice(mkeys)
                t = Sym("token", tl)
            elif mode < 0.24:
                key = ""
            elif mode < 0.26:
                body = {"moves": "nope"}
            emit({"op": "moves", "token": t, "key": key, "body": body})
            mkeys.append((t.label, key, copy.deepcopy(body)))
        elif r < 0.93:
            exports += 1
            emit({"op": "export", "label": f"e{exports}"})
        elif r < 0.97 and exports:
            emit({"op": "import", "label": f"e{rng.randint(1, exports)}"})
            # after an import the live set is whatever the export held: recompute from the probe
            users = [lab for (kind, lab), v in probe.values.items() if kind == "token" and v in probe.model.tokens]
            bookings = [lab for (kind, lab), v in probe.values.items() if kind == "ref" and v in probe.model.references]
        elif r < 0.985:
            emit({"op": "import_bad", "doc": rng.choice([
                {}, {"track": "tablekeeper", "format_version": 2, "state": {}},
                {"track": "x", "format_version": 1, "state": {}}, "[]", "{bad"])})
        else:
            emit({"op": "reset", "fixture": rng.choice(["base", "other", "base"])})
            users, bookings, keys, mkeys, owner = [], [], [], [], {}
    return ops


# ============================================================ execution
def execute(op: dict, t: Target) -> tuple[int, Any]:
    kind = op["op"]
    hdr: dict = {}
    if kind == "reset":
        fx = base_fixture() if op["fixture"] == "base" else other_fixture()
        return t.raw_call("POST", "/_test/reset", "", hdr, fx)
    if kind == "signup":
        st, body = t.raw_call("POST", "/auth/signup", "", hdr,
                              {"email": op["email"], "password": op["password"], "display_name": op["display_name"]})
        if st == 201 and isinstance(body, dict):
            t.bind("token", op["label"], body.get("token"))
            t.bind("user", op["label"], body.get("user_id"))
        return st, body
    if kind == "login":
        st, body = t.raw_call("POST", "/auth/login", "", hdr, {"email": op["email"], "password": op["password"]})
        if st == 200 and isinstance(body, dict):
            t.bind("token", op["label"], body.get("token"))
        return st, body
    if kind == "availability":
        q = f"restaurant_id={op['restaurant_id']}&date={op['date']}&party_size={op['party_size']}{op['extra']}"
        return t.raw_call("GET", "/availability", q, hdr, None)
    tokv = t.resolve(op.get("token"))
    if isinstance(tokv, str) and not tokv.startswith("MISSING"):
        hdr["Authorization"] = f"Bearer {tokv}"
    if kind == "create":
        if op["key"] != "":
            hdr["Idempotency-Key"] = op["key"]
        body = t.resolve(op["body"])
        if op.get("raw"):
            body = json.dumps(body) + "  "   # whitespace variant, still the same JSON value
        st, out = t.raw_call("POST", "/reservations", "", hdr, body)
        if st == 201 and isinstance(out, dict):
            t.bind("ref", op["label"], out.get("reference"))
            t.bind("rid", op["label"], out.get("reservation_id"))
            t.bind("created", op["label"], out.get("created_at"))
        return st, out
    if kind == "get":
        return t.raw_call("GET", f"/reservations/{t.resolve(op['reference'])}", "", hdr, None)
    if kind == "list":
        return t.raw_call("GET", "/reservations", "", hdr, None)
    if kind == "cancel":
        return t.raw_call("POST", f"/reservations/{t.resolve(op['reference'])}/cancel", "", hdr, None)
    if kind == "patch":
        return t.raw_call("PATCH", f"/reservations/{t.resolve(op['reference'])}", "", hdr, t.resolve(op["body"]))
    if kind == "moves":
        if op["key"] != "":
            hdr["Idempotency-Key"] = op["key"]
        return t.raw_call("POST", "/reservation-moves", "", hdr, t.resolve(op["body"]))
    if kind == "export":
        st, out = t.raw_call("GET", "/_test/export", "", {}, None)
        if st == 200:
            t.bind("export", op["label"], out)
        return st, out
    if kind == "import":
        doc = t.values.get(("export", op["label"]))
        if doc is None:
            return t.raw_call("POST", "/_test/import", "", {}, {"track": "tablekeeper", "format_version": 1, "state": "missing"})
        return t.raw_call("POST", "/_test/import", "", {}, doc)
    if kind == "import_bad":
        return t.raw_call("POST", "/_test/import", "", {}, op["doc"])
    raise ValueError(kind)


# ============================================================ normalisation
def normalise(status: int, body: Any, t: Target, op: dict) -> Any:
    def res(o: dict) -> dict:
        o = dict(o)
        rid_label = t.label_of("rid", o.get("reservation_id"))
        for field, kind in (("reference", "ref"), ("reservation_id", "rid")):
            lab = t.label_of(kind, o.get(field))
            if lab is not None:
                o[field] = f"<{kind}:{lab}>"
        v = o.get("created_at")
        if rid_label is not None:
            # created_at is aliased by the booking it belongs to (never by value: two bookings created in the
            # same second share a value). It must equal what the creating response returned.
            if v == t.values.get(("created", rid_label)):
                o["created_at"] = f"<created:{rid_label}>"
        elif o.get("reservation_id") == "res_seed" and isinstance(v, str) and RFC3339.match(v):
            o["created_at"] = "<created:seeded>"   # implementation-chosen for seeded rows; format only
        return o

    if isinstance(body, dict) and "error" in body and isinstance(body["error"], dict):
        return {"status": status, "error": {"code": body["error"].get("code")}}
    if op["op"] in ("signup", "login") and isinstance(body, dict):
        b = dict(body)
        if "token" in b:
            b["token"] = "<token>" if isinstance(b["token"], str) and b["token"] else b["token"]
        lab = t.label_of("user", b.get("user_id"))
        if lab:
            b["user_id"] = f"<user:{lab}>"
        return {"status": status, "body": b}
    if op["op"] == "export" and isinstance(body, dict):
        return {"status": status, "body": {k: ("<state>" if k == "state" and isinstance(v, dict) else v) for k, v in body.items()}}
    if isinstance(body, dict) and isinstance(body.get("reservations"), list):
        lst = [res(x) if isinstance(x, dict) else x for x in body["reservations"]]
        if op["op"] == "list":
            # order is starts_at descending; ties are unspecified (Q7): make them stable
            keyed = [(x.get("starts_at", ""), str(x.get("reference"))) for x in lst if isinstance(x, dict)]
            starts = [k_[0] for k_ in keyed]
            ok = all(starts[i] >= starts[i + 1] for i in range(len(starts) - 1)) if all(RFC3339.match(s) for s in starts) else True
            from datetime import datetime
            try:
                inst = [datetime.fromisoformat(s) for s in starts]
                ok = all(inst[i] >= inst[i + 1] for i in range(len(inst) - 1))
                lst = [x for _, x in sorted(zip(inst, lst), key=lambda p: (p[0], str(p[1].get("reference"))), reverse=True)]
            except ValueError:
                ok = False
            return {"status": status, "ordered": ok, "reservations": lst}
        return {"status": status, "reservations": lst}
    if isinstance(body, dict) and "reservation_id" in body:
        return {"status": status, "body": res(body)}
    return {"status": status, "body": body}


# ============================================================ runner
def run_sequence(ops: list[dict], base_url: str, stop_at_first: bool = True):
    """Returns (mismatch_index or None, details)."""
    m, s = ModelTarget(), HttpTarget(base_url)
    for i, op in enumerate(ops):
        em = execute(op, m)
        es = execute(op, s)
        nm = normalise(em[0], em[1], m, op)
        ns = normalise(es[0], es[1], s, op)
        if nm != ns:
            return i, {"op": sym_to_json(op), "expected": nm, "actual": ns, "raw_actual": es}
    return None, None


def shrink(ops: list[dict], base_url: str) -> list[dict]:
    """Greedy delta: drop one op at a time (never the leading reset) while a mismatch still occurs."""
    current = list(ops)
    changed = True
    while changed:
        changed = False
        i = 1
        while i < len(current):
            candidate = current[:i] + current[i + 1:]
            idx, _ = run_sequence(candidate, base_url)
            if idx is not None:
                current = candidate[: idx + 1]
                changed = True
            else:
                i += 1
    return current


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("ORACLE_BASE_URL", "http://127.0.0.1:18400"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--ops", type=int, default=60)
    ap.add_argument("--replay", help="JSON file written by a previous failing run")
    ap.add_argument("--out-dir", default=os.path.join(os.getcwd(), ".bg"))
    ap.add_argument("--no-shrink", action="store_true")
    a = ap.parse_args()
    base = a.base_url.rstrip("/")
    try:
        Client(base).get("/health")
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach {base}: {e}")
        return 2
    if a.replay:
        with open(a.replay, encoding="utf-8") as f:
            doc = json.load(f)
        ops = sym_from_json(doc["ops"] if isinstance(doc, dict) else doc)
        idx, details = run_sequence(ops, base)
        if idx is None:
            print(f"replay of {a.replay}: {len(ops)} ops, no mismatch")
            return 0
        print(json.dumps(details, indent=1)[:4000])
        return 1
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    total_ops = 0
    for run in range(a.runs):
        seed = a.seed + run
        ops = gen_sequence(random.Random(seed), a.ops)
        total_ops += len(ops)
        idx, details = run_sequence(ops, base)
        if idx is None:
            continue
        print(f"MISMATCH seed={seed} at op {idx}/{len(ops)}")
        print("first detection, expected (model):", json.dumps(details["expected"])[:1200])
        print("first detection, actual (service):", json.dumps(details["actual"])[:1200])
        failing = ops[: idx + 1]
        if not a.no_shrink:
            shrunk = shrink(failing, base)
            idx2, details2 = run_sequence(shrunk, base)
            if idx2 is None:
                print("shrunk sequence does not replay; re-checking the unshrunk prefix")
                idx2, details2 = run_sequence(failing, base)
                if idx2 is None:
                    print("TRANSIENT: the mismatch did not reproduce on replay (timing, concurrency or a clock-"
                          "dependent rule). Reporting the first detection; sequence saved unshrunk.")
                else:
                    failing, idx, details = failing[: idx2 + 1], idx2, details2
            else:
                failing, idx, details = shrunk, idx2, details2
        out = os.path.join(a.out_dir, f"diff-fail-seed{seed}.json")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"seed": seed, "base_url": base, "ops": sym_to_json(failing)}, f, indent=1)
        print(f"shrunk to {len(failing)} ops -> {out}")
        print("sequence:")
        for j, op in enumerate(failing):
            print(f"  {j}: {json.dumps(sym_to_json(op))[:300]}")
        print("mismatch at op", idx)
        print("expected (model):", json.dumps(details["expected"])[:1500])
        print("actual (service):", json.dumps(details["actual"])[:1500])
        print(f"reproduce: python {os.path.relpath(__file__)} --base-url {base} --replay {out}")
        return 1
    print(f"OK: {a.runs} runs, {total_ops} ops, seeds {a.seed}..{a.seed + a.runs - 1}, {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
