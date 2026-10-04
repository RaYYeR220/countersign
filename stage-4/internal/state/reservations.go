package state

import (
	"crypto/rand"
	"encoding/json"
	"regexp"
	"strings"
	"time"
)

// referencePattern is the only reference format, for issued, seeded and imported bookings (C1.81, R-28).
var referencePattern = regexp.MustCompile(`^[A-Z0-9]{6,12}$`)

// validReference reports whether ref has the reference format of C1.81.
func validReference(ref string) bool { return referencePattern.MatchString(ref) }

// referenceLength is within the 6–12 characters of A-Z0-9 that §8 allows; rand.Text uses A-Z2-7.
const referenceLength = 8

// AddReservation stores a new confirmed reservation, assigning its id and a unique reference.
func (st *State) AddReservation(res *Reservation) {
	res.ID = newID("res_", func(id string) bool {
		for _, other := range st.Reservations {
			if other.ID == id {
				return true
			}
		}
		return false
	})
	for res.Reference == "" || st.reservationsByRef[res.Reference] != nil {
		res.Reference = rand.Text()[:referenceLength]
	}
	res.Status = Confirmed
	st.Reservations = append(st.Reservations, res)
	st.reservationsByRef[res.Reference] = res
}

// TableBusy reports whether an applied closure, or a confirmed reservation other than those skip
// accepts, occupies any of the tables over any part of [start, end). skip may be nil.
func (st *State) TableBusy(restaurantID string, tableIDs []string, start, end time.Time, skip func(*Reservation) bool) bool {
	if r := st.Restaurant(restaurantID); r != nil {
		for _, id := range tableIDs {
			if r.Closed(id, start, end) {
				return true
			}
		}
	}
	for _, res := range st.Reservations {
		if res.Status != Confirmed || res.RestaurantID != restaurantID || !SharesTable(res.TableIDs, tableIDs) {
			continue
		}
		if skip != nil && skip(res) {
			continue
		}
		if res.StartsAt.Before(end) && start.Before(res.EndsAt) {
			return true
		}
	}
	return false
}

// Receipt is the stored outcome of a successful idempotent request (§7, R-18).
type Receipt struct {
	Body     string          `json:"body"` // canonical request body
	Status   int             `json:"status"`
	Response json.RawMessage `json:"response"`
}

// receiptSep separates the parts of a ReceiptKey.
const receiptSep = "\x00"

// ReceiptKey scopes an Idempotency-Key to its user, method and path.
func ReceiptKey(userID, method, path, key string) string {
	return userID + receiptSep + method + " " + path + receiptSep + key
}

// receiptUser is the user id a ReceiptKey belongs to.
func receiptUser(k string) string {
	userID, _, _ := strings.Cut(k, receiptSep)
	return userID
}

// Receipt returns the stored receipt for k, or nil.
func (st *State) Receipt(k string) *Receipt { return st.Receipts[k] }

// StoreReceipt records the outcome of a successful idempotent request.
func (st *State) StoreReceipt(k string, rc *Receipt) { st.Receipts[k] = rc }
