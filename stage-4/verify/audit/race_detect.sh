#!/usr/bin/env bash
# Race proof with Go's race detector, for guards whose absence no HTTP check observes directly
# (e.g. a shared read lock). Builds the stage twice with -race (as given, and with literal edits that
# disable the guard), runs each on an internal network under the burst attacks, and counts
# "WARNING: DATA RACE" reports. RED-without = reports > 0; GREEN-with = 0 reports and no hard failures.
#
#   race_detect.sh <stage-src> <work-dir> <out-json> <groups|readwrite> <rounds> FILE::OLD::NEW [...]
# groups=readwrite runs readwrite_load.py (mixed concurrent reads and writes) instead of audit.py.
set -u
SRC="$1"; WORK="$2"; OUT="$3"; GRPS="$4"; ROUNDS="$5"; shift 5
HERE="$(cd "$(dirname "$0")" && pwd)"
AUD="$(cd "$HERE/../../.." && pwd)"
NET=auditor-s4-racenet
SRV=auditor-s4-race-srv
export MSYS_NO_PATHCONV=1
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
rm -rf "$WORK"; mkdir -p "$WORK"
python - "$SRC" "$WORK" "$@" <<'EOF'
import shutil, sys, pathlib
src, work, edits = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3:]
for v in ("with", "without"):
    shutil.copytree(src, work / v, ignore=shutil.ignore_patterns("verify", ".git", ".bg"))
for e in edits:
    f, old, new = (x.replace("\\n", "\n").replace("\\t", "\t") for x in e.split("::", 2))
    p = work / "without" / f
    t = p.read_bytes().decode()
    assert t.count(old) >= 1, f"edit not applicable: {old!r}"
    p.write_bytes(t.replace(old, new).encode())
EOF
docker network rm "$NET" >/dev/null 2>&1; docker network create --internal "$NET" >/dev/null
declare -A RES
for v in with without; do
  D="$WORK/$v"; mkdir -p "$D/out"
  docker run --rm -v "$(win "$D"):/src" -v "$(win "$D/out"):/out" -v auditor-gocache-race:/root/.cache/go-build \
    -e CGO_ENABLED=1 -e GOFLAGS=-mod=vendor -e GOTOOLCHAIN=local -w /src golang:1.26 go build -race -o /out/tk-race . \
    || { echo "build failed ($v)"; exit 2; }
  docker rm -f "$SRV" >/dev/null 2>&1
  docker run -d --name "$SRV" --network "$NET" --cpus 2 --memory 2g -v "$(win "$D/out"):/out" -e PORT=8080 \
    -e GORACE="halt_on_error=0 log_path=/out/race" golang:1.26 /out/tk-race >/dev/null
  docker run --rm --network "$NET" -v "$(win "$AUD"):/aud:ro" -v "$(win "$D/out"):/o" auditor-runner-py \
    sh -c "if [ '$GRPS' = readwrite ]; then sleep 1; python /aud/stage-4/verify/audit/readwrite_load.py --base http://$SRV:8080 --rounds $ROUNDS > /o/load.txt 2>&1; echo '{\"hard_failures\": 0}' > /o/audit.json;       else python /aud/stage-4/verify/audit/audit.py --base http://$SRV:8080 --groups $GRPS --rounds $ROUNDS --wait 30 --out /o/audit.json >/dev/null 2>&1; fi"
  docker rm -f "$SRV" >/dev/null 2>&1
  n=$(cat "$D"/out/race.* 2>/dev/null | grep -c "WARNING: DATA RACE")
  hard=$(python -c "import json;print(json.load(open('$(win "$D/out/audit.json")'))['hard_failures'])" 2>/dev/null || echo "?")
  first=$(cat "$D"/out/race.* 2>/dev/null | grep -m1 -A6 "WARNING: DATA RACE" | grep -m2 "tablekeeper/internal" | tr -s ' \t' ' ' | tr '\n' ';')
  RES[$v]="{\"data_races\": $n, \"hard_failures\": \"$hard\", \"first_race\": \"${first//\"/}\"}"
done
docker network rm "$NET" >/dev/null 2>&1
cat > "$OUT" <<EOF
{"edits": $(python -c "import json,sys;print(json.dumps(sys.argv[1:]))" "$@"),
 "groups": "$GRPS", "rounds": $ROUNDS,
 "with": ${RES[with]},
 "without": ${RES[without]}}
EOF
cat "$OUT"
