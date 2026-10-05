package api

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

const maxInt31 = 1<<31 - 1

// bigFixture is the policy fixture with one restaurant field replaced by value.
func bigFixture(field string, value int64) string {
	fx := `{"users":[{"id":"u_ada","email":"ada@example.com","password":"correct horse","display_name":"Ada"}],
	 "restaurants":[{"id":"r_anker","name":"A","timezone":"Europe/Berlin","slot_minutes":30,
	  "reservation_duration_minutes":90,"cancellation_cutoff_minutes":120,
	  "opening_hours":[{"weekday":"thu","opens":"18:00","closes":"23:00"}],
	  "tables":[{"id":"t_1","label":"1","capacity":2}]}],
	 "reservations":[{"id":"res_s","reference":"SEED01","user_id":"u_ada","restaurant_id":"r_anker",
	  "table_id":"t_1","starts_at_local":"2027-06-03T18:00","party_size":1}]}`
	old := map[string]string{
		"slot_minutes": `"slot_minutes":30`, "reservation_duration_minutes": `"reservation_duration_minutes":90`,
		"cancellation_cutoff_minutes": `"cancellation_cutoff_minutes":120`, "capacity": `"capacity":2`,
		"party_size": `"party_size":1`,
	}[field]
	return strings.Replace(fx, old, fmt.Sprintf(`"%s":%d`, field, value), 1)
}

func newBigEnv(t *testing.T, fixture string) *env {
	c := &clock{t: time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)}
	e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
	resetWith(t, e.h, fixture)
	e.ada = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token
	return e
}

func slotsOn(t *testing.T, e *env, date string, party int64) []availabilitySlot {
	t.Helper()
	var resp availabilityResponse
	json.Unmarshal(do(e.h, "GET", fmt.Sprintf("/availability?restaurant_id=r_anker&date=%s&party_size=%d", date, party), "").Body.Bytes(), &resp)
	return resp.Slots
}

// R-75: fixture integers up to 2^31−1 are computed exactly; 2^31 is refused.
func TestFixtureIntegersAt2Pow31(t *testing.T) {
	for _, field := range []string{"slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes", "capacity", "party_size"} {
		e := newBigEnv(t, bigFixture("slot_minutes", 30))
		expect(t, do(e.h, "POST", "/_test/reset", bigFixture(field, maxInt31+1)), 422, "validation_failed")
		expect(t, do(e.h, "POST", "/_test/reset", bigFixture(field, maxInt31)), 204, "")
	}

	// A 2^31−1-minute duration ends 2^31−1 real minutes later: no slot fits, booking is refused.
	e := newBigEnv(t, bigFixture("reservation_duration_minutes", maxInt31))
	if s := slotsOn(t, e, "2027-06-03", 1); len(s) != 0 {
		t.Errorf("slots with a huge duration = %+v", s)
	}
	expect(t, e.book(e.ada, "d", `{"restaurant_id":"r_anker","table_id":"t_1","starts_at_local":"2027-06-10T18:00","party_size":1}`), 422, "outside_opening_hours")
	var seed reservationView
	json.Unmarshal(e.req("GET", "/reservations/SEED01", e.ada, "", "").Body.Bytes(), &seed)
	start, _ := time.Parse(time.RFC3339, seed.StartsAt)
	end, _ := time.Parse(time.RFC3339, seed.EndsAt)
	if want := time.Unix(start.Unix()+maxInt31*60, 0); !end.Equal(want) {
		t.Errorf("seeded ends_at = %s, want %s", seed.EndsAt, want)
	}

	// A huge grid leaves only the opening time on the grid.
	e = newBigEnv(t, bigFixture("slot_minutes", maxInt31))
	if s := slotsOn(t, e, "2027-06-10", 1); len(s) != 1 || s[0].StartsAtLocal != "2027-06-10T18:00" {
		t.Errorf("slots with a huge grid = %+v", s)
	}

	// A 2^31−1-minute cutoff makes every future booking non-cancellable.
	e = newBigEnv(t, bigFixture("cancellation_cutoff_minutes", maxInt31))
	v := e.mustBook(e.ada, "c", "t_1", "2027-06-10T18:00", 1)
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", ""), 409, "cutoff_passed")
	expect(t, e.req("PATCH", "/reservations/"+v.Reference, e.ada, "", `{"party_size":2}`), 409, "cutoff_passed")

	// A capacity of 2^31−1 seats a party of that size.
	e = newBigEnv(t, bigFixture("capacity", maxInt31))
	expect(t, e.book(e.ada, "p", fmt.Sprintf(`{"restaurant_id":"r_anker","table_id":"t_1","starts_at_local":"2027-06-10T18:00","party_size":%d}`, maxInt31)), 201, "")
	if s := slotsOn(t, e, "2027-06-10", maxInt31); len(s) == 0 || len(s[len(s)-1].AvailableTableIDs) != 1 { // the last slot is after the booking
		t.Errorf("availability for a huge party = %+v", s)
	}

	// Such a state survives export and import.
	exported := do(e.h, "GET", "/_test/export", "").Body.String()
	if rec := do(New(state.NewStore(state.Empty()), e.clock.now), "POST", "/_test/import", exported); rec.Code != 204 {
		t.Errorf("round trip of huge fixture values = %d %s", rec.Code, rec.Body)
	}
}
