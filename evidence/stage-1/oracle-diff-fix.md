[EVIDENCE] stage=1 wi=oracle-diff-fix sha=995058b

# Evidence — differential runner classes A and B on candidate 2 (model/runner side)

- Revision: see commit `[WI-oracle-diff-fix]` on `seat/oracle` (main merged for R-25)
- Branch: seat/oracle
- Clauses claimed: C1.81, C1.112 (A); C1.38, C1.90, R-25 (B)

## Fixes
- (A) `model.py`: reservation ids, references and user ids now come from a process-wide sequence that no reset or
  import restores, so a reference or id of a discarded booking is never reissued and the runner's labels cannot be
  remapped onto a different booking. The exported `counters` stay in the state for information only.
- (B) `model.py`: paths are taken literally (R-25): no dropping of empty segments; a trailing slash, doubled slash or
  empty `{id}`/`{reference}` is an unknown path → 404 `not_found`. `serve_model.py` now reads the raw request target
  from the request line, because `BaseHTTPRequestHandler` collapses a leading `//` before the handler runs.
- `test_acceptance.py::test_C1_38_C1_32_unknown_route_and_method` extended with eleven non-canonical paths.
- The runner keeps generating empty and odd references (`""`, `zzz`); both sides now answer 404 for them.

## Commands

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 56%]
........................................................                 [100%]
128 passed in 7.62s
```

### `python stage-1/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 1.5s
```

### `python stage-1/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 620154 --runs 5 --ops 80`
Exit code: 0
```
OK: 5 runs, 405 ops, seeds 620154..620158, 0.2s
```

## Known gaps
- Both runs are model-vs-model; the Auditor re-runs step 3 against candidate 2 with seed 1 plus a fresh seed.
