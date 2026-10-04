package api

import (
	"math"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"strconv"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/localtime"
	"tablekeeper/internal/state"
)

var digitsPattern = regexp.MustCompile(`^[0-9]+$`)

type availabilitySlot struct {
	StartsAtLocal     string            `json:"starts_at_local"`
	StartsAt          string            `json:"starts_at"`
	AvailableTableIDs []string          `json:"available_table_ids"` // single tables only
	AvailableOptions  []availableOption `json:"available_options"`   // singles, then declared pairs
	Explain           *[]tableExplain   `json:"explain,omitempty"`   // only with explain=true
}

// tableExplain says why one table is or is not available for a slot (stage 3).
type tableExplain struct {
	TableID       string     `json:"table_id"`
	PolicyVersion int        `json:"policy_version"`
	Available     bool       `json:"available"`
	Rules         []ruleHold `json:"rules"`
}

type ruleHold struct {
	Rule  string `json:"rule"`
	Holds bool   `json:"holds"`
}

// availableOption is one bookable table set with its summed capacity.
type availableOption struct {
	TableIDs []string `json:"table_ids"`
	Capacity int      `json:"capacity"`
}

type availabilityResponse struct {
	RestaurantID string             `json:"restaurant_id"`
	Date         string             `json:"date"`
	Timezone     string             `json:"timezone"`
	Slots        []availabilitySlot `json:"slots"`
}

// queryParam returns the first value of name; empty counts as missing.
func queryParam(q url.Values, name string) (string, error) {
	v := q.Get(name)
	if v == "" {
		return "", apperr.Validation(name + " is required")
	}
	return v, nil
}

// availability is GET /availability (§8, §9; R-5, R-12). Public: any Authorization header is ignored.
// Parameter errors (422, in order restaurant_id, date, party_size) precede the unknown-restaurant 404.
func (s *Server) availability(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	restaurantID, err := queryParam(q, "restaurant_id")
	if err != nil {
		writeError(w, err)
		return
	}
	date, err := queryParam(q, "date")
	if err != nil {
		writeError(w, err)
		return
	}
	if !localtime.ValidDate(date) {
		writeError(w, apperr.Validation("date must be a calendar date YYYY-MM-DD"))
		return
	}
	rawParty, err := queryParam(q, "party_size")
	if err != nil {
		writeError(w, err)
		return
	}
	if !digitsPattern.MatchString(rawParty) {
		writeError(w, apperr.Validation("party_size must be written as decimal digits"))
		return
	}
	party, perr := strconv.ParseInt(rawParty, 10, 64)
	if perr != nil {
		party = math.MaxInt64 // all digits but too large: valid, and larger than every table
	}
	if party < 1 {
		writeError(w, apperr.Validation("party_size must be at least 1"))
		return
	}
	// explain is optional; its only accepted value is "true" (first value counts, like every parameter).
	_, explain := q["explain"]
	if explain && q.Get("explain") != "true" {
		writeError(w, apperr.Validation(`explain must be "true" when given`))
		return
	}

	var resp *availabilityResponse
	s.store.Read(func(st *state.State) {
		resp, err = buildAvailability(st, restaurantID, date, party, explain)
	})
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

func buildAvailability(st *state.State, restaurantID, date string, party int64, explain bool) (*availabilityResponse, error) {
	rest := st.Restaurant(restaurantID)
	if rest == nil {
		return nil, apperr.NotFound("no such restaurant")
	}
	loc, err := localtime.Location(rest.Timezone)
	if err != nil {
		return nil, err
	}
	var booked []*state.Reservation
	for _, res := range st.Reservations {
		if res.RestaurantID == rest.ID && res.Status == state.Confirmed {
			booked = append(booked, res)
		}
	}
	// Every slot of a date starts on that local date, so one policy decides the whole response.
	p := rulesFor(st, rest, date)
	resp := &availabilityResponse{RestaurantID: rest.ID, Date: date, Timezone: rest.Timezone, Slots: []availabilitySlot{}}
	for _, slot := range localtime.Slots(loc, p.hours, p.slotMinutes, p.durationMinutes, date) {
		free := func(id string) bool { return !tableBusy(booked, id, slot) }
		ids := []string{}
		options := []availableOption{}
		var why []tableExplain
		for _, t := range rest.Tables {
			fits, open := int64(p.capacity(t.ID)) >= party, free(t.ID)
			if explain {
				why = append(why, tableExplain{t.ID, p.version, fits && open,
					[]ruleHold{{"capacity", fits}, {"no_overlap", open}}})
			}
			if !fits || !open {
				continue
			}
			ids = append(ids, t.ID)
			options = append(options, availableOption{[]string{t.ID}, p.capacity(t.ID)})
		}
		for _, pair := range rest.Combinable {
			capacity := p.capacity(pair[0]) + p.capacity(pair[1])
			if int64(capacity) >= party && free(pair[0]) && free(pair[1]) {
				options = append(options, availableOption{pair, capacity})
			}
		}
		out := availabilitySlot{
			StartsAtLocal:     slot.Local,
			StartsAt:          localtime.Format(slot.Start, loc),
			AvailableTableIDs: ids,
			AvailableOptions:  options,
		}
		if explain {
			if why == nil {
				why = []tableExplain{}
			}
			out.Explain = &why
		}
		resp.Slots = append(resp.Slots, out)
	}
	return resp, nil
}

// dayRules are the booking rules that apply to starts on one local date.
type dayRules struct {
	version                      int
	slotMinutes, durationMinutes int
	hours                        []localtime.Hours
	capacities                   map[string]int
}

func (d dayRules) capacity(tableID string) int { return d.capacities[tableID] }

// rulesFor selects the policy for a local start date. Until the policy core lands this is policy 0:
// the fixture's own rules.
func rulesFor(st *state.State, rest *state.Restaurant, date string) dayRules {
	caps := make(map[string]int, len(rest.Tables))
	for _, t := range rest.Tables {
		caps[t.ID] = t.Capacity
	}
	return dayRules{0, rest.SlotMinutes, rest.ReservationDurationMinutes, rest.OpeningHours, caps}
}

func tableBusy(booked []*state.Reservation, tableID string, slot localtime.Slot) bool {
	for _, res := range booked {
		if slices.Contains(res.TableIDs, tableID) && localtime.Overlaps(res.StartsAt, res.EndsAt, slot.Start, slot.End) {
			return true
		}
	}
	return false
}
