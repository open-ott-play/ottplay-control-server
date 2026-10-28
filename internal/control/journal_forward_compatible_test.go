package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const futureJournalPermission = "future_scope.stage.apply_v2"

func futureJournalRecord(index int) operationRecord {
	now := time.Now()
	return operationRecord{
		ID: fmt.Sprintf("%032x", index+1), Owner: "admin", Device: "first",
		Permission: futureJournalPermission, Key: fmt.Sprintf("future-audit-key-%04d", index),
		Digest: strings.Repeat("a", 64), State: "responded", ResultStatus: "ok",
		ExpiresAt: now.Add(time.Hour).Unix(), CommandExpiresAt: now.Add(time.Minute).Unix(),
	}
}

func writeFutureJournal(t *testing.T, c config.Config, records map[string]operationRecord) {
	t.Helper()
	data, err := json.Marshal(struct {
		Version int                        `json:"version"`
		Records map[string]operationRecord `json:"records"`
	}{1, records})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(c.Journal.Path, data, 0600); err != nil {
		t.Fatal(err)
	}
}

func readFutureJournal(t *testing.T, c config.Config) map[string]operationRecord {
	t.Helper()
	data, err := os.ReadFile(c.Journal.Path)
	if err != nil {
		t.Fatal(err)
	}
	var disk struct {
		Version int                        `json:"version"`
		Records map[string]operationRecord `json:"records"`
	}
	if json.Unmarshal(data, &disk) != nil || disk.Version != 1 {
		t.Fatal("invalid saved journal")
	}
	return disk.Records
}

func TestJournalPermissionSyntaxIsBoundedAndSeparateFromAdmission(t *testing.T) {
	for _, permission := range []string{"a", "future", "future_v2.stage.apply_3", strings.Repeat("a", 128)} {
		if !validJournalPermission(permission) {
			t.Fatalf("valid opaque permission rejected: %q", permission)
		}
	}
	for _, permission := range []string{"", strings.Repeat("a", 129), "Future", "future.*", "future..op", ".future", "future.", "future.1", "future/op", "https://future", "future op", "future\n", "future\x00", "future.é", "future-op"} {
		if validJournalPermission(permission) {
			t.Fatalf("invalid opaque permission accepted: %q", permission)
		}
	}
	if config.ValidCommandAction(futureJournalPermission) {
		t.Fatal("future audit permission became an admitted action")
	}
	for family, operations := range config.CommandOperations {
		for _, operation := range strings.Fields(operations) {
			if !validJournalPermission(family + "." + operation) {
				t.Fatalf("current composite permission rejected: %s.%s", family, operation)
			}
		}
	}
	for _, permission := range strings.Fields("status providers profiles epg_catalog capabilities screenshot inspect profile profile_settings profile_save profile_export profile_import play_catalog resolve_archive play_archive_catalog play provider vportal vportal_random vportal_search channels programs provider_settings settings_compare playback_handoff_export playback_handoff_import") {
		if !config.ValidCommandAction(permission) || !validJournalPermission(permission) {
			t.Fatalf("current simple permission rejected: %s", permission)
		}
	}
}

func TestJournalFuturePermissionsPreserveAuditAcrossRestart(t *testing.T) {
	c := journalConfig(t)
	c.Journal.MaxRecords = 8
	records := map[string]operationRecord{}
	want := map[string]operationRecord{}
	for index, state := range []struct{ state, status string }{
		{"pending", ""}, {"responded", "ok"}, {"responded", "rejected"},
		{"responded", "unsupported"}, {"acknowledged", ""}, {"unknown", ""},
	} {
		row := futureJournalRecord(index)
		row.State, row.ResultStatus = state.state, state.status
		key := journalIndex(row.Owner, row.Key)
		records[key] = row
		if row.State == "pending" {
			row.State = "unknown"
		}
		want[key] = row
	}
	known := futureJournalRecord(6)
	known.Permission = "status"
	key := journalIndex(known.Owner, known.Key)
	records[key], want[key] = known, known
	writeFutureJournal(t, c, records)
	for restart := 0; restart < 2; restart++ {
		s, err := New(c)
		if err != nil {
			t.Fatal(err)
		}
		for _, row := range want {
			value := operationValue(t, s, row.Key)
			state, status := "unknown", ""
			if row.Permission == "status" {
				state, status = "responded", "ok"
			}
			if value["id"] != row.ID || value["action"] != row.Permission || value["state"] != state || value["result_status"] != status {
				s.Close()
				t.Fatal("incorrect conservative operation projection", value)
			}
		}
		if len(s.devices[0].queue) != 0 || s.bytes != 0 || len(s.devices[0].results) != 0 || !reflect.DeepEqual(s.journal.records, want) {
			s.Close()
			t.Fatal("audit records replayed or changed in memory")
		}
		if err := s.Close(); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(readFutureJournal(t, c), want) {
			t.Fatal("saved identity, digest, expiry or completed audit fields changed")
		}
	}
}

func TestJournalFuturePermissionCannotReplayOrEvictUnexpiredAudit(t *testing.T) {
	c := journalConfig(t)
	c.Journal.MaxRecords = 1
	row := futureJournalRecord(0)
	writeFutureJournal(t, c, map[string]operationRecord{journalIndex(row.Owner, row.Key): row})
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, body := range []string{
		`{"action":"future_scope","params":{"op":"stage.apply_v2"},"idempotency_key":"` + row.Key + `"}`,
		`{"action":"future_scope.stage.apply_v2","params":{},"idempotency_key":"` + row.Key + `"}`,
	} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken,
		`{"action":"status","params":{},"idempotency_key":"`+row.Key+`"}`, nil), 409)
	body := `{"action":"status","params":{},"idempotency_key":"known-new-key-0001"}`
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 503)
	if len(s.devices[0].queue) != 0 || len(s.journal.records) != 1 || s.journal.records[journalIndex(row.Owner, row.Key)] != row {
		t.Fatal("unknown record was replayed, replaced or evicted")
	}
	if got := readFutureJournal(t, c); len(got) != 1 || got[journalIndex(row.Owner, row.Key)] != row {
		t.Fatal("failed admissions changed the saved audit")
	}
	// Retain the existing finite retention policy; unknown is not forever state.
	s.now = func() time.Time { return time.Unix(row.ExpiresAt+1, 0) }
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 1 || len(s.journal.records) != 1 {
		t.Fatal("expired audit did not follow normal retention")
	}
}

func TestJournalFuturePermissionDoesNotBroadenOwnerOrOperatorAccess(t *testing.T) {
	c := journalConfig(t)
	token := strings.Repeat("o", 32)
	digest := sha256.Sum256([]byte(token))
	c.Operators = []config.CommandOperator{{ID: "remote", CredentialSHA256: hex.EncodeToString(digest[:]), DeviceIDs: []string{"first"}, Actions: []string{"status"}}}
	admin, own, other := futureJournalRecord(0), futureJournalRecord(1), futureJournalRecord(2)
	own.Owner, other.Owner = "operator:remote", "operator:other"
	records := map[string]operationRecord{}
	for _, row := range []operationRecord{admin, own, other} {
		records[journalIndex(row.Owner, row.Key)] = row
	}
	writeFutureJournal(t, c, records)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		device, key, token string
		status             int
	}{
		{"first", admin.Key, adminToken, 200}, {"second", admin.Key, adminToken, 404},
		{"first", own.Key, token, 403}, {"second", own.Key, token, 403},
		{"first", admin.Key, token, 404}, {"first", other.Key, token, 404},
		{"first", own.Key, adminToken, 404}, {"first", admin.Key, firstToken, 403},
	} {
		expect(t, request(s, "GET", "/api/operations?device_id="+tc.device+"&key="+tc.key, tc.token, "", nil), tc.status)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	c.Operators[0].Actions = []string{futureJournalPermission}
	if s, err := New(c); err == nil {
		s.Close()
		t.Fatal("future journal syntax broadened operator configuration")
	}
}

func TestJournalFuturePermissionRetainsStructuralValidation(t *testing.T) {
	for name, edit := range map[string]func(*operationRecord){
		"bad-permission":         func(v *operationRecord) { v.Permission = "future/unsafe" },
		"long-permission":        func(v *operationRecord) { v.Permission = strings.Repeat("a", 129) },
		"bad-id":                 func(v *operationRecord) { v.ID = "invalid" },
		"bad-key":                func(v *operationRecord) { v.Key = "short" },
		"empty-owner":            func(v *operationRecord) { v.Owner = "" },
		"long-owner":             func(v *operationRecord) { v.Owner = strings.Repeat("a", 257) },
		"empty-device":           func(v *operationRecord) { v.Device = "" },
		"bad-digest":             func(v *operationRecord) { v.Digest = strings.Repeat("A", 64) },
		"future-state":           func(v *operationRecord) { v.State, v.ResultStatus = "new-state", "" },
		"bad-status":             func(v *operationRecord) { v.ResultStatus = "committed" },
		"unknown-with-status":    func(v *operationRecord) { v.State = "unknown" },
		"invalid-command-expiry": func(v *operationRecord) { v.CommandExpiresAt = 0 },
		"reversed-expiry":        func(v *operationRecord) { v.CommandExpiresAt = v.ExpiresAt + 1 },
		"too-far-expiry":         func(v *operationRecord) { v.ExpiresAt = time.Now().Add(48 * time.Hour).Unix() },
	} {
		t.Run(name, func(t *testing.T) {
			c := journalConfig(t)
			row := futureJournalRecord(0)
			edit(&row)
			writeFutureJournal(t, c, map[string]operationRecord{journalIndex(row.Owner, row.Key): row})
			if s, err := New(c); err == nil {
				s.Close()
				t.Fatal("corrupt future audit record accepted")
			}
		})
	}
	for _, scenario := range []string{"duplicate-id", "wrong-index", "over-capacity"} {
		t.Run(scenario, func(t *testing.T) {
			c := journalConfig(t)
			first, second := futureJournalRecord(0), futureJournalRecord(1)
			if scenario == "duplicate-id" {
				second.ID = first.ID
			}
			key := journalIndex(first.Owner, first.Key)
			if scenario == "wrong-index" {
				key = strings.Repeat("0", 64)
			}
			if scenario == "over-capacity" {
				c.Journal.MaxRecords = 1
			}
			writeFutureJournal(t, c, map[string]operationRecord{key: first, journalIndex(second.Owner, second.Key): second})
			if s, err := New(c); err == nil {
				s.Close()
				t.Fatal("invalid future journal collection accepted")
			}
		})
	}
}
