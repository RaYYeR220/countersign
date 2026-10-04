[CLOSE] stage=2 sha=aa63cd28f4559a0c7fc394b164001385adbe315a

# Stage 2 report

Machine-readable twin: `evidence/stage-2/report.json`, valid against `factory/templates/stage-report.schema.json`.
This file is `evidence/stage-2/report.md`.

## Outcome
- Outcome: accepted (Auditor verdict 4, `evidence/stage-2/verdicts/verdict-4.md`)
- Accepted SHA: aa63cd28f4559a0c7fc394b164001385adbe315a
- Blockers: none

## Clauses
- Total: 59 (both readers 59, A only 0, B only 0); the stage-1 contract C1.1–C1.134 continues to apply
- Rulings: 17 (R-29 … R-45; R-31 superseded by R-40, R-33 by R-39) in `evidence/stage-2/rulings.md`

## Work
- Work items: 9 — Builder WI-8 combined tables, WI-9 stage-1 → stage-2 migration + ADR-002, WI-13 empty/over-long
  ids (rework round 1), WI-14 RUN.md, WI-15 both fields regardless of type (rework round 2), WI-16 both-fields
  check before ownership (new item per R-44); Stylist WI-10/11/12 browser product (design direction, shell and
  auth, grid/booking/recovery, lookup and upgrade survival)
- Candidates: 5
- Rejections:
  - b4e805f: 3 findings (product R-42 → WI-13; Oracle lookup tests and model order), changed work: yes
  - 090df19: 1 finding (product R-43 table_id type → WI-15), changed work: yes
  - 3550522: 1 finding (product R-44 order → WI-16), changed work: yes
  - a6497b4 was superseded before a verdict (contained the 090df19 finding)

## Verification
- Mutation: mutate.py, 40/49 killed (81.6%) excluding 5 equivalent mutants per R-45; raw 40/54 (74.1%)
- Race proofs:
  - G1 write lock — bursts B3/B5: red without yes, green with yes
  - G2 signup re-check — B8: red without yes, green with yes
  - G3 idempotency single section — B3/B4: red without yes, green with yes
  - G4 moves check-before-apply — moves atomicity: red without yes, green with yes
  - G5 read lock — race detector: red without yes, green with yes
  - G6 occupancy over every table-set member — comboburst: red without yes, green with yes
- Holdout: ran yes; stage 1 120/120 and stage 2 25/25 (145 passed, 0 failed); escapes: none
- Oracle HTTP 170/170, browser 20/20; differential 8,505 ops, no mismatch; attacks 790 checks; browser checks 116;
  upgrade from the accepted stage-1 image green (tokens, lookups, receipts, lost-response retry in the browser).

## Carried forward
- Oracle O-12/O-13 (import refusals for more tampered states; party_size exactly 2^53) move into stage 3.
- Lesson: ordering rulings must state where each new check sits relative to existing ones on every path (R-44).
