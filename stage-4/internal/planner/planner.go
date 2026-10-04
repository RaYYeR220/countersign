// Package planner finds the optimal seating plan for a table closure (stage 4).
//
// The caller lists, for every considered booking in ascending reference order, the options
// that booking may take on its own (enough capacity under its accepted terms, no closure, no
// fixed booking in the way), and which pairs of bookings overlap in time. A plan picks one
// candidate per booking so that no two overlapping bookings share a table. Among feasible plans
// Solve returns the lexicographic minimum of
//
//  1. the number of bookings whose table set changes,
//  2. the total unused seats,
//  3. the vector of option ranks in booking (reference) order.
//
// The search is a depth-first enumeration of every combination in ascending rank-vector order,
// pruned only by conflicts and by bounds that cannot exclude an optimum, so the result is exact.
// At the planning limit (6 bookings, 6 singles + 4 pairs) there are at most 10^6 leaves.
package planner

import "slices"

// Candidate is one option a booking may take.
type Candidate struct {
	Rank    int      // singles in fixture order, then pairs in declared order, from 0
	Tables  []string // the option's tables
	Unused  int      // capacity under the booking's own terms minus its party size
	Changed bool     // the table set differs from the booking's current one
}

// Input is a planning problem: Candidates[i] for booking i, Overlap[i][j] when bookings i and j
// overlap in time.
type Input struct {
	Candidates [][]Candidate
	Overlap    [][]bool
}

type key struct {
	changed, unused int
	ranks           []int
}

func less(a, b key) bool {
	if a.changed != b.changed {
		return a.changed < b.changed
	}
	if a.unused != b.unused {
		return a.unused < b.unused
	}
	return slices.Compare(a.ranks, b.ranks) < 0
}

// Solve returns the index of the chosen candidate for every booking, or ok=false when no
// feasible plan exists.
func Solve(in Input) (choice []int, ok bool) {
	n := len(in.Candidates)
	// Bounds on what the remaining bookings must add at least.
	minChanged := make([]int, n+1)
	minUnused := make([]int, n+1)
	for i := n - 1; i >= 0; i-- {
		c, u := 1<<30, 1<<30
		for _, cand := range in.Candidates[i] {
			c = min(c, boolInt(cand.Changed))
			u = min(u, cand.Unused)
		}
		if len(in.Candidates[i]) == 0 {
			return nil, false
		}
		minChanged[i] = minChanged[i+1] + c
		minUnused[i] = minUnused[i+1] + u
	}

	// Candidates are tried in ascending rank, so leaves are visited in ascending order of the rank
	// vector: once a plan is found, a later branch can only win with strictly smaller totals.
	order := make([][]int, n)
	for i, cands := range in.Candidates {
		order[i] = make([]int, len(cands))
		for ci := range cands {
			order[i][ci] = ci
		}
		slices.SortStableFunc(order[i], func(a, b int) int { return cands[a].Rank - cands[b].Rank })
	}

	var best *key
	var bestChoice []int
	current := make([]int, n)
	ranks := make([]int, n)
	var visit func(i, changed, unused int)
	visit = func(i, changed, unused int) {
		if best != nil {
			lc, lu := changed+minChanged[i], unused+minUnused[i]
			if lc > best.changed || (lc == best.changed && lu >= best.unused) {
				return
			}
		}
		if i == n {
			k := key{changed, unused, slices.Clone(ranks)}
			if best == nil || less(k, *best) {
				best, bestChoice = &k, slices.Clone(current)
			}
			return
		}
		for _, ci := range order[i] {
			cand := in.Candidates[i][ci]
			if clashes(in, current, i, cand) {
				continue
			}
			current[i], ranks[i] = ci, cand.Rank
			visit(i+1, changed+boolInt(cand.Changed), unused+cand.Unused)
		}
	}
	visit(0, 0, 0)
	return bestChoice, best != nil
}

// clashes reports whether cand for booking i shares a table with an earlier overlapping choice.
func clashes(in Input, current []int, i int, cand Candidate) bool {
	for j := 0; j < i; j++ {
		if !in.Overlap[i][j] {
			continue
		}
		for _, t := range in.Candidates[j][current[j]].Tables {
			if slices.Contains(cand.Tables, t) {
				return true
			}
		}
	}
	return false
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
