[VERDICT] stage=1 sha=4f5473646302fa52183643b30552bfb8b59f475e result=REJECT

# Verdict 2 — stage 1

- Candidate: 4f5473646302fa52183643b30552bfb8b59f475e (main, "Merge branch 'seat/auditor'"); supersedes candidate 1 (bd108ef, verdict 1 closed)
- Clean clone: C:/countersign/tmp/aud-s1-v2 (`git clone C:/countersign/result`, `git checkout 4f54736…`)
- Product delta since bd108ef: `internal/api/idempotency.go` (+2: receipts stored compact), tests only otherwise
- Battery code: seat/auditor @ 8a72014 (`stage-1/verify/audit/`); Oracle suite and differential runner from the clone (ruling-aligned, seat/oracle 3f407bf)
- Contract: `evidence/stage-1/ledger.md` (C1.1–C1.134), `evidence/stage-1/rulings.md` (R-1 … R-24)
- Artifacts: `evidence/stage-1/verdicts/verdict-2-artifacts/`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage1 .` (RUN.md); `--internal` network, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | pass (123/123) | `run_oracle.sh auditor-s1-c2 C:/countersign/tmp/aud-s1-v2 …/oracle` | 0 |
| 3 | differential runner (seeds: 1, 620154 fresh, 2–7) | fail — runner/model mismatches, none attributable to the product | `run_diff.sh auditor-s1-c2 C:/countersign/tmp/aud-s1-v2 …/diff <seed> 50 80` | 1 |
| 4 | attacks | pass (583 checks, 0 hard, 0 soft; +72 core with 3 new fixture cases) | `run_attacks.sh auditor-s1-c2 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (51/65 = 78.5 %) | pass (target 75 %) | `mutate.py --src …/aud-s1-v2/stage-1 --targets internal/state,internal/localtime,internal/api,internal/snapshot,internal/jsonin --budget 1200 --workers 4 --seed 2 --oracle …` | 0 |
| 6 | race proofs (5 guards) | pass (5/5 red-without, 5/5 green-with) | `race.py` (G1–G4), `race_detect.sh … readwrite 5` (G5) | 0 |
| 7 | user-facing checks | skip | no user-facing surface in stage 1 (C1.2) | n/a |
| 8 | holdout | skip | step 3 not green | n/a |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s1-v2/stage-1 && docker build -t tablekeeper-stage1 .      → exit 0 (16.5 s), image sha256:3424d1be…, tagged auditor-s1-c2
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.06 200 {"status":"ok"}; egress: blocked URLError
  no PORT → :8080/health 200; PORT=18300 → :18300/health 200; cpus=2000000000 mem=2147483648
```
### Step 2
```
$ bash stage-1/verify/audit/run_oracle.sh auditor-s1-c2 C:/countersign/tmp/aud-s1-v2 C:/countersign/tmp/aud-s1-v2-out/oracle
123 passed in 15.43s          junit: failures="0" errors="0"
```
### Step 3
```
$ bash stage-1/verify/audit/run_diff.sh auditor-s1-c2 C:/countersign/tmp/aud-s1-v2 …/diff 1 50 80          → exit 1
  0 reset; 1 login l1; 2 export e1; 3 create b35; 4 import e1; 5 create b54 (r_ny, 2020-06-15T21:30); 6 moves [b35, …]
  mismatch at op 6: model 409 cutoff_passed, service 404 not_found
$ … run_diff.sh … 620154 50 80   (fresh seed)                                                          → exit 1
  … create b14 (s2); export e2; reset; signup s3; create b36; import e2; 8 get(s2, ref b36)
  mismatch at op 8: model 200 (body of b14), service 404 not_found
$ … seeds 2, 3 (shrunk): reset; get(reference "") → GET /reservations/      model 200 list / 401, service 404 not_found
$ … seeds 4–7 (shrunk): reset; patch(reference "") → PATCH /reservations/  model 405 method_not_allowed, service 404 not_found
```
Classification (all mismatches):
- A — reference aliasing (seeds 1, 620154). `model.py _new_reference` derives references from `counters['reservation']`;
  reset/import restore the counters, so the model re-issues a reference an earlier, discarded booking had, and the
  runner's label for that booking now resolves to a different booking in the model. The service issues random
  references, so the discarded booking's reference is unknown: 404 is what R-22 (c) and C1.90 require. C1.81 requires
  uniqueness across the reservations in state, which the service meets.
- B — empty reference (seeds 2–7). The shrinker produces `reference: ""` → path `/reservations/`. The model drops empty
  path segments (`parts = [p for p in path.split("/") if p != ""]`) and serves it as `/reservations`; the service
  answers a non-canonical path with 404 `not_found` (`server.go cleanPaths`). The specification does not define
  trailing-slash paths; R-9: "unknown path → 404 not_found". Ruling requested (ESCALATION 27694135).
### Step 4
```
$ bash stage-1/verify/audit/run_attacks.sh auditor-s1-c2 C:/countersign/seats/auditor …/attacks --rounds 3
checks 583  hard 0  soft 0  (core 66, auth 59, availability 37, create 88, reads 7, cancel 15, patch 59, dst 37, idem 23, moves 55, burst 45, export 92)
health A 0.02 s, B 0.00 s; bursts ×3 (B1–B10) as specified; export/import same server and fresh container; upgrade: n/a at stage 1
$ … run_attacks.sh … --groups core --rounds 1   (with new fixture cases from surviving mutants: closes == opens, capacity 0, slot_minutes 0)
checks 72  hard 0
```
### Step 5
```
$ python stage-1/verify/audit/mutate.py --src C:/countersign/tmp/aud-s1-v2/stage-1 … --budget 1200 --workers 4 --seed 2 --oracle …
  containerised: Linux build per mutant, server + audit.py (all groups but burst) + Oracle pytest in one --network none
  container, 2 CPU / 2 GiB; kill must reproduce on a second run; baseline 0 failing checks
generated 472, evaluated 71 (1219 s), killed 51, survived 14, invalid 6 → 78.5 %
equivalent (5): state/reservations.go:18 (random-id collision branch), localtime.go:68 (date pre-validated), localtime.go:216
  (closes == opens excluded by fixture validation), localtime.go:288 (stable sort of distinct keys), server.go:77 (abort-panic path)
  → 51/60 = 85.0 % excluding equivalents
```
### Step 6
```
G1 Store.Write lock         race.py --groups burst --rounds 3: without → 8 failing checks, server crashed (concurrent map writes); with → 0      RED/GREEN
G2 signup re-check          without → B8 wrong in r0, r1, r2; with → 0                                                                        RED/GREEN
G3 idempotency one section  split lookup/execute + 5 ms window: without → B3 (2×201), B4 (two bookings); with → 0                            RED/GREEN
G4 batch check-before-apply edit apply(changes[:1]) before the check: without → failed batch changed bookings and occupancy; with → 0         RED/GREEN
G5 Store.Read lock          race_detect.sh … readwrite 5 (go build -race, readwrite_load.py 5×60 mixed requests): without 2 DATA RACE
                            reports (StoreReceipt/keyedWrite vs readers), with 0                                                             RED/GREEN
```

## Findings
- F-1 (Oracle — differential runner; class A)
  - clause: C1.81 — "`reference` is 6 to 12 characters of `A-Z0-9`, unique across all reservations, and never changes." and R-22 (c) / C1.90 (unknown reference → 404)
  - request: seed 1, op 6 `POST /reservation-moves` whose first item is the reference of a booking discarded by `POST /_test/import`; seed 620154, op 8 `GET /reservations/{reference of a discarded booking}`
  - expected: the runner compares like with like; the service's 404 `not_found` for a reference no longer in state is correct
  - actual: the model re-issues the discarded reference to a new booking (counter-derived references restored by reset/import), so it answers 409 `cutoff_passed` / 200
  - reproduce: `bash stage-1/verify/audit/run_diff.sh auditor-s1-c2 C:/countersign/tmp/aud-s1-v2 C:/countersign/tmp/aud-s1-v2-out/diff 1 50 80` (and seed 620154)
- F-2 (Oracle — differential runner; class B; ruling requested)
  - clause: R-9 — "unknown path → 404 `not_found`; known path, unsupported method → 405 `method_not_allowed`"
  - request: `GET /reservations/` and `PATCH /reservations/` (empty reference generated by the shrinker)
  - expected: a path outside the specified set is unknown → 404 (service); pending the Foreman's ruling
  - actual: the model normalises `/reservations/` to `/reservations` → 200 list / 401 / 405
  - reproduce: `bash stage-1/verify/audit/run_diff.sh auditor-s1-c2 C:/countersign/tmp/aud-s1-v2 C:/countersign/tmp/aud-s1-v2-out/diff 4 50 80`

No product finding: steps 1, 2, 4, 5 and 6 found no deviation from the contract or the rulings.

## Work items for the Oracle (surviving mutants → stronger tests)
- O-1 C1.107 / C1.109 / R-24 — tampered-state imports → 422, destination unchanged: reservation with an invalid id or reference
  (integrity.go:59 ×2), restaurant with `slot_minutes`/`reservation_duration_minutes` invalid (integrity.go:88 ×2), duplicate table
  id inside one restaurant (integrity.go:105), boolean field with a non-boolean value (snapshot.go:131).
- O-2 C1.107 — round trips of valid exports: a restaurant with `reservation_duration_minutes: 1`, `closes: "24:00"`, a numeric
  field starting with `9` (snapshot.go:148, integrity.go:88, :97).
- O-3 C1.28 / R-20 — reset with `closes` equal to `opens` → 422 (fixture.go:215). Added to the Auditor battery in this verdict (passes).
- O-4 runner fixes for F-1 and F-2 (never alias labels across reset/import, or never re-issue references in the model; never generate an empty reference).

## Counts for the stage report
- mutation: tool mutate.py (containerised textual Go mutants, HTTP killers audit.py + Oracle pytest), killed 51, total 65 (78.5 %; 85.0 % excluding 5 equivalent; 472 generated, 71 evaluated, 6 invalid)
- race proofs: G1 Store.Write lock / B-group + crash / yes / yes; G2 signup re-check / B8 / yes / yes; G3 idempotency single section / B3, B4 / yes / yes; G4 batch check-before-apply / moves atomicity / yes / yes; G5 Store.Read lock / race detector + readwrite_load / yes / yes
- holdout: ran no, passed 0, failed 0, escapes none (step 3 not green)

## Result
result=REJECT — the product passes every executed step with no finding; gate step 3 is red on differential-runner/model artifacts (F-1, F-2) that need an Oracle fix and a ruling before the holdout can run.
