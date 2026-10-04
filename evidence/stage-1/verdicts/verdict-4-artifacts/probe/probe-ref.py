import json, sys
sys.path.insert(0, "/aud/stage-1/verify/audit"); sys.path.insert(0, "/aud/factory/tools")
from audit import Checker, Client, Ctx, S, fixture
c = Client(sys.argv[1], Checker()); ctx = Ctx()
for ref in ("bad ref", "abc123", "SEED1", "A" * 13, "SEED-01"):
    fx = fixture(ctx); fx["reservations"][0]["reference"] = ref
    r = c.req("POST", "/_test/reset", fx, timeout=12)
    out = [ref, "reset", r.status, r.code()]
    if r.status == 204:
        e = c.req("GET", "/_test/export").json
        ri = c.req("POST", "/_test/import", e, timeout=12)
        out += ["export->import", ri.status, ri.code()]
        tok = c.req("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}).json["token"]
        g = c.req("GET", "/reservations/" + ref.replace(" ", "%20"), token=tok)
        out += ["GET by ref", g.status]
    print(out)
