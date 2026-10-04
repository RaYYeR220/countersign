[LEDGER] stage=2 part=1/1

# Clause ledger — stage 2, entry A

Specification: C:/countersign/kickoff/tablekeeper/spec/stage-2.md (240 lines)
Reader: Foreman
File: `evidence/stage-2/ledger-A.md`

Written from the specification alone, before reading entry B. Ids are `A<n>`; master ids `C2.<n>` are assigned at
reconciliation. Stage-1 clauses C1.1–C1.134 and rulings R-1…R-28 continue to apply ("The stage-1 requirements
continue to apply").

| id | quoted requirement | observable behaviour | error precedence | acceptance criterion | reader | ruling |
|----|--------------------|----------------------|------------------|----------------------|--------|--------|
| A1 | "The stage-1 requirements continue to apply, with the additions below." | Every stage-1 check still passes on the stage-2 image | n/a | Stage-1 acceptance suite and holdout green against stage-2 | A | - |
| A2 | "Diners can search, book and manage reservations in a browser. Restaurants can offer approved pairs of tables for larger parties." | Browser UI + combinations | n/a | see below | A | - |
| A3 | "The following screens must be reachable by URL. Other screens must be reachable through the UI. Server-side and client-side rendering are both permitted." Routes `/` search and availability grid, `/signup`, `/login`, `/lookup` | GET of each route → 200 HTML rendering that screen; booking form and confirmation reachable from `/` | n/a | Headless browser opens each URL and finds the screen's testids | A | - |
| A4 | "A screen route returns HTML; §3.4's `application/json` convention is about the API, and does not govern the routes in the table above." | `Content-Type: text/html; charset=utf-8` on the four routes | n/a | Header check | A | - |
| A5 | "The UI must handle responses arriving out of order and connections failing after submission." | | n/a | see A6–A8 | A | - |
| A6 | "If search A starts before search B but finishes after it, the grid, table labels and booking form must describe B. A late response must not restore A's results." | Only the latest search's response is rendered | n/a | Delay A's /availability response; B renders; A's late arrival changes nothing | A | - |
| A7 | "If another client takes a table after the form opens, a `409 table_unavailable` response shows `booking-error` and refreshes availability. Preserve the selected form and its inputs so the diner can change their choice. Do not show a confirmation for that attempt." | booking-error visible; grid re-fetched; form + party size kept; no confirmation | n/a | Take the table via API, submit → booking-error, grid cell now false, form still open with typed values, no `confirmation` | A | - |
| A8 | "If a booking response is lost, including after the booking commits, show nonempty `booking-uncertain` text, without `booking-error` or a new confirmation. The unchanged form must retry with the same idempotency key and body. A successful retry removes the uncertainty/error elements and shows the original reference. A confirmed rejection uses `booking-error`." | Network failure → booking-uncertain; resubmit reuses key+body; success → uncertain gone, confirmation with original reference; a 4xx on retry → booking-error | n/a | Abort the POST after commit; booking-uncertain shown; submit again → 200 replay, confirmation-reference equals the committed reference; exactly one booking exists | A | Open: element presence |
| A9 | "These rules apply to combination bookings too. No background polling, live updates, cross-tab storage synchronization, or recovery across a page reload is required. The server remains authoritative; the browser must not manufacture a successful result from cached data." | Confirmation only from a server 2xx | n/a | Uncertain state never shows a confirmation until a 2xx arrives | A | - |
| A10 | "The UI must expose the `data-testid` attributes listed below for integration testing. Additional elements are permitted, and the visual implementation is the team's choice subject to the product-quality requirements below." | Exact testids | n/a | Each listed testid present in its state | A | - |
| A11 | Product and visual direction: "coherent, presentation-ready restaurant product … warm, confident hospitality character … obvious visual hierarchy … Combined tables should read as intentional seating options, not as concatenated technical identifiers." | Designed UI; pairs labelled with table labels | n/a | Screenshots reviewed; pair cells show e.g. "Tables 1 + 2", not "t_1+t_2" | A | - |
| A12 | "Use a consistent visual system for typography, spacing, colour, controls and feedback. Primary actions must be easy to identify. Available, unavailable, selected, loading, successful, refused and uncertain states must be visually distinct" | Distinct styling per state | n/a | Screenshots of each state differ visibly | A | - |
| A13 | "Use human-readable restaurant and table labels prominently; expose technical identifiers only where they help the user." | Names/labels shown, ids hidden | n/a | Grid header shows table labels | A | - |
| A14 | "The required flows must remain clear and usable at a 375 CSS-pixel viewport and at conventional desktop widths, without horizontal page scrolling. Inputs need visible labels, keyboard focus must be apparent, and text and controls need sufficient contrast." | No horizontal scroll at 375; labels; focus ring; WCAG AA | n/a | `document.documentElement.scrollWidth <= 375` on every screen/state at 375 px; every input has a `<label>`; tab reaches all controls | A | - |
| A15 | "Provide considered empty, loading and error states, and keep navigation consistent across the required routes. A custom illustration, brand asset or exact visual match to a reference is not required." | Same nav on every route | n/a | Nav links to /, /lookup, /login, /signup (or user + logout) on each route | A | - |
| A16 | Signup testids: `signup-email`, `signup-password`, `signup-display-name` inputs; `signup-submit` button | | n/a | Present on /signup | A | - |
| A17 | Login testids: `login-email`, `login-password`, `login-submit` | | n/a | Present on /login | A | - |
| A18 | "`auth-error` — Error message. Present only when there is one" | Absent from DOM unless an auth error is shown | n/a | Wrong password → auth-error visible; fresh page → no auth-error element | A | - |
| A19 | "`current-user` — Visible on every screen when signed in. Text contains the display name" | | n/a | After login, each route shows current-user containing display name | A | Open: when signed out |
| A20 | "`logout-button` — Button" | Signs out | n/a | Click → current-user gone, token forgotten | A | - |
| A21 | "`restaurant-select` — Selects a restaurant. Option values are restaurant ids" | `<select>` with option value = id, text = name | n/a | Options' values equal GET /restaurants ids | A | - |
| A22 | "`date-input` — Date, value `YYYY-MM-DD`" | | n/a | input value format | A | - |
| A23 | "`party-size-input` — Number" | | n/a | | A | - |
| A24 | "`search-button` — Runs the search" | Triggers GET /availability | n/a | | A | - |
| A25 | "`availability-grid` — Container for the results" | | n/a | | A | - |
| A26 | "`slot-{table_id}-{HH:MM}` — One cell per table per slot, e.g. `slot-t_2-19:00`" | One cell for every table of the restaurant for every slot; HH:MM from starts_at_local | n/a | Count = tables × slots | A | - |
| A27 | "`no-slots` — Shown instead of the grid when the day has no slots" | Closed day → no-slots, grid absent | n/a | Closed day search → no-slots present, availability-grid absent | A | Open |
| A28 | "Each cell carries `data-available="true"` or `data-available="false"`. A cell is `true` exactly when its `table_id` is in that slot's `available_table_ids` from `GET /availability` for the party size that was searched, and `false` otherwise." | | n/a | Compare every cell with API response | A | - |
| A29 | "Clicking an available cell opens the booking form for that table and slot. Clicking an unavailable cell does nothing." | | n/a | Click false cell → no booking-form | A | - |
| A30 | "Booking requires a signed-in user: clicking an available cell while signed out shows `auth-error` or navigates to `/login`, your choice." | | n/a | Signed out click → auth-error or URL /login | A | Open: choice |
| A31 | Booking form testids: `booking-form` container; `booking-summary` "Text contains the table label and the local start time"; `booking-party-size` "Number input, pre-filled from the search"; `booking-submit`; `booking-error` "Error message, when the booking fails" | | n/a | Summary contains label and HH:MM; party prefilled | A | Open: time format |
| A32 | "Keep the booking form on screen after success. Submitting it again without changing a field must return the same `confirmation-reference`, without `booking-error` or another booking. Changing a field makes the next submission a new booking request. Retries follow §7." | Same key+body on unchanged resubmit (200 replay); new key after change | n/a | Submit twice → same reference, one booking; change party → new booking request | A | - |
| A33 | Confirmation: `confirmation` container; `confirmation-reference` "Text is exactly the reference, no surrounding words"; `confirmation-details` "Text contains the restaurant name, table label and local start time" | | n/a | textContent.trim() == reference | A | - |
| A34 | Lookup: `lookup-reference-input`, `lookup-submit`; `reservation-detail` "Container, shown when found"; `reservation-status` "Text is exactly `confirmed` or `cancelled`"; `reservation-cancel-button` "Cancels. Absent once cancelled"; `reservation-error` "Shown when not found, or when a cancel is refused" | | n/a | Lookup own ref → detail with status; cancel → status cancelled, button absent; unknown → reservation-error; past booking cancel → reservation-error | A | Open: signed-out lookup |
| A35 | "A stage-2 service must accept an export produced by the same team's stage-1 service. A browser signed in before that export/import upgrade must remain signed in afterwards." | Stage-1 export imports (204); tokens valid | n/a | Export from stage-1 image, import into stage-2 → 204; old token works | A | - |
| A36 | "A retained booking reference still works through the lookup screen. A booking whose response was lost before export remains retryable after import with the same body and key; the UI must recover the original confirmation." | Receipts survive migration | n/a | Lost POST on stage-1, export, import to stage-2, retry from UI → original reference | A | - |
| A37 | "These requirements apply when import completes between browser requests; migration during an in-flight request is not required. No page reload or new screen is required. The form and pending retry identity must survive the upgrade." | In-memory UI state + same key | n/a | as A36 without reload | A | - |
| A38 | "A party may book two tables that the restaurant has declared combinable. The booking occupies both tables for its full duration." | Both tables blocked | n/a | Pair booking → neither table available in overlapping slots | A | - |
| A39 | "Existing single-table request formats remain supported." | `table_id` still works | n/a | Stage-1 POST body → 201 | A | - |
| A40 | Fixture `combinable`: "Each entry is an unordered pair of table ids in that restaurant. **Pairs only** — never three or more." | | invalid fixture → 422 (Open) | | A | Open: fixture validation; default `[]` |
| A41 | "A pair not listed cannot be combined, whatever the table sizes are. Combining is not transitive" | | n/a | [t_1,t_3] with pairs [t_1,t_2],[t_2,t_3] → 422 combination_not_allowed | A | - |
| A42 | "A combination's capacity is the sum of its tables' capacities." | | n/a | party 6 on 2+4 pair → 201; 7 → 422 party_exceeds_capacity | A | - |
| A43 | "Seeded `reservations` are `confirmed` unless they carry a `status` of `cancelled`, and may hold either `table_id` or `table_ids`." | Seeded cancelled doesn't occupy | n/a | Seed cancelled → table available; GET shows cancelled | A | Open: other status values, both fields |
| A44 | "Slots gain `available_options`. `available_table_ids` stays exactly as it was — single tables only." | | n/a | Shape check | A | - |
| A45 | "`available_options` lists every single table and every declared pair with `capacity >= party_size` and no overlapping confirmed reservation on any member. Singles first in fixture order, then pairs in `combinable` order. `table_ids` within a pair is in `combinable` order." | `{table_ids, capacity}` entries | n/a | Example from spec reproduced | A | - |
| A46 | "The body takes `table_ids` instead of `table_id`" | | n/a | POST with table_ids → 201 | A | - |
| A47 | "`table_id` is still accepted and means a set of one. Sending both is 422 `validation_failed`." | | both → 422 before table checks | | A | - |
| A48 | "Responses always carry `table_ids`. They also carry `table_id` **when the set has exactly one member**, and omit it otherwise." | | n/a | Single → both keys; pair → no table_id key | A | - |
| A49 | "The pair is not in `combinable` — 422 `combination_not_allowed`" | | Open order | | A | - |
| A50 | "More than two tables — 422 `combination_not_allowed`" | | | [t1,t2,t3] → 422 combination_not_allowed | A | - |
| A51 | "Any table in the set is taken for an overlapping interval — 409 `table_unavailable`" | | last | | A | - |
| A52 | "`party_size` exceeds the combination's summed capacity — 422 `party_exceeds_capacity`" | | | | A | - |
| A53 | "Duplicate table id in the set — 422 `validation_failed`" | [t1,t1] → 422 | | | A | - |
| A54 | "`PATCH /reservations/{reference}` accepts `table_ids` under the same rules. Cancelling frees every table in the set." | | | Cancel pair → both tables available | A | - |
| A55 | Combination cells: "`slot-{t_a}+{t_b}-{HH:MM}` — A combination cell, e.g. `slot-t_1+t_2-19:00`. Ids in `combinable` order. Carries `data-available` like a single cell" shown "when a declared pair is available for the searched party size" | | n/a | Pair in available_options → cell present, data-available true | A | Open: unavailable pairs |
| A56 | "`confirmation-tables` — Text contains every table label in the reservation"; "`reservation-tables` — On the lookup screen. Same" | | n/a | | A | - |
| A57 | "`booking-summary` must name every table in the selection. A single-table booking's cell testid, confirmation and lookup are unchanged." | | n/a | | A | - |
| A58 | "Atomic reservation moves from stage 1 also accept `table_ids` per move. No table may belong to overlapping resulting bookings. The existing browser recovery and original-receipt requirements also apply to combined-table bookings." | | | Move single → pair → 201 | A | - |
| A59 | "Concurrent requests must produce the same results as executing them one at a time in some order, and the requirements above hold at every read." | Linearizable | n/a | Bursts of pair/single conflicts: one winner; reads consistent | A | - |

## Open points (entry A's proposals)
- A8: `booking-uncertain` and `booking-error` are rendered only while their state holds (absent otherwise).
- A19: `current-user` and `logout-button` are absent when signed out; the token and display name live in
  localStorage so every route knows the session.
- A27: when `slots` is empty, `no-slots` is rendered and `availability-grid` is absent.
- A30: signed-out click on an available cell shows `auth-error` on `/` with a link to `/login`, keeping the search.
- A31/A33: "local start time" is rendered as 24-hour `HH:MM` together with the local date.
- A34: lookup while signed out shows `auth-error` with a sign-in link (the API needs a token).
- A40: reset 422 when a pair has ≠ 2 entries, repeats a table, names a table not in the restaurant, or repeats a
  pair (in either order). `combinable` absent → `[]`.
- A43: a seeded `status` other than `confirmed`/`cancelled`, or both `table_id` and `table_ids`, → reset 422.
- A47–A53 precedence: after R-1/R-19 (types 400, missing 422, values 422), `table_ids` shape: both fields → 422
  validation_failed; empty array → 422 validation_failed; duplicate id → 422 validation_failed; > 2 ids → 422
  combination_not_allowed; then 404 restaurant/tables; then pair not declared → 422 combination_not_allowed; then
  stage-1 time rules; then capacity (sum); then 409.
- A55: combination cells are rendered only when the pair is in `available_options` (always `data-available="true"`).
- Response `table_ids` order: single `[id]`; pair in declared `combinable` order whatever the input order.
- Idempotency: a reversed pair in a replayed body is a different JSON value → 409 reuse (literal §7).
