[EVIDENCE] stage=3 wi=WI-19,WI-20 sha=d3575c4f4766a0b7a59548885c8403c6228588df

# Evidence — WI-19 (explanations, policy-driven availability) and WI-20 (series)

- Revision: d3575c4f4766a0b7a59548885c8403c6228588df on seat/stylist. It merges main 09289e3 (R-46…R-59) and
  seat/builder ace321b (stage-3 core: PolicyFor, terms, revisions, history, series model and hooks).
- WI-19, `stage-3/internal/api/availability.go`:
  - Availability uses `rest.PolicyFor(date)`, the Builder's single selection function, for the grid, duration,
    opening hours and capacities, including pairs. no_overlap uses each booking's accepted StartsAt/EndsAt.
  - `explain` is checked after party_size and before the 404. Only the exact value "true" is accepted, and when
    the parameter is repeated only its first value counts (R-46).
  - Each slot's explanation lists every table once, in fixture order, with `policy_version` and the rules
    [capacity, no_overlap]. `available` is true exactly when both rules hold.
  - Without `explain`, the response has the stage-2 shape.
- WI-20, `stage-3/internal/api/series.go`:
  - POST /series is a keyed write. Errors follow R-52: types → missing → ranges → 404 → reservation_cancelled →
    already_in_series → cutoff_passed → occurrences 1…count−1. Each occurrence runs the R-7 chain under its own
    date's policy, with occupancy checked against existing bookings and earlier planned occurrences.
  - Every occurrence is planned before anything is written, so a failure leaves no state and the key is not
    claimed.
  - On success, each generated occurrence gets revision 1, its own terms and a `created` history entry, and all
    members get `SeriesID`. The restaurant revision is bumped once.
  - GET /series/{id} returns 404 for another user, no token, an invalid token or an unknown id.
  - Exceptions and series revisions are updated by the Builder's PATCH, cancel and moves code. The tests cover
    them end to end.

## Commands
### `cd stage-3 && go vet ./...` → exit 0; `go test ./internal/{jsonin,localtime,password,snapshot,state}` → ok
### `go test -c ./internal/api` and then `api.test.exe -test.v` (the machine's CPU was at 100% from other load, so the run used a long timeout)
Exit code: 0. 55 tests PASS, including:
```
--- PASS: TestExplainShapeAndRules / TestExplainAbsentAndInvalid / TestAvailabilityFollowsPolicy
--- PASS: TestSeriesAdoption            (shape, dates, anchor untouched incl. history + replay, GET 404s, replay/reuse, already_in_series for anchor and occurrence)
--- PASS: TestSeriesErrors              (R-52 matrix: 401, 400 body/anchor type/null, 422 types-before-missing, booleans/strings/fractions/ranges, ranges before 404, 404 foreign, cancelled, cutoff, 3.0 accepted)
--- PASS: TestSeriesAllOrNothing        (failed adoption: export byte-identical, key reusable)
--- PASS: TestSeriesFirstFailingIndexAndPolicies (index 1 table_unavailable beats index 2 outside_opening_hours; policy capacity; per-occurrence terms/duration)
--- PASS: TestSeriesDST                 (fall-back first occurrence +02:00 → ends 03:00+01:00; spring-forward → invalid_local_time, nothing written)
--- PASS: TestSeriesExceptionsAndRevision (no-op/failure no change; real PATCH exception + rev; cancel rev, repeat cancel nothing; anchor cancel keeps siblings; replay unchanged)
--- PASS: TestSeriesSurvivesExportImport / TestSeriesOnImportedStage1Booking
```
### `docker build -t stylist-tk3:wi20 stage-3` → exit 0
### Containers: stage-3 on 127.0.0.1:18213, accepted stage-1 on 127.0.0.1:18212 (bg.py, `--cpus 2 --memory 2g`)
- `evidence/design/stage-3/ui_check.py --base :18213 --stage1 :18212`: exit 0, **240/240** checks pass. This
  is the stage-2 browser regression on the stage-3 image at 375, 768 and 1280 px, including the stage-1 →
  stage-3 upgrade retry. The 78 screenshots are in evidence/design/stage-3/.
- `evidence/stage-3/stylist_smoke.py --base :18213 --stage1 :18212`: exit 0, **19/19**. It covers:
  - explain rules and ids; no explain without the parameter; false, 1 and "" → 422
  - a published policy changes that date's grid and policy_version
  - a seeded booking adopted under two policies; replay byte-identical; GET with and without a token
  - a real PATCH sets the exception and series revision 2
  - a stage-1 export imported, its booking adopted using the old token, and the stage-1 retry replayed
    byte-identically
- Both containers were stopped; `docker ps -a --filter name=stylist` is empty.

## Known gaps
- none
