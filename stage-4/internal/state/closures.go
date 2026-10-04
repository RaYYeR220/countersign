package state

import "time"

// Closure takes a table out of service over the half-open interval [From, To); it is recorded
// when a replan is applied and never removed.
type Closure struct {
	TableID string    `json:"table_id"`
	From    time.Time `json:"from"`
	To      time.Time `json:"to"`
	PlanID  string    `json:"plan_id"`
}

// Closed reports whether an applied closure of tableID overlaps [start, end).
func (r *Restaurant) Closed(tableID string, start, end time.Time) bool {
	for _, c := range r.Closures {
		if c.TableID == tableID && c.From.Before(end) && start.Before(c.To) {
			return true
		}
	}
	return false
}
