package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

// withClosure installs an applied closure through export/import, the way an applied plan leaves it.
func withClosure(t *testing.T, e *env, restaurantID, tableID, from, to string) {
	t.Helper()
	var env map[string]any
	json.Unmarshal(do(e.h, "GET", "/_test/export", "").Body.Bytes(), &env)
	for _, r := range env["state"].(map[string]any)["restaurants"].([]any) {
		rest := r.(map[string]any)
		if rest["id"] == restaurantID {
			rest["closures"] = append(rest["closures"].([]any), map[string]any{"table_id": tableID, "from": from, "to": to, "plan_id": "plan_test"})
		}
	}
	b, _ := json.Marshal(env)
	expect(t, do(e.h, "POST", "/_test/import", string(b)), 204, "")
}

func TestClosureInAvailabilityAndExplain(t *testing.T) {
	e, _ := newSeriesEnv(t)
	// t_2 closed 2026-10-01 19:00–21:00 Berlin; t_1+t_2 is a declared pair.
	withClosure(t, e, "r_anker", "t_2", "2026-10-01T19:00:00+02:00", "2026-10-01T21:00:00+02:00")
	type slot struct {
		StartsAtLocal     string   `json:"starts_at_local"`
		AvailableTableIDs []string `json:"available_table_ids"`
		AvailableOptions  []struct {
			TableIDs []string `json:"table_ids"`
		} `json:"available_options"`
		Explain []struct {
			TableID   string `json:"table_id"`
			Available bool   `json:"available"`
			Rules     []struct {
				Rule  string `json:"rule"`
				Holds bool   `json:"holds"`
			} `json:"rules"`
		} `json:"explain"`
	}
	var body struct{ Slots []slot }
	rec := do(e.h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-10-01&party_size=2&explain=true", "")
	json.Unmarshal(rec.Body.Bytes(), &body)
	for _, s := range body.Slots {
		hhmm := s.StartsAtLocal[11:]
		closed := hhmm >= "17:30" && hhmm < "21:00" // [start, start+90) overlaps [19:00, 21:00)
		hasT2 := strings.Contains(strings.Join(s.AvailableTableIDs, ","), "t_2")
		hasPair := false
		for _, o := range s.AvailableOptions {
			hasPair = hasPair || len(o.TableIDs) == 2
		}
		t2 := s.Explain[1]
		if t2.TableID != "t_2" || t2.Rules[0].Holds != true || t2.Rules[1].Holds == closed || t2.Available == closed {
			t.Errorf("%s: t_2 explain = %+v", hhmm, t2)
		}
		if hasT2 == closed || hasPair == closed {
			t.Errorf("%s: t_2 listed %v, pair listed %v (closed %v)", hhmm, hasT2, hasPair, closed)
		}
	}
	// Creates over the closure → 409; a booking after it is fine; other tables unaffected.
	expect(t, e.book(e.ada, "k1", booking("t_2", "2026-10-01T20:00", 2)), 409, "table_unavailable")
	expect(t, e.book(e.ada, "k2", booking("t_2", "2026-10-01T21:00", 2)), 201, "")
	expect(t, e.book(e.ada, "k3", booking("t_3", "2026-10-01T20:00", 2)), 201, "")
}

func TestSeriesAmendRespectsClosures(t *testing.T) {
	e, _ := newSeriesEnv(t)
	s := seriesOf(t, e, 3) // t_2 Thursdays 19:00 from 2026-10-01
	withClosure(t, e, "r_anker", "t_2", "2026-10-15T20:30:00+02:00", "2026-10-15T23:00:00+02:00")
	before := do(e.h, "GET", "/_test/export", "").Body.String()
	expect(t, e.amendSeries(e.ada, "k-a", s.SeriesID, 1, 0, "20:00"), 409, "table_unavailable") // index 2 meets the closure
	if got := do(e.h, "GET", "/_test/export", "").Body.String(); got != before {
		t.Fatalf("refused amendment changed state")
	}
	expect(t, e.amendSeries(e.ada, "k-a", s.SeriesID, 1, 0, "18:30"), 201, "")
}
