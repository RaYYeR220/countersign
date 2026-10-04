package state

import "sync"

// Store serialises all access to the state: writers run one at a time under the write lock,
// readers share the read lock and always see a state between two complete writes.
type Store struct {
	mu sync.RWMutex
	st *State
}

// NewStore returns a store holding st.
func NewStore(st *State) *Store { return &Store{st: st} }

// Read runs fn with shared access. fn must not modify the state.
func (s *Store) Read(fn func(*State)) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	fn(s.st)
}

// Write runs fn with exclusive access: validate, check, write and build the response inside fn.
func (s *Store) Write(fn func(*State)) {
	s.mu.Lock()
	defer s.mu.Unlock()
	fn(s.st)
}

// Replace swaps in a complete, already validated state (reset and import).
func (s *Store) Replace(st *State) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.st = st
}
