"""Stage-4 HTTP acceptance suite (Oracle): seating replans (preview, apply, closures), series amendments, the
restaurant revision, upgrades from stages 1-3, and the stage-3 leftover O-15. Test names carry the master-ledger ids C4.<n>
(evidence/stage-4/ledger.md); clauses4.json maps the entry-B ids.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
import uuid
from datetime import date, datetime, timedelta

import pytest

from client import Client, burst
from fixtures import ALL_DAYS, FUT_DAY, FUT_FRI, FUT_THU, PAST_DAY, base_fixture, policy, stage1_fixture, stage2_fixture
from test_acceptance import RFC3339, check_reservation_shape, err, k
from test_stage3_api import adopt, book_single, history, publish

FUT_FRI2 = (date.fromisoformat(FUT_FRI) + timedelta(days=7)).isoformat()
FUT_FRI3 = (date.fromisoformat(FUT_FRI) + timedelta(days=14)).isoformat()


@pytest.fixture
def mia(c) -> str:
    return c.login("mia@example.com", "mia manages")


def preview(c, token, rid, table, frm, to, key=None, **extra):
    return c.post(f"/restaurants/{rid}/replans", {"table_id": table, "from": frm, "to": to, **extra}, token=token, key=key or k())


def apply(c, token, rid, pid, key=None, body=None):
    return c.post(f"/restaurants/{rid}/replans/{pid}/apply", {} if body is None else body, token=token, key=key or k())


def amend(c, token, sid, rev, from_index, local_time, key=None, **extra):
    return c.post(f"/series/{sid}/amend", {"expected_revision": rev, "from_index": from_index, "local_time": local_time, **extra},
                  token=token, key=key or k())


def inst(local_date: str, hm: str, offset: str = "+02:00") -> str:
    return f"{local_date}T{hm}:00{offset}"


def rev_of(c, mia, rid="r_trio") -> int:
    """The restaurant revision is observable only through a preview (B15); a preview does not change it."""
    r = preview(c, mia, rid, "q_1", inst("2030-01-01", "00:00"), inst("2030-01-01", "00:01"))
    assert r.status == 201, r
    return r.json["restaurant_revision"]


# ============================================================ preview (B5-B17)
def test_C4_3_C4_4_C4_9_preview_shape_and_validation(c, ada, bob, mia):
    a = book_single(c, ada, "r_trio", "q_2", f"{FUT_FRI}T19:00", 3)
    b = book_single(c, bob, "r_trio", "q_1", f"{FUT_FRI}T19:00", 2)
    frm, to = inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")
    err(c.post("/restaurants/r_trio/replans", {"table_id": "q_2", "from": frm, "to": to}, key=k()), 401, "unauthenticated")
    err(preview(c, bob, "r_trio", "q_2", frm, to), 403, "forbidden")
    err(preview(c, mia, "nope", "q_2", frm, to), 404, "not_found")
    err(c.post("/restaurants/r_trio/replans", {"table_id": "q_2", "from": frm, "to": to}, token=mia), 400, "missing_idempotency_key")
    err(c.request("POST", "/restaurants/r_trio/replans", raw=b"[]", token=mia, key=k()), 400, "malformed_request")
    for body in ({"from": frm, "to": to}, {"table_id": "q_2", "to": to}, {"table_id": "q_2", "from": frm}):
        err(c.post("/restaurants/r_trio/replans", body, token=mia, key=k()), 422, "validation_failed")
    err(c.post("/restaurants/r_trio/replans", {"table_id": 5, "from": frm, "to": to}, token=mia, key=k()), 400, "malformed_request")
    for bad_from, bad_to in ((to, frm), (frm, frm), (f"{FUT_FRI}T18:00:00", to), ("garbage", to), (frm, ""), (5, to), (frm, None), (frm, True)):
        err(preview(c, mia, "r_trio", "q_2", bad_from, bad_to), 422, "validation_failed")      # R-62: every from/to problem
    z = preview(c, mia, "r_trio", "q_2", f"{FUT_FRI}T16:00:00Z", f"{FUT_FRI}T21:00:00.500+02:00")   # Z and fractions accepted
    assert z.status == 201 and z.json["closure"]["from"] == f"{FUT_FRI}T18:00:00+02:00" and not z.json["closure"]["to"].endswith("Z"), z
    err(preview(c, mia, "r_trio", "zzz", frm, to), 404, "not_found")
    err(preview(c, mia, "r_trio", "t_1", frm, to), 404, "not_found")
    err(preview(c, mia, "r_trio", "", frm, to), 422, "validation_failed")
    r = preview(c, mia, "r_trio", "q_2", frm, to, foo="ignored")
    assert r.status == 201, r
    p = r.json
    assert set(p) == {"plan_id", "restaurant_revision", "closure", "assignments", "moved_count", "unused_seats"}
    assert isinstance(p["plan_id"], str) and 0 < len(p["plan_id"]) <= 64
    assert p["closure"]["table_id"] == "q_2"
    assert datetime.fromisoformat(p["closure"]["from"]) == datetime.fromisoformat(frm) and datetime.fromisoformat(p["closure"]["to"]) == datetime.fromisoformat(to)
    assert RFC3339.match(p["closure"]["from"]) and RFC3339.match(p["closure"]["to"])
    refs = sorted([a["reference"], b["reference"]])
    assert [x["reference"] for x in p["assignments"]] == refs                                     # B14
    by = {x["reference"]: x for x in p["assignments"]}
    assert by[a["reference"]] == {"reference": a["reference"], "table_ids": ["q_3"], "changed": True}   # party 3 -> q_3 (cap 4)
    assert by[b["reference"]] == {"reference": b["reference"], "table_ids": ["q_1"], "changed": False}
    assert p["moved_count"] == 1 and p["unused_seats"] == (4 - 3) + (2 - 2)
    # the preview changed nothing (B16)
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == a
    assert len(history(c, ada, a["reference"])) == 1
    assert c.availability("r_trio", FUT_FRI, 1).json["slots"][2]["available_table_ids"] == ["q_3"]


def test_C4_4_considered_set(c, ada, bob, mia):
    ends_at_from = book_single(c, ada, "r_trio", "q_2", f"{FUT_FRI}T18:00", 2)      # [18:00, 19:30) ends exactly at from
    inside = book_single(c, bob, "r_trio", "q_2", f"{FUT_FRI}T19:30", 2)            # overlaps
    starts_at_to = book_single(c, ada, "r_trio", "q_2", f"{FUT_FRI}T21:30", 2)      # starts exactly at to
    other_table = book_single(c, bob, "r_trio", "q_1", f"{FUT_FRI}T20:00", 2)       # other table, overlapping
    p = preview(c, mia, "r_trio", "q_2", inst(FUT_FRI, "19:30"), inst(FUT_FRI, "21:30")).json
    refs = {x["reference"]: x for x in p["assignments"]}
    assert set(refs) == {inside["reference"], other_table["reference"]}
    assert refs[other_table["reference"]]["changed"] is False and refs[other_table["reference"]]["table_ids"] == ["q_1"]
    assert refs[inside["reference"]]["table_ids"] == ["q_3"] and p["moved_count"] == 1


def test_C4_7_C4_8_optimality(c, ada, bob, mia):
    """Capacity under each booking's own terms, no cutoff protection, and the three-level objective."""
    # policy for Friday: q_1 seats 1 only -> a party of 2 booked under it cannot go to q_1 even though the fixture says 2
    assert publish(c, mia, "r_trio", policy(FUT_FRI, capacities={"q_1": 1, "q_2": 4, "q_3": 4})).status == 201
    pair = c.post("/reservations", {"restaurant_id": "r_trio", "table_ids": ["q_2", "q_3"], "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}, token=ada, key=k()).json
    assert pair["accepted_terms"]["capacities"]["q_1"] == 1
    p = preview(c, mia, "r_trio", "q_3", inst(FUT_FRI, "19:00"), inst(FUT_FRI, "20:00")).json
    assert p["assignments"] == [{"reference": pair["reference"], "table_ids": ["q_2"], "changed": True}]
    assert p["unused_seats"] == 4 - 2
    # objective 1: keep as many as possible unchanged. Two bookings; closing q_3 forces the q_3 booking to move;
    # the q_1 booking stays although moving both to a lower rank would be "tidier".
    assert c.reset(base_fixture()).status == 204
    ada, bob, mia = c.login("ada@example.com", "correct horse"), c.login("bob@example.com", "bob secret 1"), c.login("mia@example.com", "mia manages")
    x = book_single(c, ada, "r_trio", "q_3", f"{FUT_FRI}T19:00", 2)
    y = book_single(c, bob, "r_trio", "q_1", f"{FUT_FRI}T19:00", 2)
    p = preview(c, mia, "r_trio", "q_3", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")).json
    by = {a["reference"]: a for a in p["assignments"]}
    assert by[y["reference"]]["changed"] is False and by[x["reference"]] == {"reference": x["reference"], "table_ids": ["q_2"], "changed": True}
    assert p["moved_count"] == 1 and p["unused_seats"] == (4 - 2) + (2 - 2)
    # objective 2: fewest unused seats. Party 2 booking on q_3 (cap 4) must move; q_1 (cap 2) is free -> choose q_1
    assert c.post(f"/reservations/{y['reference']}/cancel", token=bob).status == 200
    p = preview(c, mia, "r_trio", "q_3", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")).json
    assert p["assignments"] == [{"reference": x["reference"], "table_ids": ["q_1"], "changed": True}] and p["unused_seats"] == 0
    # objective 3: rank vector. Two party-4 bookings on q_2 and q_3 at the same time; closing q_3 leaves q_2 and the
    # pair q_1+q_2 (cap 6) ... only one booking can keep q_2; the other needs a set without q_3: q_1 (2) too small,
    # q_1+q_2 conflicts with q_2 -> no feasible plan (409). Then free q_2 by moving that booking elsewhere:
    assert c.post(f"/reservations/{x['reference']}/cancel", token=ada).status == 200
    m = book_single(c, ada, "r_trio", "q_2", f"{FUT_FRI}T19:00", 4)
    n = book_single(c, bob, "r_trio", "q_3", f"{FUT_FRI}T19:00", 4)
    err(preview(c, mia, "r_trio", "q_3", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")), 409, "no_feasible_plan")
    # rank tie-break: two party-2 bookings, q_1 (rank 0) and q_2 (rank 1) both free after closing q_3; the moved
    # booking (lower reference first in the vector) takes the lowest-rank feasible option with equal unused seats:
    assert c.post(f"/reservations/{m['reference']}/cancel", token=ada).status == 200
    assert c.post(f"/reservations/{n['reference']}/cancel", token=bob).status == 200
    past = book_single(c, ada, "r_trio", "q_3", "2020-09-25T19:00", 2)                   # cutoff long passed (B11)
    p = preview(c, mia, "r_trio", "q_3", inst("2020-09-25", "18:00"), inst("2020-09-25", "23:00")).json
    assert p["assignments"] == [{"reference": past["reference"], "table_ids": ["q_1"], "changed": True}]
    r = apply(c, mia, "r_trio", p["plan_id"])
    assert r.status == 201 and r.json["reservations"][0]["table_ids"] == ["q_1"] and r.json["reservations"][0]["status"] == "confirmed"


def test_C4_6_planning_limit(c, ada, bob, mia):
    fx = base_fixture()
    big = {"id": "r_big", "name": "Big", "timezone": "Europe/Berlin", "slot_minutes": 30, "reservation_duration_minutes": 90,
           "cancellation_cutoff_minutes": 60, "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in ALL_DAYS],
           "tables": [{"id": f"b_{i}", "label": str(i), "capacity": 2} for i in range(1, 8)], "combinable": [], "manager_user_ids": ["u_mia"]}
    fx["restaurants"].append(big)
    assert c.reset(fx).status == 204
    ada, mia = c.login("ada@example.com", "correct horse"), c.login("mia@example.com", "mia manages")
    for i in range(1, 8):
        assert c.book(ada, k(), "r_big", f"b_{i}", f"{FUT_FRI}T19:00", 1).status == 201
    r = preview(c, mia, "r_big", "b_1", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00"))
    assert (r.status == 422 and r.code == "planning_limit") or (r.status == 409 and r.code == "no_feasible_plan"), r
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 7


def test_C4_10_C4_11_restaurant_revision_and_no_plan(c, ada, bob, mia):
    assert rev_of(c, mia) == 0
    a = book_single(c, ada, "r_trio", "q_1", f"{FUT_FRI}T19:00", 2)                       # +1
    assert rev_of(c, mia) == 1
    err(c.book(ada, k(), "r_trio", "q_1", f"{FUT_FRI}T19:00", 2), 409, "table_unavailable")  # failure: +0
    assert c.patch(f"/reservations/{a['reference']}", {"party_size": 2}, token=ada).status == 200   # no-op: +0
    assert rev_of(c, mia) == 1
    assert c.patch(f"/reservations/{a['reference']}", {"party_size": 1}, token=ada).status == 200   # +1
    assert publish(c, mia, "r_trio", policy(FUT_FRI2, capacities={"q_1": 2, "q_2": 4, "q_3": 4})).status == 201    # +1
    key = k()
    body = {"restaurant_id": "r_trio", "table_id": "q_2", "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}
    assert c.post("/reservations", body, token=ada, key=key).status == 201                 # +1
    assert c.post("/reservations", body, token=ada, key=key).status == 200                 # replay: +0
    assert rev_of(c, mia) == 4
    assert c.post(f"/reservations/{a['reference']}/cancel", token=ada).status == 200       # +1
    assert c.post(f"/reservations/{a['reference']}/cancel", token=ada).status == 200       # repeat: +0
    assert rev_of(c, mia) == 5
    # no feasible plan: nothing changes, key reusable
    for t in ("q_1", "q_3"):
        assert c.book(bob, k(), "r_trio", t, f"{FUT_FRI}T19:00", 2).status == 201
    key = k()
    err(preview(c, mia, "r_trio", "q_2", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00"), key), 409, "no_feasible_plan")
    assert rev_of(c, mia) == 7
    assert preview(c, mia, "r_trio", "q_2", inst(FUT_FRI2, "18:00"), inst(FUT_FRI2, "23:00"), key).status == 201


# ============================================================ apply (B18-B28)
def test_C4_12_C4_13_C4_14_apply(c, ada, bob, mia):
    a = book_single(c, ada, "r_trio", "q_2", f"{FUT_FRI}T19:00", 3)
    b = book_single(c, bob, "r_trio", "q_1", f"{FUT_FRI}T19:00", 2)
    frm, to = inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")
    p = preview(c, mia, "r_trio", "q_2", frm, to).json
    err(c.post(f"/restaurants/r_trio/replans/{p['plan_id']}/apply", {}, key=k()), 401, "unauthenticated")
    err(apply(c, bob, "r_trio", p["plan_id"]), 403, "forbidden")
    err(apply(c, mia, "nope", p["plan_id"]), 404, "not_found")
    err(c.post(f"/restaurants/r_trio/replans/{p['plan_id']}/apply", {}, token=mia), 400, "missing_idempotency_key")
    err(apply(c, mia, "r_trio", "plan_nope"), 404, "not_found")
    err(apply(c, mia, "r_anker", p["plan_id"]), 404, "not_found")                              # B19: other restaurant
    key = k()
    r = apply(c, mia, "r_trio", p["plan_id"], key)
    assert r.status == 201, r
    assert set(r.json) == {"plan_id", "restaurant_revision", "reservations"} and r.json["plan_id"] == p["plan_id"]
    assert r.json["restaurant_revision"] == p["restaurant_revision"] + 1                          # B24
    assert [x["reference"] for x in r.json["reservations"]] == sorted([a["reference"], b["reference"]])
    moved = next(x for x in r.json["reservations"] if x["reference"] == a["reference"])
    kept = next(x for x in r.json["reservations"] if x["reference"] == b["reference"])
    check_reservation_shape(moved)
    assert moved["table_ids"] == ["q_3"] and moved["revision"] == 2 and moved["accepted_terms"] == a["accepted_terms"]
    assert moved["starts_at"] == a["starts_at"] and moved["ends_at"] == a["ends_at"] and moved["party_size"] == 3
    assert kept == b                                                                              # unmoved gains nothing
    h = history(c, ada, a["reference"])
    assert len(h) == 2 and h[1]["event"] == "reassigned" and h[1]["plan_id"] == p["plan_id"] and h[1]["revision"] == 2
    assert h[1]["changes"] == [{"field": "table_ids", "from": ["q_2"], "to": ["q_3"]}] and h[1]["accepted_terms"] == a["accepted_terms"]
    assert len(history(c, bob, b["reference"])) == 1
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == moved
    # replay, second key, stale
    rp = apply(c, mia, "r_trio", p["plan_id"], key)
    assert rp.status == 200 and rp.json == r.json
    err(apply(c, mia, "r_trio", p["plan_id"]), 409, "plan_already_applied")                      # B21
    p2 = preview(c, mia, "r_trio", "q_1", inst(FUT_FRI2, "18:00"), inst(FUT_FRI2, "23:00")).json
    assert c.book(bob, k(), "r_trio", "q_1", f"{FUT_FRI3}T19:00", 2).status == 201                 # intervening revision
    err(apply(c, mia, "r_trio", p2["plan_id"]), 409, "stale_plan")                                # B20
    err(apply(c, mia, "r_trio", p2["plan_id"]), 409, "stale_plan")
    assert c.availability("r_trio", FUT_FRI2, 1).json["slots"][2]["available_table_ids"] == ["q_1", "q_2", "q_3"]
    p3 = preview(c, mia, "r_trio", "q_1", inst(FUT_FRI2, "18:00"), inst(FUT_FRI2, "23:00")).json
    assert apply(c, mia, "r_trio", p3["plan_id"]).status == 201
    # replay after later changes still returns the original (B21)
    assert c.patch(f"/reservations/{a['reference']}", {"party_size": 2}, token=ada).status == 200
    rp = apply(c, mia, "r_trio", p["plan_id"], key)
    assert rp.status == 200 and rp.json == r.json


def test_C4_15_closures_block(c, ada, bob, mia):
    a = book_single(c, ada, "r_trio", "q_2", f"{FUT_FRI}T19:00", 3)
    p = preview(c, mia, "r_trio", "q_2", inst(FUT_FRI, "19:00"), inst(FUT_FRI, "21:00")).json
    assert apply(c, mia, "r_trio", p["plan_id"]).status == 201
    av = {s["starts_at_local"][-5:]: s for s in c.availability("r_trio", FUT_FRI, 1).json["slots"]}
    for hm in ("18:00", "18:30", "19:00", "19:30", "20:00", "20:30"):                               # overlap [19:00, 21:00)
        assert "q_2" not in av[hm]["available_table_ids"], hm
        assert all("q_2" not in o["table_ids"] for o in av[hm]["available_options"]), hm
    assert av["21:00"]["available_table_ids"] == ["q_1", "q_2"] or "q_2" in av["21:00"]["available_table_ids"]
    ex = {s["starts_at_local"][-5:]: s for s in c.get(f"/availability?restaurant_id=r_trio&date={FUT_FRI}&party_size=1&explain=true").json["slots"]}
    e = {x["table_id"]: x for x in ex["19:30"]["explain"]}["q_2"]
    assert e["rules"] == [{"rule": "capacity", "holds": True}, {"rule": "no_overlap", "holds": False}] and not e["available"]
    err(c.book(bob, k(), "r_trio", "q_2", f"{FUT_FRI}T20:00", 2), 409, "table_unavailable")
    err(c.post("/reservations", {"restaurant_id": "r_trio", "table_ids": ["q_1", "q_2"], "starts_at_local": f"{FUT_FRI}T18:30", "party_size": 2}, token=bob, key=k()), 409, "table_unavailable")
    err(c.patch(f"/reservations/{a['reference']}", {"table_id": "q_2"}, token=ada), 409, "table_unavailable")
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_ids": ["q_2"]}]}, token=ada, key=k()), 409, "table_unavailable")
    assert c.book(bob, k(), "r_trio", "q_2", f"{FUT_FRI}T21:00", 2).status == 201
    assert c.book(bob, k(), "r_trio", "q_2", f"{FUT_FRI2}T19:00", 2).status == 201
    # R-73: a second closure on the already-closed table is planned and applied like any other
    p2 = preview(c, mia, "r_trio", "q_2", inst(FUT_FRI, "20:00"), inst(FUT_FRI, "22:00"))
    assert p2.status == 201 and p2.json["moved_count"] == 1, p2                               # bob's 21:00 booking moves
    assert apply(c, mia, "r_trio", p2.json["plan_id"]).status == 201
    err(c.book(bob, k(), "r_trio", "q_2", f"{FUT_FRI}T21:00", 2), 409, "table_unavailable")
    # the closure persists through export/import
    exp = c.export().json
    assert c.import_(exp).status == 204
    err(c.book(bob, k(), "r_trio", "q_2", f"{FUT_FRI}T19:30", 2), 409, "table_unavailable")


def test_C4_16_concurrent_apply_and_other_restaurant(c, ada, bob, mia):
    a = book_single(c, ada, "r_trio", "q_2", f"{FUT_FRI}T19:00", 3)
    b = book_single(c, bob, "r_trio", "q_2", f"{FUT_FRI}T21:00", 2)
    p = preview(c, mia, "r_trio", "q_2", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")).json
    assert p["moved_count"] == 2
    # a closure at another restaurant does not invalidate the plan (B28)
    pa = preview(c, mia, "r_anker", "t_1", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")).json
    assert apply(c, mia, "r_anker", pa["plan_id"]).status == 201
    res = burst([(lambda: apply(Client(c.base_url, timeout=15), mia, "r_trio", p["plan_id"])) for _ in range(20)])
    codes = [r.status for r in res if not isinstance(r, BaseException)]
    assert len(codes) == 20 and codes.count(201) == 1, codes
    assert all(r.code in ("plan_already_applied", "stale_plan") for r in res if r.status != 201)
    ga, gb = c.get(f"/reservations/{a['reference']}", token=ada).json, c.get(f"/reservations/{b['reference']}", token=bob).json
    assert ga["table_ids"] != ["q_2"] and gb["table_ids"] != ["q_2"] and ga["revision"] == 2 and gb["revision"] == 2
    assert len(history(c, ada, a["reference"])) == 2 and len(history(c, bob, b["reference"])) == 2


def test_C4_22_plan_moves_series_members(c, ada, mia):
    o = book_single(c, ada, "r_trio", "q_2", f"{FUT_FRI}T19:00", 3)
    s = adopt(c, ada, o["reference"], 3, 1).json
    assert c.patch(f"/reservations/{s['occurrences'][2]['reference']}", {"party_size": 2}, token=ada).status == 200   # exception at 2
    g = c.get(f"/series/{s['series_id']}", token=ada).json
    assert g["revision"] == 2 and [x["exception"] for x in g["occurrences"]] == [False, False, True]
    p = preview(c, mia, "r_trio", "q_2", inst(FUT_FRI, "18:00"), inst(FUT_FRI3, "23:00")).json       # three Fridays
    assert p["moved_count"] == 3
    assert apply(c, mia, "r_trio", p["plan_id"]).status == 201
    g2 = c.get(f"/series/{s['series_id']}", token=ada).json
    assert g2["revision"] == 3 and [x["exception"] for x in g2["occurrences"]] == [False, False, True]      # once, flags kept
    for x, before in zip(g2["occurrences"], g["occurrences"]):
        assert x["reference"] == before["reference"] and x["reservation"]["starts_at_local"] == before["reservation"]["starts_at_local"]
        assert "q_2" not in x["reservation"]["table_ids"] and x["reservation"]["accepted_terms"] == before["reservation"]["accepted_terms"]
        assert x["reservation"]["revision"] == before["reservation"]["revision"] + 1
    # the party-2 exception fits q_1 exactly (fewest unused seats); the party-3 occurrences need q_3
    assert [x["reservation"]["table_ids"] for x in g2["occurrences"]] == [["q_3"], ["q_3"], ["q_1"]]


# ============================================================ series amend (B29-B38, B40)
def series_for(c, ada, count=4, table="q_2", party=3):
    o = book_single(c, ada, "r_trio", table, f"{FUT_FRI}T19:00", party)
    return adopt(c, ada, o["reference"], count, 1).json


def test_C4_17_C4_18_amend_preconditions(c, ada, bob):
    s = series_for(c, ada)
    sid = s["series_id"]
    err(c.post(f"/series/{sid}/amend", {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}, key=k()), 401, "unauthenticated")
    err(amend(c, bob, sid, 1, 0, "20:00"), 404, "not_found")
    err(amend(c, ada, "nope", 1, 0, "20:00"), 404, "not_found")
    err(c.post(f"/series/{sid}/amend", {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}, token=ada), 400, "missing_idempotency_key")
    err(c.request("POST", f"/series/{sid}/amend", raw=b"[]", token=ada, key=k()), 400, "malformed_request")
    for body in ({"from_index": 0, "local_time": "20:00"}, {"expected_revision": 1, "local_time": "20:00"}, {"expected_revision": 1, "from_index": 0}):
        err(c.post(f"/series/{sid}/amend", body, token=ada, key=k()), 422, "validation_failed")
    for rev in (0, -1, True, "1", 1.5, None):
        err(amend(c, ada, sid, rev, 0, "20:00"), 422, "validation_failed")
    for fi in (-1, 4, True, "0", 1.5, None):
        err(amend(c, ada, sid, 1, fi, "20:00"), 422, "validation_failed")
    for lt in ("8:00", "20:00:00", "24:00", "", "20h", "25:00", "20:60", None, 2000):
        err(amend(c, ada, sid, 1, 0, lt), 422, "validation_failed")
    err(amend(c, ada, sid, 2, 0, "20:00"), 409, "stale_revision")
    err(amend(c, ada, sid, 2, 0, "25:00"), 422, "validation_failed")                              # 422 before stale
    assert c.get(f"/series/{sid}", token=ada).json == s
    r = amend(c, ada, sid, 1, 0, "20:00", foo="ignored")
    assert r.status == 201


def test_C4_19_C4_21_amend_semantics(c, ada, bob, mia):
    s = series_for(c, ada, count=5)
    sid, refs = s["series_id"], [x["reference"] for x in s["occurrences"]]
    assert c.patch(f"/reservations/{refs[2]}", {"party_size": 2}, token=ada).status == 200       # exception at 2
    assert c.post(f"/reservations/{refs[3]}/cancel", token=ada).status == 200                   # cancelled at 3
    g = c.get(f"/series/{sid}", token=ada).json
    assert g["revision"] == 3
    # a policy from the third Friday with duration 120 and cutoff 30: occurrence 4 adopts it
    assert publish(c, mia, "r_trio", policy(FUT_FRI3, reservation_duration_minutes=120, cancellation_cutoff_minutes=30,
                                             capacities={"q_1": 2, "q_2": 4, "q_3": 4})).status == 201
    key = k()
    r = amend(c, ada, sid, 3, 1, "20:00", key)
    assert r.status == 201, r
    out = r.json
    assert out["revision"] == 4 and [x["exception"] for x in out["occurrences"]] == [False, False, True, False, False]
    occ = out["occurrences"]
    assert occ[0]["reservation"]["starts_at_local"].endswith("19:00") and occ[0]["reservation"]["revision"] == 1
    assert occ[1]["reservation"]["starts_at_local"].endswith("20:00") and occ[1]["reservation"]["revision"] == 2
    assert occ[2]["reservation"]["starts_at_local"].endswith("19:00")                            # exception untouched
    assert occ[3]["reservation"]["status"] == "cancelled" and occ[3]["reservation"]["starts_at_local"].endswith("19:00")
    assert occ[4]["reservation"]["starts_at_local"].endswith("20:00") and occ[4]["reservation"]["accepted_terms"]["policy_version"] == 1
    assert occ[4]["reservation"]["ends_at"].endswith("22:00:00+02:00") and occ[1]["reservation"]["ends_at"].endswith("21:30:00+02:00")
    for i in (1, 4):
        assert occ[i]["reservation"]["table_ids"] == ["q_2"] and occ[i]["reservation"]["party_size"] == 3
        h = history(c, ada, refs[i])
        assert h[-1]["event"] == "changed" and h[-1]["changes"] == [{"field": "starts_at_local", "from": h[-1]["changes"][0]["from"], "to": occ[i]["reservation"]["starts_at_local"]}]
    assert c.get(f"/series/{sid}", token=ada).json == out
    # all-no-op: nothing changes (B37); replay returns the original (B38)
    r2 = amend(c, ada, sid, 4, 1, "20:00")
    assert r2.status == 201 and r2.json["revision"] == 4 and r2.json == out
    r3 = amend(c, ada, sid, 4, 4, "20:00")
    assert r3.status == 201 and r3.json["revision"] == 4
    assert c.post(f"/reservations/{refs[1]}/cancel", token=ada).status == 200
    rp = amend(c, ada, sid, 3, 1, "20:00", key)
    assert rp.status == 200 and rp.json == out
    err(amend(c, ada, sid, 3, 1, "21:00", key), 409, "idempotency_key_reuse")
    # restaurant revision: +1 for the whole amend (B36) — measured through previews
    before = rev_of(c, mia)
    assert amend(c, ada, sid, 5, 4, "20:30").status == 201               # 20:30 + 120 min fits the policy's hours
    assert rev_of(c, mia) == before + 1


def test_C4_19_C4_20_amend_failures(c, ada, bob, mia):
    s = series_for(c, ada, count=4)
    sid, refs = s["series_id"], [x["reference"] for x in s["occurrences"]]
    # index 2 closed on Fridays from FUT_FRI3 (policy), index 3 occupied at the new time: index order wins
    fri4 = (date.fromisoformat(FUT_FRI) + timedelta(days=21)).isoformat()
    assert c.book(bob, k(), "r_trio", "q_2", f"{fri4}T21:30", 2).status == 201                    # blocker, before the policy
    assert publish(c, mia, "r_trio", policy(FUT_FRI3, opening_hours=[{"weekday": "mon", "opens": "18:00", "closes": "23:00"}],
                                             capacities={"q_1": 2, "q_2": 4, "q_3": 4})).status == 201
    key = k()
    err(amend(c, ada, sid, 1, 0, "21:30", key), 422, "outside_opening_hours")
    assert c.get(f"/series/{sid}", token=ada).json == s and all(len(history(c, ada, r_)) == 1 for r_ in refs)
    # repair the policy: Fridays open again from FUT_FRI3 -> index 3 conflicts -> table_unavailable
    assert publish(c, mia, "r_trio", policy(FUT_FRI3, capacities={"q_1": 2, "q_2": 4, "q_3": 4})).status == 201
    err(amend(c, ada, sid, 1, 0, "21:30", key), 409, "table_unavailable")
    assert c.get(f"/series/{sid}", token=ada).json == s
    # closures conflict too (B34)
    p = preview(c, mia, "r_trio", "q_2", inst(FUT_FRI2, "21:30"), inst(FUT_FRI2, "23:00")).json
    assert apply(c, mia, "r_trio", p["plan_id"]).status == 201                                     # index 1 is at 19:00, unaffected
    g = c.get(f"/series/{sid}", token=ada).json
    err(amend(c, ada, sid, g["revision"], 0, "21:30"), 409, "table_unavailable")
    # the key failed every time: still a first use
    assert amend(c, ada, sid, g["revision"], 0, "20:00", key).status == 201
    # cutoff: a series whose anchor is in the past cannot be amended from index 0
    past = book_single(c, ada, "r_trio", "q_1", "2020-09-25T19:00", 2)
    fx_s = adopt(c, ada, past["reference"], 2, 1)
    assert fx_s.status == 409 and fx_s.code == "cutoff_passed"


def test_C4_23_concurrent_amends(c, ada):
    s = series_for(c, ada, count=3)
    sid = s["series_id"]
    res = burst([(lambda i=i: amend(Client(c.base_url, timeout=15), ada, sid, 1, 0, f"{19 + i % 3}:30")) for i in range(12)])
    oks = [r for r in res if not isinstance(r, BaseException) and r.status == 201]
    assert all(r.status in (201, 409) for r in res if not isinstance(r, BaseException))
    changed = [r for r in oks if r.json["revision"] == 2]
    assert len(changed) <= 1
    g = c.get(f"/series/{sid}", token=ada).json
    assert g["revision"] in (1, 2)
    times = {x["reservation"]["starts_at_local"][-5:] for x in g["occurrences"]}
    assert len(times) == 1                                                                      # all moved together or none


# ============================================================ upgrade (B41)
@pytest.fixture(scope="session")
def older_exports4(request):
    out = {}
    for stage, opt, fx in ((1, "--stage1-base-url", stage1_fixture()), (2, "--stage2-base-url", stage2_fixture()), (3, "--stage3-base-url", base_fixture())):
        url = request.config.getoption(opt)
        if not url:
            continue
        s = Client(url.rstrip("/"))
        assert s.reset(fx).status == 204
        ada = s.login("ada@example.com", "correct horse")
        key = f"k-up{stage}-" + uuid.uuid4().hex[:8]
        o = s.book(ada, key, "r_trio", "q_2", f"{FUT_FRI}T19:00", 3)
        assert o.status == 201, o
        entry = {"export": None, "ada": ada, "key": key, "booking": o.json, "series": None, "series_key": None}
        if stage == 3:
            skey = "k-ser-" + uuid.uuid4().hex[:8]
            ser = s.post("/series", {"anchor_reference": o.json["reference"], "count": 3, "interval_weeks": 1}, token=ada, key=skey)
            assert ser.status == 201, ser
            assert s.post(f"/reservations/{ser.json['occurrences'][2]['reference']}/cancel", token=ada).status == 200
            assert s.patch(f"/reservations/{ser.json['occurrences'][1]['reference']}", {"table_id": "q_3"}, token=ada).status == 200
            entry["series"], entry["series_key"] = ser.json, skey
        entry["export"] = s.export().json
        out[stage] = entry
    if not out:
        pytest.skip("no --stage{1,2,3}-base-url")
    return out


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_C4_24_upgrade_from_older_exports(c, mia, older_exports4, stage):
    if stage not in older_exports4:
        pytest.skip(f"no stage-{stage} source")
    src = older_exports4[stage]
    assert c.import_(src["export"]).status == 204
    ada, booking = src["ada"], src["booking"]
    g = c.get(f"/reservations/{booking['reference']}", token=ada).json
    assert g["reference"] == booking["reference"] and g["table_ids"] == ["q_2"]
    body = {"restaurant_id": "r_trio", "table_id": "q_2", "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 3}
    rp = c.post("/reservations", body, token=ada, key=src["key"])
    assert rp.status == 200 and rp.json == booking                                                  # old receipt verbatim
    mia2 = c.login("mia@example.com", "mia manages")
    if stage == 3:
        ser = src["series"]
        sid = ser["series_id"]
        g = c.get(f"/series/{sid}", token=ada).json
        assert g["revision"] == 3 and [x["exception"] for x in g["occurrences"]] == [False, True, False]
        assert g["occurrences"][2]["reservation"]["status"] == "cancelled"
        rp = c.post("/series", {"anchor_reference": booking["reference"], "count": 3, "interval_weeks": 1}, token=ada, key=src["series_key"])
        assert rp.status == 200 and rp.json == ser
        r = amend(c, ada, sid, 3, 0, "20:00")
        assert r.status == 201 and r.json["revision"] == 4
        assert r.json["occurrences"][0]["reservation"]["starts_at_local"].endswith("20:00")
        assert r.json["occurrences"][1]["reservation"]["starts_at_local"].endswith("19:00")        # exception untouched
        assert r.json["occurrences"][2]["reservation"]["status"] == "cancelled"
        p = preview(c, mia2, "r_trio", "q_2", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")).json
        assert p["moved_count"] == 1
        assert apply(c, mia2, "r_trio", p["plan_id"]).status == 201
        assert c.get(f"/series/{sid}", token=ada).json["revision"] == 5
    else:
        ser = adopt(c, ada, booking["reference"], 2, 1).json
        assert amend(c, ada, ser["series_id"], 1, 1, "20:00").json["revision"] == 2
        # stage-1/2 fixtures declare no managers, so no one can replan an imported restaurant of theirs
        err(preview(c, mia2, "r_trio", "q_2", inst(FUT_FRI, "18:00"), inst(FUT_FRI, "23:00")), 403, "forbidden")
    exp4 = c.export().json
    assert c.import_(exp4).status == 204


# ============================================================ O-15
def test_O15_import_boundaries(c, ada, bob, mia):
    from test_hardening import _records, _reservation_records, _snapshot, _walk
    assert publish(c, mia, "r_anker", policy("2027-09-01", slot_minutes=1)).status == 201            # slot 1 accepted on publish
    key = k()
    o = c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00", 2).json
    s = adopt(c, ada, o["reference"], 2, 1).json
    exp = c.export().json
    before = _snapshot(c, [ada, bob]) | {"policies": c.get("/restaurants/r_anker/policies").json,
                                         "series": c.get(f"/series/{s['series_id']}", token=ada).json}

    def receipt(doc):
        for parent, key_, v in _walk(doc["state"]):
            if isinstance(v, dict) and isinstance(v.get("status"), int) and not isinstance(v["status"], bool) and "response" in v:
                return v
        raise AssertionError("no receipt")

    def pols(doc):
        r = _records(doc["state"], id="r_anker")[0][2]
        return next(v for v in r.values() if isinstance(v, list) and v and isinstance(v[0], dict) and "policy_version" in v[0])

    def series_container(doc):
        for key_, v in doc["state"].items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "occurrences" in v[0]:
                return v, 0
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, dict) and "occurrences" in vv:
                        return v, kk
        raise AssertionError("no series")

    e = copy.deepcopy(exp); receipt(e)["status"] = 200
    assert c.import_(e).status == 204                                                                 # boundary accepted
    assert c.post("/reservations", {"restaurant_id": "r_all", "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2}, token=ada, key=key).status == 200
    e = copy.deepcopy(exp); receipt(e)["status"] = 199
    err(c.import_(e), 422, "validation_failed")
    e = copy.deepcopy(exp); pols(e)[0]["slot_minutes"] = 1
    assert c.import_(e).status == 204                                                                 # slot 1 accepted
    e = copy.deepcopy(exp); pols(e)[0]["slot_minutes"] = 0
    err(c.import_(e), 422, "validation_failed")
    e = copy.deepcopy(exp); pols(e)[0]["opening_hours"][0]["opens"] = "6pm"                            # only opens is bad
    err(c.import_(e), 422, "validation_failed")
    e = copy.deepcopy(exp); pols(e)[0]["opening_hours"][0]["opens"] = "25:00"
    err(c.import_(e), 422, "validation_failed")
    e = copy.deepcopy(exp); cont, key_ = series_container(e)
    if isinstance(cont, list):
        cont[0] = None
    else:
        cont[key_] = None
    err(c.import_(e), 422, "validation_failed")                                                       # null series entry
    e = copy.deepcopy(exp); cont, key_ = series_container(e)
    if isinstance(cont, dict):
        cont["ser_other"] = cont.pop(key_)                                                             # mis-keyed
        err(c.import_(e), 422, "validation_failed")
    else:
        cont[0]["series_id"] = "ser_other"                                                             # id no longer matches the records
        err(c.import_(e), 422, "validation_failed")
    assert c.import_(exp).status == 204
    assert _snapshot(c, [ada, bob]) | {"policies": c.get("/restaurants/r_anker/policies").json,
                                       "series": c.get(f"/series/{s['series_id']}", token=ada).json} == before
