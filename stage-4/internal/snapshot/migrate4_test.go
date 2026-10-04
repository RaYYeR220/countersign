package snapshot

import "testing"

// testdata/stage3-export.json was exported by the accepted stage-3 service: a published policy,
// an anchor booked with key anchor-1, a 4-occurrence series adopted with key series-1 whose
// occurrence 1 was PATCHed (exception) and occurrence 2 cancelled, and a failed key.
func TestImportStage3Export(t *testing.T) {
	st, err := Import(loadExport(t, "stage3-export.json"))
	if err != nil {
		t.Fatal(err)
	}
	r := st.Restaurant("r_anker")
	if r.Closures == nil || len(r.Closures) != 0 || len(st.Plans) != 0 || len(r.Policies) != 1 || r.Revision != 5 {
		t.Errorf("restaurant: closures=%v plans=%d policies=%d revision=%d", r.Closures, len(st.Plans), len(r.Policies), r.Revision)
	}
	if len(st.Series) != 1 {
		t.Fatalf("series = %d", len(st.Series))
	}
	for _, s := range st.Series {
		if s.Revision != 3 || !s.Occurrences[1].Exception || s.Occurrences[2].Exception {
			t.Errorf("series = %+v", s)
		}
		if res := st.ReservationByRef(s.Occurrences[2].Reference); res.Status != "cancelled" || res.SeriesID != s.ID {
			t.Errorf("cancelled occurrence = %+v", res)
		}
		if res := st.ReservationByRef(s.Occurrences[1].Reference); res.Revision != 2 || len(res.History) != 2 {
			t.Errorf("patched occurrence = %+v", res)
		}
	}
}

func TestImportEarlierExportsToSchema4(t *testing.T) {
	for _, name := range []string{"stage1-export.json", "stage2-export.json"} {
		st, err := Import(loadExport(t, name))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		for _, r := range st.Restaurants {
			if r.Closures == nil || r.Revision != 0 {
				t.Errorf("%s restaurant = %+v", name, r)
			}
		}
	}
}
