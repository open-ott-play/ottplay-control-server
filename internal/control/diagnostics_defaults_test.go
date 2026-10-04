package control

import (
	"encoding/json"
	"testing"
	"time"
)

func TestDiagnosticsDefaultRuntimeTTLReclaimsLostRevocations(t *testing.T) {
	newDefault := func() (*Server, *time.Time) {
		c := diagnosticConfig()
		c.Diagnostics.RuntimeTTLMS = 0
		s, err := New(c)
		if err != nil {
			t.Fatal(err)
		}
		now := time.Date(2026, 10, 4, 21, 0, 0, 0, time.UTC)
		s.now = func() time.Time { return now }
		if s.diagnostics.cfg.RuntimeTTLMS != 30000 || s.diagnostics.cfg.SessionLeaseMSMax != 600000 {
			t.Fatal("default runtime/session limits changed unexpectedly")
		}
		return s, &now
	}
	registerBody := map[string]any{"instance_id": "same-tab", "boot_id": "same-boot", "capabilities": []string{"playback"}, "consent": map[string]any{"granted": true, "epoch": "grant"}}
	t.Run("four lost final revocations", func(t *testing.T) {
		s, now := newDefault()
		for i := 0; i < 4; i++ {
			rt, token := diagnosticRegister(t, s)
			diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
		}
		*now = now.Add(29999 * time.Millisecond)
		diagnosticCall(t, s, "POST", "/runtimes", firstToken, registerBody, 429)
		if len(s.diagnostics.runtimes) != 4 {
			t.Fatal("live confirmed slots were evicted")
		}
		*now = now.Add(time.Millisecond)
		diagnosticCall(t, s, "POST", "/runtimes", firstToken, registerBody, 201)
		if len(s.diagnostics.runtimes) != 1 {
			t.Fatal("default30s did not reclaim all lost-revocation slots")
		}
	})
	t.Run("actively polled runtime preserved", func(t *testing.T) {
		s, now := newDefault()
		keep, keepToken := diagnosticRegister(t, s)
		diagnosticCall(t, s, "POST", "/poll", keepToken, diagnosticPollBody(keep, 1, true), 200)
		for i := 0; i < 3; i++ {
			rt, token := diagnosticRegister(t, s)
			diagnosticCall(t, s, "POST", "/poll", token, diagnosticPollBody(rt, 1, true), 200)
		}
		*now = now.Add(20 * time.Second)
		diagnosticCall(t, s, "POST", "/poll", keepToken, diagnosticPollBody(keep, 2, true), 200)
		diagnosticCall(t, s, "POST", "/runtimes", firstToken, registerBody, 429)
		*now = now.Add(10 * time.Second)
		diagnosticCall(t, s, "POST", "/runtimes", firstToken, registerBody, 201)
		if len(s.diagnostics.runtimes) != 2 || s.diagnostics.runtimes[keep] == nil {
			t.Fatal("reclamation evicted actively polled runtime")
		}
		if !s.diagnostics.runtimes[keep].expires.Equal(now.Add(20 * time.Second)) {
			t.Fatal("active poll did not extend its own TTL")
		}
	})
}

func TestDiagnosticsRepairsNullOriginRequiresRuntimeRouteAndOptIn(t *testing.T) {
	c := diagnosticConfig()
	c.Diagnostics.Operators[0].Actions = append(c.Diagnostics.Operators[0].Actions, "repairs.start", "repairs.read")
	c.AllowNullOrigin = true
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	rt, token := repairRegister(t, s)
	id := repairStart(t, s, rt, "repair", "restart_stream")
	poll, _ := json.Marshal(map[string]any{"runtime_id": rt})
	result, _ := json.Marshal(repairResultBody(rt, id, "applied"))
	create, _ := json.Marshal(repairStartBody(s, rt, "other", "restart_stream"))
	headers := map[string]string{"Origin": "null"}
	expect(t, request(s, "POST", diagnosticsPrefix+"/repairs/poll", token, string(poll), headers), 200)
	expect(t, request(s, "POST", diagnosticsPrefix+"/repairs/results", token, string(result), headers), 200)
	expect(t, request(s, "POST", diagnosticsPrefix+"/repairs", diagnosticOperatorToken, string(create), headers), 403)
	expect(t, request(s, "GET", diagnosticsPrefix+"/repairs/"+id, diagnosticOperatorToken, "", headers), 403)
	expect(t, request(s, "OPTIONS", diagnosticsPrefix+"/repairs/poll", "", "", map[string]string{"Origin": "null", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}), 200)
	s.allowNull = false
	expect(t, request(s, "POST", diagnosticsPrefix+"/repairs/poll", token, string(poll), headers), 403)
}
