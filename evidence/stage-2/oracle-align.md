[EVIDENCE] stage=2 wi=oracle-align sha=ea0bea8

# Evidence — Oracle kit aligned with the stage-2 master ledger and rulings R-29 … R-40

- Revision: see commit `[WI-oracle-align-2]` on `seat/oracle` (main merged at 6c768ee)
- Branch: seat/oracle
- Clauses claimed: C2.1–C2.59 as mapped from entry B (clauses2.json); tests relabelled to `C2_<n>`.

## What changed
- R-34 (C2.40, C2.43): `combinable` entries that are not two distinct strings naming tables of that restaurant, or that
  repeat a pair in either order → 422; `combinable` itself not an array → 400; absent → `[]`. A seeded set must be one
  table or a declared pair, on reset and on import (Q8 overruled). Seeded `status` only `confirmed`/`cancelled`.
- R-35 (C2.46–C2.53): more than two ids → `combination_not_allowed` before the 404 for an unknown member; the undeclared
  pair after 404 and before the time rules; everything else as before (declared order in responses, replay with the
  other order → 409 reuse).
- R-39 (C2.55, C2.28): the reference UI renders a cell for every declared pair whose summed capacity ≥ the party size,
  `data-available` true iff the pair is in `available_options`; no cell below the party size; a false pair cell does
  nothing. The browser test checks all of that against the API, including a busy member.
- R-40 (C2.30, C2.34): signed-out cell click → `/login`; after sign-in back on `/` with the search and selection restored
  and the form open; `/lookup` signed out → `/login` → back to `/lookup`. Tested.
- R-29: absence assertions for `no-slots`/`availability-grid`, `current-user`/`logout-button`, `reservation-*`.
- R-30: the reference UI keeps the session in `localStorage`. R-32: summary and details carry `HH:MM`, the ISO date and a
  weekday/day/month form; tested. R-38: 5xx and network failures → `booking-uncertain`, key kept.
- R-37: unchanged (envelope `format_version: 1`, inner schema distinguishes versions); the upgrade test already covers it.

## Commands

### `python -m pytest stage-2/verify/oracle/test_acceptance.py stage-2/verify/oracle/test_hardening.py stage-2/verify/oracle/test_stage2_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 42%]
........................................................................ [ 85%]
.........................                                                [100%]
169 passed in 10.07s
```

### `python -m pytest stage-2/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18403 -p no:cacheprovider`
Exit code: 0
```
....................                                                     [100%]
20 passed in 24.95s
```

### `python stage-2/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18404 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 2.2s
```

## Known gaps
- Validated against the reference model and reference UI; no stage-2 candidate yet.
