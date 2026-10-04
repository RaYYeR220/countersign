[VERDICT] stage=1 sha=bd108ef786a7b8d9e9c6b9f2bad5b03d5ced13f3 result=REJECT

# Verdict 1 — stage 1

- Candidate: bd108ef786a7b8d9e9c6b9f2bad5b03d5ced13f3 (main, "[PLAN] stage-1 all work items integrated")
- Clean clone: C:/countersign/tmp/aud-s1-v1 (`git clone C:/countersign/result`, `git checkout bd108ef…`)
- Battery code: seat/auditor @ 0165e77 (`stage-1/verify/audit/`); Oracle suite from the clone (`stage-1/verify/oracle/` @ b7496b1)
- Contract: `evidence/stage-1/ledger.md` (C1.1–C1.134) and `evidence/stage-1/rulings.md` (R-1 … R-24) at the candidate SHA
- Artifacts: `evidence/stage-1/verdicts/verdict-1-artifacts/`
- File: `evidence/stage-1/verdicts/verdict-1.md`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage1 .` (RUN.md, in `stage-1/`); run on `--internal` network, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | pass (106/106) | `run_oracle.sh auditor-s1-c1 C:/countersign/tmp/aud-s1-v1 …/oracle` | 0 |
| 3 | differential runner (seeds: none) | fail — not delivered | `stage-1/verify/oracle/diff_runner.py` absent at bd108ef and on seat/oracle b7496b1; escalated to the Foreman | n/a |
| 4 | attacks | pass (583 checks, 0 hard, 0 soft) | `run_attacks.sh auditor-s1-c1 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (74/90 = 82.2 %) | pass (target 75 %) | `mutate.py --src …/stage-1 --targets internal/state,internal/localtime,internal/api,internal/snapshot,internal/jsonin --budget 1200 --workers 4 --seed 1 --oracle …` | 0 |
| 6 | race proofs (5 guards) | pass (5/5 red-without, 5/5 green-with) | `race.py …` (G1–G4), `race_detect.sh` + `readwrite_load.py` (G5) | 0 |
| 7 | user-facing checks | skip | stage 1 has no user-facing surface (C1.2 "Only the HTTP API is required.") | n/a |
| 8 | holdout | skip | step 3 is not green and no ruling records the gap; the holdout runs only after steps 1–7 are green | n/a |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s1-v1/stage-1 && docker build -t tablekeeper-stage1 .     → exit 0 (11.0 s); image sha256:b636303f…, tagged auditor-s1-c1, 6.7 MB
$ docker network create --internal auditor-s1-boot-net
$ docker run -d --name auditor-s1-boot --network auditor-s1-boot-net --cpus 2 --memory 2g -e PORT=8080 auditor-s1-c1
  (runner on the same network) healthy_after_s=0.07 200 {"status":"ok"}     (limit 60 s, C1.11)
  egress: blocked URLError                                                  (C1.7)
  no PORT  → http://auditor-s1-boot2:8080/health 200 {"status":"ok"}        (C1.10 default)
  PORT=18300 → http://auditor-s1-boot3:18300/health 200 {"status":"ok"}     (C1.10)
  cpus=2000000000 mem=2147483648; idle memory 1.7 MiB
```
### Step 2
```
$ bash stage-1/verify/audit/run_oracle.sh auditor-s1-c1 C:/countersign/tmp/aud-s1-v1 C:/countersign/tmp/aud-s1-v1-out/oracle
  (two candidates A/B on an --internal network, 2 CPU / 2 GiB each; pytest 8.4.2 in auditor-runner-py on the same network,
   --base-url http://auditor-s1-oa:8080 --second-base-url http://auditor-s1-ob:8080)
106 passed in 13.71s                                   junit: tests="106" failures="0" errors="0"
```
### Step 3
```
$ ls C:/countersign/tmp/aud-s1-v1/stage-1/verify/oracle/
README.md client.py conftest.py fixtures.py model.py serve_model.py test_acceptance.py      (no diff_runner.py)
$ git ls-tree -r --name-only seat/oracle -- stage-1/verify/oracle | grep -i diff                  (no output; head b7496b1)
→ [ESCALATION] sent to the Foreman (message 7fb87961): ruling for the gap, or a new candidate with the runner.
```
### Step 4
```
$ bash stage-1/verify/audit/run_attacks.sh auditor-s1-c1 C:/countersign/seats/auditor C:/countersign/tmp/aud-s1-v1-out/attacks --rounds 3
  (A target + B fresh import destination, --internal network, 2 CPU / 2 GiB each; health A 0.02 s, B 0.00 s)
checks 583  hard_failures 0  soft_failures 0  errors []
core 66 | auth 59 | availability 37 | create 88 | reads 7 | cancel 15 | patch 59 | dst 37 | idem 23 | moves 55 | burst 45 | export 92
burst ×3 rounds: B1 50 same-slot creates → 1×201 + 49×409; B3 30 identical keyed → 1×201 + 29×200; B5 20 identical batches → 1×201 + 19×200;
  B6 8 PATCHes → 1×200 + 7×409; B7 10 competing batches → 1×201, all-or-nothing; B8 20 same-email signups → 1×201; B10 50 reads < 5 s
export/import into the same server and into a fresh container: tokens, hashed logins, receipts (create + batch), failed keys, references preserved
98 clauses exercised: C1.3–C1.5, C1.8, C1.11–C1.18, C1.22, C1.26, C1.29–C1.32, C1.34–C1.36, C1.38, C1.40–C1.43, C1.45–C1.51, C1.53–C1.55,
  C1.57–C1.59, C1.61–C1.72, C1.74–C1.77, C1.79–C1.99, C1.101–C1.104, C1.106–C1.112, C1.114–C1.120, C1.122–C1.124, C1.132
version upgrade: not applicable at stage 1 (no previous stage image)
```
### Step 5
```
$ python stage-1/verify/audit/mutate.py --src C:/countersign/tmp/aud-s1-v1/stage-1 --work C:/countersign/tmp/aud-mut-v1 \
    --targets internal/state,internal/localtime,internal/api,internal/snapshot,internal/jsonin --budget 1200 --workers 4 --seed 1 \
    --oracle C:/countersign/tmp/aud-s1-v1/stage-1/verify/oracle --out …/mutation.json
  each mutant: Linux build in golang:1.26-alpine, server + killers (audit.py all groups but burst, Oracle pytest) in one --network none
  container (2 CPU / 2 GiB); a kill must reproduce on a second run; baseline 0 failing checks
generated 472, evaluated 100 (seeded sample within the 20-min box, 1498 s), killed 74, survived 16, invalid 10 → 82.2 %
survivors: 4 judged equivalent (localtime.go:119, :216, :262 — guarded by fixture/state validation or redundant probes; server.go:77 abort-panic path),
  12 test gaps → work items for the Oracle (below). Excluding the 4 equivalents: 74/86 = 86.0 %.
note: two earlier host-native runs were discarded — the Windows host had ~13 000 TCP sockets in TIME_WAIT (16 384 ephemeral ports),
  so connection failures produced false kills (run 0: 239/239 "killed"; re-check of two kills in isolation passed). Kept as
  mutation-run0-native-invalid.json for the record.
```
### Step 6
```
G1 Store.Write exclusive lock (internal/state/store.go)            race.py --groups burst --rounds 3
   without: run 1 green, run 2: B3/B5 counts wrong then crash (concurrent map writes) → RED; with: 0 failures → GREEN
   race detector (race_detect.sh, burst ×1): without 9 DATA RACE reports (StoreReceipt/keyedWrite), with 0
G2 signup e-mail re-check under the write lock (internal/api/auth.go)  without: B8 wrong in r0, r1, r2 → RED; with → GREEN
G3 idempotency lookup + execute + store in one write section (internal/api/idempotency.go)
   split into Read-lookup then Write-execute: 3×3 bursts green (window too narrow to observe);
   split + 5 ms window (G3b): B3 (2×201) and B4 (two bookings) → RED; with → GREEN
G4 batch moves check all resulting bookings before applying (internal/api/moves.go; edit: apply(changes[:1]) before the check)
   without: "failed batch changed nothing", "failed batch kept old occupancy", "unchanged after internal overlap" → RED; with → GREEN
G5 Store.Read shared lock (internal/state/store.go)
   HTTP checks do not observe it (burst ×3 and ×5 green without the lock);
   race detector + readwrite_load.py (5 rounds of 60 mixed reads/writes): without 13 DATA RACE reports (AddReservation vs readers), with 0 → RED/GREEN
```
### Step 7
```
skipped: no user-facing surface in stage 1 (C1.2)
```
### Step 8
```
skipped: step 3 not green, no ruling recorded
```

## Findings
- F-1 (process gap, not a product defect)
  - clause: gate step 3 — the Oracle's differential runner ("`diff_runner.py` | Differential runner: seeded random operation sequences against the model and the service" in `stage-1/verify/oracle/README.md`)
  - request: `ls stage-1/verify/oracle/` at bd108ef; `git ls-tree seat/oracle -- stage-1/verify/oracle`
  - expected: a differential runner to execute with fixed seeds plus one fresh seed
  - actual: not delivered; no ruling records the gap
  - reproduce: `git -C C:/countersign/tmp/aud-s1-v1 ls-tree -r --name-only HEAD -- stage-1/verify/oracle`

No product finding: every clause exercised by steps 1, 2, 4, 5 and 6 behaved as the contract and rulings require.

## Work items for the Oracle (surviving mutants → stronger tests)
- O-1 C1.89 / R-11 — "ties by `created_at` ascending, then `reference` ascending": no test has two bookings with the same `starts_at`, `created_at` at least one second apart, and references in the opposite order (survivor reservations.go:136).
- O-2 C1.107 / C1.109 / R-24 — "an invalid state give[s] 422 … without changing the destination": imports of tampered exports are not covered: duplicate reservation id or reference inside `state`, a missing collection, an invalid restaurant, an invalid reservation record, a boolean or number field of the wrong JSON kind (survivors integrity.go:33, :49, :59, :68; snapshot.go:131 ×2).
- O-3 C1.107 / C1.18 / R-8 — "It must accept an unchanged export produced by this service": round trips are not covered for exports containing a 64-character id, an opening-hours `closes: "24:00"`, a table of capacity 1, and a numeric field whose value is 0 (survivors integrity.go:30 ×2, :97, :105; snapshot.go:133).
- O-4 C1.98 / G5 — no suite test observes a missing read lock; `readwrite_load.py` under the race detector does. Consider a concurrent read/write acceptance test.

## Counts for the stage report
- mutation: tool mutate.py (textual Go mutants, HTTP killers: audit.py + Oracle pytest, containerised), killed 74, total 90 (472 generated, 100 evaluated, 10 invalid; 82.2 %, 86.0 % excluding 4 equivalent)
- race proofs: G1 Store.Write lock / B3,B5 + race detector / red-without yes / green-with yes;
  G2 signup re-check / B8 / yes / yes; G3 idempotency single section / B3,B4 (5 ms window) / yes / yes;
  G4 batch check-before-apply / moves atomicity checks / yes / yes; G5 Store.Read lock / race detector + readwrite_load / yes / yes
- holdout: ran no, passed 0, failed 0, escapes none (step 3 gap)

## Result
result=REJECT — the product passed every executed step with no finding, but gate step 3 (differential runner) was not delivered and no ruling records the gap, so the holdout could not run.
