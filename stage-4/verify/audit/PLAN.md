# Stage 4 — Auditor attack plan (seating repairs, closures, recurring amendments)

Written from `stage-4.md` plus the binding contracts C1–C3 and rulings R-1…R-60. Stage-4 clause ids (C4.n) are attached
after reconciliation; until then checks carry section names ("S4 …"). Ordering gaps Q1–Q14 were escalated at kickoff;
checks that depend on them are soft until ruled.

## Files
`audit.py` (all stage-1/2/3 groups as regression + `replan`, `optimal`, `samend`, `s4burst`, `upgrade4`; includes `Planner`,
an independent brute-force optimiser), `run_attacks.sh` (`PREV_IMG` c0f2b7b, `PREV2_IMG` aa63cd2, `PREV3_IMG` c4a828e),
`ui_audit.py`/`run_ui.sh` (browser regression, incl. a replanned restaurant), `run_oracle.sh` (stage-1/2/3 sources), `run_diff.sh`,
`mutate.py`, `race.py`, `race_detect.sh`.

## Stage-4 attacks
| group | attacks |
|---|---|
| replan | auth 401/403, missing key, unknown restaurant/table/foreign table 404; interval matrix (from == to, from > to, no offset, not a time, date only, empty) → 422; missing fields → 422; preview equals the brute-force optimum (assignments in reference order, changed flags, moved_count, unused_seats) on a hand-built scenario with a partially overlapping fixed booking; shape (plan_id, restaurant_revision, closure echo); restaurant_revision = successful writes since reset; preview replay 200 identical; preview changes nothing (bookings, histories, availability) and does not move the revision; apply: 403, unknown plan, plan through another restaurant 404, missing key; stale_plan after an intervening write (both plans), nothing changed; a write at another restaurant does not invalidate; apply 201 with revision +1 once, reservations in reference order; moved bookings: new tables, same times/party/terms, revision +1, one `reassigned` entry with plan_id and a table_ids change; unmoved bookings unchanged; second key → plan_already_applied; replay 200; closure: excluded from available_table_ids and options, offered outside the interval, explain no_overlap false, create/pair/amend onto it → 409; a second preview honours the applied closure (brute force) |
| optimal | ≥ 6 random scenarios (3–6 tables, 0–4 random pairs, optional policy changing capacities so bookings carry different terms, 3–9 random bookings, random closure): preview == brute force, or 409 no_feasible_plan / 422 planning_limit exactly when the brute force says so |
| samend | 401, another owner/unknown 404, missing key; 12 invalid inputs and 3 missing fields → 422; stale → 409, stale before occurrence validation; eligible set (index ≥ from_index, not cancelled, not exception); scheduled dates kept; series +1 once, no exceptions marked; per-occurrence changed entry and revision; replay 200 original; all-no-op keeps revisions; occupancy failure at a later index → 409 and nothing changes; outside hours / off grid → 422 |
| s4burst | per round: Q1 12 concurrent applications of one plan → one 201, every moved booking +1 exactly once; Q2 two plans from one revision applied concurrently → one wins, the other stale; Q3 12 concurrent amends from one expected revision → one real change; Q4 identical keyed previews → one 201 + identical 200s; global invariant |
| upgrade4 | exports from accepted stage-1, stage-2 and stage-3 images (the stage-3 one with a series that has a cancelled occurrence): import, token + lookup, original retry, amend of the imported series, replan on an imported restaurant |

Steps 5/6: mutation over the new planner/closure/amend code; race proofs for the plan-application and amend guards as found.
Step 7: the stage-2 browser regression against the candidate, plus a replanned restaurant (closed cells false, lookup shows new tables).
Step 8: holdout `--stage 4`.

## Self-test
`Planner` checked against a hand-solved scenario (2 moves, 1 unused seat; pair chosen for zero waste). The remaining groups are
validated on the first build implementing WI-23/24/25.
