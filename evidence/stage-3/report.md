[CLOSE] stage=3 sha=c4a828e341d997f00b8c078c423135fbccf6fdac

# Stage 3 report

Machine-readable twin: `evidence/stage-3/report.json`, valid against `factory/templates/stage-report.schema.json`.
This file is `evidence/stage-3/report.md`.

## Outcome
- Outcome: accepted (Auditor verdict 2, `evidence/stage-3/verdicts/verdict-2.md`)
- Accepted SHA: c4a828e341d997f00b8c078c423135fbccf6fdac
- Blockers: none

## Clauses
- Total: 53 (both readers 52, A only 0, B only 1); C1.* and C2.* continue to apply
- Rulings: 15 (R-46 … R-60) in `evidence/stage-3/rulings.md`; 3 of the Oracle's 25 defaults overruled at reconciliation

## Work
- Work items: 6 — Builder WI-17 policies/terms/revisions/history/decision and stage-3 semantics of PATCH, cancel and
  moves; WI-18 schema 1/2 → 3 import + ADR-003; WI-22 RUN.md. Stylist WI-19 explanations and policy-driven
  availability; WI-20 series; WI-21 empty/over-long anchor_reference (rework round 1 of 2)
- Candidates: 2
- Rejections:
  - 1aaecec: 1 finding (R-60 anchor_reference 404 instead of 422 → WI-21), changed work: yes
- Integration note: a semantic merge conflict (series.go calling helpers the Builder had moved) broke the build on
  main between merges; it was handed to the file owner and fixed before any candidate was cut.

## Verification
- Mutation: mutate.py, 22/27 killed (81.5%) on the accepted SHA (28 of 788 mutants evaluated in the 20-minute box)
- Race proofs: G1–G8 all red without, green with (G7 already-in-series under concurrency, G8 all-or-nothing
  adoption are new in stage 3)
- Holdout: ran yes; stage 1 120/120, stage 2 25/25, stage 3 7/7 (152 passed, 0 failed); escapes: none
- Oracle HTTP 226/226 (with stage-1 and stage-2 upgrade sources), browser regression 20/20; differential 8,505 ops,
  no mismatch; attacks 1,059 checks in 23 groups; stage-2 browser regression 116/116.

## Carried forward
- Oracle O-14 (tampered stage-3 import records; merged into main after acceptance, not yet run against a product
  image) and O-15 (import boundaries: receipt status 200 vs 199, slot_minutes 1, bad `opens` only, null or mis-keyed
  series entry) move into stage 4; any failure there is a stage-4 finding for the import validation owner.
