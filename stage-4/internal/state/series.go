package state

// Occurrence is one booking of a series; its index is its position in Series.Occurrences.
type Occurrence struct {
	Reference string `json:"reference"`
	Exception bool   `json:"exception"` // set for good by a real individual change
}

// Series is a recurring agreement adopted from an anchor reservation (occurrence 0).
type Series struct {
	ID            string       `json:"id"`
	UserID        string       `json:"user_id"`
	Revision      int          `json:"revision"`
	IntervalWeeks int          `json:"interval_weeks"`
	Occurrences   []Occurrence `json:"occurrences"`
}

// SeriesOf returns the series res belongs to, or nil.
func (st *State) SeriesOf(res *Reservation) *Series {
	if res.SeriesID == "" {
		return nil
	}
	return st.Series[res.SeriesID]
}

// Occurrence returns the series entry for reference, or nil.
func (s *Series) Occurrence(reference string) *Occurrence {
	for i := range s.Occurrences {
		if s.Occurrences[i].Reference == reference {
			return &s.Occurrences[i]
		}
	}
	return nil
}
