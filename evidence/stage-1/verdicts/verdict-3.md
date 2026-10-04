[VERDICT] stage=1 sha=4f983ace6b97652f552deb66aaa87e333a5b7b90 result=REJECT

# Verdict 3 — stage 1

- Candidate: 4f983ace6b97652f552deb66aaa87e333a5b7b90 (main, "[PLAN] stage-1 candidate 3"); supersedes candidate 2 (4f54736, verdict 2)
- Clean clone: C:/countersign/tmp/aud-s1-v3 (`git clone C:/countersign/result`, `git checkout 4f983ac…`)
- Product identity: `git diff --stat 4f54736 4f983ace6b97652f552deb66aaa87e333a5b7b90 -- stage-1 ':!stage-1/verify'` → empty (run in the clean clone)
- Battery code: seat/auditor @ 2a88432 (+ uncommitted-at-run additions in this verdict's commit); Oracle suite/runner from the clone (seat/oracle 3e387ec)
- Contract: `evidence/stage-1/ledger.md` (C1.1–C1.134), `evidence/stage-1/rulings.md` (R-1 … R-25)
- Every step was re-run on this SHA from the clean clone (none carried over from verdict 2).
- Artifacts: `evidence/stage-1/verdicts/verdict-3-artifacts/`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage1 .` (RUN.md); `--internal` network, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | fail (127/128) — Oracle test defect, product correct | `run_oracle.sh auditor-s1-c3 C:/countersign/tmp/aud-s1-v3 …/oracle` | 1 |
| 3 | differential runner (seeds: 1 ×50 runs, 620154 ×5, fresh 97069 ×50; 80 ops) | pass (8505 ops, no mismatch) | `run_diff.sh auditor-s1-c3 C:/countersign/tmp/aud-s1-v3 …/diff <seed> <runs> 80` | 0 |
| 4 | attacks | pass (603 checks, 0 hard, 0 soft; +143 core/moves with 4 new cases) | `run_attacks.sh auditor-s1-c3 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (54/64 = 84.4 %) | pass (target 75 %) | `mutate.py --src …/aud-s1-v3/stage-1 … --budget 1200 --workers 4 --seed 3 --oracle …` | 0 |
| 6 | race proofs (5 guards) | pass (5/5 red-without, 5/5 green-with) | `race.py` (G1–G4), `race_detect.sh … readwrite 5` (G5) | 0 |
| 7 | user-facing checks | skip | no user-facing surface in stage 1 (C1.2) | n/a |
| 8 | holdout | skip | step 2 not green; no ruling recorded | n/a |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s1-v3/stage-1 && docker build -t tablekeeper-stage1 .        → exit 0 (11.9 s), sha256:1ed94151…, tagged auditor-s1-c3
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.05 200 {"status":"ok"}; egress: blocked URLError
  no PORT → :8080/health 200; PORT=18300 → :18300/health 200
```
### Step 2
```
$ bash stage-1/verify/audit/run_oracle.sh auditor-s1-c3 C:/countersign/tmp/aud-s1-v3 C:/countersign/tmp/aud-s1-v3-out/oracle
FAILED ../repo/stage-1/verify/oracle/test_hardening.py::test_C1_107_C1_109_tampered_export_is_refused
  AssertionError: ('duplicate reference', Resp(204, ''))
1 failed, 127 passed in 23.60s
```
Analysis (probe in `verdict-3-artifacts/probe/`): `_tampers` edits the first dict that `_records(state, reference=<second_ref>)`
returns. In this service's export the walk reaches `state.receipts["u_ada\x00POST /reservations\x00<key>"].response` (the stored
original 201 body of the keyed booking) before `state.reservations[i]`, so the "duplicate reference" tamper rewrites a stored
response, not a reservation. The imported state has no duplicate reference among reservations and is structurally valid (R-24),
so 204 is correct; no clause requires import to cross-check stored responses against reservations. With the tamper applied to
every dict carrying the reference (Auditor attack added in this verdict), the service answers 422 `validation_failed` and the
destination is unchanged, on the same server and on a fresh container.
### Step 3
```
$ … run_diff.sh auditor-s1-c3 … 1 50 80        → OK: 50 runs, 4050 ops, seeds 1..50, 24.9s
$ … run_diff.sh auditor-s1-c3 … 620154 5 80    → OK: 5 runs, 405 ops, seeds 620154..620158, 2.9s
$ … run_diff.sh auditor-s1-c3 … 97069 50 80    → OK: 50 runs, 4050 ops, seeds 97069..97118, 26.2s   (fresh seed)
```
### Step 4
```
$ bash stage-1/verify/audit/run_attacks.sh auditor-s1-c3 C:/countersign/seats/auditor …/attacks --rounds 3
checks 603  hard 0  soft 0   (incl. R-25: 10 non-exact paths → 404; tampered export with duplicate reference → 422 + unchanged)
$ … --groups core,moves --rounds 1   (new: fixture timezone "Local" and "" → 422; moves item null → 422)      checks 143  hard 0
```
### Step 5
```
$ python stage-1/verify/audit/mutate.py --src C:/countersign/tmp/aud-s1-v3/stage-1 … --budget 1200 --workers 4 --seed 3 --oracle …
baseline failing (excluded): oracle:test_C1_107_C1_109_tampered_export_is_refused
generated 472, evaluated 70 (1288 s), killed 54, survived 10, invalid 6 → 84.4 %
equivalent (5): localtime.go:68, :262, :272 (slot at closes is removed by the end check), state/reservations.go:18,
  api/reservations.go:139 (references are unique) → 54/59 = 91.5 % excluding equivalents
```
### Step 6
```
G1 Store.Write lock          without: run 3 → B5, B4 wrong (4 checks); with: 0                              RED/GREEN
G2 signup re-check           without: B8 wrong in r0, r1, r2; with: 0                                       RED/GREEN
G3 idempotency one section   split + 5 ms window: without B3 (2×201), B4; with: 0                            RED/GREEN
G4 batch check-before-apply  without: failed batch changed bookings and occupancy; with: 0                  RED/GREEN
G5 Store.Read lock           race detector + readwrite_load (5×60): without 11 DATA RACE reports, with 0      RED/GREEN
```

## Findings
- F-1 (Oracle — acceptance test defect; not a product defect)
  - clause: C1.109 — "Invalid JSON follows §5; missing fields, wrong track/version or an invalid state give 422 `validation_failed` without changing the destination." with R-24 ("any structurally invalid state → 422")
  - request: `POST /_test/import` with the export in which only the first dict carrying the second booking's reference (the stored receipt response) has `reference` set to `SEED01`
  - expected: the test should present a state with two reservations sharing a reference; the state it built is structurally valid, so 204 is correct
  - actual: the test expects 422 and fails on the candidate's (correct) 204
  - reproduce: `bash stage-1/verify/audit/run_oracle.sh auditor-s1-c3 C:/countersign/tmp/aud-s1-v3 <out> -k tampered_export`; probe: `verdict-3-artifacts/probe/probe.py`

No product finding.

## Work items for the Oracle
- O-1 fix F-1: apply the "duplicate reference" (and "duplicate reservation id") tampers to every dict carrying the value, or to the dict that also has `user_id` and `status`.
- O-2 surviving mutants (C1.109 / R-24): reservation record with a zero `starts_at`/`ends_at` (integrity.go:60), a missing `receipts`/`tokens` collection alone (integrity.go:33), a reservation whose id is valid but reference invalid (integrity.go:59).
- Covered by the Auditor battery in this verdict (from survivors): fixture timezone `"Local"` / `""` → 422 (fixture.go:145); moves item `null` → 422 (moves.go:29).

## Counts for the stage report
- mutation: tool mutate.py (containerised, HTTP killers audit.py + Oracle pytest), killed 54, total 64 (84.4 %; 91.5 % excluding 5 equivalent; 472 generated, 70 evaluated, 6 invalid)
- race proofs: G1 Store.Write lock / B4,B5 / yes / yes; G2 signup re-check / B8 / yes / yes; G3 idempotency single section / B3,B4 / yes / yes; G4 batch check-before-apply / moves atomicity / yes / yes; G5 Store.Read lock / race detector / yes / yes
- holdout: ran no, passed 0, failed 0, escapes none (step 2 red on an Oracle test defect)

## Result
result=REJECT — product identical to 4f54736 and correct on every executed step; step 2 is red only because of an Oracle test defect (F-1), which needs a fix or a ruling before the holdout can run.
