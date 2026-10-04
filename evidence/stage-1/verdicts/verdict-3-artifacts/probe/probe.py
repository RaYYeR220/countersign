import json, sys, uuid, copy
sys.path.insert(0, "/aud/stage-1/verify/audit"); sys.path.insert(0, "/aud/factory/tools")
from audit import Checker, Client, Ctx, S, booking_body
base = sys.argv[1]
chk, ctx = Checker(), Ctx(); c = Client(base, chk); s = S(c, chk, ctx); s.reset()
r = c.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.thu, "19:00"), 2), token=s.ada, key="probe-key")
ref = r.json["reference"]
E = c.req("GET", "/_test/export").json
def walk(v, path):
    if isinstance(v, dict):
        if v.get("reference") == ref: print("record with reference", ref, "at", path, "keys:", sorted(v)[:12])
        for k, x in v.items(): walk(x, path + [k])
    elif isinstance(v, list):
        for i, x in enumerate(v): walk(x, path + [i])
walk(E["state"], [])
print("top-level state keys:", list(E["state"].keys()))
# tamper like the Oracle: first record found with reference=ref gets reference SEED01
hits = []
def find(v):
    if isinstance(v, dict):
        if v.get("reference") == ref: hits.append(v)
        for x in v.values(): find(x)
    elif isinstance(v, list):
        for x in v: find(x)
T = copy.deepcopy(E); find(T["state"]); hits[0]["reference"] = "SEED01"
ri = c.req("POST", "/_test/import", T, timeout=12)
print("import of first-hit tamper:", ri.status, ri.text(200))
lst = c.req("GET", "/reservations", token=s.ada).json["reservations"]
print("ada reservations with reference SEED01:", sum(1 for x in lst if x["reference"] == "SEED01"), "; with", ref, ":", sum(1 for x in lst if x["reference"] == ref))
rp = c.req("POST", "/reservations", booking_body("r_anker", "t_1", ctx.D(ctx.thu, "19:00"), 2), token=s.ada, key="probe-key")
print("replay after import:", rp.status, "reference in replayed body:", (rp.json or {}).get("reference"))
# tamper the reservation record itself
c.req("POST", "/_test/import", E, timeout=12)
hits.clear(); T2 = copy.deepcopy(E); find(T2["state"])
recs = [h for h in hits if "user_id" in h or "UserID" in h or "status" in h]
print("reservation-like hits:", len(recs), "of", len(hits))
