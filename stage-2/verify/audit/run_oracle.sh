#!/usr/bin/env bash
# Run the Oracle acceptance suite (battery step 2) against a built candidate image.
#
#   run_oracle.sh <candidate-image> <clean-clone-dir> <out-dir> [pytest args...]
#
# Two candidate containers (A, B for the cross-process import test) on an internal network with no
# outbound access, 2 CPUs / 2 GiB each; pytest runs from a runner container on the same network.
set -u
IMG="$1"; CLONE="$2"; OUT="$3"; shift 3
NET=auditor-s2-oracle-net
A=auditor-s2-oa
B=auditor-s2-ob
HERE="$(cd "$(dirname "$0")" && pwd)"
export MSYS_NO_PATHCONV=1
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
cleanup() {
  docker logs "$A" > "$OUT/oracle-a.log" 2>&1 || true
  docker rm -f "$A" "$B" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
mkdir -p "$OUT"
docker image inspect auditor-runner-py >/dev/null 2>&1 || docker build -q -t auditor-runner-py -f "$HERE/Dockerfile.runner-py" "$HERE" >/dev/null
docker rm -f "$A" "$B" >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create --internal "$NET" >/dev/null
for c in "$A" "$B"; do
  docker run -d --name "$c" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$IMG" >/dev/null
done
docker run --rm --network "$NET" -v "$(win "$CLONE"):/repo:ro" -v "$(win "$OUT"):/out" -w /tmp \
  -e PYTHONDONTWRITEBYTECODE=1 auditor-runner-py \
  sh -c "until wget -qO- http://$A:8080/health >/dev/null 2>&1 && wget -qO- http://$B:8080/health >/dev/null 2>&1; do sleep 0.2; done; \
         python -m pytest /repo/stage-2/verify/oracle -q -p no:cacheprovider -rfE \
         --base-url http://$A:8080 --second-base-url http://$B:8080 --junitxml=/out/oracle.xml $*"
