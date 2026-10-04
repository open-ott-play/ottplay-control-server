package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

var diagnosticOperatorToken = strings.Repeat("d", 32)

func diagnosticConfig() config.Config {
	c := testConfig()
	digest := sha256.Sum256([]byte(diagnosticOperatorToken))
	// Existing long-lease cases deliberately use the configurable upper TTL.
	c.Diagnostics = &config.Diagnostics{RuntimeTTLMS: 600000, Operators: []config.DiagnosticsOperator{{ID: "operator", CredentialSHA256: hex.EncodeToString(digest[:]), DeviceIDs: []string{"first"}, Actions: []string{"runtimes.read", "sessions.start", "sessions.stop", "sessions.read", "runtimes.revoke"}}}}
	c.Devices[0].Diagnostics = &config.DeviceDiagnostics{Enabled: true}
	c.Defaults()
	return c
}
func diagnosticServer(t *testing.T) (*Server, *time.Time) {
	t.Helper()
	s, e := New(diagnosticConfig())
	if e != nil {
		t.Fatal(e)
	}
	now := time.Date(2026, 10, 4, 12, 0, 0, 0, time.UTC)
	s.now = func() time.Time { return now }
	return s, &now
}
func diagnosticCall(t *testing.T, s *Server, method, path, token string, body any, status int) map[string]any {
	t.Helper()
	text := ""
	if body != nil {
		b, e := json.Marshal(body)
		if e != nil {
			t.Fatal(e)
		}
		text = string(b)
	}
	w := request(s, method, diagnosticsPrefix+path, token, text, nil)
	expect(t, w, status)
	var v map[string]any
	if e := json.Unmarshal(w.Body.Bytes(), &v); e != nil {
		t.Fatal(e)
	}
	if v["server_epoch"] != s.diagnostics.epoch || v["diagnostics_protocol"] != float64(2) {
		t.Fatal("missing protocol/epoch envelope")
	}
	return v
}
func diagnosticRegister(t *testing.T, s *Server) (string, string) {
	t.Helper()
	v := diagnosticCall(t, s, "POST", "/runtimes", firstToken, map[string]any{"instance_id": "tab", "boot_id": "boot", "capabilities": []string{"playback"}, "consent": map[string]any{"granted": true, "epoch": "grant"}}, 201)
	if len(v["limits"].(map[string]any)) != 5 {
		t.Fatal("registration limits changed")
	}
	return v["runtime_id"].(string), v["runtime_credential"].(string)
}
func diagnosticStartBody(s *Server, runtime, key string) map[string]any {
	return map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": key, "device_id": "first", "runtime_id": runtime, "consent_epoch": "grant", "lease_ms": 60000, "profile": "standard"}
}
func diagnosticStart(t *testing.T, s *Server, runtime, key string) map[string]any {
	t.Helper()
	return diagnosticCall(t, s, "POST", "/sessions", diagnosticOperatorToken, diagnosticStartBody(s, runtime, key), 202)
}
func diagnosticAck(t *testing.T, s *Server, runtime, token string, receipt map[string]any, status int) {
	t.Helper()
	diagnosticCall(t, s, "POST", "/results", token, map[string]any{"runtime_id": runtime, "session_id": receipt["session_id"], "request_id": receipt["request_id"], "control_revision": receipt["control_revision"], "status": "applied"}, status)
}
func diagnosticPollBody(runtime string, seq int, granted bool) map[string]any {
	c := map[string]any{"granted": granted}
	if granted {
		c["epoch"] = "grant"
	}
	return map[string]any{"runtime_id": runtime, "poll_seq": seq, "last_control_revision": 0, "consent": c}
}
func diagnosticBatch(runtime, session string, first int) map[string]any {
	return map[string]any{"runtime_id": runtime, "session_id": session, "first_seq": first, "events": []any{map[string]any{"elapsed_ms": 1000, "kind": "playback", "code": "sample", "metrics": map[string]any{"currentTime": 1, "paused": false}}}}
}

func TestDiagnosticsEndToEndAndV1Isolation(t *testing.T) {
	s, _ := diagnosticServer(t)
	rt, token := diagnosticRegister(t, s)
	other, otherToken := diagnosticRegister(t, s)
	if rt == other || token == otherToken {
		t.Fatal("same device tabs share a runtime identity")
	}
	started := diagnosticStart(t, s, rt, "start1")
	sid := started["session_id"].(string)
	poll := diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
	control := poll["control"].(map[string]any)
	if _, present := control["control_revision"]; present {
		t.Fatal("nested revision violates client schema")
	}
	if control["action"] != "start" || poll["control_revision"] != started["control_revision"] {
		t.Fatal("wrong control")
	}
	idle := diagnosticCall(t, s, "POST", "/poll", otherToken, diagnosticPollBody(other, 1, true), 200)
	if idle["control"] != nil {
		t.Fatal("cross-runtime delivery")
	}
	if got := commands(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), false); len(got) != 0 {
		t.Fatal("diagnostics entered v1 queue")
	}
	diagnosticAck(t, s, rt, token, started, 200)
	batch := diagnosticBatch(rt, sid, 1)
	events := diagnosticCall(t, s, "POST", "/events", token, batch, 200)
	if events["accepted_through_seq"] != float64(1) {
		t.Fatal(events)
	}
	page := diagnosticCall(t, s, "GET", "/sessions/"+sid+"/events?limit=1", diagnosticOperatorToken, nil, 200)
	item := page["events"].([]any)[0].(map[string]any)
	if item["seq"] != float64(1) || item["event"].(map[string]any)["code"] != "sample" {
		t.Fatal(page)
	}
	stop := diagnosticCall(t, s, "POST", "/sessions/"+sid+"/stop", diagnosticOperatorToken, map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": "stop1", "reason": "operator"}, 202)
	diagnosticCall(t, s, "POST", "/events", token, diagnosticBatch(rt, sid, 2), 409)
	view := diagnosticCall(t, s, "GET", "/sessions/"+sid, diagnosticOperatorToken, nil, 200)
	if view["device_stop_confirmed"] != false {
		t.Fatal("claimed unacknowledged stop")
	}
	diagnosticAck(t, s, rt, token, stop, 200)
	diagnosticAck(t, s, rt, token, stop, 200)
	view = diagnosticCall(t, s, "GET", "/sessions/"+sid, diagnosticOperatorToken, nil, 200)
	if view["state"] != "stopped" || view["device_stop_confirmed"] != true {
		t.Fatal(view)
	}
	// Replayed acceptance is not a new start, nor a current-state query.
	replay := diagnosticStart(t, s, rt, "start1")
	if replay["session_id"] != sid || s.diagnostics.runtimes[rt].active != "" {
		t.Fatal("replay revived session")
	}
}

func TestDiagnosticsAuthorizationEpochAndIdempotency(t *testing.T) {
	s, _ := diagnosticServer(t)
	rt, token := diagnosticRegister(t, s)
	rt2, token2 := diagnosticRegister(t, s)
	for _, cred := range []string{adminToken, firstToken, diagnosticOperatorToken} {
		diagnosticCall(t, s, "POST", "/poll", cred, diagnosticPollBody(rt, 1, true), 403)
	}
	diagnosticCall(t, s, "POST", "/poll", token2, diagnosticPollBody(rt, 1, true), 403)
	diagnosticCall(t, s, "GET", "/runtimes?device_id=second", diagnosticOperatorToken, nil, 404)
	diagnosticCall(t, s, "POST", "/runtimes", secondToken, map[string]any{}, 403)
	v := diagnosticStartBody(s, rt, "shared")
	v["server_epoch"] = "previous"
	diagnosticCall(t, s, "POST", "/sessions", diagnosticOperatorToken, v, 409)
	started := diagnosticStart(t, s, rt, "shared")
	sid := started["session_id"].(string)
	diagnosticCall(t, s, "POST", "/sessions", diagnosticOperatorToken, diagnosticStartBody(s, rt2, "shared"), 409)
	diagnosticCall(t, s, "POST", "/sessions/"+sid+"/stop", diagnosticOperatorToken, map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": "shared", "reason": "operator"}, 409)
	diagnosticCall(t, s, "DELETE", "/runtimes/"+rt, token, nil, 403)
	expect(t, request(s, "DELETE", diagnosticsPrefix+"/runtimes/"+rt, diagnosticOperatorToken, "", nil), 409)
	expect(t, request(s, "DELETE", diagnosticsPrefix+"/runtimes/"+rt, diagnosticOperatorToken, "", map[string]string{"X-OTT-Diagnostics-Epoch": s.diagnostics.epoch}), 200)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 401)
	view := diagnosticCall(t, s, "GET", "/sessions/"+sid, diagnosticOperatorToken, nil, 200)
	if view["state"] != "revoked" || view["device_stop_confirmed"] != false {
		t.Fatal(view)
	}
}

func TestDiagnosticsConsentOrderingAndLeaseExpiry(t *testing.T) {
	s, _ := diagnosticServer(t)
	rt, token := diagnosticRegister(t, s)
	started := diagnosticStart(t, s, rt, "start")
	diagnosticAck(t, s, rt, token, started, 200)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, true), 200)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, false), 409)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, false), 409)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, true), 200)
	poll := diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 3, false), 200)
	if poll["control"] != nil || s.diagnostics.runtimes[rt] != nil {
		t.Fatal("withdrawal did not retire runtime")
	}
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 4, true), 401)
	sid := started["session_id"].(string)
	view := diagnosticCall(t, s, "GET", "/sessions/"+sid, diagnosticOperatorToken, nil, 200)
	if view["state"] != "revoked" || view["device_stop_confirmed"] != false {
		t.Fatal(view)
	}
	s, now := diagnosticServer(t)
	rt, token = diagnosticRegister(t, s)
	started = diagnosticStart(t, s, rt, "start")
	diagnosticAck(t, s, rt, token, started, 200)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
	sid = started["session_id"].(string)
	stop := diagnosticCall(t, s, "POST", "/sessions/"+sid+"/stop", diagnosticOperatorToken, map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": "stop", "reason": "operator"}, 202)
	*now = now.Add(time.Minute)
	view = diagnosticCall(t, s, "GET", "/sessions/"+sid, diagnosticOperatorToken, nil, 200)
	if view["state"] != "expired" || view["device_stop_confirmed"] != false {
		t.Fatal(view)
	}
	diagnosticAck(t, s, rt, token, stop, 200)
	if s.diagnostics.sessions[sid].state != "stopped" {
		t.Fatal("exact late stop acknowledgement lost")
	}
}

func TestDiagnosticsRepeatedWithdrawalAndRegistrationTimeout(t *testing.T) {
	s, now := diagnosticServer(t)
	preserved, keepToken := diagnosticRegister(t, s)
	diagnosticCall(t, s, "POST", "/poll", keepToken, diagnosticPollBody(preserved, 1, true), 200)
	for i := 0; i < 8; i++ {
		rt, token := diagnosticRegister(t, s)
		diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, false), 200)
		diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, true), 401)
	}
	if len(s.diagnostics.runtimes) != 1 || s.diagnostics.runtimes[preserved] == nil {
		t.Fatal("withdrawal exhausted slots or evicted another runtime")
	}
	*now = now.Add(time.Minute)
	for i := 0; i < 3; i++ {
		diagnosticRegister(t, s)
	}
	if len(s.diagnostics.runtimes) != 4 {
		t.Fatal("lost-response fixture missing")
	}
	*now = now.Add(10 * time.Second)
	diagnosticCall(t, s, "GET", "/runtimes?device_id=first", diagnosticOperatorToken, nil, 200)
	if len(s.diagnostics.runtimes) != 1 || s.diagnostics.runtimes[preserved] == nil {
		t.Fatal("unconfirmed registrations were not reclaimed independently")
	}
	diagnosticRegister(t, s)
}

func TestDiagnosticsSlidingRuntimeTTLAndFiniteReplayHorizon(t *testing.T) {
	s, now := diagnosticServer(t)
	rt, token := diagnosticRegister(t, s)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
	*now = now.Add(9 * time.Minute)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, true), 200)
	expires := s.diagnostics.runtimes[rt].expires
	*now = now.Add(9 * time.Minute)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, false), 409)
	if !s.diagnostics.runtimes[rt].expires.Equal(expires) {
		t.Fatal("failed poll extended TTL")
	}
	*now = expires
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, false), 401)
	s, now = diagnosticServer(t)
	s.diagnostics.cfg.IdempotencyRetentionMS = 1000
	rt, token = diagnosticRegister(t, s)
	body := diagnosticStartBody(s, rt, "finite")
	body["lease_ms"] = 1000
	first := diagnosticCall(t, s, "POST", "/sessions", diagnosticOperatorToken, body, 202)
	*now = now.Add(time.Second)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
	second := diagnosticCall(t, s, "POST", "/sessions", diagnosticOperatorToken, body, 202)
	if first["session_id"] == second["session_id"] {
		t.Fatal("finite horizon test did not create a new session")
	}
}

func TestDiagnosticsStrictEventsGapsReplayAndRetention(t *testing.T) {
	s, now := diagnosticServer(t)
	rt, token := diagnosticRegister(t, s)
	start := diagnosticStart(t, s, rt, "start")
	diagnosticAck(t, s, rt, token, start, 200)
	sid := start["session_id"].(string)
	batch := diagnosticBatch(rt, sid, 3)
	receipt := diagnosticCall(t, s, "POST", "/events", token, batch, 200)
	if receipt["dropped_total"] != float64(2) {
		t.Fatal(receipt)
	}
	diagnosticCall(t, s, "POST", "/events", token, batch, 200)
	if len(s.diagnostics.sessions[sid].events) != 1 {
		t.Fatal("replay duplicated storage")
	}
	changed := diagnosticBatch(rt, sid, 3)
	changed["events"].([]any)[0].(map[string]any)["elapsed_ms"] = 2000
	diagnosticCall(t, s, "POST", "/events", token, changed, 409)
	diagnosticCall(t, s, "POST", "/events", token, diagnosticBatch(rt, sid, 2), 409)
	invalid := []string{`{"elapsed_ms":0,"kind":"playback","code":"sample","message":"secret"}`, `{"elapsed_ms":0,"kind":"playback","code":"sample","metrics":{"paused":1}}`, `{"elapsed_ms":0,"kind":"playback","code":"sample","metrics":{"currentTime":null}}`, `{"elapsed_ms":0,"kind":"playback","code":"sample","metrics":{"url":"secret"}}`, `{"elapsed_ms":0,"kind":"playback","code":"sample","metrics":{"errors":1,"errors":2}}`, `{"elapsed_ms":1e999,"kind":"playback","code":"sample"}`}
	for _, event := range invalid {
		body := fmt.Sprintf(`{"runtime_id":%q,"session_id":%q,"first_seq":4,"events":[%s]}`, rt, sid, event)
		expect(t, request(s, "POST", diagnosticsPrefix+"/events", token, body, nil), 400)
	}
	if s.diagnostics.sessions[sid].accepted != 3 {
		t.Fatal("invalid batch advanced sequence")
	}
	s.diagnostics.cfg.EventRetentionMS = 1000
	*now = now.Add(time.Second)
	page := diagnosticCall(t, s, "GET", "/sessions/"+sid+"/events", diagnosticOperatorToken, nil, 200)
	if len(page["events"].([]any)) != 0 || page["dropped_total"] != float64(3) || page["truncated_before_seq"] != float64(4) {
		t.Fatal(page)
	}
	diagnosticCall(t, s, "POST", "/events", token, batch, 200)
	if s.diagnostics.eventBytes != 0 {
		t.Fatal("replay after eviction restored events")
	}
}

func TestDiagnosticsStateCapsReserveStopAndTelemetryIsolation(t *testing.T) {
	s, _ := diagnosticServer(t)
	s.diagnostics.cfg.MaxIdempotencyRecords = 2
	rt, token := diagnosticRegister(t, s)
	other, _ := diagnosticRegister(t, s)
	start := diagnosticStart(t, s, rt, "start")
	diagnosticAck(t, s, rt, token, start, 200)
	diagnosticCall(t, s, "POST", "/sessions", diagnosticOperatorToken, diagnosticStartBody(s, other, "other"), 429)
	sid := start["session_id"].(string)
	s.diagnostics.runtimes[rt].eventRate = diagBudget{at: s.now(), used: 120}
	diagnosticCall(t, s, "POST", "/events", token, diagnosticBatch(rt, sid, 1), 429)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
	stop := diagnosticCall(t, s, "POST", "/sessions/"+sid+"/stop", diagnosticOperatorToken, map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": "stop", "reason": "operator"}, 202)
	diagnosticAck(t, s, rt, token, stop, 200)
	if len(s.diagnostics.idempotency) != 2 || s.diagnostics.reservedStops != 0 {
		t.Fatal("unbounded receipt reservation")
	}
	// Occupying all telemetry admissions must not block the control path.
	for i := 0; i < cap(s.diagnostics.eventSlots); i++ {
		s.diagnostics.eventSlots <- struct{}{}
	}
	diagnosticCall(t, s, "POST", "/events", token, diagnosticBatch(rt, sid, 2), 429)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, true), 200)
	for len(s.diagnostics.eventSlots) > 0 {
		<-s.diagnostics.eventSlots
	}
}

func TestDiagnosticsEventByteCapsAndConcurrentReplay(t *testing.T) {
	s, _ := diagnosticServer(t)
	s.diagnostics.cfg.EventBytesPerRuntime = 1024
	s.diagnostics.cfg.EventBytesTotal = 1024
	rt, token := diagnosticRegister(t, s)
	start := diagnosticStart(t, s, rt, "start")
	diagnosticAck(t, s, rt, token, start, 200)
	sid := start["session_id"].(string)
	for seq := 1; seq <= 20; seq++ {
		diagnosticCall(t, s, "POST", "/events", token, diagnosticBatch(rt, sid, seq), 200)
	}
	ss := s.diagnostics.sessions[sid]
	if ss.eventBytes > 1024 || s.diagnostics.eventBytes > 1024 || ss.dropped == 0 {
		t.Fatal("ring did not bound bytes")
	}
	b, _ := json.Marshal(diagnosticBatch(rt, sid, 20))
	var wg sync.WaitGroup
	results := make(chan *httptest.ResponseRecorder, 8)
	for i := 0; i < 8; i++ {
		wg.Go(func() { results <- request(s, "POST", diagnosticsPrefix+"/events", token, string(b), nil) })
	}
	wg.Wait()
	close(results)
	for w := range results {
		expect(t, w, 200)
	}
	if ss.accepted != 20 {
		t.Fatal("concurrent replay changed sequence")
	}
	overflow := diagnosticBatch(rt, sid, 1)
	overflow["first_seq"] = uint64(diagSafeInteger)
	overflow["events"] = []any{map[string]any{"elapsed_ms": 0, "kind": "lifecycle", "code": "sample"}, map[string]any{"elapsed_ms": 0, "kind": "lifecycle", "code": "sample"}}
	diagnosticCall(t, s, "POST", "/events", token, overflow, 400)
}

func TestDiagnosticsFramingStrictConsentAndDefaultOff(t *testing.T) {
	s := newTestServer(t)
	diagnosticCall(t, s, "POST", "/runtimes", firstToken, map[string]any{}, 403)
	s, _ = diagnosticServer(t)
	for _, consent := range []string{`{"Granted":true,"Epoch":"grant"}`, `{"granted":false,"Granted":true,"epoch":"grant"}`, `{"granted":false,"Epoch":null}`, `{"granted":false,"Epoch":""}`, `{"granted":false,"epoch":""}`, `{"granted":false,"epoch":null}`, `{"granted":true}`, `{"granted":true,"epoch":"grant","extra":1}`} {
		body := `{"instance_id":"tab","boot_id":"boot","capabilities":[],"consent":` + consent + `}`
		expect(t, request(s, "POST", diagnosticsPrefix+"/runtimes", firstToken, body, nil), 400)
	}
	expect(t, request(s, "POST", diagnosticsPrefix+"/runtimes", firstToken, `{"instance_id":"tab","instance_id":"other"}`, nil), 400)
	expect(t, request(s, "POST", diagnosticsPrefix+"/runtimes", firstToken, strings.Repeat("x", 4097), nil), 413)
	expect(t, request(s, "POST", diagnosticsPrefix+"/runtimes", firstToken, `{}`, map[string]string{"Origin": "https://attacker.example"}), 403)
	expect(t, request(s, "OPTIONS", diagnosticsPrefix+"/poll", "", "", map[string]string{"Origin": "https://player.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}), 200)
}
