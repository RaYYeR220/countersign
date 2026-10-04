package api

import (
	"net/http"
	"slices"
	"unicode/utf8"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/state"
)

// A booking names its tables with table_id (a set of one) or table_ids (one table or a declared
// pair), never both (stage 2).

func combinationNotAllowed(msg string) error {
	return apperr.New(http.StatusUnprocessableEntity, "combination_not_allowed", msg)
}

// tableFieldTypes is the wrong-type pass for the table fields: alone, table_id must be a string
// and table_ids an array of strings (400). When both are present neither is type-checked:
// requestedTables reports 422 whatever their types (R-43).
func tableFieldTypes(o jsonin.Object) error {
	switch {
	case bothTableFields(o):
		return nil
	case o.Has("table_id") && o.Kind("table_id") != jsonin.String:
		return apperr.Malformed("table_id must be a string")
	}
	_, _, err := o.Strings("table_ids")
	return err
}

// bothTableFields reports whether table_id and table_ids are both present, null included.
func bothTableFields(o jsonin.Object) bool { return o.Has("table_id") && o.Has("table_ids") }

// hasTables reports whether either table field is present.
func hasTables(o jsonin.Object) bool { return o.Has("table_id") || o.Has("table_ids") }

// validBodyID reports whether a body id is 1 to 64 characters (C1.18, R-42).
func validBodyID(id string) bool {
	return id != "" && utf8.RuneCountInString(id) <= state.MaxIDLength
}

func invalidID(field string) error {
	return apperr.Validation(field + " must be 1 to 64 characters")
}

// requestedTables is the value pass over the table fields in R-35/R-42 order: both present, an
// empty set, an empty or over-long id, or a duplicate id → 422 validation_failed; more than two
// tables → 422 combination_not_allowed.
// ok is false when neither field is present.
func requestedTables(o jsonin.Object) (ids []string, ok bool, err error) {
	if bothTableFields(o) {
		return nil, false, apperr.Validation("send table_id or table_ids, not both")
	}
	single, hasSingle, _ := o.String("table_id")
	ids, hasSet, _ := o.Strings("table_ids")
	switch {
	case hasSingle && !validBodyID(single):
		return nil, false, invalidID("table_id")
	case hasSingle:
		return []string{single}, true, nil
	case !hasSet:
		return nil, false, nil
	case len(ids) == 0:
		return nil, false, apperr.Validation("table_ids must name at least one table")
	case slices.ContainsFunc(ids, func(id string) bool { return !validBodyID(id) }):
		return nil, false, invalidID("every table_ids member")
	}
	for i, id := range ids {
		if slices.Contains(ids[:i], id) {
			return nil, false, apperr.Validation("table_ids must not repeat a table")
		}
	}
	if len(ids) > 2 {
		return nil, false, combinationNotAllowed("at most two tables can be combined")
	}
	return ids, true, nil
}

// resolveTables checks a requested set against the restaurant: an unknown table or one of
// another restaurant → 404; a pair that is not declared combinable → 422 combination_not_allowed.
// A pair is returned in combinable order.
func resolveTables(rest *state.Restaurant, ids []string) ([]string, error) {
	for _, id := range ids {
		if rest.Table(id) == nil {
			return nil, apperr.NotFound("no such table in this restaurant")
		}
	}
	if len(ids) == 1 {
		return ids, nil
	}
	pair := rest.Pair(ids[0], ids[1])
	if pair == nil {
		return nil, combinationNotAllowed("these tables are not offered together")
	}
	return slices.Clone(pair), nil
}

// sameTables reports whether a and b name the same set of tables.
func sameTables(a, b []string) bool {
	return len(a) == len(b) && !slices.ContainsFunc(a, func(id string) bool { return !slices.Contains(b, id) })
}
