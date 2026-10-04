[EVIDENCE] stage=3 wi=O-14 sha=c3a3f77

# Evidence — O-14: tampered stage-3 records are refused on import (C1.109 / R-24)

- Revision: see commit `[WI-O-14]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed: C1.109 (R-24), C3.28 (import of stage-3 state)

## What changed
- `test_stage3_api.py::test_C3_28_O14_tampered_stage3_records_are_refused` with a generator `_stage3_tampers` that
  locates records structurally in a real export (restaurant by id, reservations by reference, the policies list by a
  `policy_version` member, terms by `policy_version`+`capacities`, history by `seq`, the series by `occurrences`) and
  yields 36 tampers on the model's layout:
  - policies: version gap, duplicate version, slot_minutes 0, capacities naming an unknown table, invalid
    effective_from, duplicate weekday, cutoff as a string;
  - revisions/terms: revision 0 / "2" / −1; terms missing capacities, policy_version beyond the published ones or a
    string, capacities for the wrong tables, duration 0, terms not an object;
  - history: empty, seq gap, seq duplicate, bad event, bad `at`, changes not a list, entry revision above the record's,
    first entry not `created`, missing accepted_terms, history not a list;
  - series: revision 0, interval 5, occurrence naming an unknown reservation, index gap, exception not boolean,
    duplicated occurrence, unknown user, empty id, occurrences not a list, a reservation claiming an unknown series.
  Each → 422 `validation_failed` with restaurants, lists, availability, history, series and policies unchanged; the
  untouched export then imports and the series reads back identical. Threshold ≥ 25 applied.
- `model.py` import validation extended accordingly: terms fields and ranges, policy_version ≤ published count,
  capacities for exactly the restaurant's tables; history dense, first `created`, nothing after `cancelled`, entry
  revisions non-decreasing and ≤ the record's, last entry revision = record revision, every entry with full terms;
  series ids 1..64 chars, no duplicated occurrence, and a record's `series_id` must point at a series listing it.

## Commands

### `python -m pytest "stage-3/verify/oracle/test_stage3_api.py::test_C3_28_O14_tampered_stage3_records_are_refused" -q --base-url http://127.0.0.1:18400 -p no:cacheprovider`
Exit code: 0
```
1 passed in 2.00s      (in-process check: 36 tampers; not refused: [])
```

### `python -m pytest stage-3/verify/oracle/test_acceptance.py stage-3/verify/oracle/test_hardening.py stage-3/verify/oracle/test_stage2_api.py stage-3/verify/oracle/test_stage3_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 --stage2-base-url http://127.0.0.1:18413 -p no:cacheprovider`
Exit code: 0
```
226 passed in 31.60s
```

### `python stage-3/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 2.9s
```

## Known gaps
- The Auditor's verdict-1 survivor list lives on the Auditor's branch, which my guard refuses to read; the 36 cases
  above are my own coverage of the four record kinds named in the finding. If any of the four survivors is not among
  them, the Foreman relays its description and I add it.
