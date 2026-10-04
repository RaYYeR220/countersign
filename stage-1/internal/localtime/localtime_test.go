package localtime

import (
	"errors"
	"testing"
	"time"

	"tablekeeper/internal/apperr"
)

func mustLoc(t *testing.T, name string) *time.Location {
	t.Helper()
	loc, err := Location(name)
	if err != nil {
		t.Fatal(err)
	}
	return loc
}

func code(err error) string {
	var ae *apperr.Error
	if errors.As(err, &ae) {
		return ae.Code
	}
	return ""
}

func TestResolveOffsets(t *testing.T) {
	cases := []struct{ zone, local, want string }{
		{"Europe/Berlin", "2026-09-24T19:00", "2026-09-24T19:00:00+02:00"},
		{"Europe/Berlin", "2026-01-15T19:00", "2026-01-15T19:00:00+01:00"},
		{"Europe/Berlin", "2026-03-29T01:59", "2026-03-29T01:59:00+01:00"},
		{"Europe/Berlin", "2026-03-29T03:00", "2026-03-29T03:00:00+02:00"},
		{"Europe/Berlin", "2026-10-25T01:30", "2026-10-25T01:30:00+02:00"},
		{"Europe/Berlin", "2026-10-25T02:00", "2026-10-25T02:00:00+02:00"}, // repeated: first occurrence
		{"Europe/Berlin", "2026-10-25T02:30", "2026-10-25T02:30:00+02:00"},
		{"Europe/Berlin", "2026-10-25T02:59", "2026-10-25T02:59:00+02:00"},
		{"Europe/Berlin", "2026-10-25T03:00", "2026-10-25T03:00:00+01:00"},
		{"America/New_York", "2026-03-08T01:59", "2026-03-08T01:59:00-05:00"},
		{"America/New_York", "2026-03-08T03:00", "2026-03-08T03:00:00-04:00"},
		{"America/New_York", "2026-11-01T00:59", "2026-11-01T00:59:00-04:00"},
		{"America/New_York", "2026-11-01T01:00", "2026-11-01T01:00:00-04:00"},
		{"America/New_York", "2026-11-01T01:30", "2026-11-01T01:30:00-04:00"},
		{"America/New_York", "2026-11-01T02:00", "2026-11-01T02:00:00-05:00"},
		{"UTC", "2026-11-01T02:00", "2026-11-01T02:00:00+00:00"},
	}
	for _, c := range cases {
		loc := mustLoc(t, c.zone)
		got, err := Resolve(loc, c.local)
		if err != nil {
			t.Errorf("%s %s: %v", c.zone, c.local, err)
			continue
		}
		if s := Format(got, loc); s != c.want {
			t.Errorf("%s %s = %s, want %s", c.zone, c.local, s, c.want)
		}
	}
}

func TestResolveSkipped(t *testing.T) {
	for _, c := range []struct{ zone, local string }{
		{"Europe/Berlin", "2026-03-29T02:00"},
		{"Europe/Berlin", "2026-03-29T02:30"},
		{"Europe/Berlin", "2026-03-29T02:59"},
		{"America/New_York", "2026-03-08T02:00"},
		{"America/New_York", "2026-03-08T02:30"},
	} {
		if _, err := Resolve(mustLoc(t, c.zone), c.local); code(err) != "invalid_local_time" {
			t.Errorf("%s %s: err = %v, want invalid_local_time", c.zone, c.local, err)
		}
	}
	loc := mustLoc(t, "Europe/Berlin")
	if got := Format(ResolveOrAfter(loc, "2026-03-29T02:30"), loc); got != "2026-03-29T03:00:00+02:00" {
		t.Errorf("ResolveOrAfter skipped = %s", got)
	}
	if got := Format(ResolveOrAfter(loc, "2026-10-25T02:30"), loc); got != "2026-10-25T02:30:00+02:00" {
		t.Errorf("ResolveOrAfter repeated = %s", got)
	}
}

func TestResolveRejectsBadFormat(t *testing.T) {
	loc := mustLoc(t, "Europe/Berlin")
	for _, s := range []string{"", "2026-09-24 19:00", "2026-09-24T19:00:00", "2026-09-24T19:00Z",
		"2026-09-24T19:00+02:00", "2026-02-30T19:00", "2026-09-24T24:00", "2026-9-24T19:00", "2026-09-24T19:60"} {
		if ValidLocal(s) {
			t.Errorf("ValidLocal(%q) = true", s)
		}
		if _, err := Resolve(loc, s); code(err) != "validation_failed" {
			t.Errorf("Resolve(%q) err = %v", s, err)
		}
	}
	for _, s := range []string{"2026-9-24", "2026-02-30", "20260924", "2026-09-24T00:00", ""} {
		if ValidDate(s) {
			t.Errorf("ValidDate(%q) = true", s)
		}
	}
	if !ValidDate("2028-02-29") || ValidDate("2026-02-29") {
		t.Error("leap-year dates wrong")
	}
}

func TestEndIsAbsolute(t *testing.T) {
	loc := mustLoc(t, "Europe/Berlin")
	start, _ := Resolve(loc, "2026-10-25T01:30")
	if got := Format(End(start, 90), loc); got != "2026-10-25T02:00:00+01:00" {
		t.Errorf("fall-back end = %s", got)
	}
	start, _ = Resolve(loc, "2026-03-29T01:30")
	if got := Format(End(start, 90), loc); got != "2026-03-29T04:00:00+02:00" {
		t.Errorf("spring-forward end = %s", got)
	}
	ny := mustLoc(t, "America/New_York")
	start, _ = Resolve(ny, "2026-11-01T01:30")
	if got := Format(End(start, 90), ny); got != "2026-11-01T02:00:00-05:00" {
		t.Errorf("NY fall-back end = %s", got)
	}
}

func TestOverlapsHalfOpen(t *testing.T) {
	loc := mustLoc(t, "Europe/Berlin")
	a, _ := Resolve(loc, "2026-09-24T19:00")
	b, _ := Resolve(loc, "2026-09-24T20:30")
	if Overlaps(a, End(a, 90), b, End(b, 90)) {
		t.Error("19:00+90 overlaps 20:30")
	}
	c, _ := Resolve(loc, "2026-09-24T20:00")
	if !Overlaps(a, End(a, 90), c, End(c, 90)) || !Overlaps(c, End(c, 90), a, End(a, 90)) {
		t.Error("19:00+90 does not overlap 20:00")
	}
}

var anker = []Hours{{"thu", "18:00", "23:00"}, {"fri", "18:00", "23:30"}}

func locals(slots []Slot) []string {
	out := []string{}
	for _, s := range slots {
		out = append(out, s.Local[11:])
	}
	return out
}

func TestSlotsGrid(t *testing.T) {
	loc := mustLoc(t, "Europe/Berlin")
	got := locals(Slots(loc, anker, 30, 90, "2026-09-24")) // Thursday
	want := []string{"18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"}
	if len(got) != len(want) {
		t.Fatalf("thu slots = %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("thu slots = %v", got)
		}
	}
	if s := Slots(loc, anker, 30, 90, "2026-09-23"); len(s) != 0 { // Wednesday: closed
		t.Errorf("wed slots = %v", locals(s))
	}
	if got := locals(Slots(loc, anker, 30, 90, "2026-09-25")); got[len(got)-1] != "22:00" || len(got) != 9 {
		t.Errorf("fri slots = %v", got)
	}
	if got := locals(Slots(loc, []Hours{{"thu", "18:00", "23:00"}}, 45, 60, "2026-09-24")); len(got) != 6 || got[5] != "21:45" {
		t.Errorf("slot 45 = %v", got)
	}
	if got := locals(Slots(loc, []Hours{{"thu", "22:00", "24:00"}}, 30, 60, "2026-09-24")); len(got) != 3 || got[2] != "23:00" {
		t.Errorf("closes 24:00 = %v", got)
	}
}

func TestSlotsAcrossDST(t *testing.T) {
	night := []Hours{{"sun", "00:00", "06:00"}}
	loc := mustLoc(t, "Europe/Berlin")
	// Spring forward: 02:00–02:59 never appear; 6h of wall clock is 5h of real time.
	got := Slots(loc, night, 30, 90, "2026-03-29")
	for _, s := range got {
		if s.Local[11:13] == "02" {
			t.Errorf("skipped slot offered: %s", s.Local)
		}
	}
	if l := locals(got); l[len(l)-1] != "04:30" || len(l) != 8 {
		t.Errorf("spring slots = %v", l)
	}
	// Fall back: 02:00 and 02:30 appear once, as their first occurrence (+02:00).
	got = Slots(loc, night, 30, 90, "2026-10-25")
	count := map[string]int{}
	for _, s := range got {
		count[s.Local[11:]]++
		if s.Local[11:] == "02:30" && Format(s.Start, loc) != "2026-10-25T02:30:00+02:00" {
			t.Errorf("02:30 resolved to %s", Format(s.Start, loc))
		}
	}
	if count["02:00"] != 1 || count["02:30"] != 1 {
		t.Errorf("fall-back slots = %v", locals(got))
	}
	if l := locals(got); l[len(l)-1] != "04:30" {
		t.Errorf("fall-back slots = %v", l)
	}
	ny := mustLoc(t, "America/New_York")
	for _, s := range Slots(ny, night, 30, 60, "2026-03-08") {
		if s.Local[11:13] == "02" {
			t.Errorf("NY skipped slot offered: %s", s.Local)
		}
	}
	nyFall := Slots(ny, night, 30, 60, "2026-11-01")
	for _, s := range nyFall {
		if s.Local[11:] == "01:30" && Format(s.Start, ny) != "2026-11-01T01:30:00-04:00" {
			t.Errorf("NY 01:30 resolved to %s", Format(s.Start, ny))
		}
	}
}

func TestCheckStartOrder(t *testing.T) {
	loc := mustLoc(t, "Europe/Berlin")
	night := []Hours{{"sun", "00:00", "06:00"}}
	cases := []struct {
		hours []Hours
		local string
		want  string
	}{
		{anker, "2026-09-24T18:00", ""},
		{anker, "2026-09-24T18:30", ""},
		{anker, "2026-09-24T21:30", ""},
		{anker, "2026-09-24T18:15", "not_on_slot_grid"},
		{anker, "2026-09-24T21:45", "outside_opening_hours"}, // off grid and ends after closes: hours first
		{anker, "2026-09-24T22:00", "outside_opening_hours"}, // ends 23:30 > 23:00
		{anker, "2026-09-24T17:30", "outside_opening_hours"},
		{anker, "2026-09-24T23:00", "outside_opening_hours"},
		{anker, "2026-09-23T19:00", "outside_opening_hours"}, // closed Wednesday
		{night, "2026-03-29T02:30", "invalid_local_time"},
		{night, "2026-03-29T02:15", "invalid_local_time"}, // skipped beats off-grid
		{night, "2026-03-29T03:00", ""},
		{night, "2026-10-25T02:30", ""},
		{night, "2026-10-25T04:30", ""},
		{night, "2026-10-25T05:00", "outside_opening_hours"},
	}
	for _, c := range cases {
		_, err := CheckStart(loc, c.hours, 30, 90, c.local)
		if code(err) != c.want {
			t.Errorf("CheckStart(%s) = %v, want %q", c.local, err, c.want)
		}
	}
	got, _ := CheckStart(loc, anker, 30, 90, "2026-09-24T19:00")
	if Format(got, loc) != "2026-09-24T19:00:00+02:00" {
		t.Errorf("start = %s", Format(got, loc))
	}
}

func TestSlotsAcceptedByCheckStart(t *testing.T) {
	for _, zone := range []string{"Europe/Berlin", "America/New_York", "Asia/Kolkata", "Australia/Lord_Howe"} {
		loc := mustLoc(t, zone)
		hours := []Hours{}
		for _, d := range []string{"mon", "tue", "wed", "thu", "fri", "sat", "sun"} {
			hours = append(hours, Hours{d, "00:00", "24:00"})
		}
		day := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
		for i := 0; i < 365; i++ {
			date := day.AddDate(0, 0, i).Format(dateLayout)
			for _, s := range Slots(loc, hours, 15, 60, date) {
				start, err := CheckStart(loc, hours, 15, 60, s.Local)
				if err != nil || !start.Equal(s.Start) {
					t.Fatalf("%s %s: slot not bookable: %v", zone, s.Local, err)
				}
			}
		}
	}
}

func TestLocationErrors(t *testing.T) {
	for _, z := range []string{"", "Local", "Mars/Olympus"} {
		if _, err := Location(z); code(err) != "validation_failed" {
			t.Errorf("Location(%q) = %v", z, err)
		}
	}
	if Weekday("2026-09-24") != "thu" || Weekday("2026-09-27") != "sun" {
		t.Error("weekday wrong")
	}
}
