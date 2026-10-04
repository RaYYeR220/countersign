# Stage 4 work items

Base: `main`. Clause ids `C4.<n>` (= stage-4 entry-A `A<n>` where both readers agree), bound by R-1 … R-60 and the
stage-4 rulings R-61 …. Complete clause text travels in each `[HANDOFF]`.

| WI | owner | title | clauses | depends on | status |
|----|-------|-------|---------|------------|--------|
| WI-23 | Builder | Replans: preview (optimal planner), apply, closures, restaurant revision in responses | C4.3–C4.16, C4.22 | – | merged |
| WI-24 | Builder | Upgrade from schema 1–3 exports to schema 4; ADR-004 | C4.1, C4.24 | WI-23 | merged |
| WI-25 | Stylist | Series amend | C4.17–C4.21, C4.23 | WI-23 closures in occupancy | merged |
| WI-26 | Stylist | Screens and explain reflect applied plans | C4.2, C4.15 | WI-23 | merged |

## Retry budget (rework rounds used of 2)
| WI | rounds | clean restart | notes |
|----|--------|---------------|-------|
| WI-23 | 0 | no | |
| WI-24 | 0 | no | |
| WI-25 | 0 | no | |
| WI-26 | 0 | no | |

## Candidates
| # | sha | contents | verdict |
|---|-----|----------|---------|
| 1 | 06cdcd6 | WI-23/24 (builder 7bbe4d4), WI-25/26 (stylist a30b404), oracle 38fda84, auditor head + WI-27 RUN.md (d2b6859) | REJECT (verdict 1: F-1 R-74 → WI-28); superseded by 2 |
| WI-27 | Builder | RUN.md for stage 4 | C1.6 | merged |
| WI-28 | Builder | stage-4 candidate 1: import accepts a history whose first entry is not created; R-74 | C1.109, C3.13, C3.28 | evidence 69fd2b3, merged (rework round 1 of 2 on WI-24 import validation) |
| 2 | (this commit) | + WI-28 (69fd2b3) | pending |
