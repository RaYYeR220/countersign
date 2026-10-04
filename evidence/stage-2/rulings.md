# Stage 2 rulings

Decided by: Foreman. Stage-1 rulings R-1 … R-28 continue to bind (R-26 withdrawn). Stage-2 rulings continue the
numbering. Clause ids: master `C2.<n>` = entry-A row `A<n>` (stage 2) where both readers found it; entry-B-only
clauses are appended at reconciliation. R-29 … R-38 were issued from entry A before reconciliation.

## R-29 — Conditional UI elements are absent, not hidden
- Clauses: C2.8, C2.18, C2.19, C2.20, C2.27, C2.31, C2.34
- Decision: `auth-error`, `booking-error`, `booking-uncertain`, `reservation-error`, `confirmation`,
  `reservation-detail`, `no-slots`, `current-user`, `logout-button`, `reservation-cancel-button` are in the DOM
  only while their state holds; otherwise they are absent (not merely hidden). `current-user` and `logout-button`
  are absent when signed out. When `slots` is empty, `no-slots` is rendered and `availability-grid` is absent;
  when there are slots, `availability-grid` is rendered and `no-slots` is absent.
- Rationale: "Present only when there is one", "Shown instead of the grid", "Absent once cancelled" — absence is
  the literal and least ambiguous observable. Rejected: CSS-hidden elements.

## R-30 — Session in the browser
- Clauses: C2.19, C2.35, C2.37
- Decision: the token, user id and display name are kept in `localStorage` so every route knows the session and a
  session survives navigation and an export/import upgrade (tokens survive import). Logout clears them. A 401 from
  the API clears the session and shows `auth-error`.

## R-31 — Signed-out actions
- Clauses: C2.30, C2.34
- Decision: clicking an available cell while signed out shows `auth-error` on `/` (with a link to `/login`) and
  keeps the search; using the lookup screen while signed out shows `auth-error` with a link to `/login`.

## R-32 — Rendering of local start time
- Clauses: C2.31, C2.33
- Decision: `booking-summary` and `confirmation-details` contain the local start time as 24-hour `HH:MM` (exactly as
  in `starts_at_local`) and the local date (both the ISO `YYYY-MM-DD` and a readable weekday/day/month form).
  Table labels appear as given in the fixture (e.g. "Table 1"-style wording may wrap them, but the fixture label
  text must appear verbatim).

## R-33 — Combination cells
- Clauses: C2.55, C2.28
- Decision: a combination cell `slot-{t_a}+{t_b}-{HH:MM}` (ids in `combinable` order) is rendered only when that
  pair is in the slot's `available_options` for the searched party size, and then carries `data-available="true"`.
  Single cells exist for every table for every slot with `data-available` per C2.28. `HH:MM` is taken from
  `starts_at_local`.
- Rationale: "shown when a declared pair is available for the searched party size".

## R-34 — `combinable` fixture validation
- Clauses: C2.40, C2.43 (extends R-8/R-20)
- Decision: `combinable` is optional (absent → `[]`). Reset → 422 `validation_failed`, state unchanged, when an
  entry is not an array of exactly 2 strings, repeats a table, names a table not in that restaurant, or repeats a
  pair (in either order). Seeded reservations: `status` absent → confirmed; `"confirmed"` or `"cancelled"` only,
  else 422; exactly one of `table_id` / `table_ids` (both or neither → 422); a seeded `table_ids` follows the same
  set rules as POST (1 table, or a declared pair). Seeded cancelled bookings occupy nothing.

## R-35 — Table-set validation order (POST, PATCH, move items)
- Clauses: C2.46–C2.53, C2.58
- Decision: after R-1/R-19 (types 400 → missing 422 → values 422): both `table_id` and `table_ids` present → 422
  `validation_failed`; `table_ids` not an array of strings → 400; empty → 422 `validation_failed`; duplicate id →
  422 `validation_failed`; more than two ids → 422 `combination_not_allowed`; then 404 (restaurant, any table
  unknown or of another restaurant); then a pair not declared → 422 `combination_not_allowed`; then the stage-1
  time rules (R-7); then `party_exceeds_capacity` against the summed capacity; then 409 `table_unavailable` if any
  member is occupied. POST requires one of the two fields (neither → 422).
- Responses: `table_ids` is `[id]` for a single table and the declared `combinable` order for a pair, whatever the
  input order; `table_id` present exactly when one table.
- Idempotency compares parsed JSON values literally (§7): a replay that lists a pair in the other order is a
  different body → 409 `idempotency_key_reuse`.

## R-36 — PATCH and moves with table sets
- Clauses: C2.54, C2.58
- Decision: PATCH/move items accept either `table_id` (a set of one) or `table_ids`; both → 422. Supplying the
  same set in the other order is not a change. Occupancy checks cover every member of every resulting set.

## R-37 — Upgrade from stage-1 exports
- Clauses: C2.35–C2.37
- Decision: import accepts `format_version: 1` exports from the stage-1 service (state schema 1) and migrates them:
  every reservation gains `table_ids: [table_id]`, restaurants gain `combinable: []`; users, tokens, references,
  timestamps and idempotency receipts (with their original stage-1 response bodies, replayed verbatim) are kept.
  The envelope stays `track: "tablekeeper", format_version: 1`; the inner schema number distinguishes versions.

## R-38 — Retries in the browser
- Clauses: C2.8, C2.32
- Decision: the booking form generates one idempotency key per distinct (table set, start, party size) submission
  and keeps it in memory until a field changes. A resubmission with unchanged fields reuses key and body exactly
  (byte-identical JSON). A network failure or a 5xx keeps the key and shows `booking-uncertain`; a 4xx shows
  `booking-error` (409 also refreshes availability, keeping the form).

## R-39 — Combination cells (supersedes R-33)
- Clauses: C2.55, C2.28
- Ambiguity: "combination cells, shown when a declared pair is available for the searched party size" next to
  "Carries `data-available` like a single cell". R-33 read "available" as "free", which makes `data-available`
  always true and the second sentence pointless; the Stylist built a cell for every pair in every slot.
- Decision: in every slot, a combination cell `slot-{t_a}+{t_b}-{HH:MM}` (ids in `combinable` order) is rendered for
  every declared pair whose summed capacity ≥ the searched party size; it carries `data-available="true"` exactly
  when the pair is in that slot's `available_options`, else `"false"`. Pairs whose summed capacity is below the
  party size have no cell. Clicking a `false` combination cell does nothing.
- Rationale: "available for the searched party size" = able to seat that party; "like a single cell" = true/false by
  occupancy. Rejected: R-33 (free-only), and a cell for every pair regardless of capacity.

## R-40 — Signed-out actions (supersedes R-31)
- Clauses: C2.30, C2.34
- Decision: clicking an available cell while signed out navigates to `/login`; after a successful sign-in the
  diner returns to `/` with the search and selection restored and the booking form open. Using the lookup screen
  while signed out navigates to `/login` and returns to `/lookup` after sign-in. (The spec allows `auth-error` or
  navigation; navigation is the behaviour already built.)

## Answers to entry B's open questions (stage 2)
Q1 → R-35 (`[]` → 422, confirmed). Q2 → R-35 (404 before the combination rule, confirmed). Q3 → R-35 (after 404,
before invalid_local_time / hours / grid / capacity / overlap, confirmed). Q4 → a single option's capacity is the
table's capacity (confirmed). Q5 → R-35 (declared order everywhere, confirmed). Q6 → R-35 (types 400; duplicates
→ validation_failed before the more-than-two rule, confirmed). Q7 → R-36 + R-22 (422 at that item in input order,
confirmed). Q8 → R-34 OVERRULES the B default: a seeded set must be one table or a DECLARED pair; an undeclared
seeded pair → reset 422, and import refuses it too. Q9 → R-39 (pair cell for every declared pair whose summed
capacity ≥ party size; `data-available` true iff in available_options; no cell for pairs below the party size).
Q10 → R-37 (export envelope stays `format_version: 1`; the inner schema number distinguishes stages; import accepts
stage-1 exports and its own). Q11 → confirmed. Q12 → confirmed (key omitted). Q13 → confirmed (declared order).

## R-41 — "Using the lookup screen while signed out" (clarifies R-40)
- Clauses: C2.34, C2.3
- Decision: `/lookup` is reachable by URL while signed out and shows the lookup form (C2.3 "reachable by URL").
  Submitting a lookup while signed out navigates to `/login`; after sign-in the diner returns to `/lookup` and the
  lookup runs. Opening `/lookup` does not by itself redirect.
- Rationale: R-40 adopted the behaviour already built, which redirects on use (submission); redirecting on open
  would make a required route unreachable by URL for a signed-out visitor. Tests expecting a redirect on open are
  test defects.

## R-42 — Empty or over-long ids in request bodies
- Clauses: C1.18, C1.41, C2.46–C2.53 (extends R-19/R-35)
- Decision: in POST /reservations, PATCH and move items, a `restaurant_id`, `table_id` or `table_ids` member that is
  the empty string or longer than 64 characters is an invalid value → 422 `validation_failed` in the R-19 value
  pass, before any 404. Within `table_ids`, this check runs with the other set checks after "both fields" and
  "empty set" and before duplicates (so `[""]` → 422 validation_failed, `["", "c_1"]` → 422 validation_failed).
  A moves `reference` that is empty is a structural error (R-22 a) → 422. Path parameters are unaffected (R-25).
- Rationale: C1.18 makes ids non-empty strings of at most 64 characters and C1.41 makes an invalid format a 422;
  R-8/O-11 already treat an empty fixture id as 422. Strict reading of a stated format (lesson of R-26/R-28).
