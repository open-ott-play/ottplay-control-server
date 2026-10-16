package control

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func journalConfig(t *testing.T) config.Config {
	t.Helper()
	c := testConfig()
	dir := filepath.Join(t.TempDir(), "private")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	c.Journal = &config.CommandJournal{Path: filepath.Join(dir, "operations.json"), MaxRecords: 4, RetentionSeconds: 86400}
	return c
}
func operationValue(t *testing.T, s *Server, key string) map[string]any {
	t.Helper()
	w := request(s, "GET", "/api/operations?device_id=first&key="+key, adminToken, "", nil)
	expect(t, w, 200)
	var v map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &v); err != nil {
		t.Fatal(err)
	}
	return v
}
func TestJournalRestartNeverReplaysUnknownRelativeCommand(t *testing.T) {
	c := journalConfig(t)
	c.MaxPendingPerDevice = 1
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	key := "relative-next-0001"
	body := `{"action":"playback","params":{"operation":"next_channel"},"idempotency_key":"` + key + `"}`
	w := request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil)
	expect(t, w, 202)
	before := operationValue(t, s, key)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("duplicate queued")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, strings.Replace(body, "next_channel", "previous_channel", 1), nil), 409)
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	after := operationValue(t, s, key)
	if after["state"] != "unknown" || before["id"] != after["id"] {
		t.Fatal(after)
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 0 {
		t.Fatal("unknown operation replayed after restart")
	}
}

func TestJournalLegacyHeaderAndCanonicalParams(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	expect(t, request(s, "OPTIONS", "/api/webhook/commands", "", "", map[string]string{"Origin": "https://player.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization, content-type, idempotency-key"}), 204)
	headers := map[string]string{"Idempotency-Key": "canonical-command-01"}
	w := request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{"command":"set_volume","volume":35}`, headers)
	expect(t, w, 202)
	w = request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{ "volume":35,"command":"set_volume" }`, headers)
	expect(t, w, 202)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("semantic duplicate enqueued")
	}
}
func TestJournalRetainsOutcomeWithoutSecretBody(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	key := "result-retained-001"
	body := `{"action":"status","params":{},"idempotency_key":"` + key + `"}`
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	id := operationValue(t, s, key)["id"].(string)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{"token":"TOP_SECRET"}}`, nil), 200)
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(c.Journal.Path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(b), "TOP_SECRET") {
		t.Fatal("result persisted")
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	v := operationValue(t, s, key)
	if v["state"] != "responded" || v["result_status"] != "ok" {
		t.Fatal(v)
	}
	expect(t, request(s, "GET", "/api/operations?device_id=first&key="+key, firstToken, "", nil), 403)
	expect(t, request(s, "GET", "/api/operations?device_id=second&key="+key, adminToken, "", nil), 404)
}
func TestJournalExpiryCapacityAndFileProtection(t *testing.T) {
	c := journalConfig(t)
	c.Journal.MaxRecords = 1
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if other, err := New(c); err == nil {
		other.Close()
		t.Fatal("second writer accepted")
	}
	now := time.Now()
	s.now = func() time.Time { return now }
	body := `{"action":"status","params":{},"idempotency_key":"expiry-check-00001"}`
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	now = now.Add(61 * time.Second)
	if operationValue(t, s, "expiry-check-00001")["state"] != "unknown" {
		t.Fatal("expired pending result")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 0 {
		t.Fatal("expired command replayed")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, strings.Replace(body, "00001", "00002", 1), nil), 503)
	now = now.Add(24 * time.Hour)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
}
func TestJournalFailsClosedForInvalidDiskAndConfiguration(t *testing.T) {
	c := journalConfig(t)
	if err := os.WriteFile(c.Journal.Path, []byte("not json"), 0600); err != nil {
		t.Fatal(err)
	}
	if s, err := New(c); err == nil {
		s.Close()
		t.Fatal("corrupt journal accepted")
	}
	c = journalConfig(t)
	c.CommandTTLSeconds = 3600
	c.Journal.RetentionSeconds = 60
	if _, err := New(c); err == nil {
		t.Fatal("retention shorter than TTL")
	}
}
