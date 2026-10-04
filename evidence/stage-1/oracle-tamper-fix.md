[EVIDENCE] stage=1 wi=oracle-tamper-fix sha=<filled at commit>

# Evidence — tamper generator targets reservation records (candidate-3 step-2 test defect)

- Revision: see commit `[WI-oracle-tamper-fix]` on `seat/oracle`
- Branch: seat/oracle
- Clauses claimed: C1.107, C1.109 (test_hardening.py::test_C1_107_C1_109_tampered_export_is_refused)

## Fix
- `_reservation_records()` picks the dicts carrying the reference that also carry an owner key (`user_id`, `owner`,
  `owner_id`, `user`, `diner_id`, `account_id`, `customer_id`); a stored 201 response inside an idempotency receipt
  never has one. If no match carries an owner key the generator falls back to every match, so a tamper is still
  applied somewhere meaningful rather than skipped.
- Every record-level tamper (duplicate reference, duplicate id, each invalid field, O-5's empty/65-character id and
  reference, boolean id) is applied to all matching records, so a layout that stores a record twice still ends up
  inconsistent. The "duplicate record" case appends beside the record itself, never inside a receipt.
- Self-check: the model's export reordered so the `idempotency` collection precedes `reservations` (the candidate's
  layout) — the generator located the record (owner key present), produced 34 tampers, every one refused with 422,
  and the untouched export still imported with 204.

## Commands

### self-check script (model in-process, receipts-first export order)
Exit code: 0
```
records found: 1 owner key present: True
tampers: 34 not refused: []
clean import: 204
```

### `python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18400 --second-base-url http://127.0.0.1:18402 -p no:cacheprovider`
Exit code: 0
```
........................................................................ [ 53%]
...............................................................          [100%]
135 passed in 8.90s
```

## Known gaps
- Validated against the model; the Auditor re-runs step 2 on the next candidate.
