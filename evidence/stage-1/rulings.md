# Stage 1 rulings

Decided by: Foreman. Rulings bind every seat for the rest of the run. Clause ids: master id `C1.<n>` equals
entry-A id `A<n>` for n ≤ 124; clauses found only by entry B are appended from `C1.125` at reconciliation.
R-1 … R-18 were issued from entry A before reconciliation (allowed while entry B is written); any change after
reconciliation is a new numbered ruling that supersedes the old one explicitly.

## R-1 — Error precedence on keyed writes (POST /reservations, POST /reservation-moves)
- Clauses: C1.34, C1.35, C1.36, C1.45, C1.59, C1.60–C1.64
- Ambiguity: §7 fixes idempotency "after the body has been parsed as a JSON object and the caller authenticated"
  but does not order the header checks or per-field type errors.
- Options: (1) type errors before idempotency; (2) type errors after idempotency.
- Decision: 401 `unauthenticated` → 400 `malformed_request` (body does not parse, or is not a JSON object) →
  400 `missing_idempotency_key` (absent or empty) → 422 `validation_failed` (key longer than 255) → idempotency
  (replay 200 / 409 `idempotency_key_reuse`) → endpoint field checks (missing 422, wrong type 400, formats 422)
  → resource and domain checks.
- Rationale: literal reading — per-field type checks are "endpoint-specific field validation", which §7 puts
  after idempotency. Rejected (1): it would answer a reused key with 400 where §7 says 409.

## R-2 — JSON `null` in a request field
- Clauses: C1.34, C1.42, C1.44
- Ambiguity: is `"table_id": null` an absent field (422/ignored) or a field of the wrong JSON type (400)?
- Decision: `null` is a value of the wrong JSON type → 400 `malformed_request`, in every endpoint and in PATCH and
  move items too (null never means "omit"). Exception: `party_size: null` → 422 `validation_failed` (C1.42 makes
  every invalid party_size value 422).
- Rationale: literal reading — null is a JSON type distinct from string/number. Treating null as "keep current"
  in PATCH would silently accept a client's attempt to clear a field. Rejected: null-as-absent (Builder's
  proposal), because it turns a type error into a 422 or a silent no-op.

## R-3 — Integers in bodies and queries
- Clauses: C1.42, C1.43, C1.65, C1.86
- Decision: in a JSON body an integer is any JSON number whose value is integral (`4` and `4.0` are 4); `4.5`,
  strings, booleans and null for `party_size` → 422. Query integers must match `^[0-9]+$` (C1.43). Idempotency
  "same body" compares numbers by value.
- Rationale: C1.43's digits-only rule is stated for query parameters "whatever their numeric value", implying
  the body rule is about value. Rejected: treating `4.0` in a body as non-integer (no text supports it).

## R-4 — Accounts
- Clauses: C1.47–C1.52
- Decision: emails are compared case-insensitively (uniqueness and login) and stored as given. Valid email:
  exactly one `@`, non-empty local part and domain, no whitespace. `display_name` is a required string; empty
  (after trimming) → 422. Password length counts Unicode code points (< 8 → 422). Missing field → 422, wrong type
  → 400. Signup order: 400 → 422 (fields in order email, password, display_name) → 409 `email_taken`.
- Rationale: case-insensitive matching is the conservative choice for state integrity (no two accounts for one
  mailbox). Rejected: exact-case matching.

## R-5 — Slot grid and DST
- Clauses: C1.21, C1.74, C1.100–C1.104
- Decision: candidate starts are local wall-clock times `opens + k·slot_minutes` on the local date. A candidate
  that does not exist (spring forward) is omitted; a repeated one (fall back) appears once, resolved to the
  first occurrence. A slot is offered iff `instant(start) + duration ≤ instant(local closes on that date)`
  (absolute minutes). Booking uses the same rules: on grid = `(local start − opens)` is a non-negative multiple
  of `slot_minutes` in wall-clock minutes.
- Rationale: §9 speaks of skipped/repeated *local times* never appearing, which only makes sense on a wall-clock
  grid; durations are absolute by §9. Rejected: an absolute-time grid (would produce non-round local slots).

## R-6 — Cutoff boundary
- Clauses: C1.23, C1.94, C1.97, C1.117
- Decision: cancel/amend/move allowed iff `starts_at − now > cancellation_cutoff_minutes`; exactly at the boundary
  is "within" → 409 `cutoff_passed`. `now` is the real current time.
- Rationale: inclusive "within" is the conservative reading for stored state.

## R-7 — POST /reservations validation order
- Clauses: C1.82–C1.88, C1.40–C1.42
- Decision (after R-1): fields checked in order `restaurant_id`, `table_id`, `starts_at_local`, `party_size`; for
  each: missing → 422, wrong type → 400 (party_size: any invalid → 422), bad format → 422. Then 404 `not_found`
  (restaurant unknown, table unknown or of another restaurant) → 422 `invalid_local_time` → 422
  `outside_opening_hours` (closed day, start outside [opens, closes), or end after closes) → 422
  `not_on_slot_grid` → 422 `party_exceeds_capacity` → 409 `table_unavailable`.
- Rationale: the grid is defined from opening time, so a start outside the opening window is reported as
  outside_opening_hours; occupancy is the last, state-dependent check. Rejected: grid before hours.

## R-8 — Reset fixture validation
- Clauses: C1.12, C1.18, C1.26
- Decision: unparseable body or wrong JSON types → 400 `malformed_request`; missing required fields, ids > 64
  chars, unknown timezone, invalid HH:MM/weekday → 422 `validation_failed`. A refused reset changes nothing.
  `closes: "24:00"` is accepted as end of day.
- Rationale: matches §5; refusing atomically protects state.

## R-9 — Unknown routes and methods
- Clauses: C1.32, C1.38
- Decision: unknown path → 404 `not_found`; known path, unsupported method → 405 `method_not_allowed`; both with
  the §5 envelope.

## R-10 — Authorization on public endpoints
- Clauses: C1.53, C1.68
- Decision: `/health`, reset, export, import, signup, login, `GET /restaurants`, `GET /restaurants/{id}`,
  `GET /availability` ignore any Authorization header, even an invalid one.

## R-11 — List orders
- Clauses: C1.69, C1.89
- Decision: `GET /restaurants` in fixture order. `GET /reservations`: `starts_at` descending; ties by
  `created_at` ascending, then `reference` ascending.

## R-12 — Availability parameter precedence
- Clauses: C1.71, C1.43
- Decision: missing parameter → 422; `date` not a real `YYYY-MM-DD` → 422; `party_size` not `^[0-9]+$` or 0 → 422;
  then unknown restaurant → 404. Parameter checks in order restaurant_id, date, party_size.

## R-13 — Cancel
- Clauses: C1.91–C1.95
- Decision: the request body is ignored when absent or empty; a non-empty body that does not parse → 400.
  Order: 401 → 400 → 404 → already cancelled → 200 current state (even past cutoff) → 409 `cutoff_passed`.

## R-14 — PATCH
- Clauses: C1.96–C1.99
- Decision: success is 200 with the full reservation. Order: 401 → 400 (body parse / not an object / wrong field
  type) → 404 not the caller's → 409 `reservation_cancelled` → 409 `cutoff_passed` → field values 422 → 404
  table → R-7 time/capacity rules → 409 `table_unavailable`. An empty subset or values equal to the current ones
  is a no-op 200 that still requires a confirmed booking outside its cutoff. The booking's own current
  occupancy never conflicts with its new one.

## R-15 — Reservation moves
- Clauses: C1.113–C1.124
- Decision: after R-1: `moves` missing / not an array / length 0 or > 8 / an item not an object / `reference`
  missing or not a string / duplicate references → 422. A PATCH field of the wrong JSON type inside an item →
  400 (party_size → 422). Then references resolved in input order (first unknown or foreign → 404); then all
  bookings in one restaurant, else 422. Then per item in input order: 409 `reservation_cancelled`, 409
  `cutoff_passed` (every listed booking, even a no-op item), field values 422, 404 table, R-7 time and capacity
  rules. Only then occupancy of all resulting bookings against each other and against unlisted confirmed
  bookings → 409 `table_unavailable`. Listed bookings' old occupancy is released for the check (swaps succeed).

## R-16 — Seeded reservations
- Clauses: C1.30
- Decision: seeded bookings are `confirmed`; `created_at` is the fixture's value if it is a valid RFC 3339
  timestamp, else the reset time. Their references count for uniqueness. Seeded data is trusted (not
  re-validated against grid/hours/overlap) beyond R-8 shape checks.

## R-17 — Request Content-Type
- Clauses: C1.14
- Decision: not enforced; bodies are parsed as JSON whatever the header.

## R-18 — Idempotency records
- Clauses: C1.57–C1.67
- Decision: records are keyed by (user, method, path, key); only 2xx outcomes are stored (with the canonical
  body and the original response); claim, execute and store happen in one serialised step.
