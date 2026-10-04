package state

import "time"

// History events.
const (
	EventCreated   = "created"
	EventChanged   = "changed"
	EventCancelled = "cancelled"
)

// FieldChange is one field's change in a history entry; From is nil on creation.
type FieldChange struct {
	Field string `json:"field"`
	From  any    `json:"from"`
	To    any    `json:"to"`
}

// HistoryEntry is one event in a reservation's record, with the resulting revision and terms.
type HistoryEntry struct {
	Seq           int           `json:"seq"`
	At            time.Time     `json:"at"`
	Event         string        `json:"event"`
	Changes       []FieldChange `json:"changes"`
	Revision      int           `json:"revision"`
	AcceptedTerms Terms         `json:"accepted_terms"`
}

// Record appends an event carrying the reservation's current revision and terms.
func (res *Reservation) Record(at time.Time, event string, changes []FieldChange) {
	if changes == nil {
		changes = []FieldChange{}
	}
	res.History = append(res.History, HistoryEntry{
		Seq:           len(res.History) + 1,
		At:            Stamp(at),
		Event:         event,
		Changes:       changes,
		Revision:      res.Revision,
		AcceptedTerms: res.Terms,
	})
}

// TableChange describes a table-set change: table_id between single tables, table_ids (complete
// lists) whenever a pair is involved. from is nil on creation.
func TableChange(from, to []string) FieldChange {
	if len(to) == 1 && (from == nil || len(from) == 1) {
		var f any
		if from != nil {
			f = from[0]
		}
		return FieldChange{Field: "table_id", From: f, To: to[0]}
	}
	var f any
	if from != nil {
		f = from
	}
	return FieldChange{Field: "table_ids", From: f, To: to}
}

// CreatedChanges lists all three fields of a new booking, each from null.
func (res *Reservation) CreatedChanges() []FieldChange {
	return []FieldChange{
		TableChange(nil, res.TableIDs),
		{Field: "starts_at_local", From: nil, To: res.StartsAtLocal},
		{Field: "party_size", From: nil, To: res.PartySize},
	}
}
