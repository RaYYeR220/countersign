package api

import (
	"net/http"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/state"
)

type restaurantSummary struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Timezone string `json:"timezone"`
}

// listRestaurants is GET /restaurants (public), in fixture order (R-11).
func (s *Server) listRestaurants(w http.ResponseWriter, r *http.Request) {
	out := []restaurantSummary{}
	s.store.Read(func(st *state.State) {
		for _, rs := range st.Restaurants {
			out = append(out, restaurantSummary{rs.ID, rs.Name, rs.Timezone})
		}
	})
	writeJSON(w, http.StatusOK, map[string]any{"restaurants": out})
}

// restaurantDetail is the restaurant's original fixture configuration (policy 0); published
// policies and the restaurant revision are never part of it (stage 3).
type restaurantDetail struct {
	ID                         string               `json:"id"`
	Name                       string               `json:"name"`
	Timezone                   string               `json:"timezone"`
	SlotMinutes                int                  `json:"slot_minutes"`
	ReservationDurationMinutes int                  `json:"reservation_duration_minutes"`
	CancellationCutoffMinutes  int                  `json:"cancellation_cutoff_minutes"`
	OpeningHours               []state.OpeningHours `json:"opening_hours"`
	Tables                     []state.Table        `json:"tables"`
	Combinable                 [][]string           `json:"combinable"`
	ManagerUserIDs             []string             `json:"manager_user_ids"`
}

// getRestaurant is GET /restaurants/{id} (public): the restaurant in the fixture's shape.
func (s *Server) getRestaurant(w http.ResponseWriter, r *http.Request) {
	var out *restaurantDetail
	s.store.Read(func(st *state.State) {
		if rs := st.Restaurant(r.PathValue("id")); rs != nil {
			out = &restaurantDetail{rs.ID, rs.Name, rs.Timezone, rs.SlotMinutes, rs.ReservationDurationMinutes,
				rs.CancellationCutoffMinutes, rs.OpeningHours, rs.Tables, rs.Combinable, rs.ManagerUserIDs}
		}
	})
	if out == nil {
		writeError(w, apperr.NotFound("no such restaurant"))
		return
	}
	writeJSON(w, http.StatusOK, out)
}
