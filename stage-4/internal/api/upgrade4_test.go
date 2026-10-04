package api

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

// A stage-3 export imported into the stage-4 service keeps sessions, receipts, histories and
// series, and a replan can move an imported series occurrence (C4.24).
func TestUpgradeFromStage3Export(t *testing.T) {
	raw, err := os.ReadFile("../snapshot/testdata/stage3-export.json")
	if err != nil {
		t.Fatal(err)
	}
	var export struct {
		State struct {
			Users    []state.User
			Tokens   map[string]string
			Receipts map[string]state.Receipt
			Series   map[string]state.Series
		}
	}
	json.Unmarshal(raw, &export)
	tokens := map[string]string{} // user id -> token
	for tok, uid := range export.State.Tokens {
		tokens[uid] = tok
	}
	original := map[string]string{}
	for k, rc := range export.State.Receipts {
		original[k[strings.LastIndex(k, "\x00")+1:]] = string(rc.Response)
	}
	var series state.Series
	for _, s := range export.State.Series {
		series = s
	}

	c := &clock{t: time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)}
	e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
	if rec := do(e.h, "POST", "/_test/import", string(raw)); rec.Code != 204 {
		t.Fatalf("import = %d %s", rec.Code, rec.Body)
	}
	ada := tokens["u_ada"]
	e.ada = ada
	anchorBody := `{"restaurant_id":"r_anker","table_id":"t_2","starts_at_local":"2027-01-07T19:00","party_size":3}`
	if rec := e.book(ada, "anchor-1", anchorBody); rec.Code != 200 || rec.Body.String() != original["anchor-1"] {
		t.Errorf("anchor replay = %d %s", rec.Code, rec.Body)
	}
	seriesBody := `{"anchor_reference":"` + series.Occurrences[0].Reference + `","count":4,"interval_weeks":1}`
	if rec := e.req("POST", "/series", ada, "series-1", seriesBody); rec.Code != 200 || rec.Body.String() != original["series-1"] {
		t.Errorf("series replay = %d %s", rec.Code, rec.Body)
	}
	expect(t, e.book(ada, "failed-1", booking("t_1", "2027-01-07T21:00", 2)), 201, "")
	if rec := e.req("GET", "/reservations/"+series.Occurrences[1].Reference+"/history", ada, "", ""); !strings.Contains(rec.Body.String(), `"event":"changed"`) {
		t.Errorf("imported history = %s", rec.Body)
	}

	// The manager logs in (password hashes survived) and repairs a closure of t_2 on 2027-01-28,
	// which moves the last occurrence of the imported series.
	mgr := decodeSession(t, do(e.h, "POST", "/auth/login", `{"email":"mgr@example.com","password":"correct horse"}`)).Token
	pe := &policyEnv{e, mgr}
	rec, p := pe.preview("p", `{"table_id":"t_2","from":"2027-01-28T18:00:00+01:00","to":"2027-01-28T23:00:00+01:00"}`)
	if rec.Code != 201 || p.MovedCount != 1 || p.Assignments[0].Reference != series.Occurrences[3].Reference {
		t.Fatalf("preview on imported data = %d %s", rec.Code, rec.Body)
	}
	expect(t, pe.applyPlan("ap", p.PlanID), 201, "")
	var after struct {
		Revision    int
		Occurrences []struct {
			Reference string
			Exception bool
		}
	}
	json.Unmarshal(e.req("GET", "/series/"+series.ID, ada, "", "").Body.Bytes(), &after)
	if after.Revision != series.Revision+1 || !after.Occurrences[1].Exception || after.Occurrences[3].Exception {
		t.Errorf("series after repair = %+v (was revision %d)", after, series.Revision)
	}
	if got := decodeView(t, e.req("GET", "/reservations/"+series.Occurrences[3].Reference, ada, "", "")); got.TableID != "t_3" || got.StartsAtLocal != "2027-01-28T19:00" || got.Revision != 2 {
		t.Errorf("moved occurrence = %+v", got)
	}

	// Amending the imported series from index 0 skips the exception (1) and the cancelled
	// occurrence (2) and moves the anchor and the repaired occurrence (R-71).
	revisions := map[int]int{}
	for i, o := range series.Occurrences {
		revisions[i] = decodeView(t, e.req("GET", "/reservations/"+o.Reference, ada, "", "")).Revision
	}
	amend := `{"expected_revision":` + itoa(after.Revision) + `,"from_index":0,"local_time":"21:00"}`
	rec = e.req("POST", "/series/"+series.ID+"/amend", ada, "amend-1", amend)
	if rec.Code != 201 {
		t.Fatalf("amend imported series = %d %s", rec.Code, rec.Body)
	}
	json.Unmarshal(e.req("GET", "/series/"+series.ID, ada, "", "").Body.Bytes(), &after)
	if after.Revision != series.Revision+2 {
		t.Errorf("series revision after amend = %d, want %d", after.Revision, series.Revision+2)
	}
	wantLocal := map[int]string{0: "2027-01-07T21:00", 1: "2027-01-14T20:00", 2: "2027-01-21T19:00", 3: "2027-01-28T21:00"}
	for i, o := range series.Occurrences {
		got := decodeView(t, e.req("GET", "/reservations/"+o.Reference, ada, "", ""))
		changed := i == 0 || i == 3
		wantRev := revisions[i]
		if changed {
			wantRev++
		}
		if got.StartsAtLocal != wantLocal[i] || got.Revision != wantRev {
			t.Errorf("occurrence %d = %s rev %d, want %s rev %d", i, got.StartsAtLocal, got.Revision, wantLocal[i], wantRev)
		}
		hist := pe.history(o.Reference)
		last := hist.Entries[len(hist.Entries)-1]
		if changed && (last.Event != "changed" || changesString(last.Changes) != "starts_at_local:"+map[int]string{0: "2027-01-07T19:00", 3: "2027-01-28T19:00"}[i]+">"+wantLocal[i]) {
			t.Errorf("occurrence %d last entry = %+v", i, last)
		}
	}
	if after.Occurrences[0].Exception || after.Occurrences[3].Exception || !after.Occurrences[1].Exception {
		t.Errorf("exception flags after amend = %+v", after.Occurrences)
	}
	// The restaurant revision moved once more: the plan's revision +1 (apply) +1 (amend).
	_, probe := pe.preview("probe", `{"table_id":"t_1","from":"2027-06-03T18:00:00+02:00","to":"2027-06-03T19:00:00+02:00"}`)
	if probe.RestaurantRevision != p.RestaurantRevision+2 {
		t.Errorf("restaurant revision = %d, want %d", probe.RestaurantRevision, p.RestaurantRevision+2)
	}
}

func itoa(n int) string { return strconv.Itoa(n) }
