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

---
Rulings below were issued at reconciliation with entry B (Oracle's Q1–Q10 and the A/B conflicts).

## R-19 — Field-check order inside a body (supersedes the per-field order sentence of R-7)
- Clauses: C1.34, C1.40–C1.42, C1.44, C1.80 (Oracle Q1)
- Ambiguity: R-7 checked each field fully (missing → type → format) in field order; entry B checks all types
  first. They differ for e.g. `{"table_id": 5}` with `restaurant_id` missing.
- Decision: after R-1's header/idempotency steps, body fields are checked in three passes over the endpoint's
  fields in their documented order: (1) wrong JSON type → 400 `malformed_request` (R-2: null is a wrong type;
  `party_size` never gives 400); (2) missing required field → 422 `validation_failed`; (3) value format/range
  (incl. every invalid `party_size`) → 422 `validation_failed`. Applies to signup, login, POST /reservations,
  PATCH, move items.
- Rationale: §5 reserves 400 for a malformed body; a malformed body is reported before an incomplete one.
  Rejected: per-field interleaving (R-7 wording), which makes the code depend on field order for no reason.

## R-20 — Reset fixture referential checks (extends R-8 and R-16)
- Clauses: C1.12, C1.18, C1.26, C1.28, C1.30 (Oracle Q10)
- Decision: reset → 422 `validation_failed`, state unchanged, when: duplicate user ids, emails (case-insensitive),
  restaurant ids, table ids within a restaurant, reservation ids or references; a seeded reservation naming an
  unknown user, restaurant or a table not of that restaurant; `closes` not later than `opens`; a duplicate
  weekday within a restaurant. Seeded reservations are not checked against grid, hours, capacity or overlap.
- Rationale: referential integrity of stored state; rejecting is conservative. Rejected: silently accepting.

## R-21 — Timestamp offsets
- Clauses: C1.15
- Decision: every timestamp in a response carries a numeric `±HH:MM` offset; `Z` is never used. `created_at`
  is in UTC written `+00:00`, with whole seconds (no fraction). `starts_at`/`ends_at` use the restaurant's zone.
- Rationale: entry B reads "explicit offset" as excluding `Z`; the spec example uses `+00:00` for created_at.

## R-22 — Reservation-moves error order (supersedes the ordering part of R-15)
- Clauses: C1.114, C1.115, C1.117, C1.118 (Oracle Q5 and B170)
- Decision: after R-1: (a) structure → 422 `validation_failed`: `moves` missing, not an array, length 0 or > 8,
  an item not an object, `reference` missing or not a string, duplicate references; (b) wrong JSON type of an
  item's `table_id`/`starts_at_local` → 400 `malformed_request`; (c) then for each item in input order, the
  ordinary amendment checks: 404 `not_found` (unknown or another user's reference) → 422 `validation_failed`
  (booking's restaurant differs from the first item's booking) → 409 `reservation_cancelled` → 409
  `cutoff_passed` (also for no-op items) → 422 field values → 404 table → `invalid_local_time` →
  `outside_opening_hours` → `not_on_slot_grid` → `party_exceeds_capacity`; the first failing item decides;
  (d) only then occupancy of all resulting bookings → 409 `table_unavailable`.
- Rationale: "take precedence in input order" literally means the first item's error wins whatever its kind.
  Rejected: resolving all references before any other item check (R-15's old wording).

## R-23 — End-of-day check on DST days (confirms R-5 against entry B's Q8 default)
- Clauses: C1.74, C1.84, C1.103
- Decision: "slot + duration ≤ closes" and "would end after closes" compare absolute instants:
  `instant(start) + duration ≤ instant(closes on that local date)`. If `closes` falls in a skipped hour its
  instant is the transition instant; if repeated, its first occurrence. The grid itself stays wall-clock (R-5).
- Rationale: §9 makes duration absolute and §8 speaks of when the reservation *ends*; its real end is what a
  diner experiences (ends_at local reading). Rejected: wall-clock minute arithmetic (B's default), which
  would admit a booking whose ends_at reads after closes on spring-forward nights.

## Answers to entry B's open questions
Q1 → R-1 + R-19. Q2 → R-7 (B default confirmed). Q3 → R-7 (B default confirmed). Q4 → R-13/R-14 (B default
confirmed). Q5 → R-22 (non-string `reference` → 422; `moves` not an array or item not an object → 422).
Q6 → R-3 (`4.0` is the integer 4). Q7 → R-11 (ties: created_at ascending, then reference). Q8 → R-23
(absolute, overrides B default). Q9 → R-12 (422, B default confirmed). Q10 → R-20.

## R-24 — Import of an empty or unrecognised state
- Clauses: C1.109 (Auditor question 8)
- Decision: `state` must be an object this service recognises (its own schema marker and every required
  collection); `state: {}`, an unknown schema, or any structurally invalid state → 422 `validation_failed`,
  destination unchanged. Only an export produced by this service (or, from stage 2 on, by an earlier stage of
  this team's service) is accepted.
- Rationale: "an invalid state give[s] 422 … without changing the destination"; an empty object is not a state
  this service produced. Rejected: treating `{}` as an empty state (it would silently wipe the destination).

## Answers to the Auditor's attack-plan questions
1. Moves item `party_size: 0` → ordinary amendment error in input order after cancelled/cutoff (R-22 c), not a
   structural 422. 2. Item `table_id` a number → 400 `malformed_request` (R-22 b). 3. `moves` not an array /
   item not an object → 422 (R-22 a). 4. `PATCH {}` → 200 unchanged, still requires confirmed and outside cutoff
   (R-14). 5. PATCH moving a far booking to a start inside the cutoff → allowed; cutoff is measured on the current
   start (C1.97). 6. Body valid JSON but not an object → 400 `malformed_request` (R-1). 7. Signup without
   display_name / login without password → 422 (R-19 pass 2). 8. → R-24.

## R-25 — Non-canonical paths (extends R-9)
- Clauses: C1.38, C1.90 (Auditor escalation on candidate 2, differential class B)
- Ambiguity: the spec does not define paths with a trailing slash or empty segments (`/reservations/`,
  `/reservations//cancel`).
- Decision: any path that is not exactly one of the documented routes — including a trailing slash, an empty
  segment or a doubled slash — is an unknown path → 404 `not_found` with the §5 envelope. Path segments are never
  normalised, and an empty `{reference}` or `{id}` is never matched.
- Rationale: literal reading — only the documented paths exist; R-9 already maps unknown paths to 404.
  Normalising would make `/reservations/` (an empty reference) silently mean the collection. Rejected: dropping
  empty segments (the model's behaviour) and redirecting.

## R-26 — Format of seeded and imported references (C1.81 scope)
- Clauses: C1.81, C1.18, C1.30, C1.107, C1.109 (Auditor escalation on candidate 4)
- Ambiguity: "`reference` is 6 to 12 characters of `A-Z0-9`, unique across all reservations, and never changes"
  sits under `POST /reservations` — does it constrain references supplied in reset fixtures and carried in
  imported state, or only references the service issues?
- Options: (1) only issued references; seeded/imported references are opaque ids (non-empty, ≤ 64 characters,
  unique). (2) all references; reset and import must refuse non-conforming ones.
- Decision: (1). References the service generates satisfy `^[A-Z0-9]{6,12}$`. A reference supplied by a
  fixture (and therefore carried through export/import) is an opaque id: non-empty, at most 64 characters,
  unique across reservations; it is never rewritten. Import refuses (422) an empty, over-64-character or
  duplicate reference, but accepts any other string, so every export the service produces is importable
  (C1.107).
- Rationale: §4 gives seeded reservations "the same fields as a POST body plus `id`, `reference` and
  `user_id`" with no format beyond C1.18's id limit; R-16 makes seeded data trusted beyond shape checks;
  option (2) would also need reset to refuse fixtures the spec never forbids, and "never changes" forbids
  rewriting them. Rejected: (2).
- Consequence for the Oracle: hardening tampers that use a non-conforming but non-empty ≤ 64-character
  reference (e.g. 'bad ref', 'abcdef') expect 204, not 422 — replace them with empty, over-64-character or
  duplicate references. For candidate 4, the single failing tamper case is explained by this ruling.

## R-27 — Cross-group pairs in the tie-order test (verdict 4, F-2)
- Clauses: C1.89, R-11
- Question: `test_C1_89_tie_order_live_created_at` also asserts created_at order for consecutive bookings with
  different `starts_at` (FUT_DAY 15:00 vs FUT_FRI 19:00); it fails 4 of 5 runs against a correct order.
- Decision: R-11's created_at tie-break applies only between reservations with equal `starts_at`; reservations
  with different `starts_at` are ordered by `starts_at` descending alone. The cross-group assertion contradicts
  C1.89 and is a test defect; the test's R-11 assertions within groups pass in every run. For candidate
  1bfe6e2, step 2's two failing cases (F-1 under R-26, F-2 under this ruling) are explained gaps; the Oracle's
  fixes merge at stage close.
