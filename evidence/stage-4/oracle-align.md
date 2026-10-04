[EVIDENCE] stage=4 wi=oracle-align sha=0f81875

# Evidence — Oracle kit aligned with the stage-4 master ledger and rulings R-61 … R-73

- Revision: see commit `[WI-oracle-align-4]` on `seat/oracle` (main merged at dea16f5)
- Branch: seat/oracle
- Clauses claimed: C4.1–C4.24 as mapped from entry B (clauses4.json); tests relabelled to `C4_<n>`.

## What changed
- R-62 (Q1/Q2 overruled): `from`/`to` accept `Z` and fractional seconds; every `from`/`to` problem — missing, wrong
  JSON type, no offset, unparsable, `from >= to` — is 422 `validation_failed`. `table_id` keeps its rules (non-string
  400, missing/empty/over-64 422). The closure in responses stays in the restaurant's zone, whole seconds, never `Z`.
  Test: a preview with `…Z` and `….500+02:00` instants → 201 with the closure echoed as `+02:00`; eight bad instant
  shapes → 422.
- R-73: a second closure on an already-closed table is planned and applied like any other; tested (bob's later
  booking on that table moves, and the table is then blocked over the new interval too).
- R-61, R-63–R-72: already the model's behaviour; confirmed by the existing tests.

## Commands (model 18400/18402/18403; stage-1/2/3 models 18411/18413/18414 as upgrade sources)

### `python -m pytest stage-4/verify/oracle/test_acceptance.py stage-4/verify/oracle/test_hardening.py stage-4/verify/oracle/test_stage2_api.py stage-4/verify/oracle/test_stage3_api.py stage-4/verify/oracle/test_stage4_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 --stage2-base-url http://127.0.0.1:18413 --stage3-base-url http://127.0.0.1:18414 -p no:cacheprovider`
Exit code: 0
```
243 passed in 11.03s
```

### `python -m pytest stage-4/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18403 -p no:cacheprovider`
Exit code: 0
```
21 passed in 14.76s
```

### `python stage-4/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 1.6s
```

## Known gaps
- Validated against the reference model; no stage-4 candidate yet.
