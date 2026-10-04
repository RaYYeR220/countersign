[EVIDENCE] stage=4 wi=WI-25 sha=3ba1ce9

# Evidence — WI-25 (series amend) and WI-26 so far (closures in availability/explain, browser regression)

- Revision: seat/stylist 3ba1ce9. It merges main af2f047 (R-61…R-72) and seat/builder 14022e7 (closures in state
  and TableBusy, `apply(..., markExceptions)`).
- WI-25, `stage-4/internal/api/series.go` amendSeries plus route `POST /series/{id}/amend`:
  - Order follows R-70: 401 → 400 body → key → idempotency → 404 (unknown or another owner's series) → 422
    fields (expected_revision, from_index, local_time: types, missing, values) → 409 stale_revision →
    occurrences in index order → occupancy.
  - Per eligible occurrence (index ≥ from_index, not cancelled, not an exception): a no-op (same local start)
    is skipped with no checks. A real change runs 409 cutoff_passed (old terms, current start), then the R-7
    chain under the resulting date's policy (CheckStart, then capacity). The first failing index wins.
  - Occupancy then runs over all changed occurrences through the shared `conflicts` check (TableBusy, which
    includes closures, plus pairwise checks among the changes) → 409 table_unavailable.
  - Success uses the shared `apply(st, changes, now, false)`: one changed entry and revision per occurrence,
    series and restaurant revisions +1 once, and no exceptions marked. The response is 201 with the current
    series response; a replay returns 200 with the original body.
  - All checks run before any write, so a failure leaves no state change and the key unclaimed.
  - The chain stays local rather than calling CheckBooking, because R-70 requires every occurrence's
    non-occupancy errors before any occupancy error. CheckBooking would return table_unavailable mid-loop.
- WI-26 (part): `availability.go` treats an applied closure overlapping a slot like a conflicting booking
  (R-68). The table and every pair containing it drop out, and explain reports `no_overlap: false` for it.

## Commands
### `cd stage-4 && go vet ./...`; `go test -c ./internal/api` and then `api4.test.exe` → exit 0, full suite PASS, including:
```
TestSeriesAmendChangesEligibleOccurrences   (dates/times, revisions, history entry, occupancy moves, replay after cancel, reuse 409)
TestSeriesAmendSkipsExceptionsCancelledAndNoOps (exception/cancelled skipped, all-no-op and empty set: 201, no revision or state change)
TestSeriesAmendValidationAndOrder           (401, 400, 404 before field checks, 422 matrix incl. booleans/strings/null/fractions/24:00/9:00, types before stale, 409 stale, 1.0 accepted)
TestSeriesAmendErrorsInIndexOrderAndAtomic  (outside_opening_hours at index 3 beats an occupancy conflict at index 1; not_on_slot_grid; table_unavailable; export unchanged; key reusable; per-occurrence policy)
TestSeriesAmendCutoff                       (real change within cutoff → 409; no-op within cutoff fine)
TestSeriesAmendConcurrentSameRevision       (8 concurrent from revision 1 → exactly one 201, series revision 2)
TestClosureInAvailabilityAndExplain         (closed t_2 and its pair excluded over [from,to); no_overlap false; create 409; after closure 201)
TestSeriesAmendRespectsClosures             (amend into a closure → 409, nothing changed)
```
### `docker build -t stylist-tk4:ui stage-4` → exit 0
### stage-4 on :18214 and accepted stage-1 on :18212: `evidence/design/stage-4/ui_check.py --base :18214 --stage1 :18212` → exit 0, 240/240
This is the stage-2 browser regression on the stage-4 image at 375, 768 and 1280 px, including the stage-1 →
stage-4 lost-booking retry. Both containers were stopped.

## Known gaps
- WI-26 replanned-restaurant browser scenario: written (`ui_check.py --replan`: book t_2 in the browser, the
  manager previews and applies a t_2 closure, then the grid is compared with the API with every t_2 cell false,
  and lookup shows the new tables, with screenshots 27 and 28). It waits for the Builder's replan endpoints
  (WI-23), which are not on any branch yet. I will run it and post the WI-26 evidence then.
