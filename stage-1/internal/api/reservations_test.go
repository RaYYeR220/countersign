package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

// 2026-09-24 is a Thursday; 2026-03-29 (Berlin spring forward) is a Sunday.
const bookingFixture = `{
  "users": [
    {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
    {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"}
  ],
  "restaurants": [
    {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
     "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
     "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"},
                       {"weekday": "sun", "opens": "00:00", "closes": "06:00"}],
     "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4}]},
    {"id": "r_other", "name": "Other", "timezone": "America/New_York", "slot_minutes": 15,
     "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
     "opening_hours": [{"weekday": "thu", "opens": "12:00", "closes": "22:00"}],
     "tables": [{"id": "t_9", "label": "9", "capacity": 8}]}
  ],
  "reservations": [
    {"id": "res_seed", "reference": "SEED01", "user_id": "u_bob", "restaurant_id": "r_anker",
     "table_id": "t_1", "starts_at_local": "2026-10-01T20:00", "party_size": 2}
  ]
}`

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) now() time.Time  { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) set(t time.Time) { c.mu.Lock(); c.t = t; c.mu.Unlock() }

type env struct {
	t     *testing.T
	h     http.Handler
	clock *clock
	ada   string
	bob   string
}

func newEnv(t *testing.T) *env {
	c := &clock{t: time.Date(2026, 9, 21, 11, 4, 3, 0, time.UTC)}
	e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
	resetWith(t, e.h, bookingFixture)
	e.ada = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token
	e.bob = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"bob@example.com","password":"correct horse"}`)).Token
	return e
}

func (e *env) req(method, path, token, key, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	if key != "" {
		req.Header.Set("Idempotency-Key", key)
	}
	rec := httptest.NewRecorder()
	e.h.ServeHTTP(rec, req)
	return rec
}

func (e *env) book(token, key, body string) *httptest.ResponseRecorder {
	return e.req("POST", "/reservations", token, key, body)
}

func booking(table, local string, party int) string {
	return fmt.Sprintf(`{"restaurant_id":"r_anker","table_id":%q,"starts_at_local":%q,"party_size":%d}`, table, local, party)
}

func decodeView(t *testing.T, rec *httptest.ResponseRecorder) reservationView {
	t.Helper()
	var v reservationView
	if err := json.Unmarshal(rec.Body.Bytes(), &v); err != nil || v.Reference == "" {
		t.Fatalf("not a reservation: %d %s", rec.Code, rec.Body)
	}
	return v
}

func (e *env) mustBook(token, key, table, local string, party int) reservationView {
	e.t.Helper()
	rec := e.book(token, key, booking(table, local, party))
	if rec.Code != 201 {
		e.t.Fatalf("book %s %s = %d %s", table, local, rec.Code, rec.Body)
	}
	return decodeView(e.t, rec)
}

func expect(t *testing.T, rec *httptest.ResponseRecorder, status int, code string) {
	t.Helper()
	if rec.Code != status {
		t.Errorf("status = %d, want %d: %s", rec.Code, status, rec.Body)
		return
	}
	if code != "" && errorCode(t, rec) != code {
		t.Errorf("code = %s, want %s", rec.Body, code)
	}
}

var (
	timestampPattern = regexp.MustCompile(`^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$`)
	referencePattern = regexp.MustCompile(`^[A-Z0-9]{6,12}$`)
)

func TestCreateReservation(t *testing.T) {
	e := newEnv(t)
	rec := e.book(e.ada, "k1", booking("t_2", "2026-09-24T19:00", 4))
	expect(t, rec, 201, "")
	v := decodeView(t, rec)
	want := reservationView{ReservationID: v.ReservationID, Reference: v.Reference, RestaurantID: "r_anker", TableID: "t_2",
		PartySize: 4, Status: "confirmed", StartsAtLocal: "2026-09-24T19:00", StartsAt: "2026-09-24T19:00:00+02:00",
		EndsAt: "2026-09-24T20:30:00+02:00", CreatedAt: "2026-09-21T11:04:03+00:00"}
	if v != want {
		t.Errorf("got  %+v\nwant %+v", v, want)
	}
	if !referencePattern.MatchString(v.Reference) || !timestampPattern.MatchString(v.CreatedAt) {
		t.Errorf("bad reference or timestamp: %+v", v)
	}
	var keys map[string]any
	json.Unmarshal(rec.Body.Bytes(), &keys)
	if len(keys) != 10 {
		t.Errorf("response has %d fields: %s", len(keys), rec.Body)
	}
	// Half-open occupancy: 20:30 is free, 20:00 overlaps.
	e.mustBook(e.bob, "k2", "t_2", "2026-09-24T20:30", 2)
	expect(t, e.book(e.bob, "k3", booking("t_2", "2026-09-24T20:00", 2)), 409, "table_unavailable")
	// Past starts are bookable (C1.31); 2020-01-02 is a Thursday.
	e.mustBook(e.ada, "k4", "t_1", "2020-01-02T18:00", 2)
}

func TestCreateReservationErrors(t *testing.T) {
	e := newEnv(t)
	cases := []struct {
		name, body string
		status     int
		code       string
	}{
		{"not object", `[]`, 400, "malformed_request"},
		{"unparseable", `{"restaurant_id":`, 400, "malformed_request"},
		{"restaurant_id number", `{"restaurant_id":5,"table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":2}`, 400, "malformed_request"},
		{"table_id null", `{"restaurant_id":"r_anker","table_id":null,"starts_at_local":"2026-09-24T19:00","party_size":2}`, 400, "malformed_request"},
		{"type before missing", `{"table_id":5,"starts_at_local":"2026-09-24T19:00","party_size":2}`, 400, "malformed_request"},
		{"missing party_size", `{"restaurant_id":"r_anker","table_id":"t_1","starts_at_local":"2026-09-24T19:00"}`, 422, "validation_failed"},
		{"missing restaurant", `{"table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":2}`, 422, "validation_failed"},
		{"party string", `{"restaurant_id":"r_anker","table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":"2"}`, 422, "validation_failed"},
		{"party bool", `{"restaurant_id":"r_anker","table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":true}`, 422, "validation_failed"},
		{"party null", `{"restaurant_id":"r_anker","table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":null}`, 422, "validation_failed"},
		{"party fraction", `{"restaurant_id":"r_anker","table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":2.5}`, 422, "validation_failed"},
		{"party zero", booking("t_1", "2026-09-24T19:00", 0), 422, "validation_failed"},
		{"party negative", booking("t_1", "2026-09-24T19:00", -1), 422, "validation_failed"},
		{"local seconds", booking("t_1", "2026-09-24T19:00:00", 2), 422, "validation_failed"},
		{"local Z", booking("t_1", "2026-09-24T19:00Z", 2), 422, "validation_failed"},
		{"local offset", booking("t_1", "2026-09-24T19:00+02:00", 2), 422, "validation_failed"},
		{"local month 13", booking("t_1", "2026-13-01T19:00", 2), 422, "validation_failed"},
		{"value before 404", `{"restaurant_id":"nope","table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":0}`, 422, "validation_failed"},
		{"unknown restaurant", `{"restaurant_id":"nope","table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":2}`, 404, "not_found"},
		{"unknown table", booking("t_7", "2026-09-24T19:00", 2), 404, "not_found"},
		{"table of other restaurant", booking("t_9", "2026-09-24T19:00", 2), 404, "not_found"},
		{"spring-forward gap", booking("t_1", "2026-03-29T02:30", 2), 422, "invalid_local_time"},
		{"before opening", booking("t_1", "2026-09-24T17:30", 2), 422, "outside_opening_hours"},
		{"ends after closes", booking("t_1", "2026-09-24T22:00", 2), 422, "outside_opening_hours"},
		{"closed day", booking("t_1", "2026-09-23T19:00", 2), 422, "outside_opening_hours"},
		{"off grid", booking("t_1", "2026-09-24T18:15", 2), 422, "not_on_slot_grid"},
		{"over capacity", booking("t_2", "2026-09-24T19:00", 5), 422, "party_exceeds_capacity"},
	}
	for i, c := range cases {
		rec := e.book(e.ada, fmt.Sprintf("err-%d", i), c.body)
		if rec.Code != c.status || errorCode(t, rec) != c.code {
			t.Errorf("%s: %d %s, want %d %s", c.name, rec.Code, rec.Body, c.status, c.code)
		}
	}
	if rec := e.req("GET", "/reservations", e.ada, "", ""); strings.TrimSpace(rec.Body.String()) != `{"reservations":[]}` {
		t.Errorf("rejected requests created bookings: %s", rec.Body)
	}
}

func TestIdempotency(t *testing.T) {
	e := newEnv(t)
	body := booking("t_2", "2026-09-24T19:00", 4)
	expect(t, e.book("", "k", body), 401, "unauthenticated")
	expect(t, e.book(e.ada, "", body), 400, "missing_idempotency_key")
	expect(t, e.req("POST", "/reservations", e.ada, "", `[]`), 400, "malformed_request") // 400 body precedes key
	expect(t, e.book(e.ada, strings.Repeat("k", 256), body), 422, "validation_failed")

	first := e.book(e.ada, strings.Repeat("k", 255), body)
	expect(t, first, 201, "")
	reordered := ` { "party_size" : 4.0, "starts_at_local":"2026-09-24T19:00", "table_id":"t_2","restaurant_id":"r_anker" } `
	replay := e.book(e.ada, strings.Repeat("k", 255), reordered)
	if replay.Code != 200 || replay.Body.String() != first.Body.String() {
		t.Errorf("replay = %d %s, want 200 %s", replay.Code, replay.Body, first.Body)
	}
	expect(t, e.book(e.ada, strings.Repeat("k", 255), booking("t_2", "2026-09-24T19:00", 3)), 409, "idempotency_key_reuse")
	expect(t, e.book(e.ada, strings.Repeat("k", 255), `{"party_size":"x"}`), 409, "idempotency_key_reuse")

	// Same key, other user: independent.
	expect(t, e.book(e.bob, strings.Repeat("k", 255), booking("t_1", "2026-09-24T19:00", 2)), 201, "")

	// A key whose first use failed is a first use again.
	expect(t, e.book(e.ada, "retry", booking("t_2", "2026-09-24T18:15", 2)), 422, "not_on_slot_grid")
	expect(t, e.book(e.ada, "retry", booking("t_2", "2026-09-24T21:00", 2)), 201, "")

	// Replay after cancel returns the original confirmed body and changes nothing.
	v := decodeView(t, first)
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", ""), 200, "")
	replay = e.book(e.ada, strings.Repeat("k", 255), body)
	if replay.Code != 200 || replay.Body.String() != first.Body.String() {
		t.Errorf("replay after cancel = %d %s", replay.Code, replay.Body)
	}
	if got := decodeView(t, e.req("GET", "/reservations/"+v.Reference, e.ada, "", "")); got.Status != "cancelled" {
		t.Errorf("replay changed state: %+v", got)
	}
}

func TestConcurrentConflictingBookings(t *testing.T) {
	e := newEnv(t)
	tokens := make([]string, 50)
	for i := range tokens {
		email := fmt.Sprintf("user%d@example.com", i)
		tokens[i] = decodeSession(t, do(e.h, "POST", "/auth/signup", fmt.Sprintf(`{"email":%q,"password":"12345678","display_name":"U"}`, email))).Token
	}
	codes := make([]int, len(tokens))
	var wg sync.WaitGroup
	for i := range tokens {
		wg.Go(func() {
			codes[i] = e.book(tokens[i], fmt.Sprintf("c%d", i), booking("t_2", "2026-09-24T19:00", 2)).Code
		})
	}
	wg.Wait()
	created, conflicts := 0, 0
	for _, c := range codes {
		switch c {
		case 201:
			created++
		case 409:
			conflicts++
		}
	}
	if created != 1 || conflicts != 49 {
		t.Errorf("created=%d conflicts=%d, want 1 and 49", created, conflicts)
	}
}

func TestConcurrentIdenticalReplays(t *testing.T) {
	e := newEnv(t)
	recs := make([]*httptest.ResponseRecorder, 20)
	var wg sync.WaitGroup
	for i := range recs {
		wg.Go(func() { recs[i] = e.book(e.ada, "same", booking("t_2", "2026-09-24T19:00", 2)) })
	}
	wg.Wait()
	created := 0
	for _, rec := range recs {
		if rec.Code == 201 {
			created++
		} else if rec.Code != 200 {
			t.Errorf("unexpected %d %s", rec.Code, rec.Body)
		}
		if rec.Body.String() != recs[0].Body.String() {
			t.Errorf("bodies differ: %s vs %s", rec.Body, recs[0].Body)
		}
	}
	var list struct{ Reservations []reservationView }
	json.Unmarshal(e.req("GET", "/reservations", e.ada, "", "").Body.Bytes(), &list)
	if created != 1 || len(list.Reservations) != 1 {
		t.Errorf("created=%d bookings=%d, want 1 and 1", created, len(list.Reservations))
	}
}

func TestListAndGet(t *testing.T) {
	e := newEnv(t)
	early := e.mustBook(e.ada, "a", "t_1", "2026-09-24T18:00", 2)
	late := e.mustBook(e.ada, "b", "t_1", "2026-09-24T21:00", 2)
	e.clock.set(e.clock.now().Add(time.Second))
	sameStartLater := e.mustBook(e.ada, "c", "t_2", "2026-09-24T21:00", 2)
	var list struct{ Reservations []reservationView }
	json.Unmarshal(e.req("GET", "/reservations", e.ada, "", "").Body.Bytes(), &list)
	var refs []string
	for _, v := range list.Reservations {
		refs = append(refs, v.Reference)
	}
	if want := []string{late.Reference, sameStartLater.Reference, early.Reference}; fmt.Sprint(refs) != fmt.Sprint(want) {
		t.Errorf("order = %v, want %v", refs, want)
	}
	// Bob sees only his seeded booking, with its fixture id and reference.
	json.Unmarshal(e.req("GET", "/reservations", e.bob, "", "").Body.Bytes(), &list)
	if len(list.Reservations) != 1 || list.Reservations[0].ReservationID != "res_seed" || list.Reservations[0].Reference != "SEED01" ||
		list.Reservations[0].StartsAt != "2026-10-01T20:00:00+02:00" || list.Reservations[0].Status != "confirmed" {
		t.Errorf("bob's list = %+v", list.Reservations)
	}
	expect(t, e.req("GET", "/reservations/SEED01", e.ada, "", ""), 404, "not_found")
	expect(t, e.req("GET", "/reservations/NOPE00", e.ada, "", ""), 404, "not_found")
	expect(t, e.req("GET", "/reservations/SEED01", "", "", ""), 401, "unauthenticated")
	expect(t, e.req("GET", "/reservations", "", "", ""), 401, "unauthenticated")
	if got := decodeView(t, e.req("GET", "/reservations/SEED01", e.bob, "", "")); got.ReservationID != "res_seed" {
		t.Errorf("seeded get = %+v", got)
	}
	// The seeded booking occupies its table.
	expect(t, e.book(e.ada, "d", booking("t_1", "2026-10-01T19:00", 2)), 409, "table_unavailable")
}

func availableAt(t *testing.T, h http.Handler, date, local string) []string {
	t.Helper()
	var resp availabilityResponse
	json.Unmarshal(do(h, "GET", "/availability?restaurant_id=r_anker&party_size=2&date="+date, "").Body.Bytes(), &resp)
	for _, s := range resp.Slots {
		if s.StartsAtLocal == local {
			return s.AvailableTableIDs
		}
	}
	t.Fatalf("no slot %s", local)
	return nil
}

func TestCancel(t *testing.T) {
	e := newEnv(t)
	v := e.mustBook(e.ada, "a", "t_1", "2026-09-24T19:00", 2)
	if ids := availableAt(t, e.h, "2026-09-24", "2026-09-24T19:00"); fmt.Sprint(ids) != "[t_2]" {
		t.Errorf("before cancel: %v", ids)
	}
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.bob, "", ""), 404, "not_found")
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", "{"), 400, "malformed_request")
	rec := e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", "")
	expect(t, rec, 200, "")
	got := decodeView(t, rec)
	v.Status = "cancelled"
	if got != v {
		t.Errorf("cancelled = %+v, want %+v", got, v)
	}
	if ids := availableAt(t, e.h, "2026-09-24", "2026-09-24T19:00"); fmt.Sprint(ids) != "[t_1 t_2]" {
		t.Errorf("after cancel: %v", ids)
	}
	// Cancelling twice is fine, even past the cutoff.
	e.clock.set(time.Date(2026, 9, 24, 18, 0, 0, 0, time.UTC))
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", `{}`), 200, "")
}

func TestCutoffBoundary(t *testing.T) {
	e := newEnv(t)
	v := e.mustBook(e.ada, "a", "t_1", "2026-09-24T18:00", 2) // 16:00 UTC, cutoff 120 min
	past := e.mustBook(e.ada, "b", "t_1", "2020-01-02T18:00", 2)
	expect(t, e.req("POST", "/reservations/"+past.Reference+"/cancel", e.ada, "", ""), 409, "cutoff_passed")
	expect(t, e.req("PATCH", "/reservations/"+past.Reference, e.ada, "", `{"party_size":1}`), 409, "cutoff_passed")
	e.clock.set(time.Date(2026, 9, 24, 14, 0, 0, 0, time.UTC)) // exactly 120 min before
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", ""), 409, "cutoff_passed")
	e.clock.set(time.Date(2026, 9, 24, 13, 59, 59, 0, time.UTC))
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", ""), 200, "")
}

func TestAmend(t *testing.T) {
	e := newEnv(t)
	v := e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 4)
	other := e.mustBook(e.bob, "b", "t_1", "2026-09-24T19:00", 2)
	patch := func(ref, body string) *httptest.ResponseRecorder {
		return e.req("PATCH", "/reservations/"+ref, e.ada, "", body)
	}

	got := decodeView(t, patch(v.Reference, `{"party_size":3}`))
	if got.PartySize != 3 || got.Reference != v.Reference || got.ReservationID != v.ReservationID || got.StartsAt != v.StartsAt {
		t.Errorf("party patch = %+v", got)
	}
	// Moving by 30 minutes on the same table overlaps only itself.
	got = decodeView(t, patch(v.Reference, `{"starts_at_local":"2026-09-24T19:30"}`))
	if got.StartsAt != "2026-09-24T19:30:00+02:00" || got.EndsAt != "2026-09-24T21:00:00+02:00" || got.CreatedAt != v.CreatedAt {
		t.Errorf("time patch = %+v", got)
	}
	if ids := availableAt(t, e.h, "2026-09-24", "2026-09-24T18:00"); fmt.Sprint(ids) != "[t_2]" {
		t.Errorf("old slot not released: %v", ids)
	}
	before := e.req("GET", "/reservations/"+v.Reference, e.ada, "", "").Body.String()
	cases := []struct {
		name, body string
		status     int
		code       string
	}{
		{"not object", `[]`, 400, "malformed_request"},
		{"table null", `{"table_id":null}`, 400, "malformed_request"},
		{"local number", `{"starts_at_local":1900}`, 400, "malformed_request"},
		{"party string", `{"party_size":"2"}`, 422, "validation_failed"},
		{"bad local", `{"starts_at_local":"2026-09-24 19:00"}`, 422, "validation_failed"},
		{"unknown table", `{"table_id":"t_9"}`, 404, "not_found"},
		{"off grid", `{"starts_at_local":"2026-09-24T19:10"}`, 422, "not_on_slot_grid"},
		{"over capacity", `{"table_id":"t_1"}`, 422, "party_exceeds_capacity"},
		{"overlap", `{"table_id":"t_1","party_size":2}`, 409, "table_unavailable"},
	}
	for _, c := range cases {
		rec := patch(v.Reference, c.body)
		if rec.Code != c.status || errorCode(t, rec) != c.code {
			t.Errorf("%s: %d %s, want %d %s", c.name, rec.Code, rec.Body, c.status, c.code)
		}
	}
	if after := e.req("GET", "/reservations/"+v.Reference, e.ada, "", "").Body.String(); after != before {
		t.Errorf("failed amendments changed the booking:\n%s\n%s", before, after)
	}
	expect(t, patch(other.Reference, `{"party_size":1}`), 404, "not_found")
	expect(t, patch(v.Reference, `{}`), 200, "")
	expect(t, e.req("PATCH", "/reservations/"+v.Reference, "", "", `{}`), 401, "unauthenticated")
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", ""), 200, "")
	expect(t, patch(v.Reference, `{"party_size":0}`), 409, "reservation_cancelled")
}

// A fixture with a malformed seeded reference is refused and the previous state stays (R-28).
func TestResetRejectsBadSeedReferenceWithoutChange(t *testing.T) {
	e := newEnv(t)
	v := e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 2)
	for _, ref := range []string{"X", "seed01", "ABCDEFGHJKLMN", "SEED-01", "", strings.Repeat("A", 65)} {
		body := strings.Replace(bookingFixture, `"SEED01"`, fmt.Sprintf("%q", ref), 1)
		expect(t, do(e.h, "POST", "/_test/reset", body), 422, "validation_failed")
	}
	if got := decodeView(t, e.req("GET", "/reservations/"+v.Reference, e.ada, "", "")); got != v {
		t.Errorf("state changed by a refused reset: %+v", got)
	}
	if !referencePattern.MatchString(v.Reference) {
		t.Errorf("issued reference %q", v.Reference)
	}
}
