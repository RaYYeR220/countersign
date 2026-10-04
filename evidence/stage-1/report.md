[CLOSE] stage=1 sha=c0f2b7b06069d2c3d58fdd4aef1f26a0c903e9bb

# Stage 1 report

Machine-readable twin: `evidence/stage-1/report.json`, valid against `factory/templates/stage-report.schema.json`.
This file is `evidence/stage-1/report.md`.

## Outcome
- Outcome: accepted (Auditor verdict 6, `evidence/stage-1/verdicts/verdict-6.md`)
- Accepted SHA: c0f2b7b06069d2c3d58fdd4aef1f26a0c903e9bb
- Blockers: none

## Clauses
- Total: 134 (both readers 124, A only 0, B only 10)
- Rulings: 28 (`evidence/stage-1/rulings.md`; R-26 withdrawn and superseded by R-28 after the holdout escape)

## Work
- Work items: 7 (WI-1 ADR + skeleton, WI-2 accounts/restaurants, WI-3 time/DST + availability, WI-4 reservations +
  idempotency, WI-5 export/import, WI-6 atomic moves — Builder WI-1/2/4/6, Stylist WI-3/5; WI-7 rework for the
  holdout escape, Builder, round 1 of 2)
- Candidates: 5
- Rejections:
  - bd108ef: 1 finding (Oracle differential runner not delivered), changed work: no
  - 4f54736: 2 findings (Oracle model reissued references after import; trailing-slash paths → R-25), changed work: no
  - 4f983ac: 1 finding (Oracle tamper test edited a stored receipt), changed work: no
  - 1bfe6e2: 3 findings (verdict 4: two Oracle test defects, R-26/R-27; verdict 5: holdout escape C1.81 seeded
    references, 3 holdout failures), changed work: yes (WI-7)

## Verification
- Mutation: mutate.py (containerised Go mutants, HTTP killers), 54/66 killed (81.8%) on the accepted SHA
- Race proofs:
  - G1 Store write lock — audit bursts B3/B5: red without yes, green with yes
  - G2 signup email re-check — burst B8: red without yes, green with yes
  - G3 idempotency in one write section — bursts B3/B4: red without yes, green with yes
  - G4 moves check-before-apply — moves atomicity: red without yes, green with yes
  - G5 Store read lock — race detector + readwrite_load: red without yes, green with yes
- Holdout: ran yes, passed 120, failed 0 on the accepted SHA; escapes: C1.81 (seeded reservation references outside
  `^[A-Z0-9]{6,12}$` accepted by reset; found on 1bfe6e2, fixed by WI-7, regression tests added by Auditor and Oracle)
- Oracle suite 149/149; differential runner 8,505 ops, no mismatch; Auditor attacks 617 checks, 0 failures.

## Lessons carried forward
- A ruling that loosens a stated format (R-26) was wrong; rule toward the stated format for every stored value.
- Non-blocking Oracle items O-9..O-11 (import refusals, null bodies, empty fixture ids) move into stage 2.
