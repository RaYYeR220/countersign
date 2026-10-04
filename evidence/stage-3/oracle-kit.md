[EVIDENCE] stage=3 wi=oracle-kit sha=<filled at commit>

# Evidence — Oracle stage-3 verification kit (model, HTTP suite, runner, browser regression, O-12/O-13)

- Revision: see commit `[WI-oracle-kit-3]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed (entry-B ids, evidence/stage-3/ledger-B.md): B1, B3–B20, B22–B55, B56–B72, B74–B85; plus the
  stage-2 leftovers O-12 (C1.109/R-24) and O-13 (C1.34/R-3). Not machine-checked: B2 (scope), B21 (no new screens;
  the stage-2 browser suite is the regression), B73 (restaurant revision, unobservable — Q1).

## What was built (stage-3/verify/oracle/)
- `model.py`: `manager_user_ids`; policy publication (keyed, 404 → 403 → three-pass validation: booleans in integer
  fields 422, strings 400; versions dense per restaurant, none consumed by failures or replays), public policy list,
  selection by local start date (greatest effective_from ≤ date, ties → greatest version, else policy 0), the selected
  policy driving slots, duration, hours, capacities and summed pair capacity; `revision` and `accepted_terms` on every
  reservation response; history with dense `seq`, `created`/`changed`/`cancelled` entries carrying revision and
  terms (`table_id` for single-to-single, `table_ids` with full lists when a pair is involved); decision; history and
  decision answer 404 without a token; `expected_revision` (422 invalid, 409 stale, placed after cancelled and before
  cutoff); cancel checks the accepted cutoff and increments the revision once; real amendments re-select the policy for
  the resulting date, replace terms and end time, add one revision and one entry; no-ops change nothing; `explain=true`
  (exactly) with every table, both rules, `available` and `policy_version`; series adoption (fields 422 → 404 → cancelled →
  already_in_series → cutoff → occurrences in index order, first failure decides, nothing survives failure),
  `GET /series/{id}` (owner only, 404 otherwise), series revision and exception flags on PATCH/cancel/moves, replays;
  collective moves under policies with per-item `expected_revision`, one revision per changed booking, one per affected
  series, restaurant revision once per batch; export schema v3; import of v1 and v2 model states (records gain revision 1,
  policy-0 terms and a `created` entry; cancelled ones a `cancelled` entry at revision 2).
- `test_stage3_api.py` (40 tests incl. parametrised): everything above through HTTP, plus the upgrade from stage-1 and
  stage-2 exports (tokens, references, receipts replayed verbatim, adoption on an imported booking, re-export), O-12
  (11 import refusals) and O-13 (party_size 2^53 ± 1).
- `diff_runner.py`: ops for policy publication (managers and strangers, replays, invalid bodies), policy lists, history,
  decision, series adoption and reads, `explain`, `expected_revision` on PATCH and move items; history `at` masked,
  history/decision/series references and series ids aliased.
- `fixtures.py`: manager user `u_mia`, `manager_user_ids` on r_anker and r_trio, `policy()` helper, stage-1 and stage-2
  fixture shapes.

## Commands (model on 18400/18402/18403, stage-1 model 18411, stage-2 model 18413 as upgrade sources)

### `python -m pytest stage-3/verify/oracle/test_acceptance.py stage-3/verify/oracle/test_hardening.py stage-3/verify/oracle/test_stage2_api.py stage-3/verify/oracle/test_stage3_api.py -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 --stage1-base-url http://127.0.0.1:18411 --stage2-base-url http://127.0.0.1:18413 -p no:cacheprovider`
Exit code: 0
```
217 passed in 17.13s
```

### `python -m pytest stage-3/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18403 -p no:cacheprovider` (stage-2 browser regression)
Exit code: 0 (19 passed in the full run; the one failure was a null-unsafe wait in the test, fixed and re-run: 1 passed)
```
19 passed, then test_C2_34_lookup_and_cancel: 1 passed in 41.16s
```

### `python stage-3/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 11.4s
```
`op_stats.py --runs 20` (excerpt): policy 201/403/404/401, policies 200/404, history 200/404, decision 200/404,
series 201/401/404/409 cutoff_passed/409 reservation_cancelled/409 idempotency_key_reuse/422, patch 409 stale_revision.

### Accepted images as upgrade sources (informational)
`tablekeeper-stage1:latest` on 18410 and `tablekeeper-stage2:latest` on 18412 produce exports with `schema` 1 and 2
(collections receipts/reservations/restaurants/tokens/users). The model refuses them (it reads only its own formats),
as expected; against a candidate they are the real sources (`--stage1-base-url`, `--stage2-base-url`).

## Known gaps
- Validated against the reference model only; no stage-3 candidate yet.
- Q1–Q25 implemented with the stated defaults; Q4 (history `at` offset), Q8/Q20 (expected_revision position), Q9
  (policy POST order, booleans 422), Q13/Q14 (series body types and order) and Q19 (seeded/imported history) are the
  ones most likely to collide with the implementation.
