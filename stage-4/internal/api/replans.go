package api

import (
	"net/http"
	"regexp"
	"time"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/localtime"
	"tablekeeper/internal/state"
)

// instantPattern is RFC 3339 with seconds and an explicit offset (Z or ±HH:MM both count, R-62).
var instantPattern = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$`)

// managedRestaurant resolves the path's restaurant for a manager: 404 unknown → 403 forbidden.
func managedRestaurant(st *state.State, r *http.Request, user *state.User) (*state.Restaurant, error) {
	rest := st.Restaurant(r.PathValue("id"))
	if rest == nil {
		return nil, apperr.NotFound("no such restaurant")
	}
	if !rest.IsManager(user.ID) {
		return nil, apperr.New(http.StatusForbidden, "forbidden", "only the restaurant's managers may change its seating")
	}
	return rest, nil
}

// instant reads from/to: any problem is 422 (R-62).
func instant(body jsonin.Object, field string) (time.Time, error) {
	s, ok, err := body.String(field)
	if err != nil || !ok || !instantPattern.MatchString(s) {
		return time.Time{}, apperr.Validation(field + " must be an RFC 3339 instant with an explicit offset")
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return time.Time{}, apperr.Validation(field + " must be an RFC 3339 instant with an explicit offset")
	}
	return t, nil
}

type closureView struct {
	TableID string `json:"table_id"`
	From    string `json:"from"`
	To      string `json:"to"`
}

type planView struct {
	PlanID             string             `json:"plan_id"`
	RestaurantRevision int                `json:"restaurant_revision"`
	Closure            closureView        `json:"closure"`
	Assignments        []state.Assignment `json:"assignments"`
	MovedCount         int                `json:"moved_count"`
	UnusedSeats        int                `json:"unused_seats"`
}

// previewReplan is POST /restaurants/{id}/replans (R-62): keyed write → 404 restaurant → 403 →
// table_id (400 type, 422 missing/empty/long) and from/to (all 422) → 404 table → 422
// planning_limit → 409 no_feasible_plan → 201. Only the plan is stored.
func (s *Server) previewReplan(w http.ResponseWriter, r *http.Request, user *state.User) {
	s.keyedWrite(w, r, user, func(st *state.State, body jsonin.Object) (any, error) {
		rest, err := managedRestaurant(st, r, user)
		if err != nil {
			return nil, err
		}
		if body.Has("table_id") && body.Kind("table_id") != jsonin.String {
			return nil, apperr.Malformed("table_id must be a string")
		}
		tableID, err := body.RequiredString("table_id")
		if err != nil {
			return nil, err
		}
		from, err := instant(body, "from")
		if err != nil {
			return nil, err
		}
		to, err := instant(body, "to")
		if err != nil {
			return nil, err
		}
		if !validBodyID(tableID) {
			return nil, invalidID("table_id")
		}
		if !from.Before(to) {
			return nil, apperr.Validation("from must be earlier than to")
		}
		if rest.Table(tableID) == nil {
			return nil, apperr.NotFound("no such table in this restaurant")
		}
		p, err := st.PlanClosure(rest, tableID, from, to)
		if err != nil {
			return nil, err
		}
		st.Plans[p.ID] = p
		loc, _ := localtime.Location(rest.Timezone)
		return planView{
			PlanID:             p.ID,
			RestaurantRevision: p.RestaurantRevision,
			Closure:            closureView{tableID, localtime.Format(from.Truncate(time.Second), loc), localtime.Format(to.Truncate(time.Second), loc)},
			Assignments:        p.Assignments,
			MovedCount:         p.MovedCount,
			UnusedSeats:        p.UnusedSeats,
		}, nil
	})
}

// applyReplan is POST /restaurants/{id}/replans/{plan_id}/apply (R-66): keyed write → 404
// restaurant → 403 → 404 plan → 409 plan_already_applied → 409 stale_plan → apply atomically.
func (s *Server) applyReplan(w http.ResponseWriter, r *http.Request, user *state.User) {
	s.keyedWrite(w, r, user, func(st *state.State, body jsonin.Object) (any, error) {
		rest, err := managedRestaurant(st, r, user)
		if err != nil {
			return nil, err
		}
		p := st.Plans[r.PathValue("plan_id")]
		switch {
		case p == nil || p.RestaurantID != rest.ID:
			return nil, apperr.NotFound("no such plan")
		case p.Applied:
			return nil, apperr.New(http.StatusConflict, "plan_already_applied", "the plan has already been applied")
		case p.RestaurantRevision != rest.Revision:
			return nil, apperr.New(http.StatusConflict, "stale_plan", "the restaurant changed since the plan was made")
		}
		moved := st.ApplyPlan(p, s.now())
		views := make([]reservationView, len(moved))
		for i, res := range moved {
			views[i] = view(st, res)
		}
		return map[string]any{"plan_id": p.ID, "restaurant_revision": rest.Revision, "reservations": views}, nil
	})
}
