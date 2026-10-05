# Run metrics

## Seats

| seat | text messages | tool calls | mentions out | mentions in | commits | work items | cost (USD) | tokens | guard denials |
|---|---|---|---|---|---|---|---|---|---|
| Stylist | 19 | 261 | 21 | 33 | 36 | 10 | 30.59 | 73199586 | 0 |
| Oracle | 34 | 452 | 36 | 38 | 66 | 0 | 123.41 | 148772302 | 7 |
| Foreman | 109 | 299 | 138 | 112 | 119 | 0 | 30.27 | 102077902 | 1 |
| Auditor | 40 | 672 | 40 | 46 | 45 | 0 | 69.28 | 230320678 | 5 |
| Builder | 32 | 373 | 34 | 40 | 36 | 21 | 42.70 | 107563905 | 0 |
| total | 234 | 2057 | 269 | 269 | 302 | 31 | 296.25 | 661934373 | 13 |

- Commits by authors outside the room: Rayyer (3)

## Human input

Human text messages: 2

## Stage timeline (minutes)

| stage | start | end | minutes |
|---|---|---|---|
| 1 | 2026-10-04T12:17:13.938Z | 2026-10-04T16:54:50.049Z | 277.6 |
| 2 | 2026-10-04T16:56:12.457Z | 2026-10-04T19:45:22.975Z | 169.2 |
| 3 | 2026-10-04T19:47:05.854Z | 2026-10-04T22:18:33.816Z | 151.5 |
| 4 | 2026-10-04T22:19:36.112Z | 2026-10-05T01:13:26.927Z | 173.8 |

Closed stages: 4/4; total minutes: 772.1

## Verdicts

| stage | accept | reject | first verdict |
|---|---|---|---|
| 1 | 1 | 5 | REJECT |
| 2 | 1 | 2 | REJECT |
| 3 | 1 | 1 | REJECT |
| 4 | 1 | 3 | REJECT |

First-pass yield: 0/4 stages (0.0%)

## Stage reports

| stage | outcome | clauses | both | A-only | B-only | rulings | work items | candidates | rejections | mutation killed/total | race proofs red-without | race proofs green-with | holdout passed | holdout failed | escapes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | accepted | 134 | 124 | 0 | 10 | 28 | 7 | 5 | 4 (1 changed work) | 54/66 (81.8%) | 5/5 | 5/5 | 120 | 0 | 1 |
| 2 | accepted | 59 | 59 | 0 | 0 | 17 | 9 | 5 | 3 (3 changed work) | 40/49 (81.6%) | 6/6 | 6/6 | 145 | 0 | 0 |
| 3 | accepted | 53 | 52 | 0 | 1 | 15 | 6 | 2 | 1 (1 changed work) | 22/27 (81.5%) | 8/8 | 8/8 | 152 | 0 | 0 |
| 4 | accepted | 24 | 24 | 0 | 0 | 17 | 9 | 4 | 3 (3 changed work) | 34/38 (89.5%) | 10/10 | 10/10 | 158 | 0 | 0 |

- Stage 1 escapes: C1.81 (found on candidate 1bfe6e2, fixed by WI-7; none on the accepted SHA)
