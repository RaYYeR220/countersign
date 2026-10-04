[EVIDENCE] stage=1 wi=oracle-diff sha=16ca596

# Evidence — Oracle differential runner, stage 1

- Revision: see commit `[WI-oracle-diff]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed (ledger-B ids): the runner exercises B6–B9, B29–B33, B36, B51–B54, B55–B70, B73–B79, B84–B98, B100–B142, B143–B159, B161–B176 by random sequences; it is a second net under the acceptance suite, not a replacement.

## Commands

### `python stage-1/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18400 --seed 1 --runs 40 --ops 80` (model served over HTTP vs in-process model)
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 15.8s
```

### `python stage-1/verify/oracle/op_stats.py` (outcomes reached by seeds 1..30, 80 ops each; excerpt)
Exit code: 0
```
   198  create        201
    18  create        200            (replays)
    15  create        409 idempotency_key_reuse
    16  create        409 table_unavailable
    11  create        422 invalid_local_time
    26  create        422 not_on_slot_grid
    55  cancel        200     35  cancel  409 cutoff_passed
    43  patch         200     25  patch   409 reservation_cancelled
     6  moves         201     15  moves   409 cutoff_passed    144  moves  422 validation_failed
```

### Self-test: `serve_model.py --mutant N` on 18403..18406, then `diff_runner.py --base-url http://127.0.0.1:<port> --seed 1 --runs 30 --ops 80`
Exit code: 1 for every mutant (a mismatch is the expected result)
```
mutant 1 (fall-back resolves to the second occurrence): MISMATCH seed=4 at op 31/81, shrunk to 3 ops
   create r_ny n_1 2026-11-01T01:30 -> expected starts_at ...-04:00, actual ...-05:00
mutant 2 (replay answers 201):                          MISMATCH seed=1 at op 55/81, shrunk to 4 ops
   expected {"status": 200, ...}, actual {"status": 201, ...}
mutant 3 (GET /reservations ascending):                 MISMATCH seed=1 at op 27/81, shrunk to 5 ops
   expected "ordered": true, actual "ordered": false
mutant 4 (cancel ignores the cutoff):                   MISMATCH seed=1 at op 11/81, shrunk to 4 ops
   expected 409 cutoff_passed, actual 200 cancelled
```
Each shrunk sequence is saved as JSON and replays with `--replay <file>` deterministically.

## Known gaps
- Run against the reference model only so far; the candidate does not exist yet.
- The generator does not issue concurrent requests; concurrency is covered by the acceptance suite (B6, B20, B97).
- Precedence defaults Q1–Q10 are shared with the model; a ruling changes the model, and the runner follows.
