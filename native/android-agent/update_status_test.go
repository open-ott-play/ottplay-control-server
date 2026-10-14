package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const testPackageDump = "Packages:\n  Package [play.ott.kitkat] (a):\n    versionCode=9 targetSdk=19\n    versionName=0.9\n    User 0: installed=true blocked=false stopped=true notLaunched=false enabled=2\n"

func TestUpdateStatusParsers(t *testing.T) {
	for _, raw := range []string{"package:/data/app/play.ott.kitkat-1.apk\n", "package:/system/app/play.ott.kitkat.apk\r\n"} {
		if _, err := parseInstalledAPKPath([]byte(raw)); err != nil {
			t.Fatal(err)
		}
	}
	for _, raw := range []string{"package:/data/app/other-1.apk", "package:/data/app/../private", "package:/data/app/play.ott.kitkat-1.apk\npackage:/data/app/play.ott.kitkat-2.apk", "package:/data/app/play.ott.kitkat-1.apk\x00", strings.Repeat("x", 1025)} {
		if _, err := parseInstalledAPKPath([]byte(raw)); err == nil {
			t.Fatalf("untrusted path accepted: %q", raw)
		}
	}
	name, code, err := parseAPKVersion([]byte(testPackageDump))
	if err != nil || name != "0.9" || code != 9 {
		t.Fatal("version metadata unavailable", err)
	}
	for _, raw := range []string{testPackageDump + "versionName=0.9\n", strings.ReplaceAll(testPackageDump, "versionCode=9", "versionCode=2147483648"), strings.ReplaceAll(testPackageDump, "versionName=0.9", "versionName=secret token"), "versionCode=9\n"} {
		if _, _, err := parseAPKVersion([]byte(raw)); err == nil {
			t.Fatal("ambiguous version accepted")
		}
	}
	for _, code := range []string{"0", "1", "2", "3", "4"} {
		enabled, err := parseAPKEnabled([]byte(strings.ReplaceAll(testPackageDump, "enabled=2", "enabled="+code)))
		if err != nil || enabled != (code == "0" || code == "1") {
			t.Fatalf("incorrect enabled code %s", code)
		}
	}
	for _, raw := range []string{testPackageDump + "User 0: installed=true enabled=0\n", "User 10: installed=true enabled=0", "User 0: enabled=0", "User 0: installed=true enabled=9", "User 0: installed=true enabled=0 hidden=unknown"} {
		if _, err := parseAPKEnabled([]byte(raw)); err == nil {
			t.Fatal("ambiguous user state accepted")
		}
	}
	for _, raw := range []string{"User 0: installed=false enabled=0", "User 0: installed=true hidden=true enabled=0", "User 0: installed=true blocked=true enabled=1"} {
		if enabled, err := parseAPKEnabled([]byte(raw)); err != nil || enabled {
			t.Fatal("unavailable package marked enabled")
		}
	}
}

func TestUpdateFileHashBoundsAndIdentity(t *testing.T) {
	path := filepath.Join(t.TempDir(), "artifact")
	raw := []byte("reviewed OTA artifact")
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	sum, err := hashUpdateFile(context.Background(), path, int64(len(raw)))
	if err != nil || sum != digest(raw) {
		t.Fatal("wrong hash", err)
	}
	if _, err := hashUpdateFile(context.Background(), path, int64(len(raw)-1)); err == nil {
		t.Fatal("oversized file accepted")
	}
	symlink := path + ".link"
	if err := os.Symlink(path, symlink); err != nil {
		t.Fatal(err)
	}
	if _, err := hashUpdateFile(context.Background(), symlink, 1000); err == nil {
		t.Fatal("symlink accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := hashUpdateFile(ctx, path, 1000); err == nil {
		t.Fatal("cancelled hash succeeded")
	}
}

func TestUpdateStatusIsReadOnlyAndPreservesDisabledState(t *testing.T) {
	commands := 0
	hashes := 0
	probe := updateStatusProbe{
		command: func(_ context.Context, name string, args ...string) ([]byte, error) {
			commands++
			if strings.Join(args, " ") == "path "+packageName && name == "/system/bin/pm" {
				return []byte("package:/data/app/play.ott.kitkat-1.apk\n"), nil
			}
			if strings.Join(args, " ") == "package "+packageName && name == "/system/bin/dumpsys" {
				return []byte(testPackageDump), nil
			}
			t.Fatal("non-read-only command", name, args)
			return nil, errors.New("unsupported")
		},
		hash: func(_ context.Context, path string, limit int64) (string, error) {
			hashes++
			if path != binaryPath && path != "/data/app/play.ott.kitkat-1.apk" && path != "/system/bin/ott-touch-guard" {
				t.Fatal("untrusted path")
			}
			if limit > updateFileLimit {
				t.Fatal("unbounded read")
			}
			return strings.Repeat("a", 64), nil
		},
	}
	status := collectUpdateStatus(context.Background(), probe)
	apk := status["apk"].(map[string]any)
	if apk["state"] != "ready" || apk["app_state"] != "disabled" || apk["version_code"] != int64(9) || commands != 4 || hashes != 3 {
		t.Fatal(status, commands, hashes)
	}
	raw, _ := json.Marshal(status)
	if strings.Contains(string(raw), "/") || strings.Contains(string(raw), "stopped") {
		t.Fatal("raw diagnostic leaked", string(raw))
	}
}

func TestUpdateStatusRejectsReplacementAndRedactsFailures(t *testing.T) {
	reads := 0
	probe := updateStatusProbe{
		command: func(_ context.Context, name string, args ...string) ([]byte, error) {
			if name == "/system/bin/pm" {
				return []byte("package:/data/app/play.ott.kitkat-1.apk"), nil
			}
			reads++
			raw := testPackageDump
			if reads == 2 {
				raw = strings.ReplaceAll(raw, "versionCode=9", "versionCode=10")
			}
			return []byte(raw), nil
		},
		hash: func(context.Context, string, int64) (string, error) { return strings.Repeat("a", 64), nil },
	}
	status := collectUpdateStatus(context.Background(), probe)
	if status["apk"].(map[string]any)["reason"] != "package_changed" {
		t.Fatal("replacement accepted", status)
	}
	probe.command = func(context.Context, string, ...string) ([]byte, error) {
		return nil, errors.New("SECRET /private/path")
	}
	probe.hash = func(context.Context, string, int64) (string, error) { return "", errors.New("SECRET /private/path") }
	raw, _ := json.Marshal(collectUpdateStatus(context.Background(), probe))
	if strings.Contains(string(raw), "SECRET") || strings.Contains(string(raw), "/private") {
		t.Fatal("failure leaked", string(raw))
	}
}
