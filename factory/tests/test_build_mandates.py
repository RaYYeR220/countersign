import subprocess, sys
from pathlib import Path
from factory import build_mandates as bm

SEATS = ["foreman", "builder", "stylist", "oracle", "auditor"]

def test_generated_up_to_date(kit_dir):
    r = subprocess.run([sys.executable, str(kit_dir/"factory"/"build_mandates.py"), "--check"], cwd=kit_dir)
    assert r.returncode == 0

def test_headers(kit_dir):
    for s in SEATS:
        lines = (kit_dir/"mandates"/f"{s}.md").read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("Harness: ") and len(lines[0]) > 9
        assert lines[1].startswith("Model: ") and "PENDING" not in lines[1]

def test_protocol_in_every_mandate(kit_dir):
    proto = (kit_dir/"mandates-src"/"protocol.md").read_text(encoding="utf-8").strip()
    for s in SEATS:
        assert proto in (kit_dir/"mandates"/f"{s}.md").read_text(encoding="utf-8")

def test_only_auditor_accepts(kit_dir):
    for s in SEATS:
        t = (kit_dir/"mandates-src"/f"{s}.md").read_text(encoding="utf-8")
        if s != "auditor":
            assert "result=ACCEPT" not in t.replace("never post `[VERDICT] ... result=ACCEPT`", "")
