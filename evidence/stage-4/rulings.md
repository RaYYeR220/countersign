# Stage 4 rulings

Decided by: Foreman. R-1 … R-60 continue to bind. Master ids `C4.<n>` = stage-4 entry-A row `A<n>` where both readers
agree. R-61 … R-72 were issued from entry A before reconciliation.

## R-61 — The considered set
- Clauses: C4.5, C4.7
- Decision: considered = every `confirmed` booking of the restaurant whose occupancy `[starts_at, ends_at)` overlaps
  the closure `[from, to)`, on ANY table (not only the closed one). All other confirmed bookings of the restaurant
  are fixed. A considered booking may keep its current set (unchanged) when that set does not contain the closed
  table; the objective (C4.8) moves bookings only when needed.
- Rationale: literal reading of "Consider every confirmed booking at this restaurant overlapping that interval".

## R-62 — Replan preview: order and errors
- Clauses: C4.3, C4.4, C4.6, C4.11
- Decision: 401 → 400 body not a JSON object → 400 missing key → 422 key length → idempotency (replay 200 / 409 reuse)
  → 404 unknown restaurant → 403 non-manager → body: `table_id` missing → 422, not a string → 400, empty or > 64
  characters → 422 (R-42); `from`/`to` missing, not a string, not RFC 3339 with an explicit offset (`Z` or `±HH:MM`
  both count as explicit), or `from >= to` → 422 → 404 table not in this restaurant → 422 `planning_limit` (R-63)
  → 409 `no_feasible_plan` → 201. A preview with no considered bookings is feasible (empty assignments, 0, 0).

## R-63 — Planning limit
- Clauses: C4.6
- Decision: 422 `planning_limit` when the restaurant has more than 6 tables, more than 4 declared pairs, or more than
  6 considered bookings; otherwise the planner must return the optimal plan.

## R-64 — Feasibility details
- Clauses: C4.7, C4.15
- Decision: an option is a single table or a declared pair; its capacity is computed from the booking's OWN
  `accepted_terms.capacities` (sum for a pair). An option conflicts when any member is (a) occupied by a fixed
  confirmed booking over an overlapping interval, (b) used by another considered booking's assignment over an
  overlapping interval, (c) covered by a previously applied closure overlapping the booking, or (d) the closed table
  (every considered booking overlaps the proposed closure). Cutoffs are ignored.

## R-65 — Preview response
- Clauses: C4.9, C4.10
- Decision: exactly `{plan_id, restaurant_revision, closure: {table_id, from, to}, assignments: [{reference,
  table_ids, changed}], moved_count, unused_seats}`. `restaurant_revision` is the restaurant's revision when the plan
  was computed. `closure.from`/`to` are the instants as RFC 3339 in the restaurant's timezone offset, whole seconds
  (never `Z`). `assignments` in ascending reference order; `table_ids` in declared order; `changed` = the set
  differs from the current set; `moved_count` = number of `changed`; `unused_seats` = Σ(capacity under own terms −
  party_size). `plan_id` is an opaque id ≤ 64 characters.

## R-66 — Apply: order and effects
- Clauses: C4.12–C4.14, C4.16, C4.22
- Decision: 401 → 400 body not a JSON object → key checks → idempotency (replay 200 with the original body) → 404
  unknown restaurant → 403 non-manager → 404 plan unknown or of another restaurant → 409 `plan_already_applied` (the
  plan was applied under another key) → 409 `stale_plan` (restaurant revision differs from the plan's) → apply in one
  serialised step: record the closure; set each changed booking's table set, revision +1, one `reassigned` entry;
  each affected series revision +1 once; restaurant revision +1 once. 201 `{plan_id, restaurant_revision (after
  apply), reservations: [ordinary reservation responses of every considered booking, reference order]}`.

## R-67 — `reassigned` history entry
- Clauses: C4.14
- Decision: `{seq, at, event: "reassigned", changes: [{field: "table_ids", from: [old set], to: [new set]}],
  plan_id, revision, accepted_terms}` — the change always uses `table_ids` (full lists, declared order), even for
  single-to-single; accepted_terms and times unchanged.

## R-68 — Effects of applied closures
- Clauses: C4.15, C4.20
- Decision: an applied closure on table T over `[from, to)` makes T unavailable for any interval overlapping it:
  availability removes T and every pair containing T; explain reports `no_overlap: false` for T; POST /reservations,
  PATCH, moves, series adoption and series amend that would occupy T over an overlapping interval → 409
  `table_unavailable` (in the occupancy position). Closures are never removed in stage 4.

## R-69 — Restaurant revision visibility
- Clauses: C4.9, C4.10, C4.12
- Decision: the revision counter of R-54 is observable only through the preview and apply responses. It counts
  exactly R-54's writes plus plan application, now also including successful series amends that changed something.

## R-70 — Series amend: order and errors
- Clauses: C4.17, C4.18, C4.20
- Decision: 401 → 400 body not an object → key checks → idempotency → 404 series unknown or not the caller's →
  field validation: `expected_revision` (positive integer), `from_index` (integer 0..count−1), `local_time`
  (string exactly `HH:MM`, 00:00..23:59) — missing, wrong type (booleans, strings, null, fractions) or out of range
  → 422 → 409 `stale_revision` → per eligible occurrence in index order: 409 `cutoff_passed` (old accepted terms,
  current start) → R-7 chain under the resulting date's policy (`invalid_local_time`, `outside_opening_hours`,
  `not_on_slot_grid`, `party_exceeds_capacity`); first failing index wins → then occupancy for all changed
  occurrences (against unchanged occurrences, other bookings and applied closures) → 409 `table_unavailable`.

## R-71 — Series amend semantics
- Clauses: C4.19, C4.21, C4.22
- Decision: eligible = occurrences with index ≥ from_index that are neither cancelled nor exceptions. Each one's new
  local start = its scheduled local date (anchor's local date + index × interval × 7 days, unaffected by seating
  repairs) at `local_time`; resolved by stage-1 rules (nonexistent → `invalid_local_time`, repeated → first). Table
  set and party size unchanged. A no-op occurrence (same local start) is not checked beyond eligibility and keeps
  terms, revision and history. Success → 201 with the current series response; series and restaurant revisions +1
  once if anything changed; amended occurrences are not marked exceptions.

## R-72 — Upgrade to stage 4
- Clauses: C4.24
- Decision: import accepts schema 1–3 (and its own schema 4) states, envelope `format_version: 1`; migrated states
  have no plans and no closures; stage-3 series, histories, revisions and restaurant revisions are kept.

## R-73 — A closure on a table that already has an applied closure
- Clauses: C4.3, C4.7
- Decision: allowed. The new closure is planned and applied like any other; it adds another closure record and no
  error is raised for the overlap with the existing one.

## Answers to the Auditor's stage-4 questions (Q1–Q14)
Q1 → R-62 (your order). A non-string `table_id` → 400; every `from`/`to` problem, including a wrong JSON type,
→ 422. A closure on an already-closed table → R-73 (allowed). Q2 → R-61: yes, bookings on every table; ones that
can stay keep `changed: false`. Q3 → R-63: yes, always 422 when any limit is exceeded, checked before
no_feasible_plan; exactly 6 tables / 4 pairs / 6 bookings are planned normally. Q4: yes — a rank is the option's
position in the restaurant's full option list (singles in fixture order, then every declared pair), regardless of
party size or capacity; unused seats use each booking's own accepted-terms capacities (a pair's sum). Q5: yes and
yes. Q6: yes and yes. Q7 → R-66 (your order); any JSON object body is accepted and unknown fields are ignored.
Q8 → R-67: yes; a moved occurrence keeps its exception flag, and its series gets +1 once per application. Q9 → R-68:
yes; seeded bookings on the closed table that overlap the closure are considered and moved by the plan. Q10 → R-70
(your order); a wrong JSON type → 422. Q11: '24:00' and '7:00' → 422; scheduled dates per R-71, the anchor
included. Q12: yes (201 with revisions unchanged; replay 200); yes (one `changed` entry naming starts_at_local, and
reservation revision +1). Q13: yes. Q14 → R-72: yes; restaurant_revision is first observable through a preview.

## Answers to entry B's open questions (stage 4)
Q1 → R-62 OVERRULES the B default: `Z` counts as an explicit offset on input (RFC 3339's UTC designator; R-21 governs
only our responses), so `…Z` is accepted; fractional seconds accepted; and EVERY `from`/`to` problem, including a
non-string, is 422 (not 400). Q2 → R-62 as listed, except that `from`/`to` type problems are 422 (Q1). Q3 → R-61
(confirmed). Q4–Q6 → R-64/R-65 and the C4.8 rank definition (confirmed). Q7 → R-65 (confirmed). Q8–Q11 → R-65/R-66
(confirmed). Q12: confirmed — any manager of the restaurant may apply a plan previewed by another manager.
Q13 → R-67 (confirmed). Q14 → R-69 (confirmed). Q15/Q16 → R-68 (confirmed). Q17–Q20 → R-70/R-71 (confirmed).
Q21 → R-66/R-67 (confirmed). Q22 → R-18 (confirmed). Q23 → R-72 (confirmed). Q24 → R-60 (confirmed).

## R-74 — Imported history must start with `created` (extends R-24)
- Clauses: C1.109, C3.13, C3.28, C4.24
- Decision: an imported reservation whose history is not a dense `seq` 1..n sequence starting with exactly one
  `created` entry, with `cancelled` (if present) last, is an invalid state → 422 `validation_failed`, destination
  unchanged. More generally, import refuses any record shape this service can never produce under C3.10–C3.16 and
  R-51/R-67 (e.g. `created` after seq 1, an event other than created/changed/cancelled/reassigned).
- Rationale: R-24 accepts only states "this service recognises"; such a history cannot arise from any sequence of
  operations. Rejected: accepting it because each entry is individually well-formed.

## R-75 — Large integers in fixtures (extends R-8; Oracle O-16 finding)
- Clauses: C1.4, C1.22, C1.23, C1.26, C1.74
- Decision: fixture integers (capacity, slot_minutes, reservation_duration_minutes, cancellation_cutoff_minutes,
  seeded party_size) up to 2^31−1 are accepted and must be computed exactly — no overflow, no wrap-around: a
  2^31−1-minute duration ends 2^31−1 real minutes after the start (so no slot fits within opening hours and a
  booking is 422 `outside_opening_hours`); a 2^31−1-minute cutoff makes every future booking non-cancellable (409
  `cutoff_passed`); a capacity of 2^31−1 seats a party of that size. Values above 2^31−1 → reset 422
  `validation_failed`, state unchanged. Published policies keep their stage-3 ranges (R-47).
- Rationale: the stage-1 fixture states no ranges, so in-range values must behave literally; refusing them would
  risk rejecting legitimate fixtures. Exact arithmetic is the only reading that preserves C1.4/C1.23.

## R-76 — Migrated bookings cancelled by an earlier service (clarifies R-55)
- Clauses: C3.28, C3.48, C4.24
- Decision: every booking migrated from a stage-1 or stage-2 export — seeded or created through the API, confirmed
  or cancelled — gets revision 1, policy-0 terms and a history of exactly one `created` entry (at = created_at). No
  `cancelled` entry and no revision 2 are synthesised for bookings cancelled before the upgrade.
- Rationale: R-55's "revision 1 (whatever their status)"; earlier services kept no history to replay.

## R-77 — Imported closures and plans (extends R-24/R-72)
- Clauses: C1.109, C4.15, C4.24
- Decision: an imported closure (or a stored plan's closure) must name a table of its restaurant and carry two
  non-null RFC 3339 instants with `from < to`; otherwise the state is invalid → 422 `validation_failed`, destination
  unchanged. Both bounds are validated identically; no bound is ever defaulted.
