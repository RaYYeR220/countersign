// Package api is the HTTP surface: routing, request parsing and the §5 error envelope.
package api

import (
	"log"
	"net/http"
	"path"
	"time"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/state"
	"tablekeeper/internal/web"
)

// Server serves the Tablekeeper API over one state store.
type Server struct {
	store *state.Store
	now   func() time.Time
}

// New returns the HTTP handler for the whole API.
func New(store *state.Store, now func() time.Time) http.Handler {
	s := &Server{store: store, now: now}
	mux := http.NewServeMux()
	mux.Handle("/health", methods{http.MethodGet: s.health})
	mux.Handle("/_test/reset", methods{http.MethodPost: s.reset})
	mux.Handle("/_test/export", methods{http.MethodGet: s.export})
	mux.Handle("/_test/import", methods{http.MethodPost: s.importState})
	mux.Handle("/auth/signup", methods{http.MethodPost: s.signup})
	mux.Handle("/auth/login", methods{http.MethodPost: s.login})
	mux.Handle("/restaurants", methods{http.MethodGet: s.listRestaurants})
	mux.Handle("/restaurants/{id}", methods{http.MethodGet: s.getRestaurant})
	mux.Handle("/restaurants/{id}/policies", methods{
		http.MethodGet:  s.listPolicies,
		http.MethodPost: s.authed(s.publishPolicy),
	})
	mux.Handle("/availability", methods{http.MethodGet: s.availability})
	mux.Handle("/reservations", methods{
		http.MethodGet:  s.authed(s.listReservations),
		http.MethodPost: s.authed(s.createReservation),
	})
	mux.Handle("/reservations/{reference}", methods{
		http.MethodGet:   s.authed(s.getReservation),
		http.MethodPatch: s.authed(s.amendReservation),
	})
	mux.Handle("/reservations/{reference}/cancel", methods{http.MethodPost: s.authed(s.cancelReservation)})
	mux.Handle("/reservations/{reference}/history", methods{http.MethodGet: s.reservationHistory})
	mux.Handle("/reservations/{reference}/decision", methods{http.MethodGet: s.reservationDecision})
	mux.Handle("/series", methods{http.MethodPost: s.authed(s.createSeries)})
	mux.Handle("/series/{id}", methods{http.MethodGet: s.getSeries})
	mux.Handle("/series/{id}/amend", methods{http.MethodPost: s.authed(s.amendSeries)})
	mux.Handle("/reservation-moves", methods{http.MethodPost: s.authed(s.moveReservations)})
	notFound := func(w http.ResponseWriter, r *http.Request) {
		writeError(w, apperr.NotFound("no such endpoint"))
	}
	for _, route := range web.Routes {
		mux.Handle(route, methods{http.MethodGet: web.Page, http.MethodHead: web.Page})
	}
	mux.Handle("/assets/{name}", methods{http.MethodGet: web.Asset(notFound), http.MethodHead: web.Asset(notFound)})
	mux.HandleFunc("/", notFound)
	return recoverer(cleanPaths(mux))
}

// cleanPaths answers non-canonical paths (empty segments, dot segments) with a 404 envelope
// instead of ServeMux's redirect, so every unknown path is a plain 404 (R-9).
func cleanPaths(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if p := r.URL.Path; p != "/" && path.Clean(p) != p {
			writeError(w, apperr.NotFound("no such endpoint"))
			return
		}
		next.ServeHTTP(w, r)
	})
}

// methods dispatches a route by HTTP method; other methods get a 405 envelope.
type methods map[string]http.HandlerFunc

func (m methods) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if h, ok := m[r.Method]; ok {
		h(w, r)
		return
	}
	writeError(w, apperr.New(http.StatusMethodNotAllowed, "method_not_allowed", r.Method+" is not supported here"))
}

// recoverer turns a panic into a 500 envelope instead of a dropped connection.
func recoverer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if p := recover(); p != nil {
				if p == http.ErrAbortHandler {
					panic(p)
				}
				log.Printf("panic serving %s %s: %v", r.Method, r.URL.Path, p)
				writeError(w, apperr.New(http.StatusInternalServerError, "internal_error", "internal error"))
			}
		}()
		next.ServeHTTP(w, r)
	})
}
