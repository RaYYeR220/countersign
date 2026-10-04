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
