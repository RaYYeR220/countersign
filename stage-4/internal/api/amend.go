package api

import (
	"net/http"
	"time"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/localtime"
	"tablekeeper/internal/state"
)

// Amendment rules shared by PATCH /reservations/{reference} (R-14) and POST /reservation-moves (R-22).

func reservationCancelled() error {
	return apperr.New(http.StatusConflict, "reservation_cancelled", "the reservation is cancelled")
}

func cutoffPassed() error {
	return apperr.New(http.StatusConflict, "cutoff_passed", "the reservation starts within its cancellation cutoff")
}

// withinCutoff reports whether now is within the booking's accepted cutoff of its current start,
// or later: changes are allowed only while starts_at − now > cutoff (R-6, stage 3).
func withinCutoff(res *state.Reservation, now time.Time) bool {
	return res.StartsAt.Sub(now) <= time.Duration(res.Terms.CancellationCutoffMinutes)*time.Minute
}

func staleRevision() error {
	return apperr.New(http.StatusConflict, "stale_revision", "the reservation has changed since that revision")
}

// checkExpectedRevision applies an optional expected_revision: anything but a positive integer
// → 422; a revision other than the current one → 409 stale_revision.
func checkExpectedRevision(o jsonin.Object, res *state.Reservation) error {
	if !o.Has("expected_revision") {
		return nil
	}
	n, _, err := o.Int("expected_revision")
	if o.Kind("expected_revision") != jsonin.Number || err != nil || n < 1 {
		return apperr.Validation("expected_revision must be a positive integer")
	}
	if n != int64(res.Revision) {
		return staleRevision()
	}
	return nil
}

// changeFieldTypes is the wrong-type pass for amendment fields: table_id and starts_at_local
// must be strings and table_ids an array of strings when present (null included, R-2);
// party_size never gives 400 (C1.42).
func changeFieldTypes(o jsonin.Object) error {
	if o.Has("starts_at_local") && o.Kind("starts_at_local") != jsonin.String {
		return apperr.Malformed("starts_at_local must be a string")
	}
	return tableFieldTypes(o)
}

// partySize reads a present party_size: anything but an integer of at least 1 is 422 (C1.86).
func partySize(o jsonin.Object) (int, error) {
	invalid := apperr.Validation("party_size must be an integer of at least 1")
	if o.Kind("party_size") != jsonin.Number {
		return 0, invalid
	}
	n, _, err := o.Int("party_size")
	if err != nil || n < 1 {
		return 0, invalid
	}
	return int(n), nil
}

// change is the proposed new state of one confirmed reservation. A real change differs from the
// booking in tables, start or party size; anything else is a no-op that keeps everything.
type change struct {
	res      *state.Reservation
	tableIDs []string
	local    string
	party    int
	start    time.Time
	end      time.Time
	terms    state.Terms
	real     bool
}

// planChange applies the amendment checks to res in order: 422 expected_revision → 409
// stale_revision → 409 reservation_cancelled → 409 cutoff_passed (accepted terms) → 422 field
// values → then, for a real change only, 404 table / 422 combination_not_allowed →
// invalid_local_time → outside_opening_hours → not_on_slot_grid → party_exceeds_capacity, all
// against the policy of the resulting start date, whose terms the change adopts. Field types
// were checked by changeFieldTypes. A no-op still needs a confirmed booking outside its cutoff.
func planChange(st *state.State, res *state.Reservation, o jsonin.Object, now time.Time) (*change, error) {
	if err := checkExpectedRevision(o, res); err != nil {
		return nil, err
	}
	if res.Status == state.Cancelled {
		return nil, reservationCancelled()
	}
	if withinCutoff(res, now) {
		return nil, cutoffPassed()
	}
	c := &change{res: res, tableIDs: res.TableIDs, local: res.StartsAtLocal, party: res.PartySize,
		start: res.StartsAt, end: res.EndsAt, terms: res.Terms}

	local, hasLocal, _ := o.String("starts_at_local")
	if hasLocal && !localtime.ValidLocal(local) {
		return nil, apperr.Validation("starts_at_local must be a local YYYY-MM-DDTHH:MM")
	}
	if hasLocal {
		c.local = local
	}
	if o.Has("party_size") {
		n, err := partySize(o)
		if err != nil {
			return nil, err
		}
		c.party = n
	}
	ids, hasTables, err := requestedTables(o)
	if err != nil {
		return nil, err
	}
	tablesChanged := hasTables && !sameTables(ids, res.TableIDs)
	c.real = tablesChanged || c.local != res.StartsAtLocal || c.party != res.PartySize
	if !c.real {
		return c, nil
	}

	rest := st.Restaurant(res.RestaurantID)
	if tablesChanged {
		if c.tableIDs, err = resolveTables(rest, ids); err != nil {
			return nil, err
		}
	}
	loc, err := localtime.Location(rest.Timezone)
	if err != nil {
		return nil, err
	}
	pol := rest.PolicyFor(state.LocalDate(c.local))
	if c.start, err = localtime.CheckStart(loc, pol.OpeningHours, pol.SlotMinutes, pol.ReservationDurationMinutes, c.local); err != nil {
		return nil, err
	}
	if c.party > pol.Capacity(c.tableIDs) {
		return nil, apperr.PartyExceedsCapacity()
	}
	c.end, c.terms = localtime.End(c.start, pol.ReservationDurationMinutes), pol.Terms
	return c, nil
}

// conflicts reports whether any real change overlaps another change's resulting occupancy or a
// confirmed reservation outside the set. A changed booking's old occupancy is released for the
// check, so swaps succeed; unchanged bookings keep theirs.
func conflicts(st *state.State, changes []*change) bool {
	listed := make(map[*state.Reservation]bool, len(changes))
	for _, c := range changes {
		listed[c.res] = true
	}
	skipListed := func(r *state.Reservation) bool { return listed[r] }
	for _, c := range changes {
		if !c.real {
			continue
		}
		if st.TableBusy(c.res.RestaurantID, c.tableIDs, c.start, c.end, skipListed) {
			return true
		}
		for _, other := range changes {
			if other != c && state.SharesTable(other.tableIDs, c.tableIDs) && localtime.Overlaps(other.start, other.end, c.start, c.end) {
				return true
			}
		}
	}
	return false
}

// apply writes every real change of one operation; callers have checked all of them first.
// Each changed booking adopts its new terms and gains one revision and one changed history entry
// naming only the fields that changed; with markExceptions (diner PATCH and moves, not series
// amend) it also becomes a permanent exception of its series. Each affected series and the
// restaurant gain one revision for the whole operation.
func apply(st *state.State, changes []*change, now time.Time, markExceptions bool) {
	series := map[*state.Series]bool{}
	var rest *state.Restaurant
	for _, c := range changes {
		if !c.real {
			continue
		}
		res := c.res
		var diff []state.FieldChange
		if !sameTables(c.tableIDs, res.TableIDs) {
			diff = append(diff, state.TableChange(res.TableIDs, c.tableIDs))
		}
		if c.local != res.StartsAtLocal {
			diff = append(diff, state.FieldChange{Field: "starts_at_local", From: res.StartsAtLocal, To: c.local})
		}
		if c.party != res.PartySize {
			diff = append(diff, state.FieldChange{Field: "party_size", From: res.PartySize, To: c.party})
		}
		res.TableIDs, res.StartsAtLocal, res.PartySize = c.tableIDs, c.local, c.party
		res.StartsAt, res.EndsAt, res.Terms = c.start, c.end, c.terms
		res.Revision++
		res.Record(now, state.EventChanged, diff)
		if s := st.SeriesOf(res); s != nil {
			if markExceptions {
				s.Occurrence(res.Reference).Exception = true
			}
			series[s] = true
		}
		rest = st.Restaurant(res.RestaurantID)
	}
	for s := range series {
		s.Revision++
	}
	if rest != nil {
		rest.Revision++
	}
}
