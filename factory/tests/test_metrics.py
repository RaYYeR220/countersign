import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from factory.tools import metrics as m

FX = Path(__file__).parent / "fixtures" / "room.json"
SCRIPT = Path(__file__).resolve().parents[1] / "factory" / "tools" / "metrics.py"

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
U = "99999999-9999-4999-8999-999999999999"


def msg(sender, content="", kind="text", ts="2026-10-01T10:00:00.000Z", stype="Agent", name=None,
        metadata=None):
    return {"id": f"x-{sender}-{ts}", "insertedAt": ts, "messageType": kind, "content": content,
            "threadId": None, "metadata": metadata or {}, "senderId": sender, "senderType": stype,
            "senderName": name or sender}


def section(out: str, title: str) -> str:
    start = out.index(f"## {title}")
    nxt = out.find("\n## ", start + 1)
    return out[start:] if nxt == -1 else out[start:nxt]


# --- plan tests -------------------------------------------------------------------------

def test_seats_and_humans():
    msgs = m.load_room(FX)
    s = m.seat_stats(msgs)
    assert s["A"]["mentions_out"] == 1 and s["B"]["tool_calls"] == 2 and s["C"]["text"] == 0
    assert m.human_messages(msgs) == 1


def test_timeline_and_verdicts():
    msgs = m.load_room(FX)
    assert m.stage_timeline(msgs)[1]["minutes"] == 60.0
    assert m.verdicts(msgs)[1] == {"accept": 1, "reject": 1}


def test_render_without_optional_inputs(tmp_path):
    out = m.render(m.load_room(FX), git=None, usage=m.usage(tmp_path / "missing.json"), denials={}, reports=[])
    assert "| A |" in out and "n/a" in out


# --- room parsing -----------------------------------------------------------------------

def test_fixture_seat_counts_exact():
    s = m.seat_stats(m.load_room(FX))
    assert list(s) == ["A", "B", "C"]
    assert s["A"] == {"text": 2, "tool_calls": 0, "mentions_out": 1, "mentions_in": 1}
    # B mentions A once in text; the mention echoed inside a tool call does not count,
    # and the mention of the human is not a seat-to-seat mention.
    assert s["B"] == {"text": 2, "tool_calls": 2, "mentions_out": 1, "mentions_in": 1}
    assert s["C"] == {"text": 0, "tool_calls": 0, "mentions_out": 0, "mentions_in": 0}


def test_timeline_fields():
    t = m.stage_timeline(m.load_room(FX))
    assert t == {1: {"start": "2026-10-01T10:00:00.000Z", "end": "2026-10-01T11:00:00.000Z", "minutes": 60.0}}


def test_load_room_accepts_bare_message_list(tmp_path):
    p = tmp_path / "room.json"
    p.write_text(json.dumps([msg(A, "hi")]), encoding="utf-8")
    assert len(m.load_room(p)) == 1


def test_load_room_rejects_non_room(tmp_path):
    p = tmp_path / "room.json"
    p.write_text(json.dumps({"messages": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError):
        m.load_room(p)


def test_sender_type_case_insensitive_and_participant_only_seat():
    msgs = [
        msg(A, f"@[[{B}]] hello", stype="agent", name="A"),
        msg(U, "go", stype="USER", name="op"),
        msg(U, "", kind="participant", stype="User", name="op",
            metadata={"action": "joined", "participantId": B, "participantType": "Agent",
                      "participantName": "B"}),
    ]
    s = m.seat_stats(msgs)
    assert s["A"]["mentions_out"] == 1
    assert s["B"] == {"text": 0, "tool_calls": 0, "mentions_out": 0, "mentions_in": 1}
    assert m.human_messages(msgs) == 1


def test_same_target_mentioned_twice_in_one_message_counts_once():
    msgs = [msg(A, f"@[[{B}]] and again @[[{B}]]", name="A"), msg(B, "x", kind="thought", name="B")]
    assert m.seat_stats(msgs)["A"]["mentions_out"] == 1


def test_self_mention_ignored():
    msgs = [msg(A, f"@[[{A}]] note to self", name="A")]
    assert m.seat_stats(msgs)["A"] == {"text": 1, "tool_calls": 0, "mentions_out": 0, "mentions_in": 0}


# --- header grammar ---------------------------------------------------------------------

def test_parse_header_fields():
    h = m.parse_header(f"@[[{A}]] @[[{B}]]\n[VERDICT] stage=3 wi=WI-4 sha=abc1234 result=accept part=1/2\nbody")
    assert h == {"kind": "VERDICT", "stage": 3, "wi": "WI-4", "sha": "abc1234", "result": "ACCEPT",
                 "id": None, "part": (1, 2)}


def test_parse_header_rejects_non_header_lines():
    assert m.parse_header("status update, nothing formal") is None
    assert m.parse_header("text first\n[VERDICT] stage=1 result=ACCEPT") is None
    assert m.parse_header("[UNKNOWN] stage=1") is None
    assert m.parse_header(None) is None


def test_multipart_verdict_counted_once():
    msgs = [
        msg(B, "[VERDICT] stage=2 sha=abc1234 result=REJECT part=1/2", ts="2026-10-01T10:00:00Z"),
        msg(B, "[VERDICT] stage=2 sha=abc1234 result=REJECT part=2/2", ts="2026-10-01T10:01:00Z"),
    ]
    assert m.verdicts(msgs) == {2: {"accept": 0, "reject": 1}}


def test_headers_outside_text_messages_ignored():
    msgs = [msg(B, "[VERDICT] stage=1 sha=abc1234 result=ACCEPT", kind="thought"),
            msg(B, '{"args":{"x":"[KICKOFF] stage=1"}}', kind="tool_call")]
    assert m.verdicts(msgs) == {} and m.stage_timeline(msgs) == {}


def test_stage_without_close_has_open_end():
    msgs = [msg(A, "[KICKOFF] stage=2", ts="2026-10-01T12:00:00Z")]
    assert m.stage_timeline(msgs) == {2: {"start": "2026-10-01T12:00:00Z", "end": None, "minutes": None}}
    out = m.render(msgs)
    assert "| 2 | 2026-10-01T12:00:00Z | n/a | n/a |" in section(out, "Stage timeline")


def test_first_pass_yield():
    msgs = [
        msg(B, "[VERDICT] stage=1 sha=aaaaaaa result=ACCEPT", ts="2026-10-01T10:00:00Z"),
        msg(B, "[VERDICT] stage=2 sha=bbbbbbb result=REJECT", ts="2026-10-01T11:00:00Z"),
        msg(B, "[VERDICT] stage=2 sha=ccccccc result=ACCEPT", ts="2026-10-01T12:00:00Z"),
    ]
    v = section(m.render(msgs), "Verdicts")
    assert "| 1 | 1 | 0 | ACCEPT |" in v and "| 2 | 1 | 1 | REJECT |" in v
    assert "First-pass yield: 1/2 stages (50.0%)" in v


# --- Review Focus #5: degraded inputs never raise ---------------------------------------

def test_render_room_without_headers_or_usage(tmp_path):
    msgs = [msg(A, f"@[[{B}]] plain text, no header", name="A"),
            msg(B, "", kind="thought", name="B"),
            msg(U, "hello", stype="User", name="op")]
    out = m.render(msgs, git=None, usage=m.usage(tmp_path / "absent.json"), denials=None, reports=[])
    assert "| B | 0 | 0 | 0 | 1 | n/a | n/a | n/a | n/a | n/a |" in section(out, "Seats")
    assert "n/a" in section(out, "Stage timeline")
    assert "n/a" in section(out, "Verdicts")
    assert "n/a" in section(out, "Stage reports")
    assert "Human text messages: 1" in section(out, "Human input")


def test_render_empty_room():
    out = m.render([])
    for title in ("Seats", "Human input", "Stage timeline", "Verdicts", "Stage reports"):
        assert f"## {title}" in out
    assert "n/a" in section(out, "Seats")


def test_render_tolerates_junk_messages():
    msgs = [{"messageType": "text"}, {"senderType": None, "content": 5}, msg(A, None, ts="not a time")]
    assert "## Seats" in m.render(msgs)


def test_usage_missing_and_malformed(tmp_path):
    assert m.usage(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert m.usage(bad) == {}
    assert m.usage(None) == {}


def test_usage_record_list_and_wrapped_shapes(tmp_path):
    rows = [{"agentName": "Builder", "inputTokens": 10, "outputTokens": 5, "cacheCreationTokens": 1,
             "cacheReadTokens": 100, "reasoningTokens": 0, "totalCost": 1.5, "sessionCount": 2},
            {"agentName": "Builder", "inputTokens": 1, "outputTokens": 1, "totalCost": 0.5},
            {"name": "Oracle", "totalTokens": 42, "cost": 0.25},
            {"inputTokens": 7, "totalCost": 9.0}]
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"agents": rows, "totals": {"totalCost": 11.75}}), encoding="utf-8")
    u = m.usage(p)
    assert u == {"Builder": {"cost": 2.0, "tokens": 118}, "Oracle": {"cost": 0.25, "tokens": 42}}
    p.write_text(json.dumps(rows[:1]), encoding="utf-8")
    assert m.usage(p) == {"Builder": {"cost": 1.5, "tokens": 116}}
    p.write_text(json.dumps({"Oracle": {"totalCost": 3, "totalTokens": 9}}), encoding="utf-8")
    assert m.usage(p) == {"Oracle": {"cost": 3.0, "tokens": 9}}


def test_guard_denials(tmp_path):
    log = tmp_path / "guard.jsonl"
    log.write_text("\n".join([
        json.dumps({"ts": "t", "seat": "builder", "tool": "Read", "target": "x", "rule": "deny_read"}),
        json.dumps({"ts": "t", "seat": "builder", "tool": "Bash", "target": "y", "rule": "deny_command"}),
        json.dumps({"ts": "t", "seat": "oracle", "tool": "Read", "target": "z", "rule": "deny_read"}),
        json.dumps({"ts": "t", "seat": "stylist", "rule": "parse-error"}),
        "garbage line",
        "",
    ]), encoding="utf-8")
    assert m.guard_denials(log) == {"builder": 2, "oracle": 1}
    assert m.guard_denials(tmp_path / "missing.jsonl") == {}
    assert m.guard_denials(None) == {}


# --- git + stage reports ----------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo, *args, author=None):
    cmd = ["git", "-C", str(repo), "-c", "commit.gpgsign=false"]
    if author:
        cmd += ["-c", f"user.name={author}", "-c", f"user.email={author.lower()}@countersign.local"]
    subprocess.run(cmd + list(args), check=True, capture_output=True)


@needs_git
def test_git_stats(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "Factory kit", author="Operator")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "[WI-1] first item", author="Builder")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "[WI-1] [WI-2] follow-up", author="Builder")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "WI-9 without brackets", author="Stylist")
    g = m.git_stats(tmp_path)
    assert g["Builder"]["commits"] == 2 and g["Builder"]["work_items"] == {"WI-1", "WI-2"}
    assert g["Stylist"]["commits"] == 1 and g["Stylist"]["work_items"] == set()
    assert g["Operator"]["commits"] == 1
    assert g["Builder"]["emails"] == {"builder@countersign.local"}


@needs_git
def test_git_stats_empty_repo_and_non_repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    assert m.git_stats(repo) == {}
    assert m.git_stats(tmp_path / "does-not-exist") is None


REPORT = {
    "stage": 1, "outcome": "accepted", "accepted_sha": "def5678",
    "clauses": {"total": 40, "both": 34, "a_only": 4, "b_only": 2, "rulings": 3},
    "work_items": 7, "candidates": 2,
    "rejections": [{"sha": "abc1234", "findings": 3, "changed_work": True}],
    "mutation": {"tool": "mutmut", "killed": 41, "total": 50},
    "race_proofs": [{"guard": "g1", "test": "t1", "red_without": True, "green_with": True},
                    {"guard": "g2", "test": "t2", "red_without": False, "green_with": True}],
    "holdout": {"ran": True, "passed": 18, "failed": 1, "escapes": ["E-1"]},
    "blockers": [],
}


def _write_report(repo, n, data):
    d = repo / "evidence" / f"stage-{n}"
    d.mkdir(parents=True)
    (d / "report.json").write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_stage_reports_and_render(tmp_path):
    _write_report(tmp_path, 10, dict(REPORT, stage=10, holdout={"ran": False, "passed": 0, "failed": 0,
                                                                 "escapes": []}))
    _write_report(tmp_path, 1, REPORT)
    _write_report(tmp_path, 2, "{broken")
    _write_report(tmp_path, 3, {"stage": 3, "outcome": "blocked", "blockers": ["no candidate"]})
    reports = m.stage_reports(tmp_path)
    assert [r["stage"] for r in reports] == [1, 2, 3, 10]
    assert "error" in reports[1]
    out = section(m.render([], reports=reports), "Stage reports")
    assert "| 1 | accepted | 40 | 34 | 4 | 2 | 3 | 7 | 2 | 1 (1 changed work) | 41/50 (82.0%) | 1/2 | 2/2 | 18 | 1 | 1 |" in out
    assert "| 2 | unreadable |" in out
    assert "| 3 | blocked | n/a |" in out
    assert "| 10 | accepted |" in out and "not run" in out
    assert "E-1" in out and "no candidate" in out


def test_stage_reports_missing_dir(tmp_path):
    assert m.stage_reports(tmp_path / "nowhere") == []
    assert m.stage_reports(None) == []


def test_render_joins_inputs_to_seats_by_name():
    msgs = [msg("b-id", "hi", name="Builder"), msg("o-id", "hi", name="Oracle")]
    git = {"Builder": {"commits": 3, "work_items": {"WI-1", "WI-2"}, "emails": {"builder@countersign.local"}},
           "Operator": {"commits": 1, "work_items": set(), "emails": {"op@example.com"}}}
    use = {"builder": {"cost": 1.25, "tokens": 1000}}
    out = m.render(msgs, git=git, usage=use, denials={"builder": 2, "stylist": 1}, reports=[])
    seats = section(out, "Seats")
    assert "| Builder | 1 | 0 | 0 | 0 | 3 | 2 | 1.25 | 1000 | 2 |" in seats
    assert "| Oracle | 1 | 0 | 0 | 0 | 0 | 0 | n/a | n/a | 0 |" in seats
    assert "Operator" in seats and "stylist" in seats


def test_render_matches_git_author_by_email_local_part():
    msgs = [msg("b-id", "hi", name="Builder")]
    git = {"builder-bot": {"commits": 4, "work_items": set(), "emails": {"builder@countersign.local"}}}
    assert "| Builder | 1 | 0 | 0 | 0 | 4 | 0 |" in m.render(msgs, git=git)


# --- CLI --------------------------------------------------------------------------------

def test_cli_writes_report(tmp_path):
    out = tmp_path / "metrics.md"
    r = subprocess.run([sys.executable, str(SCRIPT), "--room", str(FX), "--repo", str(tmp_path / "no-repo"),
                        "--usage", str(tmp_path / "missing.json"), "--guard-log", str(tmp_path / "g.jsonl"),
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert "## Seats" in text and "| A |" in text and "| 1 | 1 | 1 | REJECT |" in text


def test_cli_stdout(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--room", str(FX), "--repo", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "## Stage timeline" in r.stdout and "60.0" in r.stdout


def test_cli_bad_room_exits_nonzero(tmp_path):
    bad = tmp_path / "room.json"
    bad.write_text("[1, 2", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--room", str(bad), "--repo", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "room" in r.stderr.lower()
