[EVIDENCE] stage=3 wi=oracle-align sha=<filled at commit>

# Evidence — Oracle kit aligned with the stage-3 master ledger and rulings R-46 … R-59

- Revision: see commit `[WI-oracle-align-3]` on `seat/oracle` (main merged at 2cc1afc)
- Branch: seat/oracle
- Clauses claimed: C3.1–C3.53 as mapped from entry B (clauses3.json); tests relabelled to `C3_<n>`.

## What changed
- R-49 / R-57 (Q8/Q20 overruled): `expected_revision` is judged right after the 404: 422 when invalid, 409
  `stale_revision` when it differs, and only then 409 `reservation_cancelled` and 409 `cutoff_passed`, on PATCH and per
  move item. Tests: stale on a cancelled booking → `stale_revision`; a matching revision on a cancelled booking →
  `reservation_cancelled`; a stale revision refuses even a no-op (P5); cancel ignores `expected_revision` (R-50).
- R-47 (Q9 overruled): every policy field problem is 422 — strings, null and booleans in integer fields, wrong types
  for `effective_from`, `opening_hours` (and its items) and `capacities`, bad times, closes ≤ opens, duplicate
  weekdays, wrong table set, capacities outside 1..100; `30.0` counts as 30 (R-3); only a body that is not a JSON
  object is 400. The parametrised validation test grew to 27 cases.
- R-51 / R-55 (Q19 overruled): seeded and imported bookings have exactly one `created` entry and revision 1 whatever
  their status; `at` equals `created_at` in the restaurant's zone. New test for a cancelled seed; the upgrade test
  expects `[created]` and revision 1 for the imported cancelled booking.
- R-54: the model keeps the internal restaurant revision (new booking, real amendment, cancellation, publication,
  adoption, move batch); not asserted anywhere (unobservable).
- R-59: manager ids that are empty, over 64 characters, unknown or duplicated → 422; not strings → 400. Tested.
- R-46, R-48, R-52, R-53, R-56, R-58: already the model's behaviour; confirmed by the existing tests.

## Commands (model 18400/18402/18403, stage-1 model 18411 and stage-2 model 18413 as upgrade sources)

### `python -m pytest stage-3/verify/oracle/test_acceptance.py stage-3/verify/oracle/test_hardening.py stage-3/verify/oracle/test_stage2_api.py stage-3/verify/oracle/test_stage3_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 --stage2-base-url http://127.0.0.1:18413 -p no:cacheprovider`
Exit code: 0
```
225 passed in 13.97s
```

### `python -m pytest stage-3/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18403 -p no:cacheprovider`
Exit code: 0
```
20 passed in 28.53s
```

### `python stage-3/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 2.6s
```

## Known gaps
- Validated against the reference model; no stage-3 candidate yet.
