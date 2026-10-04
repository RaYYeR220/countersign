"""Stylist's HTTP smoke for WI-19/WI-20 against running containers (not a verification suite).

    python stylist_smoke.py --base http://127.0.0.1:18213 --stage1 http://127.0.0.1:18212
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

FIXTURE = {
    "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
        {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Manager"},
    ],
    "restaurants": [{
        "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
        "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120, "manager_user_ids": ["u_mgr"],
        "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
        "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4}],
    }],
    "reservations": [{"id": "res_s", "reference": "SEED01", "user_id": "u_ada", "restaurant_id": "r_anker",
                      "table_id": "t_1", "starts_at_local": "2026-12-03T19:00", "party_size": 2}],
}
ok = []


def check(cond, what):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + what)


def http(base, method, path, body=None, token=None, key=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if key:
        req.add_header("Idempotency-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def j(raw):
    return json.loads(raw) if raw else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--stage1", required=True)
    a = ap.parse_args()
    B, B1 = a.base.rstrip("/"), a.stage1.rstrip("/")

    check(http(B, "POST", "/_test/reset", FIXTURE)[0] == 204, "reset 204")
    ada = j(http(B, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[1])["token"]
    mgr = j(http(B, "POST", "/auth/login", {"email": "mgr@example.com", "password": "correct horse"})[1])["token"]

    st, raw = http(B, "GET", "/availability?restaurant_id=r_anker&date=2026-12-03&party_size=2&explain=true")
    slots = j(raw)["slots"]
    s19 = [s for s in slots if s["starts_at_local"].endswith("19:00")][0]
    check(st == 200 and [e["table_id"] for e in s19["explain"]] == ["t_1", "t_2"] and
          s19["explain"][0]["rules"] == [{"rule": "capacity", "holds": True}, {"rule": "no_overlap", "holds": False}] and
          s19["available_table_ids"] == ["t_2"], "explain at 19:00: t_1 capacity holds, no_overlap fails; ids [t_2]")
    st, raw = http(B, "GET", "/availability?restaurant_id=r_anker&date=2026-12-03&party_size=2")
    check(st == 200 and b'"explain"' not in raw, "no explain without the parameter")
    for v in ["false", "1", ""]:
        check(http(B, "GET", f"/availability?restaurant_id=r_anker&date=2026-12-03&party_size=2&explain={v}")[0] == 422, f"explain={v!r} → 422")

    pol = {"effective_from": "2026-12-17", "slot_minutes": 60, "reservation_duration_minutes": 120,
           "cancellation_cutoff_minutes": 60, "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
           "capacities": {"t_1": 2, "t_2": 4}}
    check(http(B, "POST", "/restaurants/r_anker/policies", pol, mgr, "p1")[0] == 201, "policy 1 published")
    st, raw = http(B, "GET", "/availability?restaurant_id=r_anker&date=2026-12-17&party_size=2&explain=true")
    later = j(raw)["slots"]
    check([s["starts_at_local"][11:] for s in later] == ["18:00", "19:00", "20:00", "21:00"] and
          later[0]["explain"][0]["policy_version"] == 1, "2026-12-17 follows policy 1 (hourly grid, 120 min)")

    st, raw = http(B, "POST", "/series", {"anchor_reference": "SEED01", "count": 3, "interval_weeks": 2}, ada, "s1")
    s = j(raw)
    occ = s["occurrences"] if st == 201 else []
    check(st == 201 and [o["reservation"]["starts_at_local"] for o in occ] ==
          ["2026-12-03T19:00", "2026-12-17T19:00", "2026-12-31T19:00"], "seeded booking adopted: 3 occurrences 2 weeks apart")
    check(st == 201 and occ[1]["reservation"]["accepted_terms"]["policy_version"] == 1 and
          occ[1]["reservation"]["ends_at"] == "2026-12-17T21:00:00+01:00", "occurrence 1 under policy 1 (120 min)")
    st2, raw2 = http(B, "POST", "/series", {"anchor_reference": "SEED01", "count": 3, "interval_weeks": 2}, ada, "s1")
    check(st2 == 200 and raw2 == raw, "series replay 200 byte-identical")
    st3, raw3 = http(B, "GET", "/series/" + s["series_id"], token=ada)
    check(st3 == 200 and j(raw3) == s, "GET series = 201 body")
    check(http(B, "GET", "/series/" + s["series_id"])[0] == 404, "GET series without token → 404")
    check(http(B, "PATCH", "/reservations/" + occ[2]["reference"], {"party_size": 1}, ada)[0] == 200, "occurrence 2 amended (party 1)")
    g = j(http(B, "GET", "/series/" + s["series_id"], token=ada)[1])
    check(g["revision"] == 2 and g["occurrences"][2]["exception"] is True, "real PATCH → exception, revision 2")

    # Stage-1 export → stage-3 import → adopt an imported booking.
    check(http(B1, "POST", "/_test/reset", {k: v for k, v in FIXTURE.items() if k != "restaurants"} |
               {"restaurants": [{k: v for k, v in FIXTURE["restaurants"][0].items() if k != "manager_user_ids"}]})[0] == 204, "stage-1 reset")
    t1 = j(http(B1, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[1])["token"]
    st, made = http(B1, "POST", "/reservations", {"restaurant_id": "r_anker", "table_id": "t_2",
                                                  "starts_at_local": "2026-12-10T20:00", "party_size": 2}, t1, "b1")
    ref = j(made)["reference"]
    exported = http(B1, "GET", "/_test/export")[1]
    check(http(B, "POST", "/_test/import", exported)[0] == 204, "stage-1 export imported into stage 3")
    st, raw = http(B, "POST", "/series", {"anchor_reference": ref, "count": 2, "interval_weeks": 1}, t1, "s-up")
    check(st == 201 and j(raw)["occurrences"][0]["reservation"]["revision"] == 1, "imported stage-1 booking adopted with its old token")
    rep = http(B, "POST", "/reservations", {"restaurant_id": "r_anker", "table_id": "t_2",
                                            "starts_at_local": "2026-12-10T20:00", "party_size": 2}, t1, "b1")
    check(rep[0] == 200 and rep[1] == made, "stage-1 booking retry replays the original body")

    print(f"{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
