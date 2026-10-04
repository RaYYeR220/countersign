package api

import (
	"net/http"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/state"
)

// publishPolicy is POST /restaurants/{id}/policies: a keyed write (R-1 order up to the replay),
// then 404 unknown restaurant → 403 not a manager → 422 invalid policy. A published policy gets
// the next version; failures and replays allocate none.
func (s *Server) publishPolicy(w http.ResponseWriter, r *http.Request, user *state.User) {
	s.keyedWrite(w, r, user, func(st *state.State, body jsonin.Object) (any, error) {
		rest := st.Restaurant(r.PathValue("id"))
		if rest == nil {
			return nil, apperr.NotFound("no such restaurant")
		}
		if !rest.IsManager(user.ID) {
			return nil, apperr.New(http.StatusForbidden, "forbidden", "only the restaurant's managers may publish policies")
		}
		p, err := state.ParsePolicy(rest, body)
		if err != nil {
			return nil, err
		}
		p = rest.Publish(p)
		rest.Revision++
		return p, nil
	})
}

// listPolicies is GET /restaurants/{id}/policies (public): published policies in publication
// order, without policy 0.
func (s *Server) listPolicies(w http.ResponseWriter, r *http.Request) {
	var out []state.Policy
	s.store.Read(func(st *state.State) {
		if rest := st.Restaurant(r.PathValue("id")); rest != nil {
			out = append([]state.Policy{}, rest.Policies...)
		}
	})
	if out == nil {
		writeError(w, apperr.NotFound("no such restaurant"))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"policies": out})
}
