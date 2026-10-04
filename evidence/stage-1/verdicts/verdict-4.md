[VERDICT] stage=1 sha=1bfe6e25eab8cfd5f189437d289b4b997213bb61 result=REJECT

# Verdict 4 — stage 1

- Candidate: 1bfe6e25eab8cfd5f189437d289b4b997213bb61 (main, "[PLAN] stage-1 candidate 4"); supersedes candidate 3 (4f983ac, verdict 3)
- Clean clone: C:/countersign/tmp/aud-s1-v4
- Product identity (in the clean clone): `git diff --stat 4f54736 1bfe6e2 -- stage-1 ':!stage-1/verify'` → empty; same against 4f983ac
- Battery code: seat/auditor @ 5995802; Oracle suite/runner from the clone (seat/oracle dc4fd3a, 135 tests)
- Contract: ledger C1.1–C1.134; rulings R-1 … R-25
- Every step re-run on this SHA from the clean clone. Artifacts: `evidence/stage-1/verdicts/verdict-4-artifacts/`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage1 .` (RUN.md); `--internal`, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | fail (134/135; plus 1 flaky) — two Oracle test defects, product correct | `run_oracle.sh auditor-s1-c4 C:/countersign/tmp/aud-s1-v4 …/oracle` | 1 |
| 3 | differential runner (seeds 1 ×50, 620154 ×5, fresh 98150 ×50; 80 ops) | pass (8505 ops) | `run_diff.sh auditor-s1-c4 … <seed> <runs> 80` | 0 |
| 4 | attacks | pass (609 checks, 0 hard, 0 soft) | `run_attacks.sh auditor-s1-c4 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (50/61 = 82.0 %) | pass (target 75 %) | `mutate.py --src …/aud-s1-v4/stage-1 … --budget 1200 --workers 4 --seed 4 --oracle …` | 0 |
| 6 | race proofs (5 guards) | pass (5/5 red-without, 5/5 green-with) | `race.py` (G1–G4), `race_detect.sh … readwrite 5` (G5) | 0 |
| 7 | user-facing checks | skip | no user-facing surface (C1.2) | n/a |
| 8 | holdout | skip | step 2 not green; no ruling recorded | n/a |

## Outputs
### Step 1
```
docker build → exit 0 (40.9 s), sha256:745e44b5…, tagged auditor-s1-c4
internal network, 2 CPU / 2 GiB: healthy_after_s=0.08 200 {"status":"ok"}; egress blocked; no PORT → :8080 200; PORT=18300 → 200
```
### Step 2
```
$ bash stage-1/verify/audit/run_oracle.sh auditor-s1-c4 C:/countersign/tmp/aud-s1-v4 C:/countersign/tmp/aud-s1-v4-out/oracle
FAILED test_hardening.py::test_C1_107_C1_109_tampered_export_is_refused
  AssertionError: ("invalid reservation (reference='bad ref')", Resp(204, ''))
1 failed, 134 passed in 33.82s
$ … run_oracle.sh … -k tie_order   ×5  →  failures: 0, 1, 1, 1, 1
  test_C1_89_tie_order_live_created_at:  >  assert lst.index(a) < lst.index(b)   AssertionError: assert 4 < 0
```
Analysis
- (a) `reference='bad ref'` tamper. The service treats stored references as opaque ids (non-empty, ≤ 64, unique) on reset
  and on import alike. Probe (`verdict-4-artifacts/probe/`): a fixture whose seeded reservation has reference `bad ref`,
  `abc123`, `SEED1`, 13×`A` or `SEED-01` → reset 204, unchanged export → import 204, GET by that reference 200. A state holding
  `bad ref` is therefore one this service produces itself; refusing it on import would break C1.107. Whether C1.81's format
  binds seeded/imported references is open → escalated (ESCALATION 0e7ae76e), recommendation: no (R-16 "seeded data is trusted").
- (b) `test_C1_89_tie_order_live_created_at` is nondeterministic: its last loop checks every consecutive pair of created
  bookings with inverted references, including the pair across the two groups (FUT_DAY 15:00 vs FUT_FRI 19:00), whose order is
  decided by `starts_at` (C1.89), not `created_at`. It fails whenever the random references invert at that boundary (4 of 5
  runs). Its R-11 assertions (Berlin pair and noon triple in created_at order, groups by starts_at descending) pass every run.
### Step 3
```
OK: 50 runs, 4050 ops, seeds 1..50, 31.3s
OK: 5 runs, 405 ops, seeds 620154..620158, 3.2s
OK: 50 runs, 4050 ops, seeds 98150..98199, 34.6s          (fresh seed)
```
### Step 4
```
checks 609  hard 0  soft 0  errors []   (12 groups, bursts ×3, R-25 paths, tampered-export duplicate reference → 422, export/import ×2)
```
### Step 5
```
generated 472, evaluated 67 (1292 s), killed 50, survived 11, invalid 6 → 82.0 %
baseline failing (excluded): the two step-2 tests above
```
### Step 6
```
G1 Store.Write lock          without: B6 (two PATCHes won), overlapping confirmed bookings (C1.3) — 6 checks; with: 0    RED/GREEN
G2 signup re-check           without: B8 wrong r0–r2; with: 0                                                          RED/GREEN
G3 idempotency one section   split + 5 ms: B3, B4 (12 checks); with: 0                                                  RED/GREEN
G4 batch check-before-apply  without: failed batch left changes (4 checks); with: 0                                     RED/GREEN
G5 Store.Read lock           race detector + readwrite_load 5×60: without 1 DATA RACE report, with 0                    RED/GREEN
```

## Findings
- F-1 (Oracle — test expectation pending ruling)
  - clause: C1.81 — "`reference` is 6 to 12 characters of `A-Z0-9`, unique across all reservations, and never changes."; C1.107 — "It must accept an unchanged export produced by this service."; R-16
  - request: `POST /_test/import` of an export whose reservation has `reference: "bad ref"`
  - expected: per ruling (recommended: accept, as reset does — references in fixtures/state are opaque ids)
  - actual: 204; reset with the same seeded reference also 204
  - reproduce: `python probe-ref.py http://<candidate>:8080` (artifacts/probe)
- F-2 (Oracle — nondeterministic test)
  - clause: C1.89 / R-11 — "`starts_at` descending; ties by `created_at` ascending, then `reference` ascending"
  - request: `test_C1_89_tie_order_live_created_at`, final inversion loop
  - expected: the inversion check limited to pairs with equal `starts_at`
  - actual: checks a cross-group pair; fails 4/5 runs on a correct order
  - reproduce: `bash stage-1/verify/audit/run_oracle.sh auditor-s1-c4 C:/countersign/tmp/aud-s1-v4 <out> -k tie_order` (repeat)

No product finding.

## Counts for the stage report
- mutation: tool mutate.py (containerised, HTTP killers audit.py + Oracle pytest), killed 50, total 61 (82.0 %; 472 generated, 67 evaluated, 6 invalid)
- race proofs: G1 / C1.3 overlap + B6 / yes / yes; G2 / B8 / yes / yes; G3 / B3,B4 / yes / yes; G4 / moves atomicity / yes / yes; G5 / race detector / yes / yes
- holdout: ran no, passed 0, failed 0, escapes none (step 2 red on Oracle test defects)

## Result
result=REJECT — product identical to 4f54736/4f983ac and correct on every executed step; step 2 is red only on two Oracle test
defects (F-1 pending a ruling on reference format, F-2 nondeterministic). If both are recorded as test defects by ruling, the
remaining gate is the holdout on this same SHA.
