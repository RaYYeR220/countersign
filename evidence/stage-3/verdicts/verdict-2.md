[VERDICT] stage=3 sha=c4a828e341d997f00b8c078c423135fbccf6fdac result=ACCEPT

# Verdict 2 — stage 3 (candidate 2)

- Candidate: c4a828e341d997f00b8c078c423135fbccf6fdac (main, "[PLAN] stage-3 candidate 2"); supersedes candidate 1 (1aaecec, verdict 1)
- Clean clone: C:/countersign/tmp/aud-s3-v2 (working tree clean before the holdout); `stage-1/` = c0f2b7b and `stage-2/` = aa63cd2 outside verify
- Product delta since 1aaecec: WI-21 (R-60, `internal/api/series.go`), WI-22 (RUN.md retitled, tag `tablekeeper-stage3`), Go tests only otherwise
- Upgrade sources: `auditor-s1-c5` (stage-1/ c0f2b7b), `auditor-s2-c5` (stage-2/ aa63cd2)
- Battery: seat/auditor (stage-3/verify/audit); Oracle stage-3/verify/oracle from the clone (245 HTTP incl. 19 browser-skipped, 20 browser)
- Contract: C3.1–C3.53, C2.1–C2.59, C1.1–C1.134; rulings R-1…R-60
- Artifacts: `evidence/stage-3/verdicts/verdict-2-artifacts/`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage3 .` (RUN.md); `--internal`, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | pass (HTTP 226/226 with stage-1 + stage-2 sources; browser 20/20) | `PREV_IMG=auditor-s1-c5 PREV2_IMG=auditor-s2-c5 run_oracle.sh auditor-s3-c2 …` | 0 |
| 3 | differential runner (seeds 1 ×50, 312570 ×5, fresh 273450 ×50; 80 ops) | pass (8505 ops, no mismatch) | `run_diff.sh auditor-s3-c2 … <seed> <runs> 80` | 0 |
| 4 | attacks | pass (1059 checks, 23 groups, bursts ×3, upgrade from stage 1 and stage 2) | `PREV_IMG=… PREV2_IMG=… run_attacks.sh auditor-s3-c2 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (22/27 = 81.5 %) | pass (target 75 %) | `mutate.py --src …/aud-s3-v2/stage-3 … --budget 1200 --workers 4 --seed 33 --oracle …` | 0 |
| 6 | race proofs (8 guards) | pass (8/8 red-without, 8/8 green-with) | `race.py` (G1–G4, G6–G8), `race_detect.sh … readwrite 5` (G5) | 0 |
| 7 | user-facing checks (stage-2 browser regression) | pass (116) | `PREV_IMG=auditor-s1-c5 run_ui.sh auditor-s3-c2 …` | 0 |
| 8 | holdout (isolated, --stage 3) | pass — stage 1 120/120, stage 2 25/25, stage 3 7/7 | `PYTHONUTF8=1 C:/countersign/.venv/Scripts/python.exe C:/countersign/result/factory/setup/harness_win.py run --track tablekeeper --repo C:/countersign/tmp/aud-s3-v2 --stage 3 --mode isolated --out C:/countersign/tmp/checks/auditor-s3-v2-holdout` (from C:/countersign/kickoff) | 0 |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s3-v2/stage-3 && docker build -t tablekeeper-stage3 .     → exit 0; tagged auditor-s3-c2
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.38 200 {"status":"ok"}; egress blocked; no PORT → :8080 200; PORT=18300 → 200
  / /signup /login /lookup → 200 text/html; charset=utf-8, no external src/href
```
### Step 2
```
HTTP: 226 passed, 19 skipped (browser)  junit tests=245 failures=0      Browser (--ui): 20 passed
```
### Step 3
```
OK: 50 runs, 4050 ops, seeds 1..50          OK: 5 runs, 405 ops, seeds 312570..312574          OK: 50 runs, 4050 ops, seeds 273450..273499 (fresh)
```
### Step 4
```
checks 1059  hard 0  soft 0   (stage-1/2 groups; explain, policies, history, revision, series incl. R-60, s3moves, s3burst ×3, upgrade3)
```
### Step 5
```
generated 788, evaluated 28 (1381 s), killed 22, survived 5, invalid 1 → 81.5 %; baseline 0 failing
survivors (all in import-state validation → Oracle O-15): integrity.go:93 (receipt status 200 boundary), :141 (opening hours && in an
imported policy), :152 (policy slot_minutes 1 boundary), :195 (series nil/id), snapshot.go:158 (struct tag handling)
```
### Step 6
```
G1 write lock → B7 + overlaps + crash; G2 signup → B8; G3 idempotency (5 ms window) → B3/B5; G4 batch → failed batch left changes;
G5 read lock → race detector 24 vs 0; G6 member-wide occupancy → overlaps; G7 already_in_series → R3 two adoptions;
G8 partial adoption → "failed adoption created nothing", first-failing-occurrence; every green-with run: 0 failures
```
### Step 7
```
checks 116 hard 0 (stage-2 browser regression on the stage-3 service)
```
### Step 8
```
stage 1: pass   (upgrade source stage-1 built)   stage 2: pass   (upgrade source stage-2 built)   stage 3: pass
highest contiguous stage: 3      claimed stage: 3      revision c4a828e…      mode isolated
stage 1 120/120; stage 2 25/25 (sample 8, ui 17); stage 3 7/7 (sample)
(stage 4 in the report is the harness's unclaimed overshoot probe; not part of this stage)
```

## Findings
None on this SHA. Stage history: verdict 1 (1aaecec) R-60 → WI-21, RUN.md → WI-22.

## Work items for the Oracle (surviving mutants; not blocking)
- O-15 C3.48 / R-55 / R-24 — import refusals for stage-3 states at the boundaries: a receipt with status exactly 200 (accepted) and 199
  (refused); a published policy with slot_minutes 1 (accepted); an imported policy's opening hours with a bad `opens` only; a series entry
  that is null or keyed by a different id.

## Counts for the stage report
- mutation: tool mutate.py (containerised; killers audit.py 18 groups + Oracle HTTP suite), killed 22, total 27 (81.5 %; 788 generated, 28 evaluated, 1 invalid)
- race proofs: G1 write lock / G2 signup / G3 idempotency / G4 batch / G5 read lock (race detector) / G6 member occupancy /
  G7 already_in_series / G8 atomic adoption — 8/8 red-without, 8/8 green-with
- holdout: ran yes; stage 1 120/0, stage 2 25/0, stage 3 7/0; escapes none

## Result
result=ACCEPT — every battery step is green on c4a828e from a clean clone, including the isolated holdout through stage 3.
