package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"tablekeeper/internal/state"
)

const authFixture = `{
  "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
  "restaurants": [
    {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
     "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
     "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
     "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4}]},
    {"id": "r_b", "name": "B", "timezone": "America/New_York", "slot_minutes": 15,
     "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0, "opening_hours": [], "tables": []}
  ]
}`

func resetWith(t *testing.T, h http.Handler, fixture string) {
	t.Helper()
	if rec := do(h, http.MethodPost, "/_test/reset", fixture); rec.Code != http.StatusNoContent {
		t.Fatalf("reset = %d %s", rec.Code, rec.Body)
	}
}

func decodeSession(t *testing.T, rec *httptest.ResponseRecorder) session {
	t.Helper()
	var s session
	if err := json.Unmarshal(rec.Body.Bytes(), &s); err != nil || s.UserID == "" || s.Token == "" {
		t.Fatalf("not a session: %s", rec.Body)
	}
	return s
}

func TestSignupAndLogin(t *testing.T) {
	_, h := newTestServer()
	resetWith(t, h, authFixture)

	rec := do(h, http.MethodPost, "/auth/login", `{"email":"ADA@example.com","password":"correct horse"}`)
	if rec.Code != 200 {
		t.Fatalf("seeded login = %d %s", rec.Code, rec.Body)
	}
	if s := decodeSession(t, rec); s.UserID != "u_ada" || s.DisplayName != "Ada" {
		t.Errorf("seeded session = %+v", s)
	}

	rec = do(h, http.MethodPost, "/auth/signup", `{"email":"bob@example.com","password":"12345678","display_name":"Bob"}`)
	if rec.Code != 201 {
		t.Fatalf("signup = %d %s", rec.Code, rec.Body)
	}
	first := decodeSession(t, rec)
	rec = do(h, http.MethodPost, "/auth/login", `{"email":"bob@example.com","password":"12345678"}`)
	second := decodeSession(t, rec)
	if rec.Code != 200 || second.UserID != first.UserID || second.Token == first.Token {
		t.Errorf("login after signup = %d %+v", rec.Code, second)
	}
}

func TestSignupErrors(t *testing.T) {
	_, h := newTestServer()
	resetWith(t, h, authFixture)
	cases := []struct {
		body   string
		status int
		code   string
	}{
		{`{"email":"a@b.c","password":"12345678"`, 400, "malformed_request"},
		{`[]`, 400, "malformed_request"},
		{`{"email":5,"password":"12345678","display_name":"A"}`, 400, "malformed_request"},
		{`{"email":"a@b.c","password":null,"display_name":"A"}`, 400, "malformed_request"},
		{`{"email":"bad","display_name":5}`, 400, "malformed_request"}, // type errors precede missing/format
		{`{"password":"12345678","display_name":"A"}`, 422, "validation_failed"},
		{`{"email":"ada","password":"12345678","display_name":"A"}`, 422, "validation_failed"},
		{`{"email":"@x","password":"12345678","display_name":"A"}`, 422, "validation_failed"},
		{`{"email":"a@","password":"12345678","display_name":"A"}`, 422, "validation_failed"},
		{`{"email":"a@b@c","password":"12345678","display_name":"A"}`, 422, "validation_failed"},
		{`{"email":"a b@c","password":"12345678","display_name":"A"}`, 422, "validation_failed"},
		{`{"email":"a@b.c","password":"1234567","display_name":"A"}`, 422, "validation_failed"},
		{`{"email":"a@b.c","password":"12345678","display_name":"  "}`, 422, "validation_failed"},
		{`{"email":"ada@example.com","password":"1234567","display_name":"A"}`, 422, "validation_failed"}, // 422 precedes 409
		{`{"email":"Ada@Example.com","password":"12345678","display_name":"A"}`, 409, "email_taken"},
	}
	for _, c := range cases {
		rec := do(h, http.MethodPost, "/auth/signup", c.body)
		if rec.Code != c.status || errorCode(t, rec) != c.code {
			t.Errorf("signup %s = %d %s, want %d %s", c.body, rec.Code, rec.Body, c.status, c.code)
		}
	}
	if rec := do(h, http.MethodPost, "/auth/signup", `{"email":"n@b.c","password":"ääääääää","display_name":"N","extra":1}`); rec.Code != 201 {
		t.Errorf("8 code points + unknown field = %d %s", rec.Code, rec.Body)
	}
}

func TestLoginErrors(t *testing.T) {
	_, h := newTestServer()
	resetWith(t, h, authFixture)
	cases := []struct {
		body   string
		status int
		code   string
	}{
		{`nope`, 400, "malformed_request"},
		{`{"email":"ada@example.com","password":7}`, 400, "malformed_request"},
		{`{"email":"ada@example.com"}`, 422, "validation_failed"},
		{`{"email":"ada@example.com","password":"wrong horse"}`, 401, "unauthenticated"},
		{`{"email":"nobody@example.com","password":"correct horse"}`, 401, "unauthenticated"},
	}
	for _, c := range cases {
		rec := do(h, http.MethodPost, "/auth/login", c.body)
		if rec.Code != c.status || errorCode(t, rec) != c.code {
			t.Errorf("login %s = %d %s, want %d %s", c.body, rec.Code, rec.Body, c.status, c.code)
		}
	}
}

func TestAuthedRejectsBadTokens(t *testing.T) {
	store, h := newTestServer()
	resetWith(t, h, authFixture)
	tok := decodeSession(t, do(h, http.MethodPost, "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token
	tok2 := decodeSession(t, do(h, http.MethodPost, "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token

	s := &Server{store: store}
	probe := s.authed(func(w http.ResponseWriter, r *http.Request, u *state.User) {
		writeJSON(w, 200, map[string]string{"user": u.ID})
	})
	for _, header := range []string{"", "Basic x", "Bearer", "Bearer ", "Bearer nope", "Token " + tok} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/x", nil)
		if header != "" {
			req.Header.Set("Authorization", header)
		}
		probe(rec, req)
		if rec.Code != 401 || errorCode(t, rec) != "unauthenticated" {
			t.Errorf("Authorization %q = %d %s", header, rec.Code, rec.Body)
		}
	}
	for _, good := range []string{tok, tok2} { // older tokens stay valid (C1.54)
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/x", nil)
		req.Header.Set("Authorization", "Bearer "+good)
		probe(rec, req)
		if rec.Code != 200 || !strings.Contains(rec.Body.String(), "u_ada") {
			t.Errorf("valid token = %d %s", rec.Code, rec.Body)
		}
	}
	resetWith(t, h, authFixture)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/x", nil)
	req.Header.Set("Authorization", "Bearer "+tok)
	probe(rec, req)
	if rec.Code != 401 {
		t.Errorf("token must not survive reset: %d", rec.Code)
	}
}

func TestRestaurants(t *testing.T) {
	_, h := newTestServer()
	resetWith(t, h, authFixture)
	rec := do(h, http.MethodGet, "/restaurants", "")
	want := `{"restaurants":[{"id":"r_anker","name":"Zum Anker","timezone":"Europe/Berlin"},{"id":"r_b","name":"B","timezone":"America/New_York"}]}`
	if rec.Code != 200 || strings.TrimSpace(rec.Body.String()) != want {
		t.Errorf("list = %d %s", rec.Code, rec.Body)
	}
	rec = do(h, http.MethodGet, "/restaurants/r_anker", "")
	var got, exp map[string]any
	json.Unmarshal(rec.Body.Bytes(), &got)
	var fx struct{ Restaurants []map[string]any }
	json.Unmarshal([]byte(authFixture), &fx)
	exp = fx.Restaurants[0]
	gotJSON, _ := json.Marshal(got)
	expJSON, _ := json.Marshal(exp)
	if rec.Code != 200 || string(gotJSON) != string(expJSON) {
		t.Errorf("detail = %d\n got %s\nwant %s", rec.Code, gotJSON, expJSON)
	}
	if rec := do(h, http.MethodGet, "/restaurants/nope", ""); rec.Code != 404 || errorCode(t, rec) != "not_found" {
		t.Errorf("unknown = %d %s", rec.Code, rec.Body)
	}
	rec = httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/restaurants", nil)
	req.Header.Set("Authorization", "Bearer garbage") // R-10: ignored on public endpoints
	h.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Errorf("public endpoint with bad token = %d", rec.Code)
	}
	resetWith(t, h, `{}`)
	if rec := do(h, http.MethodGet, "/restaurants", ""); strings.TrimSpace(rec.Body.String()) != `{"restaurants":[]}` {
		t.Errorf("empty list = %s", rec.Body)
	}
}

func TestPasswordsNeverStoredInPlaintext(t *testing.T) {
	store, h := newTestServer()
	resetWith(t, h, authFixture)
	do(h, http.MethodPost, "/auth/signup", `{"email":"bob@example.com","password":"bobs secret","display_name":"Bob"}`)
	store.Read(func(st *state.State) {
		dump, _ := json.Marshal(st)
		if strings.Contains(string(dump), "correct horse") || strings.Contains(string(dump), "bobs secret") {
			t.Error("plaintext password in state")
		}
	})
}
