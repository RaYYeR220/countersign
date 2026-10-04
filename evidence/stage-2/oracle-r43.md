[EVIDENCE] stage=2 wi=oracle-r43 sha=<filled at commit>

# Evidence — R-43 applied: both table fields present → 422 before the type pass

- Revision: see commit `[WI-oracle-r43]` on `seat/oracle` (main merged for R-43)
- Branch: seat/oracle
- Clauses claimed: C2.47, C1.34

## What changed
- `model.py`: a `_both_table_fields` check runs before R-19's type pass on POST /reservations, PATCH and every move
  item (per item, in input order): both `table_id` and `table_ids` present → 422 `validation_failed` whatever their
  JSON types. With a single field present a wrong type stays 400 (`table_ids` not an array of strings; `table_id`
  not a string).
- `test_stage2_api.py::test_C2_47_table_id_and_table_ids`: five both-field type combinations on POST (object, number,
  null, string, array/number), one on PATCH, one on a move item → 422; single-field wrong types on POST, PATCH and a
  move item → 400.

## Commands

### `python -m pytest stage-2/verify/oracle/test_acceptance.py stage-2/verify/oracle/test_hardening.py stage-2/verify/oracle/test_stage2_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 -p no:cacheprovider`
Exit code: 0 (168 passed on the first run with one stale count in the new test; the module re-run after the fix: 20 passed)
```
20 passed in 0.57s   (test_stage2_api.py, re-run)
```

### `python stage-2/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18404 --seed 1 --runs 40 --ops 80` and `--seed 371951 --runs 5`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 2.0s
OK: 5 runs, 405 ops, seeds 371951..371955, 0.2s
```

## Known gaps
- Model-vs-model; the Auditor re-runs step 3 against candidate 2 with seed 371951.
