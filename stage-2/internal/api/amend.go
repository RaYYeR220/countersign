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

func tableUnavailable() error {
	return apperr.New(http.StatusConflict, "table_unavailable", "the table is taken for an overlapping time")
}

// withinCutoff reports whether now is within the restaurant's cutoff of res's current start, or
// later: changes are allowed only while starts_at − now > cutoff (R-6).
func withinCutoff(res *state.Reservation, rest *state.Restaurant, now time.Time) bool {
	return res.StartsAt.Sub(now) <= time.Duration(rest.CancellationCutoffMinutes)*time.Minute
}

// changeFieldTypes is the wrong-type pass for amendment fields: table_id and starts_at_local
// must be strings when present (null included, R-2); party_size never gives 400 (C1.42).
func changeFieldTypes(o jsonin.Object) error {
	for _, name := range []string{"table_id", "starts_at_local"} {
		if o.Has(name) && o.Kind(name) != jsonin.String {
			return apperr.Malformed(name + " must be a string")
		}
	}
	return nil
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

// change is the proposed new state of one confirmed reservation.
type change struct {
	res     *state.Reservation
	tableID string
	local   string
	party   int
	start   time.Time
	end     time.Time
	moved   bool // table or start differ, so occupancy must be re-checked
}

// planChange applies the ordinary amendment checks to res in order: 409 reservation_cancelled →
// 409 cutoff_passed → 422 field values → 404 table → invalid_local_time → outside_opening_hours →
// not_on_slot_grid → party_exceeds_capacity. Field types were checked by changeFieldTypes.
// Values equal to the current ones are not re-validated against the time rules: they are a no-op.
func planChange(st *state.State, res *state.Reservation, o jsonin.Object, now time.Time) (*change, error) {
	if res.Status == state.Cancelled {
		return nil, reservationCancelled()
	}
	rest := st.Restaurant(res.RestaurantID)
	if withinCutoff(res, rest, now) {
		return nil, cutoffPassed()
	}
	c := &change{res: res, tableID: res.TableID, local: res.StartsAtLocal, party: res.PartySize, start: res.StartsAt, end: res.EndsAt}

	local, hasLocal, _ := o.String("starts_at_local")
	if hasLocal && !localtime.ValidLocal(local) {
		return nil, apperr.Validation("starts_at_local must be a local YYYY-MM-DDTHH:MM")
	}
	if o.Has("party_size") {
		n, err := partySize(o)
		if err != nil {
			return nil, err
		}
		c.party = n
	}

	if tableID, ok, _ := o.String("table_id"); ok && tableID != res.TableID {
		if rest.Table(tableID) == nil {
			return nil, apperr.NotFound("no such table in this restaurant")
		}
		c.tableID, c.moved = tableID, true
	}
	if hasLocal && local != res.StartsAtLocal {
		loc, err := localtime.Location(rest.Timezone)
		if err != nil {
			return nil, err
		}
		start, err := localtime.CheckStart(loc, rest.OpeningHours, rest.SlotMinutes, rest.ReservationDurationMinutes, local)
		if err != nil {
			return nil, err
		}
		c.local, c.start, c.end, c.moved = local, start, localtime.End(start, rest.ReservationDurationMinutes), true
	}
	if (c.party != res.PartySize || c.tableID != res.TableID) && c.party > rest.Table(c.tableID).Capacity {
		return nil, partyExceedsCapacity()
	}
	return c, nil
}

func partyExceedsCapacity() error {
	return apperr.New(http.StatusUnprocessableEntity, "party_exceeds_capacity", "party_size exceeds the table's capacity")
}

// conflicts reports whether any moved change overlaps another change's resulting occupancy or a
// confirmed reservation outside the set. A changed booking's old occupancy is released for the
// check, so swaps succeed; unchanged bookings keep theirs.
func conflicts(st *state.State, changes []*change) bool {
	listed := make(map[*state.Reservation]bool, len(changes))
	for _, c := range changes {
		listed[c.res] = true
	}
	skipListed := func(r *state.Reservation) bool { return listed[r] }
	for _, c := range changes {
		if !c.moved {
			continue
		}
		if st.TableBusy(c.res.RestaurantID, c.tableID, c.start, c.end, skipListed) {
			return true
		}
		for _, other := range changes {
			if other != c && other.tableID == c.tableID && localtime.Overlaps(other.start, other.end, c.start, c.end) {
				return true
			}
		}
	}
	return false
}

// apply writes every change; callers have checked all of them first.
func apply(changes []*change) {
	for _, c := range changes {
		c.res.TableID, c.res.StartsAtLocal, c.res.PartySize = c.tableID, c.local, c.party
		c.res.StartsAt, c.res.EndsAt = c.start, c.end
	}
}
