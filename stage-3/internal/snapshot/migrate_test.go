package snapshot

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"testing"

	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/state"
)

// testdata/stage1-export.json was exported by the accepted stage-1 service: a seeded booking
// (since cancelled), a booking made with key book-1 and moved with key move-1, a failed key.
func loadStage1(t *testing.T) jsonin.Object {
	t.Helper()
	raw, err := os.ReadFile("testdata/stage1-export.json")
	if err != nil {
		t.Fatal(err)
	}
	env, err := jsonin.Decode(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	return env
}

func TestImportStage1Export(t *testing.T) {
	st, err := Import(loadStage1(t))
	if err != nil {
		t.Fatal(err)
	}
	seed := st.ReservationByRef("SEED01")
	if seed == nil || fmt.Sprint(seed.TableIDs) != "[t_1]" || seed.Status != state.Cancelled || seed.ID != "res_seed" {
		t.Errorf("seed = %+v", seed)
	}
	if len(st.Reservations) != 2 || len(st.Tokens) != 1 || len(st.Receipts) != 2 {
		t.Errorf("counts: reservations=%d tokens=%d receipts=%d", len(st.Reservations), len(st.Tokens), len(st.Receipts))
	}
	if r := st.Restaurant("r_anker"); r == nil || r.Combinable == nil || len(r.Combinable) != 0 {
		t.Errorf("restaurant = %+v", r)
	}
	// Re-export writes the current schema and imports again unchanged.
	out, err := Export(st)
	if err != nil {
		t.Fatal(err)
	}
	var env jsonin.Object
	json.Unmarshal(out, &env)
	again, err := Import(env)
	if err != nil {
		t.Fatal(err)
	}
	out2, _ := Export(again)
	if !bytes.Equal(out, out2) || !bytes.Contains(out, []byte(`"schema":2`)) {
		t.Errorf("schema-2 round trip differs or wrong schema")
	}
}

func TestImportRejectsBrokenSchema1(t *testing.T) {
	env := loadStage1(t)
	var stateObj map[string]any
	json.Unmarshal(env["state"], &stateObj)
	delete(stateObj["reservations"].([]any)[0].(map[string]any), "table_id")
	env["state"], _ = json.Marshal(stateObj)
	if _, err := Import(env); err == nil {
		t.Error("a schema-1 reservation without table_id must be refused")
	}
}
