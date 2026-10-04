[EVIDENCE] stage=1 wi=oracle-suite sha=<filled at commit>

# Evidence — Oracle acceptance suite, stage 1

- Revision: see commit `[WI-oracle-suite]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed (ledger-B ids; C1.<n> mapping follows the master ledger): B6–B9, B26–B39, B41–B59, B61–B70, B72–B99, B100–B142, B143–B159, B161–B176

## Commands

### `python factory/tools/bg.py start --name oracle-model --health http://127.0.0.1:18400/health -- python stage-1/verify/oracle/serve_model.py --port 18400`
Exit code: 0
```
{"name": "oracle-model", "healthy": true}
```

### `python factory/tools/bg.py start --name oracle-model2 --health http://127.0.0.1:18402/health -- python stage-1/verify/oracle/serve_model.py --port 18402`
Exit code: 0
```
{"name": "oracle-model2", "healthy": true}
```

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 67%]
..................................                                       [100%]
106 passed in 3.86s
```

## Known gaps
- The suite has been validated against the reference model only; no candidate exists yet. A pass against the model proves the suite and the model agree on the ledger-B reading, not that the implementation does.
- B10–B25 (delivery, image, resource limits, 60 s start) are exercised by starting the candidate image, not by this suite; B1, B5, B11–B13, B40, B60, B71, B144, B160, B177 have no HTTP-observable check.
- Precedence questions Q1–Q10 (ledger-B) are implemented with the stated defaults; tests that depend on them: Q1 (`test_B57_B66_B68_malformed_request` sends auth + bad body together only where a single error applies), Q2/Q3 (`test_B132_B135`, `test_B170`), Q4 (`test_B133`, `test_B168`), Q5 (`test_B163_moves_shape`), Q9 (`test_B64_B67`). They will be re-pointed after the `[RECONCILE]` rulings.
- `display_name` is treated as required on signup (`test_B63_B111_missing_fields`); the specification shows it in the request but does not say "required". Flagged for ruling.
