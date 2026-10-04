package state

import (
	"slices"

	"tablekeeper/internal/apperr"
)

// Pair returns the declared pair of tables a and b in combinable order, or nil when the
// restaurant does not list them together. Combining is not transitive.
func (r *Restaurant) Pair(a, b string) []string {
	for _, p := range r.Combinable {
		if (p[0] == a && p[1] == b) || (p[0] == b && p[1] == a) {
			return p
		}
	}
	return nil
}

// Capacity is the summed capacity of the tables ids; every id must belong to r.
func (r *Restaurant) Capacity(ids []string) int {
	total := 0
	for _, id := range ids {
		total += r.Table(id).Capacity
	}
	return total
}

// checkCombinable requires every declared pair to name two distinct tables of r, each
// unordered pair at most once.
func checkCombinable(r *Restaurant) error {
	for i, p := range r.Combinable {
		if len(p) != 2 || p[0] == p[1] || r.Table(p[0]) == nil || r.Table(p[1]) == nil {
			return apperr.Validation("restaurant " + r.ID + ": combinable entries must pair two distinct tables of the restaurant")
		}
		for _, q := range r.Combinable[:i] {
			if (q[0] == p[0] && q[1] == p[1]) || (q[0] == p[1] && q[1] == p[0]) {
				return apperr.Validation("restaurant " + r.ID + ": combinable pair listed twice")
			}
		}
	}
	return nil
}

// checkTableSet requires one table or two distinct tables, all of restaurant r.
func checkTableSet(r *Restaurant, ids []string) bool {
	if len(ids) < 1 || len(ids) > 2 || (len(ids) == 2 && ids[0] == ids[1]) {
		return false
	}
	for _, id := range ids {
		if r.Table(id) == nil {
			return false
		}
	}
	return true
}

// SharesTable reports whether the table sets a and b have a member in common.
func SharesTable(a, b []string) bool {
	for _, id := range a {
		if slices.Contains(b, id) {
			return true
		}
	}
	return false
}
