"""Start, stop and bound long-running processes without hanging the caller.

A shell tool call stays open until every process holding its output pipe exits. A server
started from that call inherits the pipe, so the call never returns. This tool detaches
background processes completely (no inherited handles, output to a log file) and runs
foreground work with a hard timeout that kills the whole process tree.

    python bg.py start --name api [--cwd DIR] [--health http://127.0.0.1:18100/health] [--wait 60] -- <cmd> <args>
    python bg.py stop --name api          # or: stop --all
    python bg.py list
    python bg.py run --timeout 900 [--tail 40] [--log FILE] -- <cmd> <args>

State (pid files and logs) lives in `.bg/` under the current directory, or BG_STATE_DIR.
`run` exits with the command's exit code, or 124 when the timeout killed it.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WINDOWS = os.name == "nt"
TIMEOUT_EXIT = 124


def state_dir() -> Path:
    d = Path(os.environ.get("BG_STATE_DIR") or (Path.cwd() / ".bg"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _spawn(cmd, cwd, log_path: Path) -> subprocess.Popen:
    log = open(log_path, "ab")
    kwargs = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True)
    if WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs)
    finally:
        log.close()


def kill_tree(pid: int) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def alive(pid: int) -> bool:
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def healthy(url: str, deadline: float) -> bool:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if 200 <= r.status < 300:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def cmd_start(a) -> int:
    d = state_dir()
    meta = d / f"{a.name}.json"
    if meta.exists():
        old = json.loads(meta.read_text())
        if alive(old["pid"]):
            print(json.dumps({"name": a.name, "error": "already running", **old}))
            return 1
    log = d / f"{a.name}.log"
    proc = _spawn(a.cmd, a.cwd, log)
    info = {"name": a.name, "pid": proc.pid, "log": str(log), "cmd": a.cmd}
    meta.write_text(json.dumps(info))
    if a.health:
        ok = healthy(a.health, time.monotonic() + a.wait)
        info["healthy"] = ok
        if not ok:
            kill_tree(proc.pid)
            meta.unlink(missing_ok=True)
            info["error"] = f"not healthy within {a.wait}s; stopped. Last log lines follow."
            info["log_tail"] = tail(log, 20)
            print(json.dumps(info, indent=2))
            return 1
    print(json.dumps(info, indent=2))
    return 0


def cmd_stop(a) -> int:
    d = state_dir()
    metas = sorted(d.glob("*.json")) if a.all else [d / f"{a.name}.json"]
    stopped = []
    for meta in metas:
        if not meta.exists():
            continue
        info = json.loads(meta.read_text())
        kill_tree(info["pid"])
        meta.unlink(missing_ok=True)
        stopped.append(info["name"])
    print(json.dumps({"stopped": stopped}))
    return 0


def cmd_list(_a) -> int:
    rows = []
    for meta in sorted(state_dir().glob("*.json")):
        info = json.loads(meta.read_text())
        info["alive"] = alive(info["pid"])
        rows.append(info)
    print(json.dumps(rows, indent=2))
    return 0


def tail(path: Path, n: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def cmd_run(a) -> int:
    log = Path(a.log) if a.log else state_dir() / f"run-{int(time.time() * 1000)}.log"
    started = time.monotonic()
    proc = _spawn(a.cmd, a.cwd, log)
    try:
        code = proc.wait(timeout=a.timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        proc.wait()
        code = TIMEOUT_EXIT
    elapsed = time.monotonic() - started
    print(tail(log, a.tail))
    status = "timed out and was killed" if code == TIMEOUT_EXIT else f"exit {code}"
    print(f"[bg run] {status} after {elapsed:.1f}s; full output: {log}")
    return code


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="action", required=True)
    s = sub.add_parser("start")
    s.add_argument("--name", required=True)
    s.add_argument("--cwd")
    s.add_argument("--health")
    s.add_argument("--wait", type=float, default=60)
    t = sub.add_parser("stop")
    g = t.add_mutually_exclusive_group(required=True)
    g.add_argument("--name")
    g.add_argument("--all", action="store_true")
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("--timeout", type=float, required=True)
    r.add_argument("--tail", type=int, default=40)
    r.add_argument("--log")
    r.add_argument("--cwd")
    a = ap.parse_args(argv)
    a.cmd = cmd
    if a.action in ("start", "run") and not cmd:
        ap.error("give the command after --")
    return {"start": cmd_start, "stop": cmd_stop, "list": cmd_list, "run": cmd_run}[a.action](a)


if __name__ == "__main__":
    sys.exit(main())
