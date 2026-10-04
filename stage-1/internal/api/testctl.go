package api

import (
	"net/http"

	"tablekeeper/internal/snapshot"
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

// export is GET /_test/export (§10): an atomic, read-only snapshot serialised under the read lock.
func (s *Server) export(w http.ResponseWriter, r *http.Request) {
	var body []byte
	var err error
	s.store.Read(func(st *state.State) { body, err = snapshot.Export(st) })
	if err != nil {
		writeError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	w.Write(body)
}

// importState is POST /_test/import (§10): decode and validate the export off-lock, then replace
// the whole state at once. A rejected import leaves the destination unchanged.
func (s *Server) importState(w http.ResponseWriter, r *http.Request) {
	env, err := readObject(w, r, maxControlBodyBytes)
	if err != nil {
		writeError(w, err)
		return
	}
	st, err := snapshot.Import(env)
	if err != nil {
		writeError(w, err)
		return
	}
	s.store.Replace(st)
	writeNoContent(w)
}
