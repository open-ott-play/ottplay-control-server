package control

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func transportBody(method, path, token, body string) string {
	v := map[string]any{"version": 1, "method": method, "path": path}
	if token != "" {
		v["token"] = token
	}
	b, _ := json.Marshal(v)
	if body != "" {
		return string(b[:len(b)-1]) + `,"body":` + body + `}`
	}
	return string(b)
}

func transportCall(s *Server, method, path, token, body string) *httptest.ResponseRecorder {
	return request(s, "POST", deviceTransportPath, "", transportBody(method, path, token, body), nil)
}

func TestDeviceTransportRejectsNonDeviceRoutesAndEnvelopeAmbiguity(t *testing.T) {
	for _, tc := range []struct{ method, path string }{
		{"POST", "/api/webhook/commands"}, {"POST", "/webhook/notify"},
		{"GET", "/api/requests"}, {"POST", "/api/requests"}, {"GET", "/api/devices"},
		{"GET", "/api/debug"}, {"GET", "/api/operations"}, {"GET", "/api/home-assistant/status"},
		{"GET", "/api/pairings"}, {"GET", "/api/pairings?id="},
		{"GET", "/api/pairings?id=" + strings.Repeat("a", 32) + "&id=" + strings.Repeat("b", 32)},
		{"GET", "/api/pairings?id=" + strings.Repeat("a", 32) + "&other=1"},
		{"POST", "/api/pairings/approve"}, {"GET", diagnosticsPrefix + "/runtimes"},
		{"POST", diagnosticsPrefix + "/sessions"}, {"POST", diagnosticsPrefix + "/repairs"},
		{"DELETE", diagnosticsPrefix + "/runtimes/" + strings.Repeat("a", 32)},
		{"POST", deviceTransportPath}, {"GET", "/healthz"},
		{"get", "/api/webhook/commands"}, {"HEAD", "/api/webhook/commands"},
		{"GET", "https://other.example/api/webhook/commands"}, {"GET", "//other.example/api/webhook/commands"},
		{"GET", "/api/%77ebhook/commands"}, {"GET", "/api/webhook/commands/"},
		{"GET", "/api/webhook/../webhook/commands"}, {"GET", "/api/webhook/commands#x"},
		{"GET", "/api/webhook/commands?"}, {"GET", "/api/webhook/commands\\x"},
	} {
		t.Run(tc.method+tc.path, func(t *testing.T) {
			body := ""
			if tc.method == "POST" {
				body = "{}"
			}
			expect(t, transportCall(newTestServer(t), tc.method, tc.path, adminToken, body), 400)
		})
	}
	valid := transportBody("GET", "/api/webhook/commands", firstToken, "")
	invalid := []string{
		`null`, `[]`, `{}`, valid + `{}`, strings.Replace(valid, `"version":1`, `"version":2`, 1),
		strings.Replace(valid, `"version":1`, `"version":1,"version":1`, 1),
		strings.Replace(valid, `"version":1`, `"version":1.0`, 1),
		strings.Replace(valid, `"version":1`, `"version":true`, 1),
		strings.Replace(valid, `"version":1`, `"Version":1`, 1),
		strings.Replace(valid, firstToken, "short", 1), strings.Replace(valid, firstToken, "Bearer "+firstToken, 1),
		strings.Replace(valid, `"token":"`+firstToken+`"`, `"token":null`, 1),
		strings.Replace(valid, `"token":"`+firstToken+`"`, `"token":""`, 1),
		strings.Replace(valid, `"version":1`, `"version":1,"Origin":"https://player.example"`, 1),
		strings.Replace(valid, `"version":1`, `"version":1,"headers":{"Authorization":"Bearer x"}`, 1),
		strings.Replace(valid, `"version":1`, `"version":1,"body":{}`, 1),
		strings.Replace(valid, `"version":1`, `"version":1,"body":null`, 1),
		transportBody("POST", "/api/responses", firstToken, ""),
		transportBody("POST", "/api/responses", firstToken, `"{}"`),
		transportBody("POST", "/api/responses", firstToken, `[]`),
		string(append([]byte(valid[:len(valid)-1]), 0xff, '}')),
	}
	for i, body := range invalid {
		t.Run(fmt.Sprint("body", i), func(t *testing.T) {
			expect(t, request(newTestServer(t), "POST", deviceTransportPath, "", body, nil), 400)
		})
	}
}

func TestDeviceTransportKeepsOriginalAuthenticationCORSAndResponse(t *testing.T) {
	s, _ := diagnosticServer(t)
	for _, route := range []struct{ method, path, body string }{
		{"GET", "/api/webhook/commands", ""}, {"POST", "/api/webhook/commands/ack", `{"ids":[]}`},
		{"POST", "/api/responses", `{}`}, {"GET", "/api/notifications?runtime=r1&active=false", ""},
		{"GET", "/api/notifications/media?id=" + strings.Repeat("a", 32) + "&runtime=r1", ""},
		{"POST", diagnosticsPrefix + "/runtimes", `{}`},
		{"POST", diagnosticsPrefix + "/poll", `{}`}, {"POST", diagnosticsPrefix + "/results", `{}`},
		{"POST", diagnosticsPrefix + "/events", `{}`}, {"POST", diagnosticsPrefix + "/repairs/poll", `{}`},
		{"POST", diagnosticsPrefix + "/repairs/results", `{}`},
	} {
		for _, token := range []string{"", adminToken, firstToken, diagnosticOperatorToken} {
			t.Run(route.path+token[:min(1, len(token))], func(t *testing.T) {
				h := map[string]string{"Origin": "https://player.example"}
				direct := request(s, route.method, route.path, token, route.body, h)
				wrapped := request(s, "POST", deviceTransportPath, "", transportBody(route.method, route.path, token, route.body), h)
				if direct.Code != wrapped.Code || direct.Body.String() != wrapped.Body.String() || !reflect.DeepEqual(direct.Header(), wrapped.Header()) {
					t.Fatalf("response changed: direct %d %v %s; wrapped %d %v %s", direct.Code, direct.Header(), direct.Body.String(), wrapped.Code, wrapped.Header(), wrapped.Body.String())
				}
			})
		}
	}
	for _, origin := range []string{"https://other.example", "null"} {
		expect(t, request(s, "POST", deviceTransportPath, "", transportBody("GET", "/api/webhook/commands", firstToken, ""), map[string]string{"Origin": origin}), 403)
	}
	s.allowNull = true
	expect(t, request(s, "POST", deviceTransportPath, "", transportBody("GET", "/api/webhook/commands", firstToken, ""), map[string]string{"Origin": "null"}), 200)
	r := httptest.NewRequest("POST", deviceTransportPath, strings.NewReader(transportBody("GET", "/api/webhook/commands", firstToken, "")))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Add("Origin", "https://player.example")
	r.Header.Add("Origin", "https://other.example")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	expect(t, w, 403)
	preflight := request(s, "OPTIONS", deviceTransportPath, "", "", map[string]string{"Origin": "https://player.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"})
	expect(t, preflight, 204)
	if preflight.Header().Get("Access-Control-Allow-Methods") != "POST" {
		t.Fatal("unexpected preflight methods")
	}
}

func TestDeviceTransportSingleDispatchAndLogicalBudgets(t *testing.T) {
	s := newTestServer(t)
	id := enqueue(t, s, "first", `{"command":"exit_player"}`)
	before := s.globalRate.count
	w := transportCall(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "")
	if len(commands(t, w, true)) != 1 || s.globalRate.count != before+1 || s.devices[0].rate.count != 1 {
		t.Fatal("poll was lost or a logical allowance was charged twice")
	}
	expect(t, transportCall(s, "POST", "/api/webhook/commands/ack", firstToken, fmt.Sprintf(`{"ids":[%q]}`, id)), 200)
	if len(s.devices[0].queue) != 0 || s.devices[0].rate.count != 2 {
		t.Fatal("ack did not consume exactly one command and logical request")
	}
	enqueue(t, s, "first", `{"command":"exit_player"}`)
	if len(commands(t, transportCall(s, "GET", "/webhook/poll", firstToken, ""), false)) != 1 || len(s.devices[0].queue) != 0 {
		t.Fatal("legacy drain changed")
	}
	for s.devices[0].rate.count < 120 {
		expect(t, transportCall(s, "GET", "/api/webhook/commands", firstToken, ""), 200)
	}
	w = transportCall(s, "GET", "/api/webhook/commands", firstToken, "")
	expect(t, w, 429)
	if w.Header().Get("Retry-After") != "60" {
		t.Fatal("lost logical retry header")
	}
}

func TestDeviceTransportPreservesNestedBytesAndInnerLimits(t *testing.T) {
	for _, route := range []struct {
		path, token string
		limit       int
	}{
		{"/api/webhook/commands/ack", firstToken, MaxBodyBytes},
		{"/api/responses", firstToken, maxResponseBytes},
		{diagnosticsPrefix + "/runtimes", firstToken, 4096},
	} {
		for _, extra := range []int{0, 1} {
			t.Run(fmt.Sprintf("%s/%d", route.path, extra), func(t *testing.T) {
				s, _ := diagnosticServer(t)
				body := `{"padding":"` + strings.Repeat("x", route.limit+extra-len(`{"padding":""}`)) + `"}`
				v, _, _, ok := decodeDeviceTransport([]byte(transportBody("POST", route.path, route.token, body)))
				if !ok || string(v.Body) != body {
					t.Fatal("nested JSON bytes were transformed")
				}
				got := transportCall(s, "POST", route.path, route.token, body)
				want := 400
				if extra == 1 {
					want = 413
				}
				expect(t, got, want)
			})
		}
	}
	s := newTestServer(t)
	dup := `{"ids":[], "ids":[]}`
	v, _, _, ok := decodeDeviceTransport([]byte(transportBody("POST", "/api/webhook/commands/ack", firstToken, dup)))
	if !ok || string(v.Body) != dup {
		t.Fatal("transport removed nested duplicates before route validation")
	}
	expect(t, transportCall(s, "POST", "/api/webhook/commands/ack", firstToken, dup), 400)
	base := transportBody("GET", "/api/webhook/commands", firstToken, "")
	for _, extra := range []int{0, 1} {
		body := base + strings.Repeat(" ", maxDeviceTransportBytes+extra-len(base))
		want := 200
		if extra == 1 {
			want = 413
		}
		expect(t, request(s, "POST", deviceTransportPath, "", body, nil), want)
	}
}

func TestDeviceTransportFullSizeResultAndDiagnosticEventLimit(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"channels","params":{}}`)
	prefix, suffix := `{"id":"`+id+`","status":"ok","data":{"text":"`, `"}}`
	body := prefix + strings.Repeat("<", maxResponseBytes-len(prefix)-len(suffix)) + suffix
	expect(t, transportCall(s, "POST", "/api/responses", firstToken, body), 200)
	result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, result, 200)
	if result.Body.String() != body {
		t.Fatal("full-size RPC result was changed or escaped")
	}

	d, _ := diagnosticServer(t)
	_, token := diagnosticRegister(t, d)
	for _, path := range []string{"/events", "/poll", "/results", "/repairs/poll", "/repairs/results"} {
		limit := 4096
		if path == "/events" {
			limit = 16384
		}
		for _, extra := range []int{0, 1} {
			body := `{"padding":"` + strings.Repeat("x", limit+extra-len(`{"padding":""}`)) + `"}`
			direct := request(d, "POST", diagnosticsPrefix+path, token, body, nil)
			wrapped := transportCall(d, "POST", diagnosticsPrefix+path, token, body)
			if direct.Code != wrapped.Code || direct.Body.String() != wrapped.Body.String() {
				t.Fatal("diagnostic route limit changed", path, extra)
			}
		}
	}
}

func TestDeviceTransportPairingAndDiscoveryRemainApprovalBound(t *testing.T) {
	s := pairingServer(t)
	expect(t, transportCall(s, "GET", "/api/discovery", "", ""), 200)
	expect(t, transportCall(s, "POST", "/api/pairings", "", `{"device_id":"unknown"}`), 400)
	w := transportCall(s, "POST", "/api/pairings", "", `{"device_id":"first"}`)
	expect(t, w, 201)
	var c claim
	if json.Unmarshal(w.Body.Bytes(), &c) != nil {
		t.Fatal("invalid claim")
	}
	path := "/api/pairings?id=" + c.ID
	w = transportCall(s, "GET", path, c.Secret, "")
	expect(t, w, 202)
	if strings.Contains(w.Body.String(), firstToken) {
		t.Fatal("token disclosed before approval")
	}
	expect(t, transportCall(s, "POST", "/api/pairings/approve", adminToken, fmt.Sprintf(`{"id":%q,"code":%q}`, c.ID, c.Code)), 400)
	expect(t, approve(t, s, c), 200)
	expect(t, transportCall(s, "GET", path, c.Secret, ""), 200)
	expect(t, transportCall(s, "DELETE", path, c.Secret, ""), 200)
	expect(t, transportCall(s, "GET", path, c.Secret, ""), 401)
}

func TestDeviceTransportNotificationMediaKeepsJSONAndRuntimeChecks(t *testing.T) {
	s := newTestServer(t)
	now := time.Now()
	s.now = func() time.Time { return now }
	path := "/api/notifications?runtime=r1&active=true&enabled=true&media=jpeg-v1"
	expect(t, transportCall(s, "GET", path, firstToken, ""), 200)
	n := sampleNotification("first")
	n.Media = mediaTestFrame(t)
	n.TargetRuntimes, n.MediaTargetRuntimes = s.NotificationTargets(n), s.NotificationMediaTargets(n)
	if s.DeliverNotification(n).Queued != 1 {
		t.Fatal("media notification was not queued")
	}
	w := transportCall(s, "GET", path, firstToken, "")
	expect(t, w, 200)
	var events struct{ Notifications []notification }
	if json.Unmarshal(w.Body.Bytes(), &events) != nil || len(events.Notifications) != 1 || events.Notifications[0].Media == nil {
		t.Fatal("media descriptor changed")
	}
	path = "/api/notifications/media?id=" + events.Notifications[0].Media.ID + "&runtime=r1"
	direct, wrapped := request(s, "GET", path, firstToken, "", nil), transportCall(s, "GET", path, firstToken, "")
	expect(t, wrapped, 200)
	if wrapped.Header().Get("Content-Type") != "application/json" || !bytes.Equal(direct.Body.Bytes(), wrapped.Body.Bytes()) || !bytes.Contains(wrapped.Body.Bytes(), []byte(`"data_base64"`)) {
		t.Fatal("notification media was wrapped or transformed")
	}
	expect(t, transportCall(s, "GET", path, secondToken, ""), 404)
	expect(t, transportCall(s, "GET", "/api/notifications?runtime=r1&active=false", firstToken, ""), 200)
	expect(t, transportCall(s, "GET", path, firstToken, ""), 404)
}

func TestDeviceTransportDiagnosticsRuntimeAndRepairLifecycle(t *testing.T) {
	s, _ := repairTestServer(t)
	call := func(path, token string, body any, status int) map[string]any {
		t.Helper()
		b, _ := json.Marshal(body)
		w := transportCall(s, "POST", diagnosticsPrefix+path, token, string(b))
		expect(t, w, status)
		if w.Header().Get("X-OTT-Diagnostics-Epoch") != s.diagnostics.epoch {
			t.Fatal("diagnostics epoch header lost")
		}
		var result map[string]any
		if json.Unmarshal(w.Body.Bytes(), &result) != nil {
			t.Fatal("invalid diagnostics result")
		}
		return result
	}
	v := call("/runtimes", firstToken, map[string]any{"instance_id": "tab", "boot_id": "boot", "capabilities": []string{"playback", "repairs"}, "consent": map[string]any{"granted": true, "epoch": "grant"}}, 201)
	rt, token := v["runtime_id"].(string), v["runtime_credential"].(string)
	started := diagnosticStart(t, s, rt, "start")
	poll := call("/poll", token, diagnosticPollBody(rt, 1, true), 200)
	if poll["control"].(map[string]any)["action"] != "start" {
		t.Fatal("missing diagnostics control")
	}
	call("/results", token, map[string]any{"runtime_id": rt, "session_id": started["session_id"], "request_id": started["request_id"], "control_revision": started["control_revision"], "status": "applied"}, 200)
	call("/events", token, diagnosticBatch(rt, started["session_id"].(string), 1), 200)
	id := repairStart(t, s, rt, "repair", "restart_stream")
	call("/repairs/poll", token, map[string]any{"runtime_id": rt}, 200)
	call("/repairs/results", token, repairResultBody(rt, id, "applied"), 200)
	if s.diagnostics.globalControl.used != 7 || s.diagnostics.globalEvents.used != 1 {
		t.Fatalf("diagnostics allowance charged more than once: control=%d events=%d", s.diagnostics.globalControl.used, s.diagnostics.globalEvents.used)
	}
}

type transportReadProbe struct{ read bool }

func (p *transportReadProbe) Read([]byte) (int, error) {
	p.read = true
	return 0, errors.New("unexpected body read")
}
func (p *transportReadProbe) Close() error { return nil }

func TestDeviceTransportIngressFramingCancellationAndHeaderWhitelist(t *testing.T) {
	valid := transportBody("GET", "/api/webhook/commands", firstToken, "")
	for _, setup := range []func(*http.Request){
		func(r *http.Request) { r.TransferEncoding = []string{"chunked"} },
		func(r *http.Request) { r.Header.Set("Expect", "100-continue") },
		func(r *http.Request) { r.Header.Set("Content-Type", "text/plain") },
		func(r *http.Request) { r.ContentLength = maxDeviceTransportBytes + 1 },
		func(r *http.Request) { r.Header.Set("Authorization", "Bearer "+adminToken) },
		func(r *http.Request) { r.URL.RawQuery = "host=other" },
	} {
		s := newTestServer(t)
		r := httptest.NewRequest("POST", deviceTransportPath, nil)
		r.Header.Set("Content-Type", "application/json")
		p := &transportReadProbe{}
		r.Body = p
		setup(r)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code < 400 || p.read || s.globalRate.count != 0 {
			t.Fatal("invalid framing reached body or logical handler")
		}
	}
	s := newTestServer(t)
	enqueue(t, s, "first", `{"command":"exit_player"}`)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	r := httptest.NewRequest("POST", deviceTransportPath, strings.NewReader(valid)).WithContext(ctx)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	expect(t, w, 408)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("cancelled envelope dispatched")
	}
	for len(s.transportSlots) < cap(s.transportSlots) {
		s.transportSlots <- struct{}{}
	}
	w = request(s, "POST", deviceTransportPath, "", valid, nil)
	expect(t, w, 429)
	if w.Header().Get("Retry-After") != "1" {
		t.Fatal("missing concurrency retry")
	}
	for len(s.transportSlots) > 0 {
		<-s.transportSlots
	}
	s.transportRate = bucket{start: s.now(), count: len(s.devices)*1600 + 300}
	w = request(s, "POST", deviceTransportPath, "", valid, nil)
	expect(t, w, 429)
	if w.Header().Get("Retry-After") != "60" {
		t.Fatal("missing ingress retry")
	}
	s.transportRate = bucket{start: s.now().Add(-time.Minute), count: 1000000}
	expect(t, request(s, "POST", deviceTransportPath, "", valid, nil), 200)
	base := transportBody("POST", diagnosticsPrefix+"/poll", firstToken, `{}`)
	for _, header := range []string{`{"X-OTT-Diagnostics-Epoch":"epoch"}`, `{"X-OTT-Diagnostics-Epoch":"epoch","X-OTT-Diagnostics-Epoch":"other"}`, `{"Authorization":"Bearer x"}`, `{"Origin":"null"}`, `{"X-OTT-Diagnostics-Epoch":"epoch","Cookie":"x"}`, `{}`, `null`} {
		body := base[:len(base)-1] + `,"headers":` + header + `}`
		_, _, headers, ok := decodeDeviceTransport([]byte(body))
		want := header == `{"X-OTT-Diagnostics-Epoch":"epoch"}`
		if ok != want || ok && headers.Get("X-OTT-Diagnostics-Epoch") != "epoch" {
			t.Fatalf("invalid header handling: %s", header)
		}
	}
	httpServer := httptest.NewServer(newTestServer(t))
	defer httpServer.Close()
	resp, err := http.Post(httpServer.URL+deviceTransportPath, "application/json", strings.NewReader(transportBody("GET", "/api/webhook/commands", "", "")))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil || resp.StatusCode != 401 || resp.Header.Get("WWW-Authenticate") == "" || resp.Header.Get("Content-Type") != "application/json" || !json.Valid(b) {
		t.Fatal("network response metadata changed")
	}
}

func TestDeviceTransportLargeSlotDoesNotBlockSmallRequests(t *testing.T) {
	s := newTestServer(t)
	s.transportLarge <- struct{}{}
	valid := transportBody("GET", "/api/webhook/commands", firstToken, "")
	expect(t, request(s, "POST", deviceTransportPath, "", valid, nil), 200)
	large := valid + strings.Repeat(" ", smallDeviceTransportBytes+1-len(valid))
	expect(t, request(s, "POST", deviceTransportPath, "", large, nil), 429)
	<-s.transportLarge
	expect(t, request(s, "POST", deviceTransportPath, "", large, nil), 200)
	for _, delta := range []int64{-1, 1} {
		r := httptest.NewRequest("POST", deviceTransportPath, strings.NewReader(valid))
		r.Header.Set("Content-Type", "application/json")
		r.ContentLength += delta
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		expect(t, w, 400)
	}
	r := httptest.NewRequest("POST", deviceTransportPath, strings.NewReader(large))
	r.Header.Set("Content-Type", "application/json")
	r.ContentLength = 1 // A false small length must not allocate a large body.
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	expect(t, w, 413)
	for _, body := range []string{valid, valid + strings.Repeat(" ", maxDeviceTransportBytes+1-len(valid))} {
		r = httptest.NewRequest("POST", deviceTransportPath, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		r.ContentLength = -1
		w = httptest.NewRecorder()
		s.ServeHTTP(w, r)
		want := 200
		if len(body) > maxDeviceTransportBytes {
			want = 413
		}
		expect(t, w, want)
		if len(s.transportLarge) != 0 || len(s.transportSlots) != 0 {
			t.Fatal("slot retained after request")
		}
	}
}

func TestDeviceTransportSharedContract(t *testing.T) {
	b, err := os.ReadFile("../../contracts/device-transport-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Max     int               `json:"max_envelope_bytes"`
		Small   int               `json:"small_envelope_bytes"`
		Valid   []json.RawMessage `json:"valid"`
		Invalid []json.RawMessage `json:"invalid"`
	}
	if json.Unmarshal(b, &fixture) != nil || fixture.Max != maxDeviceTransportBytes || fixture.Small != smallDeviceTransportBytes {
		t.Fatal("contract limits changed")
	}
	for _, group := range []struct {
		rows  []json.RawMessage
		valid bool
	}{{fixture.Valid, true}, {fixture.Invalid, false}} {
		for _, raw := range group.rows {
			_, _, _, valid := decodeDeviceTransport(raw)
			if valid != group.valid {
				t.Fatalf("shared fixture mismatch: %s", raw)
			}
		}
	}
}

func TestDeviceTransportCancellationReleasesSlotDuringDiscovery(t *testing.T) {
	s := pairingServer(t)
	entered, release := make(chan struct{}), make(chan struct{})
	s.discovery.lookup = func(context.Context) ([]discoveredServer, time.Duration, error) {
		close(entered)
		<-release
		return nil, 0, errors.New("test finished")
	}
	defer close(release)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := httptest.NewRequest("POST", deviceTransportPath, strings.NewReader(transportBody("GET", "/api/discovery", "", ""))).WithContext(ctx)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { s.ServeHTTP(w, r); close(done) }()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("discovery was not entered")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("cancellation did not reach original handler")
	}
	if len(s.transportSlots) != 0 || len(s.transportLarge) != 0 {
		t.Fatal("cancelled request retained slot")
	}
	expect(t, w, 503) // Preserve the original discovery handler's failure response.
}
