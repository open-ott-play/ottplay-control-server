package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func auditRead(t *testing.T, s *Server, token, query string) map[string]any {
	t.Helper()
	w := request(s, "GET", requestAuditPath+"?device_id=first"+query, token, "", nil)
	expect(t, w, 200)
	var out map[string]any
	if json.Unmarshal(w.Body.Bytes(), &out) != nil {
		t.Fatal("invalid audit JSON")
	}
	return out
}
func auditRows(t *testing.T, report map[string]any) []any {
	t.Helper()
	rows, ok := report["entries"].([]any)
	if !ok {
		t.Fatal("audit entries not array")
	}
	return rows
}
func TestRequestAuditIngressReadOnlyAndSecretFree(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	now := time.Unix(1800000000, 123000000)
	s.now = func() time.Time { return now }
	id := rpcID(t, s, `{"action":"play","params":{"query":"https://private.invalid/fixture-secret"}}`)
	queue := append([]entry(nil), s.devices[0].queue...)
	bytesBefore := s.bytes
	report := auditRead(t, s, adminToken, "")
	rows := auditRows(t, report)
	if len(rows) != 1 || report["version"] != float64(1) || report["scope"] != "controller_rpc_ingress" || report["controller_epoch"] != s.playerStateEpoch || report["lookup"] != "not_requested" {
		t.Fatal(report)
	}
	row := rows[0].(map[string]any)
	if row["request_id"] != id || row["action"] != "play" || row["state"] != "pending" || row["result_status"] != nil || row["accepted_at_ms"] != float64(now.UnixMilli()) || row["expires_at_ms"] != float64(now.Add(s.ttl).UnixMilli()) {
		t.Fatal(row)
	}
	if !reflect.DeepEqual(row["initiator"], map[string]any{"credential_role": "admin", "operator_id": nil}) {
		t.Fatal(row)
	}
	wire, _ := json.Marshal(report)
	for _, forbidden := range []string{"fixture-secret", "private.invalid", "query", "digest", "token", "User-Agent"} {
		if strings.Contains(string(wire), forbidden) {
			t.Fatalf("audit leaked %s", forbidden)
		}
	}
	if !reflect.DeepEqual(queue, s.devices[0].queue) || s.bytes != bytesBefore || len(s.devices[0].results) != 0 {
		t.Fatal("audit read changed queue/result")
	}
	found := auditRead(t, s, adminToken, "&id="+id)
	if found["lookup"] != "found" || len(auditRows(t, found)) != 1 {
		t.Fatal(found)
	}
	absent := auditRead(t, s, adminToken, "&id="+strings.Repeat("f", 32))
	if absent["lookup"] != "not_retained" || len(auditRows(t, absent)) != 0 {
		t.Fatal(absent)
	}
	now = now.Add(s.ttl)
	expired := auditRows(t, auditRead(t, s, adminToken, ""))[0].(map[string]any)
	if expired["state"] != "expired" || expired["result_status"] != nil || len(s.devices[0].queue) != 1 || s.bytes != bytesBefore {
		t.Fatal("passive expiry read mutated queue or claims response")
	}
}
func TestRequestAuditFirstValidResponseAndNoReplayAppend(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	body := `{"action":"play","params":{"query":"secret"},"idempotency_key":"audit-once-key-01"}`
	id := rpcID(t, s, body)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.requestAudit) != 1 {
		t.Fatal("idempotent replay appended audit")
	}
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", map[string]any{"accepted": true}), nil), 200)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "rejected", map[string]any{"error": "no"}), nil), 200)
	row := auditRows(t, auditRead(t, s, adminToken, ""))[0].(map[string]any)
	if row["state"] != "responded" || row["result_status"] != "ok" || len(s.requestAudit) != 1 {
		t.Fatal("duplicate changed first transport result")
	}
	// A player reply is transport evidence; this API never labels it as playback success.
	if _, exists := row["playback_confirmed"]; exists {
		t.Fatal("audit invented execution evidence")
	}
	before := len(s.requestAudit)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"play","params":{}}`, nil), 400)
	if len(s.requestAudit) != before {
		t.Fatal("invalid admission appended audit")
	}
}
func TestRequestAuditFiltersAndRetention(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	now := time.Unix(1800000000, 0)
	s.now = func() time.Time { return now }
	for i := 0; i < 40; i++ {
		s.appendRequestAudit(entry{id: fmt.Sprintf("%032x", i+1), permission: "play", owner: "admin", expires: now.Add(time.Minute)}, "first", now)
	}
	if len(s.requestAudit) != requestAuditPerDevice {
		t.Fatal("per-device bound failed")
	}
	rows := auditRows(t, auditRead(t, s, adminToken, ""))
	if rows[0].(map[string]any)["request_id"] != fmt.Sprintf("%032x", 40) {
		t.Fatal("not newest admission first")
	}
	// Wall clock moving backwards does not change admission order or report a negative age.
	earlier := now.Add(-time.Minute)
	s.appendRequestAudit(entry{id: fmt.Sprintf("%032x", 41), permission: "play", owner: "admin", expires: now.Add(time.Minute)}, "first", earlier)
	rows = auditRows(t, auditRead(t, s, adminToken, ""))
	if rows[0].(map[string]any)["request_id"] != fmt.Sprintf("%032x", 41) {
		t.Fatal("clock sorted instead of admission order")
	}
	now = earlier.Add(-time.Minute)
	if auditRows(t, auditRead(t, s, adminToken, ""))[0].(map[string]any)["age_ms"] != float64(0) {
		t.Fatal("negative age")
	}
	now = time.Unix(1800000000, 0).Add(10*time.Minute + time.Millisecond)
	before := len(s.requestAudit)
	if len(auditRows(t, auditRead(t, s, adminToken, ""))) != 0 || len(s.requestAudit) != before {
		t.Fatal("expired read retained evidence or mutated storage")
	}
	s.appendRequestAudit(entry{id: strings.Repeat("a", 32), permission: "play", owner: "admin", expires: now.Add(time.Minute)}, "first", now)
	if len(s.requestAudit) != 1 {
		t.Fatal("append did not prune expired metadata")
	}
	for i := 0; i < 600; i++ {
		s.appendRequestAudit(entry{id: fmt.Sprintf("%032x", i+100), permission: "play", owner: "admin", expires: now.Add(time.Minute)}, fmt.Sprintf("device%d", i), now)
	}
	if len(s.requestAudit) != requestAuditTotal {
		t.Fatal("global retention bound failed")
	}
	before = len(s.requestAudit)
	for _, family := range []string{"status", "capabilities", "inspect", "playback_preparation_diagnostics", "command_activity_diagnostics", "providers", "profile_save", "screenshot"} {
		s.appendRequestAudit(entry{id: strings.Repeat("f", 32), permission: family}, "first", now)
	}
	if len(s.requestAudit) != before {
		t.Fatal("untracked read family evicted activity")
	}
	for _, action := range []string{"play", "play_catalog", "playback.pause", "restart.stream", "provider", "profile", "input.up", "command.set_volume", "local_media.sources", "plex_queue.status", "plex_library.list", "lifecycle.standby", "kiosk.status"} {
		if !requestAuditFamily(action) {
			t.Fatalf("missing family %s", action)
		}
	}
}
func TestRequestAuditOperatorCurrentScopesAndTransportRoles(t *testing.T) {
	a := strings.Repeat("q", 32)
	b := strings.Repeat("w", 32)
	ah := sha256.Sum256([]byte(a))
	bh := sha256.Sum256([]byte(b))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "first-op", CredentialSHA256: hex.EncodeToString(ah[:]), DeviceIDs: []string{"first"}, Actions: []string{"play"}}, {ID: "second-op", CredentialSHA256: hex.EncodeToString(bh[:]), DeviceIDs: []string{"first"}, Actions: []string{"play"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	submit := func(token string) string {
		w := request(s, "POST", "/api/requests?device_id=first", token, `{"action":"play","params":{"query":"test"}}`, nil)
		expect(t, w, 202)
		var out map[string]string
		_ = json.Unmarshal(w.Body.Bytes(), &out)
		return out["id"]
	}
	adminID := submit(adminToken)
	aID := submit(a)
	bID := submit(b)
	rows := auditRows(t, auditRead(t, s, a, ""))
	if len(rows) != 1 || rows[0].(map[string]any)["request_id"] != aID {
		t.Fatal("operator saw another caller")
	}
	if !reflect.DeepEqual(rows[0].(map[string]any)["initiator"], map[string]any{"credential_role": "operator", "operator_id": "first-op"}) {
		t.Fatal(rows)
	}
	for _, id := range []string{adminID, bID} {
		out := auditRead(t, s, a, "&id="+id)
		if out["lookup"] != "not_retained" || len(auditRows(t, out)) != 0 {
			t.Fatal("foreign lookup leaked")
		}
	}
	delete(s.operators[0].actions, "play")
	if len(auditRows(t, auditRead(t, s, a, ""))) != 0 {
		t.Fatal("revoked current action still visible")
	}
	if len(auditRows(t, auditRead(t, s, adminToken, ""))) != 3 {
		t.Fatal("admin cannot see all roles")
	}
	expect(t, request(s, "GET", requestAuditPath+"?device_id=second", a, "", nil), 404)
	for _, token := range []string{firstToken, secondToken} {
		expect(t, request(s, "GET", requestAuditPath+"?device_id=first", token, "", nil), 403)
	}
	expect(t, request(s, "GET", requestAuditPath+"?device_id=first", "", "", nil), 401)
	expect(t, request(s, "POST", requestAuditPath+"?device_id=first", adminToken, "{}", nil), 405)
	for _, query := range []string{"", "?device_id=first&id=BAD", "?device_id=first&private=secret", "?device_id=first&device_id=first"} {
		expect(t, request(s, "GET", requestAuditPath+query, adminToken, "", nil), 400)
	}
	expect(t, request(s, "GET", requestAuditPath+"?device_id=missing", adminToken, "", nil), 404)
}
func TestRequestAuditPreservesLegacyEnvelopeAndDoesNotTrackReads(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	id := rpcID(t, s, `{"action":"plex_library","params":{"runtime":"runtime-1","op":"list"}}`)
	if len(s.requestAudit) != 1 || s.requestAudit[0].action != "plex_library.list" {
		t.Fatal("library list lost precise operation")
	}
	var wire map[string]json.RawMessage
	if json.Unmarshal(s.devices[0].queue[0].data, &wire) != nil || len(wire) != 4 || string(wire["action"]) != `"plex_library"` || string(wire["id"]) != `"`+id+`"` {
		t.Fatal("legacy request envelope changed")
	}
	for _, body := range []string{`{"action":"status","params":{}}`, `{"action":"capabilities","params":{}}`, `{"action":"playback_preparation_diagnostics","params":{"version":1,"runtime":"r"}}`, `{"action":"command_activity_diagnostics","params":{"version":2,"runtime":"r"}}`} {
		rpcID(t, s, body)
	}
	if len(s.requestAudit) != 1 {
		t.Fatal("diagnostic reads evicted audit evidence")
	}
	s2 := newTestServer(t)
	defer s2.Close()
	if len(auditRows(t, auditRead(t, s2, adminToken, ""))) != 0 || s2.playerStateEpoch == s.playerStateEpoch {
		t.Fatal("volatile history/epoch survived restart")
	}
}

func TestRequestAuditElapsedAgeIndependentOfWallMillisecondRounding(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	accepted := time.Unix(1800000000, 999500000)
	now := accepted.Add(750 * time.Microsecond)
	s.now = func() time.Time { return now }
	s.appendRequestAudit(entry{id: strings.Repeat("1", 32), permission: "play", owner: "admin", expires: accepted.Add(time.Minute)}, "first", accepted)
	row := auditRows(t, auditRead(t, s, adminToken, ""))[0].(map[string]any)
	if row["accepted_at_ms"] != float64(accepted.UnixMilli()) || row["age_ms"] != float64(0) || now.UnixMilli()-accepted.UnixMilli() != 1 {
		t.Fatal("elapsed age inferred from independently floored wall timestamps")
	}
	now = accepted.Add(-time.Hour)
	row = auditRows(t, auditRead(t, s, adminToken, ""))[0].(map[string]any)
	if row["age_ms"] != float64(0) || row["accepted_at_ms"].(float64) <= float64(now.UnixMilli()) {
		t.Fatal("clock reversal changed timestamp provenance or produced negative age")
	}
}

func TestRequestAuditExactRetentionBoundaryDoesNotMutate(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	accepted := time.Unix(1800000000, 0)
	now := accepted.Add(10 * time.Minute)
	s.now = func() time.Time { return now }
	s.appendRequestAudit(entry{id: strings.Repeat("1", 32), permission: "play", owner: "admin", expires: accepted.Add(time.Hour)}, "first", accepted)
	if len(auditRows(t, auditRead(t, s, adminToken, ""))) != 1 {
		t.Fatal("exact ten-minute evidence omitted")
	}
	now = now.Add(time.Nanosecond)
	if len(auditRows(t, auditRead(t, s, adminToken, ""))) != 0 || len(s.requestAudit) != 1 {
		t.Fatal("submillisecond stale evidence returned or GET mutated audit")
	}
}
