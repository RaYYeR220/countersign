[CLOSE] stage=4 sha=2621e7a648fe78b450f9a2e19c18139c55be1698

# Stage 4 report

Machine-readable twin: `evidence/stage-4/report.json`, valid against `factory/templates/stage-report.schema.json`.
This file is `evidence/stage-4/report.md`.

## Outcome
- Outcome: accepted (Auditor verdict 4, `evidence/stage-4/verdicts/verdict-4.md`)
- Accepted SHA: 2621e7a648fe78b450f9a2e19c18139c55be1698
- Blockers: none

## Clauses
- Total: 24 (both readers 24, A only 0, B only 0); C1.*, C2.*, C3.* continue to apply
- Rulings: 17 (R-61 … R-77) in `evidence/stage-4/rulings.md`; 1 Oracle default overruled at reconciliation (Q1/Q2)

## Work
- Work items: 9 — Builder WI-23 replans (exact planner, apply, closures), WI-24 schema 1–3 → 4 import + ADR-004,
  WI-27 RUN.md, WI-28 impossible-history import refusal (rework round 1 of 2), WI-29 (R-76; not reproducible on
  real images, regression test added), WI-30 exact arithmetic for 2^31−1 fixture integers, WI-31 closure bounds on
  import; Stylist WI-25 series amend, WI-26 closures in availability/explain and the replanned-restaurant browser
  scenario
- Candidates: 4
- Rejections:
  - 06cdcd6: 1 finding (R-74 history shape → WI-28), changed work: yes
  - e00f6bb: 2 findings (Oracle tamper-selector defect; R-75 overflow → WI-30), changed work: yes
  - b26757c: 1 finding (R-77 closure `from` null → WI-31), changed work: yes
- Integration note: two seats waited on each other's branches (replans ↔ series amend) until the operator's liveness
  note; the Foreman integrated both on main and re-dispatched. A post-acceptance change touched only
  `stage-4/verify/audit/audit.py` (the Auditor's battery), no product file.

## Verification
- Mutation: mutate.py, 34/38 killed (89.5 %; 91.9 % excluding 1 equivalent)
- Race proofs: G1–G10 all red without, green with (G9 stale_plan and G10 plan_already_applied new in stage 4)
- Holdout: ran yes, isolated `--stage 4`; stage 1 120/120, stage 2 25/25, stage 3 7/7, stage 4 6/6 (158 passed,
  0 failed); escapes: none
- Oracle HTTP 248/248, browser 21/21; differential 8,100 ops (Auditor) + 4,860 ops (Oracle), no mismatch; attacks
  1,254 checks in 32 groups; browser checks 119; upgrades from provenance-verified stage-1/2/3 images all green.

## Not carried out
- Oracle O-19/O-20 (further import refusals from surviving mutants) were reported after acceptance of the final
  stage and were not implemented; they are test-strengthening items with no open product finding.
