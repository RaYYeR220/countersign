package state

import (
	"encoding/json"
	"slices"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/localtime"
)

// ParsePolicy reads a complete policy for r from a request body (stage 3). Every invalid or
// missing field, including a field of the wrong JSON type, is 422 validation_failed; unknown
// fields are ignored. The result has no version yet.
func ParsePolicy(r *Restaurant, o jsonin.Object) (Policy, error) {
	var p Policy
	invalid := func(field string) error { return apperr.Validation("policy " + field + " is missing or invalid") }

	from, ok, err := o.String("effective_from")
	if err != nil || !ok || !localtime.ValidDate(from) {
		return p, invalid("effective_from")
	}
	p.EffectiveFrom = from
	ints := []struct {
		field    string
		min, max int64
		dst      *int
	}{
		{"slot_minutes", 1, 1440, &p.SlotMinutes},
		{"reservation_duration_minutes", 1, 1440, &p.ReservationDurationMinutes},
		{"cancellation_cutoff_minutes", 0, 10080, &p.CancellationCutoffMinutes},
	}
	for _, f := range ints {
		n, ok, err := o.Int(f.field)
		if o.Kind(f.field) != jsonin.Number || err != nil || !ok || n < f.min || n > f.max {
			return p, invalid(f.field)
		}
		*f.dst = int(n)
	}
	if p.OpeningHours, err = policyHours(o); err != nil {
		return p, invalid("opening_hours")
	}
	if p.Capacities, err = policyCapacities(r, o); err != nil {
		return p, invalid("capacities")
	}
	return p, nil
}

// policyHours reads opening hours with the stage-1 rules and no duplicate weekday.
func policyHours(o jsonin.Object) ([]OpeningHours, error) {
	items, ok, err := o.Objects("opening_hours")
	if err != nil || !ok {
		return nil, apperr.Validation("opening_hours")
	}
	hours := []OpeningHours{}
	for _, item := range items {
		oh, err := loadOpeningHours(item)
		if err != nil || slices.ContainsFunc(hours, func(h OpeningHours) bool { return h.Weekday == oh.Weekday }) {
			return nil, apperr.Validation("opening_hours")
		}
		hours = append(hours, oh)
	}
	return hours, nil
}

// policyCapacities reads capacities naming exactly r's tables, each an integer 1..100.
func policyCapacities(r *Restaurant, o jsonin.Object) (map[string]int, error) {
	invalid := apperr.Validation("capacities")
	if o.Kind("capacities") != jsonin.ObjectKind {
		return nil, invalid
	}
	var raw jsonin.Object
	if json.Unmarshal(o["capacities"], &raw) != nil || len(raw) != len(r.Tables) {
		return nil, invalid
	}
	capacities := make(map[string]int, len(raw))
	for _, t := range r.Tables {
		n, ok, err := raw.Int(t.ID)
		if raw.Kind(t.ID) != jsonin.Number || err != nil || !ok || n < 1 || n > 100 {
			return nil, invalid
		}
		capacities[t.ID] = int(n)
	}
	return capacities, nil
}
