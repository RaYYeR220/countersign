"""Stage-3 HTTP acceptance suite (Oracle): explanations, history, policies and accepted terms, revisions,
series, combined-table history, collective moves under policies, the upgrade from stage-1/stage-2 exports, and the
stage-2 leftovers O-12/O-13. Test names carry the entry-B ids (evidence/stage-3/ledger-B.md) until C3 ids exist.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import date, datetime, timedelta

import pytest

from client import Client, burst
from fixtures import (ALL_DAYS, BERLIN_SPRING, FUT_DAY, FUT_FRI, FUT_THU, FUT_WED, PAST_DAY, base_fixture,
                      other_fixture, policy, stage1_fixture, stage2_fixture)
from test_acceptance import RFC3339, check_reservation_shape, err, k

TERMS_KEYS = {"policy_version", "slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes",
              "opening_hours", "capacities"}
FUT_FRI2 = (date.fromisoformat(FUT_FRI) + timedelta(days=7)).isoformat()      # 2027-10-01
FUT_FRI3 = (date.fromisoformat(FUT_FRI) + timedelta(days=14)).isoformat()     # 2027-10-08
FUT_FRI4 = (date.fromisoformat(FUT_FRI) + timedelta(days=21)).isoformat()     # 2027-10-15


@pytest.fixture
def mia(c) -> str:
    return c.login("mia@example.com", "mia manages")


def publish(c, token, rid, pol, key=None):
    return c.post(f"/restaurants/{rid}/policies", pol, token=token, key=key or k())


def book_single(c, token, rid, table, local, party=2):
    r = c.book(token, k(), rid, table, local, party)
    assert r.status == 201, r
    return r.json


def history(c, token, ref):
    r = c.get(f"/reservations/{ref}/history", token=token)
    assert r.status == 200, r
    return r.json["entries"]


def check_terms(t: dict):
    assert set(t) == TERMS_KEYS, t
    assert isinstance(t["policy_version"], int) and isinstance(t["capacities"], dict)


def fixture_terms(rid="r_anker") -> dict:
    r = next(x for x in base_fixture()["restaurants"] if x["id"] == rid)
    return {"policy_version": 0, "slot_minutes": r["slot_minutes"],
            "reservation_duration_minutes": r["reservation_duration_minutes"],
            "cancellation_cutoff_minutes": r["cancellation_cutoff_minutes"], "opening_hours": r["opening_hours"],
            "capacities": {t["id"]: t["capacity"] for t in r["tables"]}}


# ============================================================ explanations (B3-B11)
def test_B3_B4_B7_B8_B9_B10_explain_matches_rules(c, ada):
    book_single(c, ada, "r_anker", "t_2", f"{FUT_FRI}T19:00", 2)
    plain = c.availability("r_anker", FUT_FRI, 3).json
    r = c.get(f"/availability?restaurant_id=r_anker&date={FUT_FRI}&party_size=3&explain=true")
    assert r.status == 200, r
    for s_plain, s in zip(plain["slots"], r.json["slots"]):
        assert {x: s[x] for x in s_plain} == s_plain                              # B4: nothing else changes
        ex = s["explain"]
        assert [e["table_id"] for e in ex] == ["t_1", "t_2"]                      # B8
        for e in ex:
            assert set(e) == {"table_id", "policy_version", "available", "rules"} and e["policy_version"] == 0
            assert [x["rule"] for x in e["rules"]] == ["capacity", "no_overlap"]  # B9
            assert e["available"] == all(x["holds"] for x in e["rules"])         # B10
        assert [e["table_id"] for e in ex if e["available"]] == s["available_table_ids"]
        by = {e["table_id"]: e for e in ex}
        assert by["t_1"]["rules"][0]["holds"] is False                             # capacity 2 < 3
        assert by["t_1"]["rules"][1]["holds"] is True
        hm = s["starts_at_local"][-5:]
        busy = hm in ("18:00", "18:30", "19:00", "19:30", "20:00")
        assert by["t_2"]["rules"][1]["holds"] is (not busy), hm
    # a table excluded by both reports both false
    r = c.get(f"/availability?restaurant_id=r_anker&date={FUT_FRI}&party_size=5&explain=true").json
    e2 = {e["table_id"]: e for e in r["slots"][2]["explain"]}["t_2"]
    assert e2["rules"] == [{"rule": "capacity", "holds": False}, {"rule": "no_overlap", "holds": False}] and not e2["available"]


def test_B5_B6_explain_parameter(c):
    for v in ("false", "1", "", "TRUE", "True", "yes", "0", "true%20"):
        err(c.get(f"/availability?restaurant_id=r_anker&date={FUT_FRI}&party_size=2&explain={v}"), 422, "validation_failed")
    plain = c.availability("r_anker", FUT_FRI, 2).json
    assert all("explain" not in s for s in plain["slots"])
    assert set(plain["slots"][0]) == {"starts_at_local", "starts_at", "available_table_ids", "available_options"}
    # parameter order: explain is checked after the required ones and before the restaurant 404 (Q3)
    err(c.get(f"/availability?restaurant_id=nope&date={FUT_FRI}&party_size=2&explain=false"), 422, "validation_failed")
    err(c.get(f"/availability?restaurant_id=nope&date={FUT_FRI}&party_size=2&explain=true"), 404, "not_found")
    err(c.get(f"/availability?restaurant_id=r_anker&date=bad&party_size=2&explain=false"), 422, "validation_failed")


def test_B11_explain_closed_and_full(c):
    r = c.get(f"/availability?restaurant_id=r_anker&date={FUT_WED}&party_size=2&explain=true").json
    assert r["slots"] == []
    r = c.get(f"/availability?restaurant_id=r_anker&date={FUT_FRI}&party_size=99&explain=true").json
    assert len(r["slots"]) == 9
    for s in r["slots"]:
        assert s["available_table_ids"] == [] and [e["table_id"] for e in s["explain"]] == ["t_1", "t_2"]
        assert all(e["rules"][0]["holds"] is False and not e["available"] for e in s["explain"])


# ============================================================ history (B12-B20)
def test_B12_B15_B16_B17_B18_B19_history_entries(c, ada):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    ref = o["reference"]
    h = history(c, ada, ref)
    assert len(h) == 1 and set(h[0]) == {"seq", "at", "event", "changes", "revision", "accepted_terms"}
    assert h[0]["seq"] == 1 and h[0]["event"] == "created" and h[0]["revision"] == 1 and RFC3339.match(h[0]["at"])
    assert h[0]["changes"] == [{"field": "table_id", "from": None, "to": "t_1"},
                               {"field": "starts_at_local", "from": None, "to": f"{FUT_FRI}T19:00"},
                               {"field": "party_size", "from": None, "to": 2}]
    check_terms(h[0]["accepted_terms"])
    assert c.patch(f"/reservations/{ref}", {"party_size": 2}, token=ada).status == 200       # no-op: no entry
    assert len(history(c, ada, ref)) == 1
    p = c.patch(f"/reservations/{ref}", {"table_id": "t_2", "party_size": 3, "starts_at_local": f"{FUT_FRI}T19:00"}, token=ada)
    assert p.status == 200 and p.json["revision"] == 2
    h = history(c, ada, ref)
    assert [e["seq"] for e in h] == [1, 2] and h[1]["event"] == "changed" and h[1]["revision"] == 2
    assert h[1]["changes"] == [{"field": "table_id", "from": "t_1", "to": "t_2"}, {"field": "party_size", "from": 2, "to": 3}]
    assert c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_FRI}T20:00"}, token=ada).json["revision"] == 3
    h = history(c, ada, ref)
    assert h[2]["changes"] == [{"field": "starts_at_local", "from": f"{FUT_FRI}T19:00", "to": f"{FUT_FRI}T20:00"}]
    assert c.post(f"/reservations/{ref}/cancel", token=ada).json["revision"] == 4
    assert c.post(f"/reservations/{ref}/cancel", token=ada).json["revision"] == 4
    h = history(c, ada, ref)
    assert [e["seq"] for e in h] == [1, 2, 3, 4] and h[3] == {**h[3], "event": "cancelled", "changes": [], "revision": 4}
    ats = [datetime.fromisoformat(e["at"]) for e in h]
    assert ats == sorted(ats)


def test_B13_B14_B55_history_visibility(c, ada, bob):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    ref = o["reference"]
    err(c.get(f"/reservations/{ref}/history", token=bob), 404, "not_found")
    err(c.get(f"/reservations/{ref}/history"), 404, "not_found")
    err(c.get(f"/reservations/{ref}/history", headers={"Authorization": "Bearer nope"}), 404, "not_found")
    err(c.get("/reservations/NOPE01/history", token=ada), 404, "not_found")
    err(c.get(f"/reservations/{ref}/decision", token=bob), 404, "not_found")
    err(c.get(f"/reservations/{ref}/decision"), 404, "not_found")
    assert c.post(f"/reservations/{ref}/cancel", token=ada).status == 200
    assert history(c, ada, ref)[-1]["event"] == "cancelled"
    d = c.get(f"/reservations/{ref}/decision", token=ada).json
    assert d == {"reference": ref, "revision": 2, "accepted_terms": d["accepted_terms"]} and check_terms(d["accepted_terms"]) is None


def test_B20_replay_records_nothing(c, ada):
    key = k()
    body = {"restaurant_id": "r_anker", "table_id": "t_1", "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}
    o = c.post("/reservations", body, token=ada, key=key).json
    assert c.post("/reservations", body, token=ada, key=key).status == 200
    assert len(history(c, ada, o["reference"])) == 1


# ============================================================ policies (B22-B42)
def test_B22_B23_B24_managers(c, ada, bob, mia):
    err(publish(c, ada, "r_anker", policy("2027-09-01")), 403, "forbidden")
    err(publish(c, bob, "r_anker", policy("2027-09-01")), 403, "forbidden")
    err(publish(c, mia, "nope", policy("2027-09-01")), 404, "not_found")
    err(c.post("/restaurants/r_anker/policies", policy("2027-09-01"), key=k()), 401, "unauthenticated")
    err(c.post("/restaurants/r_anker/policies", policy("2027-09-01"), token=mia), 400, "missing_idempotency_key")
    assert publish(c, mia, "r_anker", policy("2027-09-01")).status == 201
    # ada manages r_trio but not r_anker; mia manages both
    trio_pol = policy("2027-09-01", capacities={"q_1": 2, "q_2": 4, "q_3": 4})
    assert publish(c, ada, "r_trio", trio_pol).status == 201
    # a manager is still a stranger to other diners' bookings
    o = book_single(c, bob, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    err(c.get(f"/reservations/{o['reference']}", token=mia), 404, "not_found")
    err(c.get(f"/reservations/{o['reference']}/history", token=mia), 404, "not_found")
    err(c.get(f"/reservations/{o['reference']}/decision", token=mia), 404, "not_found")
    # fixture: absent managers -> nobody; unknown or malformed manager ids refused
    assert c.reset(stage2_fixture()).status == 204
    mia2 = c.login("mia@example.com", "mia manages")
    err(publish(c, mia2, "r_anker", policy("2027-09-01")), 403, "forbidden")
    fx = base_fixture()
    fx["restaurants"][0]["manager_user_ids"] = ["u_nobody"]
    err(c.reset(fx), 422, "validation_failed")
    fx = base_fixture()
    fx["restaurants"][0]["manager_user_ids"] = "u_mia"
    err(c.reset(fx), 400, "malformed_request")


def test_B25_B27_B28_B30_B40_publication_and_replay(c, mia):
    key = k()
    p1 = publish(c, mia, "r_anker", policy("2027-09-01"), key)
    assert p1.status == 201 and p1.json["policy_version"] == 1, p1
    assert {x: p1.json[x] for x in policy("2027-09-01")} == policy("2027-09-01")
    rp = publish(c, mia, "r_anker", policy("2027-09-01"), key)
    assert rp.status == 200 and rp.json == p1.json
    err(publish(c, mia, "r_anker", policy("2027-09-02"), key), 409, "idempotency_key_reuse")
    bad = k()
    err(publish(c, mia, "r_anker", policy("2027-09-01", slot_minutes=0), bad), 422, "validation_failed")
    p2 = publish(c, mia, "r_anker", policy("2027-10-01", slot_minutes=60), bad)              # failed key reusable, no gap
    assert p2.status == 201 and p2.json["policy_version"] == 2
    lst = c.get("/restaurants/r_anker/policies")
    assert lst.status == 200 and [p["policy_version"] for p in lst.json["policies"]] == [1, 2]
    assert lst.json["policies"][0] == p1.json and lst.json["policies"][1] == p2.json
    assert c.get("/restaurants/r_trio/policies").json == {"policies": []}
    err(c.get("/restaurants/nope/policies"), 404, "not_found")
    # another restaurant's numbering is independent; the same key on another restaurant is a different request
    p = publish(c, mia, "r_trio", policy("2027-09-01", capacities={"q_1": 2, "q_2": 4, "q_3": 4}), key)
    assert p.status == 201 and p.json["policy_version"] == 1
    assert c.get("/restaurants/r_anker").json["slot_minutes"] == 30                          # B41 detail unchanged


@pytest.mark.parametrize("name,mutate,status,code", [
    ("effective_from bad", lambda p: p.__setitem__("effective_from", "2027-02-30"), 422, "validation_failed"),
    ("effective_from type", lambda p: p.__setitem__("effective_from", 20270901), 400, "malformed_request"),
    ("slot 0", lambda p: p.__setitem__("slot_minutes", 0), 422, "validation_failed"),
    ("slot 1441", lambda p: p.__setitem__("slot_minutes", 1441), 422, "validation_failed"),
    ("duration 1441", lambda p: p.__setitem__("reservation_duration_minutes", 1441), 422, "validation_failed"),
    ("cutoff -1", lambda p: p.__setitem__("cancellation_cutoff_minutes", -1), 422, "validation_failed"),
    ("cutoff 10081", lambda p: p.__setitem__("cancellation_cutoff_minutes", 10081), 422, "validation_failed"),
    ("slot bool", lambda p: p.__setitem__("slot_minutes", True), 422, "validation_failed"),
    ("slot string", lambda p: p.__setitem__("slot_minutes", "30"), 400, "malformed_request"),
    ("slot float", lambda p: p.__setitem__("slot_minutes", 30.5), 422, "validation_failed"),
    ("hours dup weekday", lambda p: p.__setitem__("opening_hours", [{"weekday": "mon", "opens": "18:00", "closes": "23:00"}] * 2), 422, "validation_failed"),
    ("hours closes<=opens", lambda p: p.__setitem__("opening_hours", [{"weekday": "mon", "opens": "18:00", "closes": "18:00"}]), 422, "validation_failed"),
    ("hours type", lambda p: p.__setitem__("opening_hours", "none"), 400, "malformed_request"),
    ("caps missing table", lambda p: p.__setitem__("capacities", {"t_1": 2}), 422, "validation_failed"),
    ("caps extra table", lambda p: p.__setitem__("capacities", {"t_1": 2, "t_2": 4, "t_3": 4}), 422, "validation_failed"),
    ("caps 0", lambda p: p.__setitem__("capacities", {"t_1": 0, "t_2": 4}), 422, "validation_failed"),
    ("caps 101", lambda p: p.__setitem__("capacities", {"t_1": 2, "t_2": 101}), 422, "validation_failed"),
    ("caps bool", lambda p: p.__setitem__("capacities", {"t_1": True, "t_2": 4}), 422, "validation_failed"),
    ("caps type", lambda p: p.__setitem__("capacities", [2, 4]), 400, "malformed_request"),
    ("missing effective_from", lambda p: p.pop("effective_from"), 422, "validation_failed"),
    ("missing capacities", lambda p: p.pop("capacities"), 422, "validation_failed"),
    ("missing hours", lambda p: p.pop("opening_hours"), 422, "validation_failed"),
])
def test_B26_B35_B36_B37_B38_policy_validation(c, mia, name, mutate, status, code):
    p = policy("2027-09-01")
    mutate(p)
    r = publish(c, mia, "r_anker", p)
    assert r.status == status and r.code == code, (name, r)
    assert c.get("/restaurants/r_anker/policies").json == {"policies": []}
    assert publish(c, mia, "r_anker", policy("2027-09-01")).json["policy_version"] == 1    # no version consumed


def test_B29_B31_B32_B33_B34_B41_B42_selection(c, ada, mia):
    v1 = publish(c, mia, "r_anker", policy("2027-09-01", reservation_duration_minutes=120, cancellation_cutoff_minutes=60)).json
    v2 = publish(c, mia, "r_anker", policy("2027-10-01", slot_minutes=60)).json
    v3 = publish(c, mia, "r_anker", policy("2027-09-01", capacities={"t_1": 1, "t_2": 9})).json     # same date as v1, later version
    v4 = publish(c, mia, "r_anker", policy("2020-01-01", slot_minutes=15)).json                       # past effective date
    assert [v["policy_version"] for v in (v1, v2, v3, v4)] == [1, 2, 3, 4]
    assert [p["policy_version"] for p in c.get("/restaurants/r_anker/policies").json["policies"]] == [1, 2, 3, 4]
    # 2027-09-24 (Friday): candidates v1 and v3 (2027-09-01) and v4 (2020); tie on 2027-09-01 -> v3
    ex = c.get(f"/availability?restaurant_id=r_anker&date={FUT_FRI}&party_size=1&explain=true").json
    assert all(e["policy_version"] == 3 for s in ex["slots"] for e in s["explain"])
    assert ex["slots"][0]["available_table_ids"] == ["t_1", "t_2"]
    assert c.availability("r_anker", FUT_FRI, 2).json["slots"][0]["available_table_ids"] == ["t_2"]   # v3 capacity t_1 = 1
    # 2027-10-15: v2 (slot 60)
    ex = c.get(f"/availability?restaurant_id=r_anker&date={FUT_FRI4}&party_size=1&explain=true").json
    assert all(e["policy_version"] == 2 for s in ex["slots"] for e in s["explain"])
    assert [s["starts_at_local"][-5:] for s in ex["slots"]] == ["18:00", "19:00", "20:00", "21:00"]
    # before 2027-09-01: v4 (slot 15), never policy 0 now; and the fixture-era past date uses v4 too
    ex = c.get(f"/availability?restaurant_id=r_anker&date=2027-08-27&party_size=1&explain=true").json   # a Friday
    assert all(e["policy_version"] == 4 for s in ex["slots"] for e in s["explain"])
    assert ex["slots"][1]["starts_at_local"].endswith("18:15")
    # bookings carry the selected terms; the detail stays the fixture
    o = book_single(c, ada, "r_anker", "t_2", f"{FUT_FRI}T19:00", 9)
    assert o["accepted_terms"] == {**v3, "policy_version": 3, "effective_from": None} | {} if False else True
    t = o["accepted_terms"]
    assert t["policy_version"] == 3 and t["capacities"] == {"t_1": 1, "t_2": 9} and "effective_from" not in t
    assert t["opening_hours"] == v3["opening_hours"] and t["slot_minutes"] == 30
    o2 = book_single(c, ada, "r_anker", "t_2", f"{FUT_FRI4}T19:00", 4)
    assert o2["accepted_terms"]["policy_version"] == 2 and o2["ends_at"] == f"{FUT_FRI4}T20:30:00+02:00"
    assert c.get("/restaurants/r_anker").json["tables"][0]["capacity"] == 2
    # a new same-date policy supersedes for future decisions only (B33)
    v5 = publish(c, mia, "r_anker", policy("2027-09-01", capacities={"t_1": 2, "t_2": 2})).json
    assert c.get(f"/reservations/{o['reference']}/decision", token=ada).json["accepted_terms"]["policy_version"] == 3
    err(c.book(ada, k(), "r_anker", "t_1", f"{FUT_FRI}T21:30", 3), 422, "party_exceeds_capacity")
    assert c.book(ada, k(), "r_anker", "t_1", f"{FUT_FRI}T21:30", 2).json["accepted_terms"]["policy_version"] == 5
    # a restaurant without policies: policy 0 everywhere, terms equal the fixture
    o3 = book_single(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00", 2)
    assert o3["accepted_terms"] == fixture_terms("r_all") and o3["revision"] == 1


def test_B39_policy_cannot_change_structure(c, mia):
    p = policy("2027-09-01")
    p.update({"timezone": "America/New_York", "tables": [{"id": "t_9", "label": "9", "capacity": 9}], "combinable": [], "name": "X", "foo": 1})
    r = publish(c, mia, "r_anker", p)
    assert r.status == 201 and "timezone" not in r.json and "tables" not in r.json and "foo" not in r.json, r
    d = c.get("/restaurants/r_anker").json
    assert d["timezone"] == "Europe/Berlin" and [t["id"] for t in d["tables"]] == ["t_1", "t_2"] and d["name"] == "Zum Anker"


# ============================================================ terms, revisions, decisions (B43-B55)
def test_B43_B44_B45_B46_B47_terms_and_revision(c, ada, bob, mia):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    check_reservation_shape(o)
    assert o["revision"] == 1 and o["accepted_terms"] == fixture_terms()
    seed = c.get("/reservations/SEED01", token=bob).json
    assert seed["revision"] == 1 and seed["accepted_terms"]["policy_version"] == 0
    for r in (c.get(f"/reservations/{o['reference']}", token=ada).json, c.get("/reservations", token=ada).json["reservations"][0]):
        assert r["revision"] == 1 and r["accepted_terms"] == fixture_terms()
    before = {"res": c.get(f"/reservations/{o['reference']}", token=ada).json, "hist": history(c, ada, o["reference"])}
    assert publish(c, mia, "r_anker", policy("2020-01-01", reservation_duration_minutes=30)).status == 201
    assert c.get(f"/reservations/{o['reference']}", token=ada).json == before["res"]            # B47
    assert history(c, ada, o["reference"]) == before["hist"]
    # replay keeps the original revision/terms after a change (B46)
    key = k()
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{FUT_FRI}T21:30", "party_size": 2}
    first = c.post("/reservations", body, token=ada, key=key).json
    assert first["accepted_terms"]["policy_version"] == 1
    assert c.patch(f"/reservations/{first['reference']}", {"party_size": 3}, token=ada).json["revision"] == 2
    rp = c.post("/reservations", body, token=ada, key=key)
    assert rp.status == 200 and rp.json == first and rp.json["revision"] == 1


def test_B48_cancel_uses_accepted_cutoff(c, ada, mia):
    # policy 0 cutoff for r_all is 60; a booking accepted under it; then a policy with cutoff 0 -> still the accepted 60
    past = book_single(c, ada, "r_all", "a_1", f"{PAST_DAY}T12:00", 2)
    assert past["accepted_terms"]["cancellation_cutoff_minutes"] == 60
    fx = base_fixture()
    fx["restaurants"][1]["manager_user_ids"] = ["u_mia"]
    assert c.reset(fx).status == 204
    ada2 = c.login("ada@example.com", "correct horse")
    mia2 = c.login("mia@example.com", "mia manages")
    past = book_single(c, ada2, "r_all", "a_1", f"{PAST_DAY}T12:00", 2)
    pol = policy("2020-01-01", cancellation_cutoff_minutes=0, slot_minutes=15, reservation_duration_minutes=60,
                 opening_hours=[{"weekday": d, "opens": "10:00", "closes": "22:00"} for d in ALL_DAYS],
                 capacities={"a_1": 2, "a_2": 4, "a_3": 6})
    assert publish(c, mia2, "r_all", pol).status == 201
    err(c.post(f"/reservations/{past['reference']}/cancel", token=ada2), 409, "cutoff_passed")   # accepted cutoff 60 wins
    fut = book_single(c, ada2, "r_all", "a_2", f"{FUT_DAY}T12:00", 2)
    assert fut["accepted_terms"]["cancellation_cutoff_minutes"] == 0 and fut["accepted_terms"]["policy_version"] == 1
    assert c.post(f"/reservations/{fut['reference']}/cancel", token=ada2).json["revision"] == 2


def test_B49_B50_B51_B53_amendment_under_policies(c, ada, mia):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    ref = o["reference"]
    v1 = publish(c, mia, "r_anker", policy(FUT_FRI2, reservation_duration_minutes=120, cancellation_cutoff_minutes=30,
                                             capacities={"t_1": 3, "t_2": 4})).json
    # move onto the policy date: terms replaced, end time per the new duration, one revision
    p = c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_FRI2}T19:00", "party_size": 3}, token=ada)
    assert p.status == 200 and p.json["revision"] == 2 and p.json["accepted_terms"]["policy_version"] == 1, p
    assert p.json["ends_at"] == f"{FUT_FRI2}T21:00:00+02:00" and p.json["accepted_terms"]["cancellation_cutoff_minutes"] == 30
    h = history(c, ada, ref)
    assert h[0]["accepted_terms"]["policy_version"] == 0 and h[1]["accepted_terms"]["policy_version"] == 1    # B53
    assert h[1]["changes"] == [{"field": "starts_at_local", "from": f"{FUT_FRI}T19:00", "to": f"{FUT_FRI2}T19:00"},
                               {"field": "party_size", "from": 2, "to": 3}]
    # party 3 fits t_1 only under v1 (capacity 3); a move back to a policy-0 date fails the resulting validation
    err(c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_FRI}T19:00"}, token=ada), 422, "party_exceeds_capacity")
    g = c.get(f"/reservations/{ref}", token=ada).json
    assert g == p.json and len(history(c, ada, ref)) == 2                                        # failed: nothing
    # no-op keeps everything, cancelled/cutoff still enforced
    n = c.patch(f"/reservations/{ref}", {"party_size": 3, "starts_at_local": f"{FUT_FRI2}T19:00", "table_id": "t_1"}, token=ada)
    assert n.status == 200 and n.json == p.json and len(history(c, ada, ref)) == 2
    assert c.patch(f"/reservations/{ref}", {}, token=ada).json == p.json
    assert c.post(f"/reservations/{ref}/cancel", token=ada).json["revision"] == 3
    assert c.post(f"/reservations/{ref}/cancel", token=ada).json["revision"] == 3
    err(c.patch(f"/reservations/{ref}", {}, token=ada), 409, "reservation_cancelled")
    past = book_single(c, ada, "r_anker", "t_2", f"2020-09-25T19:00", 2)                        # a past Friday
    err(c.patch(f"/reservations/{past['reference']}", {}, token=ada), 409, "cutoff_passed")
    d = c.get(f"/reservations/{ref}/decision", token=ada).json
    assert d["revision"] == 3 and d["accepted_terms"]["policy_version"] == 1


def test_B52_expected_revision(c, ada, bob):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    ref = o["reference"]
    for bad in (0, -1, 1.5, "1", True, None, [1]):
        err(c.patch(f"/reservations/{ref}", {"party_size": 1, "expected_revision": bad}, token=ada), 422, "validation_failed")
    err(c.patch(f"/reservations/{ref}", {"party_size": 1, "expected_revision": 2}, token=ada), 409, "stale_revision")
    assert c.get(f"/reservations/{ref}", token=ada).json == o
    p = c.patch(f"/reservations/{ref}", {"party_size": 1, "expected_revision": 1}, token=ada)
    assert p.status == 200 and p.json["revision"] == 2
    err(c.patch(f"/reservations/{ref}", {"party_size": 2, "expected_revision": 1}, token=ada), 409, "stale_revision")
    assert c.patch(f"/reservations/{ref}", {"expected_revision": 2}, token=ada).json["revision"] == 2        # no-op
    assert c.patch(f"/reservations/{ref}", {"party_size": 1, "foo": "bar"}, token=ada).json["revision"] == 2
    # stale precedes cutoff and validation (B52): a past booking with a stale revision reports stale
    past = book_single(c, ada, "r_anker", "t_2", "2020-09-25T19:00", 2)
    err(c.patch(f"/reservations/{past['reference']}", {"party_size": 99, "expected_revision": 5}, token=ada), 409, "stale_revision")
    err(c.patch(f"/reservations/{past['reference']}", {"party_size": 99, "expected_revision": 1}, token=ada), 409, "cutoff_passed")
    # 404 before stale; cancelled before stale (Q8)
    err(c.patch(f"/reservations/{ref}", {"party_size": 1, "expected_revision": 9}, token=bob), 404, "not_found")
    assert c.post(f"/reservations/{ref}/cancel", token=ada).status == 200
    err(c.patch(f"/reservations/{ref}", {"party_size": 1, "expected_revision": 9}, token=ada), 409, "reservation_cancelled")
    # concurrency: 20 amendments sharing one expected_revision -> at most one real change
    o = book_single(c, ada, "r_all", "a_3", f"{FUT_DAY}T12:00", 2)
    fns = [(lambda i=i: Client(c.base_url, timeout=15).patch(f"/reservations/{o['reference']}",
                                                            {"party_size": 3 + i % 3, "expected_revision": 1}, token=ada)) for i in range(20)]
    res = burst(fns)
    oks = [r for r in res if not isinstance(r, BaseException) and r.status == 200]
    for r in res:
        assert not isinstance(r, BaseException) and r.status in (200, 409), r
    changed = [r for r in oks if r.json["revision"] == 2]
    assert len(changed) <= 1 and all(r.json["revision"] in (1, 2) for r in oks)
    final = c.get(f"/reservations/{o['reference']}", token=ada).json
    assert final["revision"] == (2 if changed else 1) and len(history(c, ada, o["reference"])) == final["revision"]


def test_B54_decision(c, ada):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    d = c.get(f"/reservations/{o['reference']}/decision", token=ada)
    assert d.status == 200 and d.json == {"reference": o["reference"], "revision": 1, "accepted_terms": fixture_terms()}
    assert c.post(f"/reservations/{o['reference']}/cancel", token=ada).status == 200
    assert c.get(f"/reservations/{o['reference']}/decision", token=ada).json["revision"] == 2


# ============================================================ series (B56-B76)
def adopt(c, token, ref, count=3, interval=1, key=None, **extra):
    return c.post("/series", {"anchor_reference": ref, "count": count, "interval_weeks": interval, **extra}, token=token, key=key or k())


def test_B56_B59_B60_B63_B66_B67_B68_adoption(c, ada, bob):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    before = {"res": c.get(f"/reservations/{o['reference']}", token=ada).json, "hist": history(c, ada, o["reference"])}
    key = k()
    r = adopt(c, ada, o["reference"], 4, 2, key, foo="ignored")
    assert r.status == 201, r
    s = r.json
    assert set(s) == {"series_id", "revision", "interval_weeks", "occurrences"} and s["revision"] == 1 and s["interval_weeks"] == 2
    assert [x["index"] for x in s["occurrences"]] == [0, 1, 2, 3]
    refs = [x["reference"] for x in s["occurrences"]]
    assert refs[0] == o["reference"] and len(set(refs)) == 4
    for i, x in enumerate(s["occurrences"]):
        assert x["exception"] is False and x["reservation"]["reference"] == x["reference"]
        check_reservation_shape(x["reservation"])
        exp_date = (date.fromisoformat(FUT_FRI) + timedelta(days=14 * i)).isoformat()
        assert x["reservation"]["starts_at_local"] == f"{exp_date}T19:00"
        assert x["reservation"]["party_size"] == 2 and x["reservation"]["table_ids"] == ["t_1"] and x["reservation"]["revision"] == 1
    assert s["occurrences"][0]["reservation"] == before["res"]                                  # B59
    assert history(c, ada, o["reference"]) == before["hist"]
    assert len(history(c, ada, refs[2])) == 1 and history(c, ada, refs[2])[0]["event"] == "created"
    mine = {x["reference"] for x in c.get("/reservations", token=ada).json["reservations"]}
    assert set(refs) <= mine
    third = (date.fromisoformat(FUT_FRI) + timedelta(days=28)).isoformat()
    assert c.availability("r_anker", third, 1).json["slots"][2]["available_table_ids"] == ["t_2"]
    err(c.book(bob, k(), "r_anker", "t_1", f"{third}T19:30", 1), 409, "table_unavailable")
    g = c.get(f"/series/{s['series_id']}", token=ada)
    assert g.status == 200 and g.json == s
    err(c.get(f"/series/{s['series_id']}", token=bob), 404, "not_found")
    err(c.get(f"/series/{s['series_id']}"), 404, "not_found")
    err(c.get("/series/nope", token=ada), 404, "not_found")
    rp = adopt(c, ada, o["reference"], 4, 2, key, foo="ignored")
    assert rp.status == 200 and rp.json == s


def test_B57_B58_adoption_errors(c, ada, bob):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    ref = o["reference"]
    err(c.post("/series", {"anchor_reference": ref, "count": 3, "interval_weeks": 1}, key=k()), 401, "unauthenticated")
    err(c.post("/series", {"anchor_reference": ref, "count": 3, "interval_weeks": 1}, token=ada), 400, "missing_idempotency_key")
    for body in ({"count": 3, "interval_weeks": 1}, {"anchor_reference": ref, "interval_weeks": 1}, {"anchor_reference": ref, "count": 3}):
        err(c.post("/series", body, token=ada, key=k()), 422, "validation_failed")
    for cnt in (1, 13, 0, True, "3", 2.5, None):
        err(adopt(c, ada, ref, cnt, 1), 422, "validation_failed")
    for iv in (0, 5, True, "1", 1.5, None):
        err(adopt(c, ada, ref, 3, iv), 422, "validation_failed")
    err(c.post("/series", {"anchor_reference": 5, "count": 3, "interval_weeks": 1}, token=ada, key=k()), 400, "malformed_request")
    err(adopt(c, ada, "NOPE01"), 404, "not_found")
    err(adopt(c, ada, "SEED01"), 404, "not_found")                                              # bob's
    err(adopt(c, ada, ref, 99, 1), 422, "validation_failed")                                     # 422 before 404? the anchor is fine
    err(adopt(c, ada, "NOPE01", 99, 1), 422, "validation_failed")                                # fields before the anchor (Q14)
    past = book_single(c, ada, "r_anker", "t_2", "2020-09-25T19:00", 2)
    err(adopt(c, ada, past["reference"]), 409, "cutoff_passed")
    canc = book_single(c, ada, "r_anker", "t_2", f"{FUT_FRI}T21:30", 2)
    assert c.post(f"/reservations/{canc['reference']}/cancel", token=ada).status == 200
    err(adopt(c, ada, canc["reference"]), 409, "reservation_cancelled")
    assert adopt(c, ada, ref, 2, 1).status == 201
    err(adopt(c, ada, ref, 2, 1), 409, "already_in_series")
    sibling = c.get("/reservations", token=ada).json["reservations"]
    sib = next(x for x in sibling if x["starts_at_local"] == f"{FUT_FRI2}T19:00")
    err(adopt(c, ada, sib["reference"], 2, 1), 409, "already_in_series")
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 4


def test_B61_B62_B64_B65_occurrence_rules(c, ada, bob, mia):
    # a closed weekday for occurrence 1, a busy table for occurrence 2: the first failing index decides, nothing survives
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    assert c.book(bob, k(), "r_anker", "t_1", f"{FUT_FRI3}T19:00", 2).status == 201           # blocker for index 2
    assert publish(c, mia, "r_anker", policy(FUT_FRI2, opening_hours=[{"weekday": "mon", "opens": "18:00", "closes": "23:00"}])).status == 201
    key = k()
    err(adopt(c, ada, o["reference"], 3, 1, key), 422, "outside_opening_hours")                 # index 1 fails first
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 1 and len(history(c, ada, o["reference"])) == 1
    assert c.get(f"/reservations/{o['reference']}", token=ada).json["revision"] == 1
    # the key is still a first use; now make occurrence 1 valid (policy for FUT_FRI2 with Fridays, duration 120)
    assert publish(c, mia, "r_anker", policy(FUT_FRI2, reservation_duration_minutes=120)).status == 201     # v2, same date -> wins
    err(adopt(c, ada, o["reference"], 3, 1, key), 409, "table_unavailable")                     # index 2: bob's booking
    assert c.post(f"/reservations/{[x for x in c.get('/reservations', token=bob).json['reservations'] if x['starts_at_local'].startswith(FUT_FRI3)][0]['reference']}/cancel", token=bob).status == 200
    r = adopt(c, ada, o["reference"], 3, 1, key)
    assert r.status == 201, r
    occ = r.json["occurrences"]
    assert occ[0]["reservation"]["accepted_terms"]["policy_version"] == 0 and occ[0]["reservation"]["ends_at"].endswith("20:30:00+02:00")
    assert occ[1]["reservation"]["accepted_terms"]["policy_version"] == 2 and occ[1]["reservation"]["ends_at"] == f"{FUT_FRI2}T21:00:00+02:00"
    assert occ[2]["reservation"]["accepted_terms"]["policy_version"] == 2
    # DST: a weekly series at 02:30 crossing the spring-forward Sunday is refused as a whole
    fx = base_fixture()
    assert c.reset(fx).status == 204
    ada2 = c.login("ada@example.com", "correct horse")
    anchor = book_single(c, ada2, "r_anker", "t_1", "2027-03-21T02:30", 2)                      # Sunday before the 2027 gap
    err(adopt(c, ada2, anchor["reference"], 2, 1), 422, "invalid_local_time")
    assert len(c.get("/reservations", token=ada2).json["reservations"]) == 1
    fall = book_single(c, ada2, "r_anker", "t_2", "2027-10-24T02:30", 2)
    r = adopt(c, ada2, fall["reference"], 2, 1)
    assert r.status == 201 and r.json["occurrences"][1]["reservation"]["starts_at"] == "2027-10-31T02:30:00+02:00"   # first occurrence


def test_B69_B70_B71_B72_B74_series_revision_and_exceptions(c, ada):
    o = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    key = k()
    s = adopt(c, ada, o["reference"], 3, 1, key).json
    sid, refs = s["series_id"], [x["reference"] for x in s["occurrences"]]
    assert c.patch(f"/reservations/{refs[1]}", {"party_size": 2}, token=ada).status == 200       # no-op
    g = c.get(f"/series/{sid}", token=ada).json
    assert g["revision"] == 1 and [x["exception"] for x in g["occurrences"]] == [False, False, False]
    err(c.patch(f"/reservations/{refs[1]}", {"party_size": 99}, token=ada), 422, "party_exceeds_capacity")
    assert c.get(f"/series/{sid}", token=ada).json["revision"] == 1
    p = c.patch(f"/reservations/{refs[1]}", {"party_size": 1}, token=ada)
    assert p.status == 200 and p.json["revision"] == 2
    g = c.get(f"/series/{sid}", token=ada).json
    assert g["revision"] == 2 and [x["exception"] for x in g["occurrences"]] == [False, True, False]
    assert g["occurrences"][1]["reservation"] == p.json and g["occurrences"][1]["reference"] == refs[1]
    assert c.post(f"/reservations/{refs[2]}/cancel", token=ada).status == 200
    g = c.get(f"/series/{sid}", token=ada).json
    assert g["revision"] == 3 and [x["exception"] for x in g["occurrences"]] == [False, True, False]
    assert g["occurrences"][2]["reservation"]["status"] == "cancelled" and len(g["occurrences"]) == 3
    assert c.post(f"/reservations/{refs[2]}/cancel", token=ada).status == 200
    assert c.get(f"/series/{sid}", token=ada).json["revision"] == 3
    # the anchor: PATCH marks it, cancel keeps siblings
    assert c.patch(f"/reservations/{refs[0]}", {"table_id": "t_2"}, token=ada).json["revision"] == 2
    g = c.get(f"/series/{sid}", token=ada).json
    assert g["revision"] == 4 and g["occurrences"][0]["exception"] is True
    assert c.post(f"/reservations/{refs[0]}/cancel", token=ada).status == 200
    g = c.get(f"/series/{sid}", token=ada).json
    assert g["revision"] == 5 and g["occurrences"][1]["reservation"]["status"] == "confirmed"
    rp = adopt(c, ada, o["reference"], 3, 1, key)
    assert rp.status == 200 and rp.json == s                                                       # B74
    assert c.get(f"/series/{sid}", token=ada).json["revision"] == 5


def test_B75_series_key_is_its_own_path(c, ada):
    key = k()
    body = {"restaurant_id": "r_anker", "table_id": "t_1", "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}
    o = c.post("/reservations", body, token=ada, key=key).json
    r = adopt(c, ada, o["reference"], 2, 1, key)
    assert r.status == 201
    err(adopt(c, ada, o["reference"], 3, 1, key), 409, "idempotency_key_reuse")
    assert c.post("/reservations", body, token=ada, key=key).status == 200


# ============================================================ combined-table history (B77, B78)
def test_B77_B78_pair_history_and_capacity(c, ada, mia):
    o = c.post("/reservations", {"restaurant_id": "r_trio", "table_ids": ["q_2", "q_1"], "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 5}, token=ada, key=k()).json
    ref = o["reference"]
    h = history(c, ada, ref)
    assert h[0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["q_1", "q_2"]}
    assert c.patch(f"/reservations/{ref}", {"table_ids": ["q_2", "q_1"]}, token=ada).json["revision"] == 1     # reversed: no-op
    assert len(history(c, ada, ref)) == 1
    p = c.patch(f"/reservations/{ref}", {"table_ids": ["q_3"], "party_size": 3}, token=ada)
    assert p.status == 200 and p.json["revision"] == 2
    assert history(c, ada, ref)[1]["changes"] == [{"field": "table_ids", "from": ["q_1", "q_2"], "to": ["q_3"]},
                                                  {"field": "party_size", "from": 5, "to": 3}]
    p = c.patch(f"/reservations/{ref}", {"table_ids": ["q_2", "q_3"]}, token=ada)
    assert history(c, ada, ref)[2]["changes"] == [{"field": "table_ids", "from": ["q_3"], "to": ["q_2", "q_3"]}]
    single = book_single(c, ada, "r_trio", "q_1", f"{FUT_FRI}T21:30", 2)
    assert c.patch(f"/reservations/{single['reference']}", {"table_id": "q_2"}, token=ada).status == 200
    assert history(c, ada, single["reference"])[1]["changes"] == [{"field": "table_id", "from": "q_1", "to": "q_2"}]
    # policy capacities apply to pairs: 1+1 seats 2, not 3
    pol = policy(FUT_FRI2, capacities={"q_1": 1, "q_2": 1, "q_3": 4})
    assert publish(c, mia, "r_trio", pol).status == 201
    err(c.post("/reservations", {"restaurant_id": "r_trio", "table_ids": ["q_1", "q_2"], "starts_at_local": f"{FUT_FRI2}T19:00", "party_size": 3}, token=ada, key=k()), 422, "party_exceeds_capacity")
    ok = c.post("/reservations", {"restaurant_id": "r_trio", "table_ids": ["q_1", "q_2"], "starts_at_local": f"{FUT_FRI2}T19:00", "party_size": 2}, token=ada, key=k())
    assert ok.status == 201 and ok.json["accepted_terms"]["capacities"] == {"q_1": 1, "q_2": 1, "q_3": 4}


# ============================================================ collective moves (B79-B85)
def test_B79_B80_B81_B82_B83_B84_B85_moves_under_policies(c, ada, mia):
    a = book_single(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
    b = book_single(c, ada, "r_anker", "t_2", f"{FUT_FRI}T19:00", 2)
    s = adopt(c, ada, a["reference"], 2, 1).json
    sib = s["occurrences"][1]["reference"]
    assert publish(c, mia, "r_anker", policy(FUT_FRI2, reservation_duration_minutes=120, cancellation_cutoff_minutes=30)).status == 201
    key = k()
    body = {"moves": [{"reference": a["reference"], "party_size": 1, "expected_revision": 1},
                      {"reference": b["reference"]},
                      {"reference": sib, "table_id": "t_2"}]}
    r = c.post("/reservation-moves", body, token=ada, key=key)
    assert r.status == 201, r
    out = r.json["reservations"]
    assert [x["revision"] for x in out] == [2, 1, 2]
    assert out[2]["accepted_terms"]["policy_version"] == 1 and out[2]["ends_at"] == f"{FUT_FRI2}T21:00:00+02:00"
    assert out[0]["accepted_terms"]["policy_version"] == 0
    assert len(history(c, ada, b["reference"])) == 1 and history(c, ada, a["reference"])[1]["changes"] == [{"field": "party_size", "from": 2, "to": 1}]
    g = c.get(f"/series/{s['series_id']}", token=ada).json
    assert g["revision"] == 2 and [x["exception"] for x in g["occurrences"]] == [True, True]          # once per series
    rp = c.post("/reservation-moves", body, token=ada, key=key)
    assert rp.status == 200 and rp.json == r.json and c.get(f"/series/{s['series_id']}", token=ada).json["revision"] == 2
    # stale and invalid expected_revision per item; a failed batch changes nothing
    err(c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "party_size": 1, "expected_revision": 2}]}, token=ada, key=k()), 409, "stale_revision")
    err(c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "party_size": 1, "expected_revision": 0}]}, token=ada, key=k()), 422, "validation_failed")
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "party_size": 1}, {"reference": b["reference"], "party_size": 99}]}, token=ada, key=k()), 422, "party_exceeds_capacity")
    assert c.get(f"/reservations/{b['reference']}", token=ada).json["revision"] == 1
    assert c.get(f"/series/{s['series_id']}", token=ada).json["revision"] == 2


# ============================================================ upgrade (B76)
@pytest.fixture(scope="session")
def older_exports(request):
    """Exports from the accepted stage-1 and stage-2 services (--stage1-base-url / --stage2-base-url)."""
    out = {}
    for stage, opt in ((1, "--stage1-base-url"), (2, "--stage2-base-url")):
        url = request.config.getoption(opt)
        if not url:
            continue
        s = Client(url.rstrip("/"))
        fx = stage1_fixture() if stage == 1 else stage2_fixture()
        assert s.reset(fx).status == 204
        ada = s.login("ada@example.com", "correct horse")
        key = f"k-up{stage}-" + uuid.uuid4().hex[:8]
        o = s.book(ada, key, "r_anker", "t_1", f"{FUT_FRI}T19:00", 2)
        assert o.status == 201, o
        extra = None
        if stage == 2:
            extra = s.post("/reservations", {"restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"], "starts_at_local": f"{FUT_FRI}T21:30", "party_size": 5}, token=ada, key=k()).json
        canc = s.book(ada, k(), "r_all", "a_2", f"{FUT_DAY}T14:00", 2).json
        assert s.post(f"/reservations/{canc['reference']}/cancel", token=ada).status == 200
        out[stage] = {"export": s.export().json, "ada": ada, "key": key, "booking": o.json, "pair": extra, "cancelled": canc}
    if not out:
        pytest.skip("no --stage1-base-url / --stage2-base-url")
    return out


@pytest.mark.parametrize("stage", [1, 2])
def test_B76_upgrade_from_older_exports(c, mia, older_exports, stage):
    if stage not in older_exports:
        pytest.skip(f"no stage-{stage} source")
    src = older_exports[stage]
    assert c.import_(src["export"]).status == 204
    ada = src["ada"]
    booking = src["booking"]
    g = c.get(f"/reservations/{booking['reference']}", token=ada)
    assert g.status == 200 and g.json["revision"] == 1 and g.json["accepted_terms"] == fixture_terms(), g
    for f in ("reservation_id", "reference", "starts_at", "ends_at", "created_at", "party_size"):
        assert g.json[f] == booking[f]
    h = history(c, ada, booking["reference"])
    assert len(h) == 1 and h[0]["event"] == "created" and h[0]["revision"] == 1 and h[0]["accepted_terms"]["policy_version"] == 0
    assert h[0]["changes"][0] == {"field": "table_id", "from": None, "to": "t_1"}
    hc = history(c, ada, src["cancelled"]["reference"])
    assert [e["event"] for e in hc] == ["created", "cancelled"] and c.get(f"/reservations/{src['cancelled']['reference']}/decision", token=ada).json["revision"] == 2
    if src["pair"]:
        hp = history(c, ada, src["pair"]["reference"])
        assert hp[0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]}
    # the original retry still replays the stage-N body verbatim (no revision/terms in the stored response)
    body = {"restaurant_id": "r_anker", "table_id": "t_1", "starts_at_local": f"{FUT_FRI}T19:00", "party_size": 2}
    rp = c.post("/reservations", body, token=ada, key=src["key"])
    assert rp.status == 200 and rp.json == booking
    # adoption works on the imported booking; policies can be published on the imported restaurant by a manager
    r = adopt(c, ada, booking["reference"], 2, 1)
    assert r.status == 201 and r.json["occurrences"][1]["reservation"]["accepted_terms"]["policy_version"] == 0
    fx_managers = c.get("/restaurants/r_anker").json.get("manager_user_ids", [])
    assert fx_managers == []
    mia_after = c.login("mia@example.com", "mia manages")                                          # the import replaced the tokens
    err(publish(c, mia_after, "r_anker", policy("2027-09-01")), 403, "forbidden")                   # no managers in old fixtures
    assert c.get(f"/restaurants/r_anker/policies").json == {"policies": []}
    exp3 = c.export().json
    assert c.import_(exp3).status == 204
    assert c.get(f"/series/{r.json['series_id']}", token=ada).json == r.json


# ============================================================ stage-2 leftovers O-12, O-13
def test_O12_import_refusals(c, ada, bob):
    from test_hardening import _records, _reservation_records, _snapshot, _top_key_of, _walk
    key = k()
    assert c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00", 2).status == 201
    exp = c.export().json
    before = _snapshot(c, [ada, bob])
    cases = []
    for bad_id in ("", "x" * 65, 5, None):
        e = copy.deepcopy(exp)
        _records(e["state"], email="ada@example.com")[0][2]["id"] = bad_id
        cases.append((f"user id {bad_id!r}", e))
    for which, locate in (("users", lambda s: _records(s, email="ada@example.com")[0][2]),
                          ("restaurants", lambda s: _records(s, id="r_anker")[0][2])):
        e = copy.deepcopy(exp)
        del e["state"][_top_key_of(e["state"], locate(e["state"]))]
        cases.append((f"missing exactly {which}", e))
        e = copy.deepcopy(exp)
        e["state"][_top_key_of(e["state"], locate(e["state"]))] = None
        cases.append((f"{which} is null", e))
    e = copy.deepcopy(exp)
    found = False
    for parent, key_, v in list(_walk(e["state"])):
        if isinstance(v, dict) and "status" in v and isinstance(v["status"], int) and not isinstance(v["status"], bool) and key in json.dumps(v):
            v["status"] = 404
            found = True
            break
    if found:
        cases.append(("receipt status 404", e))
    e = copy.deepcopy(exp)
    r = _records(e["state"], id="r_anker")[0][2]
    r["opening_hours"] = r["opening_hours"] + [dict(r["opening_hours"][0])]
    cases.append(("duplicate weekday in imported restaurant", e))
    e = copy.deepcopy(exp)
    _records(e["state"], id="r_anker")[0][2]["slot_minutes"] = True
    cases.append(("number field holds a boolean", e))
    e = copy.deepcopy(exp)
    _records(e["state"], id="r_anker")[0][2]["slot_minutes"] = "30"
    cases.append(("number field holds a string", e))
    e = copy.deepcopy(exp)
    for _, _, rec in _reservation_records(e["state"], reference="SEED01"):
        rec["party_size"] = True
    cases.append(("party_size holds a boolean", e))
    e = copy.deepcopy(exp)
    for parent, key_, v in list(_walk(e["state"])):
        if isinstance(v, bool) and parent is not None:
            parent[key_] = 1
            cases.append((f"boolean field {key_!r} holds a number", e))
            break
    for name, doc in cases:
        r = c.import_(doc)
        assert r.status == 422 and r.code == "validation_failed", (name, r)
        assert _snapshot(c, [ada, bob]) == before, name
    assert len(cases) >= 11, [n for n, _ in cases]
    assert c.import_(exp).status == 204


def test_O13_party_size_2_pow_53(c, ada):
    body = {"restaurant_id": "r_all", "table_id": "a_3", "starts_at_local": f"{FUT_DAY}T12:00"}
    for v in (2 ** 53, 2 ** 53 - 1, 2 ** 53 + 1):
        r = c.post("/reservations", {**body, "party_size": v}, token=ada, key=k())
        assert r.status == 422 and r.code in ("party_exceeds_capacity", "validation_failed"), (v, r)
    assert c.get("/reservations", token=ada).json["reservations"] == []
