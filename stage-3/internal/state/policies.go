package state

import "slices"

// Terms are the booking rules of one policy, as accepted by a reservation (accepted_terms).
type Terms struct {
	PolicyVersion              int            `json:"policy_version"`
	SlotMinutes                int            `json:"slot_minutes"`
	ReservationDurationMinutes int            `json:"reservation_duration_minutes"`
	CancellationCutoffMinutes  int            `json:"cancellation_cutoff_minutes"`
	OpeningHours               []OpeningHours `json:"opening_hours"`
	Capacities                 map[string]int `json:"capacities"`
}

// Policy is a published, immutable policy: its terms apply to bookings whose local start date is
// on or after EffectiveFrom, until a later-dated policy takes over.
type Policy struct {
	EffectiveFrom string `json:"effective_from"`
	Terms
}

// Policy0 is the fixture's own rules, which apply before any published policy.
func (r *Restaurant) Policy0() Policy {
	capacities := make(map[string]int, len(r.Tables))
	for _, t := range r.Tables {
		capacities[t.ID] = t.Capacity
	}
	return Policy{Terms: Terms{
		SlotMinutes:                r.SlotMinutes,
		ReservationDurationMinutes: r.ReservationDurationMinutes,
		CancellationCutoffMinutes:  r.CancellationCutoffMinutes,
		OpeningHours:               slices.Clone(r.OpeningHours),
		Capacities:                 capacities,
	}}
}

// PolicyFor selects the policy for a booking whose local start date is date (YYYY-MM-DD): the
// greatest effective_from not later than date, ties to the greatest version; policy 0 otherwise.
// It is the one selection function for availability, booking, amendment and series.
func (r *Restaurant) PolicyFor(date string) Policy {
	best := r.Policy0()
	for _, p := range r.Policies {
		if p.EffectiveFrom > date {
			continue
		}
		if best.PolicyVersion == 0 || p.EffectiveFrom > best.EffectiveFrom ||
			(p.EffectiveFrom == best.EffectiveFrom && p.PolicyVersion > best.PolicyVersion) {
			best = p
		}
	}
	return best
}

// Capacity is the summed capacity of the tables ids under these terms.
func (t Terms) Capacity(ids []string) int {
	total := 0
	for _, id := range ids {
		total += t.Capacities[id]
	}
	return total
}

// Publish appends a policy as the next version and returns it.
func (r *Restaurant) Publish(p Policy) Policy {
	p.PolicyVersion = len(r.Policies) + 1
	r.Policies = append(r.Policies, p)
	return p
}

// IsManager reports whether userID may publish the restaurant's policies.
func (r *Restaurant) IsManager(userID string) bool { return slices.Contains(r.ManagerUserIDs, userID) }
