package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

const availabilityFixture = `{
  "users": [{"id":"u_ada","email":"ada@example.com","password":"correct horse","display_name":"Ada"}],
  "restaurants": [
    {"id":"r_anker","name":"Zum Anker","timezone":"Europe/Berlin","slot_minutes":30,
     "reservation_duration_minutes":90,"cancellation_cutoff_minutes":120,
     "opening_hours":[{"weekday":"thu","opens":"18:00","closes":"23:00"},
                      {"weekday":"fri","opens":"18:00","closes":"23:30"},
                      {"weekday":"sun","opens":"00:00","closes":"06:00"}],
     "tables":[{"id":"t_1","label":"1","capacity":2},{"id":"t_2","label":"2","capacity":4},
               {"id":"t_3","label":"3","capacity":6}]},
    {"id":"r_ny","name":"Pier","timezone":"America/New_York","slot_minutes":30,
     "reservation_duration_minutes":60,"cancellation_cutoff_minutes":0,
     "opening_hours":[{"weekday":"sun","opens":"00:00","closes":"04:00"}],
     "tables":[{"id":"t_9","label":"9","capacity":4}]}
  ],
  "reservations": [
    {"id":"res_1","reference":"SEED01","user_id":"u_ada","restaurant_id":"r_anker","table_id":"t_2",
     "starts_at_local":"2026-09-24T19:00","party_size":2},
    {"id":"res_2","reference":"SEED02","user_id":"u_ada","restaurant_id":"r_anker","table_id":"t_3",
     "starts_at_local":"2026-10-25T01:30","party_size":2}
  ]
}`

type availabilityBody struct {
	RestaurantID string `json:"restaurant_id"`
	Date         string `json:"date"`
	Timezone     string `json:"timezone"`
	Slots        []struct {
		StartsAtLocal     string   `json:"starts_at_local"`
		StartsAt          string   `json:"starts_at"`
		AvailableTableIDs []string `json:"available_table_ids"`
	} `json:"slots"`
}

func seededServer(t *testing.T) http.Handler {
	t.Helper()
	_, h := newTestServer()
	if rec := do(h, http.MethodPost, "/_test/reset", availabilityFixture); rec.Code != 204 {
		t.Fatalf("reset = %d %s", rec.Code, rec.Body)
	}
	return h
}

func getAvailability(t *testing.T, h http.Handler, query string) availabilityBody {
	t.Helper()
	rec := do(h, http.MethodGet, "/availability?"+query, "")
	if rec.Code != 200 {
		t.Fatalf("availability %s = %d %s", query, rec.Code, rec.Body)
	}
	var body availabilityBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	return body
}

func TestAvailabilityShapeAndOccupancy(t *testing.T) {
	h := seededServer(t)
	body := getAvailability(t, h, "restaurant_id=r_anker&date=2026-09-24&party_size=2")
	if body.RestaurantID != "r_anker" || body.Date != "2026-09-24" || body.Timezone != "Europe/Berlin" || len(body.Slots) != 8 {
		t.Fatalf("body = %+v", body)
	}
	if s := body.Slots[0]; s.StartsAtLocal != "2026-09-24T18:00" || s.StartsAt != "2026-09-24T18:00:00+02:00" {
		t.Errorf("first slot = %+v", s)
	}
	// t_2 is booked 19:00–20:30: slots starting 18:00 … 20:00 overlap it; 20:30 does not.
	for _, s := range body.Slots {
		has := false
		for _, id := range s.AvailableTableIDs {
			has = has || id == "t_2"
		}
		hhmm := s.StartsAtLocal[11:]
		busy := hhmm >= "18:00" && hhmm <= "20:00"
		if has == busy {
			t.Errorf("%s: t_2 offered = %v", hhmm, has)
		}
		if len(s.AvailableTableIDs) == 0 || s.AvailableTableIDs[0] != "t_1" {
			t.Errorf("%s: fixture order lost: %v", hhmm, s.AvailableTableIDs)
		}
	}
	// party_size filters by capacity; too large still lists every slot with [].
	body = getAvailability(t, h, "restaurant_id=r_anker&date=2026-09-24&party_size=5")
	if got := body.Slots[7].AvailableTableIDs; len(got) != 1 || got[0] != "t_3" {
		t.Errorf("party 5 = %v", got)
	}
	rec := do(h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-24&party_size=99999999999999999999999", "")
	if rec.Code != 200 {
		t.Fatalf("huge party = %d", rec.Code)
	}
	var huge map[string]any
	json.Unmarshal(rec.Body.Bytes(), &huge)
	slots := huge["slots"].([]any)
	if len(slots) != 8 {
		t.Fatalf("huge party slots = %d", len(slots))
	}
	for _, s := range slots {
		if ids, ok := s.(map[string]any)["available_table_ids"].([]any); !ok || len(ids) != 0 {
			t.Errorf("huge party slot = %v", s)
		}
	}
}

func TestAvailabilityClosedDay(t *testing.T) {
	h := seededServer(t)
	rec := do(h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-23&party_size=2", "")
	want := `{"restaurant_id":"r_anker","date":"2026-09-23","timezone":"Europe/Berlin","slots":[]}` + "\n"
	if rec.Code != 200 || rec.Body.String() != want {
		t.Errorf("closed day = %d %s", rec.Code, rec.Body)
	}
}

func TestAvailabilityDST(t *testing.T) {
	h := seededServer(t)
	body := getAvailability(t, h, "restaurant_id=r_anker&date=2026-03-29&party_size=2")
	for _, s := range body.Slots {
		if s.StartsAtLocal[11:13] == "02" {
			t.Errorf("Berlin skipped slot %s", s.StartsAtLocal)
		}
	}
	body = getAvailability(t, h, "restaurant_id=r_anker&date=2026-10-25&party_size=2")
	seen := 0
	for _, s := range body.Slots {
		if s.StartsAtLocal == "2026-10-25T02:30" {
			seen++
			if s.StartsAt != "2026-10-25T02:30:00+02:00" {
				t.Errorf("02:30 = %s", s.StartsAt)
			}
		}
		// Seeded t_3 01:30(+02:00) for 90 real minutes ends 02:00(+01:00): every start up to the
		// second 01:30 wall clock overlaps it, so t_3 is busy in all of 00:00 … 02:30 (first occurrences).
		hhmm := s.StartsAtLocal[11:]
		has := false
		for _, id := range s.AvailableTableIDs {
			has = has || id == "t_3"
		}
		if busy := hhmm >= "00:30" && hhmm <= "02:30"; has == busy {
			t.Errorf("%s: t_3 offered = %v", hhmm, has)
		}
	}
	if seen != 1 {
		t.Errorf("02:30 appears %d times", seen)
	}
	ny := getAvailability(t, h, "restaurant_id=r_ny&date=2026-03-08&party_size=2")
	for _, s := range ny.Slots {
		if s.StartsAtLocal[11:13] == "02" {
			t.Errorf("NY skipped slot %s", s.StartsAtLocal)
		}
	}
	ny = getAvailability(t, h, "restaurant_id=r_ny&date=2026-11-01&party_size=2")
	for _, s := range ny.Slots {
		if s.StartsAtLocal == "2026-11-01T01:30" && s.StartsAt != "2026-11-01T01:30:00-04:00" {
			t.Errorf("NY 01:30 = %s", s.StartsAt)
		}
	}
}

func TestAvailabilityParameterErrors(t *testing.T) {
	h := seededServer(t)
	cases := []struct {
		query string
		want  int
	}{
		{"date=2026-09-24&party_size=2", 422},
		{"restaurant_id=r_anker&party_size=2", 422},
		{"restaurant_id=r_anker&date=2026-09-24", 422},
		{"restaurant_id=&date=2026-09-24&party_size=2", 422},
		{"restaurant_id=r_anker&date=2026-9-24&party_size=2", 422},
		{"restaurant_id=r_anker&date=2026-02-30&party_size=2", 422},
		{"restaurant_id=r_anker&date=2026-09-24T00:00&party_size=2", 422},
		{"restaurant_id=r_anker&date=2026-09-24&party_size=0", 422},
		{"restaurant_id=r_anker&date=2026-09-24&party_size=-1", 422},
		{"restaurant_id=r_anker&date=2026-09-24&party_size=4.0", 422},
		{"restaurant_id=r_anker&date=2026-09-24&party_size=%2B4", 422},
		{"restaurant_id=r_anker&date=2026-09-24&party_size=1e9", 422},
		{"restaurant_id=r_anker&date=2026-09-24&party_size=abc", 422},
		{"restaurant_id=r_anker&date=2026-09-24&party_size=", 422},
		{"restaurant_id=r_nope&date=bad&party_size=2", 422}, // parameter errors precede 404
		{"restaurant_id=r_nope&date=2026-09-24&party_size=2", 404},
		{"restaurant_id=r_anker&date=2026-09-24&party_size=04&extra=1", 200},
	}
	for _, c := range cases {
		rec := do(h, http.MethodGet, "/availability?"+c.query, "")
		if rec.Code != c.want {
			t.Errorf("%s = %d %s, want %d", c.query, rec.Code, rec.Body, c.want)
			continue
		}
		if c.want == 422 && errorCode(t, rec) != "validation_failed" || c.want == 404 && errorCode(t, rec) != "not_found" {
			t.Errorf("%s code = %s", c.query, rec.Body)
		}
	}
	// Public: an invalid Authorization header is ignored (R-10).
	req := httptest.NewRequest(http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-24&party_size=2", nil)
	req.Header.Set("Authorization", "Bearer nonsense")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Errorf("public = %d", rec.Code)
	}
	if rec := do(h, http.MethodPost, "/availability", ""); rec.Code != 405 {
		t.Errorf("POST /availability = %d", rec.Code)
	}
}
