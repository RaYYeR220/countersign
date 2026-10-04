package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
)

const (
	maxBodyBytes        = 1 << 20  // ordinary request bodies
	maxControlBodyBytes = 64 << 20 // reset fixtures and imports
)

// encodeJSON renders v as a response body (HTML characters left unescaped, trailing newline).
func encodeJSON(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	err := enc.Encode(v)
	return buf.Bytes(), err
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	body, err := encodeJSON(v)
	if err != nil {
		log.Printf("encode response: %v", err)
		status = http.StatusInternalServerError
		body = []byte(`{"error":{"code":"internal_error","message":"response encoding failed"}}`)
	}
	writeRaw(w, status, body)
}

// writeRaw sends an already encoded JSON body.
func writeRaw(w http.ResponseWriter, status int, body []byte) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	w.Write(body)
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
	_, obj, err := readBody(w, r, limit)
	return obj, err
}

// readBody returns the raw request body and its parse as one JSON object of at most limit bytes.
func readBody(w http.ResponseWriter, r *http.Request, limit int64) ([]byte, jsonin.Object, error) {
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
	if err != nil {
		return nil, nil, apperr.Malformed("request body could not be read")
	}
	obj, err := jsonin.Decode(bytes.NewReader(raw))
	return raw, obj, err
}
