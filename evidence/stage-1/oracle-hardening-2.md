[EVIDENCE] stage=1 wi=O-5,O-6,O-7 sha=b39257a

# Evidence — Oracle test hardening from the candidate-2 mutation survivors

- Revision: see commit `[WI-O-5..O-7]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed: C1.107, C1.109 (O-5); C1.107 (O-6); C1.12, C1.28 (O-7)
- File: `stage-1/verify/oracle/test_hardening.py`

## What changed
- O-5 (`test_C1_107_C1_109_tampered_export_is_refused`): the tamper generator now yields 34 refused imports on the
  model's layout (was 15). New: reservation `reference` and `reservation_id` empty and 65 characters; restaurant
  `slot_minutes` 0 and −30, `reservation_duration_minutes` −1; a duplicate table id inside one restaurant; and, when the
  implementation's state contains a boolean field, that field holding the string "yes" (the model has none, so this case
  is generated only against an implementation that has one). Each → 422 `validation_failed`, destination snapshot
  unchanged; the threshold is now ≥ 25 applied cases.
- O-6 (`test_C1_107_round_trip_duration_one_and_nines`): restaurant with `reservation_duration_minutes` 1, slot 20,
  cutoff 9, `closes: "24:00"`, tables of capacity 9 and 99. Slots 23:00, 23:20, 23:40; a 23:40 booking ends 23:41;
  availability for party 10 lists only the 99-table. Export → cancel → import restores identical restaurant, list and
  availability (parties 1, 9, 10, 99); the receipt replays; the same state imports into a second instance.
- O-7 (`test_C1_28_C1_12_reset_rejects_degenerate_values`): reset with closes equal to opens, capacity 0 and −1,
  slot_minutes 0 and −30, duration 0 → 422 `validation_failed`; restaurants, lists and availability unchanged and the
  existing tokens still valid.

## Commands

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 53%]
...............................................................          [100%]
135 passed in 7.99s
```

## Known gaps
- Validated against the reference model; candidate 3 carries 3e387ec, not this commit.
