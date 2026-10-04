#!/usr/bin/env bash
# Run the stage-1 attack battery against a built candidate image.
#
#   run_attacks.sh <candidate-image> <clean-clone-dir> <out-dir> [audit.py args...]
#
# Starts two candidate containers (A = target, B = fresh import destination) on an internal
# network with no outbound access, 2 CPUs and 2 GiB each, then runs audit.py from a runner
# container on the same network (Docker Desktop does not publish ports of internal networks).
# Stops and removes everything it started. Exit code is audit.py's.
set -u
IMG="$1"; CLONE="$2"; OUT="$3"; shift 3
NET=auditor-s1-int
A=auditor-s1-a
B=auditor-s1-b
RUNNER_IMG=auditor-runner-img
HERE="$(cd "$(dirname "$0")" && pwd)"
export MSYS_NO_PATHCONV=1
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }

cleanup() {
  docker logs "$A" > "$OUT/candidate-a.log" 2>&1 || true
  docker logs "$B" > "$OUT/candidate-b.log" 2>&1 || true
  docker rm -f "$A" "$B" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "$OUT"
docker image inspect "$RUNNER_IMG" >/dev/null 2>&1 || docker build -q -t "$RUNNER_IMG" -f "$HERE/Dockerfile.runner" "$HERE" >/dev/null
docker rm -f "$A" "$B" >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create --internal "$NET" >/dev/null
for c in "$A" "$B"; do
  docker run -d --name "$c" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$IMG" >/dev/null
done
docker run --rm --network "$NET" --cpus 2 --memory 1g \
  -v "$(win "$CLONE"):/repo:ro" -v "$(win "$OUT"):/out" \
  -e BURST_DIR=/repo/factory/tools -e PYTHONUNBUFFERED=1 \
  "$RUNNER_IMG" python /repo/stage-1/verify/audit/audit.py \
  --base "http://$A:8080" --dest "http://$B:8080" --out /out/attacks.json "$@"
rc=$?
docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' "$A" "$B" > "$OUT/stats.txt" 2>&1 || true
exit $rc
