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
// non-number type → 422) → missing → values (anchor_reference 1..64 characters per R-60, then the
// count and interval_weeks ranges) → 404 anchor → 409 reservation_cancelled → 409
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
		if !validBodyID(ref) { // empty or over 64 characters: an invalid value, not an unknown one (R-60)
			return nil, apperr.Validation("anchor_reference must be 1 to 64 characters")
		}
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

		// Check every occurrence before writing anything.
		planned, err := planOccurrences(st, rest, anchor, count, weeks)
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
		anchor.SeriesID = series.ID
		for _, p := range planned {
			res := st.InsertReservation(user.ID, rest, slices.Clone(anchor.TableIDs), p.local, anchor.PartySize, p.policy, p.start, p.end, now)
			res.SeriesID = series.ID
			series.Occurrences = append(series.Occurrences, state.Occurrence{Reference: res.Reference})
		}
		st.Series[series.ID] = series
		rest.Revision++ // once for the whole adoption (R-54)
		return viewSeries(st, series), nil
	})
}

// plannedOccurrence is a generated occurrence that passed every check and awaits insertion.
type plannedOccurrence struct {
	local      string
	policy     state.Policy
	start, end time.Time
}

// planOccurrences checks occurrences 1 … count−1 of anchor: the same local clock time on the
// anchor's date plus i × weeks × 7 days, each through the shared booking chain under its own date's
// policy (R-7 order). Occupancy counts existing confirmed bookings and earlier planned occurrences.
func planOccurrences(st *state.State, rest *state.Restaurant, anchor *state.Reservation, count, weeks int) ([]plannedOccurrence, error) {
	anchorDate, err := time.Parse("2006-01-02", state.LocalDate(anchor.StartsAtLocal))
	if err != nil {
		return nil, apperr.Validation("anchor has an invalid start")
	}
	clock := anchor.StartsAtLocal[len("2006-01-02T"):]
	var planned []plannedOccurrence
	for i := 1; i < count; i++ {
		local := anchorDate.AddDate(0, 0, i*weeks*7).Format("2006-01-02") + "T" + clock
		pol, start, end, err := st.CheckBooking(rest, anchor.TableIDs, local, anchor.PartySize, nil)
		if err != nil {
			return nil, err
		}
		for _, other := range planned {
			if localtime.Overlaps(other.start, other.end, start, end) {
				return nil, apperr.TableUnavailable()
			}
		}
		planned = append(planned, plannedOccurrence{local, pol, start, end})
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
