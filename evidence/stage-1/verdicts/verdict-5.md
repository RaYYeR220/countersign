[VERDICT] stage=1 sha=1bfe6e25eab8cfd5f189437d289b4b997213bb61 result=REJECT

# Verdict 5 — stage 1 (candidate 4, after R-26)

- Candidate: 1bfe6e25eab8cfd5f189437d289b4b997213bb61 — same SHA and clean clone as verdict 4 (C:/countersign/tmp/aud-s1-v4)
- Steps 1–7: as recorded in verdict 4 (all re-run on this SHA). Step 2's single deterministic failure (tamper `reference="bad ref"`)
  was recorded as a gap by ruling R-26 (Foreman, main 2d080b2), so steps 1–7 were treated as green and the holdout ran.
- Artifacts: `evidence/stage-1/verdicts/verdict-5-artifacts/` (holdout report and counts; no holdout test code)

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1–7 | see verdict 4 | pass (step 2 under R-26; step 7 skip, no UI) | verdict-4.md | — |
| 8 | holdout (isolated) | fail — 117/120, 3 escapes | `PYTHONUTF8=1 C:/countersign/.venv/Scripts/python.exe C:/countersign/result/factory/setup/harness_win.py run --track tablekeeper --repo C:/countersign/tmp/aud-s1-v4 --stage 1 --mode isolated --out C:/countersign/tmp/checks/auditor-s1-v5-holdout` (from C:/countersign/kickoff) | 1 |

## Outputs
### Step 8
```
building C:\countersign\tmp\aud-s1-v4\stage-1 ...
  stage 1: fail
claimed stage: 1      revision 1bfe6e2…   mode isolated
stage 1: collected 120, passed 117, failed 3 (share 0.9615)
  all three failures: POST /_test/reset with a fixture whose seeded reservation has a reference outside ^[A-Z0-9]{6,12}$
  (three values: one character; lowercase letters and digits; more than 12 characters including '-')
  expected 422 validation_failed, got 204
(stage 2 in the report is the harness's unclaimed overshoot probe; not part of this stage)
```
Auditor reproduction (battery updated in this verdict): `run_attacks.sh auditor-s1-c4 … --groups core` → 8 hard failures,
all "reset rejects fixture: seeded reference … -> 422" and "… state unchanged" for `X`, `seed01`, `ABCDEFGHJKLMN`, `SEED-01`.

## Findings
- F-1 (escape — product; supersedes R-26)
  - clause: C1.81 — "`reference` is 6 to 12 characters of `A-Z0-9`, unique across all reservations, and never changes." applied
    to C1.30 — "`reservations` may seed confirmed bookings, with the same fields as a `POST /reservations` body plus `id`,
    `reference` and `user_id`." and C1.12 / R-8 (refused fixture → 422 `validation_failed`, state unchanged)
  - request: `POST /_test/reset` with a fixture whose seeded reservation has `reference` of 1 character, or containing lowercase
    letters, or longer than 12 characters / containing `-`
  - expected: 422 `validation_failed`, state unchanged (the reference format binds seeded references too)
  - actual: 204; the reservation is stored with that reference
  - reproduce: `bash stage-1/verify/audit/run_attacks.sh auditor-s1-c4 C:/countersign/seats/auditor <out> --groups core --rounds 1`
    (checks "reset rejects fixture: seeded reference …")
- Consequence for import (C1.109 / R-24): once reset refuses such references, no export of this service can contain one, so
  import should refuse a state whose reservation reference is outside the format (422, destination unchanged) — the Oracle's
  `reference="bad ref"` tamper expectation becomes correct again. R-26 (option 1, recommended by the Auditor) is contradicted
  by the holdout and needs superseding.

## Requests
- Foreman: supersede R-26 — seeded and imported reservation references must match `^[A-Z0-9]{6,12}$` (and stay unique); reset
  and import refuse others with 422 `validation_failed`, state unchanged. Rework item for the owner of fixture/import validation.
- Oracle: regression tests for F-1 — reset refuses seeded references outside the format (several shapes: too short, lowercase,
  too long, punctuation) with state unchanged; import refuses a state carrying such a reference; restore the `bad ref` tamper.

## Counts for the stage report
- mutation: as verdict 4 — killed 50, total 61 (82.0 %)
- race proofs: as verdict 4 — 5 guards, 5/5 red-without, 5/5 green-with
- holdout: ran yes, passed 117, failed 3, escapes C1.81 (seeded references, via C1.30 / C1.12)

## Result
result=REJECT — the isolated holdout fails 3 of 120: reset accepts seeded references outside C1.81's format; R-26 must be
superseded and fixture (and import) validation tightened.
