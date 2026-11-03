package homeassistant

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"unsafe"

	"golang.org/x/sys/windows"
)

// Model a Windows credential writer that preserves the destination ACL.
// MoveFileEx (used by os.Rename) cannot replace an open destination even when
// its reader shares delete access; ReplaceFile is the supported operation.
func replaceTokenFile(source, target string) error {
	sourcep, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	targetp, err := windows.UTF16PtrFromString(target)
	if err != nil {
		return err
	}
	replace := windows.NewLazySystemDLL("kernel32.dll").NewProc("ReplaceFileW")
	ok, _, err := replace.Call(uintptr(unsafe.Pointer(targetp)), uintptr(unsafe.Pointer(sourcep)), 0, 0, 0, 0)
	runtime.KeepAlive(sourcep)
	runtime.KeepAlive(targetp)
	if ok == 0 {
		return err
	}
	return nil
}

func TestTokenFileWindowsReplacementWithoutDeleteSharing(t *testing.T) {
	path := makeToken(t, fixtureToken)
	namep, err := windows.UTF16PtrFromString(path)
	if err != nil {
		t.Fatal(err)
	}
	// Reproduce the old reader's access and sharing flags explicitly, so the
	// control remains meaningful if a future Go version changes os.Open.
	h, err := windows.CreateFile(namep, windows.GENERIC_READ,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE,
		nil, windows.OPEN_EXISTING, windows.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		t.Fatal(err)
	}
	f := os.NewFile(uintptr(h), path)
	defer f.Close()
	rotated := fixtureToken + "_rotated"
	if err := os.WriteFile(path+".new", []byte(rotated), 0600); err != nil {
		t.Fatal(err)
	}
	if err := replaceTokenFile(path+".new", path); err == nil {
		t.Fatal("replacement unexpectedly succeeded without delete sharing")
	}
	if token, err := readToken(path); err != nil || token != fixtureToken {
		t.Fatal("failed replacement changed the original token")
	}
	if token, err := readToken(path + ".new"); err != nil || token != rotated {
		t.Fatal("failed replacement changed the staged token")
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err := openTokenFile(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	if err := replaceTokenFile(path+".new", path); err != nil {
		t.Fatalf("replacement with production reader: %v", err)
	}
	if token, err := readToken(path); err != nil || token != rotated {
		t.Fatal("replacement did not publish the staged token")
	}
}

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
