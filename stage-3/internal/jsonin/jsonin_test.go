package jsonin

import (
	"errors"
	"strings"
	"testing"

	"tablekeeper/internal/apperr"
)

func code(err error) string {
	var ae *apperr.Error
	if errors.As(err, &ae) {
		return ae.Code
	}
	return ""
}

func TestDecodeRejectsNonObjects(t *testing.T) {
	for _, body := range []string{"", "   ", "{", "[]", "[{}]", "null", "42", `"x"`, "{} {}", `{"a":1}x`, `{'a':1}`} {
		if _, err := Decode(strings.NewReader(body)); code(err) != "malformed_request" {
			t.Errorf("Decode(%q) = %v, want malformed_request", body, err)
		}
	}
}

func TestDecodeAcceptsObjectWithWhitespace(t *testing.T) {
	obj, err := Decode(strings.NewReader(" {\"a\": 1, \"b\": null}\n "))
	if err != nil {
		t.Fatal(err)
	}
	if !obj.Has("a") || !obj.Has("b") || obj.Has("c") || obj.Kind("b") != Null {
		t.Errorf("Has: a=%v b=%v c=%v", obj.Has("a"), obj.Has("b"), obj.Has("c"))
	}
}

func mustDecode(t *testing.T, body string) Object {
	t.Helper()
	obj, err := Decode(strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	return obj
}

func TestString(t *testing.T) {
	obj := mustDecode(t, `{"s":"hi","n":1,"z":null}`)
	if s, ok, err := obj.String("s"); s != "hi" || !ok || err != nil {
		t.Errorf("String(s) = %q %v %v", s, ok, err)
	}
	if _, _, err := obj.String("n"); code(err) != "malformed_request" {
		t.Errorf("String(n) err = %v", err)
	}
	if _, _, err := obj.String("z"); code(err) != "malformed_request" {
		t.Errorf("String(null) err = %v, want malformed_request (R-2)", err)
	}
	if _, ok, err := obj.String("missing"); ok || err != nil {
		t.Errorf("String(missing) = %v %v, want absent", ok, err)
	}
	if _, err := obj.RequiredString("missing"); code(err) != "validation_failed" {
		t.Errorf("RequiredString(missing) err = %v", err)
	}
}

func TestInt(t *testing.T) {
	obj := mustDecode(t, `{"a":4,"b":4.0,"c":4.5,"d":"4","e":true,"f":1e300,"g":-3,"h":9007199254740993,"i":null}`)
	cases := []struct {
		field string
		want  int64
		code  string
	}{
		{"a", 4, ""},
		{"b", 4, ""},
		{"c", 0, "validation_failed"},
		{"d", 0, "malformed_request"},
		{"e", 0, "malformed_request"},
		{"f", 0, "validation_failed"},
		{"g", -3, ""},
		{"h", 0, "validation_failed"},
		{"i", 0, "malformed_request"},
	}
	for _, c := range cases {
		n, _, err := obj.Int(c.field)
		if code(err) != c.code || n != c.want {
			t.Errorf("Int(%s) = %d, %v; want %d, %q", c.field, n, err, c.want, c.code)
		}
	}
	if _, err := obj.RequiredInt("missing"); code(err) != "validation_failed" {
		t.Errorf("RequiredInt(missing) err = %v", err)
	}
}

func TestObjects(t *testing.T) {
	obj := mustDecode(t, `{"ok":[{"x":1},{}],"mixed":[{},1],"nulls":[null],"str":"x"}`)
	items, ok, err := obj.Objects("ok")
	if err != nil || !ok || len(items) != 2 || !items[0].Has("x") {
		t.Errorf("Objects(ok) = %v %v %v", items, ok, err)
	}
	for _, f := range []string{"mixed", "nulls", "str"} {
		if _, _, err := obj.Objects(f); code(err) != "malformed_request" {
			t.Errorf("Objects(%s) err = %v", f, err)
		}
	}
	if _, ok, err := obj.Objects("missing"); ok || err != nil {
		t.Errorf("Objects(missing) = %v %v", ok, err)
	}
}

func TestStrings(t *testing.T) {
	obj := mustDecode(t, `{"ok":["a","b"],"empty":[],"mixed":["a",1],"nulls":[null],"str":"a"}`)
	if l, ok, err := obj.Strings("ok"); err != nil || !ok || len(l) != 2 || l[1] != "b" {
		t.Errorf("Strings(ok) = %v %v %v", l, ok, err)
	}
	if l, ok, err := obj.Strings("empty"); err != nil || !ok || len(l) != 0 {
		t.Errorf("Strings(empty) = %v %v %v", l, ok, err)
	}
	for _, f := range []string{"mixed", "nulls", "str"} {
		if _, _, err := obj.Strings(f); code(err) != "malformed_request" {
			t.Errorf("Strings(%s) err = %v", f, err)
		}
	}
	if _, ok, err := obj.Strings("missing"); ok || err != nil {
		t.Errorf("Strings(missing) = %v %v", ok, err)
	}
	if items, ok, err := obj.Arrays("ok"); err != nil || !ok || len(items) != 2 {
		t.Errorf("Arrays(ok) = %v %v %v", items, ok, err)
	}
	if _, _, err := obj.Arrays("str"); code(err) != "malformed_request" {
		t.Errorf("Arrays(str) err = %v", err)
	}
}
