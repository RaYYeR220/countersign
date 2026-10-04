[VERDICT] stage=<n> sha=<sha> result=ACCEPT|REJECT

# Verdict <k> — stage <n>

- Candidate: <full sha>
- Clean clone: <absolute path>
- File: `evidence/stage-<n>/verdicts/verdict-<k>.md`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | <pass/fail/skip> | `<command>` | <code> |
| 2 | acceptance suites | <pass/fail/skip> | `<command>` | <code> |
| 3 | differential runner (seeds: <list>) | <pass/fail/skip> | `<command>` | <code> |
| 4 | attacks | <pass/fail/skip> | `<command>` | <code> |
| 5 | mutation (<killed>/<total>) | <pass/fail/skip> | `<command>` | <code> |
| 6 | race proofs | <pass/fail/skip> | `<command>` | <code> |
| 7 | user-facing checks | <pass/fail/skip> | `<command>` | <code> |
| 8 | holdout | <pass/fail/skip> | `<command>` | <code> |

A skip needs a reason: no such surface in this stage, an earlier step failed (step 8 only), or a ruling `R-<n>`.

## Outputs
### Step <s>
```
<output, at most 20 lines>
```

## Findings
- F-<n>
  - clause: C<stage>.<n> — "<quoted text>"
  - request: <exact request or action>
  - expected: <what the clause requires>
  - actual: <what the candidate did>
  - reproduce: `<command>`

## Counts for the stage report
- mutation: tool <name>, killed <n>, total <n>
- race proofs: <guard> / <test> / red-without <yes|no> / green-with <yes|no>
- holdout: ran <yes|no>, passed <n>, failed <n>, escapes <clause ids>

## Result
result=<ACCEPT|REJECT> — <one sentence why>
