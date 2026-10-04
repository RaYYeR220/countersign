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
const replanFixture = `{
  "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Manager"}],
  "restaurants": [
    {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
     "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
     "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
     "tables": [{"id": "t_1", "label": "Window", "capacity": 2}, {"id": "t_2", "label": "Booth", "capacity": 4},
                {"id": "t_3", "label": "Corner", "capacity": 4}, {"id": "t_4", "label": "Long", "capacity": 6}],
     "combinable": [["t_1", "t_2"], ["t_3", "t_4"]],
     "manager_user_ids": ["u_mgr"]},
    {"id": "r_other", "name": "Other", "timezone": "Europe/Berlin", "slot_minutes": 30,
     "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 0,
     "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
     "tables": [{"id": "o_1", "label": "1", "capacity": 4}], "manager_user_ids": ["u_mgr"]}
  ]
}`

const closeT2 = `{"table_id":"t_2","from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T23:00:00+02:00"}`

func newReplanEnv(t *testing.T) *policyEnv {
	c := &clock{t: time.Date(2026, 9, 21, 11, 4, 3, 0, time.UTC)}
	e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
	resetWith(t, e.h, replanFixture)
	e.ada = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token
	mgr := decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"mgr@example.com","password":"correct horse"}`)).Token
	return &policyEnv{e, mgr}
}

type planResp struct {
	PlanID             string `json:"plan_id"`
	RestaurantRevision int    `json:"restaurant_revision"`
	Closure            struct {
		TableID string `json:"table_id"`
		From    string `json:"from"`
		To      string `json:"to"`
	}
	Assignments []state.Assignment
	MovedCount  int `json:"moved_count"`
	UnusedSeats int `json:"unused_seats"`
}

func (e *policyEnv) preview(key, body string) (*httptest.ResponseRecorder, planResp) {
	rec := e.req("POST", "/restaurants/r_anker/replans", e.mgr, key, body)
	var p planResp
	json.Unmarshal(rec.Body.Bytes(), &p)
	return rec, p
}

func (e *policyEnv) applyPlan(key, planID string) *httptest.ResponseRecorder {
	return e.req("POST", "/restaurants/r_anker/replans/"+planID+"/apply", e.mgr, key, `{}`)
}

func assignmentsString(as []state.Assignment) string {
	var parts []string
	for _, a := range as {
		parts = append(parts, fmt.Sprintf("%s=%s%s", a.Reference, strings.Join(a.TableIDs, "+"), map[bool]string{true: "*"}[a.Changed]))
	}
	return strings.Join(parts, " ")
}

func TestReplanErrors(t *testing.T) {
	e := newReplanEnv(t)
	post := func(token, key, body string) *httptest.ResponseRecorder {
		return e.req("POST", "/restaurants/r_anker/replans", token, key, body)
	}
	expect(t, post("", "k", closeT2), 401, "unauthenticated")
	expect(t, post(e.mgr, "", closeT2), 400, "missing_idempotency_key")
	expect(t, post(e.mgr, "k", `[]`), 400, "malformed_request")
	expect(t, e.req("POST", "/restaurants/nope/replans", e.mgr, "k", closeT2), 404, "not_found")
	expect(t, post(e.ada, "k", closeT2), 403, "forbidden")
	cases := []struct {
		body   string
		status int
		code   string
	}{
		{`{"table_id":5,"from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T23:00:00+02:00"}`, 400, "malformed_request"},
		{`{"table_id":null,"from":"x"}`, 400, "malformed_request"},
		{`{"from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T23:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"","from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T23:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"t_2","to":"2026-09-24T23:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"t_2","from":5,"to":"2026-09-24T23:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"t_2","from":null,"to":"2026-09-24T23:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"t_2","from":"2026-09-24T18:00:00","to":"2026-09-24T23:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"t_2","from":"2026-09-24T18:00+02:00","to":"2026-09-24T23:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"t_2","from":"2026-09-24T23:00:00+02:00","to":"2026-09-24T23:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"t_2","from":"2026-09-24T23:00:00+02:00","to":"2026-09-24T18:00:00+02:00"}`, 422, "validation_failed"},
		{`{"table_id":"t_9","from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T23:00:00+02:00"}`, 404, "not_found"},
		{`{"table_id":"o_1","from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T23:00:00+02:00"}`, 404, "not_found"},
	}
	for i, c := range cases {
		rec := post(e.mgr, fmt.Sprintf("e%d", i), c.body)
		if rec.Code != c.status || errorCode(t, rec) != c.code {
			t.Errorf("%s = %d %s, want %d %s", c.body, rec.Code, rec.Body, c.status, c.code)
		}
	}
	// Z is an explicit offset.
	rec, p := e.preview("z", `{"table_id":"t_2","from":"2026-09-24T16:00:00Z","to":"2026-09-24T21:00:00Z"}`)
	if rec.Code != 201 || p.Closure.From != "2026-09-24T18:00:00+02:00" || p.Closure.To != "2026-09-24T23:00:00+02:00" {
		t.Errorf("Z preview = %d %s", rec.Code, rec.Body)
	}
	expect(t, e.req("POST", "/restaurants/r_anker/replans/nope/apply", e.mgr, "a", `{}`), 404, "not_found")
	expect(t, e.req("POST", "/restaurants/r_other/replans/"+p.PlanID+"/apply", e.mgr, "a", `{}`), 404, "not_found")
	expect(t, e.req("POST", "/restaurants/r_anker/replans/"+p.PlanID+"/apply", e.ada, "a", `{}`), 403, "forbidden")
}

func TestPlanningLimit(t *testing.T) {
	e := newReplanEnv(t)
	for i := range 7 {
		e.mustBook(e.ada, fmt.Sprintf("b%d", i), []string{"t_1", "t_2", "t_3", "t_4"}[i%4], fmt.Sprintf("2026-09-24T%02d:00", 18+i/4*2), 1)
	}
	// 4 considered bookings are within the limit (every table is taken, so no plan fits).
	rec, _ := e.preview("p1", `{"table_id":"t_2","from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T19:00:00+02:00"}`)
	expect(t, rec, 409, "no_feasible_plan")
	rec, _ = e.preview("p2", closeT2)
	expect(t, rec, 422, "planning_limit")
}

func TestPreviewAndApply(t *testing.T) {
	e := newReplanEnv(t)
	a := e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 3)
	b := e.mustBook(e.ada, "b", "t_3", "2026-09-24T19:30", 4)
	far := e.mustBook(e.ada, "far", "t_2", "2026-10-01T19:00", 3) // not overlapping: fixed
	before := e.snapshot(e.ada)

	rec, p := e.preview("p", closeT2)
	expect(t, rec, 201, "")
	var raw map[string]any
	json.Unmarshal(rec.Body.Bytes(), &raw)
	if len(raw) != 6 {
		t.Errorf("preview has %d fields: %s", len(raw), rec.Body)
	}
	refs := []string{a.Reference, b.Reference}
	if refs[0] > refs[1] {
		refs[0], refs[1] = refs[1], refs[0]
	}
	want := map[string]string{a.Reference: a.Reference + "=t_4*", b.Reference: b.Reference + "=t_3"}
	if got := assignmentsString(p.Assignments); got != want[refs[0]]+" "+want[refs[1]] {
		t.Errorf("assignments = %s", got)
	}
	if p.MovedCount != 1 || p.UnusedSeats != 3 || p.RestaurantRevision != 3 || p.Closure.TableID != "t_2" {
		t.Errorf("plan = %+v", p)
	}
	// A preview changes nothing, and a second preview sees the same revision.
	if after := e.snapshot(e.ada); after != before {
		t.Error("preview changed bookings")
	}
	if _, p2 := e.preview("p2", closeT2); p2.RestaurantRevision != 3 || p2.PlanID == p.PlanID {
		t.Errorf("second preview = %+v", p2)
	}

	applied := e.applyPlan("ap", p.PlanID)
	expect(t, applied, 201, "")
	var res struct {
		PlanID             string `json:"plan_id"`
		RestaurantRevision int    `json:"restaurant_revision"`
		Reservations       []reservationView
	}
	json.Unmarshal(applied.Body.Bytes(), &res)
	if res.PlanID != p.PlanID || res.RestaurantRevision != 4 || len(res.Reservations) != 2 || res.Reservations[0].Reference != refs[0] {
		t.Errorf("apply = %s", applied.Body)
	}
	moved := decodeView(t, e.req("GET", "/reservations/"+a.Reference, e.ada, "", ""))
	if moved.TableID != "t_4" || moved.Revision != 2 || moved.StartsAt != a.StartsAt || moved.EndsAt != a.EndsAt ||
		moved.AcceptedTerms.PolicyVersion != a.AcceptedTerms.PolicyVersion {
		t.Errorf("moved booking = %+v", moved)
	}
	hist := e.history(a.Reference)
	last := hist.Entries[len(hist.Entries)-1]
	if last.Event != "reassigned" || last.Revision != 2 || changesString(last.Changes) != "table_ids:[t_2]>[t_4]" {
		t.Errorf("reassigned entry = %+v", last)
	}
	rawHist := e.req("GET", "/reservations/"+a.Reference+"/history", e.ada, "", "").Body.String()
	if !strings.Contains(rawHist, `"plan_id":"`+p.PlanID+`"`) {
		t.Errorf("history lacks plan_id: %s", rawHist)
	}
	if got := decodeView(t, e.req("GET", "/reservations/"+b.Reference, e.ada, "", "")); got.Revision != 1 || len(e.history(b.Reference).Entries) != 1 {
		t.Errorf("unmoved booking changed: %+v", got)
	}
	if got := decodeView(t, e.req("GET", "/reservations/"+far.Reference, e.ada, "", "")); got.TableID != "t_2" {
		t.Errorf("fixed booking moved: %+v", got)
	}

	replay := e.applyPlan("ap", p.PlanID)
	if replay.Code != 200 || replay.Body.String() != applied.Body.String() {
		t.Errorf("apply replay = %d %s", replay.Code, replay.Body)
	}
	expect(t, e.applyPlan("other", p.PlanID), 409, "plan_already_applied")

	// The closure now blocks t_2 for creates and amendments, and pairs containing it.
	expect(t, e.book(e.ada, "c1", booking("t_2", "2026-09-24T21:00", 2)), 409, "table_unavailable")
	expect(t, e.book(e.ada, "c2", pairBooking(`["t_1","t_2"]`, "2026-09-24T21:00", 5)), 409, "table_unavailable")
	expect(t, e.req("PATCH", "/reservations/"+b.Reference, e.ada, "", `{"table_id":"t_2"}`), 409, "table_unavailable")
	expect(t, e.book(e.ada, "c3", booking("t_2", "2026-10-01T21:00", 2)), 201, "") // outside the closure

	// A stage-4 export with a reassigned history, a closure and an applied plan imports cleanly.
	exported := do(e.h, "GET", "/_test/export", "").Body.String()
	fresh := New(state.NewStore(state.Empty()), e.clock.now)
	if rec := do(fresh, "POST", "/_test/import", exported); rec.Code != 204 {
		t.Errorf("stage-4 round trip = %d %s", rec.Code, rec.Body)
	}
}

func TestStalePlanAndOtherRestaurant(t *testing.T) {
	e := newReplanEnv(t)
	e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 3)
	_, p := e.preview("p", closeT2)
	// A closure applied at another restaurant does not invalidate this plan.
	rec := e.req("POST", "/restaurants/r_other/replans", e.mgr, "o", `{"table_id":"o_1","from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T19:00:00+02:00"}`)
	var other planResp
	json.Unmarshal(rec.Body.Bytes(), &other)
	expect(t, e.req("POST", "/restaurants/r_other/replans/"+other.PlanID+"/apply", e.mgr, "oa", `{}`), 201, "")
	// A new booking here does.
	e.mustBook(e.ada, "b", "t_1", "2026-09-24T21:00", 2)
	before := e.snapshot(e.ada)
	expect(t, e.applyPlan("ap", p.PlanID), 409, "stale_plan")
	if e.snapshot(e.ada) != before {
		t.Error("stale apply changed bookings")
	}
	expect(t, e.book(e.ada, "c", booking("t_2", "2026-09-24T21:00", 2)), 201, "") // no closure was recorded
}

func TestNoFeasiblePlan(t *testing.T) {
	e := newReplanEnv(t)
	expect(t, e.book(e.ada, "a", pairBooking(`["t_3","t_4"]`, "2026-09-24T19:00", 7)), 201, "") // only t_3+t_4 seats seven
	rec, _ := e.preview("p", `{"table_id":"t_4","from":"2026-09-24T18:00:00+02:00","to":"2026-09-24T23:00:00+02:00"}`)
	expect(t, rec, 409, "no_feasible_plan")
	// An empty considered set is a feasible, empty plan that still records the closure.
	rec, p := e.preview("q", `{"table_id":"t_1","from":"2026-09-24T22:30:00+02:00","to":"2026-09-24T23:00:00+02:00"}`)
	if rec.Code != 201 || len(p.Assignments) != 0 || p.MovedCount != 0 || p.UnusedSeats != 0 {
		t.Fatalf("empty plan = %d %s", rec.Code, rec.Body)
	}
	applied := e.applyPlan("qa", p.PlanID)
	if applied.Code != 201 || !strings.Contains(applied.Body.String(), `"reservations":[]`) {
		t.Errorf("empty apply = %d %s", applied.Code, applied.Body)
	}
}

func TestReplanMovesSeriesOccurrence(t *testing.T) {
	e := newReplanEnv(t)
	e.clock.set(time.Date(2026, 9, 10, 9, 0, 0, 0, time.UTC))
	anchor := e.mustBook(e.ada, "a", "t_2", "2026-09-17T19:00", 3)
	rec := e.req("POST", "/series", e.ada, "s", `{"anchor_reference":"`+anchor.Reference+`","count":2,"interval_weeks":1}`)
	expect(t, rec, 201, "")
	var series struct {
		SeriesID    string `json:"series_id"`
		Revision    int
		Occurrences []struct {
			Reference string
			Exception bool
		}
	}
	json.Unmarshal(rec.Body.Bytes(), &series)
	_, p := e.preview("p", closeT2) // occurrence 1 is 2026-09-24T19:00 on t_2
	expect(t, e.applyPlan("ap", p.PlanID), 201, "")
	json.Unmarshal(e.req("GET", "/series/"+series.SeriesID, e.ada, "", "").Body.Bytes(), &series)
	if series.Revision != 2 || series.Occurrences[1].Exception {
		t.Errorf("series after repair = %+v", series)
	}
	if got := decodeView(t, e.req("GET", "/reservations/"+series.Occurrences[1].Reference, e.ada, "", "")); got.TableID != "t_3" || got.StartsAtLocal != "2026-09-24T19:00" { // t_3 leaves 1 seat unused, t_4 three
		t.Errorf("moved occurrence = %+v", got)
	}
}

func TestConcurrentPlanApplications(t *testing.T) {
	e := newReplanEnv(t)
	e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 3)
	e.mustBook(e.ada, "b", "t_1", "2026-09-24T19:00", 2)
	var plans []string
	for i := range 5 {
		_, p := e.preview(fmt.Sprintf("p%d", i), closeT2)
		plans = append(plans, p.PlanID)
	}
	codes := make([]int, 10)
	var wg sync.WaitGroup
	for i := range codes {
		wg.Go(func() { codes[i] = e.applyPlan(fmt.Sprintf("ap%d", i), plans[i%5]).Code })
	}
	wg.Wait()
	ok := 0
	for _, c := range codes {
		if c == 201 {
			ok++
		} else if c != 409 {
			t.Errorf("unexpected status %d", c)
		}
	}
	if ok != 1 {
		t.Errorf("%d applications succeeded, want 1", ok)
	}
	var list struct{ Reservations []reservationView }
	json.Unmarshal(e.req("GET", "/reservations", e.ada, "", "").Body.Bytes(), &list)
	for _, r := range list.Reservations {
		if r.TableID == "t_2" {
			t.Errorf("a booking stayed on the closed table: %+v", r)
		}
	}
}
