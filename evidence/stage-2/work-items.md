# Stage 2 work items

Base: `main`. Clause ids `C2.<n>` (= stage-2 entry-A `A<n>` where both readers agree), bound by stage-1 rulings
R-1…R-28 (R-26 withdrawn) and stage-2 rulings R-29…. Complete clause text and acceptance criteria travel in each
`[HANDOFF]`.

| WI | owner | title | clauses | depends on | status |
|----|-------|-------|---------|------------|--------|
| WI-8 | Builder | Combined tables in the core (fixture, availability options, POST/PATCH/moves with table_ids) | C2.38–C2.54, C2.58, C2.59, C2.39 | – | merged |
| WI-9 | Builder | Upgrade from stage-1 exports + ADR-002 (UI serving, schema 2) | C2.1, C2.35–C2.37 | WI-8 | merged |
| WI-10 | Stylist | Design direction, app shell, navigation, signup/login, session | C2.3, C2.4, C2.11–C2.20 | – | merged |
| WI-11 | Stylist | Search grid, booking form, confirmation, out-of-order + 409 + uncertain recovery, combination cells | C2.5–C2.10, C2.21–C2.33, C2.55–C2.57 | WI-10, WI-8 API | merged |
| WI-12 | Stylist | Lookup screen and upgrade survival in the browser | C2.34, C2.36, C2.37 | WI-11, WI-9 | merged |

## Retry budget (rework rounds used of 2)
| WI | rounds | clean restart | notes |
|----|--------|---------------|-------|
| WI-8 | 0 | no | |
| WI-9 | 0 | no | |
| WI-10 | 0 | no | |
| WI-11 | 0 | no | |
| WI-12 | 0 | no | |

## Candidates
| # | sha | contents | verdict |
|---|-----|----------|---------|
| 1 | b4e805f | WI-8/9 (builder 15035d7), WI-10..12 (stylist b2403a1), oracle d565258, auditor head | REJECT (verdict 1: F-1 product R-42 → WI-13; F-2, F-3 Oracle defects) |

## Rework
| WI | owner | from | clauses | status |
|----|-------|------|---------|--------|
| WI-13 | Builder | stage-2 verdict-1 escalation Q2; R-42 | C1.18, C1.41, C2.46–C2.53 | evidence 04196be, merged (rework round 1 of 2 on WI-8 validation) |
| 2 | 090df19 | + WI-13 (04196be), oracle 363c365 (R-41/R-42/R-35 model) | pending |
