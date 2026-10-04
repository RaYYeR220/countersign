// Package password hashes and verifies passwords with bcrypt (§6: no plaintext storage).
//
// The password is first reduced with SHA-256 so that inputs longer than bcrypt's 72-byte limit
// are neither rejected nor silently truncated.
package password

import (
	"crypto/sha256"
	"encoding/base64"
	"runtime"
	"sync"

	"golang.org/x/crypto/bcrypt"
)

const cost = 10

func prehash(plain string) []byte {
	sum := sha256.Sum256([]byte(plain))
	return []byte(base64.StdEncoding.EncodeToString(sum[:]))
}

// Hash returns the bcrypt hash of plain.
func Hash(plain string) (string, error) {
	h, err := bcrypt.GenerateFromPassword(prehash(plain), cost)
	return string(h), err
}

// Verify reports whether plain matches hash.
func Verify(hash, plain string) bool {
	return bcrypt.CompareHashAndPassword([]byte(hash), prehash(plain)) == nil
}

// HashAll hashes every password, spreading the work over all CPUs.
func HashAll(plains []string) ([]string, error) {
	hashes := make([]string, len(plains))
	errs := make([]error, len(plains))
	next := make(chan int)
	var wg sync.WaitGroup
	for range runtime.GOMAXPROCS(0) {
		wg.Go(func() {
			for i := range next {
				hashes[i], errs[i] = Hash(plains[i])
			}
		})
	}
	for i := range plains {
		next <- i
	}
	close(next)
	wg.Wait()
	for _, err := range errs {
		if err != nil {
			return nil, err
		}
	}
	return hashes, nil
}
