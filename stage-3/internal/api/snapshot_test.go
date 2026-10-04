package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

func exportBody(t *testing.T, h http.Handler) []byte {
	t.Helper()
	rec := do(h, http.MethodGet, "/_test/export", "")
	if rec.Code != 200 || rec.Header().Get("Content-Type") != "application/json; charset=utf-8" {
		t.Fatalf("export = %d %s", rec.Code, rec.Body)
	}
	return rec.Body.Bytes()
}

func tokenUser(store *state.Store, token string) string {
	var id string
	store.Read(func(st *state.State) {
		if u := st.UserByToken(token); u != nil {
			id = u.ID
		}
	})
	return id
}

func TestExportShape(t *testing.T) {
	_, h := newTestServer()
	var env map[string]any
	if err := json.Unmarshal(exportBody(t, h), &env); err != nil {
		t.Fatal(err)
	}
	st, ok := env["state"].(map[string]any)
	if env["track"] != "tablekeeper" || env["format_version"] != float64(1) || !ok || st["schema"] != float64(2) {
		t.Fatalf("export envelope = %v", env)
	}
	for _, k := range []string{"users", "restaurants", "reservations", "tokens", "receipts"} {
		if st[k] == nil {
			t.Errorf("empty state lacks %s: %v", k, st)
		}
	}
}

func TestExportImportRoundTrip(t *testing.T) {
	srcStore, src := newTestServer()
	resetWith(t, src, availabilityFixture)
	ada := decodeSession(t, do(src, http.MethodPost, "/auth/login", `{"email":"ada@example.com","password":"correct horse"}`))
	bob := decodeSession(t, do(src, http.MethodPost, "/auth/signup", `{"email":"bob@example.com","password":"hunter2hunter2","display_name":"Bob"}`))
	exported := exportBody(t, src)

	// Writes after the export do not change it.
	do(src, http.MethodPost, "/auth/signup", `{"email":"late@example.com","password":"hunter2hunter2","display_name":"Late"}`)
	if bytes.Contains(exported, []byte("late@example.com")) || !bytes.Contains(exportBody(t, src), []byte("late@example.com")) {
		t.Fatal("export is not a snapshot")
	}
	if tokenUser(srcStore, bob.Token) != bob.UserID {
		t.Fatal("source lost a token")
	}

	dstStore, dst := newTestServer()
	carol := decodeSession(t, do(dst, http.MethodPost, "/auth/signup", `{"email":"carol@example.com","password":"hunter2hunter2","display_name":"Carol"}`))
	for i := 0; i < 2; i++ { // import is replacement: repeating it duplicates nothing
		if rec := do(dst, http.MethodPost, "/_test/import", string(exported)); rec.Code != 204 || rec.Body.Len() != 0 {
			t.Fatalf("import #%d = %d %s", i+1, rec.Code, rec.Body)
		}
		if got := exportBody(t, dst); !bytes.Equal(got, exported) {
			t.Fatalf("re-export after import #%d differs:\n%s\n%s", i+1, exported, got)
		}
	}
	if tokenUser(dstStore, carol.Token) != "" {
		t.Error("destination token survived import")
	}
	if tokenUser(dstStore, ada.Token) != ada.UserID || tokenUser(dstStore, bob.Token) != bob.UserID {
		t.Error("exported tokens not valid after import")
	}
	if rec := do(dst, http.MethodPost, "/auth/login", `{"email":"carol@example.com","password":"hunter2hunter2"}`); rec.Code != 401 {
		t.Errorf("destination account survived import: %d", rec.Code)
	}
	if rec := do(dst, http.MethodPost, "/auth/login", `{"email":"bob@example.com","password":"hunter2hunter2"}`); rec.Code != 200 {
		t.Errorf("imported login = %d %s", rec.Code, rec.Body)
	}
	if rec := do(dst, http.MethodPost, "/auth/signup", `{"email":"ADA@example.com","password":"hunter2hunter2","display_name":"A"}`); rec.Code != 409 {
		t.Errorf("imported email not taken: %d", rec.Code)
	}
	var seeded *state.Reservation
	dstStore.Read(func(st *state.State) { seeded = st.ReservationByRef("SEED01") })
	if seeded == nil || seeded.ID != "res_1" || seeded.UserID != "u_ada" || seeded.Status != state.Confirmed ||
		!seeded.CreatedAt.Equal(srcCreatedAt(t, srcStore, "SEED01")) {
		t.Errorf("seeded reservation after import = %+v", seeded)
	}
	// Availability is computed from the imported reservations.
	body := getAvailability(t, dst, "restaurant_id=r_anker&date=2026-09-24&party_size=2")
	for _, s := range body.Slots {
		if s.StartsAtLocal == "2026-09-24T19:00" && strings.Contains(strings.Join(s.AvailableTableIDs, ","), "t_2") {
			t.Error("imported booking does not block its table")
		}
	}
	// Reset still clears everything, including imported state.
	resetWith(t, dst, `{"users":[],"restaurants":[],"reservations":[]}`)
	if tokenUser(dstStore, ada.Token) != "" {
		t.Error("reset kept imported tokens")
	}
}

func srcCreatedAt(t *testing.T, store *state.Store, ref string) (at time.Time) {
	t.Helper()
	store.Read(func(st *state.State) { at = st.ReservationByRef(ref).CreatedAt })
	return at
}

func TestImportRejectsWithoutChange(t *testing.T) {
	_, h := newTestServer()
	resetWith(t, h, availabilityFixture)
	before := exportBody(t, h)
	mutate := func(f func(st map[string]any)) string {
		var e map[string]any
		json.Unmarshal(before, &e)
		f(e["state"].(map[string]any))
		b, _ := json.Marshal(e)
		return string(b)
	}
	cases := []struct {
		body string
		code int
	}{
		{`{not json`, 400},
		{`[1,2]`, 400},
		{`{}`, 422},
		{`{"track":"tablekeeper","format_version":1}`, 422},
		{`{"track":"other","format_version":1,"state":{}}`, 422},
		{`{"track":"tablekeeper","format_version":2,"state":{}}`, 422},
		{`{"track":"tablekeeper","format_version":"1","state":{}}`, 422},
		{`{"track":"tablekeeper","format_version":1,"state":"x"}`, 422},
		{`{"track":"tablekeeper","format_version":1,"state":{}}`, 422},
		{`{"track":"tablekeeper","format_version":1,"state":{"schema":99,"users":[],"restaurants":[],"reservations":[],"tokens":{}}}`, 422},
		{strings.Replace(string(before), `"track":"tablekeeper"`, `"track":"TABLEKEEPER"`, 1), 422},
		{mutate(func(st map[string]any) { delete(st, "tokens") }), 422},
		{mutate(func(st map[string]any) { st["users"] = nil }), 422},
		{mutate(func(st map[string]any) { st["users"] = "x" }), 422},
		{mutate(func(st map[string]any) { st["reservations"].([]any)[0].(map[string]any)["user_id"] = "u_ghost" }), 422},
		{mutate(func(st map[string]any) { st["reservations"].([]any)[0].(map[string]any)["status"] = "pending" }), 422},
		{mutate(func(st map[string]any) { st["reservations"].([]any)[1].(map[string]any)["reference"] = "SEED01" }), 422},
		{mutate(func(st map[string]any) { st["reservations"].([]any)[0].(map[string]any)["reference"] = "seed01" }), 422},
		{mutate(func(st map[string]any) { st["reservations"].([]any)[0].(map[string]any)["reference"] = "X" }), 422},
		{mutate(func(st map[string]any) { st["reservations"].([]any)[0].(map[string]any)["reference"] = "ABCDEFGHJKLMN" }), 422},
		{mutate(func(st map[string]any) { st["restaurants"].([]any)[0].(map[string]any)["timezone"] = "Mars/Base" }), 422},
		{mutate(func(st map[string]any) { st["tokens"] = map[string]any{"tok": "u_ghost"} }), 422},
		{mutate(func(st map[string]any) { st["users"].([]any)[0].(map[string]any)["password_hash"] = "" }), 422},
		{mutate(func(st map[string]any) { st["users"] = append(st["users"].([]any), nil) }), 422},
	}
	for i, c := range cases {
		rec := do(h, http.MethodPost, "/_test/import", c.body)
		want := map[int]string{400: "malformed_request", 422: "validation_failed"}[c.code]
		if rec.Code != c.code || errorCode(t, rec) != want {
			t.Errorf("case %d = %d %s, want %d", i, rec.Code, rec.Body, c.code)
		}
		if after := exportBody(t, h); !bytes.Equal(after, before) {
			t.Fatalf("case %d changed the destination", i)
		}
	}
	if rec := do(h, http.MethodPost, "/_test/export", ""); rec.Code != 405 {
		t.Errorf("POST export = %d", rec.Code)
	}
	if rec := do(h, http.MethodGet, "/_test/import", ""); rec.Code != 405 {
		t.Errorf("GET import = %d", rec.Code)
	}
}

// A key whose first use failed with 4xx stays usable after export/import (C1.111).
func TestFailedKeyIsFirstUseAfterImport(t *testing.T) {
	e := newEnv(t)
	expect(t, e.book(e.ada, "k-fail", booking("t_2", "2026-09-24T19:15", 2)), 422, "not_on_slot_grid")
	expect(t, e.book(e.ada, "k-fail2", booking("t_2", "2026-09-24T19:00", 99)), 422, "party_exceeds_capacity")
	exported := do(e.h, "GET", "/_test/export", "")
	expect(t, exported, 200, "")

	fresh := &env{t: t, h: New(state.NewStore(state.Empty()), e.clock.now), clock: e.clock}
	if rec := do(fresh.h, "POST", "/_test/import", exported.Body.String()); rec.Code != 204 {
		t.Fatalf("import = %d %s", rec.Code, rec.Body)
	}
	// Different body under the failed key: a first use, not 409.
	first := fresh.book(e.ada, "k-fail", booking("t_2", "2026-09-24T19:00", 2))
	expect(t, first, 201, "")
	// Same failing body under the other key: evaluated afresh, still the ordinary error.
	expect(t, fresh.book(e.ada, "k-fail2", booking("t_2", "2026-09-24T19:00", 99)), 422, "party_exceeds_capacity")
	// The new success is now a normal receipt.
	if again := fresh.book(e.ada, "k-fail", booking("t_2", "2026-09-24T19:00", 2)); again.Code != 200 || again.Body.String() != first.Body.String() {
		t.Errorf("replay = %d %s", again.Code, again.Body)
	}
}
