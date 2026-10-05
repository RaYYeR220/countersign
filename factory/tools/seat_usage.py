"""Per-seat token use and estimated cost for a run, from the seats' own Claude Code logs.

Band's `band usage` reads the operator's default Claude Code profile, so it cannot see seats
that run with their own CLAUDE_CONFIG_DIR. Claude Code writes one folder of session logs
per working directory under `<profile>/projects/`; each seat works in its own checkout, so
each folder is one seat. This tool runs ccusage (`npx ccusage`) on one seat folder at a time
and writes a JSON file that metrics.py reads as usage.

    python seat_usage.py --profile C:/Users/<you>/.claude-countersign --since 20261004 \
        --seat foreman=C:/countersign/result --seat builder=C:/countersign/seats/builder ... \
        --out evidence/usage.json
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def project_folder(cwd: str) -> str:
    """Claude Code's folder name for a working directory: every non-alphanumeric char is '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd.rstrip("/\\"))


def first_timestamp(log: Path) -> str:
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r'"timestamp":"([^"]+)"', line)
            if m:
                return m.group(1)
    return ""


def copy_sessions(src: Path, dst: Path, after: str) -> int:
    """Copy the session logs (and their subagent folders) that started at or after `after`."""
    dst.mkdir(parents=True)
    n = 0
    for log in src.glob("*.jsonl"):
        if after and first_timestamp(log) < after:
            continue
        shutil.copy2(log, dst / log.name)
        side = src / log.stem
        if side.is_dir():
            shutil.copytree(side, dst / log.stem)
        n += 1
    return n


def ccusage(profile_copy: Path, since: str, until: str) -> dict:
    cmd = ["npx", "-y", "ccusage@latest", "daily", "--json", "--since", since]
    if until:
        cmd += ["--until", until]
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(profile_copy))
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                         shell=os.name == "nt", timeout=600)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"error": (out.stderr or out.stdout)[-500:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True, help="the seats' CLAUDE_CONFIG_DIR")
    ap.add_argument("--seat", action="append", required=True, help="name=working directory")
    ap.add_argument("--since", required=True, help="YYYYMMDD")
    ap.add_argument("--until", default="")
    ap.add_argument("--after", default="", help="ISO time: only sessions that started at or after it")
    ap.add_argument("--model-prefix", default="claude-", help="count only models with this prefix")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    projects = Path(a.profile) / "projects"
    result = {}
    for spec in a.seat:
        name, _, cwd = spec.partition("=")
        src = projects / project_folder(cwd)
        if not src.is_dir():
            result[name] = {"error": f"no session logs at {src}"}
            continue
        with tempfile.TemporaryDirectory() as tmp:
            sessions = copy_sessions(src, Path(tmp) / "projects" / src.name, a.after)
            data = ccusage(Path(tmp), a.since, a.until)
        # ccusage also reads other agents' global logs; keep only this profile's own models.
        keys = ("cost", "inputTokens", "outputTokens", "cacheReadTokens", "cacheCreationTokens")
        sums = dict.fromkeys(keys, 0)
        models = set()
        for day in data.get("daily", []):
            for mb in day.get("modelBreakdowns", []):
                if mb.get("modelName", "").startswith(a.model_prefix):
                    models.add(mb["modelName"])
                    for k in keys:
                        sums[k] += mb.get(k, 0) or 0
        result[name] = {
            "sessions": sessions,
            "totalCost": round(sums["cost"], 2),
            "totalTokens": sum(sums[k] for k in keys[1:]),
            "inputTokens": sums["inputTokens"],
            "outputTokens": sums["outputTokens"],
            "cacheReadTokens": sums["cacheReadTokens"],
            "cacheCreationTokens": sums["cacheCreationTokens"],
            "models": sorted(models),
        }
        if "error" in data:
            result[name]["error"] = data["error"]
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
