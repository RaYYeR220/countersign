package api

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

// A stage-2 export (internal/snapshot/testdata) imported into the stage-3 service keeps its
// session, references and retries; its bookings start at revision 1 under policy 0.
func TestUpgradeFromStage2Export(t *testing.T) {
	raw, err := os.ReadFile("../snapshot/testdata/stage2-export.json")
	if err != nil {
		t.Fatal(err)
	}
	var export struct {
		State struct {
			Tokens   map[string]string
			Receipts map[string]state.Receipt
		}
	}
	json.Unmarshal(raw, &export)
	var token string
	for tok := range export.State.Tokens {
		token = tok
	}
	original := map[string]string{}
	for k, rc := range export.State.Receipts {
		original[k[strings.LastIndex(k, "\x00")+1:]] = string(rc.Response)
	}

	c := &clock{t: time.Date(2026, 10, 4, 18, 0, 0, 0, time.UTC)}
	e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
	if rec := do(e.h, "POST", "/_test/import", string(raw)); rec.Code != 204 {
		t.Fatalf("import = %d %s", rec.Code, rec.Body)
	}
	pair := `{"restaurant_id":"r_anker","table_ids":["t_1","t_2"],"starts_at_local":"2027-01-07T19:00","party_size":5}`
	if rec := e.book(token, "pair-1", pair); rec.Code != 200 || rec.Body.String() != original["pair-1"] {
		t.Errorf("pair replay = %d %s\nwant %s", rec.Code, rec.Body, original["pair-1"])
	}
	var booked reservationView
	json.Unmarshal([]byte(original["single-1"]), &booked)
	got := decodeView(t, e.req("GET", "/reservations/"+booked.Reference, token, "", ""))
	if got.Revision != 1 || got.AcceptedTerms.PolicyVersion != 0 || got.AcceptedTerms.ReservationDurationMinutes != 90 {
		t.Errorf("upgraded booking = %+v", got)
	}
	rec := e.req("GET", "/reservations/"+booked.Reference+"/history", token, "", "")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"event":"created"`) {
		t.Errorf("history = %d %s", rec.Code, rec.Body)
	}
	if got := decodeView(t, e.req("PATCH", "/reservations/"+booked.Reference, token, "", `{"party_size":3,"expected_revision":1}`)); got.Revision != 2 {
		t.Errorf("amended imported booking = %+v", got)
	}
	created := e.book(token, "failed-1", booking("t_3", "2027-01-07T19:00", 2))
	if created.Code != 201 || decodeView(t, created).Revision != 1 {
		t.Errorf("failed stage-2 key = %d %s", created.Code, created.Body)
	}
	if seed := decodeView(t, e.req("GET", "/reservations/SEED01", token, "", "")); seed.Status != "cancelled" || seed.Revision != 1 {
		t.Errorf("seed = %+v", seed)
	}

	// An imported booking can anchor a series (C3.48); the anchor itself stays unchanged.
	var pairBooked reservationView
	json.Unmarshal([]byte(original["pair-1"]), &pairBooked)
	rec = e.req("POST", "/series", token, "series-1", `{"anchor_reference":"`+pairBooked.Reference+`","count":3,"interval_weeks":2}`)
	if rec.Code != 201 {
		t.Fatalf("adopt imported anchor = %d %s", rec.Code, rec.Body)
	}
	var series struct {
		SeriesID    string `json:"series_id"`
		Occurrences []struct {
			Index       int
			Reference   string
			Reservation reservationView
		}
	}
	json.Unmarshal(rec.Body.Bytes(), &series)
	if len(series.Occurrences) != 3 || series.Occurrences[0].Reference != pairBooked.Reference ||
		series.Occurrences[0].Reservation.Revision != 1 || series.Occurrences[2].Reservation.StartsAtLocal != "2027-02-04T19:00" ||
		len(series.Occurrences[2].Reservation.TableIDs) != 2 {
		t.Errorf("series = %s", rec.Body)
	}
	if rec := e.req("GET", "/series/"+series.SeriesID, token, "", ""); rec.Code != 200 {
		t.Errorf("GET series = %d %s", rec.Code, rec.Body)
	}
}
