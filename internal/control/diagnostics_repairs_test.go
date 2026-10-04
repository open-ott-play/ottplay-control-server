package control

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func repairTestServer(t *testing.T) (*Server, *time.Time) {
	t.Helper()
	c := diagnosticConfig()
	c.Diagnostics.Operators[0].Actions = append(c.Diagnostics.Operators[0].Actions, "repairs.start", "repairs.read")
	s, e := New(c)
	if e != nil {
		t.Fatal(e)
	}
	now := time.Date(2026, 10, 4, 20, 0, 0, 0, time.UTC)
	s.now = func() time.Time { return now }
	return s, &now
}
func repairRegister(t *testing.T, s *Server) (string, string) {
	t.Helper()
	v := diagnosticCall(t, s, "POST", "/runtimes", firstToken, map[string]any{"instance_id": "tab", "boot_id": "boot", "capabilities": []string{"playback", "network", "input", "epg", "repairs"}, "consent": map[string]any{"granted": true, "epoch": "grant"}}, 201)
	if len(v["limits"].(map[string]any)) != 5 || v["runtime_ttl_ms"] != float64(10000) {
		t.Fatal("registration wire changed")
	}
	return v["runtime_id"].(string), v["runtime_credential"].(string)
}
func repairStartBody(s *Server, rt, key, action string) map[string]any {
	return map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": key, "device_id": "first", "runtime_id": rt, "consent_epoch": "grant", "action": action, "deadline_ms": 30000}
}
func repairStart(t *testing.T, s *Server, rt, key, action string) string {
	t.Helper()
	v := diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, repairStartBody(s, rt, key, action), 202)
	if len(v) != 5 || v["state"] != "pending" {
		t.Fatal("unexpected create envelope")
	}
	return v["repair_id"].(string)
}
func repairPoll(t *testing.T, s *Server, rt, token string, status int) map[string]any {
	t.Helper()
	return diagnosticCall(t, s, "POST", "/repairs/poll", token, map[string]any{"runtime_id": rt}, status)
}
func repairResultBody(rt, id, status string) map[string]any {
	return map[string]any{"runtime_id": rt, "repair_id": id, "status": status}
}
func repairAck(t *testing.T, s *Server, rt, token, id, status string, want int) map[string]any {
	t.Helper()
	return diagnosticCall(t, s, "POST", "/repairs/results", token, repairResultBody(rt, id, status), want)
}

func TestDiagnosticsRepairsExactRuntimeAndIndependentDelivery(t *testing.T) {
	s, now := repairTestServer(t)
	rt, token := repairRegister(t, s)
	other, otherToken := repairRegister(t, s)
	id := repairStart(t, s, rt, "repair", "restart_stream")
	expires := s.diagnostics.runtimes[rt].expires
	idle := repairPoll(t, s, other, otherToken, 200)
	if idle["repair"] != nil || len(idle) != 3 {
		t.Fatal("repair leaked or poll schema changed")
	}
	pending := repairPoll(t, s, rt, token, 200)
	control := pending["repair"].(map[string]any)
	if len(pending) != 3 || len(control) != 4 || control["repair_id"] != id || control["lease_ms"] != float64(30000) {
		t.Fatal(pending)
	}
	*now = now.Add(time.Second)
	again := repairPoll(t, s, rt, token, 200)["repair"].(map[string]any)
	if again["lease_ms"] != float64(29000) || !s.diagnostics.runtimes[rt].expires.Equal(expires) {
		t.Fatal("repair delivery renewed deadline/TTL")
	}
	if s.diagnostics.runtimes[rt].active != "" || len(s.diagnostics.sessions) != 0 || s.diagnostics.runtimes[rt].revision != 0 {
		t.Fatal("repair mutated diagnostic session/control")
	}
	repairAck(t, s, other, otherToken, id, "applied", 403)
	repairAck(t, s, rt, token, id, "applied", 200)
	repairAck(t, s, rt, token, id, "applied", 200)
	conflict := repairAck(t, s, rt, token, id, "rejected", 409)
	if conflict["error"].(map[string]any)["code"] != "result_conflict" {
		t.Fatal(conflict)
	}
	if repairPoll(t, s, rt, token, 200)["repair"] != nil {
		t.Fatal("terminal repair still delivered")
	}
	v := diagnosticCall(t, s, "GET", "/repairs/"+id, diagnosticOperatorToken, nil, 200)
	if len(v) != 8 || v["state"] != "applied" || v["action"] != "restart_stream" || v["runtime_id"] != rt {
		t.Fatal(v)
	}
	if len(commands(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), false)) != 0 {
		t.Fatal("repair entered v1 queue")
	}
}

func TestDiagnosticsRepairsActionResultsAndLateAcknowledgements(t *testing.T) {
	for _, action := range []string{"restart_stream", "reload_player"} {
		for _, status := range []string{"applied", "accepted", "rejected", "unsupported"} {
			t.Run(action+"/"+status, func(t *testing.T) {
				s, now := repairTestServer(t)
				rt, token := repairRegister(t, s)
				diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
				id := repairStart(t, s, rt, "repair", action)
				want := 200
				if (action == "restart_stream" && status == "accepted") || (action == "reload_player" && status == "applied") {
					want = 400
				}
				repairAck(t, s, rt, token, id, status, want)
				if want == 400 {
					if s.diagnostics.repairs[id].state != "pending" {
						t.Fatal("invalid status changed state")
					}
					return
				}
				until := s.diagnostics.repairs[id].retainUntil
				repairAck(t, s, rt, token, id, status, 200)
				if !s.diagnostics.repairs[id].retainUntil.Equal(until) {
					t.Fatal("replay prolonged history retention")
				}
				*now = now.Add(30 * time.Second)
				late := repairAck(t, s, rt, token, id, status, 409)
				if late["error"].(map[string]any)["code"] != "repair_terminal" || s.diagnostics.repairs[id].state != status {
					t.Fatal("late ACK rewrote recorded history")
				}
			})
		}
	}
}

func TestDiagnosticsRepairsLifecycleRevocation(t *testing.T) {
	for _, mode := range []string{"deadline", "consent_epoch", "withdrawal", "runtime_delete", "runtime_expiry", "initial_ttl"} {
		t.Run(mode, func(t *testing.T) {
			s, now := repairTestServer(t)
			rt, token := repairRegister(t, s)
			if mode != "initial_ttl" {
				diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
			}
			id := repairStart(t, s, rt, "repair", "restart_stream")
			wantStatus := 409
			wantState := "revoked"
			switch mode {
			case "deadline":
				*now = now.Add(30 * time.Second)
				wantState = "expired"
			case "consent_epoch":
				body := diagnosticPollBody(rt, 2, true)
				body["consent"] = map[string]any{"granted": true, "epoch": "other-grant"}
				diagnosticCall(t, s, "POST", "/poll", token, body, 200)
			case "withdrawal":
				diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 2, false), 200)
				wantStatus = 401
			case "runtime_delete":
				expect(t, request(s, "DELETE", diagnosticsPrefix+"/runtimes/"+rt, diagnosticOperatorToken, "", map[string]string{"X-OTT-Diagnostics-Epoch": s.diagnostics.epoch}), 200)
				wantStatus = 401
			case "runtime_expiry":
				*now = now.Add(10 * time.Minute)
				wantStatus = 401
			case "initial_ttl":
				*now = now.Add(9 * time.Second)
				repairPoll(t, s, rt, token, 200)
				*now = now.Add(time.Second)
				wantStatus = 401
			}
			ack := repairAck(t, s, rt, token, id, "applied", wantStatus)
			if wantStatus == 409 && ack["error"].(map[string]any)["code"] != "repair_terminal" {
				t.Fatal(ack)
			}
			view := diagnosticCall(t, s, "GET", "/repairs/"+id, diagnosticOperatorToken, nil, 200)
			if view["state"] != wantState {
				t.Fatal(view)
			}
			if runtime := s.diagnostics.runtimes[rt]; runtime != nil && runtime.pendingRepair != "" {
				t.Fatal("terminal repair retained pending slot")
			}
		})
	}
}

func TestDiagnosticsRepairsPendingDeadlineAndSharedIdempotency(t *testing.T) {
	s, now := repairTestServer(t)
	rt, token := repairRegister(t, s)
	other, _ := repairRegister(t, s)
	diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
	first := repairStart(t, s, rt, "shared", "restart_stream")
	if repairStart(t, s, rt, "shared", "restart_stream") != first {
		t.Fatal("idempotent creation changed target")
	}
	diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, repairStartBody(s, other, "shared", "restart_stream"), 409)
	diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, repairStartBody(s, rt, "shared", "reload_player"), 409)
	diagnosticCall(t, s, "POST", "/sessions", diagnosticOperatorToken, diagnosticStartBody(s, rt, "shared"), 409)
	capture := diagnosticStart(t, s, rt, "capture")
	diagnosticCall(t, s, "POST", "/sessions/"+capture["session_id"].(string)+"/stop", diagnosticOperatorToken, map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": "shared", "reason": "operator"}, 409)
	if s.diagnostics.sessions[capture["session_id"].(string)].state != "start_pending" {
		t.Fatal("cross-action key conflict issued stop")
	}

	diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, repairStartBody(s, rt, "next", "restart_stream"), 409)
	*now = now.Add(30 * time.Second)
	repairPoll(t, s, rt, token, 200)
	second := repairStart(t, s, rt, "next", "restart_stream")
	if second == first || s.diagnostics.repairs[first].state != "expired" {
		t.Fatal("deadline did not release pending slot with history preserved")
	}
	if repairStart(t, s, rt, "shared", "restart_stream") != first || s.diagnostics.runtimes[rt].pendingRepair != second {
		t.Fatal("old receipt replay revived or replaced operation")
	}
	repairAck(t, s, rt, token, first, "applied", 409)
}

func TestDiagnosticsRepairsScopesCapabilitiesAndStrictSchema(t *testing.T) {
	s, _ := repairTestServer(t)
	rt, token := repairRegister(t, s)
	for _, credential := range []string{firstToken, adminToken, token} {
		diagnosticCall(t, s, "POST", "/repairs", credential, repairStartBody(s, rt, "r", "restart_stream"), 403)
	}
	for _, credential := range []string{firstToken, adminToken, diagnosticOperatorToken} {
		repairPoll(t, s, rt, credential, 403)
	}
	noCap, noCapToken := diagnosticRegister(t, s)
	diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, repairStartBody(s, noCap, "no-cap", "restart_stream"), 403)
	repairPoll(t, s, noCap, noCapToken, 403)
	for name, change := range map[string]func(map[string]any){"unknown": func(v map[string]any) { v["params"] = map[string]any{} }, "wrong case": func(v map[string]any) { v["Action"] = v["action"]; delete(v, "action") }, "null": func(v map[string]any) { v["action"] = nil }, "unsupported action": func(v map[string]any) { v["action"] = "eval" }, "short deadline": func(v map[string]any) { v["deadline_ms"] = 999 }, "long deadline": func(v map[string]any) { v["deadline_ms"] = 30001 }, "fractional": func(v map[string]any) { v["deadline_ms"] = 1000.5 }, "string deadline": func(v map[string]any) { v["deadline_ms"] = "1000" }} {
		t.Run(name, func(t *testing.T) {
			v := repairStartBody(s, rt, "r", "restart_stream")
			change(v)
			diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, v, 400)
		})
	}
	b, _ := json.Marshal(repairStartBody(s, rt, "r", "restart_stream"))
	duplicate := strings.TrimSuffix(string(b), "}") + `,"action":"reload_player"}`
	expect(t, request(s, "POST", diagnosticsPrefix+"/repairs", diagnosticOperatorToken, duplicate, nil), 400)
	expect(t, request(s, "POST", diagnosticsPrefix+"/repairs", diagnosticOperatorToken, strings.Repeat("x", 4097), nil), 413)
	diagnosticCall(t, s, "POST", "/repairs/poll", token, map[string]any{"runtime_id": rt, "consent": true}, 400)
	body := repairStartBody(s, rt, "r", "restart_stream")
	body["server_epoch"] = "old"
	diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, body, 409)
	delete(s.diagnostics.operators[0].actions, "repairs.start")
	diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, repairStartBody(s, rt, "r", "restart_stream"), 404)
	s.diagnostics.operators[0].actions["repairs.start"] = true
	id := repairStart(t, s, rt, "r", "restart_stream")
	delete(s.diagnostics.operators[0].actions, "repairs.read")
	diagnosticCall(t, s, "GET", "/repairs/"+id, diagnosticOperatorToken, nil, 404)
	repairAck(t, s, rt, token, strings.Repeat("f", 32), "applied", 404)
	diagnosticCall(t, s, "POST", "/repairs/results", token, map[string]any{"runtime_id": rt, "repair_id": id, "status": "applied", "error": "secret"}, 400)
	if len(s.diagnostics.repairs) != 1 {
		t.Fatal("invalid requests allocated repairs")
	}
}

func TestDiagnosticsRepairsReservedDiagnosticStopUnaffected(t *testing.T) {
	s, _ := repairTestServer(t)
	s.diagnostics.cfg.MaxIdempotencyRecords = 2
	rt, token := repairRegister(t, s)
	start := diagnosticStart(t, s, rt, "start")
	diagnosticAck(t, s, rt, token, start, 200)
	diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, repairStartBody(s, rt, "repair", "restart_stream"), 429)
	sid := start["session_id"].(string)
	stop := diagnosticCall(t, s, "POST", "/sessions/"+sid+"/stop", diagnosticOperatorToken, map[string]any{"server_epoch": s.diagnostics.epoch, "idempotency_key": "stop", "reason": "operator"}, 202)
	poll := diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
	if poll["control"].(map[string]any)["action"] != "stop" {
		t.Fatal("repair exhaustion obstructed diagnostic stop")
	}
	diagnosticAck(t, s, rt, token, stop, 200)
}

func TestDiagnosticsRepairsRestartFencesCredentialsAndEpoch(t *testing.T) {
	s, _ := repairTestServer(t)
	rt, token := repairRegister(t, s)
	body := repairStartBody(s, rt, "repair", "reload_player")
	id := repairStart(t, s, rt, "repair", "reload_player")
	replacement, _ := repairTestServer(t)
	if replacement.diagnostics.epoch == s.diagnostics.epoch {
		t.Fatal("restart reused process epoch")
	}
	repairPoll(t, replacement, rt, token, 401)
	repairAck(t, replacement, rt, token, id, "accepted", 401)
	diagnosticCall(t, replacement, "POST", "/repairs", diagnosticOperatorToken, body, 409)
	diagnosticCall(t, replacement, "GET", "/repairs/"+id, diagnosticOperatorToken, nil, 404)
	if len(replacement.diagnostics.repairs) != 0 {
		t.Fatal("restart restored an old repair")
	}
}

func TestDiagnosticsRepairsRetentionCapAndConcurrentReplay(t *testing.T) {
	s, now := repairTestServer(t)
	rt, token := repairRegister(t, s)
	other, _ := repairRegister(t, s)
	for i := 0; i < 255; i++ {
		if i%60 == 0 {
			*now = now.Add(time.Minute)
			if i == 0 {
				*now = now.Add(-time.Minute)
			}
			diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, i+1, true), 200)
		}
		id := repairStart(t, s, rt, fmt.Sprintf("repair-%d", i), "restart_stream")
		repairAck(t, s, rt, token, id, "applied", 200)
	}
	pending := repairStart(t, s, rt, "last", "restart_stream")
	// The second target is freshly registered because unconfirmed registrations expire.
	other, _ = repairRegister(t, s)
	diagnosticCall(t, s, "POST", "/repairs", diagnosticOperatorToken, repairStartBody(s, other, "over-cap", "restart_stream"), 429)
	if len(s.diagnostics.repairs) != 256 || s.diagnostics.repairs[pending].state != "pending" {
		t.Fatal("cap evicted pending repair")
	}
	data, _ := json.Marshal(repairStartBody(s, rt, "last", "restart_stream"))
	var wg sync.WaitGroup
	results := make(chan *httptest.ResponseRecorder, 8)
	for i := 0; i < 8; i++ {
		wg.Go(func() {
			results <- request(s, "POST", diagnosticsPrefix+"/repairs", diagnosticOperatorToken, string(data), nil)
		})
	}
	wg.Wait()
	close(results)
	for response := range results {
		expect(t, response, 202)
		var v map[string]any
		json.Unmarshal(response.Body.Bytes(), &v)
		if v["repair_id"] != pending {
			t.Fatal("concurrent retry created another operation")
		}
	}
	*now = now.Add(16 * time.Minute)
	diagnosticCall(t, s, "GET", "/repairs/"+pending, diagnosticOperatorToken, nil, 200)
	if len(s.diagnostics.repairs) != 1 || s.diagnostics.repairs[pending].state != "revoked" {
		t.Fatal("terminal history did not expire independently of pending retirement")
	}
	*now = now.Add(15 * time.Minute)
	diagnosticCall(t, s, "GET", "/repairs/"+pending, diagnosticOperatorToken, nil, 404)
	if len(s.diagnostics.repairs) != 0 {
		t.Fatal("repair records unbounded")
	}
}
