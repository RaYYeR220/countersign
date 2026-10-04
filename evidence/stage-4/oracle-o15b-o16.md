[EVIDENCE] stage=4 wi=O-15b,O-16 sha=<filled at commit>

# Evidence — O-15b and O-16 tests; full kit against the model and against the local stage-4 product image

- Revision: see commit `[WI-O-15b-O-16]` on `seat/oracle` (main merged at ca5834d)
- Branch: seat/oracle
- Clauses claimed: C1.109/R-24 (O-15b), C1.26/C1.27/R-8 (O-16)
- Product under test: the local image `tablekeeper-stage4:latest` (created 2026-10-04T22:48:38Z, no provenance label;
  built by another seat, not by me). Its commit SHA is not recorded in the image; the Foreman/Auditor can match the
  creation time to a candidate. Run twice on my ports (18420, 18421) with the accepted stage-1/2/3 images (18410/18412/18415)
  as upgrade sources.

## New tests (stage-4/verify/oracle/test_stage4_api.py)
- `test_O15b_import_refusals`: exactly one of the restaurants/reservations collections set to null; a user id empty and
  65 characters; a restaurant whose `tables` is null; a history entry whose `changes` is null — each 422 with the
  destination (restaurants, lists, availability, SEED01 history) unchanged; the untouched export then imports.
- `test_O16_fixture_integers_at_2_pow_31`: capacity 2^31−1 → 204, availability and a booking of that party size work,
  a smaller table refuses it; policy-0 `reservation_duration_minutes` / `slot_minutes` / `cancellation_cutoff_minutes`
  2^31−1 → 204 with an empty slot list and a 422 `outside_opening_hours` booking; the same values in a published policy
  → 422 (stated ranges); a seeded party_size 2^31−1 → 204 and echoed.

## Results against the model
`python -m pytest stage-4/verify/oracle/test_acceptance.py … test_stage4_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 --stage2-base-url http://127.0.0.1:18413 --stage3-base-url http://127.0.0.1:18414 -p no:cacheprovider`
Exit code: 0
```
245 passed in 11.58s
```

## Results against the product image
### HTTP kit (same command with `--base-url http://127.0.0.1:18420 --second-base-url http://127.0.0.1:18421 --stage1-base-url http://127.0.0.1:18410 --stage2-base-url http://127.0.0.1:18412 --stage3-base-url http://127.0.0.1:18415`)
Exit code: 1
```
3 failed, 242 passed in 44.47s
FAILED test_stage3_api.py::test_C3_48_upgrade_from_older_exports[2]
FAILED test_stage3_api.py::test_C3_28_O14_tampered_stage3_records_are_refused
FAILED test_stage4_api.py::test_O16_fixture_integers_at_2_pow_31
```
### `python -m pytest stage-4/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18421 -p no:cacheprovider`
Exit code: 0
```
21 passed in 23.83s
```
### `python stage-4/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18420 --seed 1 --runs 50 --ops 80`
Exit code: 0
```
OK: 50 runs, 4050 ops, seeds 1..50, 29.5s
```

## Findings (reproduced by hand against the product)
### F1 — C3.48 / R-55: a schema-2 import gives a cancelled booking a `cancelled` history entry
- Request: stage-2 service — reset, book `r_all a_2 2027-06-15T14:00` party 2, cancel it, export; stage-4 — `POST /_test/import` with that export (204), then `GET /reservations/{reference}/history` and `/decision` as the owner.
- Expected (R-55: "Migrated bookings get revision 1 (whatever their status), policy-0 terms and the R-51 history"): `entries` = `[created]`, revision 1.
- Actual: `[(1, created, revision 1), (2, cancelled, revision 2)]`, decision revision 2. The same booking imported from a stage-1 export gives `[created]`, revision 1 — the schema-2 path differs from the schema-1 path.
- Reproduce: `python -m pytest "stage-4/verify/oracle/test_stage3_api.py::test_C3_48_upgrade_from_older_exports" -q --base-url http://127.0.0.1:<stage4> --stage1-base-url http://127.0.0.1:<stage1> --stage2-base-url http://127.0.0.1:<stage2> -p no:cacheprovider`

### F3 — C1.22 / C1.74 / C1.4 / C1.23 / R-8 (O-16 target): policy-0 integers near 2^31 overflow
- Request: reset with `r_anker.reservation_duration_minutes = 2147483647` (also `slot_minutes` and `cancellation_cutoff_minutes` at that value); `GET /availability?restaurant_id=r_anker&date=2027-09-24&party_size=2`; `POST /reservations` t_1 at `2027-09-24T18:00`.
- Expected: reset 204 (no stated upper bound) and then no slot fits before closing (`slots: []`), a booking → 422 `outside_opening_hours`; with only the cutoff at 2^31−1, a cancel one year ahead → 409 `cutoff_passed`.
- Actual: reset 204; one slot offered at 18:00; the booking → 201 with `"ends_at": "2018-11-30T22:05:04+01:00"` (nine years before `starts_at`); the slot remains available after the booking (occupancy with a negative interval); with duration 2^31−1 alone, all 11 slots are offered; with cutoff 2^31−1 alone, cancel → 200. Duration 100000 behaves correctly (0 slots), so the fault starts between 10^5 and 2^31−1 minutes (consistent with an int64-nanosecond overflow at ≈1.5×10^8 minutes).
- Reproduce: `python -m pytest "stage-4/verify/oracle/test_stage4_api.py::test_O16_fixture_integers_at_2_pow_31" -q --base-url http://127.0.0.1:<stage4> -p no:cacheprovider`

### F2 — C1.109 / R-24 / R-51 (needs a ruling): a history whose first entry is `changed` imports
- Request: real export; the seeded reservation's history entry 1 `event` set from `created` to `changed` (everything else intact); `POST /_test/import`.
- Expected by my model (R-51 "entries: created first"; R-24 "structurally invalid state"): 422, destination unchanged.
- Actual: 204; the history now reads `[(1, changed)]`.
- This was one of my O-14 structural cases; no ruling states that an imported history must begin with `created`. I report it for a ruling rather than as a defect; if R-24 is read as covering it, the reproduction is `test_C3_28_O14_tampered_stage3_records_are_refused`.

## Known gaps
- The product image's commit SHA is unknown to me (no label); findings are against "the local tablekeeper-stage4:latest of 2026-10-04T22:48Z".
