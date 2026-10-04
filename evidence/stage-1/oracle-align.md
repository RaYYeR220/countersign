[EVIDENCE] stage=1 wi=oracle-align sha=3f407bf

# Evidence — Oracle alignment with the master ledger and rulings R-1 … R-24

- Revision: see commit `[WI-oracle-align]` on `seat/oracle` (main merged at 564f21e)
- Branch: seat/oracle
- Clauses claimed: C1.3–C1.5, C1.11–C1.18, C1.20–C1.36, C1.38–C1.46, C1.47–C1.59, C1.60–C1.70, C1.71–C1.99,
  C1.100–C1.124, C1.125–C1.127, C1.132, C1.134 (acceptance suite, 123 tests named by clause id; differential runner)

## What changed (by finding / ruling)
- R-2 + R-19 (C1.34, C1.42): `null` is a wrong JSON type → 400 (party_size → 422); body checks run in three passes
  (types 400 → missing 422 → values 422) for signup, login, POST /reservations, PATCH and move items.
- R-4 (C1.47–C1.52): email uniqueness and login case-insensitive; exactly one `@`, non-empty parts, no whitespace;
  blank display_name → 422; password length in code points.
- R-8 (C1.12, C1.28): `closes: "24:00"` accepted as end of day.
- R-9 (C1.38): unknown path → 404; known path with an unsupported method → 405 `method_not_allowed`.
- R-11 (C1.89): ties on `starts_at` → `created_at` ascending, then `reference` ascending.
- R-13 (C1.91): cancel ignores an absent/empty body; a non-empty unparseable body → 400.
- R-16 (C1.30): seeded `created_at` taken from the fixture when it is a valid RFC 3339 timestamp (written as UTC `+00:00`).
- R-20 (C1.12, C1.30): reset → 422 for duplicate ids/emails/references/weekdays, unknown user/restaurant/table in
  seeded rows, closes ≤ opens; seeded rows not re-validated against grid/hours/capacity/overlap.
- R-23 (C1.74, C1.84, C1.103): end-of-day check on absolute instants, `closes` in a skipped hour = transition instant.
- R-24 (C1.109): `state: {}` or an unrecognised state → 422, destination unchanged.
- C1.112: tokens are random, so a destination token issued before an import can never collide with an imported one.
- Suite relabelled from entry-B ids to master-ledger ids (`clauses.json` keeps the mapping); 17 new tests for the
  rulings above; client now keeps one connection per instance (the shared host was exhausting ephemeral ports).

## Commands

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 58%]
...................................................                      [100%]
123 passed in 2.10s
```

### `python stage-1/verify/oracle/diff_runner.py --base-url http://127.0.0.1:18402 --seed 1 --runs 40 --ops 80`
Exit code: 0
```
OK: 40 runs, 3240 ops, seeds 1..40, 2.3s
```

### Self-test: `serve_model.py --mutant N` then `diff_runner.py --seed 1 --runs 30 --ops 80`
Exit code: 1 for every mutant (expected)
```
mutant 1 (fall-back resolves to the second occurrence): MISMATCH seed=4 at op 31/81, shrunk to 3 ops
mutant 2 (replay answers 201):                          MISMATCH seed=1 at op 55/81, shrunk to 4 ops
mutant 3 (GET /reservations ascending):                 MISMATCH seed=1 at op 27/81, shrunk to 5 ops
mutant 4 (cancel ignores the cutoff):                   MISMATCH seed=1 at op 11/81, shrunk to 4 ops
```

## Known gaps
- Validated against the reference model only; the candidate (main bd108ef) has not yet been run by me. The Auditor
  holds the candidate; the aligned suite and runner are on seat/oracle for its rerun.
- Reset field-check order inside fixture objects is per field (missing → type), not the three-pass R-19 order
  (R-19 names signup, login, POST, PATCH and move items only); suites do not combine the two conditions in a fixture.
