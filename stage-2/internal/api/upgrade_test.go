package api

import (
	"encoding/json"
	"os"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

// A stage-1 export (internal/snapshot/testdata) imported into the stage-2 service keeps its
// session, references and retries (stage 2 "Existing clients after an upgrade").
func TestUpgradeFromStage1Export(t *testing.T) {
	raw, err := os.ReadFile("../snapshot/testdata/stage1-export.json")
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
		original[k[len(k)-6:]] = string(rc.Response) // keys end in book-1 / move-1
	}

	c := &clock{t: time.Date(2026, 10, 4, 18, 0, 0, 0, time.UTC)}
	e := &env{t: t, h: New(state.NewStore(state.Empty()), c.now), clock: c}
	if rec := do(e.h, "POST", "/_test/import", string(raw)); rec.Code != 204 {
		t.Fatalf("import = %d %s", rec.Code, rec.Body)
	}

	book := `{"restaurant_id":"r_anker","table_id":"t_2","starts_at_local":"2027-01-07T19:00","party_size":4}`
	if rec := e.book(token, "book-1", book); rec.Code != 200 || rec.Body.String() != original["book-1"] {
		t.Errorf("booking replay = %d %s\nwant %s", rec.Code, rec.Body, original["book-1"])
	}
	var booked reservationView
	json.Unmarshal([]byte(original["book-1"]), &booked)
	move := `{"moves":[{"reference":"` + booked.Reference + `","starts_at_local":"2027-01-07T21:00"}]}`
	if rec := e.move(token, "move-1", move); rec.Code != 200 || rec.Body.String() != original["move-1"] {
		t.Errorf("move replay = %d %s", rec.Code, rec.Body)
	}
	expect(t, e.book(token, "book-1", `{"party_size":1}`), 409, "idempotency_key_reuse")
	// The key whose first use failed in stage 1 is a first use now.
	expect(t, e.book(token, "failed-1", booking("t_2", "2027-01-07T19:00", 2)), 201, "")

	got := decodeView(t, e.req("GET", "/reservations/"+booked.Reference, token, "", ""))
	if got.TableID != "t_2" || len(got.TableIDs) != 1 || got.StartsAt != "2027-01-07T21:00:00+01:00" || got.CreatedAt != booked.CreatedAt {
		t.Errorf("upgraded booking = %+v", got)
	}
	if seed := decodeView(t, e.req("GET", "/reservations/SEED01", token, "", "")); seed.Status != "cancelled" || seed.ReservationID != "res_seed" {
		t.Errorf("seed = %+v", seed)
	}
	expect(t, do(e.h, "POST", "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`), 200, "")
}
