package planner

import (
	"fmt"
	"math/rand"
	"slices"
	"testing"
	"time"
)

// bruteForce enumerates every combination with an odometer, independently of Solve's search
// and bounds, and keeps the lexicographic minimum of (changed, unused, ranks).
func bruteForce(in Input) ([]int, bool) {
	n := len(in.Candidates)
	for _, c := range in.Candidates {
		if len(c) == 0 {
			return nil, false
		}
	}
	idx := make([]int, n)
	var best []int
	var bestKey [3]any
	better := func(a, b [3]any) bool {
		if a[0].(int) != b[0].(int) {
			return a[0].(int) < b[0].(int)
		}
		if a[1].(int) != b[1].(int) {
			return a[1].(int) < b[1].(int)
		}
		return slices.Compare(a[2].([]int), b[2].([]int)) < 0
	}
	for {
		feasible := true
		for i := 0; i < n && feasible; i++ {
			for j := i + 1; j < n && feasible; j++ {
				if !in.Overlap[i][j] {
					continue
				}
				for _, t := range in.Candidates[i][idx[i]].Tables {
					if slices.Contains(in.Candidates[j][idx[j]].Tables, t) {
						feasible = false
					}
				}
			}
		}
		if feasible {
			changed, unused, ranks := 0, 0, make([]int, n)
			for i := range idx {
				c := in.Candidates[i][idx[i]]
				if c.Changed {
					changed++
				}
				unused += c.Unused
				ranks[i] = c.Rank
			}
			k := [3]any{changed, unused, ranks}
			if best == nil || better(k, bestKey) {
				best, bestKey = slices.Clone(idx), k
			}
		}
		i := 0
		for i < n {
			idx[i]++
			if idx[i] < len(in.Candidates[i]) {
				break
			}
			idx[i] = 0
			i++
		}
		if i == n {
			break
		}
	}
	return best, best != nil
}

// randomInput builds a restaurant-like instance: up to 6 singles and 4 pairs over them, up to 6
// bookings, each allowed a random subset of the options.
func randomInput(r *rand.Rand) Input {
	tables := 2 + r.Intn(5)
	var options [][]string
	for t := 0; t < tables; t++ {
		options = append(options, []string{fmt.Sprintf("t%d", t)})
	}
	for p := 0; p < r.Intn(5); p++ {
		a, b := r.Intn(tables), r.Intn(tables)
		if a != b {
			options = append(options, []string{fmt.Sprintf("t%d", a), fmt.Sprintf("t%d", b)})
		}
	}
	n := 1 + r.Intn(6)
	in := Input{Candidates: make([][]Candidate, n), Overlap: make([][]bool, n)}
	current := make([]int, n)
	for i := range n {
		current[i] = r.Intn(len(options))
		in.Overlap[i] = make([]bool, n)
	}
	for i := range n {
		for j := i + 1; j < n; j++ {
			o := r.Intn(3) > 0
			in.Overlap[i][j], in.Overlap[j][i] = o, o
		}
		for rank, opt := range options {
			if r.Intn(4) == 0 {
				continue
			}
			in.Candidates[i] = append(in.Candidates[i], Candidate{
				Rank: rank, Tables: opt, Unused: r.Intn(4), Changed: rank != current[i],
			})
		}
	}
	return in
}

func TestSolveMatchesBruteForce(t *testing.T) {
	r := rand.New(rand.NewSource(20261005))
	feasible := 0
	for trial := 0; trial < 3000; trial++ {
		in := randomInput(r)
		got, ok := Solve(in)
		want, wantOK := bruteForce(in)
		if ok != wantOK || !slices.Equal(got, want) {
			t.Fatalf("trial %d: Solve = %v %v, brute force = %v %v", trial, got, ok, want, wantOK)
		}
		if ok {
			feasible++
		}
	}
	if feasible < 1000 {
		t.Errorf("only %d feasible instances; the generator is too tight", feasible)
	}
}

func TestSolveObjectiveOrder(t *testing.T) {
	// One booking: staying (rank 3, 2 unused) beats moving to a perfect fit (rank 0, 0 unused).
	in := Input{Candidates: [][]Candidate{{
		{Rank: 0, Tables: []string{"a"}, Unused: 0, Changed: true},
		{Rank: 3, Tables: []string{"d"}, Unused: 2, Changed: false},
	}}, Overlap: [][]bool{{false}}}
	if got, _ := Solve(in); got[0] != 1 {
		t.Errorf("fewest changes first: got %v", got)
	}
	// Two overlapping bookings that must both move: fewer unused seats beats lower ranks.
	in = Input{Candidates: [][]Candidate{
		{{Rank: 0, Tables: []string{"a"}, Unused: 2, Changed: true}, {Rank: 1, Tables: []string{"b"}, Unused: 0, Changed: true}},
		{{Rank: 0, Tables: []string{"a"}, Unused: 0, Changed: true}, {Rank: 1, Tables: []string{"b"}, Unused: 0, Changed: true}},
	}, Overlap: [][]bool{{false, true}, {true, false}}}
	if got, _ := Solve(in); !slices.Equal(got, []int{1, 0}) {
		t.Errorf("unused seats second: got %v", got)
	}
	// Equal totals: the rank vector in reference order decides.
	in.Candidates[0][0].Unused = 0
	if got, _ := Solve(in); !slices.Equal(got, []int{0, 1}) {
		t.Errorf("ranks third: got %v", got)
	}
	// Infeasible: two overlapping bookings, one table.
	in = Input{Candidates: [][]Candidate{{{Tables: []string{"a"}}}, {{Tables: []string{"a"}}}},
		Overlap: [][]bool{{false, true}, {true, false}}}
	if _, ok := Solve(in); ok {
		t.Error("expected no feasible plan")
	}
}

func TestSolveWorstCaseIsFast(t *testing.T) {
	// 6 mutually non-overlapping bookings, 10 equally good options each: 10^6 combinations.
	in := Input{Candidates: make([][]Candidate, 6), Overlap: make([][]bool, 6)}
	for i := range in.Candidates {
		in.Overlap[i] = make([]bool, 6)
		for rank := 9; rank >= 0; rank-- {
			in.Candidates[i] = append(in.Candidates[i], Candidate{Rank: rank, Tables: []string{fmt.Sprint(rank)}})
		}
	}
	start := time.Now()
	got, ok := Solve(in)
	if !ok || !slices.Equal(got, []int{9, 9, 9, 9, 9, 9}) { // index 9 holds rank 0
		t.Fatalf("got %v %v", got, ok)
	}
	if d := time.Since(start); d > 100*time.Millisecond {
		t.Errorf("worst case took %v", d)
	}
}
