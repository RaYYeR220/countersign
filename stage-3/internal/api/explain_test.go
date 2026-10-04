package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

type explainSlot struct {
	StartsAtLocal     string   `json:"starts_at_local"`
	AvailableTableIDs []string `json:"available_table_ids"`
	Explain           []struct {
		TableID       string `json:"table_id"`
		PolicyVersion int    `json:"policy_version"`
		Available     bool   `json:"available"`
		Rules         []struct {
			Rule  string `json:"rule"`
			Holds bool   `json:"holds"`
		} `json:"rules"`
	} `json:"explain"`
}

func TestExplainShapeAndRules(t *testing.T) {
	h := seededServer(t)
	rec := do(h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-24&party_size=4&explain=true", "")
	if rec.Code != 200 {
		t.Fatalf("explain = %d %s", rec.Code, rec.Body)
	}
	var body struct{ Slots []explainSlot }
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || len(body.Slots) != 8 {
		t.Fatalf("body = %s", rec.Body)
	}
	tables := []string{"t_1", "t_2", "t_3"}
	for _, s := range body.Slots {
		if len(s.Explain) != len(tables) {
			t.Fatalf("%s: %d explanations", s.StartsAtLocal, len(s.Explain))
		}
		var avail []string
		for i, e := range s.Explain {
			if e.TableID != tables[i] || e.PolicyVersion != 0 || len(e.Rules) != 2 ||
				e.Rules[0].Rule != "capacity" || e.Rules[1].Rule != "no_overlap" {
				t.Fatalf("%s: explanation %d = %+v", s.StartsAtLocal, i, e)
			}
			if e.Available != (e.Rules[0].Holds && e.Rules[1].Holds) {
				t.Errorf("%s %s: available != both rules", s.StartsAtLocal, e.TableID)
			}
			if e.Available {
				avail = append(avail, e.TableID)
			}
			// t_1 seats 2 < 4: capacity never holds.
			if e.TableID == "t_1" && e.Rules[0].Holds {
				t.Errorf("%s: t_1 capacity holds for 4", s.StartsAtLocal)
			}
		}
		if strings.Join(avail, ",") != strings.Join(s.AvailableTableIDs, ",") {
			t.Errorf("%s: explain available %v != available_table_ids %v", s.StartsAtLocal, avail, s.AvailableTableIDs)
		}
		// Seeded t_2 19:00–20:30 blocks t_2 for starts 18:00 … 20:00 (no_overlap false, capacity true).
		hhmm := s.StartsAtLocal[11:]
		t2 := s.Explain[1]
		if busy := hhmm >= "18:00" && hhmm <= "20:00"; t2.Rules[1].Holds == busy || !t2.Rules[0].Holds {
			t.Errorf("%s: t_2 rules %+v", hhmm, t2.Rules)
		}
	}
	// party 99: both rules false for a busy too-small table, every table still listed.
	rec = do(h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-24&party_size=99&explain=true", "")
	json.Unmarshal(rec.Body.Bytes(), &body)
	for _, s := range body.Slots {
		if len(s.AvailableTableIDs) != 0 || len(s.Explain) != 3 {
			t.Fatalf("party 99 slot = %+v", s)
		}
		if s.StartsAtLocal[11:] == "19:00" && (s.Explain[1].Rules[0].Holds || s.Explain[1].Rules[1].Holds) {
			t.Errorf("t_2 at 19:00 for 99 should fail both rules: %+v", s.Explain[1].Rules)
		}
	}
}

func TestExplainAbsentAndInvalid(t *testing.T) {
	h := seededServer(t)
	plain := do(h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-24&party_size=2", "")
	if plain.Code != 200 || strings.Contains(plain.Body.String(), `"explain"`) {
		t.Errorf("without explain = %d, explain key present: %v", plain.Code, strings.Contains(plain.Body.String(), `"explain"`))
	}
	closed := do(h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-23&party_size=2&explain=true", "")
	if closed.Code != 200 || !strings.Contains(closed.Body.String(), `"slots":[]`) {
		t.Errorf("closed day explain = %d %s", closed.Code, closed.Body)
	}
	for _, q := range []string{"explain=false", "explain=1", "explain=", "explain=TRUE", "explain=true%20", "explain=yes&explain=true"} {
		rec := do(h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-24&party_size=2&"+q, "")
		if rec.Code != 422 || errorCode(t, rec) != "validation_failed" {
			t.Errorf("%s = %d %s", q, rec.Code, rec.Body)
		}
	}
	// Parameter errors, explain included, precede the unknown-restaurant 404.
	if rec := do(h, http.MethodGet, "/availability?restaurant_id=r_nope&date=2026-09-24&party_size=2&explain=false", ""); rec.Code != 422 {
		t.Errorf("unknown restaurant + bad explain = %d", rec.Code)
	}
	if rec := do(h, http.MethodGet, "/availability?restaurant_id=r_nope&date=2026-09-24&party_size=2&explain=true", ""); rec.Code != 404 {
		t.Errorf("unknown restaurant + explain = %d", rec.Code)
	}
	if rec := do(h, http.MethodGet, "/availability?restaurant_id=r_anker&date=2026-09-24&party_size=0&explain=false", ""); rec.Code != 422 {
		t.Errorf("bad party first = %d", rec.Code)
	}
}
