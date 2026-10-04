[VERDICT] stage=1 sha=c0f2b7b06069d2c3d58fdd4aef1f26a0c903e9bb result=ACCEPT

# Verdict 6 — stage 1 (candidate 5)

- Candidate: c0f2b7b06069d2c3d58fdd4aef1f26a0c903e9bb (main, "[PLAN] stage-1 candidate 5"); supersedes candidate 4 (1bfe6e2, verdicts 4–5)
- Clean clone: C:/countersign/tmp/aud-s1-v6 (`git clone C:/countersign/result`, `git checkout c0f2b7b…`; working tree clean before the holdout)
- Product delta since 1bfe6e2: WI-7 (Builder f4dd4c6) — `referencePattern` in `internal/state/reservations.go`, used by the reset loader
  (`fixture.go`) and import (`integrity.go`) per R-28; plus Go unit tests
- Battery code: seat/auditor @ 885d143; Oracle suite (149 tests) and differential runner from the clone (seat/oracle 4d21f7a)
- Contract: `evidence/stage-1/ledger.md` C1.1–C1.134; rulings R-1 … R-28 (R-26 withdrawn by R-28)
- Every step run on this SHA from the clean clone. Artifacts: `evidence/stage-1/verdicts/verdict-6-artifacts/`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage1 .` (RUN.md); `--internal`, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | pass (149/149) | `run_oracle.sh auditor-s1-c5 C:/countersign/tmp/aud-s1-v6 …/oracle` | 0 |
| 3 | differential runner (seeds 1 ×50, 620154 ×5, fresh 267942 ×50; 80 ops) | pass (8505 ops, no mismatch) | `run_diff.sh auditor-s1-c5 … <seed> <runs> 80` | 0 |
| 4 | attacks | pass (617 checks, 0 hard, 0 soft; 98 clauses) | `run_attacks.sh auditor-s1-c5 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (54/66 = 81.8 %) | pass (target 75 %) | `mutate.py --src …/aud-s1-v6/stage-1 --targets internal/state,internal/localtime,internal/api,internal/snapshot,internal/jsonin --budget 1200 --workers 4 --seed 5 --oracle …` | 0 |
| 6 | race proofs (5 guards) | pass (5/5 red-without, 5/5 green-with) | `race.py` (G1–G4), `race_detect.sh … readwrite 5` (G5) | 0 |
| 7 | user-facing checks | skip | no user-facing surface in stage 1 (C1.2 "Only the HTTP API is required.") | n/a |
| 8 | holdout (isolated) | pass (120/120) | `PYTHONUTF8=1 C:/countersign/.venv/Scripts/python.exe C:/countersign/result/factory/setup/harness_win.py run --track tablekeeper --repo C:/countersign/tmp/aud-s1-v6 --stage 1 --mode isolated --out C:/countersign/tmp/checks/auditor-s1-v6-holdout` (from C:/countersign/kickoff) | 0 |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s1-v6/stage-1 && docker build -t tablekeeper-stage1 .     → exit 0 (19.8 s), sha256:f65ecbd0…, tagged auditor-s1-c5
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.07 200 {"status":"ok"}; egress: blocked URLError
  no PORT → :8080/health 200 {"status":"ok"}; PORT=18300 → :18300/health 200 {"status":"ok"}
```
### Step 2
```
$ bash stage-1/verify/audit/run_oracle.sh auditor-s1-c5 C:/countersign/tmp/aud-s1-v6 C:/countersign/tmp/aud-s1-v6-out/oracle
149 passed in 27.49s          (two candidates A/B on an --internal network; --second-base-url for the cross-process import)
```
### Step 3
```
OK: 50 runs, 4050 ops, seeds 1..50, 24.6s
OK: 5 runs, 405 ops, seeds 620154..620158, 2.8s
OK: 50 runs, 4050 ops, seeds 267942..267991, 23.1s        (fresh seed)
```
### Step 4
```
$ bash stage-1/verify/audit/run_attacks.sh auditor-s1-c5 C:/countersign/seats/auditor C:/countersign/tmp/aud-s1-v6-out/attacks --rounds 3
checks 617  hard 0  soft 0  errors []
incl.: seeded references X / seed01 / ABCDEFGHJKLMN / SEED-01 → 422, state unchanged (verdict-5 escape, now green);
R-25 non-exact paths → 404; tampered export (duplicate reference) → 422 unchanged; bursts ×3 (B1–B10); export/import into a
fresh container; 98 ledger clauses exercised. Version upgrade: not applicable at stage 1.
```
### Step 5
```
containerised (Linux build per mutant; server + audit.py + Oracle pytest in one --network none container, 2 CPU / 2 GiB;
kill must reproduce on a second run); baseline 0 failing checks
generated 473, evaluated 73 (1230 s), killed 54, survived 12, invalid 7 → 81.8 %
```
### Step 6
```
G1 Store.Write lock          without: run 2 crashed (concurrent map writes), 7 checks; with: 0      RED/GREEN
G2 signup re-check           without: B8 wrong r0–r2; with: 0                                      RED/GREEN
G3 idempotency one section   split + 5 ms window: B3, B4 (12 checks); with: 0                       RED/GREEN
G4 batch check-before-apply  without: failed batch left changes (4 checks); with: 0                 RED/GREEN
G5 Store.Read lock           race detector + readwrite_load 5×60: without 7 DATA RACE, with 0       RED/GREEN
```
### Step 7
```
skipped: no user-facing surface in stage 1 (C1.2)
```
### Step 8
```
building C:\countersign\tmp\aud-s1-v6\stage-1 ...
  stage 1: pass
highest contiguous stage: 1      claimed stage: 1      revision c0f2b7b…      mode isolated
stage 1: collected 120, passed 120, failed 0  (health/reset/auth 12/12, reservations 37/37, restaurants/availability 18/18,
  retries/time input 20/20, sample 20/20, seeded state 13/13)
(stage 2 in the report is the harness's unclaimed overshoot probe; not part of this stage)
```

## Findings
None. Every step is green; no ruling-explained gap remains (R-26 withdrawn; the earlier step-2 test defects are fixed in the suite at this SHA).

## Work items for the Oracle (surviving mutants → stronger tests; not blocking)
- O-9 C1.109 / R-24 — import refusals not yet observed: a user record with an invalid id but valid email/hash (integrity.go:39),
  a receipt whose status is out of range but body valid JSON, and the reverse (integrity.go:80), a state missing only one of
  `users`/`restaurants`/`reservations` (integrity.go:33 ×2), a number field holding a non-number starting with `-`-less text (snapshot.go:133).
- O-10 C1.34 — bodies that are JSON `null` (`null` → 400 malformed_request) on keyed and unkeyed writes (jsonin.go:25), a
  moves/array item `null` decoded through the generic path (jsonin.go:144), and an integral `party_size` beyond 2^53 (jsonin.go:111).
- O-11 R-8 — fixture id empty string `""` for user/restaurant/table/reservation ids (fixture.go:57).
- Equivalent (no action): localtime.go:216, :272 (guarded by fixture validation / end check); snapshot.go:114 (struct tags).

## Counts for the stage report
- mutation: tool mutate.py (containerised textual Go mutants, HTTP killers audit.py + Oracle pytest), killed 54, total 66 (81.8 %; 473 generated, 73 evaluated, 7 invalid)
- race proofs: G1 Store.Write lock / bursts + crash / red-without yes / green-with yes; G2 signup re-check / B8 / yes / yes;
  G3 idempotency single section / B3,B4 / yes / yes; G4 batch check-before-apply / moves atomicity / yes / yes;
  G5 Store.Read lock / race detector + readwrite_load / yes / yes
- holdout: ran yes, passed 120, failed 0, escapes none on this SHA (stage history: 1 escape, C1.81 seeded references, found on 1bfe6e2 and fixed by WI-7)

## Result
result=ACCEPT — every battery step is green on c0f2b7b from a clean clone, including the isolated holdout (120/120).
