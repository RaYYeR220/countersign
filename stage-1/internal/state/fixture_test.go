package state

import (
	"errors"
	"strings"
	"testing"
	"time"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/password"
)

const sampleFixture = `{
  "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
  "restaurants": [{
    "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
    "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
    "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"},
                      {"weekday": "fri", "opens": "18:00", "closes": "23:30"}],
    "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4}]
  }],
  "reservations": [{"id": "res_seed", "reference": "SEED01", "user_id": "u_ada", "restaurant_id": "r_anker",
                    "table_id": "t_2", "starts_at_local": "2026-09-24T19:00", "party_size": 4}]
}`

var seedTime = time.Date(2026, 9, 21, 11, 4, 3, 0, time.UTC)

func load(t *testing.T, body string) (*State, error) {
	t.Helper()
	fx, err := jsonin.Decode(strings.NewReader(body))
	if err != nil {
		t.Fatalf("decode %s: %v", body, err)
	}
	return FromFixture(fx, seedTime)
}

func TestFromFixtureSample(t *testing.T) {
	st, err := load(t, sampleFixture)
	if err != nil {
		t.Fatal(err)
	}
	u := st.UserByEmail("ADA@example.com")
	if u == nil || u.ID != "u_ada" || u.DisplayName != "Ada" {
		t.Fatalf("user = %+v", u)
	}
	if u.PasswordHash == "correct horse" || !password.Verify(u.PasswordHash, "correct horse") {
		t.Error("seeded password must be hashed and verifiable")
	}
	r := st.Restaurant("r_anker")
	if r == nil || r.SlotMinutes != 30 || len(r.OpeningHours) != 2 || r.Tables[1].ID != "t_2" || r.Table("t_2").Capacity != 4 {
		t.Fatalf("restaurant = %+v", r)
	}
	res := st.ReservationByRef("SEED01")
	if res == nil || res.Status != Confirmed || res.UserID != "u_ada" || !res.CreatedAt.Equal(seedTime) {
		t.Fatalf("reservation = %+v", res)
	}
}

func TestFromFixtureEmptyObject(t *testing.T) {
	st, err := load(t, `{}`)
	if err != nil || len(st.Users)+len(st.Restaurants)+len(st.Reservations) != 0 {
		t.Fatalf("empty fixture: %v %+v", err, st)
	}
}

func TestFromFixtureErrors(t *testing.T) {
	long := strings.Repeat("x", 65)
	cases := map[string]struct{ body, code string }{
		"users not array":     {`{"users": {}}`, "malformed_request"},
		"capacity as string":  {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"t","capacity":"2"}]}]}`, "malformed_request"},
		"missing timezone":    {`{"restaurants":[{"id":"r","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0}]}`, "validation_failed"},
		"unknown timezone":    {`{"restaurants":[{"id":"r","timezone":"Mars/Base","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0}]}`, "validation_failed"},
		"zero slot":           {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":0,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0}]}`, "validation_failed"},
		"bad weekday":         {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"opening_hours":[{"weekday":"thursday","opens":"18:00","closes":"23:00"}]}]}`, "validation_failed"},
		"duplicate weekday":   {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"opening_hours":[{"weekday":"thu","opens":"12:00","closes":"14:00"},{"weekday":"thu","opens":"18:00","closes":"23:00"}]}]}`, "validation_failed"},
		"closes before opens": {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"opening_hours":[{"weekday":"thu","opens":"23:00","closes":"18:00"}]}]}`, "validation_failed"},
		"id too long":         {`{"users":[{"id":"` + long + `","email":"a@b.c","password":"pw"}]}`, "validation_failed"},
		"duplicate email":     {`{"users":[{"id":"u1","email":"a@b.c","password":"pw"},{"id":"u2","email":"A@b.c","password":"pw"}]}`, "validation_failed"},
		"dangling user":       {strings.Replace(sampleFixture, `"user_id": "u_ada"`, `"user_id": "u_bob"`, 1), "validation_failed"},
		"bad local start":     {strings.Replace(sampleFixture, `2026-09-24T19:00`, `2026-09-24T19:00+02:00`, 1), "validation_failed"},
	}
	for name, c := range cases {
		_, err := load(t, c.body)
		var ae *apperr.Error
		if !errors.As(err, &ae) || ae.Code != c.code {
			t.Errorf("%s: err = %v, want %s", name, err, c.code)
		}
	}
}

func TestFromFixtureSeedCreatedAt(t *testing.T) {
	body := strings.Replace(sampleFixture, `"party_size": 4}`, `"party_size": 4, "created_at": "2026-09-01T08:00:00.75+02:00"}`, 1)
	st, err := load(t, body)
	if err != nil {
		t.Fatal(err)
	}
	if got := st.ReservationByRef("SEED01").CreatedAt; got.Format(time.RFC3339) != "2026-09-01T06:00:00Z" || got.Location() != time.UTC {
		t.Errorf("created_at = %v, want the fixture instant in UTC (R-16, R-21)", got)
	}
	st, _ = load(t, strings.Replace(sampleFixture, `"party_size": 4}`, `"party_size": 4, "created_at": "yesterday"}`, 1))
	if got := st.ReservationByRef("SEED01").CreatedAt; !got.Equal(seedTime) {
		t.Errorf("invalid created_at = %v, want reset time", got)
	}
}
