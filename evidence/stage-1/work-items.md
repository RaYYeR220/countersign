# Stage 1 work items

Base: `main`. Clause ids `C1.<n>` (= entry-A `A<n>`, see rulings.md). Complete clause text and acceptance
criteria are carried in each `[HANDOFF]` message (generated from `ledger-A.md` rows) and bound by rulings R-1…R-18.

| WI | owner | title | clauses | depends on | status |
|----|-------|-------|---------|------------|--------|
| WI-1 | Builder | ADR-001 + runnable skeleton (health, reset, envelope, JSON parsing) | C1.1, C1.6–C1.14, C1.16–C1.19, C1.26, C1.32–C1.34, C1.44, C1.46 | – | evidence 62b84d3 |
| WI-2 | Builder | Accounts, tokens, restaurants endpoints | C1.29, C1.36, C1.47–C1.55, C1.68–C1.70 | WI-1 | evidence 276f729, merged |
| WI-3 | Stylist | Local-time/DST engine and GET /availability | C1.20–C1.25, C1.27, C1.28, C1.43, C1.71–C1.77, C1.100–C1.104 | WI-1 | evidence 843f319, merged 67aab65 |
| WI-4 | Builder | Reservations: create with idempotency, list, get, cancel, PATCH | C1.3–C1.5, C1.15, C1.30, C1.31, C1.35, C1.40–C1.42, C1.45, C1.56–C1.67, C1.78–C1.99 | WI-2, WI-3 (time functions) | evidence 1dc62e3, merged |
| WI-5 | Stylist | Export / import with versioned state | C1.105–C1.112, C1.124 | WI-1 (state model) | evidence 09e91c0 merged 67aab65; receipts part waits on WI-4/WI-6 |
| WI-6 | Builder | Atomic reservation moves | C1.113–C1.124 | WI-4 | evidence 7a31c0a, merged b66e54f |

## Retry budget (rework rounds used of 2)
| WI | rounds | clean restart | notes |
|----|--------|---------------|-------|
| WI-1 | 0 | no | |
| WI-2 | 0 | no | |
| WI-3 | 0 | no | |
| WI-4 | 0 | no | |
| WI-5 | 0 | no | |
| WI-6 | 0 | no | |

## Candidates
| # | sha | contents | verdict |
|---|-----|----------|---------|
| 1 | bd108ef | WI-1..WI-6, oracle suite b7496b1 | REJECT (F-1: differential runner not delivered; no product finding; verdict-1.md on seat/auditor 740c805) |
| 2 | 4f54736 | + oracle aligned 3f407bf (runner), builder 507019c, stylist 14083c1 | pending |
| 2 | 4f54736 | superseded by 3 (step 3 red: Oracle model/runner faults A and B; R-25; no product finding) | superseded |
| 3 | (this commit) | + Oracle diff fix and hardening O-1..O-4 (seat/oracle 3e387ec), auditor verdict-1; product identical to 4f54736 | pending |
