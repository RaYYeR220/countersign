package snapshot

import (
	"bytes"
	"fmt"
	"os"
	"testing"

	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/state"
)

// testdata/stage2-export.json was exported by the accepted stage-2 service: a seeded cancelled
// pair (SEED01), a pair booked with key pair-1, a single booked with key single-1, a failed key.
func loadExport(t *testing.T, name string) jsonin.Object {
	t.Helper()
	raw, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatal(err)
	}
	env, err := jsonin.Decode(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	return env
}

func TestImportStage2Export(t *testing.T) {
	st, err := Import(loadExport(t, "stage2-export.json"))
	if err != nil {
		t.Fatal(err)
	}
	r := st.Restaurant("r_anker")
	if r.ManagerUserIDs == nil || len(r.ManagerUserIDs) != 0 || len(r.Policies) != 0 || r.Revision != 0 {
		t.Errorf("restaurant = %+v", r)
	}
	if len(st.Series) != 0 || len(st.Receipts) != 2 || len(st.Tokens) != 1 {
		t.Errorf("series=%d receipts=%d tokens=%d", len(st.Series), len(st.Receipts), len(st.Tokens))
	}
	for _, res := range st.Reservations {
		if res.Revision != 1 || res.Terms.PolicyVersion != 0 || res.Terms.ReservationDurationMinutes != 90 ||
			res.Terms.Capacities["t_2"] != 4 || len(res.History) != 1 || res.SeriesID != "" {
			t.Errorf("%s = %+v", res.Reference, res)
		}
		e := res.History[0]
		if e.Seq != 1 || e.Event != state.EventCreated || e.Revision != 1 || !e.At.Equal(res.CreatedAt) {
			t.Errorf("%s created entry = %+v", res.Reference, e)
		}
	}
	seed := st.ReservationByRef("SEED01")
	if seed.Status != state.Cancelled || fmt.Sprint(seed.History[0].Changes[0]) != "{table_ids <nil> [t_1 t_2]}" {
		t.Errorf("seed = %+v / %+v", seed, seed.History[0].Changes)
	}
}

func TestImportStage1ExportToSchema3(t *testing.T) {
	st, err := Import(loadExport(t, "stage1-export.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, res := range st.Reservations {
		if res.Revision != 1 || len(res.History) != 1 || res.History[0].Changes[0].Field != "table_id" {
			t.Errorf("%s = %+v", res.Reference, res)
		}
	}
}
