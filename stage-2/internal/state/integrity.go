package state

import (
	"encoding/json"
	"fmt"
	"regexp"
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
	if st.Users == nil || st.Restaurants == nil || st.Reservations == nil || st.Tokens == nil || st.Receipts == nil {
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
		if !users[res.UserID] || r == nil || !checkTableSet(r, res.TableIDs) {
			return invalid("reservation %s refers to an unknown user, restaurant or table", res.ID)
		}
		if ids[res.ID] || refs[res.Reference] {
			return invalid("duplicate reservation %s", res.ID)
		}
		ids[res.ID], refs[res.Reference] = true, true
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
	days := map[string]bool{}
	for _, h := range r.OpeningHours {
		closesOK := hhmmPattern.MatchString(h.Closes) || h.Closes == "24:00"
		if !weekdays[h.Weekday] || days[h.Weekday] || !hhmmPattern.MatchString(h.Opens) || !closesOK || h.Closes <= h.Opens {
			return invalid("restaurant %s has invalid opening hours", r.ID)
		}
		days[h.Weekday] = true
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
	return nil
}
