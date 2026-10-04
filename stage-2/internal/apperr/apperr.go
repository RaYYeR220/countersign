// Package apperr defines the client-visible failures of the API (§5 error envelope).
package apperr

import "net/http"

// Error is a failure reported to the client as an HTTP status and an error code.
type Error struct {
	Status  int
	Code    string
	Message string
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

// New returns an Error with the given status, code and message.
func New(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

// Malformed is 400 malformed_request: an unparseable body or a field of the wrong JSON type.
func Malformed(message string) *Error {
	return New(http.StatusBadRequest, "malformed_request", message)
}

// Validation is 422 validation_failed: a missing field or a value that breaks a stated rule.
func Validation(message string) *Error {
	return New(http.StatusUnprocessableEntity, "validation_failed", message)
}

// NotFound is 404 not_found.
func NotFound(message string) *Error {
	return New(http.StatusNotFound, "not_found", message)
}
