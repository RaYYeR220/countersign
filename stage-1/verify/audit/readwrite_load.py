"""Concurrent readers and writers on the same state (for race-detector proofs of read locks).

    python readwrite_load.py --base http://host:8080 --rounds 5
Every round: reset, sign up 10 users, then release 60 requests together — GET /reservations,
GET /availability, GET /restaurants and GET /_test/export interleaved with keyed POST /reservations
and signups. Prints status counts; the judge is the race detector's report, not this output.
"""
import argparse, json, os, sys, uuid
sys.path.insert(0, os.environ.get("BURST_DIR", "/aud/factory/tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burst  # noqa: E402
from audit import Checker, Client, Ctx, S, fixture, booking_body  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--base", required=True); ap.add_argument("--rounds", type=int, default=5)
a = ap.parse_args()
chk, ctx = Checker(), Ctx()
c = Client(a.base, chk)
for rnd in range(a.rounds):
    s = S(c, chk, ctx); s.reset()
    users = [s.signup() for _ in range(10)]
    reqs = []
    for i in range(60):
        u = users[i % 10]
        h = {"Authorization": f"Bearer {u}", "Content-Type": "application/json"}
        k = i % 6
        if k == 0:
            reqs.append(burst.Req("GET", a.base + "/reservations", h))
        elif k == 1:
            reqs.append(burst.Req("GET", a.base + f"/availability?restaurant_id=r_anker&date={ctx.fri}&party_size=2"))
        elif k == 2:
            reqs.append(burst.Req("GET", a.base + "/_test/export"))
        elif k == 3:
            reqs.append(burst.Req("POST", a.base + "/auth/signup", {"Content-Type": "application/json"},
                                  json.dumps({"email": f"{uuid.uuid4().hex[:8]}@x.io", "password": "password1", "display_name": "L"}).encode()))
        else:
            t = ["t_1", "t_2", "t_3"][i % 3]
            reqs.append(burst.Req("POST", a.base + "/reservations", {**h, "Idempotency-Key": uuid.uuid4().hex},
                                  json.dumps(booking_body("r_anker", t, ctx.D(ctx.fri, ["18:00", "19:30", "21:00"][i % 3]), 2)).encode()))
    print(rnd, burst.summarize(burst.fire(reqs))["statuses"], flush=True)
