package api

import (
	"net/http"

	"tablekeeper/internal/state"
)

// health is GET /health (§3.2): the store is ready as soon as the server is listening.
func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// reset is POST /_test/reset (§3.3): build the fixture state off-lock, then swap it in.
func (s *Server) reset(w http.ResponseWriter, r *http.Request) {
	fx, err := readObject(w, r, maxControlBodyBytes)
	if err != nil {
		writeError(w, err)
		return
	}
	st, err := state.FromFixture(fx, s.now().UTC())
	if err != nil {
		writeError(w, err)
		return
	}
	s.store.Replace(st)
	writeNoContent(w)
}
