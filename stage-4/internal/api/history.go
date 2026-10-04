package api

import (
	"net/http"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/localtime"
	"tablekeeper/internal/state"
)

type historyEntryView struct {
	Seq           int                 `json:"seq"`
	At            string              `json:"at"`
	Event         string              `json:"event"`
	Changes       []state.FieldChange `json:"changes"`
	Revision      int                 `json:"revision"`
	AcceptedTerms state.Terms         `json:"accepted_terms"`
	PlanID        string              `json:"plan_id,omitempty"` // reassigned entries only (R-67)
}

// ownedRead answers with fn's view of the caller's reservation. Anyone else, signed in or not,
// gets the same 404 as an unknown reference: these reads never answer 401 (stage 3).
func (s *Server) ownedRead(w http.ResponseWriter, r *http.Request, fn func(st *state.State, res *state.Reservation) any) {
	user := s.caller(r)
	var out any
	s.store.Read(func(st *state.State) {
		if user == nil {
			return
		}
		if res, err := ownReservation(st, user, r.PathValue("reference")); err == nil {
			out = fn(st, res)
		}
	})
	if out == nil {
		writeError(w, apperr.NotFound("no such reservation"))
		return
	}
	writeJSON(w, http.StatusOK, out)
}

// reservationHistory is GET /reservations/{reference}/history: the record, oldest first; `at`
// carries the restaurant's offset, like starts_at.
func (s *Server) reservationHistory(w http.ResponseWriter, r *http.Request) {
	s.ownedRead(w, r, func(st *state.State, res *state.Reservation) any {
		loc, _ := localtime.Location(st.Restaurant(res.RestaurantID).Timezone)
		entries := make([]historyEntryView, len(res.History))
		for i, e := range res.History {
			entries[i] = historyEntryView{e.Seq, localtime.Format(e.At, loc), e.Event, e.Changes, e.Revision, e.AcceptedTerms, e.PlanID}
		}
		return map[string]any{"reference": res.Reference, "entries": entries}
	})
}

// reservationDecision is GET /reservations/{reference}/decision: the current revision and terms.
func (s *Server) reservationDecision(w http.ResponseWriter, r *http.Request) {
	s.ownedRead(w, r, func(st *state.State, res *state.Reservation) any {
		return map[string]any{"reference": res.Reference, "revision": res.Revision, "accepted_terms": res.Terms}
	})
}
