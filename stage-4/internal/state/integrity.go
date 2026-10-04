package state

import (
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"unicode/utf8"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/localtime"
)

var hhmmPattern = regexp.MustCompile(`^(?:[01][0-9]|2[0-3]):[0-5][0-9]$`)

// Rebuild checks the referential integrity of a state decoded from an export (§10, R-24) and
// rebuilds its indexes. Any violation is 422 validation_failed; st must then be discarded.
// Every collection added to State must be checked here as well.
func Rebuild(st *State) error {
	if err := checkIntegrity(st); err != nil {
		return err
	}
	st.reindex()
	return nil
}

func invalid(format string, args ...any) error {
	return apperr.Validation("invalid state: " + fmt.Sprintf(format, args...))
}

func validID(id string) bool { return id != "" && utf8.RuneCountInString(id) <= MaxIDLength }

func checkIntegrity(st *State) error {
	if st.Users == nil || st.Restaurants == nil || st.Reservations == nil || st.Tokens == nil || st.Receipts == nil || st.Series == nil {
		return invalid("missing collection")
	}
	users := map[string]bool{}
	emails := map[string]bool{}
	for _, u := range st.Users {
		if u == nil || !validID(u.ID) || u.Email == "" || u.PasswordHash == "" {
			return invalid("user entry incomplete")
		}
		if users[u.ID] || emails[EmailKey(u.Email)] {
			return invalid("duplicate user %s", u.ID)
		}
		users[u.ID], emails[EmailKey(u.Email)] = true, true
	}
	restaurants := map[string]*Restaurant{}
	for _, r := range st.Restaurants {
		if err := checkRestaurant(r); err != nil {
			return err
		}
		for i, id := range r.ManagerUserIDs {
			if !users[id] || slices.Contains(r.ManagerUserIDs[:i], id) {
				return invalid("restaurant %s has an invalid manager", r.ID)
			}
		}
		if restaurants[r.ID] != nil {
			return invalid("duplicate restaurant %s", r.ID)
		}
		restaurants[r.ID] = r
	}
	ids, refs := map[string]bool{}, map[string]bool{}
	for _, res := range st.Reservations {
		if res == nil || !validID(res.ID) || !validReference(res.Reference) || !localtime.ValidLocal(res.StartsAtLocal) ||
			res.PartySize < 1 || res.StartsAt.IsZero() || res.EndsAt.IsZero() || res.CreatedAt.IsZero() ||
			(res.Status != Confirmed && res.Status != Cancelled) {
			return invalid("reservation entry incomplete")
		}
		r := restaurants[res.RestaurantID]
		if !users[res.UserID] || r == nil || !checkTableSet(r, res.TableIDs) ||
			(len(res.TableIDs) == 2 && r.Pair(res.TableIDs[0], res.TableIDs[1]) == nil) {
			return invalid("reservation %s refers to an unknown user, restaurant or table", res.ID)
		}
		if ids[res.ID] || refs[res.Reference] {
			return invalid("duplicate reservation %s", res.ID)
		}
		if err := checkRecord(r, res); err != nil {
			return err
		}
		ids[res.ID], refs[res.Reference] = true, true
	}
	if err := checkSeries(st, users); err != nil {
		return err
	}
	if err := checkPlans(st, restaurants); err != nil {
		return err
	}
	for token, userID := range st.Tokens {
		if token == "" || !users[userID] {
			return invalid("token for an unknown user")
		}
	}
	for k, rc := range st.Receipts {
		userID := receiptUser(k)
		if !users[userID] || rc == nil || rc.Status < 200 || rc.Status > 299 || !json.Valid(rc.Response) {
			return invalid("idempotency receipt incomplete or for an unknown user")
		}
	}
	return nil
}

func checkRestaurant(r *Restaurant) error {
	if r == nil || !validID(r.ID) || r.SlotMinutes < 1 || r.ReservationDurationMinutes < 1 ||
		r.CancellationCutoffMinutes < 0 || r.OpeningHours == nil || r.Tables == nil || r.Combinable == nil {
		return invalid("restaurant entry incomplete")
	}
	if _, err := localtime.Location(r.Timezone); err != nil {
		return invalid("restaurant %s has an unknown timezone", r.ID)
	}
	if !validHours(r.OpeningHours) {
		return invalid("restaurant %s has invalid opening hours", r.ID)
	}
	tables := map[string]bool{}
	for _, t := range r.Tables {
		if !validID(t.ID) || t.Capacity < 1 || tables[t.ID] {
			return invalid("restaurant %s has an invalid table", r.ID)
		}
		tables[t.ID] = true
	}
	if checkCombinable(r) != nil {
		return invalid("restaurant %s has invalid combinable pairs", r.ID)
	}
	if r.ManagerUserIDs == nil || r.Policies == nil || r.Revision < 0 || r.Closures == nil {
		return invalid("restaurant %s entry incomplete", r.ID)
	}
	for i, p := range r.Policies {
		if p.PolicyVersion != i+1 || !localtime.ValidDate(p.EffectiveFrom) || !validTerms(r, p.Terms) {
			return invalid("restaurant %s has an invalid policy", r.ID)
		}
	}
	return nil
}

// validHours applies the stage-1 opening-hours rules: known weekdays, HH:MM, closes after opens,
// no weekday twice.
func validHours(hours []OpeningHours) bool {
	if hours == nil {
		return false
	}
	days := map[string]bool{}
	for _, h := range hours {
		closesOK := hhmmPattern.MatchString(h.Closes) || h.Closes == "24:00"
		if !weekdays[h.Weekday] || days[h.Weekday] || !hhmmPattern.MatchString(h.Opens) || !closesOK || h.Closes <= h.Opens {
			return false
		}
		days[h.Weekday] = true
	}
	return true
}

// validTerms checks a policy's or accepted terms' ranges and that capacities name exactly r's
// tables.
func validTerms(r *Restaurant, t Terms) bool {
	if t.PolicyVersion < 0 || t.PolicyVersion > len(r.Policies) || t.SlotMinutes < 1 || t.SlotMinutes > 1440 ||
		t.ReservationDurationMinutes < 1 || t.ReservationDurationMinutes > 1440 ||
		t.CancellationCutoffMinutes < 0 || t.CancellationCutoffMinutes > 10080 ||
		!validHours(t.OpeningHours) || len(t.Capacities) != len(r.Tables) {
		return false
	}
	for _, tb := range r.Tables {
		if c, ok := t.Capacities[tb.ID]; !ok || c < 1 {
			return false
		}
	}
	return true
}

// checkRecord requires a revision, valid accepted terms and a dense history ending at the
// current revision.
func checkRecord(r *Restaurant, res *Reservation) error {
	if res.Revision < 1 || !validTerms(r, res.Terms) || len(res.History) == 0 {
		return invalid("reservation %s has an invalid revision or terms", res.ID)
	}
	for i, e := range res.History {
		if e.Seq != i+1 || e.At.IsZero() || !validTerms(r, e.AcceptedTerms) || e.Changes == nil ||
			(e.Event != EventCreated && e.Event != EventChanged && e.Event != EventCancelled && e.Event != EventReassigned) {
			return invalid("reservation %s has an invalid history", res.ID)
		}
	}
	if res.History[len(res.History)-1].Revision != res.Revision {
		return invalid("reservation %s history does not end at its revision", res.ID)
	}
	return nil
}

// checkSeries requires every series to belong to a known user and to list 2..12 distinct
// reservations of that user that point back at it; series membership is mutual.
func checkSeries(st *State, users map[string]bool) error {
	byRef := make(map[string]*Reservation, len(st.Reservations))
	for _, res := range st.Reservations {
		byRef[res.Reference] = res
		if res.SeriesID != "" && st.Series[res.SeriesID] == nil {
			return invalid("reservation %s names an unknown series", res.ID)
		}
	}
	for id, s := range st.Series {
		if s == nil || s.ID != id || !validID(id) || !users[s.UserID] || s.Revision < 1 ||
			s.IntervalWeeks < 1 || s.IntervalWeeks > 4 || len(s.Occurrences) < 2 || len(s.Occurrences) > 12 {
			return invalid("series entry incomplete")
		}
		for i, o := range s.Occurrences {
			res := byRef[o.Reference]
			if res == nil || res.UserID != s.UserID || res.SeriesID != id ||
				slices.ContainsFunc(s.Occurrences[:i], func(p Occurrence) bool { return p.Reference == o.Reference }) {
				return invalid("series %s has an invalid occurrence", id)
			}
		}
	}
	for _, res := range st.Reservations {
		if s := st.Series[res.SeriesID]; s != nil && s.Occurrence(res.Reference) == nil {
			return invalid("reservation %s is not an occurrence of its series", res.ID)
		}
	}
	return nil
}

// validClosure requires a table of r and a non-empty interval.
func validClosure(r *Restaurant, c Closure) bool {
	return r.Table(c.TableID) != nil && c.From.Before(c.To) && validID(c.PlanID)
}

// checkPlans requires every closure and stored plan to name tables and bookings of its
// restaurant (stage 4).
func checkPlans(st *State, restaurants map[string]*Restaurant) error {
	if st.Plans == nil {
		return invalid("missing collection")
	}
	for _, r := range restaurants {
		for _, c := range r.Closures {
			if !validClosure(r, c) {
				return invalid("restaurant %s has an invalid closure", r.ID)
			}
		}
	}
	for id, p := range st.Plans {
		if p == nil || p.ID != id || !validID(id) || p.Assignments == nil || p.MovedCount < 0 || p.UnusedSeats < 0 || p.RestaurantRevision < 0 {
			return invalid("plan entry incomplete")
		}
		r := restaurants[p.RestaurantID]
		if r == nil || !validClosure(r, p.Closure) || p.Closure.PlanID != id {
			return invalid("plan %s has an invalid restaurant or closure", id)
		}
		for _, a := range p.Assignments {
			res := st.reservationsByRefOrScan(a.Reference)
			if res == nil || res.RestaurantID != r.ID || !checkTableSet(r, a.TableIDs) {
				return invalid("plan %s has an invalid assignment", id)
			}
		}
	}
	return nil
}

// reservationsByRefOrScan finds a reservation before the indexes are rebuilt.
func (st *State) reservationsByRefOrScan(ref string) *Reservation {
	for _, res := range st.Reservations {
		if res.Reference == ref {
			return res
		}
	}
	return nil
}
