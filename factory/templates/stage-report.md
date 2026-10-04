[CLOSE] stage=<n> sha=<accepted sha>

# Stage <n> report

Machine-readable twin: `evidence/stage-<n>/report.json`, valid against `factory/templates/stage-report.schema.json`.
This file is `evidence/stage-<n>/report.md`.

## Outcome
- Outcome: <accepted | blocked>
- Accepted SHA: <sha, or "none">
- Blockers: <each blocker with its evidence path, or "none">

## Clauses
- Total: <n> (both readers <n>, A only <n>, B only <n>)
- Rulings: <n> (`evidence/stage-<n>/rulings.md`)

## Work
- Work items: <n>
- Candidates: <n>
- Rejections:
  - <sha>: <n> findings, changed work: <yes|no>

## Verification
- Mutation: <tool>, <killed>/<total> killed
- Race proofs:
  - <guard> — <test>: red without <yes|no>, green with <yes|no>
- Holdout: ran <yes|no>, passed <n>, failed <n>, escapes: <clause ids, or "none">

## report.json skeleton
```json
{
  "stage": 0,
  "outcome": "accepted",
  "accepted_sha": "<sha or null>",
  "clauses": {"total": 0, "both": 0, "a_only": 0, "b_only": 0, "rulings": 0},
  "work_items": 0,
  "candidates": 0,
  "rejections": [{"sha": "<sha>", "findings": 0, "changed_work": true}],
  "mutation": {"tool": "<name>", "killed": 0, "total": 0},
  "race_proofs": [{"guard": "<where>", "test": "<test id>", "red_without": true, "green_with": true}],
  "holdout": {"ran": false, "passed": 0, "failed": 0, "escapes": []},
  "blockers": []
}
```
