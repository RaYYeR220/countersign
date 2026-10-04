package state

import (
	"net/http"
	"slices"
	"strings"
	"time"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/planner"
)

// Planning limits (R-63).
const (
	maxPlanTables   = 6
	maxPlanPairs    = 4
	maxPlanBookings = 6
)

// Assignment is a considered booking's table set in a plan.
type Assignment struct {
	Reference string   `json:"reference"`
	TableIDs  []string `json:"table_ids"`
	Changed   bool     `json:"changed"`
}

// Plan is a previewed seating repair for a proposed closure. Applying it is allowed only while
// its restaurant's revision still equals RestaurantRevision, and only once.
type Plan struct {
	ID                 string       `json:"id"`
	RestaurantID       string       `json:"restaurant_id"`
	Closure            Closure      `json:"closure"`
	Assignments        []Assignment `json:"assignments"` // every considered booking, reference order
	MovedCount         int          `json:"moved_count"`
	UnusedSeats        int          `json:"unused_seats"`
	RestaurantRevision int          `json:"restaurant_revision"`
	Applied            bool         `json:"applied"`
}

// option is a single table or a declared pair, with its rank (singles in fixture order, then
// pairs in declared order).
type option struct {
	rank   int
	tables []string
}

func (r *Restaurant) options() []option {
	var opts []option
	for _, t := range r.Tables {
		opts = append(opts, option{len(opts), []string{t.ID}})
	}
	for _, p := range r.Combinable {
		opts = append(opts, option{len(opts), p})
	}
	return opts
}

// PlanClosure computes the optimal plan for closing tableID over [from, to) at r (R-61–R-64).
// It changes nothing; the caller stores the plan. Errors: 422 planning_limit, 409
// no_feasible_plan.
func (st *State) PlanClosure(r *Restaurant, tableID string, from, to time.Time) (*Plan, error) {
	var considered, fixed []*Reservation
	for _, res := range st.Reservations {
		if res.RestaurantID != r.ID || res.Status != Confirmed {
			continue
		}
		if res.StartsAt.Before(to) && from.Before(res.EndsAt) {
			considered = append(considered, res)
		} else {
			fixed = append(fixed, res)
		}
	}
	if len(r.Tables) > maxPlanTables || len(r.Combinable) > maxPlanPairs || len(considered) > maxPlanBookings {
		return nil, apperr.New(http.StatusUnprocessableEntity, "planning_limit", "the closure affects more than the planner supports")
	}
	slices.SortFunc(considered, func(a, b *Reservation) int { return strings.Compare(a.Reference, b.Reference) })

	in := planner.Input{Candidates: make([][]planner.Candidate, len(considered)), Overlap: make([][]bool, len(considered))}
	for i, res := range considered {
		in.Overlap[i] = make([]bool, len(considered))
		for j, other := range considered {
			in.Overlap[i][j] = i != j && res.StartsAt.Before(other.EndsAt) && other.StartsAt.Before(res.EndsAt)
		}
		for _, opt := range r.options() {
			capacity := res.Terms.Capacity(opt.tables)
			if capacity < res.PartySize || !optionFree(r, fixed, opt.tables, res, tableID) {
				continue
			}
			in.Candidates[i] = append(in.Candidates[i], planner.Candidate{
				Rank: opt.rank, Tables: opt.tables, Unused: capacity - res.PartySize,
				Changed: !sameSet(opt.tables, res.TableIDs),
			})
		}
	}
	choice, ok := planner.Solve(in)
	if !ok {
		return nil, apperr.New(http.StatusConflict, "no_feasible_plan", "no seating arrangement satisfies the closure")
	}

	p := &Plan{
		ID:                 newID("plan_", func(id string) bool { return st.Plans[id] != nil }),
		RestaurantID:       r.ID,
		Closure:            Closure{TableID: tableID, From: from, To: to},
		Assignments:        []Assignment{},
		RestaurantRevision: r.Revision,
	}
	p.Closure.PlanID = p.ID
	for i, res := range considered {
		c := in.Candidates[i][choice[i]]
		p.Assignments = append(p.Assignments, Assignment{res.Reference, slices.Clone(c.Tables), c.Changed})
		if c.Changed {
			p.MovedCount++
		}
		p.UnusedSeats += c.Unused
	}
	return p, nil
}

// optionFree reports whether the tables are free for res: not the proposed closure's table (res
// overlaps the closure), not under an applied closure, and not used by a fixed booking over an
// overlapping interval.
func optionFree(r *Restaurant, fixed []*Reservation, tables []string, res *Reservation, closedTable string) bool {
	for _, t := range tables {
		if t == closedTable || r.Closed(t, res.StartsAt, res.EndsAt) {
			return false
		}
	}
	for _, f := range fixed {
		if SharesTable(f.TableIDs, tables) && f.StartsAt.Before(res.EndsAt) && res.StartsAt.Before(f.EndsAt) {
			return false
		}
	}
	return true
}

func sameSet(a, b []string) bool {
	return len(a) == len(b) && !slices.ContainsFunc(a, func(id string) bool { return !slices.Contains(b, id) })
}

// ApplyPlan records the plan's closure and moves its changed bookings in one step (R-66, R-67):
// each moved booking keeps its times and terms, gains one revision and one reassigned entry;
// each affected series gains one revision; the restaurant gains one. It returns the considered
// bookings in reference order.
func (st *State) ApplyPlan(p *Plan, now time.Time) []*Reservation {
	r := st.Restaurant(p.RestaurantID)
	r.Closures = append(r.Closures, p.Closure)
	series := map[*Series]bool{}
	out := make([]*Reservation, 0, len(p.Assignments))
	for _, a := range p.Assignments {
		res := st.ReservationByRef(a.Reference)
		out = append(out, res)
		if !a.Changed {
			continue
		}
		change := FieldChange{Field: "table_ids", From: res.TableIDs, To: a.TableIDs}
		res.TableIDs = slices.Clone(a.TableIDs)
		res.Revision++
		res.Record(now, EventReassigned, []FieldChange{change})
		res.History[len(res.History)-1].PlanID = p.ID
		if s := st.SeriesOf(res); s != nil {
			series[s] = true
		}
	}
	for s := range series {
		s.Revision++
	}
	r.Revision++
	p.Applied = true
	return out
}
