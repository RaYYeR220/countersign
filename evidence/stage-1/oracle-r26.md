[EVIDENCE] stage=1 wi=oracle-r26 sha=92c1bec

# Evidence — R-26 applied: seeded and imported references are opaque ids

- Revision: see commit `[WI-oracle-r26]` on `seat/oracle` (main merged at 2d080b2)
- Branch: seat/oracle
- Clauses claimed: C1.81, C1.107, C1.109 (R-26)

## What changed
- `model.py`: reset and import accept any non-empty reference of at most 64 characters (uniqueness still enforced);
  the 6–12 `A-Z0-9` rule applies only to references the model issues.
- `test_hardening.py`: the tampers `reference="bad ref"` and `reference="abcdef"` no longer expect 422 (removed);
  empty, 65-character and duplicate references remain refused. New positive test
  `test_C1_107_R26_fixture_style_references_round_trip`: the seeded record's reference rewritten in a real export to
  `abcdef`, a 64-character string and `Ref-with.punct_1` imports with 204, the booking is reachable under the new
  reference with every other field unchanged, the old reference is 404, the list shows the new one; the original export
  restores it; a reset fixture may seed `seed-ref_01` directly.

## Commands

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 52%]
................................................................         [100%]
136 passed in 24.91s
```

## Known gaps
- Validated against the model; merges at stage close together with O-8.
