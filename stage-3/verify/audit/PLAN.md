# Stage 2 — Auditor attack plan (online booking UI and combined tables)

Written from `stage-2.md` plus the still-binding stage-1 contract (C1.1–C1.134, R-1…R-28). Stage-2 clause ids
(C2.n) are attached once the master ledger is reconciled; until then checks carry spec section names ("S2 …").

## Files
| file | purpose |
|---|---|
| `audit.py` | HTTP battery: all stage-1 groups (regression, unchanged fixture without `combinable`) + `combo`, `comboburst`, `upgrade` |
| `run_attacks.sh` | candidates A (target) and B (fresh import destination) on an `--internal` network, 2 CPU / 2 GiB; `PREV_IMG=<accepted stage-1 image>` adds P for the upgrade |
| `ui_audit.py` | step 7: headless Chromium (Playwright 1.55) against the candidate; screenshots of every named state |
| `run_ui.sh`, `Dockerfile.ui` | browser runner `auditor-ui-runner` on the candidate's internal network (`PREV_IMG` optional) |
| `run_oracle.sh`, `run_diff.sh` | steps 2–3 (Oracle suite incl. browser tests; differential runner) |
| `mutate.py`, `race.py`, `race_detect.sh`, `readwrite_load.py` | steps 5–6, containerised (stage-1 lesson: host-native runs give false kills) |

Previous-stage image for the upgrade: the accepted stage-1 build of c0f2b7b (`auditor-s1-c5`; rebuilt from a clean
clone of c0f2b7b `stage-1/` if absent).

## HTTP attacks added for stage 2
| group | attacks | spec |
|---|---|---|
| combo | reset with `combinable`, seeded `table_ids` and `status: cancelled`; `available_options` (singles in fixture order, then pairs in `combinable` order with ids in `combinable` order, capacity = sum, every member free) over 7 party/slot cases, incl. a pair declared out of fixture order, cancelled seeds not blocking, half-open release; `available_table_ids` unchanged; restaurant without `combinable` → singles only; POST pair → `table_ids`, no `table_id`, GET/list identical; member singles and sharing pairs → 409; reversed pair order accepted; `table_ids` of one → `table_id` present; `table_id` still accepted; error matrix (undeclared/non-transitive pair, 3 and 4 tables → `combination_not_allowed`; duplicate ids, both fields, neither → `validation_failed`; summed capacity exceeded → `party_exceeds_capacity`; string/null → 400; empty/numbers/unknown member soft pending rulings); idempotent replay of a pair; PATCH single→pair→single with release, every failure leaves the booking unchanged; cancel frees every member; moves with `table_ids` (swap single↔pair, results sharing a table → 409 and nothing changes, undeclared pair, both fields, wrong type); export→import into a fresh container keeps pairs and options | Model, API, UI (moves), Combined tables |
| comboburst | per round: K1 48 bids that all share `c_2` (two pairs + single) → exactly one 201; K2 two disjoint pairs, 20 bids each → exactly two 201; K3 8 single-item moves onto one pair → one 201, one booking moved; K4 8 PATCHes onto pairs sharing `c_3` → one 200; invariant over every member table | Concurrent bookings and amendments |
| upgrade | on the accepted stage-1 service: signup, keyed bookings, a batch, a failed key, a cancel, and a booking whose response is "lost"; export → candidate import: old tokens, identical reservations (+ `table_ids`), lookup, replays with original bodies (booking and batch), the lost booking's retry → original reference, reuse 409, failed key reusable, password login, new `table_ids` booking, no pairs on upgraded restaurants, options singles-only, idempotent re-import, current-format re-export into a fresh candidate | Existing clients after an upgrade, §7, §10 |

## Browser checks (battery step 7, `ui_audit.py`)
| check | what |
|---|---|
| u_routes | `/`, `/signup`, `/login`, `/lookup` → 200 `text/html`; search controls present; `restaurant-select` option values are ids |
| u_auth | signup signs in; `current-user` (with display name) on every route; `logout-button`; `auth-error` only on error (taken email, wrong password) |
| u_grid | every `slot-{table}-{HH:MM}` cell exists with `data-available` mirroring `available_table_ids` for the searched party; every available declared pair has a `slot-{a}+{b}-{HH:MM}` cell (ids in `combinable` order); closed day → `no-slots` and no cells |
| u_click_rules | unavailable cell → nothing; available cell signed out → `auth-error` or `/login` |
| u_booking | single and pair: form opens, `booking-summary` names every table and the time, party pre-filled; confirmation (`confirmation-reference` exactly a reference, details, `confirmation-tables`); reservation exists server-side; form stays; unchanged resubmit → same reference, same key+body, no new booking; changed field → new key |
| u_conflict | another client takes the table after the form opens → `booking-error`, no confirmation, form and inputs kept, cell refreshed to false |
| u_lost_after / u_lost_before | response aborted after (and before) the server commits → non-empty `booking-uncertain`, no error/confirmation; unchanged retry reuses key and body → original reference, uncertainty removed, exactly one booking |
| u_out_of_order | search A held, search B answered, A released → grid still B (cell values and full grid match B) |
| u_lookup | detail, status exactly `confirmed`/`cancelled`, `reservation-tables`, cancel removes the button and really cancels; unknown → `reservation-error`; refused cancel (inside cutoff) → `reservation-error` |
| u_states_distinct | available ≠ unavailable computed style; selected cell marked; loading marker (soft); screenshots of available/unavailable/selected/loading/empty/success/refused/uncertain/error |
| u_keyboard | Tab reaches search controls and `booking-submit`; Enter on a cell opens the form; visible focus; every visible input labelled |
| u_layout | no horizontal page scroll at 375, 768, 1280 px on every route, with the grid and with the form |
| u_import | with `--prev`: candidate imports a stage-1 export, legacy user signs in, legacy reference works on `/lookup`; then a lost booking, export/import between requests, no reload: still signed in, retry keeps key+body and shows the original reference |

## Other steps
- Step 1: RUN.md literally, internal network, 2 CPU / 2 GiB, health ≤ 60 s, no egress; UI assets must load with no egress (u_routes run on the internal network; any CDN/font fetch fails there).
- Step 5: mutation over `internal/…` incl. the new combination and UI-serving code, Oracle suite (HTTP + browser) as killer.
- Step 6: race proofs for every guard, incl. any new one for combination occupancy.
- Step 8: holdout `harness_win.py run --track tablekeeper --stage 2 --mode isolated` only after 1–7 are green.

## Clause mapping (master ledger, main 6c768ee)
Every HTTP check maps to C2.n / C1.n via `CLAUSE_RULES_S2` in `audit.py`; every browser check names its C2 clauses and
ruling (R-29 absent-not-hidden, R-32 HH:MM + ISO date, R-38 key/body reuse, R-39 combination cells, R-40 signed-out
navigation, R-30/R-37 upgrade).

## Self-test status
- HTTP: stage-1 groups unchanged (pass on the stage-1 build); `combo` 78/78 and `comboburst` 30/30 (5 rounds) on an
  interim Builder build (seat/builder 91230e1, WI-8 only); `upgrade` mechanics verified stage-1→stage-1 (14/18; the 4
  failures are stage-2 features) and red on 91230e1 because WI-9 is not there yet.
- Browser: runner and harness mechanics verified (Chromium launches on the internal network); the checks themselves are
  validated against the first build that serves the UI.

## Rulings applied
R-34 and R-35 (stage-2 rulings, main 4ddffa4): the six open points below are now hard checks — `[]` → 422; non-string/null members → 400; unknown or foreign table → 404 before the undeclared-pair 422; full R-34 error order (7 precedence checks); pair responses in declared `combinable` order; reordered pair under the same key → 409; 10 refused reset fixtures (unknown table in a pair, pair of one/three, self-pair, duplicate pair either order, seed naming an undeclared pair, seed with both/neither table fields, unknown seed status), each with state unchanged. Upgrade: two-part reading confirmed.

## Former open points (answered by R-34/R-35)
1. `table_ids: []` — 422 `validation_failed`?  2. `table_ids` with non-string members — 400?  3. a pair naming an unknown
   table or a table of another restaurant — 404 `not_found` or 422 `combination_not_allowed`?  4. response order of a
   pair's `table_ids` — request order, `combinable` order, or any?  5. same key with the pair listed in the other order —
   a different body (409) or the same request?  6. reset fixture validation for `combinable` (unknown table, a pair of one
   or three, a table paired with itself, duplicate pairs) and for seeded `table_ids` naming an undeclared pair.

### Dry run on the Stylist branch d066ce9 (WI-8 + WI-9 + UI; not a candidate, not a verdict)
- HTTP battery incl. upgrade from the stage-1 image: 718 checks, 0 failures.
- Browser: after fixing three harness bugs (stale session in the login helper; 22:00 cells beyond the last 21:30 slot;
  a 25-press Tab budget too small for a 140-cell grid), the only failures are against rulings issued after that build:
  R-39 (cells for pairs below the party size at party 7), R-40 (signed-out lookup does not navigate to /login), R-32
  (summary/details show "Thu, 29 Oct 2026 · 19:00" without the ISO date).
