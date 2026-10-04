[VERDICT] stage=2 sha=b4e805fceb930ae3bc84c29825c8f31590922216 result=REJECT

# Verdict 1 — stage 2 (candidate 1)

- Candidate: b4e805fceb930ae3bc84c29825c8f31590922216 (main, "[PLAN] stage-2 candidate 1")
- Clean clone: C:/countersign/tmp/aud-s2-v1; `stage-1/` identical to c0f2b7b (`git diff --stat c0f2b7b HEAD -- stage-1 ':!stage-1/verify'` empty)
- Upgrade source: accepted stage-1 image built from `stage-1/` at c0f2b7b (`auditor-s1-c5`, sha256:f65ecbd0…)
- Battery code: seat/auditor (stage-2/verify/audit); Oracle suites from the clone (seat/oracle d565258: 189 HTTP tests + 20 browser tests)
- Contract: `evidence/stage-2/ledger.md` C2.1–C2.59 and `evidence/stage-1/ledger.md` C1.1–C1.134; rulings R-1…R-28 (R-26 withdrawn), R-29…R-42 (R-31→R-40, R-33→R-39)
- Artifacts: `evidence/stage-2/verdicts/verdict-1-artifacts/` (junit, attack and browser JSON, race/mutation JSON, differential output, 20 screenshots)

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage1 .` (RUN.md, in `stage-2/`); `--internal`, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | fail — HTTP 188/189 (1 product finding), browser 18/20 (2 Oracle test defects under R-41) | `PREV_IMG=auditor-s1-c5 run_oracle.sh auditor-s2-c1 C:/countersign/tmp/aud-s2-v1 …/oracle` | 1 |
| 3 | differential runner (seeds 1 ×50, fresh 905543 ×50; 80 ops) | fail — model defect (R-35 order), service correct | `run_diff.sh auditor-s2-c1 … <seed> 50 80` | 1 |
| 4 | attacks | pass (760 checks incl. combo, comboburst ×3, upgrade from stage 1) | `PREV_IMG=auditor-s1-c5 run_attacks.sh auditor-s2-c1 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (49/50 = 98 %) | pass (target 75 %) | `mutate.py --src …/aud-s2-v1/stage-2 --targets internal/state,internal/localtime,internal/api,internal/snapshot,internal/jsonin --budget 1200 --workers 4 --seed 21 --oracle …` | 0 |
| 6 | race proofs (6 guards) | pass (6/6 red-without, 6/6 green-with) | `race.py` (G1–G4, G6), `race_detect.sh … readwrite 5` (G5) | 0 |
| 7 | user-facing checks | pass (116 browser checks; 20 screenshots) | `PREV_IMG=auditor-s1-c5 run_ui.sh auditor-s2-c1 C:/countersign/seats/auditor …/ui` | 0 |
| 8 | holdout | skip | steps 2 and 3 not green | n/a |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s2-v1/stage-2 && docker build -t tablekeeper-stage1 .     → exit 0 (11.8 s); tagged auditor-s2-c1
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.09 200 {"status":"ok"}; egress: blocked URLError
  no PORT → :8080 200; PORT=18300 → :18300 200
  / /signup /login /lookup → 200 text/html; charset=utf-8, no external src/href (assets embedded; C2.4, C1.9)
observation: stage-2/RUN.md still reads "Tablekeeper — stage 1" and tags the image `tablekeeper-stage1`; the command builds and
  starts the stage-2 service correctly (C1.6 satisfied), but the title/tag should say stage 2.
```
### Step 2
```
HTTP  (auditor-runner-py; A, B, stage-1 S1):  tests=189 failures=1 skipped=19 (the browser tests, run separately)
  FAILED test_stage2_api.py::test_C2_53_C2_47_validation_order
    POST table_ids [""] → expected 422 validation_failed, got 404 not_found ("no such table in this restaurant")
Browser (auditor-ui-runner, --ui):  tests=20 failures=2
  FAILED test_ui.py::test_C2_3_C2_10_C2_15_screens_expose_testids_and_nav   page.goto("/lookup") → wait_for_url("**/login") timeout
  FAILED test_ui.py::test_C2_30_booking_requires_sign_in                     same expectation after logout
  → under R-41 (/lookup is reachable signed out; only submitting navigates to /login) these two expectations are Oracle defects.
```
### Step 3
```
$ … run_diff.sh auditor-s2-c1 … 1 50 80   and   … 905543 50 80     → exit 1 (both)
  0 reset base; 1 reset other; 2 signup s6; 3 create {restaurant_id: "r_trio" (unknown in fixture "other"), table_ids: [q_1,q_2,q_3], party 6}
  mismatch at op 3: model 404 not_found, service 422 combination_not_allowed
  R-35: "more than two ids → 422 combination_not_allowed; then 404 (restaurant, any table …)" → the service is right, the model is wrong.
```
### Step 4
```
checks 760  hard 0  soft 0   (stage-1 groups unchanged; combo; comboburst ×3: K1 1×201/47×409, K2 2×201, K3 one move, K4 one PATCH;
  upgrade: stage-1 export → candidate: tokens, identical reservations + table_ids, lookups, original receipts, lost-response retry,
  failed key reusable, login, no pairs on upgraded restaurants, idempotent re-import, current-format re-export)
$ … --groups combo (with the R-42 attacks added in this verdict)   → checks 122, hard 13 — all 13 are F-1 below
```
### Step 5
```
containerised; killers audit.py (12 groups) + Oracle HTTP suite (189); a kill must reproduce; baseline failing (excluded): the F-1 Oracle test
generated 561, evaluated 53 (1278 s), killed 49, survived 1, invalid 3 → 98.0 %
survivor: localtime.go:119 `if !start.IsZero()` inverted — equivalent (redundant zone-bound probe; seen in stage 1)
```
### Step 6
```
G1 Store.Write lock          burst+comboburst ×3: without → B3 wrong then crash (concurrent map writes); with → 0        RED/GREEN
G2 signup re-check           burst ×3: without → B8 wrong r0–r2; with → 0                                                 RED/GREEN
G3 idempotency one section   split + 5 ms window: without → B3 (2×201), B4; with → 0                                       RED/GREEN
G4 batch check-before-apply  moves+combo: without → overlapping bookings, failed table_ids batch changed bookings; with → 0 RED/GREEN
G5 Store.Read lock           race detector + readwrite_load 5×60: without 17 DATA RACE, with 0                             RED/GREEN
G6 every member occupied     TableBusy edited to compare only first members: without → "single on the other member -> 409"
   (state/reservations.go)   fails and overlapping confirmed bookings appear (C2.38, C2.51); with → 0                     RED/GREEN
```
### Step 7
```
$ PREV_IMG=auditor-s1-c5 bash stage-2/verify/audit/run_ui.sh auditor-s2-c1 C:/countersign/seats/auditor C:/countersign/tmp/aud-s2-v1-out/ui
checks 116  hard 0  soft 0
u_routes 9, u_auth 11, u_grid 6 (R-39 cells), u_click_rules 8 (R-40/R-41), u_booking 23 (R-32 ISO date + HH:MM, R-38 same key and
byte-identical body, new key on change), u_conflict 5, u_lost_after 6, u_lost_before 6, u_out_of_order 4, u_lookup 6, u_states_distinct 2,
u_keyboard 5, u_layout 18 (no horizontal scroll at 375/768/1280 on every route, with grid and form), u_import 7 (stage-1 upgrade +
export/import between requests: still signed in, pending retry keeps key and body, original confirmation)
screenshots: verdict-1-artifacts/ui/*.png (available/unavailable grid, selected, loading, no-slots, success, refused-409, uncertain ×2,
lookup confirmed/cancelled/error, auth-error, 375/768/1280 layouts)
```

## Findings
- F-1 (product — WI-13, rework round 1)
  - clause: R-42 — "an empty or over-64-character restaurant_id, table_id or table_ids member in POST, PATCH or a move item is 422
    validation_failed in the value pass, before any 404 … An empty moves reference is 422"; C1.41 ("A field of the correct JSON type
    with an invalid format or out-of-range value gives 422 `validation_failed`"); C2.46, C2.54, C2.58
  - request: POST /reservations with `restaurant_id: ""`, `table_id: ""`, `table_ids: [""]`, `table_ids: ["", "c_1"]`, a 65-character
    restaurant_id / table_id / table_ids member; PATCH with `table_id: ""`, `table_ids: ["c_2", ""]`, a 65-character table_id; a move
    item with `table_id: ""`, `table_ids: [""]`, `reference: ""`
  - expected: 422 `validation_failed`
  - actual: 404 `not_found` in all 13 cases
  - reproduce: `bash stage-2/verify/audit/run_attacks.sh auditor-s2-c1 C:/countersign/seats/auditor <out> --groups combo --rounds 1`
    (checks "R-42 …"); Oracle `test_stage2_api.py::test_C2_53_C2_47_validation_order`
- F-2 (Oracle — test defect under R-41): `test_ui.py::test_C2_3_C2_10_C2_15_screens_expose_testids_and_nav` and
  `test_C2_30_booking_requires_sign_in` expect opening /lookup signed out to redirect; R-41 makes only submitting redirect.
- F-3 (Oracle — model defect): the differential model answers 404 before "more than two ids → 422 combination_not_allowed"
  (R-35 order); seeds 1 and 905543.

## Counts for the stage report
- mutation: tool mutate.py (containerised textual Go mutants; killers audit.py + Oracle HTTP suite), killed 49, total 50 (98.0 %; 561 generated, 53 evaluated, 3 invalid)
- race proofs: G1 Store.Write lock / bursts + crash / yes / yes; G2 signup re-check / B8 / yes / yes; G3 idempotency single section / B3,B4 / yes / yes;
  G4 batch check-before-apply / moves+combo / yes / yes; G5 Store.Read lock / race detector / yes / yes; G6 member-wide occupancy / combo / yes / yes
- holdout: ran no, passed 0, failed 0, escapes none (steps 2–3 not green)

## Result
result=REJECT — one product finding (F-1, R-42 id validation → WI-13) plus two Oracle defects (F-2, F-3) keep steps 2 and 3 red;
every other step, including 116 browser checks and the stage-1 upgrade, is green.
