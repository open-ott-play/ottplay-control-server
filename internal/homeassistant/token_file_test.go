package homeassistant

import (
	"io"
	"os"
	"testing"
)

func TestTokenFileReplacementWhileReaderIsOpen(t *testing.T) {
	path := makeToken(t, fixtureToken)
	f, err := openTokenFile(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rotated := fixtureToken + "_rotated"
	if err := os.WriteFile(path+".new", []byte(rotated), 0600); err != nil {
		t.Fatal(err)
	}
	// Keep the reader open deliberately: this must not depend on winning a
	// timing race between the rejected-credential poll and the token writer.
	if err := os.Rename(path+".new", path); err != nil {
		t.Fatal(err)
	}
	old, err := io.ReadAll(f)
	if err != nil || string(old) != fixtureToken+"\n" {
		t.Fatal("open reader did not retain the complete original token")
	}
	if next, err := readToken(path); err != nil || next != rotated {
		t.Fatal("next poll did not read the complete replacement token")
	}
}
