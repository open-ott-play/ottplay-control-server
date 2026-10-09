package control

import (
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestServerDebugUsesExistingAdminPolicy(t *testing.T) {
	s := newTestServer(t)
	for _, tc := range []struct {
		method, path, token, origin string
		status                      int
	}{
		{"GET", "/api/debug", "", "", 401},
		{"GET", "/api/debug", firstToken, "", 403},
		{"GET", "/api/debug", adminToken, "https://evil.example", 403},
		{"GET", "/api/debug", adminToken, "null", 403},
		{"POST", "/api/debug", adminToken, "", 405},
		{"GET", "/api/debug?device_id=first", adminToken, "", 400},
		{"GET", "/api/debug?token=private", adminToken, "", 400},
		{"GET", "/api/debug", adminToken, "https://player.example", 200},
	} {
		headers := map[string]string{}
		if tc.origin != "" {
			headers["Origin"] = tc.origin
		}
		w := request(s, tc.method, tc.path, tc.token, "", headers)
		expect(t, w, tc.status)
		if w.Header().Get("Cache-Control") != "no-store" {
			t.Fatal("debug response could be cached")
		}
	}
	w := request(s, "OPTIONS", "/api/debug", "", "", map[string]string{"Origin": "https://player.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
	expect(t, w, 204)
}

func TestServerDebugCountsStoredEntriesWithoutCleanupOrPrivateData(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"status","params":{}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", map[string]any{"private": "provider-secret"}), nil), 200)
	enqueue(t, s, "first", `{"command":"popup_message","message":"never-export-message"}`)
	bytes, resultBytes := s.bytes, s.resultBytes
	s.diagnostics.runtimes["private-runtime"] = &diagRuntime{device: "first"}
	s.diagnostics.sessions["private-session"] = &diagSession{}
	s.diagnostics.repairs["private-repair"] = &diagRepair{}
	s.diagnostics.controlSlots <- struct{}{}
	s.now = func() time.Time { return time.Now().Add(2 * time.Hour) }
	w := request(s, "GET", "/api/debug", adminToken, "", nil)
	expect(t, w, 200)
	var data map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &data); err != nil {
		t.Fatal(err)
	}
	if len(data) != 6 || data["version"] != float64(1) || data["consistent"] != false {
		t.Fatal("unexpected debug envelope")
	}
	c := data["control"].(map[string]any)
	if len(c) != 8 || c["devices"] != float64(2) || c["queues"] != float64(1) || c["pending"] != float64(1) || c["resultEntries"] != float64(1) || c["queueBytes"] != float64(bytes) || c["resultBytes"] != float64(resultBytes) {
		t.Fatalf("incorrect stored counts: %v", c)
	}
	d := data["diagnostics"].(map[string]any)
	if len(d) != 10 || d["runtimes"] != float64(1) || d["sessions"] != float64(1) || d["repairs"] != float64(1) || d["controlSlotsUsed"] != float64(1) {
		t.Fatalf("incorrect diagnostics counts: %v", d)
	}
	if s.bytes != bytes || s.resultBytes != resultBytes || len(s.devices[0].queue) != 1 || len(s.diagnostics.runtimes) != 1 {
		t.Fatal("debug read expired stored state")
	}
	for _, secret := range []string{"first", "second", "private-", "provider-secret", "never-export-message", adminToken, firstToken} {
		if strings.Contains(w.Body.String(), secret) {
			t.Fatalf("debug leaked %q", secret)
		}
	}
	if len(w.Body.Bytes()) > 4096 {
		t.Fatal("aggregate response unexpectedly large")
	}
}

func TestServerDebugConcurrentSnapshots(t *testing.T) {
	s := newTestServer(t)
	var wait sync.WaitGroup
	for range 4 {
		wait.Add(1)
		go func() {
			defer wait.Done()
			for range 10 {
				w := request(s, "GET", "/api/debug", adminToken, "", nil)
				if w.Code != 200 {
					t.Errorf("debug status: %d", w.Code)
				}
			}
		}()
	}
	wait.Wait()
}
