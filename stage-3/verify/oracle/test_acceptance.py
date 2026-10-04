"""Stage-1 acceptance suite (Oracle). Exercises the running service through HTTP only.

Test names carry the master-ledger clause ids they cover (evidence/stage-1/ledger.md, C1.<n>) and follow
the rulings R-1..R-24 (evidence/stage-1/rulings.md). Every response is checked for < 500 (C1.46) by the
client itself.
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from client import Client, burst
from fixtures import (ADA, ALL_DAYS, BERLIN_FALL, BERLIN_SPRING, FUT_DAY, FUT_DAY2, FUT_FRI, FUT_THU, FUT_WED,
                      NY_FALL, NY_SPRING, PAST_DAY, PAST_THU, base_fixture, other_fixture)

RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")
LOCAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
REF = re.compile(r"^[A-Z0-9]{6,12}$")
RES_FIELDS = {"reservation_id", "reference", "restaurant_id", "table_id", "party_size", "status",
              "starts_at_local", "starts_at", "ends_at", "created_at"}


def k() -> str:
    return "k-" + uuid.uuid4().hex


def err(r, status, code):
    assert r.status == status, f"expected {status} {code}, got {r}"
    assert isinstance(r.json, dict) and isinstance(r.json.get("error"), dict), r
    assert r.json["error"].get("code") == code, r
    assert isinstance(r.json["error"].get("message"), str), r
    assert r.headers.get("content-type", "").startswith("application/json"), r.headers


def check_reservation_shape(o: dict):
    assert set(o) >= RES_FIELDS, o
    assert isinstance(o["reservation_id"], str) and len(o["reservation_id"]) <= 64
    assert REF.match(o["reference"]), o["reference"]
    assert o["status"] in ("confirmed", "cancelled")
    assert LOCAL.match(o["starts_at_local"])
    for f in ("starts_at", "ends_at", "created_at"):
        assert RFC3339.match(o[f]), (f, o[f])
    assert isinstance(o["party_size"], int) and not isinstance(o["party_size"], bool)


def book_all(c: Client, token: str, restaurant: str, table: str, local: str, party=2):
    r = c.book(token, k(), restaurant, table, local, party)
    assert r.status == 201, r
    return r.json


# ============================================================ §3 runtime contract
def test_C1_11_health(c):
    r = c.get("/health")
    assert r.status == 200 and r.json == {"status": "ok"}, r


def test_C1_12_C1_13_reset_replaces_everything(base_url):
    c = Client(base_url)
    assert c.reset(base_fixture()).status == 204
    tok = c.signup("new@example.com").json["token"]
    ref = book_all(c, tok, "r_all", "a_1", f"{FUT_DAY}T12:00")["reference"]
    r = c.request("POST", "/_test/reset", json_body=other_fixture(), timeout=10)
    assert r.status == 204 and r.text == "", r
    assert [x["id"] for x in c.get("/restaurants").json["restaurants"]] == ["r_other"]
    err(c.get("/reservations", token=tok), 401, "unauthenticated")
    err(c.post("/auth/login", {"email": "new@example.com", "password": "long enough password"}), 401, "unauthenticated")
    zed = c.login("zed@example.com", "zed password")
    err(c.get(f"/reservations/{ref}", token=zed), 404, "not_found")
    assert c.reset(base_fixture()).status == 204
    assert [x["id"] for x in c.get("/restaurants").json["restaurants"]] == ["r_anker", "r_all", "r_ny", "r_trio"]


def test_C1_14_content_type(c, ada):
    for r in (c.get("/restaurants"), c.get("/reservations", token=ada), c.get("/restaurants/nope"),
              c.book(ada, k(), "r_all", "a_1", f"{FUT_DAY}T12:00")):
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/json"), (r.status, ct)
        assert "charset" not in ct or "utf-8" in ct.lower(), ct


def test_C1_15_C1_80_timestamps_and_create_shape(c, ada):
    o = book_all(c, ada, "r_anker", "t_1", f"{FUT_THU}T19:00")
    check_reservation_shape(o)
    assert o["restaurant_id"] == "r_anker" and o["table_id"] == "t_1" and o["party_size"] == 2
    assert o["status"] == "confirmed" and o["starts_at_local"] == f"{FUT_THU}T19:00"
    assert o["starts_at"] == f"{FUT_THU}T19:00:00+02:00" and o["ends_at"] == f"{FUT_THU}T20:30:00+02:00"


def test_C1_16_unknown_body_fields_ignored(c, ada):
    r = c.book(ada, k(), "r_all", "a_1", f"{FUT_DAY}T12:00", 2, foo="bar", nested={"x": [1]})
    assert r.status == 201, r
    r = c.post("/auth/signup", {"email": "x1@example.com", "password": "long enough", "display_name": "X", "role": "admin"})
    assert r.status == 201, r
    r = c.patch(f"/reservations/{book_all(c, ada, 'r_all', 'a_2', f'{FUT_DAY}T12:00')['reference']}",
                {"foo": 1}, token=ada)
    assert r.status == 200, r


def test_C1_17_unknown_query_ignored(c):
    a = c.availability("r_anker", FUT_THU, 4)
    b = c.availability("r_anker", FUT_THU, 4, "&foo=bar&page=2")
    assert a.status == 200 and a.json == b.json


def test_C1_18_ids_are_short_strings(c, ada):
    for x in c.get("/restaurants").json["restaurants"]:
        assert isinstance(x["id"], str) and len(x["id"]) <= 64
    o = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")
    assert isinstance(o["reservation_id"], str) and 0 < len(o["reservation_id"]) <= 64
    s = c.signup("ids@example.com").json
    assert isinstance(s["user_id"], str) and 0 < len(s["user_id"]) <= 64


def test_C1_18_fixture_id_too_long_rejected(c):
    fx = base_fixture()
    fx["restaurants"][0]["id"] = "r" * 65
    err(c.reset(fx), 422, "validation_failed")
    assert [x["id"] for x in c.get("/restaurants").json["restaurants"]] == ["r_anker", "r_all", "r_ny", "r_trio"]


def test_C1_26_C1_70_fixture_shape_echoed(c):
    r = c.get("/restaurants/r_anker")
    assert r.status == 200
    fx = base_fixture()["restaurants"][0]
    for f in ("id", "name", "timezone", "slot_minutes", "reservation_duration_minutes",
              "cancellation_cutoff_minutes", "opening_hours", "tables"):
        assert r.json[f] == fx[f], (f, r.json.get(f))
    err(c.get("/restaurants/nope"), 404, "not_found")


@pytest.mark.parametrize("mutate,bid", [
    (lambda fx: fx["restaurants"][0]["opening_hours"].__setitem__(0, {"weekday": "Thursday", "opens": "18:00", "closes": "23:00"}), "B48"),
    (lambda fx: fx["restaurants"][0]["opening_hours"].__setitem__(0, {"weekday": "thu", "opens": "6pm", "closes": "23:00"}), "B49"),
    (lambda fx: fx["restaurants"][0]["opening_hours"].__setitem__(0, {"weekday": "thu", "opens": "23:00", "closes": "01:00"}), "B50"),
])
def test_C1_27_C1_28_reset_rejects_bad_hours(c, mutate, bid):
    fx = base_fixture()
    mutate(fx)
    err(c.reset(fx), 422, "validation_failed")


def test_C1_28_closes_2400_is_end_of_day(c, ada):                                        # R-8
    fx = base_fixture()
    fx["restaurants"][1]["opening_hours"] = [{"weekday": d, "opens": "22:00", "closes": "24:00"} for d in ALL_DAYS]
    assert c.reset(fx).status == 204
    sl = [s["starts_at_local"][-5:] for s in c.availability("r_all", FUT_DAY, 1).json["slots"]]
    assert sl == ["22:00", "22:15", "22:30", "22:45", "23:00"], sl
    tok = c.login("ada@example.com", "correct horse")
    assert c.book(tok, k(), "r_all", "a_1", f"{FUT_DAY}T23:00").status == 201
    err(c.book(tok, k(), "r_all", "a_1", f"{FUT_DAY}T23:15"), 422, "outside_opening_hours")


@pytest.mark.parametrize("mutate", [
    lambda fx: fx["users"].append({"id": "u_dup", "email": "ADA@example.com", "password": "xxxxxxxx", "display_name": "D"}),
    lambda fx: fx["users"].append({"id": "u_ada", "email": "x@example.com", "password": "xxxxxxxx", "display_name": "D"}),
    lambda fx: fx["restaurants"].append(dict(fx["restaurants"][0])),
    lambda fx: fx["restaurants"][0]["tables"].append({"id": "t_1", "label": "dup", "capacity": 2}),
    lambda fx: fx["restaurants"][0]["opening_hours"].append({"weekday": "thu", "opens": "10:00", "closes": "12:00"}),
    lambda fx: fx["reservations"].append({"id": "res_seed", "reference": "SEED02", "user_id": "u_bob", "restaurant_id": "r_anker",
                                          "table_id": "t_1", "starts_at_local": f"{FUT_THU}T18:00", "party_size": 2}),
    lambda fx: fx["reservations"].append({"id": "res_x", "reference": "SEED01", "user_id": "u_bob", "restaurant_id": "r_anker",
                                          "table_id": "t_1", "starts_at_local": f"{FUT_THU}T18:00", "party_size": 2}),
    lambda fx: fx["reservations"].append({"id": "res_x", "reference": "SEEDX1", "user_id": "u_nobody", "restaurant_id": "r_anker",
                                          "table_id": "t_1", "starts_at_local": f"{FUT_THU}T18:00", "party_size": 2}),
    lambda fx: fx["reservations"].append({"id": "res_x", "reference": "SEEDX1", "user_id": "u_bob", "restaurant_id": "r_anker",
                                          "table_id": "a_1", "starts_at_local": f"{FUT_THU}T18:00", "party_size": 2}),
    lambda fx: fx["reservations"].append({"id": "res_x", "reference": "SEEDX1", "user_id": "u_bob", "restaurant_id": "r_nope",
                                          "table_id": "t_1", "starts_at_local": f"{FUT_THU}T18:00", "party_size": 2}),
])
def test_C1_12_C1_30_reset_referential_checks(c, mutate):                                   # R-20
    fx = base_fixture()
    mutate(fx)
    err(c.reset(fx), 422, "validation_failed")
    assert [x["id"] for x in c.get("/restaurants").json["restaurants"]] == ["r_anker", "r_all", "r_ny", "r_trio"]
    assert c.login("bob@example.com", "bob secret 1")


def test_C1_30_seeded_rows_not_revalidated(c):                                               # R-20
    fx = base_fixture()
    fx["reservations"].append({"id": "res_off", "reference": "OFFGRD", "user_id": "u_bob", "restaurant_id": "r_anker",
                               "table_id": "t_1", "starts_at_local": f"{FUT_WED}T03:07", "party_size": 9})
    assert c.reset(fx).status == 204
    tok = c.login("bob@example.com", "bob secret 1")
    assert c.get("/reservations/OFFGRD", token=tok).json["party_size"] == 9


def test_C1_12_C1_34_reset_malformed(c):
    err(c.request("POST", "/_test/reset", raw=b"{not json", timeout=10), 400, "malformed_request")
    err(c.request("POST", "/_test/reset", raw=b"[]", timeout=10), 400, "malformed_request")


def test_C1_29_seeded_users_login(c):
    r = c.post("/auth/login", {"email": ADA["email"], "password": ADA["password"]})
    assert r.status == 200 and r.json["user_id"] == "u_ada" and r.json["display_name"] == "Ada", r
    assert isinstance(r.json["token"], str) and r.json["token"]


def test_C1_30_C1_31_seeded_reservation(c, bob, ada):
    r = c.get("/reservations/SEED01", token=bob)
    assert r.status == 200, r
    check_reservation_shape(r.json)
    assert r.json["reservation_id"] == "res_seed" and r.json["status"] == "confirmed"
    assert r.json["table_id"] == "t_2" and r.json["starts_at_local"] == f"{FUT_THU}T18:00"
    err(c.get("/reservations/SEED01", token=ada), 404, "not_found")
    slot = c.availability("r_anker", FUT_THU, 2).json["slots"][0]
    assert slot["starts_at_local"] == f"{FUT_THU}T18:00" and slot["available_table_ids"] == ["t_1"]
    # any calendar date in a fixture
    fx = base_fixture()
    fx["reservations"].append({"id": "res_old", "reference": "OLD001", "user_id": "u_ada", "restaurant_id": "r_anker",
                               "table_id": "t_1", "starts_at_local": f"{PAST_THU}T18:00", "party_size": 2})
    fx["reservations"].append({"id": "res_ts", "reference": "OLDTS1", "user_id": "u_ada", "restaurant_id": "r_anker",
                               "table_id": "t_2", "starts_at_local": f"{PAST_THU}T18:00", "party_size": 2,
                               "created_at": "2020-01-02T03:04:05+01:00"})
    assert c.reset(fx).status == 204
    tok = c.login("ada@example.com", "correct horse")
    assert c.get("/reservations/OLD001", token=tok).status == 200
    ts = c.get("/reservations/OLDTS1", token=tok).json["created_at"]                      # R-16, R-21
    assert ts == "2020-01-02T02:04:05+00:00", ts


def test_C1_31_past_booking_allowed_cutoff_applies(c, ada):
    r = c.book(ada, k(), "r_anker", "t_1", f"{PAST_THU}T18:00")
    assert r.status == 201, r
    ref = r.json["reference"]
    err(c.post(f"/reservations/{ref}/cancel", token=ada), 409, "cutoff_passed")
    err(c.patch(f"/reservations/{ref}", {"party_size": 1}, token=ada), 409, "cutoff_passed")
    assert c.get(f"/reservations/{ref}", token=ada).json["status"] == "confirmed"


# ============================================================ §5 errors
def test_C1_32_C1_33_error_envelope(c, ada):
    err(c.get("/restaurants/nope"), 404, "not_found")
    err(c.get("/reservations"), 401, "unauthenticated")
    err(c.post("/reservations", {}, token=ada), 400, "missing_idempotency_key")


def test_C1_38_C1_32_unknown_route_and_method(c, ada):                                     # R-9, R-25
    err(c.get("/nope"), 404, "not_found")
    for p in ("/reservations/", "/restaurants/", "/health/", "//health", "/reservations//cancel",
              "/reservations/SEED01/", "/reservation-moves/", "/_test/export/", "/availability/"):
        r = c.request("GET", p, token=ada)
        err(r, 404, "not_found")
    err(c.request("POST", "/reservations/", json_body={"x": 1}, token=ada, key=k()), 404, "not_found")
    err(c.post("/reservations//cancel", token=ada), 404, "not_found")
    err(c.get("/reservations/x/y/z", token=ada), 404, "not_found")
    err(c.request("DELETE", "/reservations", token=ada), 405, "method_not_allowed")
    err(c.request("PUT", "/health"), 405, "method_not_allowed")
    err(c.post("/restaurants", {}), 405, "method_not_allowed")


def test_C1_34_C1_42_C1_44_malformed_request(c, ada):
    err(c.request("POST", "/reservations", raw=b"not json", token=ada, key=k()), 400, "malformed_request")
    err(c.request("POST", "/reservations", raw=b"[1,2]", token=ada, key=k()), 400, "malformed_request")
    err(c.request("POST", "/reservations", raw=b'"str"', token=ada, key=k()), 400, "malformed_request")
    err(c.book(ada, k(), 5, "a_1", f"{FUT_DAY}T12:00"), 400, "malformed_request")
    err(c.book(ada, k(), "r_all", 1, f"{FUT_DAY}T12:00"), 400, "malformed_request")
    err(c.book(ada, k(), "r_all", "a_1", 20270615), 400, "malformed_request")
    err(c.book(ada, k(), "r_all", None, f"{FUT_DAY}T12:00"), 400, "malformed_request")          # R-2
    err(c.book(ada, k(), None, "a_1", f"{FUT_DAY}T12:00"), 400, "malformed_request")
    err(c.book(ada, k(), "r_all", "a_1", None), 400, "malformed_request")
    # R-19: wrong type (400) is reported before a missing field (422)
    err(c.post("/reservations", {"table_id": 5}, token=ada, key=k()), 400, "malformed_request")
    err(c.post("/auth/signup", {"email": 1, "password": "long enough", "display_name": "X"}), 400, "malformed_request")
    err(c.request("POST", "/auth/login", raw=b"{"), 400, "malformed_request")


def test_C1_35_C1_60_C1_78_missing_idempotency_key(c, ada):
    body = {"restaurant_id": "r_all", "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2}
    err(c.post("/reservations", body, token=ada), 400, "missing_idempotency_key")
    err(c.post("/reservations", body, token=ada, key=""), 400, "missing_idempotency_key")
    err(c.post("/reservation-moves", {"moves": [{"reference": "SEED01"}]}, token=ada), 400, "missing_idempotency_key")
    assert c.get("/reservations", token=ada).json["reservations"] == []


def test_C1_36_unauthenticated_variants(c, ada):
    err(c.get("/reservations"), 401, "unauthenticated")
    err(c.get("/reservations", headers={"Authorization": "Basic abc"}), 401, "unauthenticated")
    err(c.get("/reservations", headers={"Authorization": "Bearer"}), 401, "unauthenticated")
    err(c.get("/reservations", headers={"Authorization": "Bearer nope-" + uuid.uuid4().hex}), 401, "unauthenticated")
    err(c.get("/reservations", headers={"Authorization": f"Token {ada}"}), 401, "unauthenticated")
    assert c.get("/reservations", token=ada).status == 200


def test_C1_38_C1_88_not_found(c, ada):
    err(c.get("/restaurants/nope"), 404, "not_found")
    err(c.get("/reservations/NOPE99", token=ada), 404, "not_found")
    err(c.availability("nope", FUT_THU, 2), 404, "not_found")
    err(c.book(ada, k(), "nope", "t_1", f"{FUT_THU}T19:00"), 404, "not_found")
    err(c.book(ada, k(), "r_anker", "nope", f"{FUT_THU}T19:00"), 404, "not_found")
    err(c.book(ada, k(), "r_anker", "a_1", f"{FUT_THU}T19:00"), 404, "not_found")


def test_C1_40_C1_80_missing_fields(c, ada):
    full = {"restaurant_id": "r_all", "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2}
    for f in full:
        body = {x: v for x, v in full.items() if x != f}
        err(c.post("/reservations", body, token=ada, key=k()), 422, "validation_failed")
    err(c.get(f"/availability?restaurant_id=r_all&date={FUT_DAY}"), 422, "validation_failed")
    err(c.post("/auth/signup", {"email": "m@example.com", "password": "long enough"}), 422, "validation_failed")
    err(c.post("/auth/login", {"email": "ada@example.com"}), 422, "validation_failed")


def test_C1_41_C1_43_format_and_range(c, ada):
    err(c.availability("r_all", "2027-13-40", 2), 422, "validation_failed")
    err(c.availability("r_all", "15/06/2027", 2), 422, "validation_failed")
    err(c.availability("r_all", FUT_DAY, -1), 422, "validation_failed")
    for v in ("1e9", "4.0", "+4", "four", "", "0x4"):
        err(c.availability("r_all", FUT_DAY, v), 422, "validation_failed")
    body = {"restaurant_id": "r_all", "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2}
    err(c.post("/reservations", body, token=ada, key="x" * 256), 422, "validation_failed")
    assert c.post("/reservations", body, token=ada, key="y" * 255).status == 201


@pytest.mark.parametrize("party", ["4", True, False, None, 4.5, 0, -3, [4], {"n": 4}])
def test_C1_42_C1_86_party_size_invalid(c, ada, party):
    err(c.book(ada, k(), "r_all", "a_2", f"{FUT_DAY}T12:00", party), 422, "validation_failed")


@pytest.mark.parametrize("local", [f"{FUT_DAY}T12:00:00+02:00", f"{FUT_DAY}T12:00Z", f"{FUT_DAY} 12:00",
                                   FUT_DAY, f"{FUT_DAY}T12:00:00", f"{FUT_DAY}T12", "2027-06-31T12:00",
                                   f"{FUT_DAY}T25:00", ""])
def test_C1_42_C1_79_starts_at_local_must_be_bare(c, ada, local):
    err(c.book(ada, k(), "r_all", "a_2", local, 2), 422, "validation_failed")


def test_C1_45_idempotency_key_length(c, ada):
    body = {"restaurant_id": "r_all", "table_id": "a_3", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2}
    err(c.post("/reservations", body, token=ada, key="k" * 256), 422, "validation_failed")
    assert c.post("/reservations", body, token=ada, key="k").status == 201


# ============================================================ §6 authentication
def test_C1_47_C1_48_signup_login(c):
    r = c.signup("new@example.com", "s3cret-long", "Newbie")
    assert r.status == 201, r
    assert set(r.json) >= {"user_id", "display_name", "token"} and r.json["display_name"] == "Newbie"
    uid, tok1 = r.json["user_id"], r.json["token"]
    assert c.get("/reservations", token=tok1).status == 200
    l = c.post("/auth/login", {"email": "new@example.com", "password": "s3cret-long"})
    assert l.status == 200 and l.json["user_id"] == uid and l.json["display_name"] == "Newbie", l
    assert c.get("/reservations", token=l.json["token"]).status == 200


def test_C1_49_email_taken(c):
    assert c.signup("dup@example.com").status == 201
    err(c.signup("dup@example.com"), 409, "email_taken")
    err(c.signup("ada@example.com"), 409, "email_taken")


def test_C1_50_C1_51_signup_validation(c):
    err(c.signup("short@example.com", "1234567"), 422, "validation_failed")
    assert c.signup("eight@example.com", "12345678").status == 201
    for e in ("ada", "@x.com", "a@", "a b@example.com", "", "a@b@c.com", " a@b.com"):
        err(c.signup(e), 422, "validation_failed")
    err(c.signup("blank@example.com", "long enough", "   "), 422, "validation_failed")   # R-4 blank name
    err(c.post("/auth/signup", {"email": "n@example.com", "password": "long enough", "display_name": None}), 400, "malformed_request")
    assert c.signup("uni@example.com", "pässwörd", "U").status == 201                     # 8 code points
    err(c.signup("uni2@example.com", "pässwör", "U"), 422, "validation_failed")


def test_C1_49_C1_52_email_case_insensitive(c):                                            # R-4
    err(c.signup("ADA@example.com"), 409, "email_taken")
    assert c.signup("Mixed@Example.com", "long enough", "M").status == 201
    err(c.signup("mixed@example.com", "long enough", "M"), 409, "email_taken")
    r = c.post("/auth/login", {"email": "ADA@EXAMPLE.COM", "password": "correct horse"})
    assert r.status == 200 and r.json["user_id"] == "u_ada", r


def test_C1_52_login_failures(c):
    err(c.post("/auth/login", {"email": "ada@example.com", "password": "wrong horse"}), 401, "unauthenticated")
    err(c.post("/auth/login", {"email": "nobody@example.com", "password": "correct horse"}), 401, "unauthenticated")


def test_C1_53_C1_68_public_and_protected(c, ada):
    assert c.get("/health").status == 200
    assert c.get("/restaurants").status == 200
    assert c.get("/restaurants/r_anker").status == 200
    assert c.availability("r_anker", FUT_THU, 2).status == 200
    assert c.export().status == 200
    body = {"restaurant_id": "r_all", "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2}
    err(c.post("/reservations", body, key=k()), 401, "unauthenticated")
    err(c.get("/reservations"), 401, "unauthenticated")
    err(c.get("/reservations/SEED01"), 401, "unauthenticated")
    err(c.post("/reservations/SEED01/cancel"), 401, "unauthenticated")
    err(c.patch("/reservations/SEED01", {}), 401, "unauthenticated")
    err(c.post("/reservation-moves", {"moves": [{"reference": "SEED01"}]}, key=k()), 401, "unauthenticated")


def test_C1_54_tokens_persist_and_multiply(c):
    t1 = c.login("ada@example.com", "correct horse")
    t2 = c.login("ada@example.com", "correct horse")
    s = c.signup("third@example.com").json["token"]
    for _ in range(3):
        assert c.get("/reservations", token=t1).status == 200
        assert c.get("/reservations", token=t2).status == 200
        assert c.get("/reservations", token=s).status == 200


def test_C1_55_no_plaintext_passwords_in_export(c):
    assert c.signup("hash@example.com", "unique-plain-text-pw-42").status == 201
    text = c.export().text
    assert "correct horse" not in text and "unique-plain-text-pw-42" not in text and "bob secret 1" not in text


# ============================================================ §7 idempotency
def test_C1_56_C1_96_patch_and_cancel_need_no_key(c, ada):
    ref = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")["reference"]
    assert c.patch(f"/reservations/{ref}", {"party_size": 1}, token=ada).status == 200
    assert c.post(f"/reservations/{ref}/cancel", token=ada).status == 200


def test_C1_57_key_scoped_per_user(c, ada, bob):
    key = k()
    a = c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00")
    b = c.book(bob, key, "r_all", "a_2", f"{FUT_DAY}T13:00")
    assert a.status == 201 and b.status == 201, (a, b)
    assert a.json["reference"] != b.json["reference"]


def test_C1_58_same_key_other_path_is_new_request(c, ada):
    key = k()
    r = c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00")
    assert r.status == 201
    m = c.post("/reservation-moves", {"moves": [{"reference": r.json["reference"], "party_size": 1}]}, token=ada, key=key)
    assert m.status == 201, m
    assert m.json["reservations"][0]["party_size"] == 1


def test_C1_59_C1_39_reuse_beats_validation_and_state(c, ada):
    key = k()
    r = c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00")
    assert r.status == 201
    err(c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00", -1), 409, "idempotency_key_reuse")
    err(c.book(ada, key, "nope", "a_1", f"{FUT_DAY}T12:00", 2), 409, "idempotency_key_reuse")
    err(c.book(ada, key, "r_all", "a_1", "garbage", 2), 409, "idempotency_key_reuse")
    # replay still works even though the table is now taken by the same booking
    again = c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00")
    assert again.status == 200 and again.json == r.json, again


def test_C1_61_C1_62_C1_65_first_use_then_replay(c, ada):
    key = k()
    body = {"restaurant_id": "r_all", "table_id": "a_2", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 3}
    r = c.post("/reservations", body, token=ada, key=key)
    assert r.status == 201
    raw = (' {"party_size" : 3,\n "starts_at_local":"%sT12:00",   "table_id":"a_2","restaurant_id":"r_all"} ' % FUT_DAY).encode()
    p = c.request("POST", "/reservations", raw=raw, token=ada, key=key)
    assert p.status == 200 and p.json == r.json, p
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 1


def test_C1_63_different_body_409_no_state_change(c, ada):
    key = k()
    r = c.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T12:00", 4)
    assert r.status == 201
    err(c.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T12:00", 2), 409, "idempotency_key_reuse")
    err(c.book(ada, key, "r_all", "a_3", f"{FUT_DAY}T14:00", 4), 409, "idempotency_key_reuse")
    lst = c.get("/reservations", token=ada).json["reservations"]
    assert len(lst) == 1 and lst[0]["party_size"] == 4


def test_C1_64_key_reusable_after_4xx(c, ada):
    key = k()
    err(c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00", 99), 422, "party_exceeds_capacity")
    err(c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:07", 2), 422, "not_on_slot_grid")
    r = c.book(ada, key, "r_all", "a_1", f"{FUT_DAY}T12:00", 2)
    assert r.status == 201, r
    key2 = k()
    err(c.book(ada, key2, "r_all", "a_1", f"{FUT_DAY}T12:00", 2), 409, "table_unavailable")
    r2 = c.book(ada, key2, "r_all", "a_2", f"{FUT_DAY}T12:00", 2)
    assert r2.status == 201, r2


def test_C1_66_concurrent_identical_requests(c, ada):
    key = k()
    body = {"restaurant_id": "r_all", "table_id": "a_3", "starts_at_local": f"{FUT_DAY}T15:00", "party_size": 5}
    res = burst([lambda: Client(c.base_url, timeout=15).post("/reservations", body, token=ada, key=key)] * 20)
    for r in res:
        assert not isinstance(r, BaseException), r
    statuses = sorted(r.status for r in res)
    assert statuses == [200] * 19 + [201], statuses
    bodies = {json.dumps(r.json, sort_keys=True) for r in res}
    assert len(bodies) == 1, bodies
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 1


def test_C1_67_replay_after_change_and_cancel(c, ada):
    key = k()
    r = c.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T12:00", 4)
    assert r.status == 201
    ref = r.json["reference"]
    assert c.patch(f"/reservations/{ref}", {"party_size": 3, "starts_at_local": f"{FUT_DAY}T13:00"}, token=ada).status == 200
    p = c.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T12:00", 4)
    assert p.status == 200 and p.json == r.json, p
    assert c.post(f"/reservations/{ref}/cancel", token=ada).status == 200
    p = c.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T12:00", 4)
    assert p.status == 200 and p.json == r.json and p.json["status"] == "confirmed", p
    g = c.get(f"/reservations/{ref}", token=ada).json
    assert g["status"] == "cancelled" and g["party_size"] == 3 and g["starts_at_local"] == f"{FUT_DAY}T13:00"
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 1


# ============================================================ §8 restaurants and availability
def test_C1_69_list_restaurants(c):
    r = c.get("/restaurants")
    assert r.status == 200
    assert r.json["restaurants"] == [{"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin"},
                                     {"id": "r_all", "name": "All Week", "timezone": "Europe/Berlin"},
                                     {"id": "r_ny", "name": "Hudson", "timezone": "America/New_York"},
                                     {"id": "r_trio", "name": "Trio", "timezone": "Europe/Berlin"}]


def test_C1_71_availability_params(c):
    err(c.get(f"/availability?date={FUT_THU}&party_size=2"), 422, "validation_failed")
    err(c.get("/availability?restaurant_id=r_anker&party_size=2"), 422, "validation_failed")
    err(c.get(f"/availability?restaurant_id=r_anker&date={FUT_THU}"), 422, "validation_failed")
    err(c.get("/availability"), 422, "validation_failed")
    assert c.availability("r_anker", FUT_THU, 2).status == 200


def test_C1_72_C1_73_C1_74_slots(c, ada):
    r = c.availability("r_anker", FUT_THU, 4)
    assert r.status == 200
    assert set(r.json) >= {"restaurant_id", "date", "timezone", "slots"}
    assert r.json["restaurant_id"] == "r_anker" and r.json["date"] == FUT_THU and r.json["timezone"] == "Europe/Berlin"
    locals_ = [s["starts_at_local"] for s in r.json["slots"]]
    assert locals_ == [f"{FUT_THU}T{h:02d}:{m:02d}" for h, m in
                       [(18, 0), (18, 30), (19, 0), (19, 30), (20, 0), (20, 30), (21, 0), (21, 30)]], locals_
    for s in r.json["slots"]:
        assert LOCAL.match(s["starts_at_local"]) and RFC3339.match(s["starts_at"])
        assert s["starts_at"].startswith(s["starts_at_local"]) and s["starts_at"].endswith("+02:00")
        assert isinstance(s["available_table_ids"], list)
    fri = [s["starts_at_local"] for s in c.availability("r_anker", FUT_FRI, 4).json["slots"]]
    assert fri[-1] == f"{FUT_FRI}T22:00" and len(fri) == 9, fri
    # a slot value posts unchanged
    slot = r.json["slots"][5]  # 20:30, clear of the seeded 18:00 booking on t_2
    assert slot["available_table_ids"] == ["t_2"], slot
    b = c.book(ada, k(), "r_anker", slot["available_table_ids"][0], slot["starts_at_local"], 4)
    assert b.status == 201 and b.json["starts_at_local"] == slot["starts_at_local"] and b.json["starts_at"] == slot["starts_at"]


def test_C1_75_C1_76_available_tables(c, ada):
    r = c.availability("r_anker", FUT_FRI, 3).json
    assert all(s["available_table_ids"] == ["t_2"] for s in r["slots"]), r
    r = c.availability("r_anker", FUT_FRI, 1).json
    assert all(s["available_table_ids"] == ["t_1", "t_2"] for s in r["slots"]), r
    book_all(c, ada, "r_anker", "t_2", f"{FUT_FRI}T19:00", 4)
    r = c.availability("r_anker", FUT_FRI, 1).json
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in r["slots"]}
    assert by["18:00"] == ["t_1"] and by["18:30"] == ["t_1"] and by["19:00"] == ["t_1"]
    assert by["19:30"] == ["t_1"] and by["20:00"] == ["t_1"]
    assert by["20:30"] == ["t_1", "t_2"] and by["21:00"] == ["t_1", "t_2"]
    r = c.availability("r_anker", FUT_FRI, 99).json
    assert len(r["slots"]) == 9 and all(s["available_table_ids"] == [] for s in r["slots"])


def test_C1_77_C1_24_closed_day(c, ada):
    r = c.availability("r_anker", FUT_WED, 2)
    assert r.status == 200 and r.json["slots"] == [], r
    err(c.book(ada, k(), "r_anker", "t_1", f"{FUT_WED}T19:00"), 422, "outside_opening_hours")


# ============================================================ §8 reservations
def test_C1_20_C1_79_C1_100_offsets_follow_zone(c, ada):
    b = book_all(c, ada, "r_anker", "t_1", f"{FUT_THU}T19:00")
    assert b["starts_at"] == f"{FUT_THU}T19:00:00+02:00"
    n = book_all(c, ada, "r_ny", "n_1", f"{FUT_THU}T19:00")
    assert n["starts_at"] == f"{FUT_THU}T19:00:00-04:00" and n["ends_at"] == f"{FUT_THU}T20:30:00-04:00"
    w = book_all(c, ada, "r_all", "a_1", "2027-01-20T12:00")  # winter, Wednesday
    assert w["starts_at"] == "2027-01-20T12:00:00+01:00"
    n2 = book_all(c, ada, "r_ny", "n_1", "2027-01-20T19:00")
    assert n2["starts_at"] == "2027-01-20T19:00:00-05:00"


def test_C1_81_references(c, ada):
    refs = set()
    for i in range(8):
        for t in ("a_1", "a_2", "a_3"):
            o = book_all(c, ada, "r_all", t, f"{FUT_DAY}T{10 + i:02d}:00")
            assert REF.match(o["reference"]), o["reference"]
            refs.add(o["reference"])
    assert len(refs) == 24 and "SEED01" not in refs
    ref = next(iter(refs))
    o = c.get(f"/reservations/{ref}", token=ada).json
    p = c.patch(f"/reservations/{ref}", {"party_size": 1}, token=ada).json
    assert p["reference"] == ref and p["reservation_id"] == o["reservation_id"]
    x = c.post(f"/reservations/{ref}/cancel", token=ada).json
    assert x["reference"] == ref and x["reservation_id"] == o["reservation_id"]


def test_C1_3_C1_4_C1_82_overlap(c, ada, bob):
    book_all(c, ada, "r_anker", "t_2", f"{FUT_FRI}T19:00", 4)
    err(c.book(bob, k(), "r_anker", "t_2", f"{FUT_FRI}T20:00", 2), 409, "table_unavailable")
    err(c.book(bob, k(), "r_anker", "t_2", f"{FUT_FRI}T18:00", 2), 409, "table_unavailable")
    err(c.book(bob, k(), "r_anker", "t_2", f"{FUT_FRI}T19:00", 2), 409, "table_unavailable")
    assert c.book(bob, k(), "r_anker", "t_2", f"{FUT_FRI}T20:30", 2).status == 201
    assert c.book(bob, k(), "r_anker", "t_1", f"{FUT_FRI}T19:00", 2).status == 201
    # cancelled bookings do not block
    ref = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")["reference"]
    err(c.book(bob, k(), "r_all", "a_1", f"{FUT_DAY}T12:30", 2), 409, "table_unavailable")
    assert c.post(f"/reservations/{ref}/cancel", token=ada).status == 200
    assert c.book(bob, k(), "r_all", "a_1", f"{FUT_DAY}T12:30", 2).status == 201


def test_C1_21_C1_83_slot_grid(c, ada):
    err(c.book(ada, k(), "r_anker", "t_1", f"{FUT_THU}T18:15"), 422, "not_on_slot_grid")
    err(c.book(ada, k(), "r_anker", "t_1", f"{FUT_THU}T19:01"), 422, "not_on_slot_grid")
    assert c.book(ada, k(), "r_anker", "t_1", f"{FUT_THU}T18:30").status == 201
    fx = base_fixture()
    fx["restaurants"][0]["opening_hours"][0]["opens"] = "18:10"
    assert c.reset(fx).status == 204
    tok = c.login("ada@example.com", "correct horse")
    err(c.book(tok, k(), "r_anker", "t_1", f"{FUT_THU}T18:30"), 422, "not_on_slot_grid")
    assert c.book(tok, k(), "r_anker", "t_1", f"{FUT_THU}T18:40").status == 201


def test_C1_84_outside_opening_hours(c, ada):
    err(c.book(ada, k(), "r_anker", "t_1", f"{FUT_THU}T22:00"), 422, "outside_opening_hours")
    err(c.book(ada, k(), "r_anker", "t_1", f"{FUT_THU}T17:30"), 422, "outside_opening_hours")
    err(c.book(ada, k(), "r_anker", "t_1", f"{FUT_THU}T23:00"), 422, "outside_opening_hours")
    assert c.book(ada, k(), "r_anker", "t_1", f"{FUT_THU}T21:30").status == 201
    assert c.book(ada, k(), "r_anker", "t_1", f"{FUT_FRI}T22:00").status == 201


def test_C1_25_C1_85_capacity(c, ada):
    assert c.book(ada, k(), "r_anker", "t_2", f"{FUT_THU}T20:00", 4).status == 201
    err(c.book(ada, k(), "r_anker", "t_2", f"{FUT_THU}T21:30", 5), 422, "party_exceeds_capacity")
    err(c.book(ada, k(), "r_anker", "t_1", f"{FUT_THU}T21:00", 3), 422, "party_exceeds_capacity")


def test_C1_89_list_reservations(c, ada, bob):
    assert c.get("/reservations", token=ada).json == {"reservations": []}
    a = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")
    b = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY2}T12:00")
    d = book_all(c, ada, "r_ny", "n_1", f"{FUT_DAY}T19:00")
    assert c.post(f"/reservations/{a['reference']}/cancel", token=ada).status == 200
    lst = c.get("/reservations", token=ada).json["reservations"]
    assert [x["reference"] for x in lst] == [b["reference"], d["reference"], a["reference"]], lst
    for x in lst:
        check_reservation_shape(x)
    assert lst[2]["status"] == "cancelled"
    assert [x["reference"] for x in c.get("/reservations", token=bob).json["reservations"]] == ["SEED01"]
    # R-11 ties: same starts_at in two restaurants -> created_at ascending, then reference ascending
    t1 = book_all(c, ada, "r_anker", "t_1", f"{FUT_THU}T19:00")
    t2 = book_all(c, ada, "r_all", "a_1", f"{FUT_THU}T19:00")
    lst = c.get("/reservations", token=ada).json["reservations"]
    from datetime import datetime as _dt
    expected = sorted(lst, key=lambda x: (x["created_at"], x["reference"]))
    expected = sorted(expected, key=lambda x: _dt.fromisoformat(x["starts_at"]), reverse=True)
    assert [x["reference"] for x in lst] == [x["reference"] for x in expected], lst
    assert {lst[0]["reference"], lst[1]["reference"]} == {t1["reference"], t2["reference"]}


def test_C1_90_C1_132_get_one(c, ada, bob):
    o = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")
    g = c.get(f"/reservations/{o['reference']}", token=ada)
    assert g.status == 200 and g.json == o, g
    err(c.get(f"/reservations/{o['reference']}", token=bob), 404, "not_found")


def test_C1_91_C1_92_cancel_frees_table(c, ada):
    o = book_all(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00")
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_anker", FUT_FRI, 2).json["slots"]}
    assert by["19:00"] == ["t_2"] and by["18:00"] == ["t_2"] and by["20:30"] == ["t_1", "t_2"]
    x = c.post(f"/reservations/{o['reference']}/cancel", token=ada)
    assert x.status == 200 and x.json["status"] == "cancelled" and x.json["reference"] == o["reference"], x
    check_reservation_shape(x.json)
    assert {k_: v for k_, v in x.json.items() if k_ != "status"} == {k_: v for k_, v in o.items() if k_ != "status"}
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_anker", FUT_FRI, 2).json["slots"]}
    assert by["19:00"] == ["t_1", "t_2"] and by["20:00"] == ["t_1", "t_2"]
    assert c.book(ada, k(), "r_anker", "t_1", f"{FUT_FRI}T19:00").status == 201


def test_C1_91_cancel_body_rules(c, ada):                                                   # R-13
    ref = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")["reference"]
    err(c.request("POST", f"/reservations/{ref}/cancel", raw=b"{not json", token=ada), 400, "malformed_request")
    assert c.get(f"/reservations/{ref}", token=ada).json["status"] == "confirmed"
    assert c.request("POST", f"/reservations/{ref}/cancel", raw=b"", token=ada).status == 200
    ref2 = book_all(c, ada, "r_all", "a_2", f"{FUT_DAY}T12:00")["reference"]
    assert c.request("POST", f"/reservations/{ref2}/cancel", raw=b'{"ignored": true}', token=ada).status == 200


def test_C1_93_cancel_twice(c, ada):
    ref = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")["reference"]
    x1 = c.post(f"/reservations/{ref}/cancel", token=ada)
    x2 = c.post(f"/reservations/{ref}/cancel", token=ada)
    assert x1.status == 200 and x2.status == 200 and x1.json == x2.json and x2.json["status"] == "cancelled"


def test_C1_23_C1_94_cutoff(c, ada):
    past = book_all(c, ada, "r_all", "a_1", f"{PAST_DAY}T12:00")
    err(c.post(f"/reservations/{past['reference']}/cancel", token=ada), 409, "cutoff_passed")
    assert c.get(f"/reservations/{past['reference']}", token=ada).json["status"] == "confirmed"
    fut = book_all(c, ada, "r_all", "a_2", f"{FUT_DAY}T12:00")
    assert c.post(f"/reservations/{fut['reference']}/cancel", token=ada).status == 200


def test_C1_95_cancel_others_404(c, ada, bob):
    err(c.post("/reservations/SEED01/cancel", token=ada), 404, "not_found")
    assert c.get("/reservations/SEED01", token=bob).json["status"] == "confirmed"


def test_C1_96_C1_99_patch_subsets(c, ada):
    o = book_all(c, ada, "r_all", "a_2", f"{FUT_DAY}T12:00", 3)
    ref = o["reference"]
    p = c.patch(f"/reservations/{ref}", {"party_size": 2}, token=ada)
    assert p.status == 200 and p.json["party_size"] == 2 and p.json["table_id"] == "a_2"
    assert p.json["starts_at_local"] == f"{FUT_DAY}T12:00" and p.json["reference"] == ref
    assert p.json["reservation_id"] == o["reservation_id"] and p.json["created_at"] == o["created_at"]
    p = c.patch(f"/reservations/{ref}", {"table_id": "a_3"}, token=ada)
    assert p.status == 200 and p.json["table_id"] == "a_3" and p.json["party_size"] == 2
    p = c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_DAY}T14:15"}, token=ada)
    assert p.status == 200 and p.json["starts_at_local"] == f"{FUT_DAY}T14:15"
    assert p.json["starts_at"] == f"{FUT_DAY}T14:15:00+02:00" and p.json["ends_at"] == f"{FUT_DAY}T15:15:00+02:00"
    p = c.patch(f"/reservations/{ref}", {}, token=ada)
    assert p.status == 200 and p.json["table_id"] == "a_3" and p.json["starts_at_local"] == f"{FUT_DAY}T14:15"
    p = c.patch(f"/reservations/{ref}", {"table_id": "a_1", "starts_at_local": f"{FUT_DAY}T16:00", "party_size": 1}, token=ada)
    assert p.status == 200 and (p.json["table_id"], p.json["party_size"], p.json["starts_at_local"]) == ("a_1", 1, f"{FUT_DAY}T16:00")
    assert c.get(f"/reservations/{ref}", token=ada).json == p.json


def test_C1_97_C1_98_patch_validation_like_post(c, ada, bob):
    o = book_all(c, ada, "r_all", "a_2", f"{FUT_DAY}T12:00", 3)
    ref = o["reference"]
    err(c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_DAY}T12:07"}, token=ada), 422, "not_on_slot_grid")
    err(c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_DAY}T21:15"}, token=ada), 422, "outside_opening_hours")
    err(c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_DAY}T09:00"}, token=ada), 422, "outside_opening_hours")
    err(c.patch(f"/reservations/{ref}", {"party_size": 5}, token=ada), 422, "party_exceeds_capacity")
    err(c.patch(f"/reservations/{ref}", {"table_id": "a_1"}, token=ada), 422, "party_exceeds_capacity")
    err(c.patch(f"/reservations/{ref}", {"party_size": 0}, token=ada), 422, "validation_failed")
    err(c.patch(f"/reservations/{ref}", {"party_size": "3"}, token=ada), 422, "validation_failed")
    err(c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_DAY}T12:00Z"}, token=ada), 422, "validation_failed")
    err(c.patch(f"/reservations/{ref}", {"table_id": 7}, token=ada), 400, "malformed_request")
    err(c.request("PATCH", f"/reservations/{ref}", raw=b"nope", token=ada), 400, "malformed_request")
    err(c.patch(f"/reservations/{ref}", {"table_id": "nope"}, token=ada), 404, "not_found")
    err(c.patch(f"/reservations/{ref}", {"table_id": "t_1"}, token=ada), 404, "not_found")
    err(c.patch(f"/reservations/{ref}", {"party_size": 2}, token=bob), 404, "not_found")
    book_all(c, bob, "r_all", "a_3", f"{FUT_DAY}T13:00", 2)
    err(c.patch(f"/reservations/{ref}", {"table_id": "a_3", "starts_at_local": f"{FUT_DAY}T12:30"}, token=ada), 409, "table_unavailable")
    assert c.get(f"/reservations/{ref}", token=ada).json == o
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_all", FUT_DAY, 1).json["slots"]}
    assert "a_2" not in by["12:00"] and "a_2" in by["13:00"] and "a_3" in by["12:00"]
    # cutoff measured against the current start, even when moving to the future
    past = book_all(c, ada, "r_all", "a_1", f"{PAST_DAY}T12:00")
    err(c.patch(f"/reservations/{past['reference']}", {"starts_at_local": f"{FUT_DAY}T18:00"}, token=ada), 409, "cutoff_passed")
    assert c.get(f"/reservations/{past['reference']}", token=ada).json == past


def test_C1_97_patch_cancelled(c, ada):
    ref = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00")["reference"]
    assert c.post(f"/reservations/{ref}/cancel", token=ada).status == 200
    err(c.patch(f"/reservations/{ref}", {"party_size": 1}, token=ada), 409, "reservation_cancelled")
    err(c.patch(f"/reservations/{ref}", {}, token=ada), 409, "reservation_cancelled")


def test_C1_98_amendment_moves_occupancy_atomically(c, ada, bob):
    o = book_all(c, ada, "r_anker", "t_1", f"{FUT_FRI}T19:00")
    ref = o["reference"]
    # overlapping its own old interval is fine
    p = c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_FRI}T19:30"}, token=ada)
    assert p.status == 200, p
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_anker", FUT_FRI, 2).json["slots"]}
    assert by["18:00"] == ["t_1", "t_2"] and by["19:00"] == ["t_2"] and by["21:00"] == ["t_1", "t_2"]
    p = c.patch(f"/reservations/{ref}", {"starts_at_local": f"{FUT_FRI}T21:30"}, token=ada)
    assert p.status == 200
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_anker", FUT_FRI, 2).json["slots"]}
    assert by["19:30"] == ["t_1", "t_2"] and by["21:30"] == ["t_2"] and by["20:30"] == ["t_2"]
    assert c.book(bob, k(), "r_anker", "t_1", f"{FUT_FRI}T19:00").status == 201


# ============================================================ §9 DST
def test_C1_101_C1_104_spring_forward_berlin(c, ada):
    sl = [s["starts_at_local"][-5:] for s in c.availability("r_anker", BERLIN_SPRING, 1).json["slots"]]
    assert sl == ["00:00", "00:30", "01:00", "01:30", "03:00", "03:30", "04:00", "04:30"], sl
    err(c.book(ada, k(), "r_anker", "t_1", f"{BERLIN_SPRING}T02:00"), 422, "invalid_local_time")
    err(c.book(ada, k(), "r_anker", "t_1", f"{BERLIN_SPRING}T02:30"), 422, "invalid_local_time")
    o = book_all(c, ada, "r_anker", "t_1", f"{BERLIN_SPRING}T01:30")
    assert o["starts_at"] == f"{BERLIN_SPRING}T01:30:00+01:00" and o["ends_at"] == f"{BERLIN_SPRING}T04:00:00+02:00"
    o = book_all(c, ada, "r_anker", "t_2", f"{BERLIN_SPRING}T03:00")
    assert o["starts_at"] == f"{BERLIN_SPRING}T03:00:00+02:00"


def test_C1_101_C1_104_spring_forward_new_york(c, ada):
    sl = [s["starts_at_local"][-5:] for s in c.availability("r_ny", NY_SPRING, 1).json["slots"]]
    assert sl == ["00:00", "00:30", "01:00", "01:30", "03:00", "03:30", "04:00", "04:30"], sl
    err(c.book(ada, k(), "r_ny", "n_1", f"{NY_SPRING}T02:30"), 422, "invalid_local_time")
    o = book_all(c, ada, "r_ny", "n_1", f"{NY_SPRING}T01:00")
    assert o["starts_at"] == f"{NY_SPRING}T01:00:00-05:00" and o["ends_at"] == f"{NY_SPRING}T03:30:00-04:00"


def test_C1_74_C1_84_absolute_end_of_day(c):                                                 # R-23
    fx = base_fixture()
    fx["restaurants"][0]["opening_hours"] = [{"weekday": "sun", "opens": "00:00", "closes": "03:30"}]
    assert c.reset(fx).status == 204
    tok = c.login("ada@example.com", "correct horse")
    sl = [s["starts_at_local"][-5:] for s in c.availability("r_anker", BERLIN_SPRING, 1).json["slots"]]
    assert sl == ["00:00", "00:30", "01:00"], sl
    err(c.book(tok, k(), "r_anker", "t_1", f"{BERLIN_SPRING}T01:30"), 422, "outside_opening_hours")
    assert c.book(tok, k(), "r_anker", "t_1", f"{BERLIN_SPRING}T01:00").status == 201
    sl = [s["starts_at_local"][-5:] for s in c.availability("r_anker", BERLIN_FALL, 1).json["slots"]]
    assert sl == ["00:00", "00:30", "01:00", "01:30", "02:00", "02:30"], sl
    o = c.book(tok, k(), "r_anker", "t_2", f"{BERLIN_FALL}T02:30")
    assert o.status == 201 and o.json["ends_at"] == f"{BERLIN_FALL}T03:00:00+01:00", o
    err(c.book(tok, k(), "r_anker", "t_1", f"{BERLIN_FALL}T03:00"), 422, "outside_opening_hours")
    # closes inside the skipped hour: its instant is the transition instant (01:00Z)
    fx["restaurants"][0]["opening_hours"] = [{"weekday": "sun", "opens": "00:00", "closes": "02:30"}]
    assert c.reset(fx).status == 204
    sl = [s["starts_at_local"][-5:] for s in c.availability("r_anker", BERLIN_SPRING, 1).json["slots"]]
    assert sl == ["00:00", "00:30"], sl


def test_C1_102_C1_103_C1_104_fall_back_berlin(c, ada):
    slots = c.availability("r_anker", BERLIN_FALL, 1).json["slots"]
    sl = [(s["starts_at_local"][-5:], s["starts_at"][-6:]) for s in slots]
    assert sl == [("00:00", "+02:00"), ("00:30", "+02:00"), ("01:00", "+02:00"), ("01:30", "+02:00"),
                  ("02:00", "+02:00"), ("02:30", "+02:00"), ("03:00", "+01:00"), ("03:30", "+01:00"),
                  ("04:00", "+01:00"), ("04:30", "+01:00")], sl
    assert len({s["starts_at_local"] for s in slots}) == len(slots)
    o = book_all(c, ada, "r_anker", "t_1", f"{BERLIN_FALL}T01:30")
    assert o["starts_at"] == f"{BERLIN_FALL}T01:30:00+02:00" and o["ends_at"] == f"{BERLIN_FALL}T02:00:00+01:00"
    o = book_all(c, ada, "r_anker", "t_2", f"{BERLIN_FALL}T02:30")
    assert o["starts_at"] == f"{BERLIN_FALL}T02:30:00+02:00" and o["ends_at"] == f"{BERLIN_FALL}T03:00:00+01:00"
    # t_1 occupies [23:30Z, 01:00Z), t_2 occupies [00:30Z, 02:00Z); 03:00 local (+01:00) is 02:00Z, so both are free
    # again there. The slot 02:30 appears once and is taken: the second occurrence (02:30+01:00) is not bookable.
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_anker", BERLIN_FALL, 1).json["slots"]}
    assert by["02:00"] == [] and by["02:30"] == [] and by["03:00"] == ["t_1", "t_2"] and by["01:00"] == ["t_2"], by
    err(c.book(ada, k(), "r_anker", "t_2", f"{BERLIN_FALL}T02:30"), 409, "table_unavailable")


def test_C1_102_C1_103_C1_104_fall_back_new_york(c, ada):
    slots = c.availability("r_ny", NY_FALL, 1).json["slots"]
    sl = [(s["starts_at_local"][-5:], s["starts_at"][-6:]) for s in slots]
    assert sl == [("00:00", "-04:00"), ("00:30", "-04:00"), ("01:00", "-04:00"), ("01:30", "-04:00"),
                  ("02:00", "-05:00"), ("02:30", "-05:00"), ("03:00", "-05:00"), ("03:30", "-05:00"),
                  ("04:00", "-05:00"), ("04:30", "-05:00")], sl
    o = book_all(c, ada, "r_ny", "n_1", f"{NY_FALL}T00:30")
    assert o["starts_at"] == f"{NY_FALL}T00:30:00-04:00" and o["ends_at"] == f"{NY_FALL}T01:00:00-05:00"


def test_C1_104_C1_22_duration_absolute(c, ada):
    for rest, table, local in (("r_anker", "t_1", f"{FUT_THU}T19:00"), ("r_ny", "n_1", f"{FUT_THU}T19:00"),
                               ("r_anker", "t_2", f"{BERLIN_FALL}T02:00")):
        o = book_all(c, ada, rest, table, local)
        from datetime import datetime
        d = datetime.fromisoformat(o["ends_at"]) - datetime.fromisoformat(o["starts_at"])
        assert d.total_seconds() == 90 * 60, o


# ============================================================ §10 export / import
def test_C1_105_C1_106_export_shape_unauthenticated(c):
    r = c.export()
    assert r.status == 200 and r.json["track"] == "tablekeeper" and r.json["format_version"] == 1
    assert isinstance(r.json["state"], dict)
    assert c.import_(r.json).status == 204


def _snapshot(c: Client, ada: str, bob: str):
    return {
        "restaurants": [c.get(f"/restaurants/{x['id']}").json for x in c.get("/restaurants").json["restaurants"]],
        "ada": c.get("/reservations", token=ada).json,
        "bob": c.get("/reservations", token=bob).json,
        "avail": c.availability("r_all", FUT_DAY, 1).json,
    }


def test_C1_107_C1_108_C1_110_C1_111_C1_112_round_trip(c, ada, bob):
    key_ok, key_bad = k(), k()
    o = c.book(ada, key_ok, "r_all", "a_2", f"{FUT_DAY}T12:00", 3)
    assert o.status == 201
    err(c.book(ada, key_bad, "r_all", "a_2", f"{FUT_DAY}T12:00", 99), 422, "party_exceeds_capacity")
    cancelled = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T14:00")
    assert c.post(f"/reservations/{cancelled['reference']}/cancel", token=ada).status == 200
    new_user = c.signup("imp@example.com", "import-pw-12345", "Imp").json
    before = _snapshot(c, ada, bob)
    exp = c.export()
    assert exp.status == 200
    # export is a snapshot: later writes do not change it
    later = book_all(c, ada, "r_all", "a_3", f"{FUT_DAY}T16:00")
    r = c.import_(exp.json)
    assert r.status == 204 and r.text == "", r
    assert _snapshot(c, ada, bob) == before, "state after import differs from state at export"
    err(c.get(f"/reservations/{later['reference']}", token=ada), 404, "not_found")
    # accounts, hashed login, tokens, receipts
    assert c.get("/reservations", token=new_user["token"]).status == 200
    assert c.login("imp@example.com", "import-pw-12345")
    assert c.login("ada@example.com", "correct horse")
    rp = c.book(ada, key_ok, "r_all", "a_2", f"{FUT_DAY}T12:00", 3)
    assert rp.status == 200 and rp.json == o.json, rp
    err(c.book(ada, key_ok, "r_all", "a_2", f"{FUT_DAY}T12:00", 2), 409, "idempotency_key_reuse")
    assert c.book(ada, key_bad, "r_all", "a_3", f"{FUT_DAY}T12:00", 2).status == 201
    assert c.get(f"/reservations/{cancelled['reference']}", token=ada).json["status"] == "cancelled"
    # import twice does not duplicate
    assert c.import_(exp.json).status == 204
    assert c.import_(exp.json).status == 204
    assert _snapshot(c, ada, bob) == before


def test_C1_109_invalid_import_leaves_state(c, ada):
    before = c.get("/reservations", token=ada).json
    exp = c.export().json
    err(c.request("POST", "/_test/import", raw=b"{nope", timeout=10), 400, "malformed_request")
    err(c.request("POST", "/_test/import", raw=b"[]", timeout=10), 400, "malformed_request")
    err(c.import_({}), 422, "validation_failed")
    err(c.import_({"track": "tablekeeper", "format_version": 1}), 422, "validation_failed")
    err(c.import_({"track": "other", "format_version": 1, "state": exp["state"]}), 422, "validation_failed")
    err(c.import_({"track": "tablekeeper", "format_version": 2, "state": exp["state"]}), 422, "validation_failed")
    err(c.import_({"track": "tablekeeper", "format_version": 1, "state": "garbage"}), 422, "validation_failed")
    err(c.import_({"track": "tablekeeper", "format_version": 1, "state": {"nonsense": True}}), 422, "validation_failed")
    err(c.import_({"track": "tablekeeper", "format_version": 1, "state": {}}), 422, "validation_failed")   # R-24
    err(c.import_({"track": "tablekeeper", "format_version": 1, "state": []}), 422, "validation_failed")
    assert c.get("/reservations", token=ada).json == before
    assert c.get("/restaurants").status == 200


def test_C1_112_import_and_reset_remove_previous(c, ada):
    exp = c.export().json
    extra = c.signup("gone@example.com", "gone-password").json
    extra_ref = book_all(c, extra["token"], "r_all", "a_1", f"{FUT_DAY}T12:00")["reference"]
    assert c.import_(exp).status == 204
    err(c.get("/reservations", token=extra["token"]), 401, "unauthenticated")
    err(c.post("/auth/login", {"email": "gone@example.com", "password": "gone-password"}), 401, "unauthenticated")
    err(c.get(f"/reservations/{extra_ref}", token=ada), 404, "not_found")
    assert c.get("/reservations", token=ada).status == 200
    assert c.reset(other_fixture()).status == 204
    err(c.get("/reservations", token=ada), 401, "unauthenticated")
    assert [x["id"] for x in c.get("/restaurants").json["restaurants"]] == ["r_other"]


def test_C1_112_destination_token_never_collides(c):
    t1 = c.login("ada@example.com", "correct horse")
    exp = c.export().json
    assert c.reset(base_fixture()).status == 204
    t2 = c.login("ada@example.com", "correct horse")
    assert t2 != t1
    assert c.import_(exp).status == 204
    assert c.get("/reservations", token=t1).status == 200
    err(c.get("/reservations", token=t2), 401, "unauthenticated")


def test_C1_107_import_into_second_instance(c, ada, second_base_url):
    if not second_base_url:
        pytest.skip("no --second-base-url")
    key = k()
    o = c.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T12:00", 3)
    assert o.status == 201
    exp = c.export().json
    c2 = Client(second_base_url)
    assert c2.reset(other_fixture()).status == 204
    assert c2.import_(exp).status == 204
    assert c2.get(f"/reservations/{o.json['reference']}", token=ada).json == o.json
    rp = c2.book(ada, key, "r_all", "a_2", f"{FUT_DAY}T12:00", 3)
    assert rp.status == 200 and rp.json == o.json
    assert c2.login("ada@example.com", "correct horse")
    assert c2.get("/restaurants/r_all").json == c.get("/restaurants/r_all").json


# ============================================================ §11 atomic moves
def two(c, ada):
    a = book_all(c, ada, "r_all", "a_1", f"{FUT_DAY}T12:00", 2)
    b = book_all(c, ada, "r_all", "a_2", f"{FUT_DAY}T13:00", 4)
    return a, b


def test_C1_134_C1_113_moves_preconditions(c, ada):
    a, b = two(c, ada)
    body = {"moves": [{"reference": a["reference"], "table_id": "a_2"}, {"reference": b["reference"], "table_id": "a_1"}]}
    err(c.post("/reservation-moves", body, key=k()), 401, "unauthenticated")
    err(c.post("/reservation-moves", body, token=ada), 400, "missing_idempotency_key")
    err(c.request("POST", "/reservation-moves", raw=b"{", token=ada, key=k()), 400, "malformed_request")
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == a


@pytest.mark.parametrize("moves", [[], [{"reference": "SEED01"}] * 2, [{"reference": f"REF{i:03d}"} for i in range(9)],
                                   [{"table_id": "a_1"}], [{"reference": 5}], ["SEED01"], "SEED01", None])
def test_C1_114_moves_shape(c, bob, moves):
    body = {"moves": moves} if moves is not None else {}
    err(c.post("/reservation-moves", body, token=bob, key=k()), 422, "validation_failed")
    assert c.get("/reservations/SEED01", token=bob).json["table_id"] == "t_2"


def test_C1_115_moves_ownership_and_restaurant(c, ada):
    a, b = two(c, ada)
    n = book_all(c, ada, "r_ny", "n_1", f"{FUT_DAY}T19:00")
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"]}, {"reference": "SEED01"}]}, token=ada, key=k()), 404, "not_found")
    err(c.post("/reservation-moves", {"moves": [{"reference": "NOPE01"}]}, token=ada, key=k()), 404, "not_found")
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"]}, {"reference": n["reference"]}]}, token=ada, key=k()), 422, "validation_failed")
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == a


def test_C1_116_C1_123_noop_moves(c, ada, bob):
    a, b = two(c, ada)
    key = k()
    r = c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "foo": 1}, {"reference": b["reference"]}]}, token=ada, key=key)
    assert r.status == 201, r
    assert r.json["reservations"] == [a, b]
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == a
    err(c.get(f"/reservations/{a['reference']}", token=bob), 404, "not_found")


def test_C1_117_moves_cancelled_and_cutoff(c, ada):
    a, b = two(c, ada)
    assert c.post(f"/reservations/{a['reference']}/cancel", token=ada).status == 200
    err(c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "party_size": 1}, {"reference": a["reference"]}]}, token=ada, key=k()), 409, "reservation_cancelled")
    assert c.get(f"/reservations/{b['reference']}", token=ada).json == b
    past = book_all(c, ada, "r_all", "a_3", f"{PAST_DAY}T12:00")
    err(c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "party_size": 1}, {"reference": past["reference"]}]}, token=ada, key=k()), 409, "cutoff_passed")
    err(c.post("/reservation-moves", {"moves": [{"reference": past["reference"], "starts_at_local": f"{FUT_DAY}T12:00"}]}, token=ada, key=k()), 409, "cutoff_passed")
    assert c.get(f"/reservations/{b['reference']}", token=ada).json == b


def test_C1_118_moves_error_precedence(c, ada, bob):
    a, b = two(c, ada)
    past = book_all(c, ada, "r_all", "a_3", f"{PAST_DAY}T12:00")
    # input order: item 1 off-grid beats item 2 cutoff
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "starts_at_local": f"{FUT_DAY}T12:07"},
                                                {"reference": past["reference"], "party_size": 1}]}, token=ada, key=k()), 422, "not_on_slot_grid")
    # within one item: cutoff beats its other errors
    err(c.post("/reservation-moves", {"moves": [{"reference": past["reference"], "starts_at_local": f"{FUT_DAY}T12:07"}]}, token=ada, key=k()), 409, "cutoff_passed")
    # non-occupancy errors beat occupancy errors regardless of order
    book_all(c, bob, "r_all", "a_3", f"{FUT_DAY}T12:00", 2)
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_id": "a_3"},
                                                {"reference": b["reference"], "party_size": 9}]}, token=ada, key=k()), 422, "party_exceeds_capacity")
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_id": "a_3"},
                                                {"reference": b["reference"], "table_id": "zzz"}]}, token=ada, key=k()), 404, "not_found")
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_id": "a_3"}]}, token=ada, key=k()), 409, "table_unavailable")
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == a
    assert c.get(f"/reservations/{b['reference']}", token=ada).json == b


def test_C1_119_C1_120_moves_occupancy_and_atomicity(c, ada, bob):
    a, b = two(c, ada)
    # swap tables: a_1@12:00 <-> a_2@13:00 becomes a@a_2 13:00 and b@a_1 12:00 -- needs capacity: a party 2 fits a_2, b party 4 does not fit a_1
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_id": "a_2", "starts_at_local": f"{FUT_DAY}T13:00"},
                                                {"reference": b["reference"], "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00"}]},
               token=ada, key=k()), 422, "party_exceeds_capacity")
    # two listed moves landing on the same table/time
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_id": "a_3", "starts_at_local": f"{FUT_DAY}T15:00"},
                                                {"reference": b["reference"], "table_id": "a_3", "starts_at_local": f"{FUT_DAY}T15:30"}]},
               token=ada, key=k()), 409, "table_unavailable")
    # unchanged listed booking keeps its occupancy
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"]},
                                                {"reference": b["reference"], "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:30", "party_size": 2}]},
               token=ada, key=k()), 409, "table_unavailable")
    # overlap with an unlisted booking (bob's)
    book_all(c, bob, "r_all", "a_3", f"{FUT_DAY}T15:00", 2)
    err(c.post("/reservation-moves", {"moves": [{"reference": a["reference"], "table_id": "a_3", "starts_at_local": f"{FUT_DAY}T15:30"}]},
               token=ada, key=k()), 409, "table_unavailable")
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == a
    assert c.get(f"/reservations/{b['reference']}", token=ada).json == b
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_all", FUT_DAY, 1).json["slots"]}
    assert by["12:00"] == ["a_2", "a_3"] and by["13:00"] == ["a_1", "a_3"]
    # a successful swap of the two bookings' tables and times (party sizes made compatible)
    key = k()
    r = c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2},
                                                {"reference": a["reference"], "table_id": "a_2", "starts_at_local": f"{FUT_DAY}T13:00"}]},
               token=ada, key=key)
    assert r.status == 201, r
    out = r.json["reservations"]
    assert [x["reference"] for x in out] == [b["reference"], a["reference"]]
    assert (out[0]["table_id"], out[0]["starts_at_local"], out[0]["party_size"]) == ("a_1", f"{FUT_DAY}T12:00", 2)
    assert (out[1]["table_id"], out[1]["starts_at_local"], out[1]["party_size"]) == ("a_2", f"{FUT_DAY}T13:00", 2)
    assert out[1]["reservation_id"] == a["reservation_id"] and out[1]["created_at"] == a["created_at"]
    for x in out:
        check_reservation_shape(x)
    assert c.get(f"/reservations/{a['reference']}", token=ada).json == out[1]
    # the failed batches' keys are reusable; the successful batch replays
    rp = c.post("/reservation-moves", {"moves": [{"reference": b["reference"], "table_id": "a_1", "starts_at_local": f"{FUT_DAY}T12:00", "party_size": 2},
                                                 {"reference": a["reference"], "table_id": "a_2", "starts_at_local": f"{FUT_DAY}T13:00"}]},
                token=ada, key=key)
    assert rp.status == 200 and rp.json == r.json


def test_C1_122_C1_124_moves_replay_survives_changes_and_import(c, ada):
    a, b = two(c, ada)
    key = k()
    body = {"moves": [{"reference": a["reference"], "party_size": 1}, {"reference": b["reference"], "party_size": 3}]}
    r = c.post("/reservation-moves", body, token=ada, key=key)
    assert r.status == 201
    assert c.patch(f"/reservations/{a['reference']}", {"party_size": 2}, token=ada).status == 200
    assert c.post(f"/reservations/{b['reference']}/cancel", token=ada).status == 200
    rp = c.post("/reservation-moves", body, token=ada, key=key)
    assert rp.status == 200 and rp.json == r.json, rp
    assert c.get(f"/reservations/{b['reference']}", token=ada).json["status"] == "cancelled"
    err(c.post("/reservation-moves", {"moves": body["moves"][::-1]}, token=ada, key=key), 409, "idempotency_key_reuse")
    exp = c.export().json
    assert c.import_(exp).status == 204
    rp = c.post("/reservation-moves", body, token=ada, key=key)
    assert rp.status == 200 and rp.json == r.json, rp
    assert c.get(f"/reservations/{a['reference']}", token=ada).json["party_size"] == 2


# ============================================================ concurrency
def test_C1_3_C1_5_C1_46_concurrent_conflicting_bookings(c, ada, bob):
    toks = [ada, bob] + [c.signup(f"c{i}@example.com").json["token"] for i in range(8)]
    fns = [(lambda t=toks[i % 10]: Client(c.base_url, timeout=15).book(t, k(), "r_all", "a_3", f"{FUT_DAY}T18:00", 2)) for i in range(50)]
    res = burst(fns)
    for r in res:
        assert not isinstance(r, BaseException), r
    statuses = sorted(r.status for r in res)
    assert statuses.count(201) == 1 and statuses.count(409) == 49, statuses
    assert all(r.code == "table_unavailable" for r in res if r.status == 409)
    total = sum(len(c.get("/reservations", token=t).json["reservations"]) for t in toks)
    assert total == 1 + 1  # the winner plus bob's seeded booking
    by = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in c.availability("r_all", FUT_DAY, 1).json["slots"]}
    assert by["18:00"] == ["a_1", "a_2"]


def test_C1_8_fifty_in_flight_mixed(c, ada):
    fns = []
    for i in range(50):
        if i % 5 == 0:
            fns.append(lambda: Client(c.base_url, timeout=15).availability("r_all", FUT_DAY, 2))
        elif i % 5 == 1:
            fns.append(lambda: Client(c.base_url, timeout=15).get("/restaurants"))
        elif i % 5 == 2:
            fns.append(lambda i=i: Client(c.base_url, timeout=15).book(ada, k(), "r_all", "a_1", f"{FUT_DAY}T{10 + i % 12:02d}:00", 2))
        elif i % 5 == 3:
            fns.append(lambda: Client(c.base_url, timeout=15).get("/reservations", token=ada))
        else:
            fns.append(lambda: Client(c.base_url, timeout=15).get("/reservations", headers={"Authorization": "Bearer nope"}))
    res = burst(fns)
    for r in res:
        assert not isinstance(r, BaseException), r
        assert r.status < 500, r
