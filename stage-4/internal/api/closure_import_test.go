package api

import (
	"encoding/json"
	"testing"
)

// R-77: an imported closure, or a stored plan's closure, needs a table of its restaurant and two
// non-null RFC 3339 bounds with from < to; anything else is refused with the destination intact.
func TestImportValidatesClosureBounds(t *testing.T) {
	e := newReplanEnv(t)
	e.mustBook(e.ada, "a", "t_2", "2026-09-24T19:00", 3)
	_, p := e.preview("p", closeT2)
	expect(t, e.applyPlan("ap", p.PlanID), 201, "")
	good := do(e.h, "GET", "/_test/export", "").Body.String()

	edits := map[string]func(c map[string]any){
		"from null":       func(c map[string]any) { c["from"] = nil },
		"to null":         func(c map[string]any) { c["to"] = nil },
		"from == to":      func(c map[string]any) { c["from"] = c["to"] },
		"from > to":       func(c map[string]any) { c["from"], c["to"] = c["to"], c["from"] },
		"unknown table":   func(c map[string]any) { c["table_id"] = "t_9" },
		"numeric bound":   func(c map[string]any) { c["from"] = 5 },
		"missing bound":   func(c map[string]any) { delete(c, "to") },
		"zero time bound": func(c map[string]any) { c["from"] = "0001-01-01T00:00:00Z" },
	}
	targets := map[string]func(st map[string]any) map[string]any{
		"restaurant closure": func(st map[string]any) map[string]any {
			for _, r := range st["restaurants"].([]any) {
				if r := r.(map[string]any); r["id"] == "r_anker" {
					return r["closures"].([]any)[0].(map[string]any)
				}
			}
			return nil
		},
		"plan closure": func(st map[string]any) map[string]any {
			return st["plans"].(map[string]any)[p.PlanID].(map[string]any)["closure"].(map[string]any)
		},
	}
	for tname, target := range targets {
		for ename, edit := range edits {
			var env map[string]any
			json.Unmarshal([]byte(good), &env)
			edit(target(env["state"].(map[string]any)))
			body, _ := json.Marshal(env)
			rec := do(e.h, "POST", "/_test/import", string(body))
			if rec.Code != 422 || errorCode(t, rec) != "validation_failed" {
				t.Errorf("%s / %s: %d %s", tname, ename, rec.Code, rec.Body)
			}
			if now := do(e.h, "GET", "/_test/export", "").Body.String(); now != good {
				t.Fatalf("%s / %s changed the destination", tname, ename)
			}
		}
	}
	if rec := do(e.h, "POST", "/_test/import", good); rec.Code != 204 {
		t.Errorf("untampered stage-4 export = %d %s", rec.Code, rec.Body)
	}
}
