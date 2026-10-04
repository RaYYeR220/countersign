"""Stage-2 HTTP acceptance suite (Oracle): combinable pairs, table_ids, available_options, upgrade import,
and the stage-1 leftovers O-9..O-11. Test names carry the master-ledger ids C2.<n> (evidence/stage-2/ledger.md); clauses2.json maps the
entry-B ids to them.
"""
from __future__ import annotations

import copy
import json
import os
import uuid

import pytest

from client import Client, burst
from fixtures import FUT_DAY, FUT_FRI, FUT_THU, FUT_WED, PAST_DAY, base_fixture, other_fixture, stage1_fixture
from test_acceptance import check_reservation_shape, err, k


def book_ids(c: Client, token: str, restaurant: str, ids: list[str], local: str, party: int, key: str = None):
    return c.post("/reservations", {"restaurant_id": restaurant, "table_ids": ids, "starts_at_local": local,
                                    "party_size": party}, token=token, key=key or k())


def by_slot(c: Client, restaurant: str, date: str, party: int) -> dict:
    return {s["starts_at_local"][-5:]: s for s in c.availability(restaurant, date, party).json["slots"]}


# ============================================================ fixture / model
def test_C2_40_combinable_fixture(c):
    r = c.get("/restaurants/r_trio").json
    assert r["combinable"] == [["q_1", "q_2"], ["q_2", "q_3"]]
    assert c.get("/restaurants/r_anker").json["combinable"] == [["t_1", "t_2"]]
    assert c.reset(stage1_fixture()).status == 204          # stage-1 shape still accepted (B59)
    assert c.reset(base_fixture()).status == 204
    # R-34: every malformed entry is 422; a repeated pair in either order is 422; `combinable` itself not an array is 400
    for bad in ([["t_1", "t_2", "t_3"]], [["t_1"]], [["t_1", "zzz"]], [["t_1", "t_1"]], ["t_1,t_2"], [[1, 2]],
                [["t_1", "t_2"], ["t_2", "t_1"]], [["t_1", "t_2"], ["t_1", "t_2"]], [[]], [None]):
        fx = base_fixture()
        fx["restaurants"][0]["combinable"] = bad
        err(c.reset(fx), 422, "validation_failed")
        assert c.get("/restaurants/r_anker").json["combinable"] == [["t_1", "t_2"]], bad
    fx = base_fixture()
    fx["restaurants"][0]["combinable"] = "none"
    err(c.reset(fx), 400, "malformed_request")
    fx = base_fixture()
    del fx["restaurants"][0]["combinable"]
    assert c.reset(fx).status == 204 and c.get("/restaurants/r_anker").json["combinable"] == []


def test_C2_43_seeded_status_and_table_ids(c, bob):
    fx = base_fixture()
    fx["reservations"] = [
        {"id": "res_pair", "reference": "PAIR01", "user_id": "u_bob", "restaurant_id": "r_trio",
         "table_ids": ["q_1", "q_2"], "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 6},
        {"id": "res_canc", "reference": "CANC01", "user_id": "u_bob", "restaurant_id": "r_trio",
         "table_id": "q_3", "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2, "status": "cancelled"},
    ]
    assert c.reset(fx).status == 204
    tok = c.login("bob@example.com", "bob secret 1")
    pair = c.get("/reservations/PAIR01", token=tok).json
    assert pair["table_ids"] == ["q_1", "q_2"] and "table_id" not in pair and pair["status"] == "confirmed"
    canc = c.get("/reservations/CANC01", token=tok).json
    assert canc["status"] == "cancelled" and canc["table_ids"] == ["q_3"] and canc["table_id"] == "q_3"
    s = by_slot(c, "r_trio", FUT_FRI, 1)["19:00"]
    assert s["available_table_ids"] == ["q_3"]
    assert [o["table_ids"] for o in s["available_options"]] == [["q_3"]]
    for bad in ({"status": "weird"}, {"table_ids": ["q_1", "q_2", "q_3"]}, {"table_id": "q_1", "table_ids": ["q_2"]},
                {"table_ids": ["q_1", "q_1"]}, {"table_ids": []}, {"table_ids": "q_1"},
                {"table_ids": ["q_1", "q_3"]}, {"status": "CONFIRMED"}, {"status": None}):      # R-34: undeclared pair
        fx2 = base_fixture()
        row = {"id": "res_x", "reference": "SEEDX1", "user_id": "u_bob", "restaurant_id": "r_trio",
               "table_id": "q_1", "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}
        row.update(bad)
        if "table_ids" in bad and "table_id" not in bad:
            row.pop("table_id")
        fx2["reservations"].append(row)
        r = c.reset(fx2)
        assert r.status in (400, 422), (bad, r)
        if "table_ids" in bad and bad["table_ids"] == ["q_1", "q_3"]:
            err(r, 422, "validation_failed")
    # an undeclared seeded pair is refused on import as well (R-34)
    assert c.reset(base_fixture()).status == 204
    tok = c.login("bob@example.com", "bob secret 1")
    exp = c.export().json
    from test_hardening import _reservation_records
    doc = copy.deepcopy(exp)
    for _, _, rec in _reservation_records(doc["state"], reference="SEED01"):
        rec["table_ids"] = ["t_1", "t_2"]
        rec["restaurant_id"] = "r_anker"
    assert c.import_(doc).status == 204                                   # declared pair: fine
    doc = copy.deepcopy(exp)
    for _, _, rec in _reservation_records(doc["state"], reference="SEED01"):
        rec["restaurant_id"] = "r_trio"
        rec["table_ids"] = ["q_1", "q_3"]
    err(c.import_(doc), 422, "validation_failed")
    assert c.get("/reservations/SEED01", token=tok).json["table_ids"] == ["t_1", "t_2"]


# ============================================================ availability
def test_C2_44_C2_45_available_options(c, ada):
    s = by_slot(c, "r_trio", FUT_FRI, 1)["19:00"]
    assert s["available_table_ids"] == ["q_1", "q_2", "q_3"]
    assert s["available_options"] == [{"table_ids": ["q_1"], "capacity": 2}, {"table_ids": ["q_2"], "capacity": 4},
                                      {"table_ids": ["q_3"], "capacity": 4}, {"table_ids": ["q_1", "q_2"], "capacity": 6},
                                      {"table_ids": ["q_2", "q_3"], "capacity": 8}]
    s = by_slot(c, "r_trio", FUT_FRI, 5)["19:00"]
    assert s["available_table_ids"] == []
    assert s["available_options"] == [{"table_ids": ["q_1", "q_2"], "capacity": 6}, {"table_ids": ["q_2", "q_3"], "capacity": 8}]
    s = by_slot(c, "r_trio", FUT_FRI, 7)["19:00"]
    assert s["available_options"] == [{"table_ids": ["q_2", "q_3"], "capacity": 8}]
    s = by_slot(c, "r_trio", FUT_FRI, 9)["19:00"]
    assert s["available_options"] == [] and s["available_table_ids"] == []
    # a busy member removes every pair containing it
    assert c.book(ada, k(), "r_trio", "q_2", f"{FUT_FRI}T19:00", 2).status == 201
    s = by_slot(c, "r_trio", FUT_FRI, 1)["19:00"]
    assert s["available_table_ids"] == ["q_1", "q_3"]
    assert [o["table_ids"] for o in s["available_options"]] == [["q_1"], ["q_3"]]
    s = by_slot(c, "r_trio", FUT_FRI, 1)["21:00"]
    assert len(s["available_options"]) == 5
    # a restaurant without pairs: options are the singles only
    s = by_slot(c, "r_ny", FUT_THU, 1)["19:00"]
    assert s["available_options"] == [{"table_ids": ["n_1"], "capacity": 4}]
    # the stage-1 fixture shape (no combinable) behaves like an empty list
    assert c.reset(stage1_fixture()).status == 204
    s = by_slot(c, "r_trio", FUT_FRI, 1)["19:00"]
    assert [o["table_ids"] for o in s["available_options"]] == [["q_1"], ["q_2"], ["q_3"]]


# ============================================================ POST /reservations
def test_C2_38_C2_46_C2_48_pair_booking(c, ada, bob):
    r = book_ids(c, ada, "r_trio", ["q_1", "q_2"], f"{FUT_FRI}T19:00", 6)
    assert r.status == 201, r
    o = r.json
    check_reservation_shape({**o, "table_id": "x"})
    assert o["table_ids"] == ["q_1", "q_2"] and "table_id" not in o and o["party_size"] == 6
    g = c.get(f"/reservations/{o['reference']}", token=ada).json
    assert g == o
    lst = c.get("/reservations", token=ada).json["reservations"]
    assert lst == [o]
    s = by_slot(c, "r_trio", FUT_FRI, 1)
    for hm in ("18:00", "18:30", "19:00", "19:30", "20:00"):
        assert s[hm]["available_table_ids"] == ["q_3"], hm
        assert [x["table_ids"] for x in s[hm]["available_options"]] == [["q_3"]], hm
    assert s["20:30"]["available_table_ids"] == ["q_1", "q_2", "q_3"]
    err(c.book(bob, k(), "r_trio", "q_1", f"{FUT_FRI}T20:00", 1), 409, "table_unavailable")
    err(c.book(bob, k(), "r_trio", "q_2", f"{FUT_FRI}T18:00", 1), 409, "table_unavailable")
    assert c.book(bob, k(), "r_trio", "q_3", f"{FUT_FRI}T19:00", 1).status == 201


def test_C2_47_table_id_and_table_ids(c, ada):
    r = book_ids(c, ada, "r_trio", ["q_1"], f"{FUT_FRI}T19:00", 2)
    assert r.status == 201 and r.json["table_ids"] == ["q_1"] and r.json["table_id"] == "q_1"
    r = c.book(ada, k(), "r_trio", "q_2", f"{FUT_FRI}T19:00", 2)
    assert r.status == 201 and r.json["table_ids"] == ["q_2"] and r.json["table_id"] == "q_2"
    body = {"restaurant_id": "r_trio", "table_id": "q_3", "table_ids": ["q_3"], "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}
    err(c.post("/reservations", body, token=ada, key=k()), 422, "validation_failed")
    body = {"restaurant_id": "r_trio", "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}
    err(c.post("/reservations", body, token=ada, key=k()), 422, "validation_failed")
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 2


def test_C2_41_C2_49_C2_50_combination_not_allowed(c, ada):
    err(book_ids(c, ada, "r_trio", ["q_1", "q_3"], f"{FUT_FRI}T19:00", 2), 422, "combination_not_allowed")
    err(book_ids(c, ada, "r_trio", ["q_3", "q_1"], f"{FUT_FRI}T19:00", 2), 422, "combination_not_allowed")
    err(book_ids(c, ada, "r_trio", ["q_1", "q_2", "q_3"], f"{FUT_FRI}T19:00", 2), 422, "combination_not_allowed")
    err(book_ids(c, ada, "r_ny", ["n_1", "n_1"], f"{FUT_THU}T19:00", 2), 422, "validation_failed")
    assert book_ids(c, ada, "r_trio", ["q_2", "q_1"], f"{FUT_FRI}T19:00", 2).json["table_ids"] == ["q_1", "q_2"]
    assert c.get("/reservations", token=ada).json["reservations"][0]["table_ids"] == ["q_1", "q_2"]


def test_C2_42_C2_52_summed_capacity(c, ada):
    err(book_ids(c, ada, "r_trio", ["q_1", "q_2"], f"{FUT_FRI}T19:00", 7), 422, "party_exceeds_capacity")
    assert book_ids(c, ada, "r_trio", ["q_1", "q_2"], f"{FUT_FRI}T19:00", 6).status == 201
    err(book_ids(c, ada, "r_trio", ["q_2", "q_3"], f"{FUT_FRI}T21:30", 9), 422, "party_exceeds_capacity")
    assert book_ids(c, ada, "r_trio", ["q_2", "q_3"], f"{FUT_FRI}T21:30", 8).status == 201


def test_C2_51_any_member_taken(c, ada, bob):
    assert c.book(bob, k(), "r_trio", "q_2", f"{FUT_FRI}T19:00", 2).status == 201
    err(book_ids(c, ada, "r_trio", ["q_1", "q_2"], f"{FUT_FRI}T19:30", 2), 409, "table_unavailable")
    err(book_ids(c, ada, "r_trio", ["q_2", "q_3"], f"{FUT_FRI}T18:00", 2), 409, "table_unavailable")
    assert book_ids(c, ada, "r_trio", ["q_1", "q_2"], f"{FUT_FRI}T20:30", 2).status == 201


def test_C2_53_C2_47_validation_order(c, ada):
    err(book_ids(c, ada, "r_trio", ["q_1", "q_1"], f"{FUT_FRI}T19:00", 2), 422, "validation_failed")
    err(book_ids(c, ada, "r_trio", [], f"{FUT_FRI}T19:00", 2), 422, "validation_failed")
    err(book_ids(c, ada, "r_trio", [""], f"{FUT_FRI}T19:00", 2), 422, "validation_failed")
    err(book_ids(c, ada, "r_trio", ["q_1", "zzz"], f"{FUT_FRI}T19:00", 2), 404, "not_found")
    err(book_ids(c, ada, "r_trio", ["q_1", "a_1"], f"{FUT_FRI}T19:00", 2), 404, "not_found")
    # R-35: more than two is reported before an unknown member; an undeclared pair after 404
    err(book_ids(c, ada, "r_trio", ["q_1", "q_2", "zzz"], f"{FUT_FRI}T19:00", 2), 422, "combination_not_allowed")
    err(book_ids(c, ada, "r_trio", ["q_1", "zzz"], f"{FUT_FRI}T19:07", 2), 404, "not_found")
    for bad in ("q_1", 5, None, [1], [None], ["q_1", 2], {"id": "q_1"}):
        body = {"restaurant_id": "r_trio", "table_ids": bad, "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}
        err(c.post("/reservations", body, token=ada, key=k()), 400, "malformed_request")
    # combination rule precedes the time rules (Q3); duplicate precedes count (Q6)
    err(book_ids(c, ada, "r_trio", ["q_1", "q_3"], f"{FUT_FRI}T19:07", 2), 422, "combination_not_allowed")
    err(book_ids(c, ada, "r_trio", ["q_1", "q_1", "q_1"], f"{FUT_FRI}T19:00", 2), 422, "validation_failed")


def test_C2_32_pair_idempotency(c, ada):
    key = k()
    body = {"restaurant_id": "r_trio", "table_ids": ["q_1", "q_2"], "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 6}
    r = c.post("/reservations", body, token=ada, key=key)
    assert r.status == 201
    p = c.post("/reservations", body, token=ada, key=key)
    assert p.status == 200 and p.json == r.json
    err(c.post("/reservations", {**body, "table_ids": ["q_2", "q_1"]}, token=ada, key=key), 409, "idempotency_key_reuse")
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 1


# ============================================================ PATCH, cancel, moves
def test_C2_54_patch_and_cancel_with_pairs(c, ada, bob):
    o = c.book(ada, k(), "r_trio", "q_1", f"{FUT_FRI}T19:00", 2).json
    ref = o["reference"]
    p = c.patch(f"/reservations/{ref}", {"table_ids": ["q_1", "q_2"], "party_size": 6}, token=ada)
    assert p.status == 200 and p.json["table_ids"] == ["q_1", "q_2"] and "table_id" not in p.json, p
    assert p.json["reference"] == ref and p.json["reservation_id"] == o["reservation_id"]
    err(c.patch(f"/reservations/{ref}", {"table_ids": ["q_1", "q_3"]}, token=ada), 422, "combination_not_allowed")
    err(c.patch(f"/reservations/{ref}", {"table_id": "q_3", "table_ids": ["q_3"]}, token=ada), 422, "validation_failed")
    err(c.patch(f"/reservations/{ref}", {"table_ids": "q_3"}, token=ada), 400, "malformed_request")
    err(c.patch(f"/reservations/{ref}", {"party_size": 7}, token=ada), 422, "party_exceeds_capacity")
    assert c.book(bob, k(), "r_trio", "q_3", f"{FUT_FRI}T20:00", 1).status == 201
    err(c.patch(f"/reservations/{ref}", {"table_ids": ["q_2", "q_3"]}, token=ada), 409, "table_unavailable")
    assert c.get(f"/reservations/{ref}", token=ada).json == p.json
    # self-overlap is fine; back to a single
    p2 = c.patch(f"/reservations/{ref}", {"table_ids": ["q_2"], "party_size": 4}, token=ada)
    assert p2.status == 200 and p2.json["table_ids"] == ["q_2"] and p2.json["table_id"] == "q_2"
    p3 = c.patch(f"/reservations/{ref}", {"table_id": "q_1", "party_size": 2}, token=ada)
    assert p3.status == 200 and p3.json["table_ids"] == ["q_1"]
    p4 = c.patch(f"/reservations/{ref}", {"table_ids": ["q_2", "q_1"]}, token=ada)
    assert p4.status == 200 and p4.json["table_ids"] == ["q_1", "q_2"]
    s = by_slot(c, "r_trio", FUT_FRI, 1)["19:00"]
    assert s["available_table_ids"] == [] and s["available_options"] == []      # q_1+q_2 ours, q_3 bob's at 20:00
    assert by_slot(c, "r_trio", FUT_FRI, 1)["18:00"]["available_table_ids"] == ["q_3"]
    x = c.post(f"/reservations/{ref}/cancel", token=ada)
    assert x.status == 200 and x.json["table_ids"] == ["q_1", "q_2"] and x.json["status"] == "cancelled"
    s = by_slot(c, "r_trio", FUT_FRI, 1)["19:00"]
    assert s["available_table_ids"] == ["q_1", "q_2"]
    assert [o["table_ids"] for o in s["available_options"]] == [["q_1"], ["q_2"], ["q_1", "q_2"]]
    s = by_slot(c, "r_trio", FUT_FRI, 1)["21:30"]
    assert s["available_table_ids"] == ["q_1", "q_2", "q_3"] and len(s["available_options"]) == 5


def test_C2_58_moves_with_table_ids(c, ada, bob):
    a = c.book(ada, k(), "r_trio", "q_1", f"{FUT_FRI}T19:00", 2).json
    b = c.book(ada, k(), "r_trio", "q_3", f"{FUT_FRI}T19:00", 2).json
    key = k()
    r = c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_ids": ["q_1", "q_2"], "party_size": 5},
                                                {"reference": b["reference"]}]}, token=ada, key=key)
    assert r.status == 201, r
    assert r.json["reservations"][0]["table_ids"] == ["q_1", "q_2"] and "table_id" not in r.json["reservations"][0]
    assert r.json["reservations"][1]["table_ids"] == ["q_3"] and r.json["reservations"][1]["table_id"] == "q_3"
    rp = c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_ids": ["q_1", "q_2"], "party_size": 5},
                                                 {"reference": b["reference"]}]}, token=ada, key=key)
    assert rp.status == 200 and rp.json == r.json
    # overlapping resulting sets share t_2 -> 409, nothing changes
    err(c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "table_ids": ["q_2", "q_3"], "party_size": 2}]},
               token=ada, key=k()), 409, "table_unavailable")
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_ids": ["q_2"], "party_size": 2},
                                                {"reference": b["reference"], "table_ids": ["q_2", "q_3"]}]},
               token=ada, key=k()), 409, "table_unavailable")
    err(c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "table_ids": ["q_1", "q_3"]}]},
               token=ada, key=k()), 422, "combination_not_allowed")
    err(c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "table_id": "q_3", "table_ids": ["q_3"]}]},
               token=ada, key=k()), 422, "validation_failed")
    err(c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "table_ids": [3]}]},
               token=ada, key=k()), 400, "malformed_request")
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == r.json["reservations"][0]
    assert c.get(f"/reservations/{b['reference']}", token=ada).json == r.json["reservations"][1]
    # a swap that reshuffles members: a -> [t_2,t_3] party 5, b -> t_1 party 2
    s = c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_ids": ["q_2", "q_3"]},
                                                {"reference": b["reference"], "table_ids": ["q_1"]}]}, token=ada, key=k())
    assert s.status == 201 and [x["table_ids"] for x in s.json["reservations"]] == [["q_2", "q_3"], ["q_1"]], s


# ============================================================ concurrency
def test_C2_59_concurrent_pairs_sharing_a_table(c, ada, bob):
    toks = [ada, bob] + [c.signup(f"p{i}@example.com").json["token"] for i in range(4)]
    choices = [["q_1", "q_2"], ["q_2", "q_3"], ["q_2"], ["q_1"], ["q_3"]]
    fns = [(lambda t=toks[i % 6], ids=choices[i % 5]: book_ids(Client(c.base_url, timeout=15), t, "r_trio", ids,
                                                             f"{FUT_FRI}T19:00", 1)) for i in range(40)]
    res = burst(fns)
    for r in res:
        assert not isinstance(r, BaseException), r
        assert r.status in (201, 409), r
    winners = [r.json for r in res if r.status == 201]
    used: list[str] = []
    for w in winners:
        assert not (set(w["table_ids"]) & set(used)), winners
        used += w["table_ids"]
    s = by_slot(c, "r_trio", FUT_FRI, 1)["19:00"]
    assert set(s["available_table_ids"]) == {"q_1", "q_2", "q_3"} - set(used)
    all_conf = [x for t in toks for x in c.get("/reservations", token=t).json["reservations"]
                if x["status"] == "confirmed" and x["restaurant_id"] == "r_trio"]
    assert sorted(json.dumps(x, sort_keys=True) for x in all_conf) == sorted(json.dumps(x, sort_keys=True) for x in winners)


# ============================================================ upgrade (B52)
@pytest.fixture(scope="session")
def stage1_export(request):
    """An export produced by the accepted stage-1 service: from --stage1-base-url (live) or --stage1-export (file)."""
    url = request.config.getoption("--stage1-base-url")
    path = request.config.getoption("--stage1-export")
    if url:
        s1 = Client(url.rstrip("/"))
        assert s1.reset(stage1_fixture()).status == 204
        ada = s1.login("ada@example.com", "correct horse")
        new = s1.signup("up@example.com", "upgrade-password", "Up").json
        key = "k-upgrade-" + uuid.uuid4().hex[:8]
        o = s1.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00", 2)
        assert o.status == 201, o
        err(s1.book(ada, "k-failed-" + key, "r_all", "a_1", f"{FUT_DAY}T12:07", 2), 422, "not_on_slot_grid")
        cancelled = s1.book(ada, k(), "r_all", "a_2", f"{FUT_DAY}T14:00", 2).json
        assert s1.post(f"/reservations/{cancelled['reference']}/cancel", token=ada).status == 200
        mv_key = "k-moves-" + uuid.uuid4().hex[:8]
        mv = s1.post("/reservation-moves", {"moves": [{"reference": o.json["reference"], "party_size": 1}]}, token=ada, key=mv_key)
        assert mv.status == 201, mv
        exp = s1.export().json
        return {"export": exp, "ada": ada, "new": new, "key": key, "booking": mv.json["reservations"][0],
                "original": o.json, "cancelled": cancelled, "mv_key": mv_key, "mv": mv.json, "failed_key": "k-failed-" + key}
    if path:
        with open(path, encoding="utf-8") as f:
            return {"export": json.load(f)}
    pytest.skip("no --stage1-base-url or --stage1-export")


def test_C2_35_C2_36_upgrade_from_stage1_export(c, stage1_export):
    exp = stage1_export["export"]
    assert exp.get("track") == "tablekeeper" and "state" in exp
    r = c.import_(exp)
    assert r.status == 204, r
    if "ada" not in stage1_export:
        return   # file-only: acceptance of the export is all we can check
    ada, new, key = stage1_export["ada"], stage1_export["new"], stage1_export["key"]
    booking, original = stage1_export["booking"], stage1_export["original"]
    # tokens survive (B53), accounts and hashed login survive
    assert c.get("/reservations", token=ada).status == 200
    assert c.get("/reservations", token=new["token"]).status == 200
    assert c.login("up@example.com", "upgrade-password")
    # references and records survive with table_ids added (B54, B71)
    g = c.get(f"/reservations/{booking['reference']}", token=ada)
    assert g.status == 200, g
    assert g.json["table_ids"] == [booking["table_id"]] and g.json["table_id"] == booking["table_id"]
    for f in ("reservation_id", "reference", "status", "starts_at", "ends_at", "created_at", "party_size", "starts_at_local"):
        assert g.json[f] == booking[f], f
    assert c.get(f"/reservations/{stage1_export['cancelled']['reference']}", token=ada).json["status"] == "cancelled"
    # receipts survive: the original single-table body replays with the original stage-1 response (B55)
    body = {"restaurant_id": "r_all", "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2}
    rp = c.post("/reservations", body, token=ada, key=key)
    assert rp.status == 200 and rp.json == original, rp
    err(c.post("/reservations", {**body, "party_size": 1}, token=ada, key=key), 409, "idempotency_key_reuse")
    mv = c.post("/reservation-moves", {"moves": [{"reference": booking["reference"], "party_size": 1}]}, token=ada, key=stage1_export["mv_key"])
    assert mv.status == 200 and mv.json == stage1_export["mv"], mv
    # a failed key is still a first use
    assert c.post("/reservations", {**body, "table_id": "a_3"}, token=ada, key=stage1_export["failed_key"]).status == 201
    # stage-2 features work on the imported restaurants (no pairs declared in a stage-1 fixture)
    s = by_slot(c, "r_all", FUT_DAY, 1)["12:00"]
    assert s["available_table_ids"] == ["a_2"] or "a_1" not in s["available_table_ids"]
    assert all(len(o["table_ids"]) == 1 for o in s["available_options"])
    err(book_ids(c, ada, "r_all", ["a_2", "a_3"], f"{FUT_DAY}T16:00", 2), 422, "combination_not_allowed")
    # and the upgraded state exports and re-imports as stage-2 state
    exp2 = c.export().json
    assert c.import_(exp2).status == 204
    assert c.get(f"/reservations/{booking['reference']}", token=ada).json == g.json
    assert c.post("/reservations", body, token=ada, key=key).json == original


# ============================================================ stage-1 leftovers O-9..O-11
def test_O9_import_refusals(c, ada, bob):
    from test_hardening import _records, _reservation_records, _snapshot, _top_key_of
    key = k()
    assert c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00", 2).status == 201
    exp = c.export().json
    before = _snapshot(c, [ada, bob])
    cases = []
    e = copy.deepcopy(exp)
    _records(e["state"], email="ada@example.com")[0][2]["id"] = "x" * 65
    cases.append(("user id too long", e))
    e = copy.deepcopy(exp)
    _records(e["state"], email="ada@example.com")[0][2]["id"] = ""
    cases.append(("user id empty", e))
    e = copy.deepcopy(exp)
    found = False
    # receipt status out of range: locate the receipt record by the key text and set any integer field named status
    for parent, key_, v in list(__import__("test_hardening")._walk(e["state"])):
        if isinstance(v, dict) and "status" in v and is_int_like(v["status"]) and _contains(v, key):
            v["status"] = 999
            found = True
            break
    if found:
        cases.append(("receipt status out of range", e))
    for which, locate in (("users", lambda s: _records(s, email="ada@example.com")[0][2]),
                          ("restaurants", lambda s: _records(s, id="r_trio")[0][2]),
                          ("reservations", lambda s: _reservation_records(s, reference="SEED01")[0][2])):
        e = copy.deepcopy(exp)
        del e["state"][_top_key_of(e["state"], locate(e["state"]))]
        cases.append((f"missing only {which}", e))
    e = copy.deepcopy(exp)
    _records(e["state"], id="r_trio")[0][2]["slot_minutes"] = "thirty"
    cases.append(("number field holds text", e))
    e = copy.deepcopy(exp)
    for _, _, rec in _reservation_records(e["state"], reference="SEED01"):
        rec["party_size"] = "four"
    cases.append(("party_size holds text", e))
    for name, doc in cases:
        r = c.import_(doc)
        assert r.status == 422 and r.code == "validation_failed", (name, r)
        assert _snapshot(c, [ada, bob]) == before, name
    assert c.import_(exp).status == 204


def is_int_like(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _contains(obj, needle: str) -> bool:
    return needle in json.dumps(obj)


def test_O10_null_bodies_and_items(c, ada):
    body = {"restaurant_id": "r_all", "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2}
    err(c.request("POST", "/reservations", raw=b"null", token=ada, key=k()), 400, "malformed_request")
    err(c.request("POST", "/auth/login", raw=b"null"), 400, "malformed_request")
    err(c.request("POST", "/auth/signup", raw=b"null"), 400, "malformed_request")
    err(c.request("POST", "/_test/reset", raw=b"null", timeout=10), 400, "malformed_request")
    err(c.request("POST", "/_test/import", raw=b"null", timeout=10), 400, "malformed_request")
    err(c.request("POST", "/reservation-moves", raw=b"null", token=ada, key=k()), 400, "malformed_request")
    ref = c.post("/reservations", body, token=ada, key=k()).json["reference"]
    err(c.request("PATCH", f"/reservations/{ref}", raw=b"null", token=ada), 400, "malformed_request")
    err(c.post("/reservation-moves", {"moves": [None]}, token=ada, key=k()), 422, "validation_failed")
    err(c.post("/reservation-moves", {"moves": [{"reference": ref}, None]}, token=ada, key=k()), 422, "validation_failed")
    fx = base_fixture()
    fx["restaurants"][0]["tables"].append(None)
    err(c.reset(fx), 400, "malformed_request")
    big = 2 ** 53 + 1
    r = c.post("/reservations", {**body, "table_id": "a_3", "party_size": big}, token=ada, key=k())
    assert r.status == 422 and r.code in ("party_exceeds_capacity", "validation_failed"), r
    r = c.post("/reservations", {**body, "table_id": "a_3", "party_size": 2 ** 63}, token=ada, key=k())
    assert r.status == 422 and r.code in ("party_exceeds_capacity", "validation_failed"), r
    r = c.post("/reservations", {**body, "table_id": "a_3", "party_size": 10 ** 30}, token=ada, key=k())
    assert r.status == 422 and r.code in ("party_exceeds_capacity", "validation_failed"), r


@pytest.mark.parametrize("where", ["user", "restaurant", "table", "reservation"])
def test_O11_empty_fixture_ids(c, ada, bob, where):
    before = c.get("/restaurants").json
    fx = base_fixture()
    if where == "user":
        fx["users"][0]["id"] = ""
    elif where == "restaurant":
        fx["restaurants"][0]["id"] = ""
    elif where == "table":
        fx["restaurants"][0]["tables"][0]["id"] = ""
    else:
        fx["reservations"][0]["id"] = ""
    err(c.reset(fx), 422, "validation_failed")
    assert c.get("/restaurants").json == before
    assert c.get("/reservations", token=ada).status == 200
