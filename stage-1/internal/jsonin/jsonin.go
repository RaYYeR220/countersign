// Package jsonin parses JSON request bodies and reads their fields with the error rules of §5:
// an unparseable body or a field of the wrong JSON type is 400 malformed_request, an absent
// required field or an out-of-range value is 422 validation_failed. JSON null is a value of the
// wrong type, never an omission (R-2).
package jsonin

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"strconv"

	"tablekeeper/internal/apperr"
)

// Object is a parsed JSON object whose members are decoded on demand.
type Object map[string]json.RawMessage

// Decode parses r as exactly one JSON object followed only by whitespace.
func Decode(r io.Reader) (Object, error) {
	dec := json.NewDecoder(r)
	var obj Object
	if err := dec.Decode(&obj); err != nil || obj == nil {
		return nil, apperr.Malformed("request body must be a JSON object")
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, apperr.Malformed("unexpected data after the JSON object")
	}
	return obj, nil
}

// Kind of a raw JSON value, from its first byte.
type Kind byte

const (
	Absent Kind = iota
	Null
	String
	Number
	Bool
	Array
	ObjectKind
)

// Kind reports the JSON kind of member name; a missing member is Absent.
func (o Object) Kind(name string) Kind {
	raw := bytes.TrimSpace(o[name])
	if len(raw) == 0 {
		return Absent
	}
	switch raw[0] {
	case 'n':
		return Null
	case '"':
		return String
	case 't', 'f':
		return Bool
	case '[':
		return Array
	case '{':
		return ObjectKind
	default:
		return Number
	}
}

// Has reports whether member name is present, null included.
func (o Object) Has(name string) bool { return o.Kind(name) != Absent }

func wrongType(name, want string) error {
	return apperr.Malformed(name + " must be " + want)
}

// String returns member name as a string; ok is false when it is absent.
func (o Object) String(name string) (s string, ok bool, err error) {
	switch o.Kind(name) {
	case Absent:
		return "", false, nil
	case String:
		if err := json.Unmarshal(o[name], &s); err != nil {
			return "", false, wrongType(name, "a string")
		}
		return s, true, nil
	default:
		return "", false, wrongType(name, "a string")
	}
}

// RequiredString is String with absence reported as 422.
func (o Object) RequiredString(name string) (string, error) {
	s, ok, err := o.String(name)
	if err == nil && !ok {
		err = apperr.Validation(name + " is required")
	}
	return s, err
}

// maxExactInt bounds integers to those a float64 represents without rounding neighbours together.
const maxExactInt = 1 << 53

// Int returns member name as an integer. A non-number is 400; a number that is not a whole
// value or whose magnitude reaches 2^53 is 422. ok is false when the member is absent.
func (o Object) Int(name string) (n int64, ok bool, err error) {
	switch o.Kind(name) {
	case Absent:
		return 0, false, nil
	case Number:
		f, perr := strconv.ParseFloat(string(bytes.TrimSpace(o[name])), 64)
		if perr != nil || math.Abs(f) >= maxExactInt {
			return 0, false, apperr.Validation(name + " is out of range")
		}
		if f != math.Trunc(f) {
			return 0, false, apperr.Validation(name + " must be an integer")
		}
		return int64(f), true, nil
	default:
		return 0, false, wrongType(name, "a number")
	}
}

// RequiredInt is Int with absence reported as 422.
func (o Object) RequiredInt(name string) (int64, error) {
	n, ok, err := o.Int(name)
	if err == nil && !ok {
		err = apperr.Validation(name + " is required")
	}
	return n, err
}

// Objects returns member name as an array of objects; ok is false when it is absent.
func (o Object) Objects(name string) (items []Object, ok bool, err error) {
	switch o.Kind(name) {
	case Absent:
		return nil, false, nil
	case Array:
		var raws []json.RawMessage
		if err := json.Unmarshal(o[name], &raws); err != nil {
			return nil, false, wrongType(name, "an array of objects")
		}
		items = make([]Object, len(raws))
		for i, raw := range raws {
			if err := json.Unmarshal(raw, &items[i]); err != nil || items[i] == nil {
				return nil, false, wrongType(name, "an array of objects")
			}
		}
		return items, true, nil
	default:
		return nil, false, wrongType(name, "an array of objects")
	}
}
