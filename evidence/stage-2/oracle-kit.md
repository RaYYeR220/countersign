[EVIDENCE] stage=2 wi=oracle-kit sha=<filled at commit>

# Evidence — Oracle stage-2 verification kit (model, HTTP suite, browser suite, differential runner)

- Revision: see commit `[WI-oracle-kit-2]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed (entry-B ids, evidence/stage-2/ledger-B.md): B1–B5, B7–B15, B17, B18, B20, B21, B23–B27, B29–B55,
  B57–B87; plus the stage-1 leftovers O-9, O-10, O-11 (C1.34, C1.109, R-8, R-24). Not machine-checked: B6, B16, B19, B22,
  B28, B56 (qualitative or "not required").

## What was built (stage-2/verify/oracle/)
- `model.py`: `combinable` pairs on reset (pairs of distinct tables of the restaurant, else 422; wrong types 400); seeded
  `status` and `table_ids`; `table_ids` on POST/PATCH/moves with the pair rules (`combination_not_allowed` for an
  unlisted pair or more than two, duplicates 422, both fields 422, summed capacity, occupancy on every member, declared
  order in responses, `table_id` only for singles); `available_options`; stage-1 state (schema v1) migrates on import;
  receipts with a non-2xx status refused (O-9).
- `test_stage2_api.py` (18 tests): fixture and seeds, options ordering and filtering, pair booking and occupancy, every
  stage-2 error, PATCH/cancel with pairs, moves with `table_ids` (including overlapping result sets and swaps), a
  40-request burst of pairs and singles sharing a table, the upgrade from a real stage-1 export (tokens, references,
  receipts, failed keys, moves receipts, re-export), O-9 (6 import refusals), O-10 (null bodies, null items, huge
  party_size), O-11 (empty ids in four places).
- `test_ui.py` (20 tests, Playwright headless Chromium, `--ui`): routes return HTML, every testid table, nav consistency,
  signup/login/logout/auth-error, grid cells equal the API for the searched party size, `no-slots`, pair cells with
  labels, states visually distinct, booking flow with confirmation and unchanged resubmit, changed field = new booking,
  sign-in required, pair labels on confirmation and lookup, lookup/cancel/refusal, out-of-order searches (route
  interception), 409 after the form opens, lost response → `booking-uncertain` → retry with the same key and body →
  original reference, lost request then rejection, browser survives export/import between requests, no horizontal
  scroll at 375/768/1280 with labelled inputs and visible focus.
- `ui.html` + `serve_model.py`: a minimal reference UI on the four routes so the browser suite is proven end to end.
- `diff_runner.py`: `table_ids` bodies (declared pairs, reversed pairs, unlisted pairs, triples, duplicates, odd
  types, both fields) on create, PATCH and move items; a fourth restaurant `r_trio` with two pairs sharing a table.
- `fixtures.py`: `r_anker` keeps its stage-1 shape plus the pair `[t_1, t_2]`; `r_trio` (three tables, two pairs) is the
  stage-2 pair restaurant; `stage1_fixture()` strips `combinable` to prove the stage-1 shape still resets.

## Commands

### stage-1 regression + hardening + stage-2 API (model on 18400/18402, stage-1 model on 18411 as upgrade source)
`python -m pytest stage-2/verify/oracle/test_acceptance.py stage-2/verify/oracle/test_hardening.py stage-2/verify/oracle/test_stage2_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 42%]
........................................................................ [ 85%]
.........................                                                [100%]
169 passed in 8.82s
```

### browser suite against the reference UI
`python -m pytest stage-2/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18403 -p no:cacheprovider`
Exit code: 0
```
....................                                                     [100%]
20 passed in 16.58s
```

### differential baseline
`python stage-2/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18404 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 1.7s
```
`op_stats.py --runs 30` (excerpt): create 201 ×193, combination_not_allowed ×29, party_exceeds_capacity ×25, moves 201 ×10.

### accepted stage-1 image (tablekeeper-stage1:latest, created 2026-10-04T16:00Z) on 18410
Its export (`schema: 1`, collections receipts/reservations/restaurants/tokens/users) is the input for the upgrade test
against a candidate; against the model the source is the stage-1 model, since the model reads only its own format.

## Known gaps
- Validated against the reference model and reference UI only; no stage-2 candidate exists yet.
- Q1–Q13 (ledger-B) are implemented with the stated defaults; the moves/PATCH position of `combination_not_allowed`
  (Q3) and the response order of pair ids (Q5) are the ones most likely to need re-pointing after the reconcile.
- Qualitative direction clauses (B19, B22, B28) are for screenshot review, not this kit.
