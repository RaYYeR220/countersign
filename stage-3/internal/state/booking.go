package state

import (
	"time"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/localtime"
)

// LocalDate is the local calendar date of a valid YYYY-MM-DDTHH:MM start.
func LocalDate(local string) string { return local[:len("2006-01-02")] }

// CheckBooking applies the rules for a new booking of tableIDs (already resolved against r) at
// the local start, under the policy of its local date, in order: invalid_local_time →
// outside_opening_hours → not_on_slot_grid → party_exceeds_capacity → 409 table_unavailable.
// skip, which may be nil, exempts reservations from the occupancy check. It returns the policy
// and the absolute occupancy interval.
func (st *State) CheckBooking(r *Restaurant, tableIDs []string, local string, party int, skip func(*Reservation) bool) (Policy, time.Time, time.Time, error) {
	pol := r.PolicyFor(LocalDate(local))
	loc, err := localtime.Location(r.Timezone)
	if err != nil {
		return pol, time.Time{}, time.Time{}, err
	}
	start, err := localtime.CheckStart(loc, pol.OpeningHours, pol.SlotMinutes, pol.ReservationDurationMinutes, local)
	if err != nil {
		return pol, time.Time{}, time.Time{}, err
	}
	if party > pol.Capacity(tableIDs) {
		return pol, time.Time{}, time.Time{}, apperr.PartyExceedsCapacity()
	}
	end := localtime.End(start, pol.ReservationDurationMinutes)
	if st.TableBusy(r.ID, tableIDs, start, end, skip) {
		return pol, time.Time{}, time.Time{}, apperr.TableUnavailable()
	}
	return pol, start, end, nil
}

// InsertReservation stores a new confirmed booking at revision 1 under policy p, with its
// created history entry. The caller bumps the restaurant revision once per operation.
func (st *State) InsertReservation(userID string, r *Restaurant, tableIDs []string, local string, party int,
	p Policy, start, end, createdAt time.Time) *Reservation {
	res := &Reservation{
		UserID:        userID,
		RestaurantID:  r.ID,
		TableIDs:      tableIDs,
		PartySize:     party,
		StartsAtLocal: local,
		StartsAt:      start,
		EndsAt:        end,
		CreatedAt:     Stamp(createdAt),
		Revision:      1,
		Terms:         p.Terms,
	}
	st.AddReservation(res)
	res.Record(res.CreatedAt, EventCreated, res.CreatedChanges())
	return res
}
