package api

import (
	"net/http"
	"strings"
	"unicode"
	"unicode/utf8"

	"tablekeeper/internal/apperr"
	"tablekeeper/internal/jsonin"
	"tablekeeper/internal/password"
	"tablekeeper/internal/state"
)

const minPasswordLength = 8

// dummyHash keeps login timing similar for unknown emails and wrong passwords.
var dummyHash, _ = password.Hash("tablekeeper-dummy-password")

func unauthenticated(message string) error {
	return apperr.New(http.StatusUnauthorized, "unauthenticated", message)
}

// authedHandler is a handler for a protected endpoint; user is the authenticated caller.
type authedHandler func(w http.ResponseWriter, r *http.Request, user *state.User)

// authed rejects a missing, malformed or unknown bearer token with 401 before anything else (C1.36).
func (s *Server) authed(h authedHandler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		scheme, token, ok := strings.Cut(r.Header.Get("Authorization"), " ")
		var user *state.User
		if ok && strings.EqualFold(scheme, "Bearer") && token != "" {
			s.store.Read(func(st *state.State) { user = st.UserByToken(token) })
		}
		if user == nil {
			writeError(w, unauthenticated("a valid bearer token is required"))
			return
		}
		h(w, r, user)
	}
}

// stringFields reads required string fields: any wrong JSON type (400) is reported before any
// missing field (422), and missing fields in the order given (R-4).
func stringFields(o jsonin.Object, names ...string) ([]string, error) {
	for _, name := range names {
		if o.Has(name) && o.Kind(name) != jsonin.String {
			return nil, apperr.Malformed(name + " must be a string")
		}
	}
	values := make([]string, len(names))
	for i, name := range names {
		v, err := o.RequiredString(name)
		if err != nil {
			return nil, err
		}
		values[i] = v
	}
	return values, nil
}

// validEmail is the local@domain rule of R-4: exactly one @, both parts non-empty, no whitespace.
func validEmail(email string) bool {
	local, domain, ok := strings.Cut(email, "@")
	return ok && local != "" && domain != "" && !strings.Contains(domain, "@") &&
		!strings.ContainsFunc(email, unicode.IsSpace)
}

type session struct {
	UserID      string `json:"user_id"`
	DisplayName string `json:"display_name"`
	Token       string `json:"token"`
}

func emailTaken() error {
	return apperr.New(http.StatusConflict, "email_taken", "email is already registered")
}

// signup is POST /auth/signup: 400 → 422 (email, password, display_name) → 409 email_taken.
func (s *Server) signup(w http.ResponseWriter, r *http.Request) {
	body, err := readObject(w, r, maxBodyBytes)
	if err != nil {
		writeError(w, err)
		return
	}
	f, err := stringFields(body, "email", "password", "display_name")
	if err != nil {
		writeError(w, err)
		return
	}
	email, plain, name := f[0], f[1], f[2]
	switch {
	case !validEmail(email):
		err = apperr.Validation("email must be of the form local@domain")
	case utf8.RuneCountInString(plain) < minPasswordLength:
		err = apperr.Validation("password must be at least 8 characters")
	case strings.TrimSpace(name) == "":
		err = apperr.Validation("display_name must not be empty")
	}
	if err != nil {
		writeError(w, err)
		return
	}
	var taken bool
	s.store.Read(func(st *state.State) { taken = st.UserByEmail(email) != nil })
	if taken { // cheap early answer; re-checked under the write lock below
		writeError(w, emailTaken())
		return
	}
	hash, err := password.Hash(plain)
	if err != nil {
		writeError(w, err)
		return
	}
	var out session
	s.store.Write(func(st *state.State) {
		if st.UserByEmail(email) != nil {
			err = emailTaken()
			return
		}
		u := st.AddUser(email, name, hash)
		out = session{u.ID, u.DisplayName, st.IssueToken(u.ID)}
	})
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, out)
}

// login is POST /auth/login: 400 → 422 missing → 401 wrong credentials; each login adds a token.
func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	body, err := readObject(w, r, maxBodyBytes)
	if err != nil {
		writeError(w, err)
		return
	}
	f, err := stringFields(body, "email", "password")
	if err != nil {
		writeError(w, err)
		return
	}
	email, plain := f[0], f[1]
	var user *state.User
	s.store.Read(func(st *state.State) { user = st.UserByEmail(email) })
	if user == nil {
		password.Verify(dummyHash, plain)
		writeError(w, unauthenticated("invalid email or password"))
		return
	}
	if !password.Verify(user.PasswordHash, plain) {
		writeError(w, unauthenticated("invalid email or password"))
		return
	}
	var out session
	s.store.Write(func(st *state.State) {
		// A reset or import may have replaced the account since it was read.
		if st.User(user.ID) != user {
			return
		}
		out = session{user.ID, user.DisplayName, st.IssueToken(user.ID)}
	})
	if out.Token == "" {
		writeError(w, unauthenticated("invalid email or password"))
		return
	}
	writeJSON(w, http.StatusOK, out)
}
