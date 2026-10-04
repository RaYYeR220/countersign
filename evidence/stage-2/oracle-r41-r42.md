[EVIDENCE] stage=2 wi=oracle-r41-r42 sha=<filled at commit>

# Evidence — R-41, R-42 and the R-35 order fix (stage-2 verdict-1 items)

- Revision: see commit `[WI-oracle-r41-r42]` on `seat/oracle` (main merged at 90f194c)
- Branch: seat/oracle
- Clauses claimed: C2.3, C2.34 (R-41); C2.46–C2.53 (R-35 order); C1.18, C1.41, C2.46–C2.53, C2.58 (R-42)

## What changed
- R-41: the reference UI shows the lookup form on `/lookup` while signed out; only submitting a lookup navigates to
  `/login`, and after sign-in the diner is back on `/lookup` with that lookup run. `test_C2_3_C2_10_C2_15_screens_expose_testids_and_nav`
  now asserts the form is reachable signed out; `test_C2_30_booking_requires_sign_in` submits a lookup signed out,
  signs in, and expects `reservation-detail` on `/lookup`.
- R-35 order: "more than two ids → 422 combination_not_allowed" moved into the set value checks, before the restaurant
  lookup; a create with three ids against an unknown restaurant now answers 422 (the Auditor's seeds 1 and 905543).
- R-42: `restaurant_id`, `table_id` and every `table_ids` member must be 1..64 characters → 422 `validation_failed`
  before any 404 (inside `table_ids`: after both-fields and empty-set, before duplicates and the count rule); an
  empty moves `reference` → 422 (structure). Tests added for POST, PATCH and move items, including over-64 against an
  unknown restaurant and an over-64 member in a triple.

## Commands

### `python -m pytest stage-2/verify/oracle/test_acceptance.py stage-2/verify/oracle/test_hardening.py stage-2/verify/oracle/test_stage2_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 -p no:cacheprovider`
Exit code: 0
```
169 passed in 10.57s
```

### `python -m pytest stage-2/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18403 -p no:cacheprovider`
Exit code: 0
```
20 passed in 24.14s
```

### `python stage-2/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18404 --seed 1 --runs 40 --ops 80` and `--seed 905543 --runs 5`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 2.1s
OK: 5 runs, 405 ops, seeds 905543..905547, 0.3s
```

## Known gaps
- Model-vs-model only; the Auditor re-runs step 3 against the next candidate (WI-13 carries the product's R-42 fix).
