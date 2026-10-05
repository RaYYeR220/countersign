[EVIDENCE] stage=4 wi=O-17,O-18 sha=98a46fa

# Evidence — O-17/O-18 tests; full kit against the model and against candidate 3 (auditor-s4-c3, b26757c)

- Revision: see commit `[WI-O-17-O-18]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed: C1.109/R-24/R-72 (O-17), C3.23/R-47 (O-18)
- Candidate under test: image `auditor-s4-c3` (sha256:b1f40d67…, built by the Auditor from b26757c), run as `oracle-c3`
  on 18420 and `oracle-c3b` on 18421.
- Upgrade sources available to me: none built from the accepted folders. The local tags were probed: `tablekeeper-stage1`
  behaves as stage 2 (schema-2 export), `tablekeeper-stage2` and `tablekeeper-stage3` behave as stage 3 (schema 3).
  The upgrade tests therefore ran with a schema-2 and a schema-3 source of unverified provenance and no stage-1 source.

## New tests (stage-4/verify/oracle/test_stage4_api.py)
- `test_O17_import_refusals_closures_series_terms`: closure records (every copy carrying `plan_id`, wherever the layout
  keeps them) with `from` after `to`, `from` equal to `to`, `from` null, `to` null; series owner unknown; a booking's
  `accepted_terms.cancellation_cutoff_minutes` 10081 under a published policy — each 422 with the destination unchanged.
- `test_O18_policy_capacity_non_integral`: capacities 4.5 and 0.5 → 422 with no version allocated; 4.0 publishes as 4.

## Against the model
`python -m pytest stage-4/verify/oracle/test_stage4_api.py::test_O17_… ::test_O18_… -q --base-url http://127.0.0.1:18400 -p no:cacheprovider`
Exit code: 0
```
2 passed in 0.32s
```
(and the stage-3/stage-4 modules: 73 passed, 5 image-sourced upgrade cases skipped)

## Against candidate 3
### HTTP kit, no upgrade sources (`--base-url http://127.0.0.1:18420`)
Exit code: 1
```
1 failed, 239 passed, 7 skipped in 44.44s        (the failure was O-17's locator, fixed below)
```
### O-17/O-18 re-run after the locator fix (`--base-url http://127.0.0.1:18420`)
Exit code: 1
```
test_O17_import_refusals_closures_series_terms FAILED ('closure from null', Resp(204, ''))
test_O18_policy_capacity_non_integral PASSED
```
### Upgrade tests with the probed sources (`--base-url http://127.0.0.1:18421 --stage2-base-url http://127.0.0.1:18410 --stage3-base-url http://127.0.0.1:18415`)
Exit code: 0
```
3 passed, 2 skipped in 1.12s        (stage-1 source: none)
```
### `python -m pytest stage-4/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18421 -p no:cacheprovider`
Exit code: 0
```
21 passed in 19.74s
```
### `python stage-4/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18420 --seed 1 --runs 50 --ops 80`
Exit code: 0
```
OK: 50 runs, 4050 ops, seeds 1..50, 28.9s
```

## Finding F4 — C1.109 / R-24 / R-72: an applied closure with a null `from` imports and becomes unbounded
- Request: reset (base fixture); book `r_trio q_2 2027-09-24T19:00` party 3; preview and apply a closure of q_2 over
  `[2027-09-24T18:00:00+02:00, 2027-09-24T23:00:00+02:00)`; export; set `from` to null on every closure record (the
  restaurant's `closures[0]` and the plan's `closure`, both carrying the plan_id); `POST /_test/import`.
- Expected: 422 `validation_failed`, destination unchanged (a closure needs both bounds; R-72 "structurally invalid").
- Actual: 204. Afterwards `GET /availability` for 2027-09-24 still excludes q_2; a booking on q_2 at 20:00 → 409
  (consistent) but the re-export shows the closure as `{"from": "0001-01-01T00:00:00Z", "to": "2027-09-24T23:00:00+02:00"}`:
  the table is now closed from the zero time until `to`, which no preview could have produced. Setting `to` to null is
  refused with 422 ("invalid state: restaurant r_trio …"), so the two bounds are validated asymmetrically.
- Reproduce: `python -m pytest "stage-4/verify/oracle/test_stage4_api.py::test_O17_import_refusals_closures_series_terms" -q --base-url http://127.0.0.1:<candidate> -p no:cacheprovider` (case "closure from null"); the hand reproduction is in this packet.

## Known gaps
- The upgrade tests against candidate 3 used sources of unverified provenance (no stage-1 source); a run with images
  built from c0f2b7b, aa63cd2 and c4a828e, named by the Auditor, is still owed.
