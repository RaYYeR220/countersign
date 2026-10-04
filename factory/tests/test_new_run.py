"""Run layout: result repository, one worktree per seat, sparse checkouts, guards, dispatch, archive."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from factory.setup import new_run

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

KIT = Path(__file__).resolve().parents[1]
SEATS = ("builder", "stylist", "oracle", "auditor")
PYTHON = f'"{sys.executable}"'


def git(cwd, *args) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert out.returncode == 0, f"git {' '.join(args)} in {cwd}: {out.stderr}"
    return out.stdout.strip()


def fwd(p) -> str:
    return str(p).replace("\\", "/")


def make_kickoff(base: Path) -> Path:
    src = base / "kickoff src"
    (src / "track" / "spec").mkdir(parents=True)
    (src / "track" / "test").mkdir(parents=True)
    for n in (1, 2):
        (src / "track" / "spec" / f"stage-{n}.md").write_text(f"# Stage {n}\n", encoding="utf-8")
    (src / "track" / "test" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    return src


def run_args(ws: Path, kickoff: Path, *extra) -> list:
    return ["--workspace", str(ws), "--kickoff", str(kickoff),
            "--holdout", str(ws / "kickoff" / "track" / "test"), "--track", "demo",
            "--spec", str(ws / "kickoff" / "track" / "spec" / "stage-1.md"),
            "--spec", str(ws / "kickoff" / "track" / "spec" / "stage-2.md"), *extra]


@pytest.fixture(scope="module")
def layout(tmp_path_factory):
    base = tmp_path_factory.mktemp("run")
    ws = base / "work space"  # a space in the path must survive every step
    assert new_run.main(run_args(ws, make_kickoff(base), "--python", PYTHON)) == 0
    return ws


def settings(checkout: Path) -> dict:
    return json.loads((checkout / ".claude" / "settings.local.json").read_text(encoding="utf-8"))


def hook_command(checkout: Path) -> str:
    return settings(checkout)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


# --- result repository ----------------------------------------------------------------

def test_result_has_operator_initial_commit(layout):
    result = layout / "result"
    root = git(result, "rev-list", "--max-parents=0", "main")
    name = new_run.git_config("user.name") or "operator"
    email = new_run.git_config("user.email") or "operator@localhost"
    assert git(result, "log", "-1", "--format=%an <%ae>|%cn|%s", root) == f"{name} <{email}>|{name}|Factory kit"


def test_result_ships_the_kit_without_kit_only_files(layout):
    files = set(git(layout / "result", "ls-files").splitlines())
    for f in ("README.md", "FACTORY.md", ".gitattributes", ".gitignore", "mandates/foreman.md",
              "mandates/oracle.md", "factory/roles.json", "factory/hooks/guard.py",
              "factory/DISPATCH.template.md", "factory/setup/new_run.py",
              "factory/setup/launcher/main.go", "factory/setup/opencode/opencode.json.template",
              "factory/mandates-src/protocol.md", "factory/tests/test_guard.py",
              "factory/tests/fixtures/room.json"):
        assert f in files, f
    assert "factory/tests/test_vocabulary.py" not in files
    assert not [f for f in files if "__pycache__" in f or f.endswith(".pyc") or ".pytest_cache" in f]
    assert (layout / "result" / "README.md").read_text(encoding="utf-8") == "Written after the run.\n"
    ignored = (layout / "result" / ".gitignore").read_text(encoding="utf-8").split()
    for entry in ("__pycache__/", "*.pyc", ".venv/", "node_modules/", ".claude/", "opencode.json",
                  "AGENTS.md", "/tmp/", ".pytest_cache/"):
        assert entry in ignored


def test_result_config_and_foreman_identity(layout):
    result = layout / "result"
    assert git(result, "config", "core.autocrlf") == "false"
    assert git(result, "config", "extensions.worktreeConfig") == "true"
    assert git(result, "config", "--worktree", "user.name") == "Foreman"
    assert git(result, "config", "--worktree", "user.email") == "foreman@countersign.local"
    assert git(result, "branch", "--show-current") == "main"
    exclude = (result / ".git" / "info" / "exclude").read_text(encoding="utf-8").split()
    assert ".claude/" in exclude


# --- seats -------------------------------------------------------------------------------

def test_four_seat_worktrees_with_own_identity(layout):
    listing = git(layout / "result", "worktree", "list", "--porcelain")
    branches = [line.split(" ", 1)[1] for line in listing.splitlines() if line.startswith("branch ")]
    assert sorted(branches) == sorted(["refs/heads/main"] + [f"refs/heads/seat/{s}" for s in SEATS])
    for seat in SEATS:
        wt = layout / "seats" / seat
        assert git(wt, "branch", "--show-current") == f"seat/{seat}"
        assert git(wt, "config", "user.name") == seat.capitalize()
        assert git(wt, "config", "user.email") == f"{seat}@countersign.local"


def test_sparse_patterns(layout):
    seats = layout / "seats"
    assert git(seats / "builder", "sparse-checkout", "list").splitlines() == ["/*", "!/stage-*/verify/"]
    assert git(seats / "stylist", "sparse-checkout", "list").splitlines() == ["/*", "!/stage-*/verify/"]
    assert git(seats / "oracle", "sparse-checkout", "list").splitlines() == [
        "/evidence/", "/factory/", "/mandates/", "/stage-*/verify/", "/.gitattributes", "/.gitignore"]
    for full in (seats / "auditor", layout / "result"):  # `list` fails on a checkout that is not sparse
        assert subprocess.run(["git", "-C", str(full), "sparse-checkout", "list"], capture_output=True).returncode != 0
    assert (seats / "oracle" / "mandates" / "oracle.md").is_file()
    assert (seats / "oracle" / "factory" / "hooks" / "guard.py").is_file()
    assert not (seats / "oracle" / "README.md").exists()
    assert (seats / "builder" / "README.md").is_file()


def test_sparse_checkout_holds_after_new_stages_are_merged(layout):
    result, seats = layout / "result", layout / "seats"

    def commit_on_main(stage: int):
        for rel in (f"stage-{stage}/verify/test_x.py", f"stage-{stage}/src/app.py"):
            p = result / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x = 1\n", encoding="utf-8")
        git(result, "add", "-A")
        git(result, "commit", "-q", "-m", f"stage {stage}")

    commit_on_main(1)
    for seat in SEATS:
        git(seats / seat, "merge", "-q", "main")
    # a seat with its own commits takes a real merge, not a fast-forward
    (seats / "builder" / "stage-1" / "src" / "own.py").write_text("y = 2\n", encoding="utf-8")
    git(seats / "builder", "add", "-A")
    git(seats / "builder", "commit", "-q", "-m", "own work")
    commit_on_main(2)
    for seat in SEATS:
        git(seats / seat, "merge", "-q", "--no-edit", "main")

    for stage in (1, 2):
        for seat in ("builder", "stylist"):
            assert (seats / seat / f"stage-{stage}" / "src" / "app.py").is_file()
            assert not (seats / seat / f"stage-{stage}" / "verify").exists()
        assert (seats / "oracle" / f"stage-{stage}" / "verify" / "test_x.py").is_file()
        assert not (seats / "oracle" / f"stage-{stage}" / "src").exists()
        assert (seats / "auditor" / f"stage-{stage}" / "verify" / "test_x.py").is_file()
        assert (seats / "auditor" / f"stage-{stage}" / "src" / "app.py").is_file()
    merge = git(seats / "builder", "log", "-1", "--format=%an|%P")
    assert merge.startswith("Builder|") and len(merge.split("|")[1].split()) == 2


def test_checkouts_stay_clean(layout):
    for checkout in [layout / "result"] + [layout / "seats" / s for s in SEATS]:
        assert git(checkout, "status", "--porcelain") == "", checkout


# --- guards ------------------------------------------------------------------------------

def test_claude_seats_get_the_guard_hook(layout):
    ws = fwd(layout)
    checkouts = {"foreman": layout / "result"}
    for seat in ("builder", "stylist", "oracle", "auditor"):
        if new_run.harness_of(KIT / "mandates" / f"{seat}.md") == "Claude Code":
            checkouts[seat] = layout / "seats" / seat
        else:
            assert not (layout / "seats" / seat / ".claude").exists()
    for seat, checkout in checkouts.items():
        s = settings(checkout)
        hook = s["hooks"]["PreToolUse"][0]
        assert hook["matcher"] == "*" and hook["hooks"][0]["type"] == "command"
        cmd = hook["hooks"][0]["command"]
        assert cmd.startswith(PYTHON + " ")
        assert f'"{ws}/guard/guard.py" --seat {seat} --config "{ws}/guard/roles.json"' in cmd
        assert "\\" not in cmd.replace(PYTHON, "")
        assert s["env"] == {"PYTHONUTF8": "1"} and s["includeCoAuthoredBy"] is False


def test_guard_rules_and_frozen_copy(layout):
    cfg = json.loads((layout / "guard" / "roles.json").read_text(encoding="utf-8"))
    kit = json.loads((KIT / "factory" / "roles.json").read_text(encoding="utf-8"))
    assert cfg["workspace"] == fwd(layout)
    assert cfg["log"] == fwd(layout / "logs" / "guard.jsonl")
    assert cfg["holdout"] == [fwd(layout / "kickoff" / "track" / "test")]
    assert cfg["common"] == kit["common"] and cfg["roles"] == kit["roles"]
    assert (layout / "guard" / "guard.py").read_bytes() == (KIT / "factory" / "hooks" / "guard.py").read_bytes()


def test_hook_command_denies_through_the_shell(layout):
    builder = layout / "seats" / "builder"
    event = {"tool_name": "Edit", "tool_input": {"file_path": "stage-1/verify/t.py"},
             "cwd": str(builder), "session_id": "s"}
    out = subprocess.run(hook_command(builder), shell=True, input=json.dumps(event),
                         capture_output=True, text=True, cwd=builder)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert '"seat": "builder"' in (layout / "logs" / "guard.jsonl").read_text(encoding="utf-8")


# --- oracle, kickoff, dispatch -------------------------------------------------------------

def test_opencode_seat_config(tmp_path):
    """A seat whose mandate names OpenCode gets a scoped OpenCode config instead of a hook."""
    result = tmp_path / "result"
    (result / "mandates").mkdir(parents=True)
    (result / "mandates" / "oracle.md").write_text("Harness: OpenCode\nModel: x/y\n", encoding="utf-8")
    oracle = tmp_path / "seats" / "oracle"
    oracle.mkdir(parents=True)
    new_run.write_oracle(KIT, result, oracle, [tmp_path / "kickoff" / "track" / "spec" / "stage-1.md"])
    oc = json.loads((oracle / "opencode.json").read_text(encoding="utf-8"))
    perm = oc["permission"]
    ext = perm["external_directory"]
    assert ext["*"] == "deny"
    allowed = [k for k, v in ext.items() if v == "allow"]
    assert allowed and all(k.replace("\\", "/").endswith("/spec/*") for k in allowed)
    assert perm["webfetch"] == "deny" and perm["edit"] == "allow"
    for rule in ("git show*", "git cat-file*", "git checkout*", "git worktree*", "git log -p*", "git diff*"):
        assert perm["bash"][rule] == "deny"
    assert perm["bash"]["*"] == "allow"
    assert (oracle / "AGENTS.md").read_bytes() == (result / "mandates" / "oracle.md").read_bytes()


def test_workspace_folders_and_kickoff(layout):
    for name in ("kickoff", "result", "seats", "tmp", "logs", "guard"):
        assert (layout / name).is_dir(), name
    assert (layout / "kickoff" / "track" / "spec" / "stage-1.md").read_text(encoding="utf-8") == "# Stage 1\n"


def test_dispatch_rendered(layout):
    text = (layout / "DISPATCH.md").read_text(encoding="utf-8")
    ws = fwd(layout)
    for placeholder in ("{N}", "{WS}", "{SPEC_LIST}", "{TRACK}"):
        assert placeholder not in text
    assert text.startswith("@Foreman ")
    assert "in 2 stages" in text
    assert f"Result repository (your checkout, branch main): {ws}/result" in text
    assert f"1. {ws}/kickoff/track/spec/stage-1.md\n2. {ws}/kickoff/track/spec/stage-2.md\n" in text
    assert "--track demo " in text


def test_shipped_tree_has_no_track_vocabulary(layout, kickoff_dir):
    vocabulary = pytest.importorskip("tests.test_vocabulary")
    vocab = vocabulary.load_vocabulary(kickoff_dir)
    bad = []
    for rel in git(layout / "result", "ls-files").splitlines():
        p = layout / "result" / rel
        if p.suffix in (".md", ".py", ".json", ".template", ".go", ".mod", "") and p.is_file():
            bad += [(rel, *hit) for hit in vocabulary.scan_text(p.read_text(encoding="utf-8"), vocab)]
    assert bad == []


# --- second run ------------------------------------------------------------------------------

def test_existing_run_is_refused_without_archive(layout, capsys):
    before = git(layout / "result", "rev-parse", "HEAD")
    assert new_run.main(run_args(layout, layout / "kickoff")) == 2
    assert "--archive" in capsys.readouterr().err
    assert git(layout / "result", "rev-parse", "HEAD") == before


def test_archive_moves_previous_run(tmp_path):
    ws = tmp_path / "ws two"
    kickoff = make_kickoff(tmp_path)
    assert new_run.main(run_args(ws, kickoff)) == 0
    assert hook_command(ws / "seats" / "builder").startswith("py -3.14 ")
    first = git(ws / "result", "rev-parse", "HEAD")
    for keep in (".venv", "kickoff-tools"):
        (ws / keep).mkdir()
        (ws / keep / "marker.txt").write_text(keep, encoding="utf-8")
    (ws / "kickoff" / "local-note.txt").write_text("kept", encoding="utf-8")
    (ws / "logs" / "guard.jsonl").write_text("{}\n", encoding="utf-8")

    assert new_run.main(run_args(ws, kickoff, "--archive")) == 0

    runs = list((ws / "runs").iterdir())
    assert len(runs) == 1
    old = runs[0]
    for name in ("result", "seats", "logs", "DISPATCH.md"):
        assert (old / name).exists(), name
    assert (old / "logs" / "guard.jsonl").read_text(encoding="utf-8") == "{}\n"
    assert git(old / "result", "rev-parse", "HEAD") == first
    # the archived seats still belong to the archived repository, not to the new one
    old_common = Path(git(old / "seats" / "builder", "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert old_common.resolve() == (old / "result" / ".git").resolve()
    # untouched by the archive
    for keep in (".venv", "kickoff-tools"):
        assert (ws / keep / "marker.txt").read_text(encoding="utf-8") == keep
    assert (ws / "kickoff" / "local-note.txt").read_text(encoding="utf-8") == "kept"
    # the new run is complete and sees only its own worktrees
    listing = git(ws / "result", "worktree", "list", "--porcelain")
    paths = [Path(line.split(" ", 1)[1]).resolve() for line in listing.splitlines() if line.startswith("worktree ")]
    assert sorted(paths) == sorted([(ws / "result").resolve()] + [(ws / "seats" / s).resolve() for s in SEATS])
    assert not (ws / "logs" / "guard.jsonl").exists()
    assert (ws / "DISPATCH.md").is_file()


def test_missing_kit_files_are_reported(tmp_path, capsys):
    assert new_run.main(["--kit", str(tmp_path / "nokit"), "--workspace", str(tmp_path / "ws")]) == 2
    assert "kit" in capsys.readouterr().err
    assert not (tmp_path / "ws").exists()
