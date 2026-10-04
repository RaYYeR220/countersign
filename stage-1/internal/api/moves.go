package api

import (
	"encoding/json"
	"net/http"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/state"
)

const maxMoves = 8

// moveItems checks the structure of a moves body (R-22 a, all 422) and then the item field
// types (R-22 b, 400). It returns the items and their references in input order.
func moveItems(body jsonin.Object) ([]jsonin.Object, []string, error) {
	invalid := apperr.Validation
	if body.Kind("moves") != jsonin.Array {
		return nil, nil, invalid("moves must be an array of 1 to 8 objects")
	}
	var raws []json.RawMessage
	if err := json.Unmarshal(body["moves"], &raws); err != nil || len(raws) == 0 || len(raws) > maxMoves {
		return nil, nil, invalid("moves must be an array of 1 to 8 objects")
	}
	items := make([]jsonin.Object, len(raws))
	refs := make([]string, len(raws))
	seen := map[string]bool{}
	for i, raw := range raws {
		if err := json.Unmarshal(raw, &items[i]); err != nil || items[i] == nil {
			return nil, nil, invalid("every move must be an object")
		}
		ref, ok, err := items[i].String("reference")
		if err != nil || !ok {
			return nil, nil, invalid("every move needs a string reference")
		}
		if seen[ref] {
			return nil, nil, invalid("references in moves must be distinct")
		}
		seen[ref] = true
		refs[i] = ref
	}
	for _, item := range items {
		if err := changeFieldTypes(item); err != nil {
			return nil, nil, err
		}
	}
	return items, refs, nil
}

// moveReservations is POST /reservation-moves (§11, R-22): every change commits or none does.
func (s *Server) moveReservations(w http.ResponseWriter, r *http.Request, user *state.User) {
	s.keyedWrite(w, r, user, func(st *state.State, body jsonin.Object) (any, error) {
		items, refs, err := moveItems(body)
		if err != nil {
			return nil, err
		}
		changes := make([]*change, len(items))
		var restaurantID string
		for i, item := range items {
			res, err := ownReservation(st, user, refs[i])
			if err != nil {
				return nil, err
			}
			if i == 0 {
				restaurantID = res.RestaurantID
			} else if res.RestaurantID != restaurantID {
				return nil, apperr.Validation("all moved bookings must belong to the same restaurant")
			}
			if changes[i], err = planChange(st, res, item, s.now()); err != nil {
				return nil, err
			}
		}
		if conflicts(st, changes) {
			return nil, tableUnavailable()
		}
		apply(changes)
		out := make([]reservationView, len(changes))
		for i, c := range changes {
			out[i] = view(st, c.res)
		}
		return map[string]any{"reservations": out}, nil
	})
}
