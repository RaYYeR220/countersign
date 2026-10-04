"""Stage-1 hardening tests (Oracle), from the Auditor's surviving mutants on candidate 1.

O-1  C1.89 / R-11   tie order of GET /reservations
O-2  C1.107, C1.109 / R-24   tampered exports are refused without touching the destination
O-3  C1.107, C1.18 / R-8     round trip of edge values
O-4  C1.3, C1.46            reads stay consistent during a write burst
"""
from __future__ import annotations

import copy
import random
import time
import uuid
from datetime import datetime, timedelta

import pytest

from client import Client, burst
from fixtures import ALL_DAYS, FUT_DAY, FUT_FRI, FUT_THU, base_fixture, other_fixture
from test_acceptance import book_all, check_reservation_shape, err, k

SLOT_RE_TABLES = {"a_1", "a_2", "a_3"}


def _snapshot(c: Client, tokens: list[str]) -> dict:
    return {
        "restaurants": [c.get(f"/restaurants/{x['id']}").json for x in c.get("/restaurants").json["restaurants"]],
        "lists": [c.get("/reservations", token=t).json for t in tokens],
        "avail": [c.availability(r, d, 1).json for r in ("r_anker", "r_all") for d in (FUT_THU, FUT_DAY)],
    }


# ============================================================ O-1
def test_C1_89_tie_order_seeded_created_at(c):
    """Same starts_at, different tables, created 1 s apart; the later-created one has the smaller reference."""
    fx = base_fixture()
    fx["reservations"] = [
        {"id": "res_z", "reference": "ZZZZZ1", "user_id": "u_ada", "restaurant_id": "r_all", "table_id": "a_1",
         "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 1, "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": "res_a", "reference": "AAAAA1", "user_id": "u_ada", "restaurant_id": "r_all", "table_id": "a_2",
         "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 1, "created_at": "2026-01-01T00:00:01+00:00"},
        {"id": "res_m", "reference": "MMMMM1", "user_id": "u_ada", "restaurant_id": "r_all", "table_id": "a_3",
         "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 1, "created_at": "2026-01-01T00:00:00+00:00"},
    ]
    assert c.reset(fx).status == 204
    tok = c.login("ada@example.com", "correct horse")
    lst = c.get("/reservations", token=tok).json["reservations"]
    assert [x["reference"] for x in lst] == ["MMMMM1", "ZZZZZ1", "AAAAA1"], lst
    assert lst[0]["created_at"] == lst[1]["created_at"] == "2026-01-01T00:00:00+00:00"
    assert lst[2]["created_at"] == "2026-01-01T00:00:01+00:00"


def test_C1_89_tie_order_live_created_at(c, ada):
    """Live bookings at one instant on different tables, created ≥ 1 s apart: order is created_at ascending
    whatever the references are; ties on created_at fall back to reference ascending."""
    made = []
    for table in ("a_1", "a_2", "a_3"):
        made.append(book_all(c, ada, "r_all", table, f"{FUT_DAY}T15:00", 1))
        time.sleep(1.1)
    for table in ("t_1", "t_2"):
        made.append(book_all(c, ada, "r_anker", table, f"{FUT_FRI}T19:00", 1))
        time.sleep(1.1)
    created = [x["created_at"] for x in made]
    assert created == sorted(created) and len(set(created)) == len(created), created
    lst = c.get("/reservations", token=ada).json["reservations"]
    berlin = [x for x in lst if x["starts_at"] == f"{FUT_FRI}T19:00:00+02:00"]
    assert [x["reference"] for x in berlin] == [made[3]["reference"], made[4]["reference"]]
    noon = [x for x in lst if x["starts_at"] == f"{FUT_DAY}T15:00:00+02:00"]
    assert [x["reference"] for x in noon] == [x["reference"] for x in made[:3]]
    assert lst[:2] == berlin and lst[2:] == noon
    # R-27: only pairs with equal starts_at are ordered by created_at; across instants starts_at descending wins.
    # When an implementation's references are not monotonic, such a pair proves created_at beats the reference.
    inversions = [(a, b) for a, b in zip(made, made[1:])
                  if a["starts_at"] == b["starts_at"] and b["reference"] < a["reference"]]
    for a, b in inversions:
        assert lst.index(a) < lst.index(b), (a["reference"], b["reference"])


# ============================================================ O-2
def _walk(obj, parent=None, key=None):
    yield parent, key, obj
    if isinstance(obj, dict):
        for k_, v in list(obj.items()):
            yield from _walk(v, obj, k_)
    elif isinstance(obj, list):
        for i, v in enumerate(list(obj)):
            yield from _walk(v, obj, i)


def _records(state, **match):
    """Every dict anywhere in the state with all the given key/value pairs, with its parent container."""
    out = []
    for parent, key, v in _walk(state):
        if isinstance(v, dict) and all(v.get(a) == b for a, b in match.items()):
            out.append((parent, key, v))
    return out


def _top_key_of(state: dict, needle: dict) -> str:
    for key, coll in state.items():
        if any(v is needle for _, _, v in _walk(coll)):
            return key
    raise AssertionError("record not under a top-level key")


OWNER_KEYS = ("user_id", "owner", "owner_id", "user", "diner_id", "account_id", "customer_id")


def _reservation_records(state, **match):
    """The reservation records (not copies of them inside stored responses) matching the key/value pairs.

    A stored idempotency receipt carries the original 201 body, which has the same reference as the record; the
    response shape never carries an owner, so dicts with an owner key are the records. Falls back to every match
    when the implementation names the owner differently, so a tamper is still applied somewhere meaningful.
    """
    found = _records(state, **match)
    owned = [x for x in found if any(k_ in x[2] for k_ in OWNER_KEYS)]
    return owned or found


def _top_key_with_value(state: dict, value):
    """Top-level key of the collection that holds `value` anywhere inside it, or None."""
    for key, coll in state.items():
        if any(v == value or (isinstance(v, str) and isinstance(value, str) and value in v) for _, _, v in _walk(coll)):
            return key   # substring match too: a receipt may store the key inside a composite string
    return None


def _tampers(exp: dict, second_ref: str, idem_key: str = None, token: str = None):
    """Yield (name, tampered export) pairs. Each is built from a deep copy of the real export.

    Every tamper is applied to *all* reservation records that match, so an implementation that stores a record
    in more than one place still ends up with an inconsistent state.
    """
    state0 = exp["state"]
    assert _reservation_records(state0, reference="SEED01") and _reservation_records(state0, reference=second_ref) \
        and _records(state0, id="r_anker") and _records(state0, email="ada@example.com"), \
        "could not locate the seeded reservation, a restaurant or a user in the export"

    def fresh():
        e = copy.deepcopy(exp)
        s = e["state"]
        return (e, _reservation_records(s, reference="SEED01"), _reservation_records(s, reference=second_ref),
                _records(s, id="r_anker")[0][2], _records(s, email="ada@example.com")[0][2])

    # duplicate reservation record (same id and reference), appended beside the record itself
    e, seeds, _, _, _ = fresh()
    p, key, rec = seeds[0]
    if isinstance(p, list):
        p.append(copy.deepcopy(rec))
    else:
        p[str(key) + "_dup"] = copy.deepcopy(rec)
    yield "duplicate reservation record", e
    # duplicate reference on a different record
    e, _, others, _, _ = fresh()
    for _, _, rec2 in others:
        rec2["reference"] = "SEED01"
    yield "duplicate reference", e
    # duplicate id of a different record, where the id is a field of the record
    e, seeds, others, _, _ = fresh()
    rec = seeds[0][2]
    for idf in ("reservation_id", "id"):
        if idf in rec and all(idf in r2 for _, _, r2 in others):
            for _, _, rec2 in others:
                rec2[idf] = rec[idf]
            yield "duplicate reservation id", e
            break
    # missing collections
    for which, locate in (("users", lambda s: _records(s, email="ada@example.com")[0][2]),
                          ("restaurants", lambda s: _records(s, id="r_anker")[0][2]),
                          ("reservations", lambda s: _reservation_records(s, reference="SEED01")[0][2])):
        e = copy.deepcopy(exp)
        del e["state"][_top_key_of(e["state"], locate(e["state"]))]
        yield f"missing {which} collection", e
    # O-8: only the receipts / only the tokens collection removed, located by a value each must contain; skipped when
    # the layout has no separate collection for it (for example tokens nested under users, or hashed tokens)
    main_keys = {_top_key_of(state0, _records(state0, email="ada@example.com")[0][2]),
                 _top_key_of(state0, _records(state0, id="r_anker")[0][2]),
                 _top_key_of(state0, _reservation_records(state0, reference="SEED01")[0][2])}
    for which, value in (("receipts", idem_key), ("tokens", token)):
        key = _top_key_with_value(state0, value) if value is not None else None
        if key is None or key in main_keys:
            continue
        e = copy.deepcopy(exp)
        del e["state"][key]
        yield f"missing {which} collection", e
    # invalid restaurant
    for field, value in (("timezone", "Nope/Zone"), ("slot_minutes", -5), ("reservation_duration_minutes", 0),
                         ("name", 5), ("tables", "none"), ("slot_minutes", 0), ("reservation_duration_minutes", -1),
                         ("slot_minutes", -30)):
        e, _, _, r, _ = fresh()
        if field in r:
            r[field] = value
            yield f"invalid restaurant ({field}={value!r})", e
    e, _, _, r, _ = fresh()
    if isinstance(r.get("tables"), list) and r["tables"] and isinstance(r["tables"][0], dict) and "capacity" in r["tables"][0]:
        r["tables"][0]["capacity"] = 0
        yield "invalid restaurant (table capacity 0)", e
    e, _, _, r, _ = fresh()
    if isinstance(r.get("tables"), list) and len(r["tables"]) >= 2 and all(isinstance(t, dict) and "id" in t for t in r["tables"]):
        r["tables"][1]["id"] = r["tables"][0]["id"]
        yield "duplicate table id inside one restaurant", e
    # invalid reservation record fields, applied to every copy of the seeded record
    for field, value in (("party_size", -1), ("party_size", "4"), ("status", "weird"), ("starts_at_local", "garbage"),
                         ("table_id", "zzz"), ("user_id", "u_nobody"), ("restaurant_id", "r_nope"),
                         ("reference", "bad ref"), ("reference", "abcdef"), ("reference", "X"), ("reference", "A" * 13),
                         ("reference", "SEED-01"), ("reference", ""), ("reference", "X" * 65),
                         ("reservation_id", ""), ("reservation_id", "X" * 65), ("id", ""), ("id", "X" * 65)):
        e, seeds, _, _, _ = fresh()
        if all(field in rec for _, _, rec in seeds):
            for _, _, rec in seeds:
                rec[field] = value
            yield f"invalid reservation ({field}={str(value)[:8]!r}{'…' if len(str(value)) > 8 else ''})", e
    # O-8: zero or empty start/end timestamps on the record, whatever the field is called
    for field in ("starts_at", "ends_at", "start", "end", "starts_at_utc", "ends_at_utc", "start_at", "end_at"):
        for value in ("", 0, "0001-01-01T00:00:00Z"):
            e, seeds, _, _, _ = fresh()
            if all(field in rec for _, _, rec in seeds):
                for _, _, rec in seeds:
                    rec[field] = value
                yield f"reservation {field}={value!r}", e
    # wrong JSON types
    for which, locate in (("users", lambda s: _records(s, email="ada@example.com")[0][2]),
                          ("restaurants", lambda s: _records(s, id="r_anker")[0][2])):
        for value in (True, 7):
            e = copy.deepcopy(exp)
            e["state"][_top_key_of(e["state"], locate(e["state"]))] = value
            yield f"{which} collection replaced by {value!r}", e
    e, _, _, _, u = fresh()
    u["email"] = 12
    yield "user email is a number", e
    e, seeds, _, _, _ = fresh()
    for _, _, rec in seeds:
        rec["reservation_id" if "reservation_id" in rec else "id"] = False
    yield "reservation id is a boolean", e
    # a boolean field holding a non-boolean (only when the implementation's state has one)
    e = copy.deepcopy(exp)
    for parent, key, v in _walk(e["state"]):
        if isinstance(v, bool) and parent is not None:
            parent[key] = "yes"
            yield f"boolean field {key!r} holds a string", e
            break



def test_C1_107_C1_109_tampered_export_is_refused(c, ada, bob):
    other = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")
    key = k()
    assert c.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T13:00", 2).status == 201
    exp = c.export()
    assert exp.status == 200
    before = _snapshot(c, [ada, bob])
    applied = 0
    for name, doc in _tampers(exp.json, other["reference"], idem_key=key, token=ada):
        r = c.import_(doc)
        assert r.status == 422 and r.code == "validation_failed", (name, r)
        assert _snapshot(c, [ada, bob]) == before, f"destination changed after refused import: {name}"
        applied += 1
    assert applied >= 30, applied
    # the untouched export still imports, and receipts survive
    assert c.import_(exp.json).status == 204
    assert c.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T13:00", 2).status == 200
    assert _snapshot(c, [ada, bob]) == before


def test_C1_107_C1_81_imported_references_must_conform(c, ada, bob):
    """R-28: a reference carried in imported state must match ^[A-Z0-9]{6,12}$ and be unique; otherwise 422 and
    the destination is unchanged. Conforming ones round-trip."""
    before = _snapshot(c, [ada, bob])
    exp = c.export().json
    for bad in ("abcdef", "r" * 64, "Ref-with.punct_1", "X", "seed01", "ABCDEFGHJKLMN", "SEED-01", "", "X" * 65):
        doc = copy.deepcopy(exp)
        for _, _, rec in _reservation_records(doc["state"], reference="SEED01"):
            rec["reference"] = bad
        r = c.import_(doc)
        assert r.status == 422 and r.code == "validation_failed", (bad, r)
        assert _snapshot(c, [ada, bob]) == before, bad
    for good in ("ABCDEFGHJKLM", "A1B2C3"):
        doc = copy.deepcopy(exp)
        for _, _, rec in _reservation_records(doc["state"], reference="SEED01"):
            rec["reference"] = good
        assert c.import_(doc).status == 204, good
        got = c.get(f"/reservations/{good}", token=bob)
        assert got.status == 200 and got.json["reservation_id"] == "res_seed", (good, got)
        err(c.get("/reservations/SEED01", token=bob), 404, "not_found")
    assert c.import_(exp).status == 204
    assert _snapshot(c, [ada, bob]) == before


@pytest.mark.parametrize("ref", ["X", "seed01", "ABCDEFGHJKLMN", "SEED-01", "", "X" * 65, "bad ref", "abcdef"])
def test_C1_81_C1_30_reset_refuses_nonconforming_reference(c, ada, bob, ref):
    """R-28: a seeded reference must match ^[A-Z0-9]{6,12}$."""
    before = _snapshot(c, [ada, bob])
    fx = base_fixture()
    fx["reservations"][0]["reference"] = ref
    r = c.reset(fx)
    assert r.status == 422 and r.code == "validation_failed", (ref, r)
    assert _snapshot(c, [ada, bob]) == before, ref
    assert c.get("/reservations/SEED01", token=bob).status == 200


@pytest.mark.parametrize("ref", ["SEED01", "ABCDEFGHJKLM", "A1B2C3", "000000"])
def test_C1_81_C1_30_reset_accepts_conforming_reference(c, ref):
    fx = base_fixture()
    fx["reservations"][0]["reference"] = ref
    assert c.reset(fx).status == 204, ref
    tok = c.login("bob@example.com", "bob secret 1")
    assert c.get(f"/reservations/{ref}", token=tok).json["reservation_id"] == "res_seed"


def test_C1_81_C1_30_reset_refuses_duplicate_reference(c, ada, bob):
    before = _snapshot(c, [ada, bob])
    fx = base_fixture()
    fx["reservations"].append({"id": "res_dup", "reference": "SEED01", "user_id": "u_ada", "restaurant_id": "r_all",
                               "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 1})
    r = c.reset(fx)
    assert r.status == 422 and r.code == "validation_failed", r
    assert _snapshot(c, [ada, bob]) == before


# ============================================================ O-3
LONG_R = "r" + "x" * 63
LONG_T = "t" + "y" * 63
LONG_U = "u" + "z" * 63


def edge_fixture() -> dict:
    fx = base_fixture()
    fx["users"].append({"id": LONG_U, "email": "edge@example.com", "password": "edge password", "display_name": "Edge"})
    fx["restaurants"].append({
        "id": LONG_R, "name": "Edge", "timezone": "Europe/Berlin", "slot_minutes": 30,
        "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
        "opening_hours": [{"weekday": d, "opens": "22:00", "closes": "24:00"} for d in ALL_DAYS],
        "tables": [{"id": LONG_T, "label": "solo", "capacity": 1}],
    })
    fx["reservations"].append({"id": "res_edge", "reference": "EDGE01", "user_id": LONG_U, "restaurant_id": LONG_R,
                               "table_id": LONG_T, "starts_at_local": f"{FUT_DAY}T22:00", "party_size": 1})
    return fx


def test_C1_107_C1_18_round_trip_edge_values(c, second_base_url):
    assert c.reset(edge_fixture()).status == 204
    edge = c.login("edge@example.com", "edge password")
    ada = c.login("ada@example.com", "correct horse")
    r = c.get(f"/restaurants/{LONG_R}").json
    assert r["id"] == LONG_R and r["tables"][0]["id"] == LONG_T and r["tables"][0]["capacity"] == 1
    assert r["cancellation_cutoff_minutes"] == 0 and r["opening_hours"][0]["closes"] == "24:00"
    sl = [s["starts_at_local"][-5:] for s in c.availability(LONG_R, FUT_DAY, 1).json["slots"]]
    assert sl == ["22:00", "22:30", "23:00"], sl
    key = k()
    o = c.book(edge, key, LONG_R, LONG_T, f"{FUT_DAY}T23:00", 1)
    assert o.status == 201 and o.json["ends_at"] == "2027-06-16T00:00:00+02:00", o   # closes 24:00 = next midnight
    err(c.book(ada, k(), LONG_R, LONG_T, f"{FUT_DAY}T23:00", 1), 409, "table_unavailable")
    err(c.book(ada, k(), LONG_R, LONG_T, f"{FUT_DAY}T22:30", 2), 422, "party_exceeds_capacity")
    assert c.get("/reservations/EDGE01", token=edge).json["reservation_id"] == "res_edge"
    before = {
        "r": c.get(f"/restaurants/{LONG_R}").json,
        "edge": c.get("/reservations", token=edge).json,
        "avail": c.availability(LONG_R, FUT_DAY, 1).json,
        "ids": [x["id"] for x in c.get("/restaurants").json["restaurants"]],
    }
    exp = c.export()
    assert exp.status == 200
    # mutate, then restore
    assert c.post("/reservations/EDGE01/cancel", token=edge).status == 200
    assert c.book(ada, k(), LONG_R, LONG_T, f"{FUT_DAY}T22:00", 1).status == 201
    assert c.import_(exp.json).status == 204
    after = {
        "r": c.get(f"/restaurants/{LONG_R}").json,
        "edge": c.get("/reservations", token=edge).json,
        "avail": c.availability(LONG_R, FUT_DAY, 1).json,
        "ids": [x["id"] for x in c.get("/restaurants").json["restaurants"]],
    }
    assert after == before
    rp = c.book(edge, key, LONG_R, LONG_T, f"{FUT_DAY}T23:00", 1)
    assert rp.status == 200 and rp.json == o.json
    assert c.import_(exp.json).status == 204 and c.import_(exp.json).status == 204
    assert c.get("/reservations", token=edge).json == before["edge"]
    if second_base_url:
        c2 = Client(second_base_url)
        assert c2.reset(other_fixture()).status == 204
        assert c2.import_(exp.json).status == 204
        assert c2.get(f"/restaurants/{LONG_R}").json == before["r"]
        assert c2.get("/reservations", token=edge).json == before["edge"]
        assert c2.availability(LONG_R, FUT_DAY, 1).json == before["avail"]
        assert c2.book(edge, key, LONG_R, LONG_T, f"{FUT_DAY}T23:00", 1).status == 200


# ============================================================ O-6
def test_C1_107_round_trip_duration_one_and_nines(c, second_base_url):
    fx = base_fixture()
    fx["restaurants"].append({
        "id": "r_min", "name": "Minute", "timezone": "Europe/Berlin", "slot_minutes": 20,
        "reservation_duration_minutes": 1, "cancellation_cutoff_minutes": 9,
        "opening_hours": [{"weekday": d, "opens": "23:00", "closes": "24:00"} for d in ALL_DAYS],
        "tables": [{"id": "m_9", "label": "nine", "capacity": 9}, {"id": "m_99", "label": "ninety-nine", "capacity": 99}],
    })
    assert c.reset(fx).status == 204
    ada = c.login("ada@example.com", "correct horse")
    r = c.get("/restaurants/r_min").json
    assert r["reservation_duration_minutes"] == 1 and r["cancellation_cutoff_minutes"] == 9
    assert [t["capacity"] for t in r["tables"]] == [9, 99]
    sl = [s["starts_at_local"][-5:] for s in c.availability("r_min", FUT_DAY, 99).json["slots"]]
    assert sl == ["23:00", "23:20", "23:40"], sl
    assert all(s["available_table_ids"] == ["m_99"] for s in c.availability("r_min", FUT_DAY, 10).json["slots"])
    key = k()
    o = c.book(ada, key, "r_min", "m_9", f"{FUT_DAY}T23:40", 9)
    assert o.status == 201 and o.json["ends_at"] == f"{FUT_DAY}T23:41:00+02:00", o
    o2 = book_all(c, ada, "r_min", "m_99", f"{FUT_DAY}T23:00", 99)
    assert o2["ends_at"] == f"{FUT_DAY}T23:01:00+02:00"
    err(c.book(ada, k(), "r_min", "m_9", f"{FUT_DAY}T23:40", 1), 409, "table_unavailable")
    assert c.book(ada, k(), "r_min", "m_9", f"{FUT_DAY}T23:20", 1).status == 201
    snap = lambda cl: {"r": cl.get("/restaurants/r_min").json, "list": cl.get("/reservations", token=ada).json,
                       "avail": [cl.availability("r_min", FUT_DAY, p).json for p in (1, 9, 10, 99)]}
    before = snap(c)
    exp = c.export()
    assert exp.status == 200
    assert c.post(f"/reservations/{o2['reference']}/cancel", token=ada).status == 200
    assert c.import_(exp.json).status == 204
    assert snap(c) == before
    rp = c.book(ada, key, "r_min", "m_9", f"{FUT_DAY}T23:40", 9)
    assert rp.status == 200 and rp.json == o.json
    if second_base_url:
        c2 = Client(second_base_url)
        assert c2.reset(other_fixture()).status == 204
        assert c2.import_(exp.json).status == 204
        assert snap(c2) == before


# ============================================================ O-7
@pytest.mark.parametrize("name,mutate", [
    ("closes equals opens", lambda fx: fx["restaurants"][0]["opening_hours"].__setitem__(0, {"weekday": "thu", "opens": "18:00", "closes": "18:00"})),
    ("capacity 0", lambda fx: fx["restaurants"][0]["tables"][0].__setitem__("capacity", 0)),
    ("slot_minutes 0", lambda fx: fx["restaurants"][0].__setitem__("slot_minutes", 0)),
    ("reservation_duration_minutes 0", lambda fx: fx["restaurants"][0].__setitem__("reservation_duration_minutes", 0)),
    ("capacity -1", lambda fx: fx["restaurants"][0]["tables"][0].__setitem__("capacity", -1)),
    ("slot_minutes -30", lambda fx: fx["restaurants"][0].__setitem__("slot_minutes", -30)),
])
def test_C1_28_C1_12_reset_rejects_degenerate_values(c, ada, bob, name, mutate):
    before = _snapshot(c, [ada, bob])
    fx = base_fixture()
    mutate(fx)
    r = c.reset(fx)
    assert r.status == 422 and r.code == "validation_failed", (name, r)
    assert _snapshot(c, [ada, bob]) == before, name
    assert c.get("/reservations", token=ada).status == 200


# ============================================================ O-4
STATIC_SLOTS = {"11:15", "11:30", "11:45", "12:00", "12:15", "12:30", "12:45"}   # overlap [12:00, 13:00) with 60 min
GRID = [f"{h:02d}:{m:02d}" for h in range(10, 21) for m in (0, 15, 30, 45)] + ["21:00"]   # 60-min bookings until 22:00


def _expected_availability(c: Client, tokens: list[str]) -> dict[str, list[str]]:
    """Slot -> available tables computed from every user's confirmed bookings (duration 60 min)."""
    busy: list[tuple[str, datetime, datetime]] = []
    for t in tokens:
        for x in c.get("/reservations", token=t).json["reservations"]:
            if x["status"] == "confirmed" and x["restaurant_id"] == "r_all":
                busy.append((x["table_id"], datetime.fromisoformat(x["starts_at"]), datetime.fromisoformat(x["ends_at"])))
    out = {}
    for hm in GRID:
        s = datetime.fromisoformat(f"{FUT_DAY}T{hm}:00+02:00")
        e = s + timedelta(minutes=60)
        out[hm] = [tid for tid in ("a_1", "a_2", "a_3")
                   if not any(b[0] == tid and b[1] < e and s < b[2] for b in busy)]
    return out


def test_C1_3_C1_46_reads_consistent_during_write_burst(c, ada, bob):
    static = [book_all(c, ada, "r_all", t, f"{FUT_DAY}T12:00", 1) for t in ("a_1", "a_2", "a_3")]
    static_refs = {x["reference"]: x for x in static}
    writers = [bob] + [c.signup(f"w{i}@example.com").json["token"] for i in range(3)]
    rng = random.Random(42)
    mine: dict[str, list[str]] = {t: [] for t in writers}
    hours = [14, 15, 16, 17, 18, 19, 20]

    def writer(tok: str):
        choice = rng.random()
        cl = Client(c.base_url, timeout=15)
        if mine[tok] and choice < 0.3:
            ref = rng.choice(mine[tok])
            return cl.post(f"/reservations/{ref}/cancel", token=tok)
        if mine[tok] and choice < 0.55:
            ref = rng.choice(mine[tok])
            body = {"starts_at_local": f"{FUT_DAY}T{rng.choice(hours):02d}:00"} if rng.random() < 0.5 else {"table_id": rng.choice(["a_1", "a_2", "a_3"])}
            return cl.patch(f"/reservations/{ref}", body, token=tok)
        return cl.book(tok, k(), "r_all", rng.choice(["a_1", "a_2", "a_3"]), f"{FUT_DAY}T{rng.choice(hours):02d}:00", 1)

    def reader(kind: str):
        cl = Client(c.base_url, timeout=15)
        if kind == "avail":
            return cl.availability("r_all", FUT_DAY, 1)
        if kind == "list":
            return cl.get("/reservations", token=ada)
        return cl.get(f"/reservations/{rng.choice(list(static_refs))}", token=ada)

    for _ in range(4):
        fns = []
        for i in range(48):
            if i % 2 == 0:
                tok = writers[i % len(writers)]
                fns.append(lambda tok=tok: ("w", tok, writer(tok)))
            else:
                kind = ("avail", "list", "get")[i % 3]
                fns.append(lambda kind=kind: ("r", kind, reader(kind)))
        for item in burst(fns):
            assert not isinstance(item, BaseException), item
            side, what, r = item
            assert r.status < 500, r
            if side == "w":
                if r.status == 201:
                    mine[what].append(r.json["reference"])
                assert r.status in (200, 201, 409), r
                continue
            if what == "avail":
                assert r.status == 200, r
                slots = r.json["slots"]
                assert [s["starts_at_local"][-5:] for s in slots] == GRID
                for s in slots:
                    ids = s["available_table_ids"]
                    assert set(ids) <= SLOT_RE_TABLES and len(ids) == len(set(ids)), s
                    if s["starts_at_local"][-5:] in STATIC_SLOTS:
                        assert ids == [], f"static booking shown available: {s}"
            elif what == "list":
                assert r.status == 200, r
                lst = r.json["reservations"]
                for x in lst:
                    check_reservation_shape(x)
                assert {x["reference"]: x for x in lst} == static_refs, lst
            else:
                assert r.status == 200, r
                check_reservation_shape(r.json)
                assert r.json == static_refs[r.json["reference"]]
    # steady state: availability equals the union of everybody's confirmed bookings
    expected = _expected_availability(c, [ada] + writers)
    actual = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_all", FUT_DAY, 1).json["slots"]}
    assert actual == expected
    # and no two confirmed bookings overlap on a table
    busy = []
    for t in [ada] + writers:
        for x in c.get("/reservations", token=t).json["reservations"]:
            if x["status"] == "confirmed":
                busy.append((x["table_id"], datetime.fromisoformat(x["starts_at"]), datetime.fromisoformat(x["ends_at"]), x["reference"]))
    for i, a in enumerate(busy):
        for b in busy[i + 1:]:
            assert not (a[0] == b[0] and a[1] < b[2] and b[1] < a[2]), (a, b)
