#!/usr/bin/env python3
"""Mutation testing of the Go domain core, killed through HTTP (battery step 5).

Each mutant is one textual change to one non-test .go file under the target packages. A mutant is
built natively with the host Go toolchain, started on a private port, and attacked with the killers:
the Auditor battery (audit.py) and, when given, the Oracle acceptance suite (pytest). A mutant is
KILLED when it crashes, never becomes healthy, or any check that passed on the unmutated baseline
fails; SURVIVED otherwise; INVALID when it does not compile (excluded from the rate).

    python mutate.py --src <clean-clone>/stage-2 --work C:/countersign/tmp/aud-mut-<k> \
        --targets internal/state,internal/api --budget 1200 --workers 4 --seed 1 \
        [--oracle <clean-clone>/stage-2/verify/oracle] --out <dir>/mutation.json

Survivors are work items for the Oracle (stronger tests), never for implementers.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXE = ".exe" if os.name == "nt" else ""
OPS = [
    ("CB", r"(?<![<>=!:])<=(?!=)", "<"), ("CB", r"(?<![<>=!:-])<(?![<=-])", "<="),
    ("CB", r"(?<![<>=!:])>=(?!=)", ">"), ("CB", r"(?<![<>=!:-])>(?![>=])", ">="),
    ("NEG", r"==", "!="), ("NEG", r"!=", "=="),
    ("LOG", r"&&", "||"), ("LOG", r"\|\|", "&&"),
    ("ARI", r" \+ ", " - "), ("ARI", r" - ", " + "),
    ("BOOL", r"\breturn true\b", "return false"), ("BOOL", r"\breturn false\b", "return true"),
    ("INV", r"\bif !", "if "),
    ("NIL", r"\breturn nil\b(?!,)", "return errors.New(\"mutant\")"),
]
SKIP_LINE = re.compile(r"^\s*(//|import\b|package\b|\"|`)|^\s*$")


def outside_strings(line: str, pos: int) -> bool:
    before = line[:pos]
    if "//" in before:
        return False
    return before.count('"') % 2 == 0 and before.count("`") % 2 == 0 and before.count("'") % 2 == 0


def gen_mutants(root: Path, targets: list[str]) -> list[dict]:
    out = []
    for t in targets:
        for f in sorted((root / t).rglob("*.go")):
            if f.name.endswith("_test.go"):
                continue
            rel = f.relative_to(root).as_posix()
            lines = f.read_text(encoding="utf-8").split("\n")
            for i, line in enumerate(lines):
                if SKIP_LINE.match(line):
                    continue
                for op, pat, rep in OPS:
                    for m in re.finditer(pat, line):
                        if not outside_strings(line, m.start()):
                            continue
                        if op == "NIL" and "errors" not in "".join(lines[:40]):
                            continue
                        new = line[:m.start()] + rep + line[m.end():]
                        out.append({"file": rel, "line": i + 1, "op": op, "orig": line, "mutated": new})
    return out


def apply(root: Path, mt: dict, revert: str | None = None) -> str:
    """Write the mutated line (or restore the original text); returns the original file text."""
    p = root / mt["file"]
    if revert is not None:
        p.write_bytes(revert.encode("utf-8"))
        return revert
    text = p.read_bytes().decode("utf-8")
    lines = text.split("\n")
    assert lines[mt["line"] - 1] == mt["orig"], f"stale mutant {mt['file']}:{mt['line']}"
    lines[mt["line"] - 1] = mt["mutated"]
    p.write_bytes("\n".join(lines).encode("utf-8"))
    return text


def build(root: Path, out: Path) -> tuple[bool, str]:
    env = {**os.environ, "CGO_ENABLED": "0", "GOFLAGS": "-mod=vendor", "GOTOOLCHAIN": "local"}
    r = subprocess.run(["go", "build", "-o", str(out), "."], cwd=root, env=env, capture_output=True, text=True, timeout=300,
                       stdin=subprocess.DEVNULL)
    return r.returncode == 0, (r.stderr or "")[-400:]


def healthy(port: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def kill(p: subprocess.Popen):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, stdin=subprocess.DEVNULL)
    else:
        p.kill()
    try:
        p.wait(10)
    except Exception:
        pass


def run_killers(port: int, scratch: Path, a) -> tuple[set, str]:
    """Return the set of failing check ids and a status note ("" when the killers ran to the end)."""
    failing = set()
    out = scratch / "audit.json"
    if out.exists():
        out.unlink()
    try:
        subprocess.run([sys.executable, str(HERE / "audit.py"), "--base", f"http://127.0.0.1:{port}", "--rounds", str(getattr(a, "rounds", 1)),
                        "--groups", a.groups, "--wait", "10", "--out", str(out)], capture_output=True, timeout=a.killer_timeout,
                       stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return failing, "audit timeout"
    if not out.exists():
        return failing, "audit produced no report"
    d = json.loads(out.read_text(encoding="utf-8"))
    failing |= {f"audit:{r['group']}/{r['name']}" for r in d["results"] if not r["ok"] and not r["soft"]}
    if a.oracle:
        xml = scratch / "oracle.xml"
        if xml.exists():
            xml.unlink()
        try:
            subprocess.run([a.pytest_python, "-m", "pytest", a.oracle, "-q", "-p", "no:cacheprovider", "--base-url",
                            f"http://127.0.0.1:{port}", f"--junitxml={xml}"], capture_output=True, timeout=a.killer_timeout,
                           stdin=subprocess.DEVNULL, cwd=str(scratch))
        except subprocess.TimeoutExpired:
            return failing, "oracle timeout"
        if xml.exists():
            for tc in ET.parse(xml).getroot().iter("testcase"):
                if tc.find("failure") is not None or tc.find("error") is not None:
                    failing.add(f"oracle:{tc.get('classname')}::{tc.get('name')}")
        else:
            return failing, "oracle produced no report"
    return failing, ""


AUDIT_ROOT = HERE.parents[2]          # repository root holding stage-1/verify/audit and factory/tools
GO_IMAGE = "golang:1.26-alpine"
RUNNER_IMAGE = "auditor-runner-py"    # Dockerfile.runner-py: python 3.12 + tzdata + pytest


def _docker(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def evaluate_docker(root: Path, scratch: Path, a, tag: str) -> dict:
    """Build a Linux binary in a Go container, then run server and killers together in one runner
    container (own network namespace: no host ephemeral-port pressure, 2 CPUs / 2 GiB)."""
    for f in ("tk", "audit.json", "oracle.xml", "crashed", "nohealth", "audit.rc"):
        try:
            (scratch / f).unlink()
        except OSError:
            pass
    b = _docker(["run", "--rm", "-v", f"{root.as_posix()}:/src", "-v", f"{scratch.as_posix()}:/out",
                 "-v", "auditor-gocache:/root/.cache/go-build", "-e", "CGO_ENABLED=0", "-e", "GOFLAGS=-mod=vendor",
                 "-e", "GOTOOLCHAIN=local", "-w", "/src", GO_IMAGE, "go", "build", "-o", "/out/tk", "."], 300)
    if b.returncode != 0:
        return {"status": "INVALID", "note": (b.stderr or b.stdout)[-400:]}
    kt = int(a.killer_timeout)
    script = (
        "/w/tk >/dev/null 2>/w/server.err & P=$!; i=0; "
        "until wget -qO- http://127.0.0.1:8080/health >/dev/null 2>&1; do i=$((i+1)); "
        "if [ $i -gt 150 ]; then touch /w/nohealth; exit 0; fi; sleep 0.1; done; "
        f"timeout {kt} python /aud/stage-2/verify/audit/audit.py --base http://127.0.0.1:8080 "
        f"--rounds {getattr(a, 'rounds', 1)} --groups {a.groups} --wait 10 --out /w/audit.json >/dev/null 2>&1; echo $? >/w/audit.rc; "
        + (f"timeout {kt} python -m pytest /oracle -q -p no:cacheprovider --base-url http://127.0.0.1:8080 "
           "--junitxml=/w/oracle.xml >/dev/null 2>&1; " if a.oracle else "")
        + "kill -0 $P 2>/dev/null || touch /w/crashed; kill $P 2>/dev/null; exit 0")
    vols = ["-v", f"{scratch.as_posix()}:/w", "-v", f"{AUDIT_ROOT.as_posix()}:/aud:ro"]
    if a.oracle:
        vols += ["-v", f"{Path(a.oracle).as_posix()}:/oracle:ro"]
    name = f"auditor-mut-{tag}-{os.getpid()}"
    try:
        _docker(["run", "--rm", "--name", name, "--network", "none", "--cpus", "2", "--memory", "2g", "-e", "PORT=8080",
                 "-e", "PYTHONDONTWRITEBYTECODE=1", "-w", "/tmp", *vols, RUNNER_IMAGE, "sh", "-c", script], 3 * kt + 60)
    except subprocess.TimeoutExpired:
        _docker(["rm", "-f", name], 30)
        return {"failing": [], "note": "runner timeout", "crashed": False}
    if (scratch / "nohealth").exists():
        return {"status": "KILLED", "note": "never healthy", "failing": []}
    failing, note = set(), ""
    out = scratch / "audit.json"
    if out.exists():
        d = json.loads(out.read_text(encoding="utf-8"))
        failing |= {f"audit:{r['group']}/{r['name']}" for r in d["results"] if not r["ok"] and not r["soft"]}
    else:
        note = "audit produced no report (rc %s)" % ((scratch / "audit.rc").read_text().strip() if (scratch / "audit.rc").exists() else "?")
    if a.oracle:
        xml = scratch / "oracle.xml"
        if xml.exists():
            for tc in ET.parse(xml).getroot().iter("testcase"):
                if tc.find("failure") is not None or tc.find("error") is not None:
                    failing.add(f"oracle:{tc.get('name')}")
        else:
            note = note or "oracle produced no report"
    crashed = (scratch / "crashed").exists()
    return {"failing": sorted(failing), "note": note or ("crashed" if crashed else ""), "crashed": crashed}


def evaluate(root: Path, scratch: Path, port: int, a, tag: str) -> dict:
    if getattr(a, "docker", True):
        return evaluate_docker(root, scratch, a, tag)
    exe = scratch / f"tk-{tag}{EXE}"
    ok, err = build(root, exe)
    if not ok:
        return {"status": "INVALID", "note": err}
    p = subprocess.Popen([str(exe)], env={**os.environ, "PORT": str(port)}, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not healthy(port, 15):
            return {"status": "KILLED", "note": "never healthy", "failing": []}
        failing, note = run_killers(port, scratch, a)
        crashed = p.poll() is not None
        return {"failing": sorted(failing), "note": note or ("crashed" if crashed else ""), "crashed": crashed}
    finally:
        kill(p)
        try:
            exe.unlink()
        except OSError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="stage folder of the clean clone (contains go.mod)")
    ap.add_argument("--work", required=True)
    ap.add_argument("--targets", required=True, help="comma-separated package dirs relative to --src")
    ap.add_argument("--budget", type=float, default=1200, help="seconds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--port-base", type=int, default=18320)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--groups", default="core,auth,availability,create,reads,cancel,patch,dst,idem,moves,export")
    ap.add_argument("--oracle", help="path of the Oracle acceptance suite (pytest)")
    ap.add_argument("--pytest-python", default=sys.executable)
    ap.add_argument("--killer-timeout", type=float, default=240)
    ap.add_argument("--native", action="store_true", help="build and run on the host instead of in containers")
    ap.add_argument("--only-survivors", help="mutation.json of an earlier run: re-evaluate only its survivors (same mutants)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    a.docker = not a.native
    t0 = time.monotonic()
    work = Path(a.work)
    if work.exists():
        shutil.rmtree(work)
    base = work / "base"
    shutil.copytree(a.src, base, ignore=shutil.ignore_patterns("verify", ".git"))
    mutants = gen_mutants(base, [t.strip() for t in a.targets.split(",") if t.strip()])
    random.Random(a.seed).shuffle(mutants)
    if a.only_survivors:
        prev = json.loads(Path(a.only_survivors).read_text(encoding="utf-8"))
        keep = {(r["file"], r["line"], r["op"], r["mutated"]) for r in prev.get("survivors", [])}
        mutants = [m for m in mutants if (m["file"], m["line"], m["op"], m["mutated"]) in keep]
    print(f"{len(mutants)} mutants generated", flush=True)
    base_scratch = work / "scratch-base"
    base_scratch.mkdir(parents=True)
    baseline = evaluate(base, base_scratch, a.port_base, a, "base")
    if baseline.get("status") in ("INVALID", "KILLED") or baseline.get("note"):
        print(f"baseline unusable: {baseline}", file=sys.stderr)
        return 2
    base_fail = set(baseline["failing"])
    print(f"baseline: {len(base_fail)} failing checks (excluded from kill criteria)", flush=True)

    results, lock, queue = [], threading.Lock(), list(enumerate(mutants))
    deadline = t0 + a.budget

    def worker(w: int):
        root = work / f"w{w}"
        shutil.copytree(base, root)
        scratch = work / f"scratch-{w}"
        scratch.mkdir()
        port = a.port_base + 1 + w
        while time.monotonic() < deadline:
            with lock:
                if not queue:
                    return
                idx, mt = queue.pop(0)
            original = apply(root, mt)
            try:
                r = evaluate(root, scratch, port, a, f"m{idx}")
                # A kill must reproduce: re-run once and keep only failures seen both times, so load or
                # timing noise never counts as a kill.
                if r.get("status") is None and (set(r.get("failing", [])) - base_fail or r.get("note") or r.get("crashed")):
                    r2 = evaluate(root, scratch, port, a, f"m{idx}b")
                    if r2.get("status") == "KILLED":
                        r = r2
                    else:
                        r["failing"] = sorted(set(r.get("failing", [])) & set(r2.get("failing", [])))
                        if not (r.get("note") and r2.get("note")):
                            r["note"] = ""
                        r["crashed"] = bool(r.get("crashed") and r2.get("crashed"))
            finally:
                apply(root, mt, revert=original)
            if r.get("status") != "INVALID":
                new = sorted(set(r.get("failing", [])) - base_fail)
                r["status"] = r.get("status") or ("KILLED" if new or r.get("crashed") or r.get("note") else "SURVIVED")
                r["killed_by"] = new[:5]
                r.pop("failing", None)
            with lock:
                results.append({"id": idx, **mt, **r})
                # partial results after every mutant, so a hung container cannot lose the run
                Path(a.out + ".partial").write_text(json.dumps({"partial": True, "results": results}, indent=1), encoding="utf-8")
                k = sum(1 for x in results if x["status"] == "KILLED")
                s = sum(1 for x in results if x["status"] == "SURVIVED")
                print(f"[{int(time.monotonic() - t0)}s] #{idx} {mt['file']}:{mt['line']} {mt['op']} -> {r['status']}  (killed {k}, survived {s})",
                      flush=True)

    threads = [threading.Thread(target=worker, args=(w,), daemon=True) for w in range(a.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    killed = [r for r in results if r["status"] == "KILLED"]
    survived = [r for r in results if r["status"] == "SURVIVED"]
    invalid = [r for r in results if r["status"] == "INVALID"]
    total = len(killed) + len(survived)
    summary = {"tool": "mutate.py (textual Go mutants, HTTP killers)", "seed": a.seed, "generated": len(mutants),
               "evaluated": len(results), "killed": len(killed), "survived": len(survived), "invalid": len(invalid),
               "kill_rate": round(len(killed) / total, 3) if total else None, "elapsed_s": round(time.monotonic() - t0),
               "baseline_failing": sorted(base_fail), "killers": ["audit.py"] + (["oracle pytest"] if a.oracle else [])}
    Path(a.out).write_text(json.dumps({**summary, "survivors": survived, "results": results}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
