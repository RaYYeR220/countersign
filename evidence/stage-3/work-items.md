# Stage 3 work items

Base: `main`. Clause ids `C3.<n>` (= stage-3 entry-A `A<n>` where both readers agree), bound by R-1 … R-45 and the
stage-3 rulings R-46 …. Complete clause text travels in each `[HANDOFF]`.

| WI | owner | title | clauses | depends on | status |
|----|-------|-------|---------|------------|--------|
| WI-17 | Builder | Policies, accepted terms, revisions, history, decision, PATCH/cancel/moves under policies, restaurant revision | C3.10–C3.16, C3.18–C3.36, C3.49–C3.52 | – | merged |
| WI-18 | Builder | Upgrade from schema 1/2 exports to schema 3; ADR-003 | C3.1, C3.28, C3.48 | WI-17 | merged |
| WI-19 | Stylist | Availability explanations and policy-driven availability | C3.2–C3.9, C3.17, C3.25, C3.26 | WI-17 policy selection | merged |
| WI-20 | Stylist | Recurring reservations (series) | C3.37–C3.47 | WI-17 | merged |

## Retry budget (rework rounds used of 2)
| WI | rounds | clean restart | notes |
|----|--------|---------------|-------|
| WI-17 | 0 | no | |
| WI-18 | 0 | no | |
| WI-19 | 0 | no | |
| WI-20 | 0 | no | |

## Candidates
| # | sha | contents | verdict |
|---|-----|----------|---------|
| 1 | 1aaecec | WI-17/18 (builder f052006), WI-19/20 (stylist 4c50589), oracle fbe9fd2, auditor head | REJECT (verdict 1: F-1 R-60 → WI-21); superseded by 2 |

## Rework
| WI | owner | from | clauses | status |
|----|-------|------|---------|--------|
| WI-21 | Stylist | stage-3 candidate 1 escalation; R-60 | C3.38 | evidence 0782d40, merged (rework round 1 of 2 on WI-20) |
| WI-22 | Builder | Auditor observation: stage-3/RUN.md still stage-2 text | C1.6 | evidence 26a8055, merged (docs) |
| 2 | (this commit) | + WI-21 (0782d40), WI-22 RUN.md (26a8055), C3.48 adoption tests (builder e3fb8f5, stylist 6473b73) | pending |
