[EVIDENCE] stage=4 wi=oracle-candidate-4 sha=5fa6c9e

# Evidence — Oracle full kit against candidate 4 (2621e7a) with provenance-verified upgrade sources

- Revision: see commit `[WI-oracle-candidate-4]` on `seat/oracle` (kit at main 2621e7a + 94707a7)
- Branch: seat/oracle
- Images (named by the Auditor, built with `--no-cache` from its fresh clone at 2621e7a): candidate `auditor-s4-c4`
  (run as oracle-c4 on 18420 and oracle-c4b on 18421); sources `auditor-s4v4-src1` (stage-1/ = c0f2b7b, 18410),
  `auditor-s4v4-src2` (stage-2/ = aa63cd2, 18412), `auditor-s4v4-src3` (stage-3/ = c4a828e, 18415). Each source's export
  schema was re-checked before the run: 1, 2, 3; the candidate exports schema 4.
- Clauses covered: the whole kit — C1.1–C1.134, C2.1–C2.59, C3.1–C3.53, C4.1–C4.24 as mapped by clauses*.json, plus
  O-1…O-18 and the R-7x cases.

## Commands

### `python -m pytest stage-4/verify/oracle/test_acceptance.py stage-4/verify/oracle/test_hardening.py stage-4/verify/oracle/test_stage2_api.py stage-4/verify/oracle/test_stage3_api.py stage-4/verify/oracle/test_stage4_api.py -q --base-url http://127.0.0.1:18420 --stage1-base-url http://127.0.0.1:18410 --stage2-base-url http://127.0.0.1:18412 --stage3-base-url http://127.0.0.1:18415 -p no:cacheprovider`
Exit code: 0
```
246 passed, 1 skipped in 45.14s      (skipped: the two-instance import test, run separately below)
```
Includes the five image-backed upgrade tests (stage-3 suite: sources 1 and 2; stage-4 suite: sources 1, 2 and 3), the
first run of those with real provenance.

### `python -m pytest "stage-4/verify/oracle/test_acceptance.py::test_C1_107_import_into_second_instance" stage-4/verify/oracle/test_stage2_api.py -q --base-url http://127.0.0.1:18420 --second-base-url http://127.0.0.1:18421 -p no:cacheprovider`
Exit code: 0
```
20 passed, 1 skipped in 3.69s        (skipped: the stage-2 upgrade case without a stage-1 URL in this invocation; it ran in the full run above)
```

### `python -m pytest stage-4/verify/oracle/test_ui.py -q --ui --base-url http://127.0.0.1:18421 -p no:cacheprovider`
Exit code: 0
```
21 passed in 21.00s
```

### `python stage-4/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18420 --seed 1 --runs 60 --ops 80`
Exit code: 0
```
OK: 60 runs, 4860 ops, seeds 1..60, 34.2s
```

## Findings
- None. F3 (R-75, integer overflow) and F4 (R-77, null closure bound) from earlier candidates no longer reproduce:
  `test_O16_fixture_integers_at_2_pow_31` and `test_O17_import_refusals_closures_series_terms` pass against this image.

## Known gaps
- None for the Oracle kit against candidate 4.
