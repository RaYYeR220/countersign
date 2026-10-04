package api

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

// 2026-09-24 is a Thursday.
const comboFixture = `{
  "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
  "restaurants": [
    {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
     "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
     "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
     "tables": [{"id": "t_1", "label": "Window", "capacity": 2}, {"id": "t_2", "label": "Booth", "capacity": 4},
                {"id": "t_3", "label": "Corner", "capacity": 4}, {"id": "t_4", "label": "Bar", "capacity": 2}],
     "combinable": [["t_1", "t_2"], ["t_3", "t_2"]]}
  ],
  "reservations": [
    {"id": "res_pair", "reference": "PAIR01", "user_id": "u_ada", "restaurant_id": "r_anker",
     "table_ids": ["t_2", "t_1"], "starts_at_local": "2026-10-01T18:00", "party_size": 5},
    {"id": "res_gone", "reference": "GONE01", "user_id": "u_ada", "restaurant_id": "r_anker",
     "table_id": "t_4", "starts_at_local": "2026-10-01T18:00", "party_size": 2, "status": "cancelled"}
  ]
}`

func newComboEnv(t *testing.T) *env {
	c := &clock{t: time.Date(2026, 9, 21, 11, 4, 3, 0, time.UTC)}
	e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
	resetWith(t, e.h, comboFixture)
	e.ada = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token
	return e
}

func pairBooking(ids, local string, party int) string {
	return fmt.Sprintf(`{"restaurant_id":"r_anker","table_ids":%s,"starts_at_local":%q,"party_size":%d}`, ids, local, party)
}

func slotAt(t *testing.T, e *env, date, local string, party int) availabilitySlot {
	t.Helper()
	var resp availabilityResponse
	json.Unmarshal(do(e.h, "GET", fmt.Sprintf("/availability?restaurant_id=r_anker&date=%s&party_size=%d", date, party), "").Body.Bytes(), &resp)
	for _, s := range resp.Slots {
		if s.StartsAtLocal == local {
			return s
		}
	}
	t.Fatalf("no slot %s", local)
	return availabilitySlot{}
}

func optionsString(s availabilitySlot) string {
	var parts []string
	for _, o := range s.AvailableOptions {
		parts = append(parts, fmt.Sprintf("%s=%d", strings.Join(o.TableIDs, "+"), o.Capacity))
	}
	return strings.Join(parts, " ")
}

func TestAvailabilityOptions(t *testing.T) {
	e := newComboEnv(t)
	s := slotAt(t, e, "2026-09-24", "2026-09-24T19:00", 2)
	if got := optionsString(s); got != "t_1=2 t_2=4 t_3=4 t_4=2 t_1+t_2=6 t_3+t_2=8" {
		t.Errorf("party 2 options = %s", got)
	}
	if fmt.Sprint(s.AvailableTableIDs) != "[t_1 t_2 t_3 t_4]" {
		t.Errorf("available_table_ids = %v", s.AvailableTableIDs)
	}
	if got := optionsString(slotAt(t, e, "2026-09-24", "2026-09-24T19:00", 5)); got != "t_1+t_2=6 t_3+t_2=8" {
		t.Errorf("party 5 options = %s", got)
	}
	// The seeded pair occupies both tables; the cancelled seed occupies nothing.
	s = slotAt(t, e, "2026-10-01", "2026-10-01T18:00", 2)
	if fmt.Sprint(s.AvailableTableIDs) != "[t_3 t_4]" || optionsString(s) != "t_3=4 t_4=2" {
		t.Errorf("seeded day = %v / %s", s.AvailableTableIDs, optionsString(s))
	}
}

func TestBookPair(t *testing.T) {
	e := newComboEnv(t)
	rec := e.book(e.ada, "p", pairBooking(`["t_2","t_1"]`, "2026-09-24T19:00", 6))
	expect(t, rec, 201, "")
	var raw map[string]any
	json.Unmarshal(rec.Body.Bytes(), &raw)
	if _, has := raw["table_id"]; has || fmt.Sprint(raw["table_ids"]) != "[t_1 t_2]" {
		t.Errorf("pair response = %s", rec.Body)
	}
	single := decodeView(t, e.book(e.ada, "s", pairBooking(`["t_3"]`, "2026-09-24T21:00", 2)))
	if single.TableID != "t_3" || fmt.Sprint(single.TableIDs) != "[t_3]" {
		t.Errorf("single via table_ids = %+v", single)
	}
	s := slotAt(t, e, "2026-09-24", "2026-09-24T19:00", 2)
	if optionsString(s) != "t_3=4 t_4=2" {
		t.Errorf("after pair booking: %s", optionsString(s))
	}
	// Any member taken → 409 for a single and for the other pair sharing t_2.
	expect(t, e.book(e.ada, "x1", booking("t_1", "2026-09-24T20:00", 2)), 409, "table_unavailable")
	expect(t, e.book(e.ada, "x2", pairBooking(`["t_2","t_3"]`, "2026-09-24T18:00", 6)), 409, "table_unavailable")
	// Cancelling frees both tables.
	expect(t, e.req("POST", "/reservations/"+raw["reference"].(string)+"/cancel", e.ada, "", ""), 200, "")
	if got := optionsString(slotAt(t, e, "2026-09-24", "2026-09-24T19:00", 2)); got != "t_1=2 t_2=4 t_3=4 t_4=2 t_1+t_2=6 t_3+t_2=8" {
		t.Errorf("after cancel: %s", got)
	}
}

func TestPairErrors(t *testing.T) {
	e := newComboEnv(t)
	cases := []struct {
		name, body string
		status     int
		code       string
	}{
		{"table_ids string", pairBooking(`"t_1"`, "2026-09-24T19:00", 2), 400, "malformed_request"},
		{"table_ids number item", pairBooking(`[1]`, "2026-09-24T19:00", 2), 400, "malformed_request"},
		{"table_ids null", pairBooking(`null`, "2026-09-24T19:00", 2), 400, "malformed_request"},
		{"no tables", `{"restaurant_id":"r_anker","starts_at_local":"2026-09-24T19:00","party_size":2}`, 422, "validation_failed"},
		{"both fields, table_ids wrong type", `{"restaurant_id":"r_anker","table_id":"t_1","table_ids":"t_1","starts_at_local":"2026-09-24T19:00","party_size":2}`, 422, "validation_failed"},
		{"both fields", `{"restaurant_id":"r_anker","table_id":"t_1","table_ids":["t_1"],"starts_at_local":"2026-09-24T19:00","party_size":2}`, 422, "validation_failed"},
		{"empty set", pairBooking(`[]`, "2026-09-24T19:00", 2), 422, "validation_failed"},
		{"duplicate", pairBooking(`["t_1","t_1"]`, "2026-09-24T19:00", 2), 422, "validation_failed"},
		{"three tables", pairBooking(`["t_1","t_2","t_3"]`, "2026-09-24T19:00", 2), 422, "combination_not_allowed"},
		{"undeclared pair", pairBooking(`["t_1","t_3"]`, "2026-09-24T19:00", 2), 422, "combination_not_allowed"},
		{"unknown member", pairBooking(`["t_1","t_9"]`, "2026-09-24T19:00", 2), 404, "not_found"},
		{"over summed capacity", pairBooking(`["t_1","t_2"]`, "2026-09-24T19:00", 7), 422, "party_exceeds_capacity"},
		{"off grid pair", pairBooking(`["t_1","t_2"]`, "2026-09-24T19:10", 2), 422, "not_on_slot_grid"},
	}
	for i, c := range cases {
		rec := e.book(e.ada, fmt.Sprintf("e%d", i), c.body)
		if rec.Code != c.status || errorCode(t, rec) != c.code {
			t.Errorf("%s: %d %s, want %d %s", c.name, rec.Code, rec.Body, c.status, c.code)
		}
	}
}

func TestPatchAndMoveWithTableIDs(t *testing.T) {
	e := newComboEnv(t)
	a := e.mustBook(e.ada, "a", "t_1", "2026-09-24T19:00", 2)
	got := decodeView(t, e.req("PATCH", "/reservations/"+a.Reference, e.ada, "", `{"table_ids":["t_2","t_1"],"party_size":6}`))
	if fmt.Sprint(got.TableIDs) != "[t_1 t_2]" || got.TableID != "" || got.PartySize != 6 {
		t.Errorf("patch to pair = %+v", got)
	}
	expect(t, e.req("PATCH", "/reservations/"+a.Reference, e.ada, "", `{"table_ids":["t_1","t_3"]}`), 422, "combination_not_allowed")
	expect(t, e.req("PATCH", "/reservations/"+a.Reference, e.ada, "", `{"table_ids":["t_1"]}`), 422, "party_exceeds_capacity")
	expect(t, e.req("PATCH", "/reservations/"+a.Reference, e.ada, "", `{"table_id":"t_1","table_ids":["t_1"]}`), 422, "validation_failed")
	expect(t, e.req("PATCH", "/reservations/"+a.Reference, e.ada, "", `{"table_ids":"t_1"}`), 400, "malformed_request")

	// Swap a pair booking and a single booking onto each other's tables in one move.
	b := e.mustBook(e.ada, "b", "t_3", "2026-09-24T19:00", 2)
	body := fmt.Sprintf(`{"moves":[{"reference":%q,"table_ids":["t_3","t_2"]},{"reference":%q,"table_id":"t_1"}]}`, a.Reference, b.Reference)
	rec := e.move(e.ada, "m", body)
	expect(t, rec, 201, "")
	views := decodeViews(t, rec)
	if fmt.Sprint(views[0].TableIDs) != "[t_3 t_2]" || views[1].TableID != "t_1" {
		t.Errorf("move result = %+v", views)
	}
	// Two resulting bookings sharing t_2 collide.
	body = fmt.Sprintf(`{"moves":[{"reference":%q,"table_ids":["t_1","t_2"]},{"reference":%q}]}`, b.Reference, a.Reference)
	expect(t, e.move(e.ada, "m2", body), 409, "table_unavailable")
	expect(t, e.move(e.ada, "m3", fmt.Sprintf(`{"moves":[{"reference":%q,"table_ids":[5]}]}`, a.Reference)), 400, "malformed_request")
}

func TestConcurrentOverlappingPairs(t *testing.T) {
	e := newComboEnv(t)
	tokens := make([]string, 50)
	for i := range tokens {
		tokens[i] = decodeSession(t, do(e.h, "POST", "/auth/signup", fmt.Sprintf(`{"email":"c%d@example.com","password":"12345678","display_name":"C"}`, i))).Token
	}
	recs := make([]*httptest.ResponseRecorder, len(tokens))
	var wg sync.WaitGroup
	for i := range tokens {
		pair := `["t_1","t_2"]`
		if i%2 == 1 {
			pair = `["t_2","t_3"]`
		}
		wg.Go(func() { recs[i] = e.book(tokens[i], "k", pairBooking(pair, "2026-09-24T19:00", 6)) })
	}
	wg.Wait()
	created := 0
	for _, rec := range recs {
		switch rec.Code {
		case 201:
			created++
		case 409:
		default:
			t.Errorf("unexpected %d %s", rec.Code, rec.Body)
		}
	}
	if created != 1 {
		t.Errorf("created %d bookings sharing t_2, want 1", created)
	}
}

func TestResetAndImportRefuseUndeclaredPair(t *testing.T) {
	e := newComboEnv(t)
	before := do(e.h, "GET", "/_test/export", "").Body.String()
	undeclared := strings.Replace(comboFixture, `["t_2", "t_1"]`, `["t_1", "t_3"]`, 1)
	expect(t, do(e.h, "POST", "/_test/reset", undeclared), 422, "validation_failed")
	if after := do(e.h, "GET", "/_test/export", "").Body.String(); after != before {
		t.Error("refused reset changed state")
	}
	bad := strings.Replace(before, `"table_ids":["t_1","t_2"]`, `"table_ids":["t_1","t_3"]`, 1)
	if bad == before {
		t.Fatal("export does not contain the seeded pair")
	}
	expect(t, do(e.h, "POST", "/_test/import", bad), 422, "validation_failed")
	if after := do(e.h, "GET", "/_test/export", "").Body.String(); after != before {
		t.Error("refused import changed state")
	}
}

// R-42: empty or over-long ids in bodies are invalid values (422) before any 404.
func TestEmptyAndLongIDsAre422(t *testing.T) {
	e := newComboEnv(t)
	long := strings.Repeat("x", 65)
	posts := []string{
		`{"restaurant_id":"","table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":2}`,
		`{"restaurant_id":"` + long + `","table_id":"t_1","starts_at_local":"2026-09-24T19:00","party_size":2}`,
		`{"restaurant_id":"r_anker","table_id":"","starts_at_local":"2026-09-24T19:00","party_size":2}`,
		`{"restaurant_id":"r_anker","table_id":"` + long + `","starts_at_local":"2026-09-24T19:00","party_size":2}`,
		pairBooking(`[""]`, "2026-09-24T19:00", 2),
		pairBooking(`["","t_1"]`, "2026-09-24T19:00", 2),
		pairBooking(`["`+long+`"]`, "2026-09-24T19:00", 2),
		pairBooking(`["","",""]`, "2026-09-24T19:00", 2),
	}
	for i, body := range posts {
		rec := e.book(e.ada, fmt.Sprintf("id%d", i), body)
		if rec.Code != 422 || errorCode(t, rec) != "validation_failed" {
			t.Errorf("POST %s = %d %s", body, rec.Code, rec.Body)
		}
	}
	expect(t, e.book(e.ada, "three", pairBooking(`["t_1","t_2","t_9"]`, "2026-09-24T19:00", 2)), 422, "combination_not_allowed")
	expect(t, e.book(e.ada, "unknown", pairBooking(`["t_9"]`, "2026-09-24T19:00", 2)), 404, "not_found")
	if rec := e.req("GET", "/reservations", e.ada, "", ""); strings.Count(rec.Body.String(), `"reference"`) != 2 {
		t.Errorf("rejected requests created bookings: %s", rec.Body) // only the two seeds
	}

	a := e.mustBook(e.ada, "a", "t_3", "2026-09-24T19:00", 2)
	before := e.req("GET", "/reservations/"+a.Reference, e.ada, "", "").Body.String()
	for i, fields := range []string{`"table_id":""`, `"table_id":"` + long + `"`, `"table_ids":[""]`, `"table_ids":["","t_1"]`, `"table_ids":["` + long + `"]`} {
		expect(t, e.req("PATCH", "/reservations/"+a.Reference, e.ada, "", "{"+fields+"}"), 422, "validation_failed")
		body := fmt.Sprintf(`{"moves":[{"reference":%q,%s}]}`, a.Reference, fields)
		expect(t, e.move(e.ada, fmt.Sprintf("mv%d", i), body), 422, "validation_failed")
	}
	expect(t, e.move(e.ada, "mv-empty-ref", `{"moves":[{"reference":""}]}`), 422, "validation_failed")
	if after := e.req("GET", "/reservations/"+a.Reference, e.ada, "", "").Body.String(); after != before {
		t.Errorf("booking changed:\n%s\n%s", before, after)
	}
}
