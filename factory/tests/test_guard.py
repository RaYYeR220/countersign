import json, subprocess, sys
from pathlib import Path
import pytest
from factory.hooks import guard

@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "result" / ".git").mkdir(parents=True)
    (w / "seats" / "builder").mkdir(parents=True)
    (w / "seats" / "builder" / ".git").write_text("gitdir: x")
    (w / "kickoff" / "track" / "test").mkdir(parents=True)
    cfg = json.loads((Path(guard.__file__).parents[1] / "roles.json").read_text())
    cfg["workspace"] = str(w).replace("\\", "/")
    cfg["log"] = str(w / "guard.jsonl")
    cfg["holdout"] = [str(w / "kickoff" / "track" / "test")]
    return w, cfg

def ev(tool, cwd, **inp):
    return {"tool_name": tool, "tool_input": inp, "cwd": str(cwd), "session_id": "s"}

def test_builder_cannot_edit_verify(ws):
    w, cfg = ws
    ok, why = guard.decide("builder", ev("Edit", w/"seats"/"builder", file_path="stage-1/verify/test_x.py"), cfg)
    assert not ok and "verify" in why

def test_builder_cannot_read_verify_in_main_checkout_windows_path(ws):
    w, cfg = ws
    p = str(w / "result" / "stage-2" / "verify" / "model.py").replace("/", "\\")
    ok, _ = guard.decide("builder", ev("Read", w/"seats"/"builder", file_path="\\\\?\\" + p), cfg)
    assert not ok

def test_builder_can_edit_source(ws):
    w, cfg = ws
    ok, _ = guard.decide("builder", ev("Write", w/"seats"/"builder", file_path="stage-1/src/app.py"), cfg)
    assert ok

def test_builder_command_touching_verify_denied(ws):
    w, cfg = ws
    ok, _ = guard.decide("builder", ev("Bash", w/"seats"/"builder", command="git show main:stage-1/verify/x.py"), cfg)
    assert not ok

def test_builder_cannot_read_holdout(ws):
    w, cfg = ws
    ok, _ = guard.decide("builder", ev("Read", w, file_path=str(w/"kickoff"/"track"/"test"/"test_a.py")), cfg)
    assert not ok
    ok, _ = guard.decide("builder", ev("Bash", w, command=f"cat {w/'kickoff'/'track'/'test'/'test_a.py'}"), cfg)
    assert not ok

def test_auditor_may_not_edit_product(ws):
    w, cfg = ws
    ok, _ = guard.decide("auditor", ev("Edit", w/"result", file_path="stage-1/src/app.py"), cfg)
    assert not ok
    ok, _ = guard.decide("auditor", ev("Write", w/"result", file_path="stage-1/verify/audit/test_burst.py"), cfg)
    assert ok

def test_oracle_blind_to_source_but_sees_verify(ws):
    w, cfg = ws
    assert not guard.decide("oracle", ev("Read", w/"result", file_path="stage-1/src/app.py"), cfg)[0]
    assert guard.decide("oracle", ev("Read", w/"result", file_path="stage-1/verify/oracle/model.py"), cfg)[0]

def test_foreman_writes_no_product_code(ws):
    w, cfg = ws
    assert not guard.decide("foreman", ev("Edit", w/"result", file_path="stage-1/src/app.py"), cfg)[0]
    assert guard.decide("foreman", ev("Write", w/"result", file_path="evidence/stage-1/ledger-A.md"), cfg)[0]

@pytest.mark.parametrize("cmd", ["git push origin main", "git rebase main", "git commit --amend -m x",
                                 "band rm @x/y", "docker system prune -f", "cat ~/.ssh/id_rsa"])
def test_common_commands_denied(ws, cmd):
    w, cfg = ws
    assert not guard.decide("auditor", ev("Bash", w/"result", command=cmd), cfg)[0]

def test_ask_user_denied(ws):
    w, cfg = ws
    assert not guard.decide("foreman", ev("mcp__jam__AskUserQuestion", w/"result"), cfg)[0]

def test_write_outside_workspace_denied(ws, tmp_path):
    w, cfg = ws
    assert not guard.decide("builder", ev("Write", w/"seats"/"builder", file_path=str(tmp_path/"elsewhere.txt")), cfg)[0]

def test_factory_frozen(ws):
    w, cfg = ws
    assert not guard.decide("builder", ev("Edit", w/"seats"/"builder", file_path="factory/hooks/guard.py"), cfg)[0]

def test_cli_deny_output_and_log(ws, tmp_path):
    w, cfg = ws
    cfgp = tmp_path / "roles.json"; cfgp.write_text(json.dumps(cfg))
    e = ev("Edit", w/"seats"/"builder", file_path="stage-1/verify/t.py")
    out = subprocess.run([sys.executable, guard.__file__, "--seat", "builder", "--config", str(cfgp)],
                         input=json.dumps(e), capture_output=True, text=True)
    assert out.returncode == 0
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "builder" in Path(cfg["log"]).read_text()

def test_cli_malformed_stdin_allows(ws, tmp_path):
    w, cfg = ws
    cfgp = tmp_path / "roles.json"; cfgp.write_text(json.dumps(cfg))
    out = subprocess.run([sys.executable, guard.__file__, "--seat", "builder", "--config", str(cfgp)],
                         input="not json", capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == ""


# ===========================================================================
# Added beyond the plan's tests: one block per Review Focus item 1-3, plus the
# reason wording and the guard's own files.
# ===========================================================================
import os

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows path spellings")


def _spellings(p: Path) -> dict:
    """Every way a Windows tool call can spell the same absolute path."""
    s = str(p)
    fwd = s.replace("\\", "/")
    return {
        "backslash": s,                                            # C:\x\y
        "forward": fwd,                                            # C:/x/y
        "lower-drive-forward": fwd[0].lower() + fwd[1:],           # c:/x/y
        "all-lower": fwd.lower(),
        "all-upper": s.upper(),
        "long-prefix": "\\\\?\\" + s,                              # \\?\C:\x\y
        "long-prefix-forward": "//?/" + fwd,                       # //?/C:/x/y
        "doubled-separators": s.replace("\\", "\\\\"),             # C:\\x\\y
        "msys": "/" + fwd[0].lower() + fwd[2:],                    # /c/x/y (Git Bash)
        "dot-segments": str(p.parent / ".." / p.parent.name / "." / p.name),
        "trailing-dot": str(p.parent.parent / (p.parent.name + ".") / p.name),
        "trailing-space": str(p.parent.parent / (p.parent.name + " ") / p.name),
        "stream-suffix": str(p.parent.parent / (p.parent.name + "::$INDEX_ALLOCATION") / p.name),
    }


SPELLINGS = sorted(_spellings(Path("C:/a/b/c")))


def _mk(path: Path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _oracle_checkout(w: Path) -> Path:
    o = w / "seats" / "oracle"
    o.mkdir(parents=True)
    (o / ".git").write_text("gitdir: x")
    _mk(o / "stage-1" / "verify" / "oracle" / "model.py")
    return o


# --- Review Focus 1: every spelling is normalised before matching ----------

@windows_only
@pytest.mark.parametrize("exists", [False, True], ids=["missing", "present"])
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_rf1_builder_read_of_verify_denied_in_every_spelling(ws, spelling, exists):
    w, cfg = ws
    target = w / "result" / "stage-1" / "verify" / "t.py"
    if exists:
        _mk(target)
    path = _spellings(target)[spelling]
    assert not guard.decide("builder", ev("Read", w/"seats"/"builder", file_path=path), cfg)[0], path


@windows_only
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_rf1_builder_read_of_holdout_denied_in_every_spelling(ws, spelling):
    w, cfg = ws
    target = _mk(w / "kickoff" / "track" / "test" / "test_a.py")
    path = _spellings(target)[spelling]
    assert not guard.decide("stylist", ev("Read", w/"seats"/"builder", file_path=path), cfg)[0], path


@windows_only
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_rf1_write_outside_workspace_denied_in_every_spelling(ws, tmp_path, spelling):
    w, cfg = ws
    path = _spellings(tmp_path / "elsewhere" / "x.txt")[spelling]
    assert not guard.decide("builder", ev("Write", w/"seats"/"builder", file_path=path), cfg)[0], path


@windows_only
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_rf1_normalising_does_not_deny_legitimate_writes(ws, spelling):
    w, cfg = ws
    path = _spellings(w / "seats" / "builder" / "stage-1" / "src" / "app.py")[spelling]
    ok, why = guard.decide("builder", ev("Write", w/"seats"/"builder", file_path=path), cfg)
    assert ok, (path, why)


@pytest.mark.parametrize("rel", [
    "../../result/stage-1/verify/t.py",
    "..\\..\\result\\stage-1\\verify\\t.py",
    "..\\..\\RESULT\\Stage-1\\Verify\\T.py",
    "./../../result/stage-1/./verify/t.py",
    "stage-1/../../../result/stage-1/verify/t.py",
])
def test_rf1_relative_read_into_other_checkout_denied(ws, rel):
    w, cfg = ws
    assert not guard.decide("builder", ev("Read", w/"seats"/"builder", file_path=rel), cfg)[0]


@pytest.mark.parametrize("rel", ["stage-1\\verify\\t.py", "STAGE-1/VERIFY/T.PY", "stage-1/src/../verify/t.py",
                                 "./stage-1//verify/t.py"])
def test_rf1_relative_write_into_verify_denied(ws, rel):
    w, cfg = ws
    assert not guard.decide("builder", ev("Write", w/"seats"/"builder", file_path=rel), cfg)[0]


@windows_only
@pytest.mark.parametrize("rel", ["stage-1/verify./t.py", "stage-1/verify /t.py",
                                 "stage-1/verify::$INDEX_ALLOCATION/t.py", "stage-1\\VERIFY.\\t.py"])
def test_rf1_windows_name_tricks_on_write_denied(ws, rel):
    w, cfg = ws
    assert not guard.decide("builder", ev("Write", w/"seats"/"builder", file_path=rel), cfg)[0]


@pytest.mark.parametrize("rel", ["../../../elsewhere.txt", "..\\..\\..\\elsewhere.txt", "~/elsewhere.txt"])
def test_rf1_relative_and_home_writes_outside_workspace_denied(ws, rel):
    w, cfg = ws
    assert not guard.decide("builder", ev("Write", w/"seats"/"builder", file_path=rel), cfg)[0]


def test_rf1_holdout_relative_read_denied_and_prefix_boundary_respected(ws):
    w, cfg = ws
    b = w / "seats" / "builder"
    assert not guard.decide("builder", ev("Read", b, file_path="../../kickoff/track/test/test_a.py"), cfg)[0]
    assert not guard.decide("builder", ev("Read", b, file_path="..\\..\\kickoff\\track\\test"), cfg)[0]
    assert guard.decide("builder", ev("Read", b, file_path="../../kickoff/track/testing/notes.md"), cfg)[0]


def test_rf1_checkout_relative_rules_use_the_nearest_checkout(ws):
    w, cfg = ws
    clone = w / "tmp" / "clone"
    (clone / ".git").mkdir(parents=True)
    assert not guard.decide("builder", ev("Read", w, file_path=str(clone/"stage-3"/"verify"/"t.py")), cfg)[0]
    # a copy of a stage folder outside any checkout is still a stage folder
    loose = w / "tmp" / "copy" / "stage-3" / "verify" / "t.py"
    assert not guard.decide("builder", ev("Read", w, file_path=str(loose)), cfg)[0]


@pytest.mark.parametrize("tool,inp", [
    ("Grep", {"pattern": "x", "path": "{verify}"}),
    ("Grep", {"pattern": "x", "path": "{result}"}),
    ("Grep", {"pattern": "x", "path": "{result}/stage-1"}),
    ("Grep", {"pattern": "x", "path": "{ws}"}),
    ("Grep", {"pattern": "x", "path": "{ws}/kickoff"}),
    ("Grep", {"pattern": "x", "path": "{ws}/tmp"}),
    ("Glob", {"pattern": "**/*.py", "path": "{result}"}),
    ("Glob", {"pattern": "../../result/stage-1/verify/*.py"}),
    ("Glob", {"pattern": "{verify}/*.py"}),
    ("LS", {"path": "{verify}"}),
])
def test_rf1_builder_search_that_would_reach_denied_files_is_denied(ws, tool, inp):
    w, cfg = ws
    _mk(w / "result" / "stage-1" / "verify" / "t.py")
    _mk(w / "result" / "stage-1" / "src" / "app.py")
    clone = w / "tmp" / "clone"
    (clone / ".git").mkdir(parents=True)
    _mk(clone / "stage-1" / "verify" / "t.py")
    _mk(w / "kickoff" / "track" / "test" / "test_a.py")
    fill = {"ws": str(w), "result": str(w / "result"), "verify": str(w / "result" / "stage-1" / "verify")}
    inp = {k: v.format(**fill) for k, v in inp.items()}
    assert not guard.decide("builder", ev(tool, w/"seats"/"builder", **inp), cfg)[0], inp


@pytest.mark.parametrize("tool,inp", [
    ("Grep", {"pattern": "x"}),
    ("Glob", {"pattern": "**/*.py"}),
    ("Grep", {"pattern": "x", "path": "stage-1"}),
    ("Grep", {"pattern": "x", "path": "{result}/stage-1/src"}),
    ("Grep", {"pattern": "x", "path": "{ws}/seats"}),
])
def test_rf1_builder_ordinary_searches_allowed(ws, tool, inp):
    w, cfg = ws
    _mk(w / "result" / "stage-1" / "verify" / "t.py")
    _mk(w / "result" / "stage-1" / "src" / "app.py")
    _mk(w / "seats" / "builder" / "stage-1" / "src" / "app.py")
    fill = {"ws": str(w), "result": str(w / "result")}
    inp = {k: v.format(**fill) for k, v in inp.items()}
    ok, why = guard.decide("builder", ev(tool, w/"seats"/"builder", **inp), cfg)
    assert ok, (inp, why)


def test_rf1_oracle_searches_its_sparse_checkout_but_not_the_full_one(ws):
    w, cfg = ws
    o = _oracle_checkout(w)
    _mk(w / "result" / "stage-1" / "src" / "app.py")
    assert guard.decide("oracle", ev("Grep", o, pattern="x"), cfg)[0]
    assert guard.decide("oracle", ev("Read", o, file_path="stage-1/verify/oracle/model.py"), cfg)[0]
    assert not guard.decide("oracle", ev("Grep", o, pattern="x", path=str(w / "result")), cfg)[0]
    assert not guard.decide("oracle", ev("Read", o, file_path="../../result/stage-1/src/app.py"), cfg)[0]


def test_rf1_foreman_and_auditor_search_anywhere_in_workspace(ws):
    w, cfg = ws
    _mk(w / "result" / "stage-1" / "verify" / "t.py")
    _mk(w / "kickoff" / "track" / "test" / "test_a.py")
    for seat in ("foreman", "auditor"):
        assert guard.decide(seat, ev("Grep", w / "result", pattern="x", path=str(w)), cfg)[0]


# --- Review Focus 2: commands that reach a denied area by string -----------

IMPLEMENTER_DENIED_COMMANDS = [
    "cat ../result/stage-1/verify/t.py",
    "cat ..\\result\\stage-1\\verify\\t.py",
    "Get-Content C:\\countersign\\result\\stage-1\\verify\\t.py",
    "git show main:stage-1/verify/x",
    "git show main:stage-1\\verify\\x",
    "ls stage-2/verify/",
    "cp -r ../../result/stage-1/verify/ ../tmp/v",
    "python -m harness check .",
    "PYTHONUTF8=1 py harness_win.py run --stage 1",
]


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize("seat", ["builder", "stylist"])
@pytest.mark.parametrize("cmd", IMPLEMENTER_DENIED_COMMANDS)
def test_rf2_implementer_commands_reaching_verification_denied(ws, cmd, seat, tool):
    w, cfg = ws
    assert not guard.decide(seat, ev(tool, w/"seats"/"builder", command=cmd), cfg)[0]


@windows_only
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_rf2_implementer_command_naming_holdout_denied_in_every_spelling(ws, spelling):
    w, cfg = ws
    path = _spellings(w / "kickoff" / "track" / "test" / "test_a.py")[spelling]
    for cmd in (f"cat {path}", f'type "{path}"', f"py -m pytest '{path}' -q"):
        assert not guard.decide("builder", ev("Bash", w/"seats"/"builder", command=cmd), cfg)[0], cmd


@pytest.mark.parametrize("cmd", ["cat ../../kickoff/track/test/test_a.py",
                                 "Get-ChildItem ..\\..\\kickoff\\track\\test -Recurse",
                                 "grep -r assert ../../KICKOFF/Track/Test/"])
def test_rf2_implementer_command_naming_holdout_relatively_denied(ws, cmd):
    w, cfg = ws
    assert not guard.decide("builder", ev("Bash", w/"seats"/"builder", command=cmd), cfg)[0]


@pytest.mark.parametrize("cmd", [
    "git status", "git merge main", "git log --oneline -5", "py -3.14 -m pytest stage-1/tests -q",
    "docker build -t builder-app stage-1", "docker rm -f builder-app", "ls stage-1/src",
    "git commit -m '[WI-3] verify input lengths (C1.4)'", "npm test --prefix stage-1",
])
def test_rf2_ordinary_implementer_commands_allowed(ws, cmd):
    w, cfg = ws
    ok, why = guard.decide("builder", ev("Bash", w/"seats"/"builder", command=cmd), cfg)
    assert ok, why


def test_rf2_verification_owners_may_name_verify_and_run_the_holdout(ws):
    w, cfg = ws
    for cmd in ("cat stage-1/verify/t.py", "python -m harness check .",
                f"cat {w/'kickoff'/'track'/'test'/'test_a.py'}"):
        assert guard.decide("auditor", ev("Bash", w/"result", command=cmd), cfg)[0]


@pytest.mark.parametrize("seat", ["foreman", "builder", "stylist", "oracle", "auditor"])
@pytest.mark.parametrize("cmd", ["git push", "GIT PUSH origin main", "git filter-repo --force",
                                 "git reset --hard origin/main", "jam.exe ask x", "type C:\\Users\\me\\.git-credentials",
                                 "cat ~/.config/opencode/venice.key", "dir C:\\Users\\me\\.claude-countersign",
                                 "type C:\\\\Users\\\\me\\\\.config\\\\opencode\\\\opencode.json"])
def test_rf2_common_commands_denied_for_every_seat_and_shell(ws, seat, cmd):
    w, cfg = ws
    for tool in ("Bash", "PowerShell"):
        assert not guard.decide(seat, ev(tool, w/"result", command=cmd), cfg)[0]


# --- Review Focus 3: malformed input, unknown tools, broken setup ----------

def _run_cli(cfgp, stdin, *extra):
    args = [sys.executable, guard.__file__, *extra] if extra else \
           [sys.executable, guard.__file__, "--seat", "builder", "--config", str(cfgp)]
    data = stdin if isinstance(stdin, bytes) else stdin.encode("utf-8")
    return subprocess.run(args, input=data, capture_output=True, timeout=60)


MALFORMED = [
    "", "   ", "not json", "{", "[]", "null", '"text"', "42",
    '{"tool_name": 5, "tool_input": []}',
    '{"tool_name": "Write", "tool_input": "x"}',
    '{"tool_name": "Write", "tool_input": {"file_path": 123}}',
    '{"tool_name": "Read", "tool_input": {"file_path": null}, "cwd": 7}',
    '{"tool_name": "Bash", "tool_input": {"command": ["git", "push"]}}',
    '{"tool_name": "SomeFutureTool", "tool_input": {"file_path": "stage-1/verify/t.py"}}',
    '{"tool_input": {"file_path": "stage-1/verify/t.py"}}',
]


@pytest.mark.parametrize("stdin", MALFORMED)
def test_rf3_malformed_or_unknown_input_allows_quietly(ws, tmp_path, stdin):
    w, cfg = ws
    cfgp = tmp_path / "roles.json"; cfgp.write_text(json.dumps(cfg))
    out = _run_cli(cfgp, stdin)
    assert out.returncode == 0 and out.stdout.strip() == b"", out


def test_rf3_undecodable_bytes_allow(ws, tmp_path):
    w, cfg = ws
    cfgp = tmp_path / "roles.json"; cfgp.write_text(json.dumps(cfg))
    out = _run_cli(cfgp, b"\xff\xfe\x00{garbage")
    assert out.returncode == 0 and out.stdout.strip() == b""


def test_rf3_parse_error_is_logged(ws, tmp_path):
    w, cfg = ws
    cfgp = tmp_path / "roles.json"; cfgp.write_text(json.dumps(cfg))
    _run_cli(cfgp, "not json")
    lines = [json.loads(x) for x in Path(cfg["log"]).read_text().splitlines()]
    assert lines[-1]["rule"] == "parse-error" and lines[-1]["seat"] == "builder"


@pytest.mark.parametrize("setup", ["missing-config", "corrupt-config", "no-arguments", "unknown-flag"])
def test_rf3_broken_setup_never_breaks_the_turn(ws, tmp_path, setup):
    w, cfg = ws
    e = json.dumps(ev("Bash", w/"result", command="git push"))
    if setup == "missing-config":
        out = _run_cli(None, e, "--seat", "builder", "--config", str(tmp_path / "nope.json"))
    elif setup == "corrupt-config":
        bad = tmp_path / "bad.json"; bad.write_text("{ not json")
        out = _run_cli(None, e, "--seat", "builder", "--config", str(bad))
    elif setup == "no-arguments":
        out = subprocess.run([sys.executable, guard.__file__], input=e.encode(), capture_output=True, timeout=60)
    else:
        out = _run_cli(None, e, "--seat", "builder", "--config", "x", "--bogus")
    assert out.returncode == 0 and out.stdout.strip() == b""


def test_rf3_log_directory_created_and_unwritable_log_still_denies(ws, tmp_path):
    w, cfg = ws
    e = json.dumps(ev("Bash", w/"result", command="git push"))
    cfg["log"] = str(w / "logs" / "nested" / "guard.jsonl")
    cfgp = tmp_path / "roles.json"; cfgp.write_text(json.dumps(cfg))
    out = _run_cli(cfgp, e)
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert json.loads(Path(cfg["log"]).read_text().splitlines()[-1])["rule"].startswith("deny_command")
    cfg["log"] = str(w)  # a directory: appending fails
    cfgp.write_text(json.dumps(cfg))
    out = _run_cli(cfgp, e)
    assert out.returncode == 0
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_rf3_cli_deny_output_shape_and_log_fields(ws, tmp_path):
    w, cfg = ws
    cfgp = tmp_path / "roles.json"; cfgp.write_text(json.dumps(cfg))
    out = _run_cli(cfgp, json.dumps(ev("Read", w/"seats"/"builder", file_path="../../result/stage-1/verify/тест.py")))
    body = json.loads(out.stdout)
    assert set(body) == {"hookSpecificOutput"}
    hso = body["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse" and hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"]
    entry = json.loads(Path(cfg["log"]).read_text(encoding="utf-8").splitlines()[-1])
    assert {"ts", "seat", "tool", "target", "rule"} <= set(entry)
    assert entry["seat"] == "builder" and entry["tool"] == "Read" and "verify" in entry["target"]


def test_rf3_cli_allow_prints_nothing(ws, tmp_path):
    w, cfg = ws
    cfgp = tmp_path / "roles.json"; cfgp.write_text(json.dumps(cfg))
    out = _run_cli(cfgp, json.dumps(ev("Write", w/"seats"/"builder", file_path="stage-1/src/app.py")))
    assert out.returncode == 0 and out.stdout.strip() == b""


@pytest.mark.parametrize("event", [{}, {"tool_name": None}, {"tool_name": "Read", "tool_input": None},
                                   {"tool_name": "Edit", "tool_input": {"file_path": ""}},
                                   {"tool_name": "Grep", "tool_input": {"pattern": "x"}}])
def test_rf3_decide_tolerates_sparse_events(ws, event):
    w, cfg = ws
    ok, _ = guard.decide("builder", event, cfg)
    assert ok


def test_rf3_unknown_seat_still_gets_common_rules(ws):
    w, cfg = ws
    assert not guard.decide("intruder", ev("Bash", w/"result", command="git push"), cfg)[0]
    assert not guard.decide("intruder", ev("AskUserQuestion", w/"result"), cfg)[0]


# --- tools, reasons, and the guard's own files -------------------------------

@pytest.mark.parametrize("tool", ["AskUserQuestion", "mcp__jam__AskUserQuestion", "mcp__other__AskUserQuestion",
                                  "EnterPlanMode", "ExitPlanMode"])
def test_operator_facing_tools_denied_for_every_seat(ws, tool):
    w, cfg = ws
    for seat in ("foreman", "builder", "stylist", "oracle", "auditor"):
        ok, why = guard.decide(seat, ev(tool, w/"result"), cfg)
        assert not ok and "operator" in why


@pytest.mark.parametrize("tool,key", [("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"),
                                      ("NotebookEdit", "notebook_path")])
def test_every_write_tool_is_guarded(ws, tool, key):
    w, cfg = ws
    assert not guard.decide("builder", ev(tool, w/"seats"/"builder", **{key: "stage-1/verify/t.ipynb"}), cfg)[0]


def test_reasons_name_the_boundary_and_the_owner(ws):
    w, cfg = ws
    b = w / "seats" / "builder"
    _, why = guard.decide("builder", ev("Read", b, file_path="../../result/stage-1/verify/t.py"), cfg)
    assert why.startswith("Builder may not read verification suites (stage-*/verify/**)") and "Foreman" in why
    _, why = guard.decide("stylist", ev("Edit", b, file_path="stage-1/verify/t.py"), cfg)
    assert why.startswith("Stylist may not edit verification suites") and "Foreman" in why
    _, why = guard.decide("auditor", ev("Edit", w/"result", file_path="stage-1/src/app.py"), cfg)
    assert why.startswith("Auditor may write only") and "finding" in why
    _, why = guard.decide("foreman", ev("Edit", w/"result", file_path="stage-1/src/app.py"), cfg)
    assert why.startswith("Foreman may not edit") and "work item" in why
    _, why = guard.decide("oracle", ev("Read", w/"result", file_path="stage-1/src/app.py"), cfg)
    assert why.startswith("Oracle may not read implementation files")
    _, why = guard.decide("builder", ev("Write", b, file_path="factory/roles.json"), cfg)
    assert "frozen" in why
    _, why = guard.decide("builder", ev("Bash", b, command="git push"), cfg)
    assert why.startswith("Builder may not run this command") and "history" in why
    _, why = guard.decide("builder", ev("Bash", b, command="cat ../../kickoff/track/test/test_a.py"), cfg)
    assert "external acceptance tests" in why and "Auditor" in why


def test_unreadable_locations_are_unwritable(ws):
    w, cfg = ws
    b = w / "seats" / "builder"
    assert not guard.decide("builder", ev("Write", b, file_path="../../kickoff/track/test/test_a.py"), cfg)[0]
    assert not guard.decide("builder", ev("Write", b, file_path=".claude/settings.local.json"), cfg)[0]
    assert not guard.decide("foreman", ev("Edit", w/"result", file_path=".claude/settings.local.json"), cfg)[0]


def test_guard_files_are_protected(ws, tmp_path):
    w, cfg = ws
    for seat in ("foreman", "builder"):  # seats with no allow_write_only list of their own
        ok, why = guard.decide(seat, ev("Write", w/"result", file_path=cfg["log"]), cfg)
        assert not ok and "guard" in why
    cfgp = w / "guard" / "roles.json"
    ok, why = guard.decide("foreman", ev("Edit", w/"result", file_path=str(cfgp)), cfg, protect=[str(cfgp)])
    assert not ok and "guard" in why
    assert not guard.decide("auditor", ev("Bash", w/"result", command=f"echo x >> {cfg['log']}"), cfg)[0]
    roles = Path(guard.__file__).parent / "roles.json"
    assert not guard.decide("auditor", ev("Bash", w/"result", command=f"echo {{}} > {roles}"), cfg)[0]
    assert not guard.decide("auditor", ev("Bash", w/"result",
                                          command=f"sed -i d {str(roles).replace(chr(92), '/')}"), cfg)[0]
