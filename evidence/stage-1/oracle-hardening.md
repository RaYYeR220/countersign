[EVIDENCE] stage=1 wi=O-1,O-2,O-3,O-4 sha=35669bd

# Evidence — Oracle test hardening from the candidate-1 mutation survivors

- Revision: see commit `[WI-O-1..O-4]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed: C1.89 (O-1), C1.107, C1.109 (O-2), C1.107, C1.18, C1.28 (O-3), C1.3, C1.46 (O-4)
- File: `stage-1/verify/oracle/test_hardening.py` (5 tests), plus R-24 validation inside the model's import so the
  model itself refuses every tampered state the new test produces.

## What each test does
- O-1 `test_C1_89_tie_order_seeded_created_at`: three seeded bookings at one instant on three tables; two share
  `created_at`, the third is 1 s later and has the lexicographically smallest reference (`AAAAA1`); the list must read
  `MMMMM1, ZZZZZ1, AAAAA1` (created_at ascending, then reference ascending). `test_C1_89_tie_order_live_created_at`:
  five live bookings at two instants, created ≥ 1.1 s apart; asserts created_at ascending within each tie and, for
  any pair whose later booking has the smaller reference, that created_at still wins.
- O-2 `test_C1_107_C1_109_tampered_export_is_refused`: takes the real export and derives ≥ 15 tampered copies by
  locating records structurally (the seeded reservation by `reference`, the restaurant by `id`, the user by `email`,
  whatever the implementation's layout): duplicate reservation record, duplicate reference, duplicate id, each of the
  three collections removed, invalid restaurant (timezone, slot_minutes −5, duration 0, name 5, tables "none",
  capacity 0), invalid reservation (party_size −1 and "4", status, starts_at_local, table_id, user_id, restaurant_id,
  reference), collections replaced by `true`/`7`, email as a number, reservation id as a boolean. Each → 422
  `validation_failed` with the destination snapshot unchanged; the untouched export then imports and a receipt replays.
- O-3 `test_C1_107_C1_18_round_trip_edge_values`: a 64-character restaurant, table and user id, `closes: "24:00"`
  (slots 22:00, 22:30, 23:00; 23:00 booking ends 2027-06-16T00:00:00+02:00), a capacity-1 table, cutoff 0; export,
  mutate, import → observable state identical, receipt replays, repeated import idempotent; also into the second
  instance when `--second-base-url` is given.
- O-4 `test_C1_3_C1_46_reads_consistent_during_write_burst`: three static bookings never touched by writers; four
  rounds of 48 barrier-released requests (24 create/cancel/PATCH by four writers, 24 availability/list/get reads).
  Every response < 500; every availability response has the exact slot grid, table ids ⊆ fixture, no duplicates, and
  shows no table available on any slot overlapping the static bookings; every list/get returns the static bookings
  unchanged with a valid shape. Afterwards availability equals the union of all confirmed bookings and no two confirmed
  bookings overlap on a table.

## Commands

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 56%]
........................................................                 [100%]
128 passed in 7.44s
```

### `python stage-1/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 1.4s
```

## Known gaps
- Validated against the reference model; not yet run by me against candidate 2 (4f54736).
- O-2 locates records by content; if an implementation's export nests the seeded reservation, restaurant or user in a
  form where none of the three can be found, the test fails with "could not locate …" rather than passing vacuously.
