"""Lay out one factory run under a workspace folder.

    py -3.14 factory/setup/new_run.py --workspace C:/countersign --kickoff <job package> \\
        --holdout <folder> --track <name> --spec <stage 1 spec> --spec <stage 2 spec> ... [--archive]

What it leaves in the workspace:

    kickoff/        the job package, copied in once (an existing non-empty copy is kept)
    result/         the result repository, branch main: the Foreman's checkout
    seats/<seat>/   one git worktree per seat on branch seat/<seat>, sparse where the role demands it
    guard/          frozen copy of the role guard and its rules, outside every checkout
    logs/  tmp/     guard log; scratch space for every seat
    runs/<stamp>/   earlier runs, moved there by --archive
    DISPATCH.md     the one message that starts the run

A workspace that already holds a run is refused unless --archive is given. kickoff/, .venv/ and
kickoff-tools/ are never moved or changed. The seat launcher is built separately, once
(factory/setup/launcher/README.md).

Stdlib only.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]

# seat -> sparse-checkout patterns (non-cone mode); None keeps the full tree
SEATS = {
    "builder": ["/*", "!/stage-*/verify/"],
    "stylist": ["/*", "!/stage-*/verify/"],
    "oracle": ["/evidence/", "/factory/", "/mandates/", "/stage-*/verify/", "/.gitattributes", "/.gitignore"],
    "auditor": None,
}
GUARDED_SEATS = ("builder", "stylist", "oracle", "auditor")  # seats besides the Foreman (result/)


def harness_of(mandate: Path) -> str:
    """The `Harness:` value from a mandate's header, or "" if it has none."""
    for line in mandate.read_text(encoding="utf-8").splitlines()[:5]:
        if line.startswith("Harness:"):
            return line.split(":", 1)[1].strip()
    return ""
MAIL_DOMAIN = "countersign.local"
MANDATES = ("foreman", "builder", "stylist", "oracle", "auditor")
REQUIRED = tuple(f"mandates/{m}.md" for m in MANDATES) + (
    "factory/roles.json", "factory/hooks/guard.py", "factory/DISPATCH.template.md",
    "factory/setup/opencode/opencode.json.template", ".gitattributes")
GITIGNORE = ("__pycache__/", "*.pyc", ".venv/", "node_modules/", ".claude/", "opencode.json", "AGENTS.md",
             "/tmp/", ".pytest_cache/", ".bg/")
STUB = "Written after the run.\n"
CACHES = ("__pycache__", "*.pyc", ".pytest_cache")
KIT_ONLY = ("test_vocabulary.py",)  # holds the banned word lists themselves; stays in the kit
RUN_ITEMS = ("result", "seats", "logs", "DISPATCH.md")  # what --archive moves


class SetupError(Exception):
    """A problem the operator has to fix; reported on one line with exit code 2."""


# --- helpers ---------------------------------------------------------------------------

def git_config(key: str) -> str:
    """The operator's global git setting, or "" when git or the key is missing."""
    try:
        out = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def absolute(p) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(p))))


def fwd(p) -> str:
    return str(p).replace("\\", "/")


def shell_arg(text: str) -> str:
    """A path as one word on a hook command line (bash and cmd both honour double quotes)."""
    return f'"{text}"' if any(c in text for c in " \t&|;<>()$`^%!'") else text


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, data) -> None:
    write(path, json.dumps(data, indent=2) + "\n")


def warn(message: str) -> None:
    print(f"new_run: warning: {message}", file=sys.stderr)


def git(cwd: Path, *args, stdin=None, check=True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(cwd), *args], input=stdin, capture_output=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise SetupError(f"`git {' '.join(args)}` failed in {cwd}: {(proc.stderr or proc.stdout).strip()}")
    return proc


def ignore(*extra):
    return shutil.ignore_patterns(*CACHES, *extra)


def non_empty(folder: Path) -> bool:
    return folder.is_dir() and any(folder.iterdir())


# --- steps -----------------------------------------------------------------------------

def check_kit(kit: Path) -> None:
    missing = [r for r in REQUIRED if not (kit / r).is_file()]
    if missing:
        raise SetupError(f"{kit} is not a factory kit (missing {', '.join(missing)}); pass --kit <kit root>")


def previous_run(ws: Path) -> list:
    """What a run left in the workspace; empty when there is nothing to archive."""
    if not ((ws / "result").exists() or non_empty(ws / "seats")):
        return []
    return [name for name in RUN_ITEMS if (ws / name).exists()]


def archive(ws: Path) -> Path:
    """Move the previous run into runs/<UTC stamp>/ and relink its worktrees there."""
    result = ws / "result"
    if (result / ".git").is_dir():
        git(result, "worktree", "prune", check=False)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest, n = ws / "runs" / stamp, 1
    while dest.exists():
        n += 1
        dest = ws / "runs" / f"{stamp}-{n}"
    dest.mkdir(parents=True)
    moved = []
    for name in RUN_ITEMS:
        src = ws / name
        if not src.exists():
            continue
        try:
            os.rename(src, dest / name)
        except OSError as e:
            done = f"; already moved: {', '.join(moved)}" if moved else ""
            raise SetupError(f"cannot move {src} into {dest} ({e.strerror or e}). Stop every seat and program "
                             f"working there, then rerun with --archive{done}") from None
        moved.append(name)
    # The seats' .git files still name <ws>/result, which the new run is about to recreate: point both
    # sides at the archive so neither run can reach into the other.
    seats, repo = dest / "seats", dest / "result"
    linked = [str(p) for p in sorted(seats.iterdir()) if (p / ".git").is_file()] if seats.is_dir() else []
    if linked and (repo / ".git").is_dir():
        proc = git(repo, "worktree", "repair", *linked, check=False)
        if proc.returncode != 0:
            warn(f"archived worktrees in {seats} were not relinked: {proc.stderr.strip()}")
    return dest


def copy_kickoff(source, kickoff: Path) -> None:
    if source is None:
        kickoff.mkdir(parents=True, exist_ok=True)
        return
    if non_empty(kickoff):
        print(f"Kickoff: kept existing {fwd(kickoff)}")
        return
    shutil.copytree(absolute(source), kickoff, ignore=ignore(), dirs_exist_ok=True)


def copy_kit(kit: Path, result: Path) -> None:
    shutil.copytree(kit / "mandates", result / "mandates", ignore=ignore())
    shutil.copytree(kit / "factory", result / "factory", ignore=ignore(*KIT_ONLY))
    for src, dst in (("mandates-src", "factory/mandates-src"), ("tests", "factory/tests")):
        if (kit / src).is_dir():  # a kit that is itself a result repository already has them under factory/
            shutil.copytree(kit / src, result / dst, ignore=ignore(*KIT_ONLY), dirs_exist_ok=True)
    shutil.copyfile(kit / ".gitattributes", result / ".gitattributes")
    write(result / ".gitignore", "\n".join(GITIGNORE) + "\n")
    for stub in ("README.md", "FACTORY.md"):
        write(result / stub, STUB)


def init_result(kit: Path, result: Path, operator: tuple) -> None:
    result.mkdir(parents=True)
    git(result, "init", "-q", "-b", "main")
    git(result, "config", "core.autocrlf", "false")
    git(result, "config", "extensions.worktreeConfig", "true")
    copy_kit(kit, result)
    git(result, "add", "-A")
    name, email = operator
    git(result, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", "Factory kit")
    git(result, "config", "--worktree", "user.name", "Foreman")
    git(result, "config", "--worktree", "user.email", f"foreman@{MAIL_DOMAIN}")
    exclude = result / ".git" / "info" / "exclude"
    lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
    if ".claude/" not in lines:
        write(exclude, "\n".join(lines + [".claude/"]) + "\n")


def write_guard(kit: Path, ws: Path, holdouts: list) -> None:
    cfg = json.loads((kit / "factory" / "roles.json").read_text(encoding="utf-8"))
    cfg["workspace"] = fwd(ws)
    cfg["log"] = fwd(ws / "logs" / "guard.jsonl")
    cfg["holdout"] = [fwd(h) for h in holdouts]
    write_json(ws / "guard" / "roles.json", cfg)
    shutil.copyfile(kit / "factory" / "hooks" / "guard.py", ws / "guard" / "guard.py")


def add_seats(result: Path, seats: Path) -> None:
    for seat, patterns in SEATS.items():
        checkout = seats / seat
        git(result, "worktree", "add", "-q", "-b", f"seat/{seat}", str(checkout))
        git(checkout, "config", "--worktree", "user.name", seat.capitalize())
        git(checkout, "config", "--worktree", "user.email", f"{seat}@{MAIL_DOMAIN}")
        if patterns:
            git(checkout, "sparse-checkout", "set", "--no-cone", "--stdin", stdin="\n".join(patterns) + "\n")


def claude_settings(python: str, ws: Path, seat: str) -> dict:
    guard = shell_arg(fwd(ws / "guard" / "guard.py"))
    rules = shell_arg(fwd(ws / "guard" / "roles.json"))
    command = f"{python} {guard} --seat {seat} --config {rules}"
    return {
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]},
        "env": {"PYTHONUTF8": "1"},
        "includeCoAuthoredBy": False,
    }


def write_oracle(kit: Path, result: Path, oracle: Path, specs: list = ()) -> None:
    """OpenCode config for the Oracle: outside its own checkout it may read only the
    folders that hold the specifications it was given, and nothing else."""
    template = kit / "factory" / "setup" / "opencode" / "opencode.json.template"
    text = template.read_text(encoding="utf-8")
    try:
        cfg = json.loads(text)
    except ValueError as e:
        raise SetupError(f"{template} is not valid JSON: {e}") from None
    folders = sorted({Path(s).parent for s in specs})
    if folders:
        scoped = {"*": "deny"}
        for folder in folders:
            scoped[fwd(folder) + "/*"] = "allow"
            scoped[str(folder).replace("/", "\\") + "\\*"] = "allow"
        cfg.setdefault("permission", {})["external_directory"] = scoped
    write(oracle / "opencode.json", json.dumps(cfg, indent=2) + "\n")
    shutil.copyfile(result / "mandates" / f"{oracle.name}.md", oracle / "AGENTS.md")


def render_dispatch(kit: Path, ws: Path, track, specs: list) -> Path:
    text = (kit / "factory" / "DISPATCH.template.md").read_text(encoding="utf-8")
    values = {
        "{N}": str(len(specs)) if specs else "<n>",
        "{WS}": fwd(ws),
        "{SPEC_LIST}": "\n".join(f"{i}. {fwd(s)}" for i, s in enumerate(specs, 1))
                       or "<specification files, one per stage, in order>",
        "{TRACK}": track or "<track>",
    }
    for key, value in values.items():
        text = text.replace(key, value)
    if not specs or not track:
        warn("no --spec or --track given: fill the <...> placeholders in DISPATCH.md before sending it")
    path = ws / "DISPATCH.md"
    write(path, text)
    return path


# --- entry point -------------------------------------------------------------------------

def parse(argv):
    p = argparse.ArgumentParser(prog="new_run.py", description="Lay out one factory run: result repository, "
                                "seat worktrees with sparse checkouts and role guards, and the dispatch.")
    p.add_argument("--kit", default=str(KIT), help="kit root (default: %(default)s)")
    p.add_argument("--workspace", required=True, help="run workspace, e.g. C:/countersign")
    p.add_argument("--kickoff", help="job package folder; copied to <workspace>/kickoff when that is empty")
    p.add_argument("--holdout", action="append", default=[], metavar="DIR",
                   help="folder of external acceptance tests no implementer may read (repeatable)")
    p.add_argument("--track", help="track name for the acceptance command in DISPATCH.md")
    p.add_argument("--spec", action="append", default=[], metavar="FILE",
                   help="specification file, one per stage, in order (repeatable; DISPATCH.md only)")
    p.add_argument("--archive", action="store_true",
                   help="move an existing run into <workspace>/runs/<UTC stamp>/ first")
    p.add_argument("--operator-name", default=git_config("user.name") or "operator",
                   help="author of the initial commit (default: your git user.name)")
    p.add_argument("--operator-email", default=git_config("user.email") or "operator@localhost",
                   help="email of the initial commit (default: your git user.email)")
    p.add_argument("--python", default="py -3.14", help="command that runs Python in the hook command")
    return p.parse_args(argv)


def run(args) -> Path:
    if shutil.which("git") is None:
        raise SetupError("git is not on PATH")
    kit = absolute(args.kit)
    check_kit(kit)
    ws = absolute(args.workspace)
    if ws.exists() and not ws.is_dir():
        raise SetupError(f"workspace {ws} exists and is not a folder")
    if args.kickoff and not absolute(args.kickoff).is_dir():
        raise SetupError(f"kickoff folder {absolute(args.kickoff)} does not exist")

    found = previous_run(ws)
    if found and not args.archive:
        raise SetupError(f"{fwd(ws)} already holds a run ({', '.join(found)}); rerun with --archive to move it "
                         f"into {fwd(ws / 'runs')}/<UTC stamp>/")
    if found:
        print(f"Archived previous run: {fwd(archive(ws))}")

    copy_kickoff(args.kickoff, ws / "kickoff")
    for name in ("seats", "tmp", "logs", "guard"):
        (ws / name).mkdir(parents=True, exist_ok=True)
    holdouts = [absolute(h) for h in args.holdout]
    specs = [absolute(s) for s in args.spec]
    for kind, paths in (("holdout", holdouts), ("spec", specs)):
        for p in paths:
            if not p.exists():
                warn(f"{kind} {p} does not exist")

    result = ws / "result"
    init_result(kit, result, (args.operator_name, args.operator_email))
    write_guard(kit, ws, holdouts)
    add_seats(result, ws / "seats")
    write_json(result / ".claude" / "settings.local.json", claude_settings(args.python, ws, "foreman"))
    for seat in GUARDED_SEATS:
        harness = harness_of(result / "mandates" / f"{seat}.md")
        if harness == "Claude Code":
            write_json(ws / "seats" / seat / ".claude" / "settings.local.json",
                       claude_settings(args.python, ws, seat))
        elif harness == "OpenCode":
            write_oracle(kit, result, ws / "seats" / seat, specs)
        else:
            warn(f"{seat}: harness {harness!r} gets no guard configuration")
    dispatch = render_dispatch(kit, ws, args.track, specs)

    print(f"Run laid out in {fwd(ws)}")
    print(f"  result  {fwd(result)} (main, Foreman)")
    print(f"  seats   {', '.join(SEATS)} under {fwd(ws / 'seats')}")
    print(f"  guard   {fwd(ws / 'guard' / 'roles.json')}")
    return dispatch


def main(argv=None) -> int:
    args = parse(argv)
    try:
        dispatch = run(args)
    except SetupError as e:
        print(f"new_run: error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"new_run: error: {e}", file=sys.stderr)
        return 2
    print(f"Dispatch: {fwd(dispatch)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
