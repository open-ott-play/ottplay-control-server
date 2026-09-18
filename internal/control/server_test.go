package control

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

var adminToken = strings.Repeat("a", 32)
var firstToken = strings.Repeat("b", 32)
var secondToken = strings.Repeat("c", 32)

func testConfig() config.Config {
	c := config.Config{AdminToken: adminToken, Devices: []config.Device{{ID: "first", Token: firstToken}, {ID: "second", Token: secondToken}}, AllowedOrigins: []string{"https://player.example"}}
	c.Defaults()
	return c
}
func newTestServer(t *testing.T) *Server {
	t.Helper()
	s, err := New(testConfig())
	if err != nil {
		t.Fatal(err)
	}
	return s
}
func request(s *Server, method, path, token, body string, headers map[string]string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	if body != "" {
		r.Header.Set("Content-Type", "application/json")
	}
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	return w
}
func expect(t *testing.T, w *httptest.ResponseRecorder, status int) {
	t.Helper()
	if w.Code != status {
		t.Fatalf("status %d, want %d; body %s", w.Code, status, w.Body.String())
	}
}
func enqueue(t *testing.T, s *Server, id, body string) string {
	t.Helper()
	w := request(s, "POST", "/api/webhook/commands?device_id="+id, adminToken, body, nil)
	expect(t, w, 202)
	var result struct{ ID string }
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	return result.ID
}
func commands(t *testing.T, w *httptest.ResponseRecorder, ack bool) []map[string]any {
	t.Helper()
	expect(t, w, 200)
	var result []map[string]any
	if ack {
		var wrapper struct{ Commands []map[string]any }
		if err := json.Unmarshal(w.Body.Bytes(), &wrapper); err != nil {
			t.Fatal(err)
		}
		result = wrapper.Commands
	} else if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result == nil {
		t.Fatal("commands must be an array, not null")
	}
	return result
}

func TestAuthenticationAndQueueIsolation(t *testing.T) {
	s := newTestServer(t)
	for _, path := range []string{"/healthz", "/readyz"} {
		expect(t, request(s, "GET", path, "", "", nil), 200)
	}
	for _, token := range []string{"", "invalid", strings.Repeat("x", 32)} {
		expect(t, request(s, "GET", "/api/webhook/commands", token, "", nil), 401)
	}
	expect(t, request(s, "GET", "/api/webhook/commands", adminToken, "", nil), 403)
	expect(t, request(s, "POST", "/api/webhook/commands?device_id=first", firstToken, `{"command":"exit_player"}`, nil), 403)
	expect(t, request(s, "GET", "/api/devices", firstToken, "", nil), 403)
	expect(t, request(s, "POST", "/api/webhook/commands", adminToken, `{"command":"exit_player"}`, nil), 400)
	expect(t, request(s, "GET", "/api/webhook/commands?device_id=second", firstToken, "", nil), 403)
	expect(t, request(s, "GET", "/api/webhook/commands?token="+firstToken, "", "", nil), 401)
	expect(t, request(s, "GET", "/api/webhook/commands?token=secret", firstToken, "", nil), 400)
	r := httptest.NewRequest("GET", "/api/webhook/commands", nil)
	r.Header.Add("Authorization", "Bearer "+firstToken)
	r.Header.Add("Authorization", "Bearer "+secondToken)
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	expect(t, w, 401)
	enqueue(t, s, "first", `{"command":"exit_player"}`)
	if len(commands(t, request(s, "GET", "/api/webhook/commands", secondToken, "", nil), false)) != 0 {
		t.Fatal("leaked another device queue")
	}
	if len(commands(t, request(s, "GET", "/api/webhook/commands?device_id=first", firstToken, "", nil), false)) != 1 {
		t.Fatal("missing own command")
	}
}

func TestAcknowledgementsRetryExpiryAndLegacyDrain(t *testing.T) {
	s := newTestServer(t)
	now := time.Unix(100, 0)
	s.now = func() time.Time { return now }
	id := enqueue(t, s, "first", `{"command":"popup_message","message":"hello"}`)
	secondID := enqueue(t, s, "second", `{"command":"exit_player"}`)
	for i := 0; i < 2; i++ {
		got := commands(t, request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil), true)
		if len(got) != 1 || got[0]["id"] != id {
			t.Fatal("retry changed or drained command")
		}
		if got[0]["expires_at"] != float64(160) {
			t.Fatal("missing authoritative command expiry")
		}
		var envelope struct {
			ServerTime float64 `json:"server_time"`
		}
		_ = json.Unmarshal(request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil).Body.Bytes(), &envelope)
		if envelope.ServerTime != 100 {
			t.Fatal("missing server clock for devices with incorrect local time")
		}
	}
	w := request(s, "POST", "/api/webhook/commands/ack", firstToken, fmt.Sprintf(`{"ids":["%s","%s","%s"]}`, id, id, secondID), nil)
	expect(t, w, 200)
	if !strings.Contains(w.Body.String(), `"acknowledged":1`) {
		t.Fatal("incorrect ACK count")
	}
	w = request(s, "POST", "/api/webhook/commands/ack", firstToken, fmt.Sprintf(`{"ids":["%s"]}`, id), nil)
	expect(t, w, 200)
	if !strings.Contains(w.Body.String(), `"acknowledged":0`) {
		t.Fatal("ACK is not idempotent")
	}
	if len(commands(t, request(s, "GET", "/webhook/poll", secondToken, "", nil), false)) != 1 {
		t.Fatal("cross-device ACK removed command")
	}
	if len(commands(t, request(s, "GET", "/webhook/poll", secondToken, "", nil), false)) != 0 {
		t.Fatal("legacy poll did not drain")
	}
	enqueue(t, s, "first", `{"command":"exit_player"}`)
	enqueue(t, s, "first", `{"command":"exit_player"}`)
	got := commands(t, request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil), true)
	if got[0]["ts"].(float64) >= got[1]["ts"].(float64) || got[0]["id"] == got[1]["id"] {
		t.Fatal("identifiers/timestamps must be unique")
	}
	now = now.Add(60 * time.Second)
	if len(commands(t, request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil), true)) != 0 || s.bytes != 0 {
		t.Fatal("expired entries not reclaimed")
	}
	for _, body := range []string{`{"ids":null}`, `{"ids":["bad"]}`, `{"ids":[],"extra":1}`, `{"ids":[],"ids":[]}`} {
		expect(t, request(s, "POST", "/api/webhook/commands/ack", firstToken, body, nil), 400)
	}
}

func TestCommandValidation(t *testing.T) {
	valid := []string{
		`{"command":"popup_message","message":"hello","popup_duration":5}`,
		`{"command":"channel_by_number","channel_number":1}`,
		`{"command":"channel_by_name","channel_name":"News"}`,
		`{"command":"random_channel"}`, `{"command":"random_channel","random_range":[1,9]}`,
		`{"command":"change_provider","provider":0}`,
		`{"command":"change_provider_settings","provider_settings":"{\"host\":\"example\"}"}`,
		`{"command":"change_playlist","playlist":"https://example.com/list.m3u?key=private"}`,
		`{"command":"set_volume","volume":0}`, `{"command":"set_volume","volume_step":-2.5}`,
		`{"command":"exit_player"}`,
	}
	for _, body := range valid {
		if _, err := validateCommand([]byte(body)); err != nil {
			t.Errorf("rejected valid command %s: %v", body, err)
		}
	}
	invalid := []string{
		`null`, `[]`, `{}`, `{"command":"unknown"}`, `{"command":"exit_player","id":"injected"}`, `{"command":"exit_player","ts":1}`,
		`{"command":"exit_player","command":"random_channel"}`, `{"command":"exit_player"} {}`,
		`{"command":"popup_message","message":""}`, `{"command":"popup_message","message":"x","popup_duration":0}`,
		`{"command":"channel_by_number","channel_number":1.1}`, `{"command":"channel_by_number","channel_number":"1"}`, `{"command":"channel_by_number","channel_number":null}`,
		`{"command":"channel_by_name","channel_name":" "}`, `{"command":"random_channel","random_range":[9,1]}`, `{"command":"random_channel","random_range":[1]}`,
		`{"command":"change_provider","provider":-1}`, `{"command":"change_provider_settings","provider_settings":"broken"}`,
		`{"command":"change_playlist","playlist":"javascript:alert(1)"}`,
		`{"command":"set_volume","volume":101}`, `{"command":"set_volume","volume":1,"volume_step":1}`, `{"command":"set_volume","volume_step":1e999}`, `{"command":"set_volume","volume":true}`,
	}
	for _, body := range invalid {
		if _, err := validateCommand([]byte(body)); err == nil {
			t.Errorf("accepted invalid command %s", body)
		}
	}
}

func TestCORSExactOriginsAndNullOptIn(t *testing.T) {
	for _, allowNull := range []bool{false, true} {
		c := testConfig()
		c.AllowNullOrigin = allowNull
		s, _ := New(c)
		for _, origin := range []string{"https://player.example", "https://player.example.evil", "https://player.example/", "null"} {
			want := 403
			if origin == "https://player.example" || origin == "null" && allowNull {
				want = 200
			}
			w := request(s, "GET", "/api/webhook/commands", firstToken, "", map[string]string{"Origin": origin})
			expect(t, w, want)
			if want == 200 && w.Header().Get("Access-Control-Allow-Origin") != origin {
				t.Fatal("missing exact CORS response")
			}
			wantPreflight := 403
			if want == 200 {
				wantPreflight = 204
			}
			expect(t, request(s, "OPTIONS", "/api/webhook/commands", "", "", map[string]string{"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization, content-type"}), wantPreflight)
			if origin == "null" {
				wantPreflight = 403
			}
			expect(t, request(s, "OPTIONS", "/api/webhook/commands", "", "", map[string]string{"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization, content-type"}), wantPreflight)
		}
		expect(t, request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{"command":"exit_player"}`, map[string]string{"Origin": "null"}), 403)
		expect(t, request(s, "GET", "/api/devices", adminToken, "", map[string]string{"Origin": "null"}), 403)
		ackStatus := 403
		if allowNull {
			ackStatus = 200
		}
		expect(t, request(s, "POST", "/api/webhook/commands/ack", firstToken, `{"ids":[]}`, map[string]string{"Origin": "null"}), ackStatus)
		expect(t, request(s, "OPTIONS", "/api/webhook/commands", "", "", map[string]string{"Origin": "https://player.example", "Access-Control-Request-Method": "DELETE"}), 403)
		expect(t, request(s, "OPTIONS", "/api/webhook/commands", "", "", map[string]string{"Origin": "https://player.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-unapproved"}), 403)
	}
}

func TestBoundsRateLimitsAndPrivateStatus(t *testing.T) {
	c := testConfig()
	c.MaxPendingPerDevice = 1
	s, _ := New(c)
	enqueue(t, s, "first", `{"command":"exit_player"}`)
	expect(t, request(s, "POST", "/webhook/notify?device_id=first", adminToken, `{"command":"exit_player"}`, nil), 429)
	expect(t, request(s, "POST", "/webhook/notify?device_id=second", adminToken, strings.Repeat("x", MaxBodyBytes+1), nil), 413)
	expect(t, request(s, "POST", "/webhook/notify?device_id=second", adminToken, `{"command":"exit_player"}`, map[string]string{"Content-Type": "text/plain"}), 415)
	w := request(s, "GET", "/api/devices", adminToken, "", nil)
	expect(t, w, 200)
	for _, secret := range []string{adminToken, firstToken, secondToken, "exit_player"} {
		if strings.Contains(w.Body.String(), secret) {
			t.Fatal("status disclosed credentials or payload")
		}
	}
	if !strings.Contains(w.Body.String(), `"pending":1`) || !strings.Contains(w.Body.String(), `"last_seen":null`) {
		t.Fatal("missing private status")
	}
	for i := 0; i < 120; i++ {
		expect(t, request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil), 200)
	}
	expect(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), 429)
	expect(t, request(s, "GET", "/api/webhook/commands", secondToken, "", nil), 200)
}

func TestAggregateQueueMemoryBoundAndReclamation(t *testing.T) {
	c := testConfig()
	c.CommandTTLSeconds = 3600
	c.Devices = nil
	for i := 0; i < 8; i++ {
		c.Devices = append(c.Devices, config.Device{ID: fmt.Sprintf("tv%d", i), Token: fmt.Sprintf("%032d", i)})
	}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(100, 0)
	s.now = func() time.Time { return now }
	body := `{"command":"popup_message","message":"` + strings.Repeat("x", 8192) + `"}`
	accepted, full := 0, false
	for i := 0; i < 400; i++ {
		if i == 200 {
			now = now.Add(time.Minute)
		}
		w := request(s, "POST", fmt.Sprintf("/api/webhook/commands?device_id=tv%d", i/50), adminToken, body, nil)
		if w.Code == 429 {
			full = true
			break
		}
		expect(t, w, 202)
		accepted++
	}
	if !full || accepted < 200 || s.bytes > MaxQueueBytes {
		t.Fatalf("aggregate limit not exercised: accepted=%d bytes=%d", accepted, s.bytes)
	}
	commands(t, request(s, "GET", "/api/webhook/commands", c.Devices[0].Token, "", nil), false)
	expect(t, request(s, "POST", "/api/webhook/commands?device_id=tv0", adminToken, body, nil), 202)
	now = now.Add(time.Hour)
	commands(t, request(s, "GET", "/api/webhook/commands?delivery=ack", c.Devices[1].Token, "", nil), true)
	if s.bytes != 0 {
		t.Fatal("expiry did not reclaim all aggregate queue bytes")
	}
}

func TestAllConfiguredDevicesCanPollAtOneHertz(t *testing.T) {
	c := testConfig()
	c.Devices = nil
	for i := 0; i < 64; i++ {
		c.Devices = append(c.Devices, config.Device{ID: fmt.Sprintf("tv%d", i), Token: fmt.Sprintf("%032d", i)})
	}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	s.now = func() time.Time { return time.Unix(100, 0) }
	for i := 0; i < 60; i++ {
		for _, d := range c.Devices {
			expect(t, request(s, "GET", "/api/webhook/commands?delivery=ack", d.Token, "", nil), 200)
		}
	}
}

func TestConcurrentQueuesAndCredentialRotation(t *testing.T) {
	s := newTestServer(t)
	var wg sync.WaitGroup
	for i := 0; i < 40; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			w := request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{"command":"exit_player"}`, nil)
			if w.Code != 202 {
				t.Errorf("enqueue status %d", w.Code)
			}
		}()
	}
	wg.Wait()
	got := commands(t, request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil), true)
	if len(got) != 40 {
		t.Fatalf("lost commands: %d", len(got))
	}
	seen := map[any]bool{}
	for _, c := range got {
		if seen[c["id"]] {
			t.Fatal("duplicate ID")
		}
		seen[c["id"]] = true
	}
	c := testConfig()
	c.Devices[0].Token = strings.Repeat("d", 32)
	restarted, _ := New(c)
	expect(t, request(restarted, "GET", "/api/webhook/commands", firstToken, "", nil), 401)
	if len(commands(t, request(restarted, "GET", "/api/webhook/commands", c.Devices[0].Token, "", nil), false)) != 0 {
		t.Fatal("restart unexpectedly retained commands")
	}
}

func TestFramingAndMethods(t *testing.T) {
	s := newTestServer(t)
	expect(t, request(s, "PUT", "/api/webhook/commands", adminToken, "", nil), 405)
	expect(t, request(s, "GET", "/unknown", firstToken, "", nil), 404)
	for _, header := range []string{"Expect", "Transfer-Encoding"} {
		expect(t, request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{"command":"exit_player"}`, map[string]string{header: "chunked"}), 400)
	}
	r := httptest.NewRequest(http.MethodPost, "/api/webhook/commands?device_id=first", strings.NewReader(`{"command":"exit_player"}`))
	r.TransferEncoding = []string{"chunked"}
	r.Header.Set("Authorization", "Bearer "+adminToken)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	expect(t, w, 400)
}
