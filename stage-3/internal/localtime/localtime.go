// Package localtime resolves restaurant wall-clock times to instants and applies the slot grid and
// opening hours (§4, §8, §9; rulings R-5, R-7). It is a leaf package: stdlib and apperr only.
//
// Rules:
//   - Candidate starts are wall-clock times opens + k·slot_minutes on the local date (R-5).
//   - A wall-clock time inside a spring-forward gap does not exist: it is never offered and booking
//     it is 422 invalid_local_time.
//   - A wall-clock time inside a fall-back overlap resolves to its first occurrence, the one before
//     the clocks change.
//   - Durations are absolute: ends_at = starts_at + duration minutes of real time.
//   - A start is inside opening hours iff opens <= start (wall clock) and
//     instant(start) + duration <= instant(closes on that date).
package localtime

import (
	"net/http"
	"regexp"
	"sort"
	"sync"
	"time"

	"tablekeeper/internal/apperr"
)

// Hours is one opening interval on a weekday, in local 24-hour HH:MM. Closes may be "24:00".
type Hours struct {
	Weekday string `json:"weekday"`
	Opens   string `json:"opens"`
	Closes  string `json:"closes"`
}

const (
	localLayout = "2006-01-02T15:04"
	dateLayout  = "2006-01-02"
	// OffsetLayout renders RFC 3339 with an explicit numeric offset: +00:00, never Z (§3.4).
	OffsetLayout = "2006-01-02T15:04:05-07:00"
)

var (
	localPattern = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$`)
	datePattern  = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}$`)
	clockPattern = regexp.MustCompile(`^(?:[01][0-9]|2[0-3]):[0-5][0-9]$|^24:00$`)
	weekdayKeys  = [...]string{"sun", "mon", "tue", "wed", "thu", "fri", "sat"}
)

// ValidLocal reports whether s is a bare local YYYY-MM-DDTHH:MM naming a real calendar date and
// clock time (no seconds, offset or Z).
func ValidLocal(s string) bool {
	if !localPattern.MatchString(s) {
		return false
	}
	_, err := time.Parse(localLayout, s)
	return err == nil
}

// ValidDate reports whether s is a real calendar date written YYYY-MM-DD.
func ValidDate(s string) bool {
	if !datePattern.MatchString(s) {
		return false
	}
	_, err := time.Parse(dateLayout, s)
	return err == nil
}

// Weekday returns the opening-hours key (mon … sun) of a YYYY-MM-DD date.
func Weekday(date string) string {
	d, err := time.Parse(dateLayout, date)
	if err != nil {
		return ""
	}
	return weekdayKeys[d.Weekday()]
}

var locations sync.Map // zone name → *time.Location

// Location returns the IANA zone name's location, cached. Unknown zones are an error.
func Location(name string) (*time.Location, error) {
	if loc, ok := locations.Load(name); ok {
		return loc.(*time.Location), nil
	}
	if name == "" || name == "Local" {
		return nil, apperr.Validation("timezone must be an IANA zone name")
	}
	loc, err := time.LoadLocation(name)
	if err != nil {
		return nil, apperr.Validation("unknown timezone: " + name)
	}
	locations.Store(name, loc)
	return loc, nil
}

// Format renders t in loc as RFC 3339 with an explicit offset.
func Format(t time.Time, loc *time.Location) string { return t.In(loc).Format(OffsetLayout) }

// End is the absolute end of an occupancy that starts at start.
func End(start time.Time, durationMinutes int) time.Time {
	return start.Add(time.Duration(durationMinutes) * time.Minute)
}

// Overlaps reports whether the half-open intervals [aStart, aEnd) and [bStart, bEnd) intersect.
func Overlaps(aStart, aEnd, bStart, bEnd time.Time) bool {
	return aStart.Before(bEnd) && bStart.Before(aEnd)
}

// InvalidLocalTime is 422 invalid_local_time: a wall-clock time skipped by a spring-forward change.
func InvalidLocalTime(local string) *apperr.Error {
	return apperr.New(http.StatusUnprocessableEntity, "invalid_local_time", local+" does not exist in the restaurant's timezone")
}

// candidates returns every instant whose wall clock in loc reads wall, earliest first.
// wall carries the wall-clock fields in UTC.
func candidates(loc *time.Location, wall time.Time) []time.Time {
	offsets := map[int]bool{}
	for _, probe := range []time.Time{wall.Add(-48 * time.Hour), wall, wall.Add(48 * time.Hour)} {
		t := probe.In(loc)
		_, off := t.Zone()
		offsets[off] = true
		start, end := t.ZoneBounds()
		if !start.IsZero() {
			_, before := start.Add(-time.Second).In(loc).Zone()
			offsets[before] = true
		}
		if !end.IsZero() {
			_, after := end.In(loc).Zone()
			offsets[after] = true
		}
	}
	var out []time.Time
	for off := range offsets {
		inst := wall.Add(-time.Duration(off) * time.Second)
		y, mo, d := inst.In(loc).Date()
		h, mi, s := inst.In(loc).Clock()
		if time.Date(y, mo, d, h, mi, s, 0, time.UTC).Equal(wall) {
			out = append(out, inst)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Before(out[j]) })
	return out
}

// resolveWall maps wall-clock fields to their first occurrence in loc; ok is false when skipped.
func resolveWall(loc *time.Location, wall time.Time) (time.Time, bool) {
	c := candidates(loc, wall)
	if len(c) == 0 {
		return time.Time{}, false
	}
	return c[0], true
}

// resolveWallOrAfter is resolveWall, except that a skipped time maps to the first instant whose
// wall clock reads later than it: the moment the clocks jumped forward.
func resolveWallOrAfter(loc *time.Location, wall time.Time) time.Time {
	if t, ok := resolveWall(loc, wall); ok {
		return t
	}
	// Read with the offset in force before the gap, wall lands after the jump; the jump itself
	// is the start of that zone period.
	_, before := wall.Add(-48 * time.Hour).In(loc).Zone()
	start, _ := wall.Add(-time.Duration(before) * time.Second).In(loc).ZoneBounds()
	return start
}

// Resolve maps a bare local "YYYY-MM-DDTHH:MM" in loc to an instant: a skipped (spring-forward)
// time is 422 invalid_local_time, a repeated (fall-back) time is its first occurrence. A string
// that is not a valid local time is 422 validation_failed (callers normally check ValidLocal first).
func Resolve(loc *time.Location, local string) (time.Time, error) {
	if !ValidLocal(local) {
		return time.Time{}, apperr.Validation("starts_at_local must be a local YYYY-MM-DDTHH:MM")
	}
	wall, _ := time.Parse(localLayout, local)
	t, ok := resolveWall(loc, wall)
	if !ok {
		return time.Time{}, InvalidLocalTime(local)
	}
	return t, nil
}

// ResolveOrAfter is Resolve for trusted stored data (seeded reservations, R-16): a skipped time maps
// to the instant the clocks jumped instead of failing. Invalid strings give the zero time.
func ResolveOrAfter(loc *time.Location, local string) time.Time {
	wall, err := time.Parse(localLayout, local)
	if err != nil || !localPattern.MatchString(local) {
		return time.Time{}
	}
	return resolveWallOrAfter(loc, wall)
}

// clockMinutes parses HH:MM (or 24:00) into minutes after midnight.
func clockMinutes(s string) (int, bool) {
	if !clockPattern.MatchString(s) {
		return 0, false
	}
	return int(s[0]-'0')*600 + int(s[1]-'0')*60 + int(s[3]-'0')*10 + int(s[4]-'0'), true
}

// window is one opening interval resolved on a concrete date.
type window struct {
	opens, closes int       // wall-clock minutes after midnight
	closesAt      time.Time // instant of closes on that date
}

// windows returns the opening intervals that apply on date (YYYY-MM-DD), in fixture order.
func windows(loc *time.Location, hours []Hours, date string) []window {
	day, err := time.Parse(dateLayout, date)
	if err != nil {
		return nil
	}
	key := weekdayKeys[day.Weekday()]
	var out []window
	for _, h := range hours {
		if h.Weekday != key {
			continue
		}
		o, ok1 := clockMinutes(h.Opens)
		c, ok2 := clockMinutes(h.Closes)
		if !ok1 || !ok2 || c <= o {
			continue
		}
		out = append(out, window{opens: o, closes: c, closesAt: resolveWallOrAfter(loc, day.Add(time.Duration(c)*time.Minute))})
	}
	return out
}

// CheckStart applies the time rules of a booking after its 404 checks (R-7, R-5), in order:
// 422 invalid_local_time → 422 outside_opening_hours (closed day, start before opens or at/after
// closes, or start+duration after closes) → 422 not_on_slot_grid. It returns the start instant.
func CheckStart(loc *time.Location, hours []Hours, slotMinutes, durationMinutes int, local string) (time.Time, error) {
	start, err := Resolve(loc, local)
	if err != nil {
		return time.Time{}, err
	}
	wall, _ := time.Parse(localLayout, local)
	minute := wall.Hour()*60 + wall.Minute()
	end := End(start, durationMinutes)
	inHours := false
	for _, w := range windows(loc, hours, local[:10]) {
		if minute < w.opens || minute >= w.closes || end.After(w.closesAt) {
			continue
		}
		inHours = true
		if slotMinutes > 0 && (minute-w.opens)%slotMinutes == 0 {
			return start, nil
		}
	}
	if !inHours {
		return time.Time{}, apperr.New(http.StatusUnprocessableEntity, "outside_opening_hours", local+" is outside the restaurant's opening hours")
	}
	return time.Time{}, apperr.New(http.StatusUnprocessableEntity, "not_on_slot_grid", local+" is not on the restaurant's slot grid")
}

// Slot is one bookable start on a date.
type Slot struct {
	Local string    // YYYY-MM-DDTHH:MM, accepted unchanged by CheckStart
	Start time.Time // first occurrence
	End   time.Time // Start + duration
}

// Slots lists the bookable starts on date (YYYY-MM-DD), ascending: every slot_minutes step from
// opens whose start exists and whose end is no later than closes (§8, R-5). Skipped wall-clock times
// are omitted; repeated ones appear once. A closed day has none.
func Slots(loc *time.Location, hours []Hours, slotMinutes, durationMinutes int, date string) []Slot {
	if slotMinutes <= 0 {
		return nil
	}
	day, err := time.Parse(dateLayout, date)
	if err != nil {
		return nil
	}
	seen := map[int]bool{}
	var out []Slot
	for _, w := range windows(loc, hours, date) {
		for m := w.opens; m < w.closes && m < 24*60; m += slotMinutes {
			if seen[m] {
				continue
			}
			start, ok := resolveWall(loc, day.Add(time.Duration(m)*time.Minute))
			if !ok {
				continue
			}
			end := End(start, durationMinutes)
			if end.After(w.closesAt) {
				continue
			}
			seen[m] = true
			out = append(out, Slot{Local: day.Add(time.Duration(m) * time.Minute).Format(localLayout), Start: start, End: end})
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Local < out[j].Local })
	return out
}
