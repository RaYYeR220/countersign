[LEDGER] stage=<n> part=<i>/<k>

# Clause ledger — stage <n>, entry <A|B|master>

Specification: <absolute path> @ <revision>
Reader: <seat>
File: `evidence/stage-<n>/ledger-<A|B>.md` (entries) or `evidence/stage-<n>/ledger.md` (master, posted as `[RECONCILE] stage=<n>`)

| id | quoted requirement | observable behaviour | error precedence | acceptance criterion | reader (A/B/both) | ruling |
|----|--------------------|----------------------|------------------|----------------------|-------------------|--------|
| C<stage>.<n> | "<exact sentence from the specification>" | <what a client can observe> | <which error wins when several rules apply, or "n/a"> | <a check that passes only if the behaviour holds> | <A, B or both> | <R-n, or "-"> |

Rules for this matrix:
- One row per normative sentence. Quote it exactly; never paraphrase in the second column.
- Ids are stable for the run: `C<stage>.<n>`, numbered in reading order. Never reuse a retired id.
- Entries A and B are written from the specification alone, without reading the other entry.
- Master only: a row found by one reader is re-read by both against the text before it is kept or ruled.
