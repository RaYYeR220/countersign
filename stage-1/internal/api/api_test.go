package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

func newTestServer() (*state.Store, http.Handler) {
	store := state.NewStore(state.Empty())
	now := func() time.Time { return time.Date(2026, 9, 21, 11, 4, 3, 0, time.UTC) }
	return store, New(store, now)
}

func do(h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(method, path, strings.NewReader(body)))
	return rec
}

func errorCode(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	if ct := rec.Header().Get("Content-Type"); ct != "application/json; charset=utf-8" {
		t.Errorf("Content-Type = %q", ct)
	}
	var body struct {
		Error struct{ Code, Message string }
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || body.Error.Code == "" || body.Error.Message == "" {
		t.Fatalf("not an error envelope: %s", rec.Body)
	}
	return body.Error.Code
}

func TestHealth(t *testing.T) {
	_, h := newTestServer()
	rec := do(h, http.MethodGet, "/health", "")
	if rec.Code != 200 || strings.TrimSpace(rec.Body.String()) != `{"status":"ok"}` {
		t.Errorf("health = %d %s", rec.Code, rec.Body)
	}
}

func TestUnknownRouteAndMethod(t *testing.T) {
	_, h := newTestServer()
	if rec := do(h, http.MethodGet, "/nope", ""); rec.Code != 404 || errorCode(t, rec) != "not_found" {
		t.Errorf("unknown path = %d %s", rec.Code, rec.Body)
	}
	if rec := do(h, http.MethodDelete, "/health", ""); rec.Code != 405 || errorCode(t, rec) != "method_not_allowed" {
		t.Errorf("wrong method = %d %s", rec.Code, rec.Body)
	}
}

func TestResetReplacesState(t *testing.T) {
	store, h := newTestServer()
	fixture := `{"restaurants":[{"id":"r_a","name":"A","timezone":"Europe/Berlin","slot_minutes":30,
		"reservation_duration_minutes":90,"cancellation_cutoff_minutes":120,"tables":[{"id":"t_1","label":"1","capacity":2}]}]}`
	if rec := do(h, http.MethodPost, "/_test/reset", fixture); rec.Code != 204 || rec.Body.Len() != 0 {
		t.Fatalf("reset = %d %s", rec.Code, rec.Body)
	}
	store.Read(func(st *state.State) {
		if st.Restaurant("r_a") == nil {
			t.Error("fixture not stored")
		}
	})
	if rec := do(h, http.MethodPost, "/_test/reset", `{}`); rec.Code != 204 {
		t.Fatalf("second reset = %d", rec.Code)
	}
	store.Read(func(st *state.State) {
		if st.Restaurant("r_a") != nil {
			t.Error("second reset must replace, not merge")
		}
	})
}

func TestResetRejectsBadBodiesWithoutChangingState(t *testing.T) {
	store, h := newTestServer()
	do(h, http.MethodPost, "/_test/reset", `{"restaurants":[{"id":"r_a","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0}]}`)
	cases := map[string]struct {
		body   string
		status int
		code   string
	}{
		"unparseable": {`{"users":`, 400, "malformed_request"},
		"not object":  {`[]`, 400, "malformed_request"},
		"wrong type":  {`{"restaurants":"r"}`, 400, "malformed_request"},
		"invalid":     {`{"restaurants":[{"id":"r_b"}]}`, 422, "validation_failed"},
	}
	for name, c := range cases {
		rec := do(h, http.MethodPost, "/_test/reset", c.body)
		if rec.Code != c.status || errorCode(t, rec) != c.code {
			t.Errorf("%s: %d %s", name, rec.Code, rec.Body)
		}
	}
	store.Read(func(st *state.State) {
		if st.Restaurant("r_a") == nil {
			t.Error("a rejected reset must leave the previous state in place")
		}
	})
}

func TestPanicBecomesEnvelope(t *testing.T) {
	h := recoverer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { panic("boom") }))
	rec := do(h, http.MethodGet, "/", "")
	if rec.Code != 500 || errorCode(t, rec) != "internal_error" {
		t.Errorf("panic = %d %s", rec.Code, rec.Body)
	}
}
