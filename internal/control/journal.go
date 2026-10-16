package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const maxJournalBytes = 4 * 1024 * 1024

var operationKey = regexp.MustCompile(`^[a-zA-Z0-9_-]{16,80}$`)
var journalError = errors.New("command journal unavailable")

type operationRecord struct {
	ID               string `json:"id"`
	Owner            string `json:"owner"`
	Device           string `json:"device_id"`
	Permission       string `json:"action"`
	Key              string `json:"key"`
	Digest           string `json:"digest"`
	State            string `json:"state"`
	ResultStatus     string `json:"result_status,omitempty"`
	ExpiresAt        int64  `json:"expires_at"`
	CommandExpiresAt int64  `json:"command_expires_at"`
}
type commandJournal struct {
	config  config.CommandJournal
	records map[string]operationRecord
	lock    *os.File
}

func journalIndex(owner, key string) string {
	h := sha256.Sum256([]byte(owner + "\x00" + key))
	return hex.EncodeToString(h[:])
}

// Whole-second records must outlive the queue's precise deadline, never expire
// up to 999 ms before it and allow the same key to enqueue a second command.
func ceilUnix(t time.Time) int64 {
	seconds := t.Unix()
	if t.Nanosecond() != 0 {
		seconds++
	}
	return seconds
}
func copyRecords(rows map[string]operationRecord, now time.Time) map[string]operationRecord {
	result := map[string]operationRecord{}
	for k, v := range rows {
		if v.ExpiresAt > now.Unix() {
			result[k] = v
		}
	}
	return result
}
func newCommandJournal(c *config.CommandJournal) (*commandJournal, error) {
	if c == nil {
		return nil, nil
	}
	j := &commandJournal{config: *c, records: map[string]operationRecord{}}
	parent := filepath.Dir(c.Path)
	if os.MkdirAll(parent, 0700) != nil {
		return nil, journalError
	}
	info, err := os.Stat(parent)
	if err != nil || !info.IsDir() || (runtime.GOOS != "windows" && info.Mode().Perm()&0077 != 0) {
		return nil, journalError
	}
	// A trusted private directory plus no symlink at either file prevents redirection.
	for _, name := range []string{c.Path, c.Path + ".lock"} {
		if f, e := os.Lstat(name); e == nil {
			if !f.Mode().IsRegular() || (runtime.GOOS != "windows" && f.Mode().Perm()&0077 != 0) {
				return nil, journalError
			}
		} else if !os.IsNotExist(e) {
			return nil, journalError
		}
	}
	j.lock, err = lockJournal(c.Path + ".lock")
	if err != nil {
		return nil, journalError
	}
	ok := false
	defer func() {
		if !ok {
			j.lock.Close()
		}
	}()
	f, err := os.Open(c.Path)
	if err == nil {
		b, e := io.ReadAll(io.LimitReader(f, maxJournalBytes+1))
		f.Close()
		if e != nil || len(b) > maxJournalBytes {
			return nil, journalError
		}
		var disk struct {
			Version int                        `json:"version"`
			Records map[string]operationRecord `json:"records"`
		}
		if json.Unmarshal(b, &disk) != nil || disk.Version != 1 || len(disk.Records) > 4096 {
			return nil, journalError
		}
		j.records = disk.Records
	} else if !os.IsNotExist(err) {
		return nil, journalError
	}
	now := time.Now()
	j.records = copyRecords(j.records, now)
	ids := make(map[string]bool)
	for k, v := range j.records {
		digest, err := hex.DecodeString(v.Digest)
		if k != journalIndex(v.Owner, v.Key) || !operationKey.MatchString(v.Key) || !commandID.MatchString(v.ID) || ids[v.ID] || !config.ValidCommandAction(v.Permission) || v.Owner == "" || len(v.Owner) > 256 || v.Device == "" || err != nil || len(digest) != 32 || hex.EncodeToString(digest) != v.Digest || v.CommandExpiresAt <= 0 || v.CommandExpiresAt > v.ExpiresAt || v.ExpiresAt > now.Add(24*time.Hour).Unix()+60 {
			return nil, journalError
		}
		ids[v.ID] = true
		if (v.State == "responded" && v.ResultStatus != "ok" && v.ResultStatus != "rejected" && v.ResultStatus != "unsupported") || (v.State != "responded" && v.ResultStatus != "") {
			return nil, journalError
		}
		switch v.State {
		case "pending":
			v.State = "unknown"
			j.records[k] = v
		case "responded", "acknowledged", "unknown":
		default:
			return nil, journalError
		}
	}
	if len(j.records) > c.MaxRecords || j.save(j.records) != nil {
		return nil, journalError
	}
	ok = true
	return j, nil
}
func (j *commandJournal) save(rows map[string]operationRecord) error {
	b, e := json.Marshal(struct {
		Version int                        `json:"version"`
		Records map[string]operationRecord `json:"records"`
	}{1, rows})
	if e != nil || len(b) > maxJournalBytes {
		return journalError
	}
	f, e := os.CreateTemp(filepath.Dir(j.config.Path), ".ott-journal-*")
	if e != nil {
		return journalError
	}
	name := f.Name()
	defer os.Remove(name)
	defer f.Close()
	if f.Chmod(0600) != nil {
		return journalError
	}
	if _, e = f.Write(b); e != nil {
		return journalError
	}
	if f.Sync() != nil || f.Close() != nil {
		return journalError
	}
	if os.Rename(name, j.config.Path) != nil {
		return journalError
	}
	if runtime.GOOS != "windows" {
		d, e := os.Open(filepath.Dir(j.config.Path))
		if e != nil {
			return journalError
		}
		e = d.Sync()
		d.Close()
		if e != nil {
			return journalError
		}
	}
	return nil
}
func (s *Server) Close() error {
	if s.journal != nil {
		return s.journal.lock.Close()
	}
	return nil
}

// Called under s.mu; retained duplicates consume no queue capacity.
func (s *Server) hasOperation(r *http.Request, key string, now time.Time) bool {
	if key == "" || s.journal == nil {
		return false
	}
	v, ok := s.journal.records[journalIndex(requestOwner(r), key)]
	return ok && v.ExpiresAt > now.Unix()
}

// Called under s.mu. Payloads, URLs, tokens and result bodies never enter the journal.
func (j *commandJournal) begin(owner, device, permission, key, id string, payload []byte, now, deadline time.Time) (operationRecord, bool, error) {
	// Normalize object ordering and whitespace while retaining numeric text;
	// identifiers must never pass through float64 before hashing.
	var value any
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.UseNumber()
	if decoder.Decode(&value) != nil {
		return operationRecord{}, false, journalError
	}
	payload, err := json.Marshal(value)
	if err != nil {
		return operationRecord{}, false, journalError
	}
	digest := sha256.Sum256(payload)
	sum := hex.EncodeToString(digest[:])
	index := journalIndex(owner, key)
	rows := copyRecords(j.records, now)
	if prior, ok := rows[index]; ok {
		if prior.Digest != sum || prior.Device != device || prior.Permission != permission {
			return operationRecord{}, false, errors.New("conflict")
		}
		return prior, true, nil
	}
	if len(rows) >= j.config.MaxRecords {
		return operationRecord{}, false, errors.New("full")
	}
	v := operationRecord{ID: id, Owner: owner, Device: device, Permission: permission, Key: key, Digest: sum, State: "pending", ExpiresAt: ceilUnix(now.Add(time.Duration(j.config.RetentionSeconds) * time.Second)), CommandExpiresAt: ceilUnix(deadline)}
	rows[index] = v
	if e := j.save(rows); e != nil {
		return operationRecord{}, false, e
	}
	j.records = rows
	return v, false, nil
}
func (j *commandJournal) finish(ids map[string]bool, state, status string, now time.Time) error {
	if j == nil || len(ids) == 0 {
		return nil
	}
	rows := copyRecords(j.records, now)
	changed := false
	for k, v := range rows {
		if ids[v.ID] {
			v.State = state
			v.ResultStatus = status
			rows[k] = v
			changed = true
		}
	}
	if !changed {
		return nil
	}
	if err := j.save(rows); err != nil {
		return err
	}
	j.records = rows
	return nil
}
func (s *Server) operation(w http.ResponseWriter, r *http.Request) {
	q, ok := query(r, "device_id", "key")
	if !ok {
		failure(w, 400, "unsupported query parameters")
		return
	}
	id := q.Get("device_id")
	var target *device
	for _, d := range s.devices {
		if d.id == id {
			target = d
		}
	}
	if target == nil {
		failure(w, 400, "a configured device_id is required")
		return
	}
	if o := operatorFor(r); o != nil && !o.devices[id] {
		failure(w, 403, "device scope is not allowed")
		return
	}
	if s.journal == nil {
		failure(w, 503, "command journal is disabled")
		return
	}
	if !q.Has("key") {
		reply(w, 200, map[string]any{"enabled": true, "retention_seconds": s.journal.config.RetentionSeconds, "max_records": s.journal.config.MaxRecords, "command_ttl_seconds": int(s.ttl / time.Second)})
		return
	}
	key := q.Get("key")
	if !operationKey.MatchString(key) {
		failure(w, 400, "invalid operation key")
		return
	}
	s.mu.Lock()
	v, found := s.journal.records[journalIndex(requestOwner(r), key)]
	s.mu.Unlock()
	if !found || v.Device != id || v.ExpiresAt <= s.now().Unix() {
		failure(w, 404, "operation expired or not found")
		return
	}
	if !ownResult(r, v.Owner, v.Permission) {
		failure(w, 403, "operation scope is not allowed")
		return
	}
	if v.State == "pending" && v.CommandExpiresAt <= s.now().Unix() {
		v.State = "unknown"
	}
	reply(w, 200, map[string]any{"id": v.ID, "state": v.State, "result_status": v.ResultStatus, "expires_at": v.ExpiresAt, "command_expires_at": v.CommandExpiresAt, "action": v.Permission})
}
func (s *Server) startOperation(w http.ResponseWriter, r *http.Request, d *device, permission, key, id string, payload []byte, now, deadline time.Time) (bool, bool) {
	if key == "" {
		return false, true
	}
	if s.journal == nil {
		failure(w, 503, "command journal is disabled")
		return false, false
	}
	v, replay, err := s.journal.begin(requestOwner(r), d.id, permission, key, id, payload, now, deadline)
	if err != nil {
		code := 503
		if err.Error() == "conflict" {
			code = 409
		}
		failure(w, code, "operation cannot be accepted")
		return false, false
	}
	if replay {
		state := v.State
		if state == "pending" && v.CommandExpiresAt <= now.Unix() {
			state = "unknown"
		}
		reply(w, 202, map[string]string{"status": "retained", "id": v.ID, "state": state})
		return true, true
	}
	return false, true
}
