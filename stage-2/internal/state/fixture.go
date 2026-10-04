package state

import (
	"fmt"
	"regexp"
	"slices"
	"time"
	_ "time/tzdata" // embed the IANA database: no zone files exist at runtime
	"unicode/utf8"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/localtime"
	"tablekeeper/internal/password"
)

// MaxIDLength bounds every id, including ids supplied by fixtures (§3.4).
const MaxIDLength = 64

var (
	weekdays     = map[string]bool{"mon": true, "tue": true, "wed": true, "thu": true, "fri": true, "sat": true, "sun": true}
	clockPattern = regexp.MustCompile(`^(?:[01][0-9]|2[0-3]):[0-5][0-9]$`)
)

// LocalStartPattern is the bare local `YYYY-MM-DDTHH:MM` form of starts_at_local.
var LocalStartPattern = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$`)

// FromFixture builds a complete state from a reset fixture (§3.3, §4). Wrong JSON types are
// 400 malformed_request; missing fields, bad values and dangling references are 422.
// Seeded reservations are confirmed; created_at is the fixture's RFC 3339 value, else createdAt.
func FromFixture(fx jsonin.Object, createdAt time.Time) (*State, error) {
	st := Empty()
	plains, err := loadUsers(st, fx)
	if err != nil {
		return nil, err
	}
	if err := loadRestaurants(st, fx); err != nil {
		return nil, err
	}
	if err := loadReservations(st, fx, createdAt); err != nil {
		return nil, err
	}
	hashes, err := password.HashAll(plains)
	if err != nil {
		return nil, err
	}
	for i, u := range st.Users {
		u.PasswordHash = hashes[i]
	}
	return st, nil
}

func requiredID(o jsonin.Object, field, where string) (string, error) {
	id, err := o.RequiredString(field)
	if err != nil {
		return "", err
	}
	if id == "" || utf8.RuneCountInString(id) > MaxIDLength {
		return "", apperr.Validation(fmt.Sprintf("%s.%s must be 1 to %d characters", where, field, MaxIDLength))
	}
	return id, nil
}

func intAtLeast(o jsonin.Object, field, where string, min int64) (int, error) {
	n, err := o.RequiredInt(field)
	if err != nil {
		return 0, err
	}
	if n < min || n > 1<<31-1 {
		return 0, apperr.Validation(fmt.Sprintf("%s.%s must be an integer of at least %d", where, field, min))
	}
	return int(n), nil
}

func optionalString(o jsonin.Object, field string) (string, error) {
	s, _, err := o.String(field)
	return s, err
}

func loadUsers(st *State, fx jsonin.Object) ([]string, error) {
	items, _, err := fx.Objects("users")
	if err != nil {
		return nil, err
	}
	plains := make([]string, 0, len(items))
	for _, o := range items {
		id, err := requiredID(o, "id", "users[]")
		if err != nil {
			return nil, err
		}
		email, err := o.RequiredString("email")
		if err != nil {
			return nil, err
		}
		plain, err := o.RequiredString("password")
		if err != nil {
			return nil, err
		}
		name, err := optionalString(o, "display_name")
		if err != nil {
			return nil, err
		}
		if st.User(id) != nil || st.UserByEmail(email) != nil {
			return nil, apperr.Validation("duplicate user id or email: " + id)
		}
		u := &User{ID: id, Email: email, DisplayName: name}
		st.Users = append(st.Users, u)
		st.usersByID[id] = u
		st.usersByEmail[EmailKey(email)] = u
		plains = append(plains, plain)
	}
	return plains, nil
}

func loadRestaurants(st *State, fx jsonin.Object) error {
	items, _, err := fx.Objects("restaurants")
	if err != nil {
		return err
	}
	for _, o := range items {
		r, err := loadRestaurant(o)
		if err != nil {
			return err
		}
		if st.Restaurant(r.ID) != nil {
			return apperr.Validation("duplicate restaurant id: " + r.ID)
		}
		st.Restaurants = append(st.Restaurants, r)
		st.restaurantsByID[r.ID] = r
	}
	return nil
}

func loadRestaurant(o jsonin.Object) (*Restaurant, error) {
	r := &Restaurant{OpeningHours: []OpeningHours{}, Tables: []Table{}, Combinable: [][]string{}}
	var err error
	if r.ID, err = requiredID(o, "id", "restaurants[]"); err != nil {
		return nil, err
	}
	if r.Name, err = optionalString(o, "name"); err != nil {
		return nil, err
	}
	if r.Timezone, err = o.RequiredString("timezone"); err != nil {
		return nil, err
	}
	if r.Timezone == "" || r.Timezone == "Local" {
		return nil, apperr.Validation("timezone must be an IANA zone name")
	}
	if _, err := time.LoadLocation(r.Timezone); err != nil {
		return nil, apperr.Validation("unknown timezone: " + r.Timezone)
	}
	if r.SlotMinutes, err = intAtLeast(o, "slot_minutes", "restaurants[]", 1); err != nil {
		return nil, err
	}
	if r.ReservationDurationMinutes, err = intAtLeast(o, "reservation_duration_minutes", "restaurants[]", 1); err != nil {
		return nil, err
	}
	if r.CancellationCutoffMinutes, err = intAtLeast(o, "cancellation_cutoff_minutes", "restaurants[]", 0); err != nil {
		return nil, err
	}
	hours, _, err := o.Objects("opening_hours")
	if err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	for _, h := range hours {
		oh, err := loadOpeningHours(h)
		if err != nil {
			return nil, err
		}
		if seen[oh.Weekday] {
			return nil, apperr.Validation("duplicate opening hours for " + oh.Weekday + " in restaurant " + r.ID)
		}
		seen[oh.Weekday] = true
		r.OpeningHours = append(r.OpeningHours, oh)
	}
	tables, _, err := o.Objects("tables")
	if err != nil {
		return nil, err
	}
	for _, t := range tables {
		tb := Table{}
		if tb.ID, err = requiredID(t, "id", "tables[]"); err != nil {
			return nil, err
		}
		if tb.Label, err = optionalString(t, "label"); err != nil {
			return nil, err
		}
		if tb.Capacity, err = intAtLeast(t, "capacity", "tables[]", 1); err != nil {
			return nil, err
		}
		if r.Table(tb.ID) != nil {
			return nil, apperr.Validation("duplicate table id in restaurant " + r.ID + ": " + tb.ID)
		}
		r.Tables = append(r.Tables, tb)
	}
	pairs, _, err := o.Arrays("combinable")
	if err != nil {
		return nil, err
	}
	for _, raw := range pairs {
		p, ok := jsonin.StringList(raw)
		if !ok {
			return nil, apperr.Malformed("combinable must be an array of arrays of table ids")
		}
		r.Combinable = append(r.Combinable, p)
	}
	if err := checkCombinable(r); err != nil {
		return nil, err
	}
	return r, nil
}

func loadOpeningHours(o jsonin.Object) (OpeningHours, error) {
	var oh OpeningHours
	var err error
	if oh.Weekday, err = o.RequiredString("weekday"); err != nil {
		return oh, err
	}
	if !weekdays[oh.Weekday] {
		return oh, apperr.Validation("weekday must be one of mon tue wed thu fri sat sun")
	}
	if oh.Opens, err = o.RequiredString("opens"); err != nil {
		return oh, err
	}
	if oh.Closes, err = o.RequiredString("closes"); err != nil {
		return oh, err
	}
	closesOK := clockPattern.MatchString(oh.Closes) || oh.Closes == "24:00"
	if !clockPattern.MatchString(oh.Opens) || !closesOK || oh.Closes <= oh.Opens {
		return oh, apperr.Validation("opening hours must be HH:MM with closes later than opens")
	}
	return oh, nil
}

// seededTables reads a seeded booking's table_id or table_ids (exactly one of them).
func seededTables(o jsonin.Object) ([]string, error) {
	single, hasSingle, err := o.String("table_id")
	if err != nil {
		return nil, err
	}
	ids, hasSet, err := o.Strings("table_ids")
	if err != nil {
		return nil, err
	}
	switch {
	case hasSingle && hasSet:
		return nil, apperr.Validation("reservations[] takes table_id or table_ids, not both")
	case hasSingle:
		ids = []string{single}
	case !hasSet:
		return nil, apperr.Validation("reservations[].table_ids is required")
	}
	for _, id := range ids {
		if id == "" || utf8.RuneCountInString(id) > MaxIDLength {
			return nil, apperr.Validation(fmt.Sprintf("reservations[] table ids must be 1 to %d characters", MaxIDLength))
		}
	}
	return ids, nil
}

// seededStatus reads a seeded booking's optional status: confirmed unless it says cancelled.
func seededStatus(o jsonin.Object) (string, error) {
	status, ok, err := o.String("status")
	switch {
	case err != nil:
		return "", err
	case !ok:
		return Confirmed, nil
	case status != Confirmed && status != Cancelled:
		return "", apperr.Validation("reservations[].status must be confirmed or cancelled")
	}
	return status, nil
}

func loadReservations(st *State, fx jsonin.Object, createdAt time.Time) error {
	items, _, err := fx.Objects("reservations")
	if err != nil {
		return err
	}
	ids := map[string]bool{}
	for _, o := range items {
		res := &Reservation{Status: Confirmed, CreatedAt: Stamp(createdAt)}
		if res.ID, err = requiredID(o, "id", "reservations[]"); err != nil {
			return err
		}
		if res.Reference, err = requiredID(o, "reference", "reservations[]"); err != nil {
			return err
		}
		if !validReference(res.Reference) {
			return apperr.Validation("reservations[].reference must be 6 to 12 characters of A-Z0-9")
		}
		if res.UserID, err = requiredID(o, "user_id", "reservations[]"); err != nil {
			return err
		}
		if res.RestaurantID, err = requiredID(o, "restaurant_id", "reservations[]"); err != nil {
			return err
		}
		if res.TableIDs, err = seededTables(o); err != nil {
			return err
		}
		if res.Status, err = seededStatus(o); err != nil {
			return err
		}
		if res.StartsAtLocal, err = o.RequiredString("starts_at_local"); err != nil {
			return err
		}
		if res.PartySize, err = intAtLeast(o, "party_size", "reservations[]", 1); err != nil {
			return err
		}
		if _, perr := time.Parse("2006-01-02T15:04", res.StartsAtLocal); perr != nil || !LocalStartPattern.MatchString(res.StartsAtLocal) {
			return apperr.Validation("reservations[].starts_at_local must be YYYY-MM-DDTHH:MM")
		}
		if s, _, err := o.String("created_at"); err == nil {
			if t, perr := time.Parse(time.RFC3339, s); perr == nil {
				res.CreatedAt = Stamp(t) // R-16: a valid fixture timestamp wins over the reset time
			}
		}
		r := st.Restaurant(res.RestaurantID)
		if st.User(res.UserID) == nil || r == nil || !checkTableSet(r, res.TableIDs) {
			return apperr.Validation("reservation " + res.ID + " refers to an unknown user, restaurant or table")
		}
		if len(res.TableIDs) == 2 {
			if p := r.Pair(res.TableIDs[0], res.TableIDs[1]); p != nil {
				res.TableIDs = slices.Clone(p)
			}
		}
		if ids[res.ID] || st.ReservationByRef(res.Reference) != nil {
			return apperr.Validation("duplicate reservation id or reference: " + res.ID)
		}
		loc, err := localtime.Location(r.Timezone)
		if err != nil {
			return err
		}
		res.StartsAt = localtime.ResolveOrAfter(loc, res.StartsAtLocal)
		res.EndsAt = localtime.End(res.StartsAt, r.ReservationDurationMinutes)
		ids[res.ID] = true
		st.Reservations = append(st.Reservations, res)
		st.reservationsByRef[res.Reference] = res
	}
	return nil
}
