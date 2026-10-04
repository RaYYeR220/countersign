package state

import (
	"fmt"
	"math/rand"
	"slices"
	"sort"
	"testing"
	"time"
)

// randomRestaurant builds a state with up to 6 tables, up to 4 declared pairs and up to 8
// confirmed bookings scattered over one evening, some on pairs, some under a smaller capacity in
// their accepted terms, plus an optional applied closure.
func randomRestaurant(r *rand.Rand) (*State, *Restaurant) {
	st := Empty()
	rest := &Restaurant{ID: "r", Timezone: "UTC", Combinable: [][]string{}}
	tables := 2 + r.Intn(5)
	for t := 0; t < tables; t++ {
		rest.Tables = append(rest.Tables, Table{ID: fmt.Sprintf("t%d", t), Capacity: 2 + r.Intn(4)})
	}
	for p := 0; p < r.Intn(5) && len(rest.Combinable) < 4; p++ {
		a, b := r.Intn(tables), r.Intn(tables)
		if a != b && rest.Pair(rest.Tables[a].ID, rest.Tables[b].ID) == nil {
			rest.Combinable = append(rest.Combinable, []string{rest.Tables[a].ID, rest.Tables[b].ID})
		}
	}
	if r.Intn(3) == 0 {
		t := rest.Tables[r.Intn(tables)].ID
		from := base.Add(time.Duration(r.Intn(6)) * 30 * time.Minute)
		rest.Closures = append(rest.Closures, Closure{TableID: t, From: from, To: from.Add(time.Hour), PlanID: "old"})
	}
	st.Restaurants = append(st.Restaurants, rest)
	terms := rest.Policy0().Terms
	for i := 0; i < r.Intn(9); i++ {
		opts := rest.options()
		opt := opts[r.Intn(len(opts))]
		own := terms
		own.Capacities = map[string]int{}
		for k, v := range terms.Capacities {
			own.Capacities[k] = max(1, v-r.Intn(2))
		}
		start := base.Add(time.Duration(r.Intn(8)) * 30 * time.Minute)
		st.Reservations = append(st.Reservations, &Reservation{
			ID: fmt.Sprintf("res%d", i), Reference: fmt.Sprintf("REF%03d", r.Intn(1000)*10+i), RestaurantID: "r",
			TableIDs: opt.tables, PartySize: 1 + r.Intn(5), Status: []string{Confirmed, Confirmed, Confirmed, Cancelled}[r.Intn(4)],
			StartsAt: start, EndsAt: start.Add(time.Duration(60+30*r.Intn(3)) * time.Minute), Terms: own,
		})
	}
	st.reindex()
	return st, rest
}

var base = time.Date(2026, 9, 24, 17, 0, 0, 0, time.UTC)

func overlaps(a, b *Reservation) bool {
	return a.StartsAt.Before(b.EndsAt) && b.StartsAt.Before(a.EndsAt)
}

// bruteForcePlan tries every combination of restaurant options for the considered bookings and
// checks each constraint of C4.7/R-64 directly, keeping the C4.8 minimum.
func bruteForcePlan(st *State, rest *Restaurant, closed string, from, to time.Time) ([]Assignment, bool) {
	var considered, fixed []*Reservation
	for _, res := range st.Reservations {
		if res.Status != Confirmed {
			continue
		}
		if res.StartsAt.Before(to) && from.Before(res.EndsAt) {
			considered = append(considered, res)
		} else {
			fixed = append(fixed, res)
		}
	}
	sort.Slice(considered, func(i, j int) bool { return considered[i].Reference < considered[j].Reference })
	opts := rest.options()
	idx := make([]int, len(considered))
	var best []Assignment
	var bestKey []int // changed, unused, ranks...
	for {
		ok := true
		key := []int{0, 0}
		var plan []Assignment
		for i, res := range considered {
			o := opts[idx[i]]
			capacity := 0
			for _, t := range o.tables {
				capacity += res.Terms.Capacities[t]
				if t == closed || rest.Closed(t, res.StartsAt, res.EndsAt) {
					ok = false
				}
			}
			if capacity < res.PartySize {
				ok = false
			}
			for _, f := range fixed {
				if overlaps(f, res) && SharesTable(f.TableIDs, o.tables) {
					ok = false
				}
			}
			for j := 0; j < i; j++ {
				if overlaps(considered[j], res) && SharesTable(opts[idx[j]].tables, o.tables) {
					ok = false
				}
			}
			changed := !sameSet(o.tables, res.TableIDs)
			if changed {
				key[0]++
			}
			key[1] += capacity - res.PartySize
			plan = append(plan, Assignment{res.Reference, o.tables, changed})
		}
		if ok {
			for i := range considered {
				key = append(key, opts[idx[i]].rank)
			}
			if best == nil || slices.Compare(key, bestKey) < 0 {
				best, bestKey = plan, key
			}
		}
		i := 0
		for i < len(idx) {
			idx[i]++
			if idx[i] < len(opts) {
				break
			}
			idx[i] = 0
			i++
		}
		if i == len(idx) {
			break
		}
	}
	if best == nil && len(considered) == 0 {
		return []Assignment{}, true
	}
	return best, best != nil
}

func TestPlanClosureMatchesBruteForce(t *testing.T) {
	r := rand.New(rand.NewSource(4))
	planned := 0
	for trial := 0; trial < 1500; trial++ {
		st, rest := randomRestaurant(r)
		closed := rest.Tables[r.Intn(len(rest.Tables))].ID
		from := base.Add(time.Duration(r.Intn(6)) * 30 * time.Minute)
		to := from.Add(time.Duration(1+r.Intn(4)) * 30 * time.Minute)
		want, wantOK := bruteForcePlan(st, rest, closed, from, to)
		p, err := st.PlanClosure(rest, closed, from, to)
		if err != nil {
			if wantOK && err.Error() != "planning_limit: the closure affects more than the planner supports" {
				t.Fatalf("trial %d: %v, brute force found %v", trial, err, want)
			}
			continue
		}
		if !wantOK {
			t.Fatalf("trial %d: planner found %v, brute force found none", trial, p.Assignments)
		}
		if fmt.Sprint(p.Assignments) != fmt.Sprint(want) {
			t.Fatalf("trial %d: planner %v, brute force %v", trial, p.Assignments, want)
		}
		planned++
	}
	if planned < 300 {
		t.Errorf("only %d feasible trials", planned)
	}
}
