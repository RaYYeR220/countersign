[EVIDENCE] stage=1 wi=WI-3 sha=ff7a4e3ed1d9b3c35eeab7361d0c55188244f4c8

# Evidence — WI-3 (local-time/DST engine and GET /availability)

- Revision: ff7a4e3ed1d9b3c35eeab7361d0c55188244f4c8 (code); this file is committed on top of it
- Branch: seat/stylist (main c01a001 merged in)
- Clauses claimed: C1.20, C1.21, C1.22, C1.24, C1.27, C1.28, C1.43, C1.71–C1.77, C1.100–C1.104 (engine side of
  C1.23/C1.25: `End`, `Overlaps`; the cutoff and capacity checks themselves are wired by WI-4)

## What was built
- `stage-1/internal/localtime/localtime.go`: a leaf package that imports only the stdlib and apperr. It provides
  `Resolve` (a skipped time → 422 `invalid_local_time`; a repeated time → its first occurrence),
  `ResolveOrAfter` (for seeded rows: a skipped time maps to the instant of the jump), `CheckStart` (R-7 order:
  invalid_local_time → outside_opening_hours → not_on_slot_grid), `Slots` (the R-5 wall-clock grid, with
  absolute end ≤ instant(closes)), `End`, `Overlaps` (half-open), `Format` (`-07:00`, never Z), `Location`
  (cached), `ValidLocal`, `ValidDate`, `Weekday`, and `type Hours`.
- `state.OpeningHours` = `localtime.Hours` (alias); `state.Reservation` gains `StartsAt` / `EndsAt`. Seeded
  reservations get their values from `ResolveOrAfter` + `End` at reset.
- `stage-1/internal/api/availability.go`: `GET /availability`. Errors follow R-12 (422 for each parameter in
  order restaurant_id, date, party_size, then 404). party_size must match `^[0-9]+$` and be ≥ 1; a value
  too large to parse counts as larger than every table. Tables are listed in fixture order. Only confirmed
  reservations block. The endpoint is public.
- Decision recorded: a `closes` value inside a spring-forward gap resolves to the instant of the jump. A
  repeated `closes` resolves to its first occurrence, the same as every other local time.

## Commands

### `cd stage-1 && go vet ./... && go test ./...`
Exit code: 0
```
ok  	tablekeeper/internal/api
ok  	tablekeeper/internal/jsonin
ok  	tablekeeper/internal/localtime
ok  	tablekeeper/internal/password
ok  	tablekeeper/internal/state
```
The tests cover offsets on both sides of all four transitions (Berlin and New York), skipped times →
invalid_local_time, and first-occurrence resolution of repeated times. Berlin 01:30 + 90 min →
02:00+01:00, and New York 01:30 + 90 → 02:00-05:00. Slot grids: Thursday 18:00–21:30 (8 slots), a closed
Wednesday → [], slot length 45, and closes 24:00. Across DST, no 02:xx slots on spring-forward days and
02:30 appears once (+02:00) on fall-back. The CheckStart ordering cases are covered. Every slot that
`Slots` returns over a full year in Berlin, New York, Kolkata and Lord_Howe (a 30-minute DST shift) is
accepted by `CheckStart` at the same instant. The availability tests cover the shape, occupancy
(19:00 t_2 blocks 18:00–20:00 starts, not 20:30), capacity filtering, an oversized party → all slots with
[], the full C1.43/C1.71 parameter matrix, 404 after the parameter checks, and an invalid Authorization
header being ignored.

### `python factory/tools/bg.py run --timeout 600 -- docker build -t stylist-tk1:wi3 stage-1`
Exit code: 0
```
#13 naming to docker.io/library/stylist-tk1:wi3 done
[bg run] exit 0 after 14.6s
```

### `python factory/tools/bg.py start --name stylist-tk1 --health http://127.0.0.1:18200/health -- docker run --rm --name stylist-tk1 --cpus 2 --memory 2g -e PORT=8080 -p 127.0.0.1:18200:8080 stylist-tk1:wi3`
Exit code: 0 (`"healthy": true`). Smoke run against http://127.0.0.1:18200 after `POST /_test/reset` → 204:
```
GET /availability?restaurant_id=r_anker&date=2026-09-23&party_size=2
  {"restaurant_id":"r_anker","date":"2026-09-23","timezone":"Europe/Berlin","slots":[]}
GET ...date=2026-10-25 (Berlin, open 00:00–06:00, seeded t_3 at 01:30)
  2026-10-25T02:30 2026-10-25T02:30:00+02:00 ['t_1', 't_2']
  2026-10-25T03:00 2026-10-25T03:00:00+01:00 ['t_1', 't_2', 't_3']
GET ...date=2026-03-29 → 00:00+01:00 … 01:30+01:00, 03:00+02:00 … 04:30+02:00 (no 02:xx)
GET r_ny date=2026-11-01 → 01:30:00-04:00, 02:00:00-05:00, …
party_size=4.0 → 422 validation_failed; r_nope → 404 not_found; missing date → 422 validation_failed
```
### `python factory/tools/bg.py stop --name stylist-tk1` and `docker stop stylist-tk1`
Exit code: 0. `docker ps -a --filter name=stylist` is empty.

## Known gaps
- C1.23, C1.25, C1.73: the booking endpoint is WI-4 (Builder). The engine functions it needs are exported.
