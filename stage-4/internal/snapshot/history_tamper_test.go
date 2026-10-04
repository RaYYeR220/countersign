package snapshot

import (
	"encoding/json"
	"testing"

	"tablekeeper/internal/jsonin"
)

// tamper decodes the stage-3 export, lets f edit the reservation with the given reference and
// re-encodes the envelope.
func tamper(t *testing.T, ref string, f func(res map[string]any)) jsonin.Object {
	t.Helper()
	env := loadExport(t, "stage3-export.json")
	var st map[string]any
	json.Unmarshal(env["state"], &st)
	for _, r := range st["reservations"].([]any) {
		if res := r.(map[string]any); res["reference"] == ref {
			f(res)
		}
	}
	env["state"], _ = json.Marshal(st)
	return env
}

func stage3Refs(t *testing.T) (anchor, exception, cancelled string) {
	t.Helper()
	env := loadExport(t, "stage3-export.json")
	var st struct {
		Series map[string]struct{ Occurrences []struct{ Reference string } }
	}
	json.Unmarshal(env["state"], &st)
	for _, s := range st.Series {
		return s.Occurrences[0].Reference, s.Occurrences[1].Reference, s.Occurrences[2].Reference
	}
	t.Fatal("no series in the stage-3 export")
	return
}

func entries(res map[string]any) []any { return res["history"].([]any) }

func entry(res map[string]any, i int) map[string]any { return entries(res)[i].(map[string]any) }

// appendEntry adds a well-formed-looking entry at the next seq and revision.
func appendEntry(res map[string]any, event string, changes []any) {
	h := entries(res)
	last := h[len(h)-1].(map[string]any)
	next := map[string]any{"seq": float64(len(h) + 1), "at": last["at"], "event": event, "changes": changes,
		"revision": last["revision"].(float64) + 1, "accepted_terms": last["accepted_terms"]}
	res["history"] = append(h, next)
	res["revision"] = next["revision"]
}

func TestImportRefusesImpossibleHistories(t *testing.T) {
	anchor, exception, cancelled := stage3Refs(t)
	partyChange := []any{map[string]any{"field": "party_size", "from": float64(3), "to": float64(2)}}
	cases := map[string]jsonin.Object{
		"seq 1 changed":    tamper(t, anchor, func(r map[string]any) { entry(r, 0)["event"] = "changed" }),
		"seq 1 reassigned": tamper(t, anchor, func(r map[string]any) { entry(r, 0)["event"] = "reassigned"; entry(r, 0)["plan_id"] = "plan_x" }),
		"second created": tamper(t, exception, func(r map[string]any) {
			entry(r, 1)["event"] = "created"
		}),
		"entry after cancelled": tamper(t, cancelled, func(r map[string]any) { appendEntry(r, "changed", partyChange) }),
		"cancelled on a confirmed booking": tamper(t, anchor, func(r map[string]any) {
			appendEntry(r, "cancelled", []any{})
		}),
		"unknown event":          tamper(t, exception, func(r map[string]any) { entry(r, 1)["event"] = "moved" }),
		"created with a from":    tamper(t, anchor, func(r map[string]any) { entry(r, 0)["changes"].([]any)[2].(map[string]any)["from"] = float64(1) }),
		"created two fields":     tamper(t, anchor, func(r map[string]any) { entry(r, 0)["changes"] = entry(r, 0)["changes"].([]any)[:2] }),
		"changed empty":          tamper(t, exception, func(r map[string]any) { entry(r, 1)["changes"] = []any{} }),
		"cancelled with changes": tamper(t, cancelled, func(r map[string]any) { entry(r, 1)["changes"] = partyChange }),
		"revision gap": tamper(t, exception, func(r map[string]any) {
			entry(r, 1)["revision"] = float64(3)
			r["revision"] = float64(3)
		}),
		"plan_id on changed": tamper(t, exception, func(r map[string]any) { entry(r, 1)["plan_id"] = "plan_x" }),
		"seq gap":            tamper(t, exception, func(r map[string]any) { entry(r, 1)["seq"] = float64(3) }),
	}
	for name, env := range cases {
		if _, err := Import(env); err == nil {
			t.Errorf("%s: imported, want 422", name)
		}
	}
	for _, name := range []string{"stage1-export.json", "stage2-export.json", "stage3-export.json"} {
		if _, err := Import(loadExport(t, name)); err != nil {
			t.Errorf("untampered %s: %v", name, err)
		}
	}
}
