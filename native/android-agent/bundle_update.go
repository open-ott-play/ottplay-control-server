package main

// This is deliberately a two-component update, not a general archive installer.
// Call prepareBundleUpdate only after authenticating the enclosing Update and
// verifying its payload digest. Neither archive entry supplies a destination.
import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

const bundleAPKLimit = 60 * 1024 * 1024
const bundleGuardLimit = 4 * 1024 * 1024
const bundleDirectory = dataDir + "/bundle-update"
const bundleHelperPath = "/system/bin/ott-touch-guard"

type bundlePayload struct{ APK, Guard []byte }

func bundleHash(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func bundleEntry(f *zip.File, limit uint64) ([]byte, error) {
	if !f.Mode().IsRegular() || f.UncompressedSize64 == 0 || f.UncompressedSize64 > limit ||
		(f.Method != zip.Store && f.Method != zip.Deflate) || f.Flags&1 != 0 {
		return nil, errors.New("unsupported bundle entry")
	}
	r, err := f.Open()
	if err != nil {
		return nil, err
	}
	defer r.Close()
	b, err := io.ReadAll(io.LimitReader(r, int64(limit)+1))
	if err != nil || uint64(len(b)) != f.UncompressedSize64 || uint64(len(b)) > limit {
		return nil, errors.New("invalid bundle entry size or checksum")
	}
	return b, nil
}

func bundleEmbeddedGuard(apk []byte) ([]byte, error) {
	z, err := zip.NewReader(bytes.NewReader(apk), int64(len(apk)))
	if err != nil {
		return nil, err
	}
	var guard []byte
	for _, f := range z.File {
		if f.Name != "assets/ott-touch-guard" {
			continue
		}
		if guard != nil {
			return nil, errors.New("duplicate APK helper")
		}
		guard, err = bundleEntry(f, bundleGuardLimit)
		if err != nil {
			return nil, err
		}
	}
	if guard == nil {
		return nil, errors.New("APK has no bundled helper")
	}
	return guard, nil
}

func validateBundlePair(p bundlePayload) error {
	if len(p.APK) == 0 || len(p.APK) > bundleAPKLimit || len(p.Guard) < 52 || len(p.Guard) > bundleGuardLimit {
		return errors.New("invalid bundle component size")
	}
	// 32-bit little-endian ARM ELF, executable or PIE, ELF version 1.
	b := p.Guard
	if string(b[:4]) != "\x7fELF" || b[4] != 1 || b[5] != 1 || b[6] != 1 ||
		(b[16] != 2 && b[16] != 3) || b[17] != 0 || b[18] != 40 || b[19] != 0 ||
		b[20] != 1 || b[21] != 0 || b[22] != 0 || b[23] != 0 {
		return errors.New("bundle helper must be ARM ELF")
	}
	pkg, err := apkPackage(p.APK)
	if err != nil || pkg != packageName {
		return errors.New("wrong bundle APK package")
	}
	embedded, err := bundleEmbeddedGuard(p.APK)
	if err != nil || !bytes.Equal(embedded, p.Guard) {
		return errors.New("APK and system helper must be the same build")
	}
	return nil
}

func parseBundleUpdate(raw []byte) (bundlePayload, error) {
	var p bundlePayload
	if len(raw) == 0 || len(raw) > 64*1024*1024 {
		return p, errors.New("invalid bundle size")
	}
	z, err := zip.NewReader(bytes.NewReader(raw), int64(len(raw)))
	if err != nil || len(z.File) != 2 {
		return p, errors.New("bundle must contain exactly two files")
	}
	for _, f := range z.File {
		switch f.Name {
		case "app.apk":
			if p.APK != nil {
				return p, errors.New("duplicate bundle APK")
			}
			p.APK, err = bundleEntry(f, bundleAPKLimit)
		case "ott-touch-guard":
			if p.Guard != nil {
				return p, errors.New("duplicate bundle helper")
			}
			p.Guard, err = bundleEntry(f, bundleGuardLimit)
		default:
			return p, errors.New("unexpected bundle path")
		}
		if err != nil {
			return p, err
		}
	}
	return p, validateBundlePair(p)
}

type bundleBaseline struct {
	Locked     bool    `json:"locked"`
	Strict     bool    `json:"strict"`
	Playing    bool    `json:"playing"`
	QueueTotal float64 `json:"queue_total"`
	Provider   string  `json:"provider"`
	TargetHash string  `json:"target_hash"`
}

type bundleTransaction struct {
	Schema         int            `json:"schema"`
	State          string         `json:"state"`
	PreviousAPK    string         `json:"previous_apk"`
	PreviousGuard  string         `json:"previous_guard"`
	CandidateAPK   string         `json:"candidate_apk"`
	CandidateGuard string         `json:"candidate_guard"`
	Baseline       bundleBaseline `json:"baseline"`
}

// The interface describes effects, rather than shell strings. Fault-injection
// tests exercise ordering, restart recovery, and failures after partial writes.
type bundleUpdateOps interface {
	Preflight(context.Context) (bundleBaseline, error)
	ReadPair(context.Context) (bundlePayload, error)
	CheckStorage(context.Context, bundlePayload, bundlePayload) error
	Stop(context.Context) error
	ReplaceGuard(context.Context, []byte) error
	InstallAPK(context.Context, string, bool) error
	Start(context.Context) error
	Verify(context.Context, bundlePayload, bundleBaseline) error
	EnsureReadOnly(context.Context) error
}

type bundleUpdater struct {
	dir string
	ops bundleUpdateOps
}

func bundleReadFile(path string, limit int64, rootOnly bool) ([]byte, error) {
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok || !st.Mode().IsRegular() || st.Size() < 1 || st.Size() > limit || st.Mode().Perm()&0022 != 0 ||
		(sys.Uid != uint32(os.Getuid()) && (rootOnly || sys.Uid != 1000)) {
		return nil, errors.New("unsafe update component file")
	}
	b, err := io.ReadAll(io.LimitReader(f, limit+1))
	after, statErr := f.Stat()
	if err != nil || statErr != nil || int64(len(b)) != st.Size() || after.Size() != st.Size() || !after.ModTime().Equal(st.ModTime()) {
		return nil, errors.New("update component changed while reading")
	}
	return b, nil
}

func (u bundleUpdater) checkDirectory(create bool) error {
	if create {
		if err := os.Mkdir(u.dir, 0700); err != nil {
			if !os.IsExist(err) {
				return err
			}
		} else {
			// Persist the newly created directory itself before a transaction
			// inside it can authorize device changes.
			parent, err := os.Open(filepath.Dir(u.dir))
			if err != nil {
				return err
			}
			err = parent.Sync()
			closeErr := parent.Close()
			if err != nil {
				return err
			}
			if closeErr != nil {
				return closeErr
			}
		}
	}
	st, err := os.Lstat(u.dir)
	if err != nil {
		return err
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok || !st.IsDir() || st.Mode().Perm() != 0700 || sys.Uid != uint32(os.Getuid()) {
		return errors.New("unsafe bundle transaction directory")
	}
	return nil
}

func (u bundleUpdater) readState() (*bundleTransaction, error) {
	if err := u.checkDirectory(false); err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	b, err := bundleReadFile(filepath.Join(u.dir, "transaction.json"), 4096, true)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var s bundleTransaction
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if d.Decode(&s) != nil || d.Decode(new(any)) != io.EOF || s.Schema != 1 ||
		(s.State != "pending" && s.State != "committed" && s.State != "rolled_back") {
		return nil, errors.New("invalid bundle transaction state")
	}
	for _, h := range []string{s.PreviousAPK, s.PreviousGuard, s.CandidateAPK, s.CandidateGuard} {
		if !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(h) {
			return nil, errors.New("invalid transaction component digest")
		}
	}
	return &s, nil
}

func (u bundleUpdater) writeState(s *bundleTransaction) error {
	return atomicJSON(filepath.Join(u.dir, "transaction.json"), s)
}

func (u bundleUpdater) prepare(ctx context.Context, next bundlePayload) (func() error, error) {
	if err := validateBundlePair(next); err != nil {
		return nil, err
	}
	s, err := u.readState()
	if err != nil {
		return nil, err
	}
	if s != nil && s.State == "pending" {
		return nil, errors.New("bundle recovery pending")
	}
	baseline, err := u.ops.Preflight(ctx)
	if err != nil {
		return nil, err
	}
	old, err := u.ops.ReadPair(ctx)
	if err != nil {
		return nil, err
	}
	if err = validateBundlePair(old); err != nil {
		return nil, errors.New("installed APK and helper are not a recoverable pair")
	}
	if err = u.ops.CheckStorage(ctx, old, next); err != nil {
		return nil, err
	}
	oldAPK, oldGuard := bundleHash(old.APK), bundleHash(old.Guard)
	return func() error {
		// The controller ACK may have been delayed. Recheck both ownership and
		// baseline immediately before any local transaction or device mutation.
		call, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
		defer cancel()
		currentBaseline, err := u.ops.Preflight(call)
		if err != nil {
			return err
		}
		current, err := u.ops.ReadPair(call)
		if err != nil {
			return err
		}
		if bundleHash(current.APK) != oldAPK || bundleHash(current.Guard) != oldGuard || currentBaseline != baseline {
			return errors.New("bundle baseline changed before execution")
		}
		if err = u.ops.CheckStorage(call, old, next); err != nil {
			return err
		}
		if err = u.checkDirectory(true); err != nil {
			return err
		}
		for _, item := range []struct {
			name string
			data []byte
		}{
			{"previous.apk", old.APK}, {"previous.guard", old.Guard}, {"candidate.apk", next.APK}, {"candidate.guard", next.Guard},
		} {
			if err = atomicFile(filepath.Join(u.dir, item.name), item.data, 0600); err != nil {
				return err
			}
		}
		s := &bundleTransaction{1, "pending", oldAPK, oldGuard, bundleHash(next.APK), bundleHash(next.Guard), baseline}
		if err = u.writeState(s); err != nil {
			return err
		}
		// From this durable point every exit, including process death, leaves a
		// complete rollback pair. Startup recovery never executes the candidate.
		if err = u.apply(call, next, baseline); err != nil {
			rollbackErr := u.rollback(s, true)
			return errors.Join(err, rollbackErr)
		}
		s.State = "committed"
		if err = u.writeState(s); err != nil {
			s.State = "pending"
			return errors.Join(err, u.rollback(s, true))
		}
		return nil
	}, nil
}

func (u bundleUpdater) apply(ctx context.Context, p bundlePayload, baseline bundleBaseline) error {
	if err := u.ops.Stop(ctx); err != nil {
		return err
	}
	if err := u.ops.ReplaceGuard(ctx, p.Guard); err != nil {
		return err
	}
	if err := u.ops.InstallAPK(ctx, filepath.Join(u.dir, "candidate.apk"), false); err != nil {
		return err
	}
	if err := u.ops.EnsureReadOnly(ctx); err != nil {
		return err
	}
	if err := u.ops.Start(ctx); err != nil {
		return err
	}
	if err := u.ops.Verify(ctx, p, baseline); err != nil {
		return err
	}
	return u.ops.EnsureReadOnly(ctx)
}

func (u bundleUpdater) rollback(s *bundleTransaction, restart bool) error {
	// Cleanup receives its own deadline even when forward installation timed out.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	oldAPK, err := bundleReadFile(filepath.Join(u.dir, "previous.apk"), bundleAPKLimit, true)
	if err != nil {
		return errors.Join(err, u.ops.EnsureReadOnly(ctx))
	}
	oldGuard, err := bundleReadFile(filepath.Join(u.dir, "previous.guard"), bundleGuardLimit, true)
	if err != nil {
		return errors.Join(err, u.ops.EnsureReadOnly(ctx))
	}
	old := bundlePayload{oldAPK, oldGuard}
	if bundleHash(oldAPK) != s.PreviousAPK || bundleHash(oldGuard) != s.PreviousGuard || validateBundlePair(old) != nil {
		return errors.Join(errors.New("rollback pair invalid; recovery remains pending"), u.ops.EnsureReadOnly(ctx))
	}
	// Do not launch either APK until both previous components are restored.
	if err = u.ops.Stop(ctx); err == nil {
		err = u.ops.ReplaceGuard(ctx, old.Guard)
	}
	if err == nil {
		err = u.ops.InstallAPK(ctx, filepath.Join(u.dir, "previous.apk"), true)
	}
	err = errors.Join(err, u.ops.EnsureReadOnly(ctx))
	if err != nil {
		return fmt.Errorf("bundle rollback incomplete: %w", err)
	}
	actual, err := u.ops.ReadPair(ctx)
	if err != nil || bundleHash(actual.APK) != s.PreviousAPK || bundleHash(actual.Guard) != s.PreviousGuard {
		return errors.New("restored bundle pair could not be verified")
	}
	// During startup recovery another app may now be HOME. Restore files only;
	// never turn OTT on or take focus from the user's current application.
	if restart {
		if err = u.ops.Start(ctx); err != nil {
			return err
		}
		if err = u.ops.Verify(ctx, old, s.Baseline); err != nil {
			return err
		}
	}
	s.State = "rolled_back"
	return u.writeState(s)
}

func (u bundleUpdater) recover(ctx context.Context) error {
	s, err := u.readState()
	if err != nil {
		return errors.Join(err, u.ops.EnsureReadOnly(ctx))
	}
	if s == nil || s.State != "pending" {
		return nil
	}
	return u.rollback(s, false)
}

type androidBundleOps struct{ agent *Agent }

func bundleForeground(raw []byte) bool {
	if len(raw) > 256*1024 {
		return false
	}
	count, match := 0, false
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.Contains(line, "mResumedActivity:") {
			count++
			match = regexp.MustCompile(`^\s*mResumedActivity: ActivityRecord\{[^{}\r\n]*\bplay\.ott\.kitkat/(?:\.MainActivity|play\.ott\.kitkat\.MainActivity)(?:\s|\})`).MatchString(line)
		}
	}
	return count == 1 && match
}

func bundleCommand(ctx context.Context, timeout time.Duration, name string, args ...string) ([]byte, error) {
	child, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(child, name, args...)
	// pm help writes stderr on API 19; capture both streams with a fixed bound.
	var out bundleBoundedWriter
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	if out.overflow {
		return nil, errors.New("bundle command output exceeded limit")
	}
	if err != nil {
		return nil, errors.New("bundle command failed")
	}
	return out.b, nil
}

type bundleBoundedWriter struct {
	b        []byte
	overflow bool
}

func (w *bundleBoundedWriter) Write(p []byte) (int, error) {
	n := len(p)
	if n > 256*1024-len(w.b) {
		w.overflow = true
		return 0, errors.New("output limit")
	}
	w.b = append(w.b, p...)
	return n, nil
}

func bundleBaselineFrom(p map[string]any) bundleBaseline {
	k, v, q := object(p["kiosk"]), object(p["video"]), object(p["queue"])
	provider, _ := p["provider"].(string)
	total, _ := q["total"].(float64)
	return bundleBaseline{Locked: k["enabled"] == true && k["state"] == "locked", Strict: k["strict"] == true,
		Playing: v["paused"] == false && v["ended"] == false, QueueTotal: total, Provider: provider}
}

func (o androidBundleOps) identityBaseline(ctx context.Context, p map[string]any) (bundleBaseline, error) {
	b := bundleBaselineFrom(p)
	if !b.Locked {
		return b, nil
	}
	k := object(p["kiosk"])
	if channel := object(k["channel"]); len(channel) > 0 {
		id, ok := channel["id"].(string)
		if !ok || id == "" || len(id) > 256 {
			return b, errors.New("kiosk channel identity unavailable")
		}
		b.TargetHash = bundleHash([]byte(b.Provider + "\x00tv\x00" + id))
		return b, nil
	}
	if b.Provider != "vportal" || b.QueueTotal < 1 || b.QueueTotal > 10000 {
		return b, errors.New("kiosk queue identity unavailable")
	}
	child, cancel := context.WithTimeout(ctx, 7*time.Second)
	defer cancel()
	q, err := o.agent.player(child, "vportal_queue", map[string]any{"operation": "status"})
	if err != nil {
		return b, errors.New("kiosk queue identity unavailable")
	}
	b.TargetHash, err = bundleQueueHash(q, b.QueueTotal)
	return b, err
}

func bundleQueueHash(q map[string]any, total float64) (string, error) {
	items, ok := q["items"].([]any)
	if !ok || float64(len(items)) != total || len(items) == 0 || len(items) > 10000 {
		return "", errors.New("kiosk queue changed during observation")
	}
	ids := make([]float64, len(items))
	for i, item := range items {
		id, ok := object(item)["id"].(float64)
		if !ok || id < 1 || id > 9007199254740991 || id != float64(int64(id)) {
			return "", errors.New("kiosk item identity unavailable")
		}
		ids[i] = id
	}
	encoded, err := json.Marshal(ids)
	if err != nil {
		return "", err
	}
	return bundleHash(encoded), nil
}

func (o androidBundleOps) Preflight(ctx context.Context) (bundleBaseline, error) {
	var empty bundleBaseline
	if os.Getuid() != 0 || property("ro.build.version.sdk") != "19" || property("ro.product.model") != "Q1001L4B2" ||
		property("ro.serialno") != o.agent.cfg.Serial {
		return empty, errors.New("bundle unsupported on this device")
	}
	raw, err := bundleCommand(ctx, 5*time.Second, "/system/bin/pm", "help")
	// API 19 AOSP pm/PMS explicitly implement -d for release APKs. Refuse
	// vendor builds whose own help does not advertise this rollback facility.
	// android.googlesource.com/platform/frameworks/base/+/android-4.4.2_r2/
	// cmds/pm/src/com/android/commands/pm/Pm.java (INSTALL_ALLOW_DOWNGRADE), and
	// services/java/com/android/server/pm/PackageManagerService.java.
	if err != nil || !strings.Contains(string(raw), "-d: allow version code downgrade") {
		return empty, errors.New("APK downgrade rollback unavailable")
	}
	raw, err = bundleCommand(ctx, 5*time.Second, "/system/bin/dumpsys", "package", packageName)
	if err != nil {
		return empty, err
	}
	enabled, err := parseAPKEnabled(raw)
	if err != nil || !enabled {
		return empty, errors.New("bundle requires an enabled OTT app")
	}
	raw, err = bundleCommand(ctx, 5*time.Second, "/system/bin/dumpsys", "activity", "activities")
	if err != nil || !bundleForeground(raw) {
		return empty, errors.New("bundle requires OTT in foreground")
	}
	_, readOnly, err := bundleSystemMount()
	if err != nil || !readOnly {
		return empty, errors.New("bundle requires read-only system partition")
	}
	child, cancel := context.WithTimeout(ctx, 7*time.Second)
	defer cancel()
	p, err := o.agent.player(child, "health", nil)
	if err != nil {
		return empty, errors.New("bundle requires responsive player")
	}
	b, err := o.identityBaseline(ctx, p)
	if err != nil {
		return empty, err
	}
	if b.Locked && p["touch"] != "system" {
		return empty, errors.New("bundle requires verified system touch lock")
	}
	return b, nil
}

func (o androidBundleOps) ReadPair(ctx context.Context) (bundlePayload, error) {
	child, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	path, err := installedAPKPath(child)
	if err != nil {
		return bundlePayload{}, err
	}
	apk, err := bundleReadFile(path, bundleAPKLimit, false)
	if err != nil {
		return bundlePayload{}, err
	}
	guard, err := bundleReadFile(bundleHelperPath, bundleGuardLimit, true)
	return bundlePayload{apk, guard}, err
}

func (o androidBundleOps) CheckStorage(_ context.Context, old, next bundlePayload) error {
	// Keep room for both durable pairs and PackageManager's replacement/copy.
	// This is an admission floor, not a reservation against unrelated writers.
	dataNeed := uint64(2*(len(old.APK)+len(next.APK)) + len(old.Guard) + len(next.Guard) + 16*1024*1024)
	systemNeed := uint64(len(next.Guard) + 4*1024*1024)
	for _, p := range []struct {
		path string
		need uint64
	}{{dataDir, dataNeed}, {"/system", systemNeed}} {
		var st syscall.Statfs_t
		if err := syscall.Statfs(p.path, &st); err != nil || st.Bsize <= 0 || uint64(st.Bavail) < (p.need+uint64(st.Bsize)-1)/uint64(st.Bsize) {
			return errors.New("insufficient space for a recoverable bundle update")
		}
	}
	return nil
}

func (o androidBundleOps) Stop(ctx context.Context) error {
	_, err := bundleCommand(ctx, 10*time.Second, "/system/bin/am", "force-stop", packageName)
	if err != nil {
		return err
	}
	// Killing the app closes its authenticated socket, but the broker's old
	// child may still be releasing EVIOCGRAB. Never overlap old and new helpers.
	child, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	for {
		quiet, err := bundleProcessesQuiet("/proc")
		if err != nil {
			return err
		}
		if quiet {
			return nil
		}
		select {
		case <-child.Done():
			return errors.New("OTT or touch helper did not quiesce")
		case <-time.After(100 * time.Millisecond):
		}
	}
}

func bundleProcessesQuiet(proc string) (bool, error) {
	dir, err := os.Open(proc)
	if err != nil {
		return false, err
	}
	defer dir.Close()
	entries, err := dir.ReadDir(8193)
	if err != nil && err != io.EOF {
		return false, err
	}
	if len(entries) > 8192 {
		return false, errors.New("process scan limit exceeded")
	}
	for _, entry := range entries {
		if !regexp.MustCompile(`^[0-9]+$`).MatchString(entry.Name()) {
			continue
		}
		base := filepath.Join(proc, entry.Name())
		f, err := os.Open(filepath.Join(base, "cmdline"))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return false, err
		}
		cmd, readErr := io.ReadAll(io.LimitReader(f, 4097))
		f.Close()
		if os.IsNotExist(readErr) {
			continue
		}
		if readErr != nil || len(cmd) > 4096 {
			return false, errors.New("process command unavailable")
		}
		name := strings.SplitN(string(cmd), "\x00", 2)[0]
		if name == packageName || strings.HasPrefix(name, packageName+":") {
			return false, nil
		}
		exe, err := os.Readlink(filepath.Join(base, "exe"))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return false, err
		}
		exe = strings.TrimSuffix(exe, " (deleted)")
		if exe == bundleHelperPath || strings.HasPrefix(exe, "/data/data/"+packageName+"/files/ott-touch-guard") {
			return false, nil
		}
	}
	return true, nil
}

func bundleSystemMount() (string, bool, error) {
	b, err := os.ReadFile("/proc/mounts")
	if err != nil || len(b) > 1024*1024 {
		return "", false, errors.New("system mount unavailable")
	}
	count, source, ro := 0, "", false
	for _, line := range strings.Split(string(b), "\n") {
		f := strings.Fields(line)
		if len(f) < 4 || f[1] != "/system" {
			continue
		}
		count++
		source = f[0]
		if !regexp.MustCompile(`^/dev/block/[A-Za-z0-9_./-]+$`).MatchString(source) || filepath.Clean(source) != source {
			return "", false, errors.New("unexpected system mount source")
		}
		for _, flag := range strings.Split(f[3], ",") {
			if flag == "ro" {
				ro = true
			}
		}
	}
	if count != 1 {
		return "", false, errors.New("ambiguous system mount")
	}
	return source, ro, nil
}

func (o androidBundleOps) EnsureReadOnly(ctx context.Context) error {
	source, ro, err := bundleSystemMount()
	if err != nil || ro {
		return err
	}
	for i := 0; i < 3; i++ {
		_, err = bundleCommand(ctx, 5*time.Second, "/system/bin/mount", "-o", "remount,ro", source, "/system")
		_, ro, checkErr := bundleSystemMount()
		if err == nil && checkErr == nil && ro {
			return nil
		}
	}
	return errors.New("CRITICAL: system could not be restored read-only; bundle recovery remains pending")
}

func (o androidBundleOps) ReplaceGuard(ctx context.Context, b []byte) (err error) {
	source, _, err := bundleSystemMount()
	if err != nil {
		return err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		err = errors.Join(err, o.EnsureReadOnly(cleanup))
	}()
	if _, err = bundleCommand(ctx, 5*time.Second, "/system/bin/mount", "-o", "remount,rw", source, "/system"); err != nil {
		return err
	}
	// atomicFile fsyncs bytes and the parent directory. No broker, secret or
	// startup file is opened for writing, and no shell command is assembled.
	return atomicFile(bundleHelperPath, b, 0755)
}

func (o androidBundleOps) InstallAPK(ctx context.Context, path string, downgrade bool) error {
	args := []string{"install", "-r"}
	if downgrade {
		args = append(args, "-d")
	}
	args = append(args, path)
	b, err := bundleCommand(ctx, 60*time.Second, "/system/bin/pm", args...)
	if err != nil {
		return err
	}
	for _, line := range strings.Split(string(b), "\n") {
		if strings.TrimSpace(line) == "Success" {
			return nil
		}
	}
	return errors.New("bundle APK installation rejected")
}

func (o androidBundleOps) Start(ctx context.Context) error {
	raw, err := bundleCommand(ctx, 5*time.Second, "/system/bin/dumpsys", "package", packageName)
	if err != nil {
		return err
	}
	enabled, err := parseAPKEnabled(raw)
	if err != nil || !enabled {
		return errors.New("OTT was disabled; refusing to launch it")
	}
	_, err = bundleCommand(ctx, 10*time.Second, "/system/bin/am", "start", "-n", packageName+"/.MainActivity")
	return err
}

func (o androidBundleOps) Verify(ctx context.Context, p bundlePayload, want bundleBaseline) error {
	actual, err := o.ReadPair(ctx)
	if err != nil || !bytes.Equal(actual.APK, p.APK) || !bytes.Equal(actual.Guard, p.Guard) {
		return errors.New("installed bundle bytes differ")
	}
	deadline, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	var prior map[string]any
	for {
		child, done := context.WithTimeout(deadline, 7*time.Second)
		current, readErr := o.agent.player(child, "health", nil)
		done()
		if readErr == nil {
			got, identityErr := o.identityBaseline(deadline, current)
			if got.Locked == want.Locked && got.Strict == want.Strict && got.QueueTotal == want.QueueTotal && got.Provider == want.Provider &&
				identityErr == nil && got.TargetHash == want.TargetHash && (!want.Locked || current["touch"] == "system") {
				if !want.Playing {
					return nil
				}
				v, pv := object(current["video"]), object(prior["video"])
				pos, _ := v["position"].(float64)
				before, _ := pv["position"].(float64)
				if got.Playing && prior != nil && v["source"] == pv["source"] && pos > before {
					return nil
				}
				prior = current
			}
		}
		select {
		case <-deadline.Done():
			return errors.New("bundle player verification failed")
		case <-time.After(time.Second):
		}
	}
}

func (a *Agent) prepareBundleUpdate(ctx context.Context, raw []byte) (func() error, error) {
	p, err := parseBundleUpdate(raw)
	if err != nil {
		return nil, err
	}
	return (bundleUpdater{bundleDirectory, androidBundleOps{a}}).prepare(ctx, p)
}

func (a *Agent) recoverBundleUpdate(ctx context.Context) error {
	return (bundleUpdater{bundleDirectory, androidBundleOps{a}}).recover(ctx)
}

func bundleUpdateState() string {
	s, err := (bundleUpdater{dir: bundleDirectory}).readState()
	if err != nil {
		return "unavailable"
	}
	if s == nil {
		return "absent"
	}
	return s.State
}
