[VERDICT] stage=4 sha=2621e7a648fe78b450f9a2e19c18139c55be1698 result=ACCEPT

# Verdict 4 — stage 4 (candidate 4)

- Candidate: 2621e7a648fe78b450f9a2e19c18139c55be1698 (main, "[PLAN] stage-4 candidate 4"); supersedes candidate 3 (b26757c, verdict 3)
- Clean clone: C:/countersign/tmp/aud-s4-v4. The working tree was clean before and after the holdout. `stage-1/`, `stage-2/` and `stage-3/` have 0 product
  files different from c0f2b7b, aa63cd2 and c4a828e.
- Product delta since b26757c: WI-31 (R-77) — `stage-4/internal/state/integrity.go` (plus a Go test)
- Images, all `docker build --no-cache` from the clone: candidate `auditor-s4-c4` (sha256:3ce94d6e…, export schema 4). Upgrade sources:
  `auditor-s4v4-src1` (stage-1/, schema 1), `auditor-s4v4-src2` (stage-2/, schema 2), `auditor-s4v4-src3` (stage-3/, schema 3).
  All four were named in the room for the Oracle's kit.
- Battery code: seat/auditor @ bd64a3d (stage-4/verify/audit; adds `r77` over the clone's merged copy); Oracle suites from the clone (268 HTTP incl.
  20 browser-skipped, 21 browser)
- Contract: C1–C4; rulings R-1…R-77
- Artifacts: `evidence/stage-4/verdicts/verdict-4-artifacts/` (junit, attack/browser JSON, screenshots, race and mutation JSON, differential output,
  holdout report)

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage4 .` (RUN.md, `stage-4/`); `--internal`, `--cpus 2 --memory 2g` | 0 |
| 2 | acceptance suites | pass (HTTP 248/248, 20 browser-skipped; browser 21/21) | `PREV_IMG=auditor-s4v4-src1 PREV2_IMG=auditor-s4v4-src2 PREV3_IMG=auditor-s4v4-src3 run_oracle.sh auditor-s4-c4 C:/countersign/tmp/aud-s4-v4 …/oracle` | 0 |
| 3 | differential (seed 1 ×50; fresh 497914 ×50; 80 ops) | pass (8100 ops, no mismatch) | `run_diff.sh auditor-s4-c4 … <seed> 50 80` | 0 |
| 4 | attacks | pass (1254 checks, 32 groups) | `PREV_IMG=… PREV2_IMG=… PREV3_IMG=… run_attacks.sh auditor-s4-c4 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (34/38 = 89.5 %) | pass (target 75 %) | `mutate.py --src …/aud-s4-v4/stage-4 --targets internal/api,internal/state,internal/snapshot,internal/localtime,internal/jsonin,internal/planner --budget 1200 --workers 4 --seed 43 --oracle …` | 0 |
| 6 | race proofs (10 guards) | pass (10/10 red-without, 10/10 green-with) | `race.py` (G1–G4, G6–G10), `race_detect.sh … readwrite 10` (G5) | 0 |
| 7 | user-facing checks | pass (119 browser checks incl. a replanned restaurant) | `PREV_IMG=auditor-s4v4-src1 run_ui.sh auditor-s4-c4 C:/countersign/seats/auditor …/ui` | 0 |
| 8 | holdout (isolated, --stage 4) | pass — stage 1 120/120, stage 2 25/25, stage 3 7/7, stage 4 6/6; highest contiguous 4 | `PYTHONUTF8=1 C:/countersign/.venv/Scripts/python.exe C:/countersign/result/factory/setup/harness_win.py run --track tablekeeper --repo C:/countersign/tmp/aud-s4-v4 --stage 4 --mode isolated --out C:/countersign/tmp/checks/auditor-s4-v4-holdout` (from C:/countersign/kickoff) | 0 |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s4-v4/stage-4 && docker build --no-cache -t tablekeeper-stage4 .   → exit 0 (10.9 s); tagged auditor-s4-c4
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.02 200 {"status":"ok"}; host and container egress blocked
  no PORT → :8080 200; PORT=18300 → :18300 200; / /signup /login /lookup → 200 text/html; charset=utf-8, 0 external src/href
  upgrade sources (stage-1/2/3 folders, --no-cache): export state.schema 1, 2, 3; candidate 4
```
### Step 2
```
HTTP (A, B, S1/S2/S3 from the clone's stage folders):  248 passed, 20 skipped (browser tests only)   junit tests=268 failures=0
Browser (--ui):                                          21 passed                                    junit tests=21 failures=0
```
### Step 3
```
OK: 50 runs, 4050 ops, seeds 1..50, 30.3s
OK: 50 runs, 4050 ops, seeds 497914..497963, 28.1s        (fresh seed)
```
### Step 4
```
checks 1254  hard 0  soft 0
  core 94, auth 59, availability 37, create 88, reads 7, cancel 15, patch 59, dst 37, idem 23, moves 57, burst 45, export 96,
  combo 147, comboburst 18, upgrade 18, explain 16, policies 97, history 23, revision 24, series 55, s3moves 10, s3burst 24,
  upgrade3 12, replan 63, limits 4, optimal 12, samend 35, s4burst 18, upgrade4 11, staticupgrade 12, s4rulings 18, r77 20
```
### Step 5
```
seed 43 (fresh): generated 988, evaluated 41, killed 34, survived 4, invalid 3, 1335 s; baseline failing: none
killers: audit.py (24 groups incl. staticupgrade, r77) + Oracle HTTP suite
survivors (4):
  server.go:94        recoverer re-panics only http.ErrAbortHandler (`==`→`!=`): reachable only when a handler panics; no conforming
                      request makes the service panic → equivalent
  snapshot.go:46      schema-1 migration: a reservation with no table_id (`||`→`&&`) → Oracle work item
  snapshot.go:178     import kind check: a numeric field whose first digit is 9 (`<= '9'`→`< '9'`) → Oracle work item
  integrity.go:168    import: accepted_terms capacity out of range / missing table (`return false`→`true`) → Oracle work item
→ 34/38 = 89.5 % raw; 34/37 = 91.9 % excluding the equivalent one
```
### Step 6
```
G1  Store.Write lock                 RED/GREEN      G6  every member occupied            RED/GREEN
G2  signup re-check                  RED/GREEN      G7  anchor already in a series       RED/GREEN
G3  idempotency one section (+5 ms)  RED/GREEN      G8  adoption checks all first        RED/GREEN
G4  batch check-before-apply         RED/GREEN      G9  apply refuses a stale plan       RED/GREEN
G10 apply refuses an applied plan    RED/GREEN
G5  Store.Read lock, race detector + readwrite_load ×10: without 40 DATA RACE (State.AddReservation / InsertReservation), with 0   RED/GREEN
```
### Step 7
```
checks 119  hard 0  soft 0 (u_routes 9, u_auth 11, u_grid 6, u_click_rules 8, u_booking 23, u_conflict 5, u_lost_after 6,
u_lost_before 6, u_out_of_order 4, u_lookup 6, u_states_distinct 2, u_keyboard 5, u_layout 18, u_import 7, u_replan 3)
```
### Step 8
```
building C:\countersign\tmp\aud-s4-v4\stage-4 ...
  stage 1: pass
building upgrade source C:\countersign\tmp\aud-s4-v4\stage-1 ...
  stage 2: pass
building upgrade source C:\countersign\tmp\aud-s4-v4\stage-2 ...
  stage 3: pass
building upgrade source C:\countersign\tmp\aud-s4-v4\stage-3 ...
  stage 4: pass
highest contiguous stage: 4      claimed stage: 4      revision 2621e7a…      mode isolated      state completed
stage 1: 120/120   stage 2: 25/25 (sample 8, ui 17)   stage 3: 7/7   stage 4: 6/6
```

## Findings
None on this SHA. Stage-4 history:
- verdict 1 (06cdcd6): F-1 R-74 → WI-28.
- verdict 2 (e00f6bb): F-2, an Oracle tamper selector defect (fixed in the Oracle); F-3 R-75 → WI-30. R-76 was not reproduced (WI-29 needed only a test).
- verdict 3 (b26757c): F-4 R-77 → WI-31.

## Work items for the Oracle (surviving mutants; not blocking)
- O-19 C1.109 / R-24: import refusals not yet observed:
  - a schema-1 export reservation without `table_id` (snapshot.go:46);
  - an imported accepted_terms capacity of 0, above the maximum, or for a missing table (integrity.go:168).
- O-20 C1.107: a round-trip/import with a numeric field whose value starts with the digit 9 (e.g. party_size 9) imports (snapshot.go:178).

## Counts for the stage report
- mutation: tool mutate.py (containerised textual Go mutants; killers audit.py + Oracle HTTP suite), killed 34, total 38 raw (89.5 %);
  91.9 % excluding 1 equivalent (988 generated, 41 evaluated, 3 invalid, seed 43)
- race proofs: 10 guards — G1 Store.Write lock / s4burst+burst; G2 signup re-check / B8; G3 idempotency single section / B3,Q4; G4 batch
  check-before-apply / moves,s3moves; G5 Store.Read lock / race detector 40 vs 0; G6 member-wide occupancy / combo; G7 anchor already in
  series / R3; G8 adoption all-before-any / series; G9 stale_plan / replan,Q2; G10 plan_already_applied / replan — 10 red-without, 10 green-with
- holdout: ran yes; stage 1 passed 120 failed 0, stage 2 passed 25 failed 0, stage 3 passed 7 failed 0, stage 4 passed 6 failed 0; escapes none
- stage-4 findings across candidates: F-1 (R-74), F-2 (Oracle), F-3 (R-75), F-4 (R-77); all closed

## Result
result=ACCEPT — every battery step is green on 2621e7a from a clean clone:
- build/boot;
- Oracle suites 248 + 21;
- differential, fixed and fresh seeds;
- 1254 attack checks, including R-75/R-76/R-77 and upgrades from images built from the accepted stage folders;
- mutation 89.5 %;
- 10/10 race proofs;
- 119 browser checks;
- the isolated holdout through stage 4.
