#!/usr/bin/env bash
# Run the Oracle differential runner (battery step 3) against a built candidate image.
#
#   run_diff.sh <candidate-image> <clean-clone-dir> <out-dir> <seed> <runs> <ops>
#
# One candidate on an internal network (no egress, 2 CPU / 2 GiB); the runner (model in-process)
# executes in a container on the same network. Mismatch files land in <out-dir>/seed-<seed>/.
set -u
IMG="$1"; CLONE="$2"; OUT="$3"; SEED="$4"; RUNS="$5"; OPS="$6"
NET=auditor-s3-diff-net
SVC=auditor-s3-diff
export MSYS_NO_PATHCONV=1
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
cleanup() { docker rm -f "$SVC" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
mkdir -p "$OUT/seed-$SEED"
cleanup
docker network create --internal "$NET" >/dev/null
docker run -d --name "$SVC" --network "$NET" --cpus 2 --memory 2g -e PORT=8080 "$IMG" >/dev/null
docker run --rm --network "$NET" -v "$(win "$CLONE"):/repo:ro" -v "$(win "$OUT/seed-$SEED"):/out" -w /tmp \
  -e PYTHONDONTWRITEBYTECODE=1 auditor-runner-py \
  sh -c "until wget -qO- http://$SVC:8080/health >/dev/null 2>&1; do sleep 0.2; done; \
         python /repo/stage-3/verify/oracle/diff_runner.py --base-url http://$SVC:8080 --seed $SEED --runs $RUNS --ops $OPS --out-dir /out"
