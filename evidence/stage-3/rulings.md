# Stage 3 rulings

Decided by: Foreman. Stage-1 (R-1 … R-28) and stage-2 (R-29 … R-45) rulings continue to bind. Master ids `C3.<n>` =
stage-3 entry-A row `A<n>` where both readers agree. R-46 … R-58 were issued from entry A before reconciliation.

## R-46 — `explain` parameter
- Clauses: C3.3, C3.4
- Decision: `explain` is checked after R-12's parameter checks (restaurant_id, date, party_size) and before the 404
  for an unknown restaurant; any value other than the exact string `true` (including `false`, `1`, `""`, `TRUE`) →
  422 `validation_failed`. A repeated `explain` behaves like the other parameters: only the first value counts. Without `explain`, slots carry exactly the stage-2 fields (`starts_at_local`,
  `starts_at`, `available_table_ids`, `available_options`) and no explanation fields.

## R-47 — Policy publication: order and field errors
- Clauses: C3.18–C3.23
- Decision: 401 → 400 body not a JSON object → 400 missing key → 422 key length → idempotency (replay / 409 reuse)
  → 404 unknown restaurant → 403 `forbidden` (caller not in `manager_user_ids`) → policy validation. Every
  field-level problem in the policy body — missing field, wrong JSON type (including booleans or strings where
  integers are required, null), out-of-range value, invalid date, invalid or duplicate weekday, bad `HH:MM`,
  `closes` not later than `opens`, `capacities` not naming exactly the restaurant's tables or a capacity outside
  1..100 — is 422 `validation_failed` (endpoint-specific rule: "Invalid policy is 422"). Integral numbers such as
  `30.0` count as integers (R-3). Unknown fields are ignored and not echoed.
- Rationale: the spec states one error for an invalid policy and names booleans explicitly; permission is decided
  before the body is judged, so a non-manager never learns validation details.

## R-48 — Policy representation
- Clauses: C3.20, C3.24, C3.27
- Decision: the 201 body and each `GET …/policies` entry are exactly `{effective_from, slot_minutes,
  reservation_duration_minutes, cancellation_cutoff_minutes, opening_hours, capacities, policy_version}` with
  values as supplied (opening_hours in supplied order, each `{weekday, opens, closes}`; capacities as an object
  keyed by table id). GET on an unknown restaurant → 404. Policy 0 has no `effective_from` and is never listed; it
  applies to every date before the earliest applicable published policy.
- `accepted_terms` = `{policy_version, slot_minutes, reservation_duration_minutes, cancellation_cutoff_minutes,
  opening_hours, capacities}` of the selected policy; for policy 0 these come from the fixture (opening_hours in
  fixture order, capacities from the fixture tables).

## R-49 — PATCH with policies and `expected_revision`
- Clauses: C3.31–C3.34
- Decision: PATCH order: 401 → 400 body / other fields' types (R-19, R-44 both-fields 422) → 404 not the caller's
  → 422 `expected_revision` present but not a positive integer (booleans, strings, 0, negatives, fractions;
  `null` too) → 409 `stale_revision` (differs from current revision) → 409 `reservation_cancelled` → 409
  `cutoff_passed` (old accepted terms, current start) → resulting-field validation against the policy of the
  resulting start date (R-35/R-7 order) → 409 `table_unavailable`. A no-op (identical resulting table set, start
  and party size) returns 200 unchanged after passing the checks up to cutoff. Real change: revision +1, new
  accepted terms and ends_at, one `changed` history entry. The check-and-apply is one serialised step, so two
  concurrent PATCHes with the same `expected_revision` cannot both make a real change.
- Rationale: the spec places stale_revision "before cutoff/validation"; cancelled is a state check of the same kind
  as cutoff and follows stale.

## R-50 — Cancel under stage 3
- Clauses: C3.30, C3.33, C3.46
- Decision: 401 → 404 → already cancelled → 200 current state, nothing changes → 409 `cutoff_passed` (accepted
  cutoff, current start) → cancel: revision +1, `cancelled` history entry, series revision +1 if the booking is a
  series occurrence (not an exception). Cancel ignores `expected_revision`.

## R-51 — History entries
- Clauses: C3.10–C3.16, C3.35, C3.50
- Decision: entry = `{seq, at, event, changes, revision, accepted_terms}`. `at` is the time of the write in the
  restaurant's timezone offset, whole seconds, RFC 3339 with numeric offset (never `Z`). `created` uses
  `created_at`. Field names: `table_id` for single tables; `table_ids` whenever the before or the after set is a
  pair (full lists, declared order); `starts_at_local`; `party_size`. Seeded and imported bookings have exactly one
  `created` entry (revision 1, policy-0 terms, `at` = created_at), whatever their status ("Seeded bookings start at
  revision 1 under policy 0"). `revision` is the reservation's revision after the entry.

## R-52 — `POST /series` order and field errors
- Clauses: C3.37, C3.38, C3.41
- Decision: 401 → 400 body not an object → key checks → idempotency → `anchor_reference` missing → 422, not a
  string → 400 (§5); `count` / `interval_weeks` missing, non-integer (booleans, strings, null, fractions) or out of
  range → 422 → 404 anchor unknown or not the caller's → 409 `reservation_cancelled` → 409 `already_in_series`
  (anchor already belongs to any series) → 409 `cutoff_passed` (anchor's accepted cutoff) → occurrence generation:
  for i = 1 … count−1 in index order, the first failing occurrence decides, with R-35/R-7 order inside an
  occurrence (`invalid_local_time` → `outside_opening_hours` → `not_on_slot_grid` → `party_exceeds_capacity` →
  409 `table_unavailable`). Generated occurrences are not subject to a cutoff. Occupancy is checked against all
  confirmed bookings and the other generated occurrences.

## R-53 — Series representation
- Clauses: C3.42–C3.45
- Decision: exactly `{series_id, revision, interval_weeks, occurrences:[{index, reference, exception,
  reservation}]}` where `reservation` is the ordinary reservation response (current state for GET; at adoption
  time for the 201 and its replays). GET by another user, with no token or an unknown id → 404. `series_id` is an
  opaque id ≤ 64 characters. Each generated occurrence gets a `created` history entry, revision 1 and its own
  policy's terms.

## R-54 — Restaurant revision (internal in stage 3)
- Clauses: C3.47, C3.52
- Decision: each restaurant keeps a revision counter, 0 after reset, incremented once per successful new
  booking, real amendment, cancellation, policy publication, series adoption and successful move batch (once per
  batch). No-ops, failures and replays never increment it. It is preserved by export/import (imports from schema
  1/2 start at 0). Stage 3 does not expose it.

## R-55 — Upgrade from stage-1 and stage-2 exports
- Clauses: C3.28, C3.48
- Decision: import accepts schema 1, 2 and 3 states (envelope `format_version: 1`). Migrated bookings get revision 1
  (whatever their status), policy-0 terms and the R-51 history; no series; no published policies; restaurant revisions 0;
  receipts replay their original bodies verbatim (without revision/terms, as originally sent).

## R-56 — History and decision access
- Clauses: C3.10, C3.36
- Decision: `GET …/history` and `GET …/decision` with no token, an invalid token, another user's token or an
  unknown reference → 404 `not_found` (never 401).

## R-57 — Moves under policies
- Clauses: C3.51, C3.52
- Decision: R-22 order with stage-3 additions per item: after the item's 404 and restaurant check, 422 invalid
  `expected_revision` → 409 `stale_revision` → 409 `reservation_cancelled` → 409 `cutoff_passed` (also for no-op
  items) → resulting-field validation against the resulting date's policy → then batch occupancy (409). Success:
  each really changed booking +1 revision and one `changed` entry; series occurrences that really changed become
  exceptions; each affected series +1 once; restaurant revision +1 once.

## R-58 — No-op definition
- Clauses: C3.14, C3.32, C3.46, C3.50
- Decision: an amendment (PATCH or move item) is a no-op exactly when the resulting table set (as a set), local
  start and party size equal the current ones. Supplying only `expected_revision` (matching) is a no-op.

## Answers to the Auditor's stage-3 attack-plan questions (P1–P11)
P1 → R-47 stands (not the proposed order): §7 resolves idempotency "before endpoint-specific field validation or
current-resource checks", so 401 → 400 body → key checks → idempotency → 404 restaurant → 403 → policy validation.
P2 → R-47: every policy field problem is 422, including a non-object `capacities`, missing/extra table ids,
non-integer or 0/101 capacities, closes ≤ opens and duplicate weekdays. P3 → R-46: after party_size, before the
404; a repeated `explain` uses its first value, like the other parameters. P4 → R-49 (not the proposed order): 404 → 422
invalid `expected_revision` (any non-positive-integer incl. strings, booleans, null, 0, −1, 1.5) → 409
stale_revision → 409 reservation_cancelled → 409 cutoff_passed → values; per move item R-57. P5 → yes: a stale
`expected_revision` gives 409 even when the PATCH would be a no-op. P6 → R-50: cancel ignores `expected_revision`;
cancelling twice → 200, revision unchanged. P7 → R-52 (`anchor_reference` not a string → 400; count/interval any
invalid incl. type → 422). P8 → R-52: yes, R-7 order inside an occurrence, occupancy includes earlier generated
occurrences, and the anchor's accepted cutoff is checked against now (409 cutoff_passed). P9 → R-54: internal in
stage 3, not observable; do not assert on export field names (the state is opaque). P10 → R-51/R-55: yes — one
`created` entry, at = created_at, revision 1 whatever the status, policy-0 terms from the (imported) fixture
config. P11 → yes: the resulting date's policy supplies terms and capacities, including a pair's summed capacity
(C3.49).

## R-59 — `manager_user_ids` fixture validation (extends R-8/R-20)
- Clauses: C3.18
- Decision: optional (absent → `[]`); not an array of strings → 400; an id that is empty, longer than 64 characters,
  not a fixture user, or duplicated → 422, state unchanged. `GET /restaurants/{id}` keeps the fixture shape and
  includes `manager_user_ids` (and `combinable`); published policies and the restaurant revision never appear there.

## Answers to the Builder's E1–E9 and the Stylist's E1–E4 (stage 3)
Builder: E1 → R-47 (your default). E2 → R-47 (your default; field order as you list). E3 → R-59 (your default).
E4 → R-49 (your default: stale before cancelled). E5 → R-51 (restaurant zone, whole seconds). E6 → R-51/R-55
(revision 1, history [created] only). E7 → R-54 (internal only; not served anywhere in stage 3). E8 → confirmed: a
real amendment re-checks every resulting field against the resulting date's policy; a no-op only needs confirmed +
accepted cutoff (+ expected_revision). E9 → confirmed: `PolicyFor(date)` owned by the Builder.
Stylist: E1 → R-46 (your default, incl. first value for a repeated `explain`). E2 → R-52 refined: types pass first
(anchor_reference not a string → 400; count/interval_weeks of a non-integer JSON type → 422), then missing → 422 in
the order anchor_reference, count, interval_weeks, then ranges → 422 — observably identical to R-52; a generated
occurrence is in a series, so adopting it → 409 already_in_series. E3 → confirmed (created_at = adoption time,
revision 1, its own date's terms, one created entry; occurrence 0 can become an exception; series revision starts
at 1). E4 → confirmed: series logic in your package; the Builder's write paths call one hook.

## Answers to entry B's open questions (stage 3)
Q1 → R-54 (internal; not observable in stage 3; suites do not check it). Q2/Q3 → R-46 (confirmed). Q4/Q5 → R-51
(restaurant zone; created `at` = created_at instant; confirmed). Q6 → confirmed (404). Q7 → R-59 (confirmed).
Q8/Q20 → R-49/R-57 OVERRULE the B default: 404 → (R-44 both fields 422 sits in the type pass before the 404) →
422 invalid `expected_revision` → 409 `stale_revision` → 409 `reservation_cancelled` → 409 `cutoff_passed` → values.
Q9 → R-47 OVERRULES the B default for strings: every policy field problem, including strings or null in integer
fields, is 422 (booleans too); only a body that is not a JSON object is 400. Q10–Q12 → confirmed. Q13/Q14 → R-52
(confirmed). Q15–Q18 → confirmed (R-53). Q19 → R-51/R-55 OVERRULE the B default: seeded and imported bookings
have exactly one `created` entry and revision 1 whatever their status — a cancelled seed gets no `cancelled` entry.
Q21–Q24 → confirmed. Q25 → R-59 (`manager_user_ids` is echoed in the detail).
