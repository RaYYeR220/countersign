package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"log"
	"net/http"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
)

const (
	maxBodyBytes        = 1 << 20  // ordinary request bodies
	maxControlBodyBytes = 64 << 20 // reset fixtures and imports
)

func writeJSON(w http.ResponseWriter, status int, v any) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		log.Printf("encode response: %v", err)
		status = http.StatusInternalServerError
		buf.Reset()
		buf.WriteString(`{"error":{"code":"internal_error","message":"response encoding failed"}}`)
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	w.Write(buf.Bytes())
}

type errorBody struct {
	Error errorDetail `json:"error"`
}

type errorDetail struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// writeError renders err in the §5 envelope. Anything that is not an *apperr.Error is a bug
// and is reported as 500 internal_error.
func writeError(w http.ResponseWriter, err error) {
	var ae *apperr.Error
	if !errors.As(err, &ae) {
		log.Printf("internal error: %v", err)
		ae = apperr.New(http.StatusInternalServerError, "internal_error", "internal error")
	}
	writeJSON(w, ae.Status, errorBody{errorDetail{ae.Code, ae.Message}})
}

func writeNoContent(w http.ResponseWriter) { w.WriteHeader(http.StatusNoContent) }

// readObject parses the request body as one JSON object of at most limit bytes.
func readObject(w http.ResponseWriter, r *http.Request, limit int64) (jsonin.Object, error) {
	return jsonin.Decode(http.MaxBytesReader(w, r.Body, limit))
}
