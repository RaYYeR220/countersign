"""Create the factory's five seats as Band Desktop agents.

Seats are created once and keep constant working directories under the workspace, so a
new run (new_run.py) never needs them re-pointed. Each seat reads its mandate live from
`<workspace>/result/mandates/<seat>.md`.

Usage:
    py -3.14 factory/setup/seats.py --workspace C:/countersign \
        --launcher C:/Users/<you>/.claude-countersign/seat-launcher.exe [--dry-run] [--only builder]
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

CLAUDE_MODEL = "claude-opus-5-5"
DISALLOWED = ["Workflow", "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "EnterWorktree",
              "ExitWorktree", "DesignSync", "RemoteTrigger", "PushNotification", "ReportFindings"]

SEATS = {
    "foreman": ("Foreman", "Plans the job, cuts work items, integrates, rules on ambiguity and reports. Writes no product code.", "result"),
    "builder": ("Builder", "Implements core behaviour, interfaces, state, concurrency control and version migration.", "seats/builder"),
    "stylist": ("Stylist", "Implements user-facing surfaces and their states; takes ordinary implementation work when there is none.", "seats/stylist"),
    "oracle": ("Oracle", "Reads the specification independently and builds a blind executable reference model and acceptance suites.", "seats/oracle"),
    "auditor": ("Auditor", "The only seat that accepts a candidate: runs the full gate battery from a clean clone. Never edits product code.", "seats/auditor"),
}


def band_exe() -> str:
    found = shutil.which("band")
    if found:
        return found
    local = Path.home() / "AppData" / "Local" / "Band" / "band.exe"
    if local.exists():
        return str(local)
    sys.exit("band CLI not found")


def mandate_header(path: Path) -> tuple[str, str]:
    harness = model = ""
    for line in path.read_text(encoding="utf-8").splitlines()[:5]:
        if line.startswith("Harness:"):
            harness = line.split(":", 1)[1].strip()
        if line.startswith("Model:"):
            model = line.split(":", 1)[1].strip()
    return harness, model


def command(seat: str, ws: Path, launcher: str, dry_run: bool, opencode_launcher: str = "") -> list[str]:
    name, description, rel = SEATS[seat]
    mandate = ws / "result" / "mandates" / f"{seat}.md"
    harness, model = mandate_header(mandate)
    cmd = [band_exe(), "agent", "create", "--session", f"cs-{seat}", "--name", name,
           "--description", description, "--cwd", str(ws / rel), "--json"]
    if harness == "Claude Code":
        cmd += ["--transport", "claude-code-cli", "--spawn-command", launcher,
                "--runtime-auth", "subscription", "--runtime-model", model or CLAUDE_MODEL,
                "--runtime-effort", "high", "--claude-permission-mode", "bypassPermissions",
                "--claude-context-mode", "local_config"]
        for tool in DISALLOWED:
            cmd += ["--claude-disallowed-tool", tool]
    elif harness == "OpenCode":
        cmd += ["--transport", "opencode", "--runtime-model", model]
        if opencode_launcher:
            # A custom spawn command gets no default arguments: name the ACP entry point.
            cmd += ["--spawn-command", opencode_launcher, "--spawn-arg", "acp"]
    else:
        sys.exit(f"{mandate}: unsupported harness {harness!r}")
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd += ["--instructions-file", str(mandate)]
    return cmd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--launcher", required=True)
    ap.add_argument("--opencode-launcher", default="", help="launcher for OpenCode seats (clean profile)")
    ap.add_argument("--only", action="append", choices=sorted(SEATS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    failed = 0
    for seat in args.only or list(SEATS):
        out = subprocess.run(command(seat, ws, args.launcher, args.dry_run, args.opencode_launcher), capture_output=True, text=True)
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            data = {"ok": False, "error": (out.stdout + out.stderr).strip()[:2000]}
        probe = data.get("probe") or {}
        print(json.dumps({"seat": seat, "ok": data.get("ok"), "message": probe.get("message") or data.get("error"),
                          "created": data.get("created")}, indent=2))
        failed += not data.get("ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
