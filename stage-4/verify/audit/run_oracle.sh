#!/usr/bin/env bash
# Run the Oracle acceptance suites (battery step 2) against a built candidate image.
#
#   [PREV_IMG=<accepted stage-1 image>] run_oracle.sh <candidate-image> <clean-clone-dir> <out-dir> [pytest args...]
#
# Candidates A and B (cross-process import) and, with PREV_IMG, the previous stage S1 (upgrade source) on an
# internal network with no outbound access, 2 CPUs / 2 GiB each. The HTTP suites run in auditor-runner-py; the
# browser suite (test_ui.py --ui) runs in auditor-ui-runner (Chromium + Playwright + pytest) on the same network,
# against a fresh reset of A. JUnit: <out-dir>/oracle.xml and <out-dir>/oracle-ui.xml.
set -u
IMG="$1"; CLONE="$2"; OUT="$3"; shift 3
NET=auditor-s4-oracle-net
A=auditor-s4-oa
B=auditor-s4-ob
S1=auditor-s4-os1
S2=auditor-s4-os2
S3=auditor-s4-os3
PREV_IMG="${PREV_IMG:-}"
PREV2_IMG="${PREV2_IMG:-}"
PREV3_IMG="${PREV3_IMG:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export MSYS_NO_PATHCONV=1
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
cleanup() {
  docker logs "$A" > "$OUT/oracle-a.log" 2>&1 || true
  docker rm -f "$A" "$B" "$S1" "$S2" "$S3" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
mkdir -p "$OUT"
docker image inspect auditor-runner-py >/dev/null 2>&1 || docker build -q -t auditor-runner-py -f "$HERE/Dockerfile.runner-py" "$HERE" >/dev/null
docker image inspect auditor-ui-runner >/dev/null 2>&1 || docker build -q -t auditor-ui-runner -f "$HERE/Dockerfile.ui" "$HERE" >/dev/null
docker rm -f "$A" "$B" "$S1" "$S2" "$S3" >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create --internal "$NET" >/dev/null
for c in "$A" "$B"; do
  docker run -d --name "$c" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$IMG" >/dev/null
done
S1_ARG=""
if [ -n "$PREV_IMG" ]; then
  docker run -d --name "$S1" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$PREV_IMG" >/dev/null
  S1_ARG="--stage1-base-url http://$S1:8080"
fi
if [ -n "$PREV2_IMG" ]; then
  docker run -d --name "$S2" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$PREV2_IMG" >/dev/null
  S1_ARG="$S1_ARG --stage2-base-url http://$S2:8080"
fi
if [ -n "$PREV3_IMG" ]; then
  docker run -d --name "$S3" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$PREV3_IMG" >/dev/null
  S1_ARG="$S1_ARG --stage3-base-url http://$S3:8080"
fi
WAIT="until wget -qO- http://$A:8080/health >/dev/null 2>&1 && wget -qO- http://$B:8080/health >/dev/null 2>&1; do sleep 0.2; done;"
docker run --rm --network "$NET" -v "$(win "$CLONE"):/repo:ro" -v "$(win "$OUT"):/out" -w /tmp \
  -e PYTHONDONTWRITEBYTECODE=1 auditor-runner-py \
  sh -c "$WAIT python -m pytest /repo/stage-4/verify/oracle -q -p no:cacheprovider -rfE \
         --base-url http://$A:8080 --second-base-url http://$B:8080 $S1_ARG --junitxml=/out/oracle.xml $*"
rc1=$?
docker run --rm --network "$NET" --shm-size 1g -v "$(win "$CLONE"):/repo:ro" -v "$(win "$OUT"):/out" -w /tmp \
  -e PYTHONDONTWRITEBYTECODE=1 auditor-ui-runner \
  sh -c "python -m pytest /repo/stage-4/verify/oracle/test_ui.py -q --ui -p no:cacheprovider -rfE \
         --base-url http://$A:8080 --junitxml=/out/oracle-ui.xml $*"
rc2=$?
[ $rc1 -eq 0 ] && [ $rc2 -eq 0 ]
