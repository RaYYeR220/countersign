package api

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"tablekeeper/internal/state"
)

func (e *env) move(token, key, body string) *httptest.ResponseRecorder {
	return e.req("POST", "/reservation-moves", token, key, body)
}

func decodeViews(t *testing.T, rec *httptest.ResponseRecorder) []reservationView {
	t.Helper()
	var out struct{ Reservations []reservationView }
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("not a reservation list: %d %s", rec.Code, rec.Body)
	}
	return out.Reservations
}

func (e *env) snapshot(token string) string {
	return e.req("GET", "/reservations", token, "", "").Body.String()
}

func TestMoveSwapAndOrder(t *testing.T) {
	e := newEnv(t)
	a := e.mustBook(e.ada, "a", "t_1", "2026-09-24T19:00", 2)
	b := e.mustBook(e.ada, "b", "t_2", "2026-09-24T19:00", 2)
	c := e.mustBook(e.ada, "c", "t_2", "2026-09-24T21:00", 2)
	body := fmt.Sprintf(`{"moves":[{"reference":%q,"table_id":"t_1"},{"reference":%q,"table_id":"t_2","extra":true},{"reference":%q}]}`,
		b.Reference, a.Reference, c.Reference)
	rec := e.move(e.ada, "m1", body)
	expect(t, rec, 201, "")
	got := decodeViews(t, rec)
	if len(got) != 3 || got[0].Reference != b.Reference || got[0].TableID != "t_1" ||
		got[1].Reference != a.Reference || got[1].TableID != "t_2" || got[2] != c {
		t.Fatalf("swap result = %+v", got)
	}
	if got[0].ReservationID != b.ReservationID || got[0].CreatedAt != b.CreatedAt || got[0].StartsAt != b.StartsAt {
		t.Errorf("identity changed: %+v", got[0])
	}
	// Replay after a later cancellation returns the original response.
	expect(t, e.req("POST", "/reservations/"+a.Reference+"/cancel", e.ada, "", ""), 200, "")
	replay := e.move(e.ada, "m1", body)
	if replay.Code != 200 || replay.Body.String() != rec.Body.String() {
		t.Errorf("replay = %d %s", replay.Code, replay.Body)
	}
	// The same key on another path is a different request (C1.58).
	expect(t, e.book(e.ada, "m1", booking("t_1", "2026-09-24T21:00", 2)), 201, "")
}

func TestMoveFieldsAndNoOp(t *testing.T) {
	e := newEnv(t)
	a := e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 4)
	rec := e.move(e.ada, "m", fmt.Sprintf(`{"moves":[{"reference":%q,"party_size":3,"starts_at_local":"2026-09-24T20:00"}]}`, a.Reference))
	expect(t, rec, 201, "")
	got := decodeViews(t, rec)[0]
	if got.PartySize != 3 || got.TableID != "t_2" || got.StartsAt != "2026-09-24T20:00:00+02:00" || got.EndsAt != "2026-09-24T21:30:00+02:00" {
		t.Errorf("moved = %+v", got)
	}
}

func TestMoveErrors(t *testing.T) {
	e := newEnv(t)
	a := e.mustBook(e.ada, "a", "t_1", "2026-09-24T19:00", 2)
	b := e.mustBook(e.ada, "b", "t_2", "2026-09-24T19:00", 2)
	cancelled := e.mustBook(e.ada, "c", "t_1", "2026-09-24T21:30", 2)
	expect(t, e.req("POST", "/reservations/"+cancelled.Reference+"/cancel", e.ada, "", ""), 200, "")
	past := e.mustBook(e.ada, "p", "t_1", "2020-01-02T18:00", 2)
	elsewhere := decodeView(t, e.book(e.ada, "o", `{"restaurant_id":"r_other","table_id":"t_9","starts_at_local":"2026-09-24T12:00","party_size":2}`))
	bobs := e.mustBook(e.bob, "x", "t_2", "2026-09-24T21:00", 2)

	nine := make([]string, 9)
	for i := range nine {
		nine[i] = fmt.Sprintf(`{"reference":"R%d"}`, i)
	}
	cases := []struct {
		name, body string
		status     int
		code       string
	}{
		{"body not object", `[]`, 400, "malformed_request"},
		{"moves missing", `{}`, 422, "validation_failed"},
		{"moves not array", `{"moves":{}}`, 422, "validation_failed"},
		{"moves null", `{"moves":null}`, 422, "validation_failed"},
		{"moves empty", `{"moves":[]}`, 422, "validation_failed"},
		{"nine moves", `{"moves":[` + strings.Join(nine, ",") + `]}`, 422, "validation_failed"},
		{"item not object", `{"moves":[1]}`, 422, "validation_failed"},
		{"reference missing", `{"moves":[{"table_id":"t_1"}]}`, 422, "validation_failed"},
		{"reference number", `{"moves":[{"reference":5}]}`, 422, "validation_failed"},
		{"duplicate refs", fmt.Sprintf(`{"moves":[{"reference":%q},{"reference":%q}]}`, a.Reference, a.Reference), 422, "validation_failed"},
		{"structure before types", `{"moves":[{"reference":"X","table_id":5},{"table_id":"t_1"}]}`, 422, "validation_failed"},
		{"item table type", fmt.Sprintf(`{"moves":[{"reference":"NOPE00"},{"reference":%q,"table_id":5}]}`, a.Reference), 400, "malformed_request"},
		{"item local null", fmt.Sprintf(`{"moves":[{"reference":%q,"starts_at_local":null}]}`, a.Reference), 400, "malformed_request"},
		{"unknown ref", fmt.Sprintf(`{"moves":[{"reference":%q},{"reference":"NOPE00"}]}`, a.Reference), 404, "not_found"},
		{"foreign ref", fmt.Sprintf(`{"moves":[{"reference":%q}]}`, bobs.Reference), 404, "not_found"},
		{"other restaurant", fmt.Sprintf(`{"moves":[{"reference":%q},{"reference":%q}]}`, a.Reference, elsewhere.Reference), 422, "validation_failed"},
		{"404 in input order", fmt.Sprintf(`{"moves":[{"reference":"NOPE00"},{"reference":%q}]}`, elsewhere.Reference), 404, "not_found"},
		{"cancelled", fmt.Sprintf(`{"moves":[{"reference":%q}]}`, cancelled.Reference), 409, "reservation_cancelled"},
		{"cutoff on no-op", fmt.Sprintf(`{"moves":[{"reference":%q},{"reference":%q}]}`, a.Reference, past.Reference), 409, "cutoff_passed"},
		{"party zero", fmt.Sprintf(`{"moves":[{"reference":%q,"party_size":0}]}`, a.Reference), 422, "validation_failed"},
		{"bad table", fmt.Sprintf(`{"moves":[{"reference":%q,"table_id":"t_9"}]}`, a.Reference), 404, "not_found"},
		{"gap", fmt.Sprintf(`{"moves":[{"reference":%q,"starts_at_local":"2026-03-29T02:30"}]}`, a.Reference), 422, "invalid_local_time"},
		{"capacity", fmt.Sprintf(`{"moves":[{"reference":%q,"party_size":3}]}`, a.Reference), 422, "party_exceeds_capacity"},
		{"overlap precedes nothing: grid wins", fmt.Sprintf(`{"moves":[{"reference":%q,"table_id":"t_2"},{"reference":%q,"starts_at_local":"2026-09-24T19:10"}]}`,
			a.Reference, b.Reference), 422, "not_on_slot_grid"},
		{"two into one slot", fmt.Sprintf(`{"moves":[{"reference":%q,"table_id":"t_2"},{"reference":%q}]}`, a.Reference, b.Reference), 409, "table_unavailable"},
		{"into unlisted booking", fmt.Sprintf(`{"moves":[{"reference":%q,"table_id":"t_2","starts_at_local":"2026-09-24T20:00"}]}`, a.Reference), 409, "table_unavailable"},
	}
	before := e.snapshot(e.ada)
	for i, c := range cases {
		key := fmt.Sprintf("err-%d", i)
		rec := e.move(e.ada, key, c.body)
		if rec.Code != c.status || errorCode(t, rec) != c.code {
			t.Errorf("%s: %d %s, want %d %s", c.name, rec.Code, rec.Body, c.status, c.code)
		}
	}
	if after := e.snapshot(e.ada); after != before {
		t.Errorf("failed moves changed bookings:\n%s\n%s", before, after)
	}
	// A failed batch leaves its key free (C1.120).
	expect(t, e.move(e.ada, "err-25", fmt.Sprintf(`{"moves":[{"reference":%q,"table_id":"t_2","starts_at_local":"2026-09-24T21:00"}]}`, a.Reference)), 409, "table_unavailable")
	expect(t, e.move(e.ada, "err-25", fmt.Sprintf(`{"moves":[{"reference":%q,"starts_at_local":"2026-09-24T21:00"}]}`, a.Reference)), 201, "")
	expect(t, e.move("", "k", `{"moves":[]}`), 401, "unauthenticated")
	expect(t, e.move(e.ada, "", `{"moves":[]}`), 400, "missing_idempotency_key")
}

func TestMoveCutoffMeasuredOnCurrentStart(t *testing.T) {
	e := newEnv(t)
	a := e.mustBook(e.ada, "a", "t_2", "2026-10-01T19:00", 2)
	e.clock.set(time.Date(2026, 9, 24, 15, 0, 0, 0, time.UTC)) // 17:00 Berlin
	// Moving to a start inside the cutoff is allowed: only the current start counts.
	expect(t, e.move(e.ada, "m", fmt.Sprintf(`{"moves":[{"reference":%q,"starts_at_local":"2026-09-24T18:00"}]}`, a.Reference)), 201, "")
}

// Receipts of both keyed paths survive export → import into a fresh server (C1.124, C1.67).
func TestReceiptsSurviveExportImport(t *testing.T) {
	e := newEnv(t)
	created := e.book(e.ada, "k-book", booking("t_2", "2026-09-24T19:00", 2))
	expect(t, created, 201, "")
	v := decodeView(t, created)
	moveBody := fmt.Sprintf(`{"moves":[{"reference":%q,"table_id":"t_1"}]}`, v.Reference)
	moved := e.move(e.ada, "k-move", moveBody)
	expect(t, moved, 201, "")
	exported := do(e.h, "GET", "/_test/export", "")
	expect(t, exported, 200, "")

	fresh := &env{t: t, h: New(state.NewStore(state.Empty()), e.clock.now), clock: e.clock}
	if rec := do(fresh.h, "POST", "/_test/import", exported.Body.String()); rec.Code != 204 {
		t.Fatalf("import = %d %s", rec.Code, rec.Body)
	}
	for _, c := range []struct{ got, want *httptest.ResponseRecorder }{
		{fresh.book(e.ada, "k-book", booking("t_2", "2026-09-24T19:00", 2)), created},
		{fresh.move(e.ada, "k-move", moveBody), moved},
	} {
		if c.got.Code != 200 || !jsonEqual(c.got.Body.Bytes(), c.want.Body.Bytes()) {
			t.Errorf("replay after import = %d %s, want 200 %s", c.got.Code, c.got.Body, c.want.Body)
		}
	}
	expect(t, fresh.move(e.ada, "k-move", `{"moves":[{"reference":"X"}]}`), 409, "idempotency_key_reuse")
	if got := decodeView(t, fresh.req("GET", "/reservations/"+v.Reference, e.ada, "", "")); got.TableID != "t_1" || got.CreatedAt != v.CreatedAt {
		t.Errorf("imported booking = %+v", got)
	}
}

func jsonEqual(a, b []byte) bool {
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	return reflect.DeepEqual(x, y)
}
