package api

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func (e *env) amendSeries(token, key, id string, rev, from int, clock string) *httptest.ResponseRecorder {
	return e.req("POST", "/series/"+id+"/amend", token, key,
		fmt.Sprintf(`{"expected_revision":%d,"from_index":%d,"local_time":%q}`, rev, from, clock))
}

// seriesOf books Ada's anchor on t_2 at 2026-10-01 19:00 and adopts it weekly for count weeks.
func seriesOf(t *testing.T, e *env, count int) seriesBody {
	t.Helper()
	anchor := e.mustBook(e.ada, "k-anchor", "t_2", "2026-10-01T19:00", 2)
	rec := e.adopt(e.ada, "k-adopt", anchor.Reference, count, 1)
	expect(t, rec, 201, "")
	return decodeSeries(t, rec)
}

func historyLen(t *testing.T, e *env, ref string) int {
	var h struct{ Entries []json.RawMessage }
	json.Unmarshal(e.req("GET", "/reservations/"+ref+"/history", e.ada, "", "").Body.Bytes(), &h)
	return len(h.Entries)
}

func TestSeriesAmendChangesEligibleOccurrences(t *testing.T) {
	e, _ := newSeriesEnv(t)
	s := seriesOf(t, e, 4)
	rec := e.amendSeries(e.ada, "k-am", s.SeriesID, 1, 1, "20:00")
	expect(t, rec, 201, "")
	got := decodeSeries(t, rec)
	if got.Revision != 2 || len(got.Occurrences) != 4 {
		t.Fatalf("series = %+v", got)
	}
	want := []string{"2026-10-01T19:00", "2026-10-08T20:00", "2026-10-15T20:00", "2026-10-22T20:00"}
	for i, o := range got.Occurrences {
		r := occ(t, got, i)
		if r.StartsAtLocal != want[i] || o.Exception || o.Reference != s.Occurrences[i].Reference {
			t.Errorf("occurrence %d = %+v %+v", i, o, r)
		}
		wantRev := 2
		if i == 0 {
			wantRev = 1
		}
		if r.Revision != wantRev || historyLen(t, e, o.Reference) != wantRev {
			t.Errorf("occurrence %d revision %d, history %d", i, r.Revision, historyLen(t, e, o.Reference))
		}
	}
	var h struct {
		Entries []struct {
			Event   string `json:"event"`
			Changes []struct {
				Field    string `json:"field"`
				From, To any
			} `json:"changes"`
		}
	}
	json.Unmarshal(e.req("GET", "/reservations/"+s.Occurrences[2].Reference+"/history", e.ada, "", "").Body.Bytes(), &h)
	if last := h.Entries[len(h.Entries)-1]; last.Event != "changed" || len(last.Changes) != 1 || last.Changes[0].Field != "starts_at_local" ||
		last.Changes[0].From != "2026-10-15T19:00" || last.Changes[0].To != "2026-10-15T20:00" {
		t.Errorf("history = %+v", h)
	}
	// The new times occupy their tables; the old ones are free.
	expect(t, e.book(e.bob, "k-b1", booking("t_2", "2026-10-15T20:30", 2)), 409, "table_unavailable")
	expect(t, e.book(e.bob, "k-b2", booking("t_2", "2026-10-15T18:00", 2)), 201, "")

	// Replay returns the original response even after later changes.
	expect(t, e.req("POST", "/reservations/"+s.Occurrences[3].Reference+"/cancel", e.ada, "", ""), 200, "")
	if again := e.amendSeries(e.ada, "k-am", s.SeriesID, 1, 1, "20:00"); again.Code != 200 || again.Body.String() != rec.Body.String() {
		t.Errorf("replay = %d", again.Code)
	}
	expect(t, e.amendSeries(e.ada, "k-am", s.SeriesID, 1, 1, "21:00"), 409, "idempotency_key_reuse")
}

func TestSeriesAmendSkipsExceptionsCancelledAndNoOps(t *testing.T) {
	e, _ := newSeriesEnv(t)
	s := seriesOf(t, e, 4)
	expect(t, e.req("PATCH", "/reservations/"+s.Occurrences[2].Reference, e.ada, "", `{"party_size":3}`), 200, "") // exception, rev 2
	expect(t, e.req("POST", "/reservations/"+s.Occurrences[3].Reference+"/cancel", e.ada, "", ""), 200, "")        // rev 3
	rec := e.amendSeries(e.ada, "k-am", s.SeriesID, 3, 0, "21:00")
	expect(t, rec, 201, "")
	got := decodeSeries(t, rec)
	starts := []string{}
	for i := range got.Occurrences {
		starts = append(starts, occ(t, got, i).StartsAtLocal[11:])
	}
	if strings.Join(starts, ",") != "21:00,21:00,19:00,19:00" || got.Revision != 4 || !got.Occurrences[2].Exception ||
		got.Occurrences[0].Exception || got.Occurrences[1].Exception {
		t.Errorf("amend over exception/cancelled = %v %+v", starts, got)
	}
	// All-no-op: success, nothing changes.
	before := do(e.h, "GET", "/_test/export", "").Body.String()
	noop := e.amendSeries(e.ada, "k-noop", s.SeriesID, 4, 0, "21:00")
	expect(t, noop, 201, "")
	if decodeSeries(t, noop).Revision != 4 {
		t.Errorf("no-op bumped the series revision")
	}
	// Empty eligible set (from the cancelled last index).
	expect(t, e.amendSeries(e.ada, "k-empty", s.SeriesID, 4, 3, "18:00"), 201, "")
	after := do(e.h, "GET", "/_test/export", "").Body.String()
	// Only the two new receipts differ.
	var b, a struct{ State map[string]json.RawMessage }
	json.Unmarshal([]byte(before), &b)
	json.Unmarshal([]byte(after), &a)
	for k := range b.State {
		if k != "receipts" && string(b.State[k]) != string(a.State[k]) {
			t.Errorf("no-op amend changed %s", k)
		}
	}
}

func TestSeriesAmendValidationAndOrder(t *testing.T) {
	e, _ := newSeriesEnv(t)
	s := seriesOf(t, e, 3)
	path := "/series/" + s.SeriesID + "/amend"
	before := do(e.h, "GET", "/_test/export", "").Body.String()
	cases := []struct {
		token, path, body string
		status            int
		code              string
	}{
		{"", path, `{"expected_revision":1,"from_index":0,"local_time":"20:00"}`, 401, "unauthenticated"},
		{e.ada, path, `[]`, 400, "malformed_request"},
		{e.ada, "/series/ser_nope/amend", `{"expected_revision":true}`, 404, "not_found"},
		{e.bob, path, `{"expected_revision":1,"from_index":0,"local_time":"20:00"}`, 404, "not_found"},
		{e.ada, path, `{"from_index":0,"local_time":"20:00"}`, 422, "validation_failed"},
		{e.ada, path, `{"expected_revision":1,"local_time":"20:00"}`, 422, "validation_failed"},
		{e.ada, path, `{"expected_revision":1,"from_index":0}`, 422, "validation_failed"},
		{e.ada, path, `{"expected_revision":9,"from_index":0,"local_time":24}`, 422, "validation_failed"}, // types before stale
		{e.ada, path, `{"expected_revision":9,"from_index":5,"local_time":"20:00"}`, 422, "validation_failed"},
	}
	for _, v := range []string{`0`, `-1`, `true`, `"1"`, `null`, `1.5`, `[]`} {
		cases = append(cases, struct {
			token, path, body string
			status            int
			code              string
		}{e.ada, path, `{"expected_revision":` + v + `,"from_index":0,"local_time":"20:00"}`, 422, "validation_failed"})
	}
	for _, v := range []string{`-1`, `3`, `true`, `"0"`, `null`, `0.5`} {
		cases = append(cases, struct {
			token, path, body string
			status            int
			code              string
		}{e.ada, path, `{"expected_revision":1,"from_index":` + v + `,"local_time":"20:00"}`, 422, "validation_failed"})
	}
	for _, v := range []string{`"24:00"`, `"9:00"`, `"20:00:00"`, `"20:60"`, `""`, `2000`, `null`, `" 20:00"`} {
		cases = append(cases, struct {
			token, path, body string
			status            int
			code              string
		}{e.ada, path, `{"expected_revision":1,"from_index":0,"local_time":` + v + `}`, 422, "validation_failed"})
	}
	cases = append(cases, struct {
		token, path, body string
		status            int
		code              string
	}{e.ada, path, `{"expected_revision":2,"from_index":0,"local_time":"20:00"}`, 409, "stale_revision"})
	for i, c := range cases {
		expect(t, e.req("POST", c.path, c.token, fmt.Sprintf("k-v%d", i), c.body), c.status, c.code)
		if t.Failed() {
			t.Fatalf("case %d: %s %s", i, c.path, c.body)
		}
	}
	expect(t, e.req("POST", path, e.ada, "", `{"expected_revision":1,"from_index":0,"local_time":"20:00"}`), 400, "missing_idempotency_key")
	if got := do(e.h, "GET", "/_test/export", "").Body.String(); got != before {
		t.Errorf("refused amendments changed state")
	}
	// 1.0 is an integer; unknown fields are ignored.
	expect(t, e.req("POST", path, e.ada, "k-ok", `{"expected_revision":1.0,"from_index":0,"local_time":"20:00","note":"x"}`), 201, "")
}

func TestSeriesAmendErrorsInIndexOrderAndAtomic(t *testing.T) {
	e, mgr := newSeriesEnv(t)
	s := seriesOf(t, e, 4)
	// From 2026-10-22 (index 3) Thursdays close at 21:00: 20:30 + 90 min is outside opening hours.
	policy := `{"effective_from":"2026-10-22","slot_minutes":30,"reservation_duration_minutes":90,
	  "cancellation_cutoff_minutes":60,"opening_hours":[{"weekday":"thu","opens":"18:00","closes":"21:00"}],
	  "capacities":{"t_1":2,"t_2":4,"t_3":4}}`
	expect(t, e.req("POST", "/restaurants/r_anker/policies", mgr, "p1", policy), 201, "")
	// Bob holds t_2 at 20:30 on index 1's date: an occupancy conflict at a lower index.
	e.mustBook(e.bob, "k-bob", "t_2", "2026-10-08T20:30", 2)
	before := do(e.h, "GET", "/_test/export", "").Body.String()
	expect(t, e.amendSeries(e.ada, "k-am", s.SeriesID, 1, 1, "20:30"), 422, "outside_opening_hours") // non-occupancy first
	expect(t, e.amendSeries(e.ada, "k-am", s.SeriesID, 1, 1, "20:15"), 422, "not_on_slot_grid")
	expect(t, e.amendSeries(e.ada, "k-am", s.SeriesID, 1, 0, "19:30"), 409, "table_unavailable") // index 1: 19:30–21:00 meets Bob at 20:30
	if got := do(e.h, "GET", "/_test/export", "").Body.String(); got != before {
		t.Fatalf("failed amendments changed state")
	}
	// The key stayed unclaimed; a feasible amendment succeeds and index 3 adopts policy 1.
	rec := e.amendSeries(e.ada, "k-am", s.SeriesID, 1, 1, "18:30")
	expect(t, rec, 201, "")
	got := decodeSeries(t, rec)
	if r := occ(t, got, 3); r.AcceptedTerms.PolicyVersion != 1 || r.StartsAtLocal != "2026-10-22T18:30" {
		t.Errorf("index 3 = %+v", r)
	}
	if r := occ(t, got, 1); r.AcceptedTerms.PolicyVersion != 0 {
		t.Errorf("index 1 = %+v", r)
	}
}

func TestSeriesAmendCutoff(t *testing.T) {
	e, _ := newSeriesEnv(t)
	s := seriesOf(t, e, 2)
	e.clock.set(time.Date(2026, 10, 1, 16, 30, 0, 0, time.UTC)) // 18:30 Berlin: index 0 at 19:00 is within its cutoff
	expect(t, e.amendSeries(e.ada, "k-c1", s.SeriesID, 1, 0, "20:00"), 409, "cutoff_passed")
	// Index 0 unchanged (no-op) is not subject to the cutoff.
	expect(t, e.amendSeries(e.ada, "k-c2", s.SeriesID, 1, 0, "19:00"), 201, "")
	expect(t, e.amendSeries(e.ada, "k-c3", s.SeriesID, 1, 1, "20:00"), 201, "")
}

func TestSeriesAmendConcurrentSameRevision(t *testing.T) {
	e, _ := newSeriesEnv(t)
	s := seriesOf(t, e, 3)
	var wg sync.WaitGroup
	codes := make([]int, 8)
	for i := range codes {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			clock := []string{"20:00", "20:30"}[i%2]
			codes[i] = e.amendSeries(e.ada, fmt.Sprintf("k-%d", i), s.SeriesID, 1, 0, clock).Code
		}(i)
	}
	wg.Wait()
	created := 0
	for _, c := range codes {
		if c == 201 {
			created++
		} else if c != 409 {
			t.Errorf("unexpected status %d", c)
		}
	}
	if created != 1 {
		t.Errorf("%d concurrent amendments succeeded from one revision: %v", created, codes)
	}
	if g := decodeSeries(t, e.req("GET", "/series/"+s.SeriesID, e.ada, "", "")); g.Revision != 2 {
		t.Errorf("series revision = %d", g.Revision)
	}
}
