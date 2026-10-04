package state

import (
	"errors"
	"fmt"
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

func TestFromFixtureReferenceBounds(t *testing.T) {
	for _, ref := range []string{"SEED01", "ABCDEFGHJKLM"} {
		if _, err := load(t, strings.Replace(sampleFixture, `"SEED01"`, `"`+ref+`"`, 1)); err != nil {
			t.Errorf("reference %s: %v", ref, err)
		}
	}
}

func TestFromFixtureSeedTablesAndStatus(t *testing.T) {
	body := strings.Replace(sampleFixture, `"table_id": "t_2"`, `"table_ids": ["t_2", "t_1"]`, 1)
	body = strings.Replace(body, `"tables": [`, `"combinable": [["t_1", "t_2"]], "tables": [`, 1)
	body = strings.Replace(body, `"party_size": 4}`, `"party_size": 4, "status": "cancelled"}`, 1)
	st, err := load(t, body)
	if err != nil {
		t.Fatal(err)
	}
	res := st.ReservationByRef("SEED01")
	if fmt.Sprint(res.TableIDs) != "[t_1 t_2]" || res.Status != Cancelled {
		t.Errorf("seed = %+v", res)
	}
	st, _ = load(t, sampleFixture)
	if r := st.Restaurant("r_anker"); r.Combinable == nil || len(r.Combinable) != 0 {
		t.Errorf("absent combinable = %#v, want empty", r.Combinable)
	}
}

func TestFromFixtureManagers(t *testing.T) {
	st, err := load(t, strings.Replace(sampleFixture, `"name": "Zum Anker",`, `"name": "Zum Anker", "manager_user_ids": ["u_ada"],`, 1))
	if err != nil || !st.Restaurant("r_anker").IsManager("u_ada") {
		t.Fatalf("managers: %v", err)
	}
	st, _ = load(t, sampleFixture)
	if m := st.Restaurant("r_anker").ManagerUserIDs; m == nil || len(m) != 0 {
		t.Errorf("absent managers = %#v, want []", m)
	}
	if res := st.ReservationByRef("SEED01"); res.Revision != 1 || res.Terms.PolicyVersion != 0 || len(res.History) != 1 {
		t.Errorf("seed record = %+v", res)
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
		"users not array":        {`{"users": {}}`, "malformed_request"},
		"capacity as string":     {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"t","capacity":"2"}]}]}`, "malformed_request"},
		"missing timezone":       {`{"restaurants":[{"id":"r","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0}]}`, "validation_failed"},
		"unknown timezone":       {`{"restaurants":[{"id":"r","timezone":"Mars/Base","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0}]}`, "validation_failed"},
		"zero slot":              {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":0,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0}]}`, "validation_failed"},
		"bad weekday":            {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"opening_hours":[{"weekday":"thursday","opens":"18:00","closes":"23:00"}]}]}`, "validation_failed"},
		"duplicate weekday":      {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"opening_hours":[{"weekday":"thu","opens":"12:00","closes":"14:00"},{"weekday":"thu","opens":"18:00","closes":"23:00"}]}]}`, "validation_failed"},
		"closes before opens":    {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"opening_hours":[{"weekday":"thu","opens":"23:00","closes":"18:00"}]}]}`, "validation_failed"},
		"id too long":            {`{"users":[{"id":"` + long + `","email":"a@b.c","password":"pw"}]}`, "validation_failed"},
		"duplicate email":        {`{"users":[{"id":"u1","email":"a@b.c","password":"pw"},{"id":"u2","email":"A@b.c","password":"pw"}]}`, "validation_failed"},
		"dangling user":          {strings.Replace(sampleFixture, `"user_id": "u_ada"`, `"user_id": "u_bob"`, 1), "validation_failed"},
		"reference lowercase":    {strings.Replace(sampleFixture, `"SEED01"`, `"seed01"`, 1), "validation_failed"},
		"reference too short":    {strings.Replace(sampleFixture, `"SEED01"`, `"X"`, 1), "validation_failed"},
		"reference 13 chars":     {strings.Replace(sampleFixture, `"SEED01"`, `"ABCDEFGHJKLMN"`, 1), "validation_failed"},
		"reference hyphen":       {strings.Replace(sampleFixture, `"SEED01"`, `"SEED-01"`, 1), "validation_failed"},
		"reference empty":        {strings.Replace(sampleFixture, `"SEED01"`, `""`, 1), "validation_failed"},
		"reference 65 chars":     {strings.Replace(sampleFixture, `"SEED01"`, `"`+strings.Repeat("A", 65)+`"`, 1), "validation_failed"},
		"combinable not array":   {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"a","capacity":2},{"id":"b","capacity":2}],"combinable":"a"}]}`, "malformed_request"},
		"combinable item number": {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"a","capacity":2},{"id":"b","capacity":2}],"combinable":[["a",2]]}]}`, "validation_failed"},
		"combinable single":      {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"a","capacity":2},{"id":"b","capacity":2}],"combinable":[["a"]]}]}`, "validation_failed"},
		"combinable triple":      {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"a","capacity":2},{"id":"b","capacity":2}],"combinable":[["a","b","a"]]}]}`, "validation_failed"},
		"combinable self":        {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"a","capacity":2},{"id":"b","capacity":2}],"combinable":[["a","a"]]}]}`, "validation_failed"},
		"combinable unknown":     {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"a","capacity":2},{"id":"b","capacity":2}],"combinable":[["a","z"]]}]}`, "validation_failed"},
		"combinable twice":       {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"a","capacity":2},{"id":"b","capacity":2}],"combinable":[["a","b"],["b","a"]]}]}`, "validation_failed"},
		"seed both table fields": {strings.Replace(sampleFixture, `"table_id": "t_2"`, `"table_id": "t_2", "table_ids": ["t_2"]`, 1), "validation_failed"},
		"seed no table":          {strings.Replace(sampleFixture, `"table_id": "t_2",`, ``, 1), "validation_failed"},
		"seed table_ids string":  {strings.Replace(sampleFixture, `"table_id": "t_2"`, `"table_ids": "t_2"`, 1), "malformed_request"},
		"seed three tables":      {strings.Replace(sampleFixture, `"table_id": "t_2"`, `"table_ids": ["t_1", "t_2", "t_1"]`, 1), "validation_failed"},
		"seed unknown in set":    {strings.Replace(sampleFixture, `"table_id": "t_2"`, `"table_ids": ["t_1", "t_9"]`, 1), "validation_failed"},
		"seed undeclared pair":   {strings.Replace(sampleFixture, `"table_id": "t_2"`, `"table_ids": ["t_1", "t_2"]`, 1), "validation_failed"},
		"combinable item object": {`{"restaurants":[{"id":"r","timezone":"UTC","slot_minutes":30,"reservation_duration_minutes":90,"cancellation_cutoff_minutes":0,"tables":[{"id":"a","capacity":2},{"id":"b","capacity":2}],"combinable":[{"a":"b"}]}]}`, "validation_failed"},
		"seed bad status":        {strings.Replace(sampleFixture, `"party_size": 4}`, `"party_size": 4, "status": "pending"}`, 1), "validation_failed"},
		"seed status number":     {strings.Replace(sampleFixture, `"party_size": 4}`, `"party_size": 4, "status": 1}`, 1), "malformed_request"},
		"managers not array":     {strings.Replace(sampleFixture, `"name": "Zum Anker",`, `"name": "Zum Anker", "manager_user_ids": "u_ada",`, 1), "malformed_request"},
		"managers item number":   {strings.Replace(sampleFixture, `"name": "Zum Anker",`, `"name": "Zum Anker", "manager_user_ids": [1],`, 1), "malformed_request"},
		"manager unknown":        {strings.Replace(sampleFixture, `"name": "Zum Anker",`, `"name": "Zum Anker", "manager_user_ids": ["u_bob"],`, 1), "validation_failed"},
		"manager empty":          {strings.Replace(sampleFixture, `"name": "Zum Anker",`, `"name": "Zum Anker", "manager_user_ids": [""],`, 1), "validation_failed"},
		"manager duplicate":      {strings.Replace(sampleFixture, `"name": "Zum Anker",`, `"name": "Zum Anker", "manager_user_ids": ["u_ada", "u_ada"],`, 1), "validation_failed"},
		"bad local start":        {strings.Replace(sampleFixture, `2026-09-24T19:00`, `2026-09-24T19:00+02:00`, 1), "validation_failed"},
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
