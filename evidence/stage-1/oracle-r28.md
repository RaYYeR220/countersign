[EVIDENCE] stage=1 wi=oracle-r28 sha=79cd1b2

# Evidence — R-28 applied (supersedes R-26): every reference matches ^[A-Z0-9]{6,12}$ and is unique

- Revision: see commit `[WI-oracle-r28]` on `seat/oracle` (main merged at 4328c8a)
- Branch: seat/oracle
- Clauses claimed: C1.81, C1.30, C1.107, C1.109

## What changed
- `model.py`: reset and import again refuse a reference that does not match the C1.81 format (R-26 relaxation reverted);
  duplicates were always refused. Issued references unchanged. The R-27 tie-order fix (0d5f337) is kept.
- `test_hardening.py`:
  - tampers `reference="bad ref"` and `"abcdef"` restored as 422 cases, plus `"X"`, 13 characters and `"SEED-01"`;
    empty and 65 characters remain.
  - `test_C1_107_C1_81_imported_references_must_conform` replaces the R-26 round trip: nine non-conforming references
    rewritten into a real export → 422 with the destination snapshot unchanged; two conforming ones (`ABCDEFGHJKLM`,
    `A1B2C3`) import and are reachable; the original export restores the state.
  - `test_C1_81_C1_30_reset_refuses_nonconforming_reference` (parametrised): `X`, `seed01`, `ABCDEFGHJKLMN`, `SEED-01`,
    `""`, 65 characters, `bad ref`, `abcdef` → 422, state unchanged, the previous seed still reachable.
  - `test_C1_81_C1_30_reset_accepts_conforming_reference`: `SEED01`, `ABCDEFGHJKLM`, `A1B2C3`, `000000` → 204 and reachable.
  - `test_C1_81_C1_30_reset_refuses_duplicate_reference`: two seeded rows sharing `SEED01` → 422, state unchanged.

## Commands

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 48%]
........................................................................ [ 96%]
.....                                                                    [100%]
149 passed in 10.69s
```

## Known gaps
- Validated against the model; the next candidate carries this branch.
