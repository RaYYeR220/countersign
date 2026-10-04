package api

import (
	"bytes"
	"io"
	"net/http"
	"sort"
	"time"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/localtime"
	"tablekeeper/internal/state"
)

// reservationView is the reservation shape of every reservation response (§8, R-21).
type reservationView struct {
	ReservationID string `json:"reservation_id"`
	Reference     string `json:"reference"`
	RestaurantID  string `json:"restaurant_id"`
	TableID       string `json:"table_id"`
	PartySize     int    `json:"party_size"`
	Status        string `json:"status"`
	StartsAtLocal string `json:"starts_at_local"`
	StartsAt      string `json:"starts_at"`
	EndsAt        string `json:"ends_at"`
	CreatedAt     string `json:"created_at"`
}

// view renders res; starts_at and ends_at carry the restaurant's offset, created_at is UTC +00:00.
func view(st *state.State, res *state.Reservation) reservationView {
	loc, _ := localtime.Location(st.Restaurant(res.RestaurantID).Timezone) // validated at reset
	return reservationView{
		ReservationID: res.ID,
		Reference:     res.Reference,
		RestaurantID:  res.RestaurantID,
		TableID:       res.TableID,
		PartySize:     res.PartySize,
		Status:        res.Status,
		StartsAtLocal: res.StartsAtLocal,
		StartsAt:      localtime.Format(res.StartsAt, loc),
		EndsAt:        localtime.Format(res.EndsAt, loc),
		CreatedAt:     localtime.Format(res.CreatedAt, time.UTC),
	}
}

// ownReservation returns the caller's reservation with reference ref, or a 404 that does not
// reveal whether someone else holds it (C1.90).
func ownReservation(st *state.State, user *state.User, ref string) (*state.Reservation, error) {
	res := st.ReservationByRef(ref)
	if res == nil || res.UserID != user.ID {
		return nil, apperr.NotFound("no such reservation")
	}
	return res, nil
}

// createReservation is POST /reservations: keyedWrite supplies R-1; the body follows R-19
// (types 400 → missing 422 → values 422) and then R-7.
func (s *Server) createReservation(w http.ResponseWriter, r *http.Request, user *state.User) {
	s.keyedWrite(w, r, user, func(st *state.State, body jsonin.Object) (any, error) {
		for _, name := range []string{"restaurant_id", "table_id", "starts_at_local"} {
			if body.Has(name) && body.Kind(name) != jsonin.String {
				return nil, apperr.Malformed(name + " must be a string")
			}
		}
		for _, name := range []string{"restaurant_id", "table_id", "starts_at_local", "party_size"} {
			if !body.Has(name) {
				return nil, apperr.Validation(name + " is required")
			}
		}
		restaurantID, _, _ := body.String("restaurant_id")
		tableID, _, _ := body.String("table_id")
		local, _, _ := body.String("starts_at_local")
		if !localtime.ValidLocal(local) {
			return nil, apperr.Validation("starts_at_local must be a local YYYY-MM-DDTHH:MM")
		}
		party, err := partySize(body)
		if err != nil {
			return nil, err
		}

		rest := st.Restaurant(restaurantID)
		if rest == nil {
			return nil, apperr.NotFound("no such restaurant")
		}
		table := rest.Table(tableID)
		if table == nil {
			return nil, apperr.NotFound("no such table in this restaurant")
		}
		loc, err := localtime.Location(rest.Timezone)
		if err != nil {
			return nil, err
		}
		start, err := localtime.CheckStart(loc, rest.OpeningHours, rest.SlotMinutes, rest.ReservationDurationMinutes, local)
		if err != nil {
			return nil, err
		}
		if party > table.Capacity {
			return nil, partyExceedsCapacity()
		}
		end := localtime.End(start, rest.ReservationDurationMinutes)
		if st.TableBusy(rest.ID, table.ID, start, end, nil) {
			return nil, tableUnavailable()
		}
		res := &state.Reservation{
			UserID:        user.ID,
			RestaurantID:  rest.ID,
			TableID:       table.ID,
			PartySize:     party,
			StartsAtLocal: local,
			StartsAt:      start,
			EndsAt:        end,
			CreatedAt:     state.Stamp(s.now()),
		}
		st.AddReservation(res)
		return view(st, res), nil
	})
}

// listReservations is GET /reservations: the caller's bookings, starts_at descending, then
// created_at ascending, then reference ascending (R-11).
func (s *Server) listReservations(w http.ResponseWriter, r *http.Request, user *state.User) {
	out := []reservationView{}
	s.store.Read(func(st *state.State) {
		var mine []*state.Reservation
		for _, res := range st.Reservations {
			if res.UserID == user.ID {
				mine = append(mine, res)
			}
		}
		sort.SliceStable(mine, func(i, j int) bool {
			a, b := mine[i], mine[j]
			if !a.StartsAt.Equal(b.StartsAt) {
				return a.StartsAt.After(b.StartsAt)
			}
			if !a.CreatedAt.Equal(b.CreatedAt) {
				return a.CreatedAt.Before(b.CreatedAt)
			}
			return a.Reference < b.Reference
		})
		for _, res := range mine {
			out = append(out, view(st, res))
		}
	})
	writeJSON(w, http.StatusOK, map[string]any{"reservations": out})
}

// getReservation is GET /reservations/{reference}.
func (s *Server) getReservation(w http.ResponseWriter, r *http.Request, user *state.User) {
	var out reservationView
	var err error
	s.store.Read(func(st *state.State) {
		var res *state.Reservation
		if res, err = ownReservation(st, user, r.PathValue("reference")); err == nil {
			out = view(st, res)
		}
	})
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, out)
}

// cancelReservation is POST /reservations/{reference}/cancel (R-13): an absent or empty body is
// ignored; 404 → already cancelled 200 → 409 cutoff_passed → cancel.
func (s *Server) cancelReservation(w http.ResponseWriter, r *http.Request, user *state.User) {
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
	if err != nil {
		writeError(w, apperr.Malformed("request body could not be read"))
		return
	}
	if len(bytes.TrimSpace(raw)) > 0 {
		if _, err := jsonin.Decode(bytes.NewReader(raw)); err != nil {
			writeError(w, err)
			return
		}
	}
	var out reservationView
	s.store.Write(func(st *state.State) {
		var res *state.Reservation
		if res, err = ownReservation(st, user, r.PathValue("reference")); err != nil {
			return
		}
		if res.Status == state.Confirmed {
			if withinCutoff(res, st.Restaurant(res.RestaurantID), s.now()) {
				err = cutoffPassed()
				return
			}
			res.Status = state.Cancelled
		}
		out = view(st, res)
	})
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, out)
}

// amendReservation is PATCH /reservations/{reference} (R-14): 400 body/types → 404 → planChange
// → 409 table_unavailable. The old occupancy is released and the new one taken in one step.
func (s *Server) amendReservation(w http.ResponseWriter, r *http.Request, user *state.User) {
	body, err := readObject(w, r, maxBodyBytes)
	if err == nil {
		err = changeFieldTypes(body)
	}
	if err != nil {
		writeError(w, err)
		return
	}
	var out reservationView
	s.store.Write(func(st *state.State) {
		var res *state.Reservation
		if res, err = ownReservation(st, user, r.PathValue("reference")); err != nil {
			return
		}
		var c *change
		if c, err = planChange(st, res, body, s.now()); err != nil {
			return
		}
		if conflicts(st, []*change{c}) {
			err = tableUnavailable()
			return
		}
		apply([]*change{c})
		out = view(st, res)
	})
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, out)
}
