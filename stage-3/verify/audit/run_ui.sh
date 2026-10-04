#!/usr/bin/env bash
# Battery step 7 (stage 2): browser checks against a built candidate image.
#
#   [PREV_IMG=<accepted previous-stage image>] run_ui.sh <candidate-image> <battery-root> <out-dir> [ui_audit.py args...]
#
# Candidate A (and, with PREV_IMG, the previous stage P) on an internal network with no outbound access,
# 2 CPUs / 2 GiB; headless Chromium (auditor-ui-runner, Dockerfile.ui) on the same network. Results go to
# <out-dir>/ui.json, screenshots of every named state to <out-dir>/shots/. Stops what it starts.
set -u
IMG="$1"; ROOT="$2"; OUT="$3"; shift 3
NET=auditor-s3-ui-net
A=auditor-s3-ua
P=auditor-s3-up
PREV_IMG="${PREV_IMG:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export MSYS_NO_PATHCONV=1
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
cleanup() {
  docker logs "$A" > "$OUT/ui-candidate.log" 2>&1 || true
  docker rm -f "$A" "$P" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
mkdir -p "$OUT/shots"
docker image inspect auditor-ui-runner >/dev/null 2>&1 || docker build -q -t auditor-ui-runner -f "$HERE/Dockerfile.ui" "$HERE" >/dev/null
docker rm -f "$A" "$P" >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create --internal "$NET" >/dev/null
docker run -d --name "$A" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$IMG" >/dev/null
PREV_ARGS=()
if [ -n "$PREV_IMG" ]; then
  docker run -d --name "$P" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$PREV_IMG" >/dev/null
  PREV_ARGS=(--prev "http://$P:8080")
fi
docker run --rm --network "$NET" --shm-size 1g -v "$(win "$ROOT"):/repo:ro" -v "$(win "$OUT"):/out" \
  -e BURST_DIR=/repo/factory/tools -e PYTHONUNBUFFERED=1 -e PYTHONDONTWRITEBYTECODE=1 -w /tmp auditor-ui-runner \
  sh -c "until python -c \"import urllib.request;urllib.request.urlopen('http://$A:8080/health',timeout=1)\" 2>/dev/null; do sleep 0.2; done; \
         python /repo/stage-3/verify/audit/ui_audit.py --base http://$A:8080 ${PREV_ARGS[*]} --out /out/ui.json --shots /out/shots $*"
