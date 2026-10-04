[EVIDENCE] stage=1 wi=oracle-r27 sha=0d5f337

# Evidence — R-27 applied: tie-order test compares created_at only within equal starts_at

- Revision: see commit `[WI-oracle-r27]` on `seat/oracle` (main merged for R-27)
- Branch: seat/oracle
- Clauses claimed: C1.89 (test_hardening.py::test_C1_89_tie_order_live_created_at)

## What changed
- The inversion check (a later-created booking with a lexicographically smaller reference must still sort after the
  earlier one) now considers only consecutive pairs with equal `starts_at`. The pair across the two instants
  (FUT_DAY 15:00 vs FUT_FRI 19:00) is ordered by `starts_at` descending per C1.89 and is no longer compared.
- The within-group assertions (created_at ascending inside each tie, groups in starts_at-descending order) are unchanged.

## Commands

### `python -m pytest "stage-1/verify/oracle/test_hardening.py::test_C1_89_tie_order_live_created_at" -q --base-url http://127.0.0.1:18400 -p no:cacheprovider` × 5
Exit code: 0 each time
```
1 passed in 5.63s
1 passed in 5.53s
1 passed in 5.54s
1 passed in 5.55s
1 passed in 5.55s
```

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 -p no:cacheprovider`
Exit code: 0
```
135 passed, 1 skipped in 7.81s     (skipped: second-instance import test, no --second-base-url in this run)
```

## Known gaps
- The model issues monotonic references, so the inversion branch is exercised only against an implementation with
  non-monotonic references (the candidate), where it now compares equal-instant pairs only.
