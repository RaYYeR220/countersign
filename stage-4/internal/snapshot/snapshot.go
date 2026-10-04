// Package snapshot is the versioned export/import format of the whole service state (§10, ADR-001 §6).
//
// Envelope: {"track":"tablekeeper","format_version":1,"state":{"schema":N, <State fields>}}.
// format_version is the specification's envelope version; schema is this service's own state
// version. Import migrates schema 1 → … → Current step by step, so every later stage keeps reading
// every earlier export: never remove a migration, only append one when State changes shape.
package snapshot

import (
	"bytes"
	"encoding/json"
	"errors"
	"reflect"
	"strings"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/state"
)

const (
	Track         = "tablekeeper"
	FormatVersion = 1
	// Current is the schema written by Export. Schemas 1 and 2 are the stage-1 and stage-2
	// services' states.
	Current = 3
)

// migrations[n] turns a schema-n state object into a schema-(n+1) one.
var migrations = map[int]func(map[string]json.RawMessage) (map[string]json.RawMessage, error){
	1: schema1to2,
	2: schema2to3,
}

// schema1to2 (stage 1 → stage 2): a reservation's table_id becomes the one-member set
// table_ids, and every restaurant gains an empty combinable list. Users, tokens, references,
// timestamps and receipts (with their original response bytes) are carried over unchanged.
func schema1to2(raw map[string]json.RawMessage) (map[string]json.RawMessage, error) {
	var reservations []map[string]json.RawMessage
	if err := json.Unmarshal(raw["reservations"], &reservations); err != nil {
		return nil, err
	}
	for _, res := range reservations {
		tableID, ok := res["table_id"]
		if res == nil || !ok {
			return nil, errors.New("a schema-1 reservation has no table_id")
		}
		res["table_ids"] = json.RawMessage("[" + string(tableID) + "]")
		delete(res, "table_id")
	}
	var restaurants []map[string]json.RawMessage
	if err := json.Unmarshal(raw["restaurants"], &restaurants); err != nil {
		return nil, err
	}
	for _, r := range restaurants {
		if r == nil {
			return nil, errors.New("a schema-1 restaurant is null")
		}
		if _, ok := r["combinable"]; !ok {
			r["combinable"] = json.RawMessage("[]")
		}
	}
	var err error
	if raw["reservations"], err = json.Marshal(reservations); err != nil {
		return nil, err
	}
	if raw["restaurants"], err = json.Marshal(restaurants); err != nil {
		return nil, err
	}
	raw["schema"] = json.RawMessage("2")
	return raw, nil
}

// Export serialises st. Call it under the store's read lock: the returned bytes are a complete,
// immutable snapshot that later writes cannot change.
func Export(st *state.State) ([]byte, error) {
	fields := map[string]any{"schema": Current}
	v := reflect.ValueOf(st).Elem()
	for _, f := range persisted() {
		fv := v.Field(f.index)
		switch {
		case fv.Kind() == reflect.Slice && fv.IsNil():
			fields[f.name] = []any{}
		case fv.Kind() == reflect.Map && fv.IsNil():
			fields[f.name] = map[string]any{}
		default:
			fields[f.name] = fv.Interface()
		}
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	err := enc.Encode(map[string]any{"track": Track, "format_version": FormatVersion, "state": fields})
	return buf.Bytes(), err
}

// Import validates an export envelope and decodes it into a complete new state with its indexes
// built. It never touches the live state: the caller swaps the result in. Any problem with the
// envelope or the state is 422 validation_failed (R-24).
func Import(env jsonin.Object) (*state.State, error) {
	if track, ok := stringValue(env["track"]); !ok || track != Track {
		return nil, apperr.Validation(`track must be "tablekeeper"`)
	}
	if !numberIs(env["format_version"], FormatVersion) {
		return nil, apperr.Validation("format_version must be 1")
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(env["state"], &raw); err != nil || raw == nil {
		return nil, apperr.Validation("state must be an object produced by export")
	}
	schema := 0
	for n := 1; n <= Current; n++ {
		if numberIs(raw["schema"], n) {
			schema = n
		}
	}
	if schema == 0 {
		return nil, apperr.Validation("state has an unknown schema")
	}
	for ; schema < Current; schema++ {
		var err error
		if raw, err = migrations[schema](raw); err != nil {
			return nil, apperr.Validation("state could not be migrated: " + err.Error())
		}
	}

	st := &state.State{}
	v := reflect.ValueOf(st).Elem()
	for _, f := range persisted() {
		member := bytes.TrimSpace(raw[f.name])
		if len(member) == 0 || !kindMatches(member[0], v.Field(f.index).Kind()) {
			return nil, apperr.Validation("state." + f.name + " is missing or of the wrong type")
		}
		dec := json.NewDecoder(bytes.NewReader(member))
		dec.DisallowUnknownFields()
		if err := dec.Decode(v.Field(f.index).Addr().Interface()); err != nil {
			return nil, apperr.Validation("state." + f.name + " is invalid")
		}
	}
	if err := state.Rebuild(st); err != nil {
		return nil, err
	}
	return st, nil
}

type field struct {
	index int
	name  string
}

// persisted lists State's exported, JSON-tagged fields: they are the persisted state.
func persisted() []field {
	var out []field
	t := reflect.TypeOf(state.State{})
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		name, _, _ := strings.Cut(f.Tag.Get("json"), ",")
		if !f.IsExported() || name == "" || name == "-" {
			continue
		}
		out = append(out, field{i, name})
	}
	return out
}

func kindMatches(first byte, k reflect.Kind) bool {
	switch k {
	case reflect.Slice:
		return first == '['
	case reflect.Map, reflect.Struct, reflect.Pointer:
		return first == '{'
	case reflect.String:
		return first == '"'
	case reflect.Bool:
		return first == 't' || first == 'f'
	default:
		return first == '-' || (first >= '0' && first <= '9')
	}
}

func stringValue(raw json.RawMessage) (string, bool) {
	var s string
	if err := json.Unmarshal(raw, &s); err != nil || len(bytes.TrimSpace(raw)) == 0 || raw[0] != '"' {
		return "", false
	}
	return s, true
}

func numberIs(raw json.RawMessage, n int) bool {
	var f float64
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || !(trimmed[0] == '-' || (trimmed[0] >= '0' && trimmed[0] <= '9')) {
		return false
	}
	return json.Unmarshal(trimmed, &f) == nil && f == float64(n)
}
