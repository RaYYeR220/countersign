package state

import (
	"crypto/rand"
	"strings"
)

// newID returns prefix plus a random suffix that is not yet taken according to taken.
func newID(prefix string, taken func(string) bool) string {
	for {
		id := prefix + strings.ToLower(rand.Text()[:12])
		if !taken(id) {
			return id
		}
	}
}

// AddUser registers a new account and returns it. The caller has checked that the email is free.
func (st *State) AddUser(email, displayName, passwordHash string) *User {
	u := &User{
		ID:           newID("u_", func(id string) bool { return st.usersByID[id] != nil }),
		Email:        email,
		DisplayName:  displayName,
		PasswordHash: passwordHash,
	}
	st.Users = append(st.Users, u)
	st.usersByID[u.ID] = u
	st.usersByEmail[EmailKey(email)] = u
	return u
}

// IssueToken creates a new bearer token for userID. Tokens never expire; a user may hold many.
func (st *State) IssueToken(userID string) string {
	token := rand.Text() + rand.Text()
	st.Tokens[token] = userID
	return token
}

// UserByToken returns the user a bearer token belongs to, or nil.
func (st *State) UserByToken(token string) *User {
	id, ok := st.Tokens[token]
	if !ok {
		return nil
	}
	return st.usersByID[id]
}
