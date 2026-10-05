[VERDICT] stage=4 sha=06cdcd69b73e9de4af3f4dd5186ec3970c7de954 result=REJECT

# Verdict 1 — stage 4 (candidate 1)

- Candidate: 06cdcd69b73e9de4af3f4dd5186ec3970c7de954 (main, "[PLAN] stage-4 candidate 1 (with RUN.md)")
- Clean clone: C:/countersign/tmp/aud-s4-v1; `stage-1/`..`stage-3/` untouched
- Upgrade sources: accepted stage-1 image c0f2b7b (`auditor-s1-c5`), stage-2 aa63cd2 (`auditor-s2-c5`), stage-3 c4a828e (`auditor-s3-c2`)
- Battery code: seat/auditor (stage-4/verify/audit); Oracle suites from the clone (stage-4/verify/oracle: 264 HTTP incl. 20 browser-skipped, 21 browser)
- Contract: C4 master ledger plus C1–C3; rulings R-1…R-74 (R-61…R-74 for stage 4)
- Artifacts: `evidence/stage-4/verdicts/verdict-1-artifacts/` (junit, attack/browser JSON, screenshots, race/mutation JSON, differential output)

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage4 .` (RUN.md, in `stage-4/`); `--internal`, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | **fail**: HTTP 243/244 (20 browser-skipped), browser 21/21; the one failure is F-1 | `PREV_IMG=auditor-s1-c5 PREV2_IMG=auditor-s2-c5 PREV3_IMG=auditor-s3-c2 run_oracle.sh auditor-s4-c1 C:/countersign/tmp/aud-s4-v1 …/oracle` | 1 |
| 3 | differential runner (seed 1 ×50; fresh seed 15916 ×50; 80 ops) | pass (8100 ops, no mismatch) | `run_diff.sh auditor-s4-c1 … <seed> 50 80` | 0 |
| 4 | attacks | pass (1140 checks + replan 63 + series 55; 0 product failures) | `PREV_IMG=… PREV2_IMG=… PREV3_IMG=auditor-s3-c2 run_attacks.sh auditor-s4-c1 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation | pass: 28/36 raw = 77.8 % (target 75 %); 28/34 = 82.4 % excluding 2 equivalent mutants | `mutate.py --src …/aud-s4-v1/stage-4 --targets internal/api,internal/state,internal/snapshot,internal/localtime,internal/jsonin,internal/planner --budget 1200 --workers 4 --seed 41 --oracle …`; survivors re-run with `--only-survivors` | 0 |
| 6 | race proofs (10 guards) | pass (10/10 red-without, 10/10 green-with) | `race.py` (G1–G4, G6–G10), `race_detect.sh … readwrite 5` (G5) | 0 |
| 7 | user-facing checks | pass (119 browser checks incl. a replanned restaurant; screenshots) | `PREV_IMG=auditor-s1-c5 run_ui.sh auditor-s4-c1 C:/countersign/seats/auditor …/ui` | 0 |
| 8 | holdout (isolated, --stage 4) | **not run**: step 2 is not green | — | — |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s4-v1/stage-4 && docker build -t tablekeeper-stage4 .     → exit 0; tagged auditor-s4-c1
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.08 200 {"status":"ok"}; egress: blocked URLError
```
### Step 2
```
HTTP (A, B, stage-1/2/3 sources):  243 passed, 1 failed, 20 skipped (browser tests)   junit tests=264 failures=1
  FAILED test_stage3_api::test_C3_28_O14_tampered_stage3_records_are_refused
         ('history first entry not created', Resp(204, ''))  assert 204 == 422
Browser (--ui):                    21 passed                                          junit tests=21 failures=0
```
### Step 3
```
OK: 50 runs, 4050 ops, seeds 1..50, 30.3s
OK: 50 runs, 4050 ops, seeds 15916..15965, 28.1s        (fresh seed)
```
### Step 4
```
full run, 29 groups ×3 burst rounds: checks 1140, product failures 0
  (core 94, auth 59, availability 37, create 88, reads 7, cancel 15, patch 59, dst 37, idem 23, moves 57, burst 45, export 96,
   combo 147, comboburst 18, upgrade 18, explain 16, policies 97, history 23, revision 24, series 53, s3moves 10, s3burst 24,
   upgrade3 12, limits 4, optimal 12, samend 35, s4burst 18, upgrade4 11)
  replan: harness defect (setup booking at 22:00, outside opening hours) — fixed in the battery (38ce3da), group re-run:
$ … --groups replan --rounds 1      checks 63  hard 0   (preview == brute force, apply, stale_plan, plan_already_applied, closures)
$ … --groups series --rounds 1      checks 55  hard 0   (adds count 12 / interval_weeks 4 maximum accepted, for a step-5 survivor)
```
### Step 5
```
run 1 (seed 41): generated 914, evaluated 38, killed 27, survived 9, invalid 2, 1796 s; baseline failing: only the F-1 Oracle test
  (excluded from kill criteria)
survivors re-run against the strengthened battery (same 9 mutants, --only-survivors, 295 s):
  series.go:64 `n > max`→`n >= max` now killed (count 12 / interval_weeks 4 maximum accepted, C3.37)
totals: killed 28, survived 8 → 28/36 = 77.8 % raw
equivalent (2): no observable behaviour can differ
  amend.go:26       cutoff `<=`→`<`: differs only when starts_at − now equals the cutoff to the nanosecond (R-6); a real clock
                    cannot reach it
  localtime.go:216  window skip `c <= o`→`c < o`: closes == opens is refused when a fixture or policy is written, so a stored
                    window never has it
→ 28/34 = 82.4 %
remaining survivors (6):
  integrity.go:34, :40, :105, :176; fixture.go:69 → Oracle work items (below)
  schema3.go:68     mustJSON `err != nil`→`== nil` panics on every schema-1/2 import. The upgrade groups (upgrade3/upgrade4) and
                    Oracle C3.48 kill it, but they need the previous-stage images, which the mutation runner does not start →
                    Auditor harness item: a static schema-2 export fixture so the mutation killers cover the 2→3 migration
```
### Step 6
```
G1  Store.Write lock                 without: s4burst Q3 / burst failures; with: 0                              RED/GREEN
G2  signup re-check                  without: B8 (20 concurrent signups); with: 0                               RED/GREEN
G3  idempotency one section (+5 ms)  without: B3 and s4burst Q4; with: 0                                        RED/GREEN
G4  batch check-before-apply         without: failed batch left changes (moves, s3moves); with: 0               RED/GREEN
G5  Store.Read lock                  race detector + readwrite_load 5×: without 37 DATA RACE, with 0            RED/GREEN
G6  every member occupied            without: overlapping combo bookings; with: 0                               RED/GREEN
G7  anchor already in a series       without: s3burst R3 (10 concurrent adoptions); with: 0                     RED/GREEN
G8  adoption checks all first        without: failed adoption created occurrences; with: 0                      RED/GREEN
G9  apply refuses a stale plan       without: stale apply 201 (replan 7 checks, s4burst Q2 + overlap); with: 0  RED/GREEN
G10 apply refuses an applied plan    without: second key on the same plan 201; with: 0                          RED/GREEN
```
### Step 7
```
checks 119  hard 0  soft 0 (u_routes 9, u_auth 11, u_grid 6, u_click_rules 8, u_booking 23, u_conflict 5, u_lost_after 6,
u_lost_before 6, u_out_of_order 4, u_lookup 6, u_states_distinct 2, u_keyboard 5, u_layout 18, u_import 7, u_replan 3)
u_replan: after an applied closure 18:00–21:30 on a_3, slot cells at 17:00/19:00/21:00 data-available=false, 12:00 true;
          lookup shows the new table labels and not the closed one
```

## Findings
### F-1 — C1.109 / C3.28 / C4.24, ruling R-74
- Clause (R-74): an imported history that is not dense seq 1..n, starting with exactly one `created` entry, with `cancelled` last when
  present, is an invalid state; the import is refused with 422 and the state is unchanged (C1.109).
- Request: `POST /import` of an otherwise valid stage-4 export in which one reservation's history begins with a `changed` entry
  instead of `created` (seq still 1..n).
- Expected: 422 `validation_failed`, state unchanged.
- Actual: 204; the import is accepted.
- Reproduction: `run_oracle.sh auditor-s4-c1 C:/countersign/tmp/aud-s4-v1 …/oracle` →
  `test_stage3_api::test_C3_28_O14_tampered_stage3_records_are_refused`.
- Owner: Builder, as WI-28 (per the Foreman's R-74 ruling).

## Work items for the Oracle (surviving mutants; not blocking)
- O-15 C1.109 / R-24 / R-74 — import refusals not yet observed:
  - a state in which exactly one of restaurants/reservations is null (integrity.go:34);
  - a user record with an invalid id but a non-empty email and hash (integrity.go:40);
  - a restaurant with `tables: null` and valid opening hours (integrity.go:105);
  - a history entry with `changes: null` and a valid event (integrity.go:176).
- O-16 C1.26 / C1.27 — a fixture integer exactly 2^31−1 (e.g. capacity or slot_minutes) is accepted at its upper bound
  (fixture.go:69 boundary).

## Counts for the stage report
- mutation: tool mutate.py (containerised textual Go mutants; killers audit.py + Oracle HTTP suite); killed 28, total 36 raw (77.8 %);
  82.4 % excluding 2 equivalent mutants (914 generated, 38 evaluated, 2 invalid)
- race proofs: 10 guards, 10 red-without, 10 green-with. G1 Store.Write lock / s4burst+burst; G2 signup re-check / B8; G3 idempotency single
  section / B3,Q4; G4 batch check-before-apply / moves,s3moves; G5 Store.Read lock / race detector 37 vs 0; G6 member-wide occupancy / combo;
  G7 anchor already in series / R3; G8 adoption all-before-any / series; G9 stale_plan / replan,Q2; G10 plan_already_applied / replan
- holdout: not run (step 2 not green); escapes: none observed
- findings: 1 (F-1, R-74 → WI-28)

## Result
result=REJECT — F-1 (R-74): an imported history whose first entry is not `created` is accepted (204) instead of refused (422).
Every other step that ran is green: build/boot, differential, 1140 + 63 + 55 attack checks, 10/10 race proofs and 119 browser checks.
The holdout was not run. Candidate 2 (WI-28 only) gets the full battery again, plus the holdout.
