package snapshot

import (
	"encoding/json"
	"errors"

	"tablekeeper/internal/state"
)

// schema2to3 (stage 2 → stage 3): restaurants gain no managers, no published policies and
// revision 0; every reservation gains revision 1 under its restaurant's policy 0, the matching
// accepted terms, a history holding its created entry (at its created_at) and no series; the
// state gains an empty series collection. Everything else, receipts included, is unchanged.
func schema2to3(raw map[string]json.RawMessage) (map[string]json.RawMessage, error) {
	var restaurants []map[string]json.RawMessage
	if err := json.Unmarshal(raw["restaurants"], &restaurants); err != nil {
		return nil, err
	}
	policy0 := map[string]state.Terms{}
	for _, r := range restaurants {
		if r == nil {
			return nil, errors.New("a schema-2 restaurant is null")
		}
		var rest state.Restaurant
		if err := json.Unmarshal(mustJSON(r), &rest); err != nil {
			return nil, err
		}
		policy0[rest.ID] = rest.Policy0().Terms
		r["manager_user_ids"] = json.RawMessage("[]")
		r["policies"] = json.RawMessage("[]")
		r["revision"] = json.RawMessage("0")
	}

	var reservations []map[string]json.RawMessage
	if err := json.Unmarshal(raw["reservations"], &reservations); err != nil {
		return nil, err
	}
	for _, r := range reservations {
		if r == nil {
			return nil, errors.New("a schema-2 reservation is null")
		}
		var res state.Reservation
		if err := json.Unmarshal(mustJSON(r), &res); err != nil {
			return nil, err
		}
		terms, ok := policy0[res.RestaurantID]
		if !ok {
			return nil, errors.New("a schema-2 reservation names an unknown restaurant")
		}
		res.Revision, res.Terms = 1, terms
		res.Record(res.CreatedAt, state.EventCreated, res.CreatedChanges())
		r["revision"] = json.RawMessage("1")
		r["accepted_terms"] = mustJSON(res.Terms)
		r["history"] = mustJSON(res.History)
		r["series_id"] = json.RawMessage(`""`)
	}

	raw["restaurants"] = mustJSON(restaurants)
	raw["reservations"] = mustJSON(reservations)
	raw["series"] = json.RawMessage("{}")
	raw["schema"] = json.RawMessage("3")
	return raw, nil
}

// mustJSON encodes values that are already valid JSON structures; it cannot fail.
func mustJSON(v any) json.RawMessage {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

// schema3to4 (stage 3 → stage 4): restaurants gain no closures and the state gains no plans.
// Series, histories, revisions (restaurant revisions included) and receipts are unchanged.
func schema3to4(raw map[string]json.RawMessage) (map[string]json.RawMessage, error) {
	var restaurants []map[string]json.RawMessage
	if err := json.Unmarshal(raw["restaurants"], &restaurants); err != nil {
		return nil, err
	}
	for _, r := range restaurants {
		if r == nil {
			return nil, errors.New("a schema-3 restaurant is null")
		}
		r["closures"] = json.RawMessage("[]")
	}
	raw["restaurants"] = mustJSON(restaurants)
	raw["plans"] = json.RawMessage("{}")
	raw["schema"] = json.RawMessage("4")
	return raw, nil
}
