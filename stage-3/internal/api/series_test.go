package api

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

// Thursdays: 2026-10-01, 10-08, 10-15, 10-22, 10-29. Berlin falls back on Sunday 2026-10-25 and springs
// forward on Sunday 2027-03-28. The clock stands at 2026-09-21.
const seriesFixture = `{
  "users": [
    {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
    {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"},
    {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Manager"}
  ],
  "restaurants": [
    {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
     "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120, "manager_user_ids": ["u_mgr"],
     "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"},
                       {"weekday": "sun", "opens": "00:00", "closes": "06:00"}],
     "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4},
                {"id": "t_3", "label": "3", "capacity": 4}],
     "combinable": [["t_1", "t_2"]]}
  ],
  "reservations": []
}`

type seriesBody struct {
	SeriesID      string `json:"series_id"`
	Revision      int    `json:"revision"`
	IntervalWeeks int    `json:"interval_weeks"`
	Occurrences   []struct {
		Index       int             `json:"index"`
		Reference   string          `json:"reference"`
		Exception   bool            `json:"exception"`
		Reservation json.RawMessage `json:"reservation"`
	} `json:"occurrences"`
}

type seriesRes struct {
	Reference     string   `json:"reference"`
	Status        string   `json:"status"`
	TableIDs      []string `json:"table_ids"`
	PartySize     int      `json:"party_size"`
	StartsAtLocal string   `json:"starts_at_local"`
	StartsAt      string   `json:"starts_at"`
	EndsAt        string   `json:"ends_at"`
	Revision      int      `json:"revision"`
	AcceptedTerms struct {
		PolicyVersion              int `json:"policy_version"`
		ReservationDurationMinutes int `json:"reservation_duration_minutes"`
	} `json:"accepted_terms"`
}

func newSeriesEnv(t *testing.T) (*env, string) {
	e := newEnv(t)
	resetWith(t, e.h, seriesFixture)
	e.ada = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token
	e.bob = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"bob@example.com","password":"correct horse"}`)).Token
	mgr := decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"mgr@example.com","password":"correct horse"}`)).Token
	return e, mgr
}

func (e *env) adopt(token, key, ref string, count, weeks int) *httptest.ResponseRecorder {
	return e.req("POST", "/series", token, key, fmt.Sprintf(`{"anchor_reference":%q,"count":%d,"interval_weeks":%d}`, ref, count, weeks))
}

func decodeSeries(t *testing.T, rec *httptest.ResponseRecorder) seriesBody {
	t.Helper()
	var s seriesBody
	if err := json.Unmarshal(rec.Body.Bytes(), &s); err != nil || s.SeriesID == "" {
		t.Fatalf("not a series: %d %s", rec.Code, rec.Body)
	}
	return s
}

func occ(t *testing.T, s seriesBody, i int) seriesRes {
	t.Helper()
	var r seriesRes
	if err := json.Unmarshal(s.Occurrences[i].Reservation, &r); err != nil {
		t.Fatal(err)
	}
	return r
}

func (e *env) count(token string) int {
	var list struct{ Reservations []json.RawMessage }
	json.Unmarshal(e.req("GET", "/reservations", token, "", "").Body.Bytes(), &list)
	return len(list.Reservations)
}

func TestSeriesAdoption(t *testing.T) {
	e, _ := newSeriesEnv(t)
	created := e.book(e.ada, "k-anchor", booking("t_2", "2026-10-01T19:00", 3))
	expect(t, created, 201, "")
	anchor := decodeView(t, created)
	before := e.req("GET", "/reservations/"+anchor.Reference, e.ada, "", "").Body.String()
	histBefore := e.req("GET", "/reservations/"+anchor.Reference+"/history", e.ada, "", "").Body.String()

	rec := e.adopt(e.ada, "k-s1", anchor.Reference, 3, 2)
	expect(t, rec, 201, "")
	s := decodeSeries(t, rec)
	if s.Revision != 1 || s.IntervalWeeks != 2 || len(s.Occurrences) != 3 || len(s.SeriesID) > 64 {
		t.Fatalf("series = %+v", s)
	}
	wantDates := []string{"2026-10-01T19:00", "2026-10-15T19:00", "2026-10-29T19:00"}
	refs := map[string]bool{}
	for i, o := range s.Occurrences {
		r := occ(t, s, i)
		if o.Index != i || o.Exception || o.Reference != r.Reference || r.StartsAtLocal != wantDates[i] ||
			r.PartySize != 3 || strings.Join(r.TableIDs, ",") != "t_2" || r.Status != "confirmed" || r.Revision != 1 {
			t.Errorf("occurrence %d = %+v %+v", i, o, r)
		}
		refs[o.Reference] = true
	}
	if len(refs) != 3 || s.Occurrences[0].Reference != anchor.Reference {
		t.Errorf("references = %v", refs)
	}
	// 2026-10-29 is after the fall-back: +01:00; 90 absolute minutes.
	if r := occ(t, s, 2); r.StartsAt != "2026-10-29T19:00:00+01:00" || r.EndsAt != "2026-10-29T20:30:00+01:00" {
		t.Errorf("occurrence 2 times = %s %s", r.StartsAt, r.EndsAt)
	}
	// Occurrence zero is untouched: same reservation response and history.
	if got := e.req("GET", "/reservations/"+anchor.Reference, e.ada, "", "").Body.String(); got != before {
		t.Errorf("anchor changed:\n%s\n%s", before, got)
	}
	if got := e.req("GET", "/reservations/"+anchor.Reference+"/history", e.ada, "", "").Body.String(); got != histBefore {
		t.Errorf("anchor history changed")
	}
	if rec := e.book(e.ada, "k-anchor", booking("t_2", "2026-10-01T19:00", 3)); rec.Code != 200 || rec.Body.String() != created.Body.String() {
		t.Errorf("anchor replay changed: %d", rec.Code)
	}
	// Generated occurrences: in lists, with a created history entry, occupying their table.
	if n := e.count(e.ada); n != 3 {
		t.Errorf("ada has %d reservations", n)
	}
	var hist struct {
		Entries []struct {
			Seq      int    `json:"seq"`
			Event    string `json:"event"`
			Revision int    `json:"revision"`
		}
	}
	json.Unmarshal(e.req("GET", "/reservations/"+s.Occurrences[1].Reference+"/history", e.ada, "", "").Body.Bytes(), &hist)
	if len(hist.Entries) != 1 || hist.Entries[0].Event != "created" || hist.Entries[0].Seq != 1 || hist.Entries[0].Revision != 1 {
		t.Errorf("occurrence history = %+v", hist)
	}
	expect(t, e.book(e.bob, "k-clash", booking("t_2", "2026-10-15T19:30", 2)), 409, "table_unavailable")

	// GET: owner only; everyone else 404.
	got := e.req("GET", "/series/"+s.SeriesID, e.ada, "", "")
	if got.Code != 200 || strings.TrimSpace(got.Body.String()) != strings.TrimSpace(rec.Body.String()) {
		t.Errorf("GET series = %d\n%s\n%s", got.Code, got.Body, rec.Body)
	}
	for _, tok := range []string{e.bob, "", "nonsense"} {
		expect(t, e.req("GET", "/series/"+s.SeriesID, tok, "", ""), 404, "not_found")
	}
	expect(t, e.req("GET", "/series/ser_unknown", e.ada, "", ""), 404, "not_found")

	// Replay: original response, no new state; reuse with another body: 409.
	if again := e.adopt(e.ada, "k-s1", anchor.Reference, 3, 2); again.Code != 200 || again.Body.String() != rec.Body.String() {
		t.Errorf("replay = %d %s", again.Code, again.Body)
	}
	expect(t, e.adopt(e.ada, "k-s1", anchor.Reference, 4, 2), 409, "idempotency_key_reuse")
	if n := e.count(e.ada); n != 3 {
		t.Errorf("replay created bookings: %d", n)
	}
	// Anchor and generated occurrences are already in a series.
	expect(t, e.adopt(e.ada, "k-s2", anchor.Reference, 2, 1), 409, "already_in_series")
	expect(t, e.adopt(e.ada, "k-s3", s.Occurrences[1].Reference, 2, 1), 409, "already_in_series")
}

func TestSeriesErrors(t *testing.T) {
	e, _ := newSeriesEnv(t)
	anchor := e.mustBook(e.ada, "k-a", "t_2", "2026-10-01T19:00", 2)
	ref := anchor.Reference
	body := func(s string) string { return s }
	cases := []struct {
		token, body string
		status      int
		code        string
	}{
		{"", fmt.Sprintf(`{"anchor_reference":%q,"count":2,"interval_weeks":1}`, ref), 401, "unauthenticated"},
		{e.ada, `[1]`, 400, "malformed_request"},
		{e.ada, `{"anchor_reference":5,"count":2,"interval_weeks":1}`, 400, "malformed_request"},
		{e.ada, `{"anchor_reference":null,"count":2,"interval_weeks":1}`, 400, "malformed_request"},
		{e.ada, `{"anchor_reference":5}`, 400, "malformed_request"}, // types before missing
		{e.ada, `{"count":2,"interval_weeks":1}`, 422, "validation_failed"},
		{e.ada, body(fmt.Sprintf(`{"anchor_reference":%q,"interval_weeks":1}`, ref)), 422, "validation_failed"},
		{e.ada, body(fmt.Sprintf(`{"anchor_reference":%q,"count":2}`, ref)), 422, "validation_failed"},
		{e.ada, `{"count":"2","interval_weeks":1}`, 422, "validation_failed"}, // count type (422) before missing anchor
	}
	for _, v := range []string{`true`, `"3"`, `null`, `1`, `13`, `2.5`, `-2`, `[]`} {
		cases = append(cases, struct {
			token, body string
			status      int
			code        string
		}{e.ada, fmt.Sprintf(`{"anchor_reference":%q,"count":%s,"interval_weeks":1}`, ref, v), 422, "validation_failed"})
		cases = append(cases, struct {
			token, body string
			status      int
			code        string
		}{e.ada, fmt.Sprintf(`{"anchor_reference":%q,"count":2,"interval_weeks":%s}`, ref, strings.Replace(strings.Replace(v, "13", "5", 1), "1", "0", 1)), 422, "validation_failed"})
	}
	cases = append(cases,
		struct {
			token, body string
			status      int
			code        string
		}{e.ada, `{"anchor_reference":"NOPE0000","count":99,"interval_weeks":1}`, 422, "validation_failed"}, // ranges before 404
		struct {
			token, body string
			status      int
			code        string
		}{e.ada, `{"anchor_reference":"NOPE0000","count":2,"interval_weeks":1}`, 404, "not_found"},
		struct {
			token, body string
			status      int
			code        string
		}{e.bob, fmt.Sprintf(`{"anchor_reference":%q,"count":2,"interval_weeks":1}`, ref), 404, "not_found"},
	)
	for i, c := range cases {
		rec := e.req("POST", "/series", c.token, fmt.Sprintf("k-err-%d", i), c.body)
		expect(t, rec, c.status, c.code)
		if t.Failed() {
			t.Fatalf("case %d: %s", i, c.body)
		}
	}
	expect(t, e.req("POST", "/series", e.ada, "", fmt.Sprintf(`{"anchor_reference":%q,"count":2,"interval_weeks":1}`, ref)), 400, "missing_idempotency_key")
	// Integral 3.0 is an integer (R-3); unknown fields are ignored.
	ok := e.req("POST", "/series", e.ada, "k-ok", fmt.Sprintf(`{"anchor_reference":%q,"count":3.0,"interval_weeks":1,"color":"red"}`, ref))
	expect(t, ok, 201, "")

	// Cancelled anchor; cutoff.
	other := e.mustBook(e.ada, "k-b", "t_3", "2026-10-01T19:00", 2)
	expect(t, e.req("POST", "/reservations/"+other.Reference+"/cancel", e.ada, "", ""), 200, "")
	expect(t, e.adopt(e.ada, "k-c", other.Reference, 2, 1), 409, "reservation_cancelled")
	soon := e.mustBook(e.ada, "k-d", "t_1", "2026-10-01T21:30", 2)
	e.clock.set(time.Date(2026, 10, 1, 18, 30, 0, 0, time.UTC)) // 20:30 Berlin: within 120 min of 21:30
	expect(t, e.adopt(e.ada, "k-e", soon.Reference, 2, 1), 409, "cutoff_passed")
}

func TestSeriesAllOrNothing(t *testing.T) {
	e, _ := newSeriesEnv(t)
	anchor := e.mustBook(e.ada, "k-a", "t_2", "2026-10-01T19:00", 2)
	// Bob holds t_2 on the third occurrence (index 2).
	blocker := e.mustBook(e.bob, "k-b", "t_2", "2026-10-15T20:00", 2)
	beforeAda, beforeBob := e.count(e.ada), e.count(e.bob)
	exportBefore := do(e.h, "GET", "/_test/export", "").Body.String()

	expect(t, e.adopt(e.ada, "k-s", anchor.Reference, 4, 1), 409, "table_unavailable")
	if e.count(e.ada) != beforeAda || e.count(e.bob) != beforeBob {
		t.Fatalf("partial adoption left bookings")
	}
	if got := do(e.h, "GET", "/_test/export", "").Body.String(); got != exportBefore {
		t.Fatalf("failed adoption changed state")
	}
	// The failed key is reusable once the clash is gone.
	expect(t, e.req("POST", "/reservations/"+blocker.Reference+"/cancel", e.bob, "", ""), 200, "")
	expect(t, e.adopt(e.ada, "k-s", anchor.Reference, 4, 1), 201, "")
}

func TestSeriesFirstFailingIndexAndPolicies(t *testing.T) {
	e, mgr := newSeriesEnv(t)
	anchor := e.mustBook(e.ada, "k-a", "t_2", "2026-10-01T19:00", 4)
	// From 2026-10-15, Thursdays close at 20:00 (index 2 → outside_opening_hours);
	// index 1 (2026-10-08) is taken by Bob (table_unavailable). Index 1 decides.
	policy := `{"effective_from":"2026-10-15","slot_minutes":30,"reservation_duration_minutes":120,
	  "cancellation_cutoff_minutes":60,"opening_hours":[{"weekday":"thu","opens":"18:00","closes":"20:00"}],
	  "capacities":{"t_1":2,"t_2":4,"t_3":4}}`
	expect(t, e.req("POST", "/restaurants/r_anker/policies", mgr, "p1", policy), 201, "")
	blocker := e.mustBook(e.bob, "k-b", "t_2", "2026-10-08T19:00", 2)
	expect(t, e.adopt(e.ada, "k-s", anchor.Reference, 3, 1), 409, "table_unavailable")
	expect(t, e.req("POST", "/reservations/"+blocker.Reference+"/cancel", e.bob, "", ""), 200, "")
	expect(t, e.adopt(e.ada, "k-s", anchor.Reference, 3, 1), 422, "outside_opening_hours")

	// From 2026-10-22: open late, duration 120, t_2 seats only 2 (index 3 → party_exceeds_capacity).
	policy2 := `{"effective_from":"2026-10-22","slot_minutes":30,"reservation_duration_minutes":120,
	  "cancellation_cutoff_minutes":60,"opening_hours":[{"weekday":"thu","opens":"18:00","closes":"23:00"}],
	  "capacities":{"t_1":2,"t_2":2,"t_3":4}}`
	expect(t, e.req("POST", "/restaurants/r_anker/policies", mgr, "p2", policy2), 201, "")
	expect(t, e.adopt(e.ada, "k-s2", anchor.Reference, 2, 3), 422, "party_exceeds_capacity") // index 1 = 10-22

	// Each occurrence carries its own date's policy: a 2-week series lands 10-01 (policy 0) and 10-29 (policy 2 by date
	// would refuse party 4), so use party-4-compatible t_3 via a new anchor.
	anchor3 := e.mustBook(e.ada, "k-c", "t_3", "2026-10-01T20:00", 4)
	rec := e.adopt(e.ada, "k-s3", anchor3.Reference, 2, 4) // 10-01 → 10-29
	expect(t, rec, 201, "")
	s := decodeSeries(t, rec)
	if r := occ(t, s, 1); r.AcceptedTerms.PolicyVersion != 2 || r.AcceptedTerms.ReservationDurationMinutes != 120 ||
		r.EndsAt != "2026-10-29T22:00:00+01:00" {
		t.Errorf("occurrence under policy 2 = %+v", r)
	}
	if r := occ(t, s, 0); r.AcceptedTerms.PolicyVersion != 0 {
		t.Errorf("anchor terms changed: %+v", r)
	}
}

func TestSeriesDST(t *testing.T) {
	e, _ := newSeriesEnv(t)
	// Fall back: 2026-10-18 02:30 → 2026-10-25 02:30 is repeated → first occurrence (+02:00).
	fall := e.mustBook(e.ada, "k-f", "t_1", "2026-10-18T02:30", 2)
	rec := e.adopt(e.ada, "k-sf", fall.Reference, 2, 1)
	expect(t, rec, 201, "")
	if r := occ(t, decodeSeries(t, rec), 1); r.StartsAt != "2026-10-25T02:30:00+02:00" || r.EndsAt != "2026-10-25T03:00:00+01:00" {
		t.Errorf("fall-back occurrence = %s %s", r.StartsAt, r.EndsAt)
	}
	// Spring forward: 2027-03-21 02:30 → 2027-03-28 02:30 does not exist → whole adoption refused.
	spring := e.mustBook(e.ada, "k-s", "t_1", "2027-03-21T02:30", 2)
	before := e.count(e.ada)
	expect(t, e.adopt(e.ada, "k-ss", spring.Reference, 3, 1), 422, "invalid_local_time")
	if e.count(e.ada) != before {
		t.Errorf("spring-forward adoption left bookings")
	}
}

func TestSeriesExceptionsAndRevision(t *testing.T) {
	e, _ := newSeriesEnv(t)
	anchor := e.mustBook(e.ada, "k-a", "t_2", "2026-10-01T19:00", 2)
	rec := e.adopt(e.ada, "k-s", anchor.Reference, 3, 1)
	expect(t, rec, 201, "")
	s := decodeSeries(t, rec)
	get := func() seriesBody { return decodeSeries(t, e.req("GET", "/series/"+s.SeriesID, e.ada, "", "")) }
	o1, o2 := s.Occurrences[1].Reference, s.Occurrences[2].Reference

	expect(t, e.req("PATCH", "/reservations/"+o1, e.ada, "", `{"party_size":2}`), 200, "") // no-op
	if g := get(); g.Revision != 1 || g.Occurrences[1].Exception {
		t.Errorf("no-op changed series: %+v", g)
	}
	expect(t, e.req("PATCH", "/reservations/"+o1, e.ada, "", `{"party_size":9}`), 422, "party_exceeds_capacity")
	if g := get(); g.Revision != 1 || g.Occurrences[1].Exception {
		t.Errorf("failure changed series: %+v", g)
	}
	expect(t, e.req("PATCH", "/reservations/"+o1, e.ada, "", `{"party_size":3}`), 200, "")
	g := get()
	if g.Revision != 2 || !g.Occurrences[1].Exception || g.Occurrences[1].Reference != o1 {
		t.Errorf("real change: %+v", g)
	}
	expect(t, e.req("POST", "/reservations/"+o2+"/cancel", e.ada, "", ""), 200, "")
	expect(t, e.req("POST", "/reservations/"+o2+"/cancel", e.ada, "", ""), 200, "")
	g = get()
	if g.Revision != 3 || g.Occurrences[2].Exception || occ(t, g, 2).Status != "cancelled" || len(g.Occurrences) != 3 {
		t.Errorf("cancel: %+v", g)
	}
	// Cancelling the anchor leaves its siblings.
	expect(t, e.req("POST", "/reservations/"+anchor.Reference+"/cancel", e.ada, "", ""), 200, "")
	if g = get(); occ(t, g, 1).Status != "confirmed" || g.Revision != 4 {
		t.Errorf("anchor cancel: %+v", g)
	}
	// The original response is replayed unchanged after all of this.
	if again := e.adopt(e.ada, "k-s", anchor.Reference, 3, 1); again.Code != 200 || again.Body.String() != rec.Body.String() {
		t.Errorf("replay after changes = %d", again.Code)
	}
}

func TestSeriesSurvivesExportImport(t *testing.T) {
	e, _ := newSeriesEnv(t)
	anchor := e.mustBook(e.ada, "k-a", "t_2", "2026-10-01T19:00", 2)
	rec := e.adopt(e.ada, "k-s", anchor.Reference, 2, 1)
	expect(t, rec, 201, "")
	s := decodeSeries(t, rec)
	exported := do(e.h, "GET", "/_test/export", "")
	fresh := &env{t: t, h: New(state.NewStore(state.Empty()), e.clock.now), clock: e.clock, ada: e.ada}
	expect(t, do(fresh.h, "POST", "/_test/import", exported.Body.String()), 204, "")
	if got := fresh.req("GET", "/series/"+s.SeriesID, e.ada, "", ""); got.Code != 200 || strings.TrimSpace(got.Body.String()) != strings.TrimSpace(rec.Body.String()) {
		t.Errorf("series after import = %d %s", got.Code, got.Body)
	}
	if again := fresh.adopt(e.ada, "k-s", anchor.Reference, 2, 1); again.Code != 200 || again.Body.String() != rec.Body.String() {
		t.Errorf("replay after import = %d", again.Code)
	}
	expect(t, fresh.adopt(e.ada, "k-s2", anchor.Reference, 2, 1), 409, "already_in_series")
}

// Imported stage-1 bookings can be adopted.
func TestSeriesOnImportedStage1Booking(t *testing.T) {
	e, _ := newSeriesEnv(t)
	resetWith(t, e.h, bookingFixture) // a stage-1-shaped fixture is also a valid stage-3 fixture
	e.ada = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token
	e.bob = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"bob@example.com","password":"correct horse"}`)).Token
	expect(t, e.adopt(e.bob, "k", "SEED01", 2, 1), 201, "") // seeded booking, revision 1 under policy 0
}

// Adoption works on bookings imported from stage-1 and stage-2 exports (C3.48, R-55).
func TestSeriesOnImportedExports(t *testing.T) {
	for _, tc := range []struct{ file, anchor string }{
		{"stage1-export.json", "FDNDYW3N"}, // 2027-01-07 21:00, schema 1
		{"stage2-export.json", "PFOHNWE4"}, // 2027-01-14 19:00, schema 2
	} {
		raw, err := os.ReadFile("../snapshot/testdata/" + tc.file)
		if err != nil {
			t.Fatal(err)
		}
		var export struct{ State struct{ Tokens map[string]string } }
		json.Unmarshal(raw, &export)
		var token string
		for tok := range export.State.Tokens {
			token = tok
		}
		c := &clock{t: time.Date(2026, 10, 4, 18, 0, 0, 0, time.UTC)}
		e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
		expect(t, do(e.h, "POST", "/_test/import", string(raw)), 204, "")
		before := e.req("GET", "/reservations/"+tc.anchor, token, "", "").Body.String()
		hist := e.req("GET", "/reservations/"+tc.anchor+"/history", token, "", "").Body.String()

		rec := e.adopt(token, "k-up", tc.anchor, 3, 1)
		expect(t, rec, 201, "")
		if t.Failed() {
			t.Fatalf("%s: adoption failed", tc.file)
		}
		s := decodeSeries(t, rec)
		if a := occ(t, s, 0); a.Reference != tc.anchor || a.Revision != 1 || a.AcceptedTerms.PolicyVersion != 0 || len(s.Occurrences) != 3 {
			t.Errorf("%s: anchor occurrence = %+v", tc.file, a)
		}
		if got := e.req("GET", "/reservations/"+tc.anchor, token, "", "").Body.String(); got != before {
			t.Errorf("%s: imported anchor changed", tc.file)
		}
		if got := e.req("GET", "/reservations/"+tc.anchor+"/history", token, "", "").Body.String(); got != hist {
			t.Errorf("%s: imported anchor history changed", tc.file)
		}
		expect(t, e.adopt(token, "k-cancelled", "SEED01", 2, 1), 409, "reservation_cancelled")
	}
}

// R-60: an empty or over-long anchor_reference is an invalid value (422, value pass, first field);
// a well-sized unknown reference is 404. Nothing changes and the key stays unclaimed.
func TestSeriesAnchorReferenceValue(t *testing.T) {
	e, _ := newSeriesEnv(t)
	anchor := e.mustBook(e.ada, "k-a", "t_2", "2026-10-01T19:00", 2)
	before := do(e.h, "GET", "/_test/export", "").Body.String()
	long65, long64 := strings.Repeat("A", 65), strings.Repeat("A", 64)
	for _, c := range []struct {
		body   string
		status int
		code   string
	}{
		{`{"anchor_reference":"","count":3,"interval_weeks":2}`, 422, "validation_failed"},
		{`{"anchor_reference":"` + long65 + `","count":3,"interval_weeks":2}`, 422, "validation_failed"},
		{`{"anchor_reference":"","count":99,"interval_weeks":2}`, 422, "validation_failed"},
		{`{"anchor_reference":"` + long64 + `","count":3,"interval_weeks":2}`, 404, "not_found"},
		{`{"anchor_reference":"abc","count":3,"interval_weeks":2}`, 404, "not_found"},
	} {
		expect(t, e.req("POST", "/series", e.ada, "k-r60", c.body), c.status, c.code)
		if got := do(e.h, "GET", "/_test/export", "").Body.String(); got != before {
			t.Fatalf("%s changed state", c.body)
		}
	}
	// The key was never claimed: it now adopts the real anchor.
	expect(t, e.adopt(e.ada, "k-r60", anchor.Reference, 2, 1), 201, "")
}
