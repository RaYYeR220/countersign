package api

import (
	"crypto/rand"
	"net/http"
	"slices"
	"strings"
	"time"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/localtime"
	"tablekeeper/internal/state"
)

const (
	minSeriesCount, maxSeriesCount     = 2, 12
	minIntervalWeeks, maxIntervalWeeks = 1, 4
	seriesIDPrefix                     = "ser_"
)

type seriesOccurrenceView struct {
	Index       int             `json:"index"`
	Reference   string          `json:"reference"`
	Exception   bool            `json:"exception"`
	Reservation reservationView `json:"reservation"`
}

type seriesView struct {
	SeriesID      string                 `json:"series_id"`
	Revision      int                    `json:"revision"`
	IntervalWeeks int                    `json:"interval_weeks"`
	Occurrences   []seriesOccurrenceView `json:"occurrences"`
}

// viewSeries renders a series with the current state of every occurrence, in index order (R-53).
func viewSeries(st *state.State, s *state.Series) seriesView {
	out := seriesView{SeriesID: s.ID, Revision: s.Revision, IntervalWeeks: s.IntervalWeeks,
		Occurrences: make([]seriesOccurrenceView, len(s.Occurrences))}
	for i, o := range s.Occurrences {
		out.Occurrences[i] = seriesOccurrenceView{i, o.Reference, o.Exception, view(st, st.ReservationByRef(o.Reference))}
	}
	return out
}

func alreadyInSeries() error {
	return apperr.New(http.StatusConflict, "already_in_series", "the reservation already belongs to a series")
}

// seriesInt reads count or interval_weeks: any non-integer (wrong JSON type, fraction) or a value
// outside [min, max] is 422 validation_failed (C3.38, R-52).
func seriesInt(body jsonin.Object, name string, min, max int64) (int, error) {
	if !body.Has(name) {
		return 0, apperr.Validation(name + " is required")
	}
	if body.Kind(name) != jsonin.Number {
		return 0, apperr.Validation(name + " must be an integer")
	}
	n, _, err := body.Int(name)
	if err != nil {
		return 0, apperr.Validation(name + " must be an integer")
	}
	if n < min || n > max {
		return 0, apperr.Validation(name + " is out of range")
	}
	return int(n), nil
}

// createSeries is POST /series (C3.37–C3.47). keyedWrite supplies R-1 (401 → 400 body → key →
// replay/reuse); then R-52: types (anchor_reference not a string → 400; count / interval_weeks of a
// non-number type → 422) → missing → ranges → 404 anchor → 409 reservation_cancelled → 409
// already_in_series → 409 cutoff_passed → occurrences 1 … count−1, the first failure deciding.
// Every check runs before any write, so a failed adoption changes nothing.
func (s *Server) createSeries(w http.ResponseWriter, r *http.Request, user *state.User) {
	s.keyedWrite(w, r, user, func(st *state.State, body jsonin.Object) (any, error) {
		if body.Has("anchor_reference") && body.Kind("anchor_reference") != jsonin.String {
			return nil, apperr.Malformed("anchor_reference must be a string")
		}
		for _, name := range []string{"count", "interval_weeks"} {
			if body.Has(name) && body.Kind(name) != jsonin.Number {
				return nil, apperr.Validation(name + " must be an integer")
			}
		}
		if !body.Has("anchor_reference") {
			return nil, apperr.Validation("anchor_reference is required")
		}
		for _, name := range []string{"count", "interval_weeks"} {
			if !body.Has(name) {
				return nil, apperr.Validation(name + " is required")
			}
		}
		ref, _, _ := body.String("anchor_reference")
		count, err := seriesInt(body, "count", minSeriesCount, maxSeriesCount)
		if err != nil {
			return nil, err
		}
		weeks, err := seriesInt(body, "interval_weeks", minIntervalWeeks, maxIntervalWeeks)
		if err != nil {
			return nil, err
		}

		anchor, err := ownReservation(st, user, ref)
		if err != nil {
			return nil, err
		}
		if anchor.Status != state.Confirmed {
			return nil, reservationCancelled()
		}
		if anchor.SeriesID != "" {
			return nil, alreadyInSeries()
		}
		now := s.now()
		if withinCutoff(anchor, now) {
			return nil, cutoffPassed()
		}
		rest := st.Restaurant(anchor.RestaurantID)
		loc, err := localtime.Location(rest.Timezone)
		if err != nil {
			return nil, err
		}

		// Plan every occurrence before writing anything.
		planned, err := planOccurrences(st, rest, loc, anchor, count, weeks)
		if err != nil {
			return nil, err
		}

		series := &state.Series{
			ID:            newSeriesID(st),
			UserID:        user.ID,
			Revision:      1,
			IntervalWeeks: weeks,
			Occurrences:   []state.Occurrence{{Reference: anchor.Reference}},
		}
		createdAt := state.Stamp(now)
		anchor.SeriesID = series.ID
		for _, res := range planned {
			res.CreatedAt = createdAt
			res.SeriesID = series.ID
			st.AddReservation(res)
			res.Record(res.CreatedAt, state.EventCreated, res.CreatedChanges())
			series.Occurrences = append(series.Occurrences, state.Occurrence{Reference: res.Reference})
		}
		st.Series[series.ID] = series
		rest.Revision++ // once for the whole adoption (R-54)
		return viewSeries(st, series), nil
	})
}

// planOccurrences builds occurrences 1 … count−1 of anchor: the same local clock time on the
// anchor's date plus i × weeks × 7 days, each under its own date's policy, with the ordinary booking
// checks in R-7 order. Occupancy counts existing confirmed bookings and earlier planned occurrences.
func planOccurrences(st *state.State, rest *state.Restaurant, loc *time.Location, anchor *state.Reservation, count, weeks int) ([]*state.Reservation, error) {
	anchorDate, err := time.Parse("2006-01-02", localDate(anchor.StartsAtLocal))
	if err != nil {
		return nil, apperr.Validation("anchor has an invalid start")
	}
	clock := anchor.StartsAtLocal[len("2006-01-02T"):]
	var planned []*state.Reservation
	for i := 1; i < count; i++ {
		date := anchorDate.AddDate(0, 0, i*weeks*7).Format("2006-01-02")
		local := date + "T" + clock
		pol := rest.PolicyFor(date)
		start, err := localtime.CheckStart(loc, pol.OpeningHours, pol.SlotMinutes, pol.ReservationDurationMinutes, local)
		if err != nil {
			return nil, err
		}
		if anchor.PartySize > pol.Capacity(anchor.TableIDs) {
			return nil, partyExceedsCapacity()
		}
		end := localtime.End(start, pol.ReservationDurationMinutes)
		if st.TableBusy(rest.ID, anchor.TableIDs, start, end, nil) {
			return nil, tableUnavailable()
		}
		for _, other := range planned {
			if localtime.Overlaps(other.StartsAt, other.EndsAt, start, end) {
				return nil, tableUnavailable()
			}
		}
		planned = append(planned, &state.Reservation{
			UserID:        anchor.UserID,
			RestaurantID:  rest.ID,
			TableIDs:      slices.Clone(anchor.TableIDs),
			PartySize:     anchor.PartySize,
			StartsAtLocal: local,
			StartsAt:      start,
			EndsAt:        end,
			Revision:      1,
			Terms:         pol.Terms,
		})
	}
	return planned, nil
}

func newSeriesID(st *state.State) string {
	for {
		id := seriesIDPrefix + strings.ToLower(rand.Text()[:16])
		if st.Series[id] == nil {
			return id
		}
	}
}

// getSeries is GET /series/{series_id}: owner only; another user, no token, an invalid token or an
// unknown id all get the same 404 (C3.45, R-53).
func (s *Server) getSeries(w http.ResponseWriter, r *http.Request) {
	user := s.caller(r)
	var out *seriesView
	s.store.Read(func(st *state.State) {
		if user == nil {
			return
		}
		if series := st.Series[r.PathValue("id")]; series != nil && series.UserID == user.ID {
			v := viewSeries(st, series)
			out = &v
		}
	})
	if out == nil {
		writeError(w, apperr.NotFound("no such series"))
		return
	}
	writeJSON(w, http.StatusOK, out)
}
