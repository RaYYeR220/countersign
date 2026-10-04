import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

BG = Path(__file__).resolve().parents[1] / "factory" / "tools" / "bg.py"
if not BG.exists():  # tests shipped under factory/tests
    BG = Path(__file__).resolve().parents[1] / "tools" / "bg.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bg(args, cwd, timeout=60):
    """Call bg.py the way a seat's shell tool does: with captured pipes."""
    return subprocess.run([sys.executable, str(BG), *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def test_start_returns_promptly_and_stop_kills(tmp_path):
    port = free_port()
    t0 = time.monotonic()
    r = bg(["start", "--name", "web", "--health", f"http://127.0.0.1:{port}/", "--wait", "30", "--",
            sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert time.monotonic() - t0 < 30          # the caller's pipes did not keep it open
    info = json.loads(r.stdout)
    assert info["healthy"] is True
    rows = json.loads(bg(["list"], tmp_path).stdout)
    assert rows[0]["name"] == "web" and rows[0]["alive"] is True
    assert json.loads(bg(["stop", "--name", "web"], tmp_path).stdout)["stopped"] == ["web"]
    time.sleep(1)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


def test_unhealthy_start_is_stopped_and_reported(tmp_path):
    port = free_port()
    r = bg(["start", "--name", "dead", "--health", f"http://127.0.0.1:{port}/", "--wait", "2", "--",
            sys.executable, "-c", "import time; time.sleep(60)"], tmp_path)
    assert r.returncode == 1 and "not healthy" in r.stdout
    assert json.loads(bg(["list"], tmp_path).stdout) == []


def test_run_timeout_kills_tree(tmp_path):
    t0 = time.monotonic()
    r = bg(["run", "--timeout", "2", "--", sys.executable, "-c", "import time; print('working', flush=True); time.sleep(60)"],
           tmp_path)
    assert r.returncode == 124
    assert time.monotonic() - t0 < 20
    assert "working" in r.stdout and "timed out" in r.stdout


def test_run_does_not_wait_for_detached_grandchildren(tmp_path):
    child = ("import subprocess, sys; "
             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
             "print('parent done', flush=True)")
    t0 = time.monotonic()
    r = bg(["run", "--timeout", "30", "--", sys.executable, "-c", child], tmp_path)
    assert r.returncode == 0 and "parent done" in r.stdout
    assert time.monotonic() - t0 < 20


def test_run_passes_exit_code(tmp_path):
    r = bg(["run", "--timeout", "10", "--", sys.executable, "-c", "import sys; sys.exit(3)"], tmp_path)
    assert r.returncode == 3
