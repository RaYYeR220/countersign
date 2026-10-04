package password

import (
	"strings"
	"testing"
)

func TestHashVerify(t *testing.T) {
	long := strings.Repeat("x", 100)
	hashes, err := HashAll([]string{"correct horse", long, long + "y"})
	if err != nil {
		t.Fatal(err)
	}
	if hashes[0] == "correct horse" || !strings.HasPrefix(hashes[0], "$2") {
		t.Errorf("hash is not bcrypt: %q", hashes[0])
	}
	if !Verify(hashes[0], "correct horse") || Verify(hashes[0], "correct horsE") {
		t.Error("short password verification wrong")
	}
	if !Verify(hashes[1], long) || Verify(hashes[1], long+"y") || !Verify(hashes[2], long+"y") {
		t.Error("passwords beyond 72 bytes must be distinguished")
	}
}
