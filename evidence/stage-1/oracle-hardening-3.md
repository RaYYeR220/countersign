[EVIDENCE] stage=1 wi=O-8 sha=30c1518

# Evidence — O-8: more tampered-state imports (verdict-3 survivors)

- Revision: see commit `[WI-O-8]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed: C1.107, C1.109 (R-24) via `test_hardening.py::test_C1_107_C1_109_tampered_export_is_refused`

## What changed
- Zero or empty start/end timestamps: for whichever of `starts_at`, `ends_at`, `start`, `end`, `starts_at_utc`,
  `ends_at_utc`, `start_at`, `end_at` the record carries, the values `""`, `0` and `"0001-01-01T00:00:00Z"` are each
  applied to every copy of the record.
- Only the receipts collection removed, only the tokens collection removed: each located structurally by a value it
  must contain (the idempotency key used in the test, substring-matched so composite keys count; the caller's token).
  Skipped when the layout has no separate top-level collection for it (nested under users, hashed tokens).
- Valid id with an invalid reference: lowercase `abcdef` added beside the existing empty and 65-character cases.
- The model's import now refuses a timestamp before year 1000 (a zero value is not something it produced).
- The generator now yields 43 tampers on the model's layout (was 34); threshold raised to ≥ 30.

## Commands

### self-check (model in-process, receipts-first export order)
Exit code: 0
```
tampers: 43 not refused: [] ['missing users collection', 'missing restaurants collection',
 'missing reservations collection', 'missing receipts collection', 'missing tokens collection']
```

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 53%]
...............................................................          [100%]
135 passed in 8.59s
```

## Known gaps
- Validated against the model; merges at stage close per the Foreman.
