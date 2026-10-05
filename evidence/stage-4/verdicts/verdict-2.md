[VERDICT] stage=4 sha=e00f6bb5f3f7f6343c10083cef014ff189c586ce result=REJECT

# Verdict 2 — stage 4 (candidate 2)

- Candidate: e00f6bb5f3f7f6343c10083cef014ff189c586ce (main, "[PLAN] stage-4 candidate 2"); supersedes candidate 1 (06cdcd6, verdict 1)
- Clean clone: C:/countersign/tmp/aud-s4-v2 (from C:/countersign/result)
- Product delta since 06cdcd6: WI-28 (R-74) — `stage-4/internal/state/integrity.go` (plus Go tests); stage-1..3 folders untouched
- Upgrade sources: accepted stage-1 c0f2b7b (`auditor-s1-c5`), stage-2 aa63cd2 (`auditor-s2-c5`), stage-3 c4a828e (`auditor-s3-c2`)
- Image: `docker build --no-cache -t tablekeeper-stage4 .` → tagged `auditor-s4-c2` (sha256:37d07abc…)
- Battery code: seat/auditor @ 0a0db07 (stage-4/verify/audit); Oracle suites from the clone (264 HTTP incl. 20 browser-skipped, 21 browser)
- Contract: C1–C4 master ledgers; rulings R-1…R-76 (R-75, R-76 ruled during this run, on main at 0dbd307)
- Artifacts: `evidence/stage-4/verdicts/verdict-2-artifacts/`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage4 .` (RUN.md, in `stage-4/`); `--internal`, `--cpus 2 --memory 2g` | 0 |
| 2 | acceptance suites | **fail**: HTTP 243/244 (20 browser-skipped), browser 21/21; the failure is an Oracle test defect (F-2) | `PREV_IMG=auditor-s1-c5 PREV2_IMG=auditor-s2-c5 PREV3_IMG=auditor-s3-c2 run_oracle.sh auditor-s4-c2 C:/countersign/tmp/aud-s4-v2 …/oracle` | 1 |
| 3 | differential runner (seed 1 ×50; fresh seed 636492 ×50; 80 ops) | pass (8100 ops, no mismatch) | `run_diff.sh auditor-s4-c2 … <seed> 50 80` | 0 |
| 4 | attacks | **fail**: 1216 checks green; the new R-75/R-76 group (`s4rulings`) has 16 checks with 3 failures (F-3) | `PREV_IMG=… PREV2_IMG=… PREV3_IMG=… run_attacks.sh auditor-s4-c2 C:/countersign/seats/auditor …/attacks --rounds 3`; `… --groups s4rulings` | 0 / 1 |
| 5 | mutation | pass: 31/38 = 81.6 % raw (target 75 %) | `mutate.py --src …/aud-s4-v2/stage-4 --targets internal/api,internal/state,internal/snapshot,internal/localtime,internal/jsonin,internal/planner --budget 1200 --workers 4 --seed 42 --oracle …` | 0 |
| 6 | race proofs (10 guards) | pass (10/10 red-without, 10/10 green-with) | `race.py` (G1–G4, G6–G10, same edits as verdict 1), `race_detect.sh … readwrite 10` (G5) | 0 |
| 7 | user-facing checks | pass (119 browser checks incl. a replanned restaurant) | `PREV_IMG=auditor-s1-c5 run_ui.sh auditor-s4-c2 C:/countersign/seats/auditor …/ui` | 0 |
| 8 | holdout (isolated, --stage 4) | **not run**: steps 2 and 4 are not green | — | — |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s4-v2/stage-4 && docker build --no-cache -t tablekeeper-stage4 .   → exit 0 (11.0 s); tagged auditor-s4-c2
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.06 200 {"status":"ok"}; host and container egress blocked
  no PORT → :8080 200; PORT=18300 → :18300 200; / /signup /login /lookup → 200 text/html; charset=utf-8, 0 external src/href
```
### Step 2
```
HTTP (A, B, S1/S2/S3):  243 passed, 1 failed, 20 skipped     junit tests=264 failures=1
  FAILED test_stage3_api::test_C3_28_O14_tampered_stage3_records_are_refused
    test_stage3_api.py:849 in _stage3_tampers ("series occurrence unknown reservation"): IndexError: list index out of range
Browser (--ui):         21 passed                            junit tests=21 failures=0
Scratch copy outside the repo (C:/countersign/tmp/aud-s4-v2-ofix) with only that selector widened to `reference`:
  … run_oracle.sh auditor-s4-c2 C:/countersign/tmp/aud-s4-v2-ofix …/oracle-ofix -k O14   → tests=1 failures=0
```
### Step 3
```
OK: 50 runs, 4050 ops, seeds 1..50, 29.8s
OK: 50 runs, 4050 ops, seeds 636492..636541, 28.9s        (fresh seed)
```
### Step 4
```
full run (30 groups, bursts ×3): checks 1216  hard 0  soft 0
  core 94, auth 59, availability 37, create 88, reads 7, cancel 15, patch 59, dst 37, idem 23, moves 57, burst 45, export 96,
  combo 147, comboburst 18, upgrade 18, explain 16, policies 97, history 23, revision 24, series 55, s3moves 10, s3burst 24,
  upgrade3 12, replan 63, limits 4, optimal 12, samend 35, s4burst 18, upgrade4 11, staticupgrade 12
$ … --groups s4rulings (R-75, R-76; written during this run)       checks 16  hard 3
  FAIL R-75 duration 2^31-1: booking → 201 with ends_at 2018-01-05T00:05:04+01:00 (start 2026-10-29T19:00); expected 422 outside_opening_hours
  FAIL R-75 duration 2^31-1: availability still offers slots
  FAIL R-75 cutoff 2^31-1: cancel of a future booking → 200 cancelled; expected 409 cutoff_passed
  pass R-75 capacity 2^31-1 seats a party of 2^31-1; 2^31 for duration/cutoff/slot/capacity → reset 422
  pass R-76 stage 1 and stage 2: API-confirmed, API-cancelled and every seeded booking (10 + 3, incl. a seeded cancelled one)
       migrate with revision 1 and exactly one `created` entry
```
### Step 5
```
seed 42 (fresh): generated 981, evaluated 39, killed 31, survived 7, invalid 1, 1276 s
baseline failing: only the F-2 Oracle test (excluded from kill criteria). Its post-loop "relayed survivors" assertions therefore
could not kill anything in this run.
killers: audit.py (23 groups incl. staticupgrade) + Oracle HTTP suite
survivors (7):
  reservations.go:144   sort `a.Reference < b.Reference` → `<=`: references are unique, so this is equivalent
  integrity.go:105      import: restaurant with `tables: null` (O-15, still open)
  integrity.go:156      import: accepted_terms duration exactly 1440 vs 1441 (in the masked part of O-14)
  integrity.go:157      import: accepted_terms cutoff above 10080 (`||`→`&&`)
  integrity.go:221      import: closures out of order / closure with a null bound
  integrity.go:246      import: series of an unknown user with a valid revision (`||`→`&&`)
  policyparse.go:79     policy capacities: a number that is not an integer (`err != nil || !ok` → `&&`)
→ 31/38 = 81.6 % raw; 31/37 = 83.8 % excluding the equivalent one
```
### Step 6
```
G1  Store.Write lock                 RED/GREEN      G6  every member occupied            RED/GREEN
G2  signup re-check                  RED/GREEN      G7  anchor already in a series       RED/GREEN
G3  idempotency one section (+5 ms)  RED/GREEN      G8  adoption checks all first        RED/GREEN
G4  batch check-before-apply         RED/GREEN      G9  apply refuses a stale plan       RED/GREEN
G10 apply refuses an applied plan    RED/GREEN
G5  Store.Read lock, race detector + readwrite_load:
    5 rounds (as verdict 1): without 0, with 0 — inconclusive (detector observes only interleavings that happen)
    10 rounds:               without 7 DATA RACE (State.AddReservation / InsertReservation), with 0       RED/GREEN
```
### Step 7
```
checks 119  hard 0  soft 0 (u_routes 9, u_auth 11, u_grid 6, u_click_rules 8, u_booking 23, u_conflict 5, u_lost_after 6,
u_lost_before 6, u_out_of_order 4, u_lookup 6, u_states_distinct 2, u_keyboard 5, u_layout 18, u_import 7, u_replan 3)
```

## Findings
### F-1 (verdict 1) — closed
R-74 history shapes on import: `test_C3_28_O14…` now passes every history tamper (it stops later, at F-2); the Auditor's import checks
pass.

### F-2 — Oracle test defect (C1.109 / C3.28 / R-74); owner: Oracle
- `stage-4/verify/oracle/test_stage3_api.py:848–849`: the tamper "series occurrence unknown reservation" picks the field to corrupt with
  `[k for k in occ if "reservation" in k and isinstance(occ[k], str)][0]`; series occurrences carry `reference` and `exception`, so
  the test's own setup raises IndexError. It was never reached before WI-28 (O-14 landed after stage 3 was accepted).
- With the selector widened in a scratch copy, the test passes 1/1 on this image. Not a product finding.

### F-3 — C1.22 / C1.23 / C1.74, ruling R-75; owner: Builder (WI-30)
- Clause (R-75): fixture integers up to 2^31−1 are computed exactly; a 2^31−1-minute duration ends 2^31−1 real minutes after the
  start, so no slot fits within opening hours (booking 422 `outside_opening_hours`); a 2^31−1-minute cutoff makes every future booking
  non-cancellable (409 `cutoff_passed`).
- Request 1: reset a restaurant with `reservation_duration_minutes: 2147483647`; `POST /reservations` for a future 19:00 slot.
  Expected 422 `outside_opening_hours`. Actual 201, `ends_at` 2018-01-05T00:05:04+01:00 for a 2026-10-29T19:00 start;
  `GET /availability` still offers slots.
- Request 2: reset with `cancellation_cutoff_minutes: 2147483647`; book a future slot; `POST /reservations/{ref}/cancel`.
  Expected 409 `cutoff_passed`. Actual 200 `cancelled`.
- Reproduction: `PREV_IMG=auditor-s1-c5 PREV2_IMG=auditor-s2-c5 run_attacks.sh auditor-s4-c2 C:/countersign/seats/auditor <out> --groups s4rulings --rounds 1`

### R-76 (WI-29) — not reproduced on this SHA
The Auditor's independent check (stage-1 and stage-2 sources: confirmed and cancelled through the API, plus every seeded booking
including a seeded cancelled one) finds revision 1 and exactly one `created` entry on e00f6bb. The Oracle's report came from a
pre-WI-28 image; its regression test decides on candidate 3. The `s4rulings` group keeps checking it.

## Work items
- Oracle: fix F-2 (selector independent of export field names).
- Oracle (surviving mutants, not blocking): O-17 C1.109 / C4 closures: refuse imported closures that are out of order or have a null
  `from`/`to` (integrity.go:221); a series whose user is unknown (integrity.go:246); accepted_terms cutoff 10081 (integrity.go:157).
  O-18 C3.20 / R-47: a policy capacity given as a non-integer number such as 4.5 → 422 (policyparse.go:79). O-15 (tables: null) still
  open. integrity.go:156 should die once F-2 unmasks the O-14 boundary assertions.
- Auditor (done in this run): `s4rulings` (R-75/R-76) and `staticupgrade` (stored stage-1/2/3 exports) groups; G5 now runs 10 rounds.
- Note for the record: the Auditor's upgrade3 check accepted "at least one history entry" for migrated bookings, which was a lenient
  reading of R-55. `s4rulings` now requires exactly one `created` entry.

## Counts for the stage report
- mutation: tool mutate.py (containerised textual Go mutants; killers audit.py + Oracle HTTP suite); killed 31, total 38 raw (81.6 %);
  83.8 % excluding 1 equivalent (981 generated, 39 evaluated, 1 invalid, seed 42)
- race proofs: 10 guards, 10 red-without, 10 green-with (G5 at 10 rounds: 7 vs 0; the 5-round run was 0 vs 0, inconclusive)
- holdout: not run (steps 2 and 4 not green); escapes: none observed
- findings: F-1 closed (R-74); F-2 Oracle test defect; F-3 R-75 → WI-30; R-76 not reproduced on e00f6bb

## Result
result=REJECT — F-3 (R-75): fixture durations and cutoffs near 2^31−1 minutes overflow (an end time nine years before the start; a
cancel accepted that the cutoff forbids). Step 2 also fails on an Oracle test defect (F-2), which is not product behaviour.
F-1 (R-74) is fixed. Build/boot, differential, 1216 attack checks, 10/10 race proofs and 119 browser checks are green. The holdout
was not run. Candidate 3 (WI-29, WI-30, Oracle fixes) gets the full battery, plus the holdout.
