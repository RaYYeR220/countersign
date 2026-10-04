@Foreman Job for the factory. Build the service specified below in {N} stages, one folder per stage, each a
complete buildable service, following the Countersign protocol.

Workspace: {WS}
Result repository (your checkout, branch main): {WS}/result
Seat checkouts: {WS}/seats/<seat>   Temporary space: {WS}/tmp
Specifications, in order:
{SPEC_LIST}
External acceptance command (holdout; the Auditor runs it last for each stage, from {WS}/kickoff):
  PYTHONUTF8=1 {WS}/.venv/Scripts/python.exe {WS}/result/factory/setup/harness_win.py run --track {TRACK} --repo {WS}/tmp/<clone> --stage <n> --mode isolated --out {WS}/tmp/checks/<unique>
Platform: Windows host, Docker Desktop available, use the Bash tool (Git Bash). Docker Desktop does not
forward published ports for containers on an internal network: reach such a container from another
container on the same network. Python 3 is available as `python`. Service contract for every stage
folder is in the specifications.

Build the stages in order; each stage is accepted before the next one starts. Report [FINAL] when done.
