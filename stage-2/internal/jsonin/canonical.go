package jsonin

import (
	"bytes"
	"encoding/json"
	"strconv"
)

// Canonical returns a form of the JSON document raw that is equal for equal JSON values:
// object keys sorted, insignificant whitespace dropped and numbers compared by value (§7, R-3).
func Canonical(raw []byte) (string, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return "", err
	}
	out, err := json.Marshal(normalize(v))
	return string(out), err
}

func normalize(v any) any {
	switch x := v.(type) {
	case map[string]any:
		for k, e := range x {
			x[k] = normalize(e)
		}
	case []any:
		for i, e := range x {
			x[i] = normalize(e)
		}
	case json.Number:
		if f, err := strconv.ParseFloat(string(x), 64); err == nil {
			return json.Number(strconv.FormatFloat(f, 'g', -1, 64))
		}
	}
	return v
}
