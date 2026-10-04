"""Run the event harness natively on Windows, isolated mode included.

The harness passes the suite path to its Linux runner container as a Windows relative path
(`toy\\test\\stage_1`), which pytest inside the container cannot find. This wrapper leaves
the event package untouched and rewrites only that argument to forward slashes for the
`docker run ... pytest` commands. Everything else is the stock harness.

Run it from the event package directory (or set CS_KICKOFF to it), with the same arguments
as `python -m harness`:

    cd <kickoff> && PYTHONUTF8=1 <venv>/Scripts/python.exe <repo>/factory/setup/harness_win.py \
        run --track <track> --repo <abs> --stage <n> --mode isolated --out <abs>
"""
import os
import pathlib
import re
import subprocess
import sys
import types

KICKOFF = pathlib.Path(os.environ.get("CS_KICKOFF") or pathlib.Path.cwd())
if not (KICKOFF / "harness" / "cli.py").exists():
    sys.exit(f"harness_win: no event package at {KICKOFF}; run from it or set CS_KICKOFF")
sys.path.insert(0, str(KICKOFF))

import harness.cli as cli  # noqa: E402

_REL_SUITE = re.compile(r"[A-Za-z0-9_]+(?:\\[A-Za-z0-9_]+)+")
_real_run = subprocess.run


def _run(cmd, *args, **kwargs):
    if isinstance(cmd, list) and cmd[:2] == ["docker", "run"] and "pytest" in cmd:
        cmd = [c.replace("\\", "/") if _REL_SUITE.fullmatch(c) else c for c in cmd]
    return _real_run(cmd, *args, **kwargs)


# Patch only the `subprocess` name the CLI sees; the docker driver is unaffected.
cli.subprocess = types.SimpleNamespace(**{**vars(subprocess), "run": _run})

if __name__ == "__main__":
    raise SystemExit(cli.main())
