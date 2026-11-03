package homeassistant

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unsafe"

	"golang.org/x/sys/windows"
)

func TestTokenFileWindowsHandleIsReadOnlyAndNotInherited(t *testing.T) {
	path := makeToken(t, fixtureToken)
	f, err := openTokenFile(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var flags uint32
	getHandleInformation := windows.NewLazySystemDLL("kernel32.dll").NewProc("GetHandleInformation")
	if ok, _, err := getHandleInformation.Call(f.Fd(), uintptr(unsafe.Pointer(&flags))); ok == 0 {
		t.Fatal(err)
	}
	if flags&windows.HANDLE_FLAG_INHERIT != 0 {
		t.Fatal("token handle is inheritable")
	}
	if _, err := f.Write([]byte("changed")); err == nil {
		t.Fatal("token handle permits writes")
	}
	if token, err := readToken(path); err != nil || token != fixtureToken {
		t.Fatal("read-only handle changed the token")
	}
}

func TestTokenFileWindowsLongAndRelativePaths(t *testing.T) {
	dir := t.TempDir()
	for len(dir) < 280 {
		dir = filepath.Join(dir, strings.Repeat("long", 10))
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	name := "token-日本語"
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte(fixtureToken), 0600); err != nil {
		t.Fatal(err)
	}
	full, err := windows.FullPath(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, candidate := range []string{path, `\\?\` + full} {
		if token, err := readToken(candidate); err != nil || token != fixtureToken {
			t.Fatal("long-path token was not read")
		}
	}
	t.Chdir(dir)
	if token, err := readToken(name); err != nil || token != fixtureToken {
		t.Fatal("relative token in long working directory was not read")
	}
}

func TestTokenFileWindowsPathKeepsNamespaces(t *testing.T) {
	long := strings.Repeat("long", 80)
	for _, path := range []string{`\\?\C:\` + long, `\\?\UNC\server\share\` + long, `\\.\C:\` + long, `\??\C:\` + long} {
		got, err := tokenFileWindowsPath(path)
		if err != nil || got != path {
			t.Fatal("explicit Windows namespace was changed")
		}
	}
	unc := `\\server\share\` + long
	if got, err := tokenFileWindowsPath(unc); err != nil || got != `\\?\UNC\server\share\`+long {
		t.Fatal("long UNC path was not extended")
	}
}
