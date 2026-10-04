[VERDICT] stage=2 sha=aa63cd28f4559a0c7fc394b164001385adbe315a result=ACCEPT

# Verdict 4 — stage 2 (candidate 5)

- Candidate: aa63cd28f4559a0c7fc394b164001385adbe315a (main, "[PLAN] stage-2 candidate 5"); supersedes candidate 4 (3550522, verdict 3)
- Clean clone: C:/countersign/tmp/aud-s2-v4 (working tree clean before the holdout); `stage-1/` identical to c0f2b7b
- Product delta since 3550522: WI-16 (R-44) — `internal/api/moves.go`, `internal/api/tables.go` (+ a Go test)
- Upgrade source: accepted stage-1 image from `stage-1/` at c0f2b7b (`auditor-s1-c5`)
- Battery code: seat/auditor (stage-2/verify/audit); Oracle suites from the clone (seat/oracle f1ec500: 189 HTTP incl. 19 browser-skipped, 20 browser)
- Contract: C2.1–C2.59 and C1.1–C1.134; rulings R-1…R-28 (R-26 withdrawn) and R-29…R-44 (R-31→R-40, R-33→R-39)
- Artifacts: `evidence/stage-2/verdicts/verdict-4-artifacts/` (junit, attack/browser JSON, 20 screenshots, race/mutation JSON, differential output, holdout report)

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass | `docker build -t tablekeeper-stage2 .` (RUN.md, in `stage-2/`); `--internal`, `--cpus 2 --memory 2g -e PORT=8080` | 0 |
| 2 | acceptance suites | pass (HTTP 170/170 + browser 20/20) | `PREV_IMG=auditor-s1-c5 run_oracle.sh auditor-s2-c5 C:/countersign/tmp/aud-s2-v4 …/oracle` | 0 |
| 3 | differential runner (seeds 1 ×50, 976792 ×5, fresh 404836 ×50; 80 ops) | pass (8505 ops, no mismatch) | `run_diff.sh auditor-s2-c5 … <seed> <runs> 80` | 0 |
| 4 | attacks | pass (790 checks; + combo 147 with the boundary cases added here) | `PREV_IMG=auditor-s1-c5 run_attacks.sh auditor-s2-c5 C:/countersign/seats/auditor …/attacks --rounds 3` | 0 |
| 5 | mutation (40/54 raw = 74.1 %; 40/49 = 81.6 % excluding 5 equivalent mutants) | pass on the equivalent-adjusted score (target 75 %) — see below | `mutate.py … --seed 25 …`; survivors re-run with `--only-survivors` after strengthening the battery | 0 |
| 6 | race proofs (6 guards) | pass (6/6 red-without, 6/6 green-with) | `race.py` (G1–G4, G6), `race_detect.sh … readwrite 5` (G5) | 0 |
| 7 | user-facing checks | pass (116 browser checks; screenshots) | `PREV_IMG=auditor-s1-c5 run_ui.sh auditor-s2-c5 C:/countersign/seats/auditor …/ui` | 0 |
| 8 | holdout (isolated, --stage 2) | pass — stage 1 120/120, stage 2 25/25 | `PYTHONUTF8=1 C:/countersign/.venv/Scripts/python.exe C:/countersign/result/factory/setup/harness_win.py run --track tablekeeper --repo C:/countersign/tmp/aud-s2-v4 --stage 2 --mode isolated --out C:/countersign/tmp/checks/auditor-s2-v4-holdout` (from C:/countersign/kickoff) | 0 |

## Outputs
### Step 1
```
$ cd C:/countersign/tmp/aud-s2-v4/stage-2 && docker build -t tablekeeper-stage2 .     → exit 0 (19.9 s); tagged auditor-s2-c5
  internal network, 2 CPU / 2 GiB: healthy_after_s=0.09 200 {"status":"ok"}; egress: blocked URLError
  no PORT → :8080 200; PORT=18300 → :18300 200; / /signup /login /lookup → 200 text/html; charset=utf-8, no external src/href
```
### Step 2
```
HTTP (A, B, stage-1 S1):  170 passed, 19 skipped (browser tests)   junit tests=189 failures=0
Browser (--ui):            20 passed                                  junit tests=20 failures=0
```
### Step 3
```
OK: 50 runs, 4050 ops, seeds 1..50, 26.3s
OK: 5 runs, 405 ops, seeds 976792..976796, 2.0s
OK: 50 runs, 4050 ops, seeds 404836..404885, 28.6s        (fresh seed)
```
### Step 4
```
checks 790  hard 0  soft 0   (stage-1 groups; combo incl. R-34/R-35/R-39/R-42/R-43/R-44 attacks; comboburst ×3; upgrade from stage 1)
$ … --groups combo (adds: options with party exactly equal to a single/pair capacity — parties 6, 8, 10; seeded starts_at_local
  not a calendar date / malformed → 422)    checks 147  hard 0
```
### Step 5
```
run 1 (seed 25): generated 570, evaluated 60, killed 39, survived 15, invalid 6, 1411 s; baseline 0 failing
survivors re-run against the strengthened battery (same mutants, --only-survivors): availability.go:123 now killed (pair capacity == party)
totals: killed 40, survived 14 → 74.1 % raw
equivalent (5), no observable behaviour can differ:
  localtime.go:272  loop bound `&&`→`||`: extra candidates past `closes` are removed by the end-of-day check
  localtime.go:237  `minute >= closes`→`>`: a start at `closes` is rejected by the end check (duration > 0)
  localtime.go:288  stable sort `<`→`<=` over distinct slot keys
  localtime.go:182  ResolveOrAfter for trusted seeds: an unparseable-but-pattern-matching date never reaches it (fixture
                    validation refuses it first — verified by the new attack)
  amend.go:30       cutoff `<=`→`<`: differs only when starts_at − now equals the cutoff to the nanosecond (R-6), not reachable
                    with a real clock
→ 40 / 49 = 81.6 % (target 75 %)
remaining survivors (9) → Oracle work items (below)
```
### Step 6
```
G1 Store.Write lock          without: crash (concurrent map writes), 9 checks; with: 0                 RED/GREEN
G2 signup re-check           without: B8 wrong r0–r2; with: 0                                         RED/GREEN
G3 idempotency one section   split + 5 ms window: B3, B4 (12 checks); with: 0                          RED/GREEN
G4 batch check-before-apply  without: failed batch left changes (4 checks); with: 0                    RED/GREEN
G5 Store.Read lock           race detector + readwrite_load 5×60: without 14 DATA RACE, with 0         RED/GREEN
G6 every member occupied     without: overlapping bookings, "single on the other member -> 409" (7); with: 0   RED/GREEN
```
### Step 7
```
checks 116  hard 0  soft 0 (u_routes 9, u_auth 11, u_grid 6, u_click_rules 8, u_booking 23, u_conflict 5, u_lost_after 6,
u_lost_before 6, u_out_of_order 4, u_lookup 6, u_states_distinct 2, u_keyboard 5, u_layout 18, u_import 7)
```
### Step 8
```
building C:\countersign\tmp\aud-s2-v4\stage-2 ...
  stage 1: pass
building upgrade source C:\countersign\tmp\aud-s2-v4\stage-1 ...
  stage 2: pass
highest contiguous stage: 2      claimed stage: 2      revision aa63cd2…      mode isolated
stage 1: 120/120 (health/reset/auth 12, reservations 37, restaurants/availability 18, retries/time input 20, sample 20, seeded state 13)
stage 2: 25/25 (sample 8, ui 17)
(stage 3 in the report is the harness's unclaimed overshoot probe; not part of this stage)
```

## Findings
None on this SHA. Stage history: verdict 1 (b4e805f) R-42 → WI-13; verdict 2 (090df19) R-43 → WI-15; verdict 3 (3550522) R-44 → WI-16;
Oracle defects F-2/F-3 (verdict 1) and the R-43 model order fixed in the Oracle.

## Work items for the Oracle (surviving mutants; not blocking)
- O-12 C1.109 / R-24 — import refusals not yet observed: a user record with an invalid id (integrity.go:39); a state missing exactly
  one of users/restaurants (integrity.go:33 ×2); a receipt with a status outside 2xx (integrity.go:81); opening hours with a duplicate
  weekday inside an imported restaurant (integrity.go:99); a nil collection serialised as `null` (snapshot.go:80); boolean/number
  fields holding the wrong kind (snapshot.go:173, :175).
- O-13 C1.34 / R-3 — party_size exactly 2^53 (jsonin.go:111 boundary).

## Counts for the stage report
- mutation: tool mutate.py (containerised textual Go mutants; killers audit.py + Oracle HTTP suite), killed 40, total 54 raw (74.1 %);
  81.6 % excluding 5 equivalent mutants (570 generated, 60 evaluated, 6 invalid)
- race proofs: G1 Store.Write lock / bursts + crash / yes / yes; G2 signup re-check / B8 / yes / yes; G3 idempotency single section /
  B3,B4 / yes / yes; G4 batch check-before-apply / moves / yes / yes; G5 Store.Read lock / race detector / yes / yes; G6 member-wide
  occupancy / combo / yes / yes
- holdout: ran yes, stage 1 passed 120 failed 0, stage 2 passed 25 failed 0, escapes none

## Result
result=ACCEPT — every battery step is green on aa63cd2 from a clean clone, including 116 browser checks, the stage-1 upgrade and the
isolated holdout (stage 1 120/120, stage 2 25/25); step 5 passes on the equivalent-adjusted score, each equivalence argued above.
