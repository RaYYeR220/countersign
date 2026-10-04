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

// getRestaurant is GET /restaurants/{id} (public): the restaurant in the fixture's shape.
func (s *Server) getRestaurant(w http.ResponseWriter, r *http.Request) {
	var found bool
	var out state.Restaurant
	s.store.Read(func(st *state.State) {
		if rs := st.Restaurant(r.PathValue("id")); rs != nil {
			found, out = true, *rs // restaurants are immutable between resets; a shallow copy suffices
		}
	})
	if !found {
		writeError(w, apperr.NotFound("no such restaurant"))
		return
	}
	writeJSON(w, http.StatusOK, out)
}
