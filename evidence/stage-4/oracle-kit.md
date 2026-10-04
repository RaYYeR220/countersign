[EVIDENCE] stage=4 wi=oracle-kit sha=854c25b

# Evidence — Oracle stage-4 verification kit (planner, closures, apply, series amend, upgrades, O-15)

- Revision: see commit `[WI-oracle-kit-4]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed (entry-B ids, evidence/stage-4/ledger-B.md): B1, B3–B41; plus the stage-3 leftover O-15 (C3.48/R-55/R-24).
  Not machine-checked: B2 (scope); B8 is checked as "201 or 422 planning_limit or 409" beyond the supported sizes.

## What was built (stage-4/verify/oracle/)
- `model.py`: replan preview (manager + key; 404 restaurant → 403 → types 400 → missing 422 → instants with a numeric
  offset and from < to 422 → 404 table → 422 planning_limit → 409 no_feasible_plan); considered bookings = confirmed,
  same restaurant, overlapping [from, to) on any table; a brute-force planner over singles (fixture order) and declared
  pairs, feasibility under each booking's own accepted capacities with no conflict against fixed bookings, applied
  closures, the proposed closure or other assignments, minimising (moved, unused seats, rank vector) lexicographically;
  preview stores only the plan and reports the restaurant revision. Apply (404 plan / other restaurant → 409
  plan_already_applied → 409 stale_plan) records the closure, moves bookings with revision +1 and a `reassigned` entry
  carrying `plan_id` and a `table_ids` change, bumps each affected series once and the restaurant once, and is
  idempotent. Closures block availability, options, explain (`no_overlap` false) and every write path. Series amend
  (owner + key; 404 → every field problem 422 → 409 stale_revision → eligible occurrences in index order: cutoff → the
  resulting date's policy → occupancy last) with no-ops, one `changed` entry and revision per real change, series and
  restaurant revisions once, no exception marking, replays. Restaurant revision per R-54 plus plan application.
  Export schema v4 with closures and plans; v1/v2/v3 model states migrate on import. R-60 folded in.
- `test_stage4_api.py` (17 tests): preview shape, validation and the considered set; capacity under own terms, no
  cutoff protection and each objective level; planning limit; the restaurant revision through previews; no feasible
  plan; apply with every precondition, replay, second key, stale plan, atomicity under 20 concurrent applies, a closure
  at another restaurant; closures blocking creates, pairs, PATCH, moves, explain, and surviving export/import; plans
  moving series members (flags kept, series revision once); amend preconditions, semantics across exceptions and
  cancelled occurrences and a policy change, all-no-op, replay, index-order failures, closures as conflicts, concurrent
  amends; upgrades from stage-1/2/3 exports (receipts verbatim, imported series amended and replanned); O-15
  (receipt 200 accepted vs 199 refused, slot_minutes 1 accepted vs 0 refused, bad `opens` only, null and mis-keyed
  series entries).
- `test_ui.py`: a stage-4 test that an applied plan shows as unavailable cells on the grid and as the new table in lookup.
- `diff_runner.py`: ops for preview, apply (including key reuse and wrong restaurant) and amend; plan ids aliased, closure
  instants compared as UTC.

## Commands (model 18400/18402/18403; stage-1/2/3 models 18411/18413/18414 as upgrade sources)

### `python -m pytest stage-4/verify/oracle/test_acceptance.py stage-4/verify/oracle/test_hardening.py stage-4/verify/oracle/test_stage2_api.py stage-4/verify/oracle/test_stage3_api.py stage-4/verify/oracle/test_stage4_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 --stage2-base-url http://127.0.0.1:18413 --stage3-base-url http://127.0.0.1:18414 -p no:cacheprovider`
Exit code: 0
```
243 passed in 10.29s
```

### `python -m pytest stage-4/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18403 -p no:cacheprovider`
Exit code: 0
```
21 passed in 17.00s
```

### `python stage-4/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 2.3s
```
`op_stats.py --runs 20` (excerpt): preview 201/401/403/404/422, apply 201/401/403/404/409 plan_already_applied,
amend 404/422 (more once series exist earlier in a sequence).

### Accepted images as upgrade sources (informational)
`tablekeeper-stage{1,2,3}:latest` on 18410/18412/18415 produce exports with `schema` 1/2/3; the model refuses them (it
reads only its own formats), as expected; against a candidate they are the real sources.

## Known gaps
- Validated against the reference model only; no stage-4 candidate yet.
- Q1–Q24 implemented with the stated defaults; Q2/Q11 (orders), Q3 (considered set on any table), Q7 (closure
  echo in the restaurant's zone) and Q17 (amend order, every field problem 422) are the likeliest collisions.
