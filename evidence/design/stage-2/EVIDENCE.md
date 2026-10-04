[EVIDENCE] stage=2 wi=WI-10 sha=582f707b91a7e4e560ef237156a2ed0e2df97e33

# Evidence — stage-2 browser product (Stylist)

- Revision: 582f707b91a7e4e560ef237156a2ed0e2df97e33 on seat/stylist (main 1ad4ec6 merged). Screenshots were
  re-captured against the Docker image and committed on top of it.
- Design direction: evidence/design/direction.md
- Code: stage-2/internal/web/ holds web.go, which embeds static/index.html, app.css and app.js. Routes are
  added in stage-2/internal/api/server.go: GET/HEAD `/`, `/signup`, `/login` and `/lookup` → HTML shell;
  `/assets/{app.js,app.css}`; anything else → the JSON 404 as before. Nothing is fetched from another origin
  (system font stacks, inline SVG favicon). TestScreenRoutesServeHTML checks this.

## Screens and states covered (each at 375, 768 and 1280 px → `<width>-<nn>-<state>.png`)
01 search before first search · 02 loading skeleton · 03 results (available / booked / overlapping cells) ·
04 no-slots (closed day) · 05 search error (connection failed, no stale grid) · 06 sign-in required after a
signed-out click on an available cell · 07 login auth-error · 08 selected cell + booking form · 09 refused
(party exceeds capacity → booking-error) · 10 success (confirmation, form kept) · 11 conflict (409 after
another client took the table: booking-error, refreshed grid, inputs kept, no confirmation) · 12 uncertain
(booking committed, response lost: booking-uncertain only) · 13 recovered after export → reset → import
(still signed in; the retry with the same key and body shows the original reference; exactly one booking) ·
14 keyboard focus on a grid cell (Tab, then Enter opens the form) · 15 lookup found · 16 lookup cancelled
(cancel button absent) · 17 lookup not found · 18 cancel refused (cutoff → reservation-error, status
unchanged) · 19 signup · 20 signup auth-error (email taken) · 21 lookup while signed out · 22 combination
cells and pair selection (stage-2 response shape mocked in the browser; see Known gaps).

## Behaviour asserted by the run (201 checks)
- Out-of-order: search A (party 6) is held while search B (party 2) completes; releasing A does not change
  the grid. Only the newest search may render (sequence number), and the restaurant detail (labels) and
  availability belong to the same search.
- `data-available` is exactly membership in `available_table_ids` (singles) or `available_options` (pairs).
  Clicking an unavailable cell does nothing: it stays an enabled button so the click is accepted.
- An unchanged resubmission after success returns the same `confirmation-reference`, with exactly one booking
  on the server. Any edit to the form issues a new idempotency key.
- No horizontal scrolling at any width in any state (`scrollWidth <= innerWidth`).
- `auth-error`, `booking-error`, `booking-uncertain`, `reservation-error`, `confirmation`, `no-slots` and
  `reservation-cancel-button` are absent from the DOM when not applicable.

## Commands
### `cd stage-2 && go vet ./... && go test ./...`
Exit code: 0 (api, jsonin, localtime, password, state ok; includes TestScreenRoutesServeHTML)
### `node --check stage-2/internal/web/static/app.js`
Exit code: 0
### `python factory/tools/bg.py run --timeout 600 -- docker build -t stylist-tk2:ui stage-2`
Exit code: 0
### `bg.py start --name stylist-tk2 -- docker run --rm --name stylist-tk2 --cpus 2 --memory 2g -e PORT=8080 -p 127.0.0.1:18211:8080 stylist-tk2:ui`
healthy
### `PYTHONUTF8=1 C:/countersign/.venv/Scripts/python.exe evidence/design/stage-2/ui_check.py --base http://127.0.0.1:18211`
Exit code: 0
```
201/201 checks passed
```
### `bg.py stop --name stylist-tk2`; `docker stop stylist-tk2`
`docker ps -a --filter name=stylist` is empty.

## Known gaps
- Combined tables against the real core: step 22 uses mocked stage-2 responses (`combinable` on
  GET /restaurants/{id}, `available_options` on availability), because the Builder's WI-8 is not merged yet.
  The UI sends `table_id` for one table and `table_ids` (in combinable order) for a pair, and reads
  `table_ids`/`table_id` from responses. I will re-run against the real core once WI-8 lands.
- Upgrade from a stage-1 export: step 13 exercises export/import on the stage-2 service. The stage-1 → stage-2
  migration is the Builder's WI-9. The UI side (token in localStorage, form and pending key/body in memory)
  needs no change for it.
- Open ESCALATION Q1 (combination cell presence; built to option (a): every declared pair in every slot) and
  Q2 (start-time text; built as "Tue, 3 Nov 2026 · 19:00").

---

# Addendum — WI-10, WI-11, WI-12 conformed to R-29 … R-40, plus the real core

- Revision: 78e1acc9af3578ab8d1211b374f07021528de216 (code and check script). It merges seat/builder 15035d7
  (WI-8 combined tables, WI-9 schema-2 migration) and main bcc4ef7 (R-39, R-40). Screenshots were re-captured
  against the Docker images and committed on top of it.
- Changes:
  - R-39: a combination cell exists for every declared pair whose summed capacity ≥ the searched party, with
    `data-available` true exactly when the pair is in `available_options`. Pairs too small for the party have no
    cell.
  - R-32: `booking-summary`, `confirmation-details` and the lookup "When" read e.g.
    "Tue, 3 Nov 2026 · 19:00 (2026-11-03)". Fixture labels appear verbatim ("Table 2", "Window", "Tables 1 + 2").
  - R-40: a signed-out lookup goes to /login, then returns to /lookup and runs the lookup. A signed-out cell
    click goes to /login, then returns to / with the form open (as before).
  - R-30: an API 401 clears localStorage and shows `auth-error` (with a Sign in link) inside the
    `booking-error` or `reservation-error` panel. Signing in again returns to the same form or lookup.
- The mocked combination step was replaced by the real core.

## Command
### `PYTHONUTF8=1 C:/countersign/.venv/Scripts/python.exe evidence/design/stage-2/ui_check.py --base http://127.0.0.1:18211 --stage1 http://127.0.0.1:18212`
- Stage-2 image: stylist-tk2:ui2, built from stage-2 at 78e1acc.
- Stage-1 image: stylist-tk1:accepted, built from the accepted stage-1/.
- Both were started with bg.py (`--cpus 2 --memory 2g`) and stopped afterwards; `docker ps -a --filter name=stylist`
  is empty.

Exit code: 0
```
240/240 checks passed
```
New checks, at each of 375, 768 and 1280 px:
- `grid == API`: every `slot-*` cell compared with GET /availability and GET /restaurants/{id} for parties 2, 6
  and 7, so exactly the cells R-39 requires exist, and each has the right `data-available`.
- At party 7 the t_1+t_2 pair (6 seats) has no cell.
- Pair booking through the UI: the summary names both tables, HH:MM and the ISO date. `confirmation-tables` and
  lookup `reservation-tables` list both labels, and the server holds `table_ids` ["t_1","t_2"].
- 401 after a server reset: `booking-error` + `auth-error` are shown, `current-user` is gone and the
  localStorage session is cleared (screenshot 24).
- Lookup while signed out goes to /login, and after sign-in back to /lookup with the detail shown (21).
- Stage-1 → stage-2 upgrade (C2.35–C2.37), with no page reload:
  - The browser's API calls go to the real stage-1 container; sign-in and booking A succeed there.
  - Booking B commits on stage 1, but its response is dropped, so the UI shows `booking-uncertain` (25).
  - The stage-1 export is imported into stage-2 (200 / 204), and the browser is switched to stage-2.
  - Pressing Try again shows a reference equal to the one stage 1 committed. Uncertainty is gone and the user
    is still signed in (26).
  - Looking up booking A's reference on stage-2 shows "confirmed".
Screenshots now number 26 states × 3 widths: evidence/design/stage-2/<width>-<nn>-<state>.png.

## Known gaps
- none
