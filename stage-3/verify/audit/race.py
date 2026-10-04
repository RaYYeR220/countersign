#!/usr/bin/env python3
"""Race proofs (battery step 6): disable one concurrency/atomicity guard in a scratch copy, show the
relevant attacks fail; run the unmodified copy, show they pass.

    python race.py --src <clean-clone>/stage-3 --work C:/countersign/tmp/aud-race-<k> --guard "store write lock" \
        --edit "internal/state/store.go::s.mu.Lock()::/*race*/" [--edit ...] --groups burst --rounds 3 --out race-1.json

`--edit FILE::OLD::NEW` replaces every literal occurrence of OLD in FILE (at least one must exist).
RED-without = some hard check fails (or the binary crashes) with the guard disabled.
GREEN-with  = no hard check fails on the unmodified build.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mutate  # noqa: E402


def run(root: Path, scratch: Path, port: int, a, tag: str) -> dict:
    """Evaluate the build up to a.repeat times (in containers, see mutate.evaluate); red on the first failure."""
    runs = []
    for k in range(a.repeat):
        r = mutate.evaluate(root, scratch, port, a, f"{tag}{k}")
        if r.get("status") == "INVALID":
            return {"built": False, "error": r.get("note")}
        if r.get("status") == "KILLED":
            return {"built": True, "healthy": False, "red": True, "runs": runs}
        runs.append({"failing": r["failing"][:10], "n": len(r["failing"]), "note": r.get("note", ""), "crashed": r.get("crashed")})
        if r["failing"] or r.get("note") or r.get("crashed"):
            break
    return {"built": True, "healthy": True, "runs": runs, "red": any(x["n"] or x["note"] or x["crashed"] for x in runs)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--guard", required=True)
    ap.add_argument("--edit", action="append", required=True)
    ap.add_argument("--groups", default="burst")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--repeat", type=int, default=3, help="re-run the killers up to N times while the disabled build stays green")
    ap.add_argument("--port", type=int, default=18390)
    ap.add_argument("--oracle")
    ap.add_argument("--pytest-python", default=sys.executable)
    ap.add_argument("--killer-timeout", type=float, default=300)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    work = Path(a.work)
    shutil.rmtree(work, ignore_errors=True)
    good, bad = work / "with", work / "without"
    for d in (good, bad):
        shutil.copytree(a.src, d, ignore=shutil.ignore_patterns("verify", ".git"))
    applied = []
    for e in a.edit:
        f, old, new = (x.replace("\\n", "\n").replace("\\t", "\t") for x in e.split("::", 2))
        p = bad / f
        text = p.read_bytes().decode("utf-8")
        n = text.count(old)
        if n == 0:
            sys.exit(f"edit not applicable: {old!r} not in {f}")
        p.write_bytes(text.replace(old, new).encode("utf-8"))
        applied.append({"file": f, "old": old, "new": new, "occurrences": n})
    scratch = work / "scratch"
    scratch.mkdir()
    a.docker = True
    without = run(bad, scratch, a.port, a, "without")
    a.repeat = 1
    with_ = run(good, scratch, a.port + 1, a, "with")
    res = {"guard": a.guard, "edits": applied, "groups": a.groups, "rounds": a.rounds,
           "red_without": bool(without.get("red")) or without.get("healthy") is False,
           "green_with": with_.get("healthy") is True and not with_.get("red"),
           "without": without, "with": with_}
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("guard", "edits", "red_without", "green_with")}, indent=1))
    shutil.rmtree(work, ignore_errors=True)
    return 0 if res["red_without"] and res["green_with"] else 1


if __name__ == "__main__":
    sys.exit(main())
