package api

import (
	"bytes"
	"net/http"
	"unicode/utf8"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/state"
)

const maxIdempotencyKeyLength = 255

// keyedOp performs a keyed write under the write lock and returns the 201 response value.
// It must not change st when it returns an error.
type keyedOp func(st *state.State, body jsonin.Object) (any, error)

// keyedWrite runs op as an idempotent write (§7) with the R-1 order: 400 body → 400 missing key →
// 422 key length → replay 200 / 409 reuse → op. Claiming the key, running op and storing the
// receipt happen in one write-locked step, so concurrent identical requests take effect once.
// Only successes are stored, so a key whose first use failed stays usable (R-18).
func (s *Server) keyedWrite(w http.ResponseWriter, r *http.Request, user *state.User, op keyedOp) {
	raw, body, err := readBody(w, r, maxBodyBytes)
	if err != nil {
		writeError(w, err)
		return
	}
	key := r.Header.Get("Idempotency-Key")
	if key == "" {
		writeError(w, apperr.New(http.StatusBadRequest, "missing_idempotency_key", "Idempotency-Key header is required"))
		return
	}
	if utf8.RuneCountInString(key) > maxIdempotencyKeyLength {
		writeError(w, apperr.Validation("Idempotency-Key must be 1 to 255 characters"))
		return
	}
	canonical, err := jsonin.Canonical(raw)
	if err != nil {
		writeError(w, apperr.Malformed("request body must be a JSON object"))
		return
	}
	rk := state.ReceiptKey(user.ID, r.Method, r.URL.Path, key)

	var status int
	var resp []byte
	s.store.Write(func(st *state.State) {
		if rc := st.Receipt(rk); rc != nil {
			if rc.Body != canonical {
				err = apperr.New(http.StatusConflict, "idempotency_key_reuse", "Idempotency-Key was already used with a different body")
				return
			}
			status, resp = http.StatusOK, rc.Response
			return
		}
		var out any
		if out, err = op(st, body); err != nil {
			return
		}
		if resp, err = encodeJSON(out); err != nil {
			return
		}
		resp = bytes.TrimSpace(resp) // compact form: export/import re-encodes receipts byte-identically
		status = http.StatusCreated
		st.StoreReceipt(rk, &state.Receipt{Body: canonical, Status: status, Response: resp})
	})
	if err != nil {
		writeError(w, err)
		return
	}
	writeRaw(w, status, resp)
}
