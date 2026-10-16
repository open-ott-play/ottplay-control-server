package main

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type bundleTestEntry struct {
	name string
	data []byte
	mode os.FileMode
}

func bundleTestZip(t *testing.T, entries ...bundleTestEntry) []byte {
	t.Helper()
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	for _, entry := range entries {
		h := zip.FileHeader{Name: entry.name, Method: zip.Deflate}
		if entry.mode != 0 {
			h.SetMode(entry.mode)
		}
		w, err := z.CreateHeader(&h)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = w.Write(entry.data); err != nil {
			t.Fatal(err)
		}
	}
	if err := z.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

func bundleTestPair(t *testing.T, tag byte) bundlePayload {
	t.Helper()
	b := make([]byte, 64)
	copy(b, "\x7fELF")
	b[4] = 1
	b[5] = 1
	b[6] = 1
	b[16] = 3
	b[18] = 40
	b[20] = 1
	b[63] = tag
	apk := bundleTestZip(t, bundleTestEntry{"AndroidManifest.xml", testManifest(packageName, true), 0600}, bundleTestEntry{"assets/ott-touch-guard", b, 0600})
	return bundlePayload{apk, b}
}

func TestBundleArchiveContract(t *testing.T) {
	p := bundleTestPair(t, 1)
	good := []bundleTestEntry{{"app.apk", p.APK, 0600}, {"ott-touch-guard", p.Guard, 0755}}
	raw := bundleTestZip(t, good...)
	got, err := parseBundleUpdate(raw)
	if err != nil || !reflect.DeepEqual(got, p) {
		t.Fatalf("valid bundle: %v", err)
	}
	for name, entries := range map[string][]bundleTestEntry{
		"duplicate":                 {good[0], good[0]},
		"path traversal":            {{"../app.apk", p.APK, 0600}, good[1]},
		"absolute path":             {{"/app.apk", p.APK, 0600}, good[1]},
		"directory":                 {{"app.apk", p.APK, os.ModeDir | 0700}, good[1]},
		"symlink":                   {{"app.apk", p.APK, os.ModeSymlink | 0700}, good[1]},
		"extra":                     {good[0], good[1], {"readme", []byte("x"), 0600}},
		"missing":                   {good[0]},
		"wrong architecture":        {good[0], {"ott-touch-guard", bytes.Repeat([]byte{0}, 64), 0755}},
		"mismatched helper":         {good[0], {"ott-touch-guard", bundleTestPair(t, 2).Guard, 0755}},
		"wrong package":             {{"app.apk", bundleTestZip(t, bundleTestEntry{"AndroidManifest.xml", testManifest("another.app", true), 0600}, bundleTestEntry{"assets/ott-touch-guard", p.Guard, 0600}), 0600}, good[1]},
		"duplicate embedded helper": {{"app.apk", bundleTestZip(t, bundleTestEntry{"AndroidManifest.xml", testManifest(packageName, true), 0600}, bundleTestEntry{"assets/ott-touch-guard", p.Guard, 0600}, bundleTestEntry{"assets/ott-touch-guard", p.Guard, 0600}), 0600}, good[1]},
		"oversize helper":           {good[0], {"ott-touch-guard", make([]byte, bundleGuardLimit+1), 0600}},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := parseBundleUpdate(bundleTestZip(t, entries...)); err == nil {
				t.Fatal("invalid archive accepted")
			}
		})
	}
	for _, b := range [][]byte{nil, []byte("bad"), raw[:len(raw)/2]} {
		if _, err := parseBundleUpdate(b); err == nil {
			t.Fatal("truncated bundle accepted")
		}
	}
}

func TestBundleRealAPKWhenProvided(t *testing.T) {
	path := os.Getenv("OTT_TEST_APK")
	if path == "" {
		t.Skip("set OTT_TEST_APK for a real wrapper artifact")
	}
	apk, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	guard, err := bundleEmbeddedGuard(apk)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = parseBundleUpdate(bundleTestZip(t, bundleTestEntry{"app.apk", apk, 0600}, bundleTestEntry{"ott-touch-guard", guard, 0755})); err != nil {
		t.Fatal(err)
	}
}

type fakeBundleOps struct {
	pair           bundlePayload
	baseline       bundleBaseline
	events         []string
	readOnly       bool
	fail           map[string]int
	crash          string
	beforeMutation func()
}

func (f *fakeBundleOps) event(name string) error {
	f.events = append(f.events, name)
	if name == f.crash {
		panic("simulated power loss")
	}
	if f.fail[name] > 0 {
		f.fail[name]--
		return errors.New("injected " + name)
	}
	return nil
}
func (f *fakeBundleOps) Preflight(context.Context) (bundleBaseline, error) {
	return f.baseline, f.event("preflight")
}
func (f *fakeBundleOps) ReadPair(context.Context) (bundlePayload, error) {
	return f.pair, f.event("read")
}
func (f *fakeBundleOps) CheckStorage(context.Context, bundlePayload, bundlePayload) error {
	return f.event("space")
}
func (f *fakeBundleOps) Stop(context.Context) error {
	if f.beforeMutation != nil {
		f.beforeMutation()
	}
	return f.event("stop")
}
func (f *fakeBundleOps) ReplaceGuard(_ context.Context, b []byte) error {
	f.readOnly = false
	f.pair.Guard = append([]byte(nil), b...)
	return f.event("guard")
}
func (f *fakeBundleOps) InstallAPK(_ context.Context, path string, downgrade bool) error {
	name := "install"
	if downgrade {
		name = "rollback-install"
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	f.pair.APK = b
	return f.event(name)
}
func (f *fakeBundleOps) Start(context.Context) error {
	if !f.readOnly {
		return errors.New("started with writable system")
	}
	return f.event("start")
}
func (f *fakeBundleOps) Verify(_ context.Context, p bundlePayload, _ bundleBaseline) error {
	if !reflect.DeepEqual(f.pair, p) {
		return errors.New("pair mismatch")
	}
	return f.event("verify")
}
func (f *fakeBundleOps) EnsureReadOnly(context.Context) error {
	if err := f.event("ro"); err != nil {
		return err
	}
	f.readOnly = true
	return nil
}

func newBundleTest(t *testing.T) (bundleUpdater, *fakeBundleOps, bundlePayload) {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "transaction")
	f := &fakeBundleOps{pair: bundleTestPair(t, 1), baseline: bundleBaseline{Locked: true, Strict: true, Playing: true, QueueTotal: 5, Provider: "vportal"}, readOnly: true, fail: map[string]int{}}
	return bundleUpdater{dir, f}, f, bundleTestPair(t, 2)
}

func TestBundleTransactionSuccessAndDurability(t *testing.T) {
	u, f, next := newBundleTest(t)
	old := f.pair
	f.beforeMutation = func() {
		s, err := u.readState()
		if err != nil || s == nil || s.State != "pending" {
			t.Fatalf("no durable state before mutation: %v %#v", err, s)
		}
		b, err := bundleReadFile(filepath.Join(u.dir, "previous.apk"), bundleAPKLimit, true)
		if err != nil || !bytes.Equal(b, old.APK) {
			t.Fatal("no rollback APK before mutation")
		}
	}
	effect, err := u.prepare(context.Background(), next)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(u.dir); !os.IsNotExist(err) {
		t.Fatal("prepare mutated filesystem")
	}
	if err = effect(); err != nil {
		t.Fatal(err)
	}
	s, err := u.readState()
	if err != nil || s.State != "committed" {
		t.Fatalf("not committed: %#v %v", s, err)
	}
	if !f.readOnly || !reflect.DeepEqual(f.pair, next) {
		t.Fatal("wrong final state")
	}
	want := []string{"preflight", "read", "space", "preflight", "read", "space", "stop", "guard", "install", "ro", "start", "verify", "ro"}
	if !reflect.DeepEqual(f.events, want) {
		t.Fatalf("order: %#v", f.events)
	}
	// A successful transaction retains its previous pair, but startup must not
	// replay it or restart an unrelated current foreground application.
	f.events = nil
	if err = u.recover(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(f.events) != 0 {
		t.Fatalf("committed transaction replayed: %v", f.events)
	}
}

func TestBundleFailuresRestoreMatchingPair(t *testing.T) {
	for _, point := range []string{"stop", "guard", "install", "ro", "start", "verify"} {
		t.Run(point, func(t *testing.T) {
			u, f, next := newBundleTest(t)
			old := f.pair
			effect, err := u.prepare(context.Background(), next)
			if err != nil {
				t.Fatal(err)
			}
			f.fail[point] = 1
			if err = effect(); err == nil {
				t.Fatal("injected failure ignored")
			}
			if !f.readOnly || !reflect.DeepEqual(f.pair, old) {
				t.Fatalf("inconsistent rollback at %s", point)
			}
			s, err := u.readState()
			if err != nil || s.State != "rolled_back" {
				t.Fatalf("rollback state: %v %#v", err, s)
			}
		})
	}
}

func TestBundleRollbackFailureRetriesOnStartupWithoutTakingFocus(t *testing.T) {
	u, f, next := newBundleTest(t)
	old := f.pair
	effect, err := u.prepare(context.Background(), next)
	if err != nil {
		t.Fatal(err)
	}
	f.fail["verify"] = 1
	f.fail["rollback-install"] = 1
	if err = effect(); err == nil {
		t.Fatal("rollback failure ignored")
	}
	s, err := u.readState()
	if err != nil || s.State != "pending" {
		t.Fatalf("not pending: %v %#v", err, s)
	}
	f.events = nil
	if err = u.recover(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(f.pair, old) || !f.readOnly {
		t.Fatal("startup recovery mismatch")
	}
	for _, name := range f.events {
		if name == "start" || name == "preflight" {
			t.Fatal("startup recovery took foreground")
		}
	}
	s, _ = u.readState()
	if s.State != "rolled_back" {
		t.Fatal("recovery not durable")
	}
}

func TestBundleReadOnlyFailureKeepsRecoveryPendingAndNeverStarts(t *testing.T) {
	u, f, next := newBundleTest(t)
	effect, err := u.prepare(context.Background(), next)
	if err != nil {
		t.Fatal(err)
	}
	f.fail["ro"] = 2
	if err = effect(); err == nil {
		t.Fatal("read-only failure ignored")
	}
	s, err := u.readState()
	if err != nil || s.State != "pending" {
		t.Fatal("lost recovery state")
	}
	if f.readOnly {
		t.Fatal("fixture did not exercise writable state")
	}
	for _, name := range f.events {
		if name == "start" {
			t.Fatal("launched while system writable")
		}
	}
	f.events = nil
	if err = u.recover(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !f.readOnly {
		t.Fatal("recovery did not restore read-only")
	}
	for _, name := range f.events {
		if name == "start" {
			t.Fatal("recovery launched app")
		}
	}
}

func TestBundlePowerLossRecovery(t *testing.T) {
	for _, point := range []string{"stop", "guard", "install", "start", "verify"} {
		t.Run(point, func(t *testing.T) {
			u, f, next := newBundleTest(t)
			old := f.pair
			effect, err := u.prepare(context.Background(), next)
			if err != nil {
				t.Fatal(err)
			}
			f.crash = point
			func() {
				defer func() {
					if recover() == nil {
						t.Fatal("missing simulated crash")
					}
				}()
				_ = effect()
			}()
			f.crash = ""
			f.events = nil
			if err = u.recover(context.Background()); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(f.pair, old) || !f.readOnly {
				t.Fatal("crash left inconsistent pair")
			}
			for _, name := range f.events {
				if name == "start" {
					t.Fatal("recovery launched OTT")
				}
			}
		})
	}
}

func TestBundleRefusalsHaveNoDeviceEffects(t *testing.T) {
	for _, scenario := range []string{"preflight", "space", "changed pair", "changed foreground", "bad archive", "pending"} {
		t.Run(scenario, func(t *testing.T) {
			u, f, next := newBundleTest(t)
			if scenario == "preflight" {
				f.fail["preflight"] = 1
			}
			if scenario == "space" {
				f.fail["space"] = 1
			}
			if scenario == "bad archive" {
				next.Guard = next.Guard[:20]
			}
			if scenario == "pending" {
				if err := u.checkDirectory(true); err != nil {
					t.Fatal(err)
				}
				h := strings.Repeat("a", 64)
				if err := u.writeState(&bundleTransaction{1, "pending", h, h, h, h, f.baseline}); err != nil {
					t.Fatal(err)
				}
			}
			effect, err := u.prepare(context.Background(), next)
			if err == nil {
				switch scenario {
				case "changed pair":
					f.pair = bundleTestPair(t, 3)
				case "changed foreground":
					f.fail["preflight"] = 1
				default:
					t.Fatal("refusal accepted")
				}
				if err = effect(); err == nil {
					t.Fatal("changed baseline accepted")
				}
			}
			for _, name := range f.events {
				if name != "read" && name != "preflight" && name != "space" {
					t.Fatalf("refusal mutated device: %v", f.events)
				}
			}
		})
	}
}

func TestBundleCorruptRollbackNeverInstallsOrLaunches(t *testing.T) {
	u, f, next := newBundleTest(t)
	effect, err := u.prepare(context.Background(), next)
	if err != nil {
		t.Fatal(err)
	}
	f.crash = "install"
	func() { defer func() { _ = recover() }(); _ = effect() }()
	f.crash = ""
	f.events = nil
	if err = os.WriteFile(filepath.Join(u.dir, "previous.guard"), []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = u.recover(context.Background()); err == nil {
		t.Fatal("corrupt rollback accepted")
	}
	if !reflect.DeepEqual(f.events, []string{"ro"}) {
		t.Fatalf("executed corrupt rollback: %v", f.events)
	}
	s, _ := u.readState()
	if s.State != "pending" {
		t.Fatal("lost pending recovery")
	}
}

func TestBundlePathsArePrivateAndNeverFollowSymlinks(t *testing.T) {
	u, f, next := newBundleTest(t)
	destination := t.TempDir()
	if err := os.Symlink(destination, u.dir); err != nil {
		t.Fatal(err)
	}
	if _, err := u.prepare(context.Background(), next); err == nil {
		t.Fatal("transaction directory symlink accepted")
	}
	if len(f.events) > 0 {
		t.Fatal("unsafe transaction directory reached device")
	}
	file := filepath.Join(t.TempDir(), "file")
	if err := os.Symlink("/etc/passwd", file); err != nil {
		t.Fatal(err)
	}
	if _, err := bundleReadFile(file, 10000, true); err == nil {
		t.Fatal("component symlink read")
	}
}

func TestBundleForegroundParser(t *testing.T) {
	good := "  mResumedActivity: ActivityRecord{123 u0 play.ott.kitkat/.MainActivity t8}\n"
	if !bundleForeground([]byte(good)) {
		t.Fatal("OTT foreground not recognized")
	}
	for _, bad := range []string{"", strings.Replace(good, "play.ott.kitkat/", "play.ott.kitkat.evil/", 1), strings.Replace(good, ".MainActivity", ".Other", 1), good + good, "  mResumedActivity: ActivityRecord{123 u0 home.plan/.MainActivity t9}\n"} {
		if bundleForeground([]byte(bad)) {
			t.Fatalf("accepted foreground: %q", bad)
		}
	}
}

func TestBundleQuiesceIncludesAppSubprocessAndOldHelper(t *testing.T) {
	for _, scenario := range []struct {
		name, cmd, exe string
		quiet          bool
	}{
		{"other app", "home.plan\x00", "/system/bin/app_process", true},
		{"OTT", packageName + "\x00", "/system/bin/app_process", false},
		{"OTT subprocess", packageName + ":renderer\x00", "/system/bin/app_process", false},
		{"helper", "guard\x00", bundleHelperPath, false},
		{"old mapped helper", "guard\x00", bundleHelperPath + " (deleted)", false},
		{"private helper", "guard\x00", "/data/data/" + packageName + "/files/ott-touch-guard-v09", false},
		{"broker", "service\x00", "/system/bin/ott-touch-service", true},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			proc := t.TempDir()
			p := filepath.Join(proc, "100")
			if err := os.Mkdir(p, 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(p, "cmdline"), []byte(scenario.cmd), 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(scenario.exe, filepath.Join(p, "exe")); err != nil {
				t.Fatal(err)
			}
			quiet, err := bundleProcessesQuiet(proc)
			if err != nil || quiet != scenario.quiet {
				t.Fatalf("quiesce: %v %v", quiet, err)
			}
		})
	}
}

func TestBundleQueueIdentityIncludesOrderedIDs(t *testing.T) {
	q := func(ids ...float64) map[string]any {
		items := make([]any, len(ids))
		for i, id := range ids {
			items[i] = map[string]any{"id": id, "title": "ignored"}
		}
		return map[string]any{"items": items}
	}
	want, err := bundleQueueHash(q(1, 2, 3), 3)
	if err != nil {
		t.Fatal(err)
	}
	for _, changed := range []map[string]any{q(3, 2, 1), q(1, 2, 4)} {
		h, err := bundleQueueHash(changed, 3)
		if err != nil || h == want {
			t.Fatal("different ordered queue has same identity")
		}
	}
	for _, bad := range []map[string]any{q(1, 2), q(1, 2, -1), q(1, 2, 3.5), {"items": []any{map[string]any{"title": "no id"}, map[string]any{}, map[string]any{}}}} {
		if _, err := bundleQueueHash(bad, 3); err == nil {
			t.Fatal("unknown queue identity accepted")
		}
	}
}
