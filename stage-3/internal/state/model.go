// Package state holds the whole service state as one value guarded by one lock (ADR-001 §3–§4).
package state

import (
	"strings"
	"time"

	"tablekeeper/internal/localtime"
)

// User is an account. Only the password hash is stored.
type User struct {
	ID           string `json:"id"`
	Email        string `json:"email"`
	DisplayName  string `json:"display_name"`
	PasswordHash string `json:"password_hash"`
}

// OpeningHours is one opening interval on a weekday, in local HH:MM.
type OpeningHours = localtime.Hours

// Table is a bookable table.
type Table struct {
	ID       string `json:"id"`
	Label    string `json:"label"`
	Capacity int    `json:"capacity"`
}

// Restaurant is supplied by the fixture; its JSON shape is the fixture's shape.
type Restaurant struct {
	ID                         string         `json:"id"`
	Name                       string         `json:"name"`
	Timezone                   string         `json:"timezone"`
	SlotMinutes                int            `json:"slot_minutes"`
	ReservationDurationMinutes int            `json:"reservation_duration_minutes"`
	CancellationCutoffMinutes  int            `json:"cancellation_cutoff_minutes"`
	OpeningHours               []OpeningHours `json:"opening_hours"`
	Tables                     []Table        `json:"tables"`
	Combinable                 [][]string     `json:"combinable"` // declared pairs, each in declared order
}

// Table returns the restaurant's table with the given id, or nil.
func (r *Restaurant) Table(id string) *Table {
	for i := range r.Tables {
		if r.Tables[i].ID == id {
			return &r.Tables[i]
		}
	}
	return nil
}

// Stamp normalises a creation time as R-21 requires: UTC, whole seconds.
func Stamp(t time.Time) time.Time { return t.UTC().Truncate(time.Second) }

// Reservation statuses.
const (
	Confirmed = "confirmed"
	Cancelled = "cancelled"
)

// Reservation is a booking. ID, Reference, UserID and CreatedAt never change. It occupies its
// table over the half-open interval [StartsAt, EndsAt) while confirmed.
type Reservation struct {
	ID            string    `json:"id"`
	Reference     string    `json:"reference"`
	UserID        string    `json:"user_id"`
	RestaurantID  string    `json:"restaurant_id"`
	TableIDs      []string  `json:"table_ids"` // one table, or a declared pair in combinable order
	PartySize     int       `json:"party_size"`
	Status        string    `json:"status"`
	StartsAtLocal string    `json:"starts_at_local"`
	StartsAt      time.Time `json:"starts_at"` // absolute start: StartsAtLocal resolved in the restaurant's zone
	EndsAt        time.Time `json:"ends_at"`   // StartsAt + reservation_duration_minutes (absolute)
	CreatedAt     time.Time `json:"created_at"`
}

// State is the complete service state. Exported fields are the persisted state; slices keep
// fixture order and the unexported maps are indexes over them, rebuilt by reindex.
type State struct {
	Users        []*User             `json:"users"`
	Restaurants  []*Restaurant       `json:"restaurants"`
	Reservations []*Reservation      `json:"reservations"`
	Tokens       map[string]string   `json:"tokens"`   // bearer token -> user id
	Receipts     map[string]*Receipt `json:"receipts"` // ReceiptKey -> outcome of an idempotent request

	usersByID         map[string]*User
	usersByEmail      map[string]*User
	restaurantsByID   map[string]*Restaurant
	reservationsByRef map[string]*Reservation
}

// Empty returns a state with no data.
func Empty() *State {
	st := &State{}
	st.reindex()
	return st
}

// EmailKey is the lookup form of an email address; addresses are matched case-insensitively.
func EmailKey(email string) string { return strings.ToLower(email) }

func (st *State) reindex() {
	if st.Tokens == nil {
		st.Tokens = map[string]string{}
	}
	if st.Receipts == nil {
		st.Receipts = map[string]*Receipt{}
	}
	st.usersByID = make(map[string]*User, len(st.Users))
	st.usersByEmail = make(map[string]*User, len(st.Users))
	for _, u := range st.Users {
		st.usersByID[u.ID] = u
		st.usersByEmail[EmailKey(u.Email)] = u
	}
	st.restaurantsByID = make(map[string]*Restaurant, len(st.Restaurants))
	for _, r := range st.Restaurants {
		st.restaurantsByID[r.ID] = r
	}
	st.reservationsByRef = make(map[string]*Reservation, len(st.Reservations))
	for _, res := range st.Reservations {
		st.reservationsByRef[res.Reference] = res
	}
}

// User returns the user with the given id, or nil.
func (st *State) User(id string) *User { return st.usersByID[id] }

// UserByEmail returns the user registered under email, or nil.
func (st *State) UserByEmail(email string) *User { return st.usersByEmail[EmailKey(email)] }

// Restaurant returns the restaurant with the given id, or nil.
func (st *State) Restaurant(id string) *Restaurant { return st.restaurantsByID[id] }

// ReservationByRef returns the reservation with the given reference, or nil.
func (st *State) ReservationByRef(ref string) *Reservation { return st.reservationsByRef[ref] }
