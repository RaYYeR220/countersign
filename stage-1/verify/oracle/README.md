# Oracle verification kit — stage 1

Everything here talks to the service through HTTP only. Nothing in this folder ships in the image.

| file | purpose |
|---|---|
| `model.py` | Reference model of the stage-1 specification: one in-memory state machine, deterministic ids, injectable clock. Precedence defaults for the open questions Q1–Q10 of `evidence/stage-1/ledger-B.md` are marked `# Qn`. |
| `serve_model.py` | Serves the model over HTTP so the suites can be validated against it: `python serve_model.py --port 18400`. |
| `client.py` | Stdlib HTTP client (`Client`) plus `burst()` for barrier-released concurrent requests. Any 5xx fails the test that caused it (B70). |
| `fixtures.py` | Shared fixtures and dates (future dates for mutable bookings, past dates for cutoff cases, the four DST transitions). |
| `conftest.py` | `--base-url` (or `ORACLE_BASE_URL`), optional `--second-base-url` for the cross-process import test. |
| `test_acceptance.py` | Acceptance suite; test names carry the ledger-B ids they cover. |
| `diff_runner.py` | Differential runner: seeded random operation sequences against the model and the service, every response compared, mismatches shrunk to the shortest replaying sequence and saved as JSON for `--replay`. |
| `op_stats.py` | Prints which (operation, status, code) outcomes the generator reaches on the model, to judge coverage of a seed range. |

## Run

```sh
# against any running service (candidate container, or the model itself)
python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18401 -p no:cacheprovider
# with a second, independent instance for the import-into-fresh-container test (B149)
python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18401 --second-base-url http://127.0.0.1:18403 -p no:cacheprovider
# differential run
python stage-1/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18401 --seed 1 --runs 50 --ops 60
```

The suite resets the service before every test with `POST /_test/reset`; it leaves state behind on purpose.

## Differential runner

Each generated sequence starts with a reset and mixes signup/login, availability, create (fresh keys, replays,
key reuse with a different body, the same key from another user, missing and over-long keys), get, list, cancel,
PATCH, atomic moves, export, import (of an earlier export) and invalid imports, over future, past and all four
DST dates. The generator is model-guided: it runs each op on a private model as it goes, so it knows which
tokens and bookings are live and biases amendments towards values valid for the booking's restaurant.

Responses are compared after normalisation: tokens, user ids, reservation ids, references and `created_at` are
replaced by labels assigned by the op that produced them (the newest binding wins, so id reuse after import is
harmless); error messages are masked; export `state` is opaque; `GET /reservations` ties on equal `starts_at`
are made stable while the descending order itself is still checked.

Self-test: `serve_model.py --mutant N` deviates from the specification on purpose (1 fall-back resolves to the
second occurrence, 2 replays answer 201, 3 list ascending, 4 cancel ignores the cutoff). The runner must find
each within the default seed range and shrink it to a handful of ops.
