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

// 2026-09-24 and 2026-10-01 are Thursdays.
const policyFixture = `{
  "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Manager"}],
  "restaurants": [
    {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
     "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
     "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
     "tables": [{"id": "t_1", "label": "Window", "capacity": 2}, {"id": "t_2", "label": "Booth", "capacity": 4}],
     "combinable": [["t_1", "t_2"]],
     "manager_user_ids": ["u_mgr"]}
  ]
}`

type policyEnv struct {
	*env
	mgr string
}

func newPolicyEnv(t *testing.T) *policyEnv {
	c := &clock{t: time.Date(2026, 9, 21, 11, 4, 3, 0, time.UTC)}
	e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
	resetWith(t, e.h, policyFixture)
	e.ada = decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`)).Token
	mgr := decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"mgr@example.com","password":"correct horse"}`)).Token
	return &policyEnv{e, mgr}
}

func policyBody(from string, duration, cutoff int, t2 int) string {
	return fmt.Sprintf(`{"effective_from":%q,"slot_minutes":30,"reservation_duration_minutes":%d,
		"cancellation_cutoff_minutes":%d,"opening_hours":[{"weekday":"thu","opens":"17:00","closes":"23:30"}],
		"capacities":{"t_1":2,"t_2":%d}}`, from, duration, cutoff, t2)
}

func (e *policyEnv) publish(token, key, body string) *httptest.ResponseRecorder {
	return e.req("POST", "/restaurants/r_anker/policies", token, key, body)
}

type historyView struct {
	Reference string
	Entries   []struct {
		Seq           int
		At            string
		Event         string
		Changes       []state.FieldChange
		Revision      int
		AcceptedTerms state.Terms `json:"accepted_terms"`
	}
}

func (e *policyEnv) history(ref string) historyView {
	e.t.Helper()
	rec := e.req("GET", "/reservations/"+ref+"/history", e.ada, "", "")
	if rec.Code != 200 {
		e.t.Fatalf("history = %d %s", rec.Code, rec.Body)
	}
	var h historyView
	json.Unmarshal(rec.Body.Bytes(), &h)
	return h
}

func changesString(cs []state.FieldChange) string {
	var parts []string
	for _, c := range cs {
		parts = append(parts, fmt.Sprintf("%s:%v>%v", c.Field, c.From, c.To))
	}
	return strings.Join(parts, " ")
}

func TestPublishPolicies(t *testing.T) {
	e := newPolicyEnv(t)
	body := policyBody("2026-10-01", 120, 60, 6)
	expect(t, e.publish("", "p", body), 401, "unauthenticated")
	expect(t, e.publish(e.ada, "p", body), 403, "forbidden")
	expect(t, e.req("POST", "/restaurants/nope/policies", e.mgr, "p", body), 404, "not_found")
	expect(t, e.publish(e.mgr, "", body), 400, "missing_idempotency_key")

	invalid := []string{
		strings.Replace(body, `"2026-10-01"`, `"2026-02-30"`, 1),
		strings.Replace(body, `"slot_minutes":30`, `"slot_minutes":0`, 1),
		strings.Replace(body, `"slot_minutes":30`, `"slot_minutes":true`, 1),
		strings.Replace(body, `"slot_minutes":30`, `"slot_minutes":"30"`, 1),
		strings.Replace(body, `"reservation_duration_minutes":120`, `"reservation_duration_minutes":1441`, 1),
		strings.Replace(body, `"cancellation_cutoff_minutes":60`, `"cancellation_cutoff_minutes":10081`, 1),
		strings.Replace(body, `"cancellation_cutoff_minutes":60,`, ``, 1),
		strings.Replace(body, `"t_2":6`, `"t_2":101`, 1),
		strings.Replace(body, `"t_2":6`, `"t_2":6,"t_9":2`, 1),
		strings.Replace(body, `,"t_2":6`, ``, 1),
		strings.Replace(body, `[{"weekday":"thu","opens":"17:00","closes":"23:30"}]`, `[{"weekday":"thu","opens":"17:00","closes":"23:30"},{"weekday":"thu","opens":"10:00","closes":"12:00"}]`, 1),
		strings.Replace(body, `"closes":"23:30"`, `"closes":"16:00"`, 1),
		`{"effective_from":"2026-10-01"}`,
		strings.Replace(body, `"capacities":{"t_1":2,"t_2":6}`, `"capacities":[2,6]`, 1),
	}
	for i, b := range invalid {
		expect(t, e.publish(e.mgr, fmt.Sprintf("bad%d", i), b), 422, "validation_failed")
	}

	rec := e.publish(e.mgr, "p1", body+"")
	expect(t, rec, 201, "")
	var got map[string]any
	json.Unmarshal(rec.Body.Bytes(), &got)
	if got["policy_version"] != float64(1) || got["effective_from"] != "2026-10-01" || got["reservation_duration_minutes"] != float64(120) {
		t.Errorf("published = %s", rec.Body)
	}
	replay := e.publish(e.mgr, "p1", body)
	if replay.Code != 200 || replay.Body.String() != rec.Body.String() {
		t.Errorf("replay = %d %s", replay.Code, replay.Body)
	}
	rec = e.publish(e.mgr, "p2", policyBody("2026-09-01", 60, 0, 4))
	json.Unmarshal(rec.Body.Bytes(), &got)
	if got["policy_version"] != float64(2) {
		t.Errorf("second version = %s", rec.Body)
	}
	var list struct{ Policies []map[string]any }
	json.Unmarshal(do(e.h, "GET", "/restaurants/r_anker/policies", "").Body.Bytes(), &list)
	if len(list.Policies) != 2 || list.Policies[0]["policy_version"] != float64(1) || list.Policies[1]["effective_from"] != "2026-09-01" {
		t.Errorf("list = %+v", list.Policies)
	}
	expect(t, do(e.h, "GET", "/restaurants/nope/policies", ""), 404, "not_found")
	// The restaurant detail still shows the fixture configuration.
	var detail map[string]any
	json.Unmarshal(do(e.h, "GET", "/restaurants/r_anker", "").Body.Bytes(), &detail)
	if detail["reservation_duration_minutes"] != float64(90) || detail["policies"] != nil || detail["revision"] != nil {
		t.Errorf("detail = %v", detail)
	}
}

func TestPolicySelectionAndTerms(t *testing.T) {
	e := newPolicyEnv(t)
	before := e.mustBook(e.ada, "early", "t_1", "2026-10-01T19:00", 2) // policy 0, 90 min
	expect(t, e.publish(e.mgr, "p1", policyBody("2026-10-01", 120, 60, 6)), 201, "")
	expect(t, e.publish(e.mgr, "p2", policyBody("2026-10-01", 150, 30, 6)), 201, "") // same date, newer wins

	v := e.mustBook(e.ada, "a", "t_2", "2026-10-01T17:00", 6) // 17:00 and party 6 only under the policy
	if v.AcceptedTerms.PolicyVersion != 2 || v.EndsAt != "2026-10-01T19:30:00+02:00" || v.AcceptedTerms.Capacities["t_2"] != 6 {
		t.Errorf("booking under policy = %+v", v)
	}
	if old := e.mustBook(e.ada, "b", "t_2", "2026-09-24T19:00", 4); old.AcceptedTerms.PolicyVersion != 0 || old.EndsAt != "2026-09-24T20:30:00+02:00" {
		t.Errorf("booking before the policy = %+v", old)
	}
	expect(t, e.book(e.ada, "c", booking("t_2", "2026-09-24T17:00", 2)), 422, "outside_opening_hours")
	// Publishing changed neither the earlier booking nor its history.
	if got := decodeView(t, e.req("GET", "/reservations/"+before.Reference, e.ada, "", "")); got.EndsAt != before.EndsAt ||
		got.AcceptedTerms.PolicyVersion != 0 || got.Revision != 1 || len(e.history(before.Reference).Entries) != 1 {
		t.Errorf("published policy touched an accepted booking: %+v", got)
	}
}

func TestRevisionsAndHistory(t *testing.T) {
	e := newPolicyEnv(t)
	created := e.book(e.ada, "k", booking("t_1", "2026-09-24T19:00", 2))
	v := decodeView(t, created)
	if v.Revision != 1 {
		t.Fatalf("revision = %d", v.Revision)
	}
	// A replay records nothing.
	expect(t, e.book(e.ada, "k", booking("t_1", "2026-09-24T19:00", 2)), 200, "")
	patch := func(body string) *httptest.ResponseRecorder {
		return e.req("PATCH", "/reservations/"+v.Reference, e.ada, "", body)
	}
	// No-op: same values, same set → 200, no revision, no history.
	if got := decodeView(t, patch(`{"table_ids":["t_1"],"party_size":2,"starts_at_local":"2026-09-24T19:00"}`)); got.Revision != 1 {
		t.Errorf("no-op revision = %d", got.Revision)
	}
	if got := decodeView(t, patch(`{"party_size":1}`)); got.Revision != 2 {
		t.Errorf("real change revision = %d", got.Revision)
	}
	if got := decodeView(t, patch(`{"table_ids":["t_2","t_1"],"party_size":5}`)); got.Revision != 3 {
		t.Errorf("pair change revision = %d", got.Revision)
	}
	if got := decodeView(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", "")); got.Revision != 4 {
		t.Errorf("cancel revision = %d", got.Revision)
	}
	if got := decodeView(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", "")); got.Revision != 4 {
		t.Errorf("repeat cancel revision = %d", got.Revision)
	}
	h := e.history(v.Reference)
	want := []string{
		"1 created 1 table_id:<nil>>t_1 starts_at_local:<nil>>2026-09-24T19:00 party_size:<nil>>2",
		"2 changed 2 party_size:2>1",
		"3 changed 3 table_ids:[t_1]>[t_1 t_2] party_size:1>5",
		"4 cancelled 4 ",
	}
	if len(h.Entries) != len(want) || h.Reference != v.Reference {
		t.Fatalf("history = %+v", h)
	}
	for i, en := range h.Entries {
		if got := fmt.Sprintf("%d %s %d %s", en.Seq, en.Event, en.Revision, changesString(en.Changes)); got != want[i] {
			t.Errorf("entry %d = %q, want %q", i, got, want[i])
		}
		if !timestampPattern.MatchString(en.At) || en.Changes == nil {
			t.Errorf("entry %d at/changes = %q %v", i, en.At, en.Changes)
		}
	}
	var decision struct {
		Reference     string
		Revision      int
		AcceptedTerms state.Terms `json:"accepted_terms"`
	}
	json.Unmarshal(e.req("GET", "/reservations/"+v.Reference+"/decision", e.ada, "", "").Body.Bytes(), &decision)
	if decision.Reference != v.Reference || decision.Revision != 4 || decision.AcceptedTerms.SlotMinutes != 30 {
		t.Errorf("decision = %+v", decision)
	}
	// Owner only, and 404 (never 401) without a token.
	for _, path := range []string{"/history", "/decision"} {
		expect(t, e.req("GET", "/reservations/"+v.Reference+path, e.mgr, "", ""), 404, "not_found")
		expect(t, e.req("GET", "/reservations/"+v.Reference+path, "", "", ""), 404, "not_found")
		expect(t, e.req("GET", "/reservations/NOPE00"+path, e.ada, "", ""), 404, "not_found")
	}
}

func TestPairCreationHistory(t *testing.T) {
	e := newPolicyEnv(t)
	v := decodeView(t, e.book(e.ada, "p", pairBooking(`["t_2","t_1"]`, "2026-09-24T19:00", 5)))
	h := e.history(v.Reference)
	if got := changesString(h.Entries[0].Changes); got != "table_ids:<nil>>[t_1 t_2] starts_at_local:<nil>>2026-09-24T19:00 party_size:<nil>>5" {
		t.Errorf("pair created = %s", got)
	}
}

func TestAmendmentAdoptsResultingPolicy(t *testing.T) {
	e := newPolicyEnv(t)
	v := e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 4)
	expect(t, e.publish(e.mgr, "p1", policyBody("2026-10-01", 120, 0, 6)), 201, "")
	got := decodeView(t, e.req("PATCH", "/reservations/"+v.Reference, e.ada, "", `{"starts_at_local":"2026-10-01T17:00","party_size":6}`))
	if got.AcceptedTerms.PolicyVersion != 1 || got.EndsAt != "2026-10-01T19:00:00+02:00" || got.Revision != 2 {
		t.Errorf("amended = %+v", got)
	}
	h := e.history(v.Reference)
	if h.Entries[0].AcceptedTerms.PolicyVersion != 0 || h.Entries[1].AcceptedTerms.PolicyVersion != 1 {
		t.Errorf("history terms = %d, %d", h.Entries[0].AcceptedTerms.PolicyVersion, h.Entries[1].AcceptedTerms.PolicyVersion)
	}
	// Validation uses the resulting date's policy: back on 2026-09-24 party 6 exceeds policy 0.
	expect(t, e.req("PATCH", "/reservations/"+v.Reference, e.ada, "", `{"starts_at_local":"2026-09-24T19:00"}`), 422, "party_exceeds_capacity")
	// The accepted cutoff (0 minutes) now applies, not policy 0's 120 minutes.
	e.clock.set(time.Date(2026, 10, 1, 14, 30, 0, 0, time.UTC)) // 16:30 Berlin, 30 min before start
	expect(t, e.req("POST", "/reservations/"+v.Reference+"/cancel", e.ada, "", ""), 200, "")
}

func TestExpectedRevision(t *testing.T) {
	e := newPolicyEnv(t)
	v := e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 2)
	patch := func(body string) *httptest.ResponseRecorder {
		return e.req("PATCH", "/reservations/"+v.Reference, e.ada, "", body)
	}
	for _, bad := range []string{`"1"`, `true`, `null`, `0`, `-1`, `1.5`, `[1]`} {
		expect(t, patch(`{"expected_revision":`+bad+`,"party_size":3}`), 422, "validation_failed")
	}
	expect(t, patch(`{"expected_revision":2,"party_size":3}`), 409, "stale_revision")
	expect(t, patch(`{"expected_revision":1,"party_size":3}`), 200, "")
	expect(t, patch(`{"expected_revision":1,"party_size":4}`), 409, "stale_revision")
	expect(t, patch(`{"expected_revision":2}`), 200, "") // no-op with a current revision

	// Concurrent amendments sharing one revision: exactly one real change.
	recs := make([]*httptest.ResponseRecorder, 20)
	var wg sync.WaitGroup
	for i := range recs {
		wg.Go(func() {
			recs[i] = patch(fmt.Sprintf(`{"expected_revision":2,"party_size":%d}`, 1+i%2))
		})
	}
	wg.Wait()
	ok := 0
	for _, rec := range recs {
		if rec.Code == 200 {
			ok++
		} else if rec.Code != 409 || errorCode(t, rec) != "stale_revision" {
			t.Errorf("unexpected %d %s", rec.Code, rec.Body)
		}
	}
	if ok != 1 || len(e.history(v.Reference).Entries) != 3 {
		t.Errorf("successes = %d, history = %d entries", ok, len(e.history(v.Reference).Entries))
	}
}

func TestMovesUnderPolicies(t *testing.T) {
	e := newPolicyEnv(t)
	a := e.mustBook(e.ada, "a", "t_1", "2026-09-24T19:00", 2)
	b := e.mustBook(e.ada, "b", "t_2", "2026-09-24T19:00", 2)
	body := fmt.Sprintf(`{"moves":[{"reference":%q,"starts_at_local":"2026-09-24T21:00","expected_revision":1},{"reference":%q,"party_size":2}]}`, a.Reference, b.Reference)
	views := decodeViews(t, e.move(e.ada, "m", body))
	if views[0].Revision != 2 || views[1].Revision != 1 {
		t.Errorf("revisions = %d, %d", views[0].Revision, views[1].Revision)
	}
	if len(e.history(a.Reference).Entries) != 2 || len(e.history(b.Reference).Entries) != 1 {
		t.Error("history must record only the real change")
	}
	stale := fmt.Sprintf(`{"moves":[{"reference":%q,"party_size":1,"expected_revision":1}]}`, a.Reference)
	expect(t, e.move(e.ada, "m2", stale), 409, "stale_revision")
	// A replay changes nothing.
	expect(t, e.move(e.ada, "m", body), 200, "")
	if got := decodeView(t, e.req("GET", "/reservations/"+a.Reference, e.ada, "", "")); got.Revision != 2 {
		t.Errorf("replay changed revision: %d", got.Revision)
	}
}
