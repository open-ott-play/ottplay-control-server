package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const nativeCaptureTestID = "cap_0123456789abcdef0123456789abcdef"
const nativeCaptureTestCapability = `{"version":1,"operations":["start","status","read","stop"],"default_duration_s":30,"min_duration_s":5,"max_duration_s":60,"sample_interval_ms":1000,"max_records":64}`

func nativeCaptureTestParams(operation string) map[string]any {
	p := map[string]any{"version": 1, "runtime": "lg-native-capture", "operation": operation}
	if operation == "start" {
		p["duration_s"] = 30
	} else if operation == "status" {
		p["capture_id"] = nil
	} else if operation == "read" || operation == "stop" {
		p["capture_id"] = nativeCaptureTestID
	}
	return p
}

func nativeCaptureTestRequest(t *testing.T, params map[string]any) string {
	t.Helper()
	return `{"action":"native_playback_diagnostics","params":` + string(transferJSON(t, params)) + `}`
}

func TestNativePlaybackCaptureRequestBoundsAndExactShape(t *testing.T) {
	for _, operation := range []string{"start", "status", "read", "stop"} {
		p := nativeCaptureTestParams(operation)
		s := newTestServer(t)
		id := rpcID(t, s, nativeCaptureTestRequest(t, p))
		bound := s.devices[0].queue[0].nativePlaybackCapture
		if id == "" || bound == nil || bound.Runtime != p["runtime"] || bound.Operation != operation || bound.Duration != 30 {
			t.Fatal("native capture request lost binding")
		}
		if operation == "read" || operation == "stop" {
			if bound.CaptureID != nativeCaptureTestID {
				t.Fatal("native capture request lost capture ID")
			}
		}
	}
	for _, duration := range []int{5, 30, 60} {
		p := nativeCaptureTestParams("start")
		p["duration_s"] = duration
		bound := parseNativePlaybackCaptureRequest(transferObject(t, transferJSON(t, p)))
		if bound == nil || bound.Duration != duration {
			t.Fatal("valid duration rejected")
		}
	}
	for _, mutation := range []func(map[string]any){
		func(p map[string]any) { delete(p, "runtime") },
		func(p map[string]any) { p["runtime"] = "" },
		func(p map[string]any) { p["runtime"] = strings.Repeat("a", 97) },
		func(p map[string]any) { p["runtime"] = "private/path" },
		func(p map[string]any) { p["version"] = 2 },
		func(p map[string]any) { p["version"] = true },
		func(p map[string]any) { p["version"] = json.Number("1.0") },
		func(p map[string]any) { p["operation"] = "export" },
		func(p map[string]any) { delete(p, "duration_s") },
		func(p map[string]any) { p["duration_s"] = 4 },
		func(p map[string]any) { p["duration_s"] = 61 },
		func(p map[string]any) { p["duration_s"] = true },
		func(p map[string]any) { p["duration_s"] = nil },
		func(p map[string]any) { p["duration_s"] = json.Number("5.0") },
		func(p map[string]any) { p["capture_id"] = nativeCaptureTestID },
		func(p map[string]any) { p["extra"] = "private-value" },
	} {
		p := nativeCaptureTestParams("start")
		mutation(p)
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, nativeCaptureTestRequest(t, p), nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid native capture request was queued")
		}
	}
	for _, operation := range []string{"status", "read", "stop"} {
		for _, id := range []any{"", "cap_bad", strings.ToUpper(nativeCaptureTestID), 123, true, "cap_" + strings.Repeat("a", 33)} {
			p := nativeCaptureTestParams(operation)
			p["capture_id"] = id
			if parseNativePlaybackCaptureRequest(transferObject(t, transferJSON(t, p))) != nil {
				t.Fatalf("invalid capture ID accepted for %s", operation)
			}
		}
		p := nativeCaptureTestParams(operation)
		delete(p, "capture_id")
		if parseNativePlaybackCaptureRequest(transferObject(t, transferJSON(t, p))) != nil {
			t.Fatal("missing capture ID accepted")
		}
		p["capture_id"] = nil
		if operation != "status" && parseNativePlaybackCaptureRequest(transferObject(t, transferJSON(t, p))) != nil {
			t.Fatal("unbound capture mutation/read accepted")
		}
		p["capture_id"] = nativeCaptureTestID
		if parseNativePlaybackCaptureRequest(transferObject(t, transferJSON(t, p))) == nil {
			t.Fatal("bound capture request rejected")
		}
	}
	for _, bad := range []string{
		`{"version":1,"version":1,"runtime":"lg-native-capture","operation":"start","duration_s":30}`,
		`{"version":1,"runtime":"lg-native-capture","operation":"start","operation":"stop","duration_s":30}`,
		`null`, `[]`, `{}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"native_playback_diagnostics","params":`+bad+`}`, nil), 400)
	}
}

func TestNativePlaybackCaptureCapabilityIndependentAndProbeStripped(t *testing.T) {
	for _, passive := range []string{"", `,"native_playback_diagnostics":{"version":1}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{"native_playback_capture":true}}`)
		queued, err := decodeObject(s.devices[0].queue[0].data)
		if err != nil || string(queued["params"]) != "{}" {
			t.Fatal("controller probe leaked into player capabilities request")
		}
		body := `{"id":"` + id + `","status":"ok","data":{"native_playback_capture":` + nativeCaptureTestCapability + passive + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		if got.Body.String() != body {
			t.Fatal("native capture capability was rewritten")
		}
	}
	for _, value := range []string{"false", "1", "null", `"true"`, "{}"} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":{"native_playback_capture":`+value+`}}`, nil), 400)
	}
	for _, bad := range []string{
		`{}`, `null`, `[]`,
		strings.Replace(nativeCaptureTestCapability, `"version":1`, `"version":1.0`, 1),
		strings.Replace(nativeCaptureTestCapability, `"max_records":64`, `"max_records":65`, 1),
		strings.Replace(nativeCaptureTestCapability, `"min_duration_s":5`, `"min_duration_s":0`, 1),
		strings.Replace(nativeCaptureTestCapability, `"stop"`, `"read"`, 1),
		strings.Replace(nativeCaptureTestCapability, `"stop"`, `"export"`, 1),
		strings.Replace(nativeCaptureTestCapability, `,"stop"`, ``, 1),
		strings.Replace(nativeCaptureTestCapability, `["start","status","read","stop"]`, `["stop","read","status","start"]`, 1),
		strings.TrimSuffix(nativeCaptureTestCapability, "}") + `,"path":"private-marker"}`,
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := `{"id":"` + id + `","status":"ok","data":{"native_playback_capture":` + bad + `}}`
		got := request(s, "POST", "/api/responses", firstToken, body, nil)
		expect(t, got, 400)
		if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || s.resultBytes != 0 || strings.Contains(got.Body.String(), "private-marker") {
			t.Fatal("invalid capability consumed request or leaked details")
		}
	}
}

func TestNativePlaybackCaptureExactOperatorScopes(t *testing.T) {
	token := strings.Repeat("n", 32)
	hash := sha256.Sum256([]byte(token))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "capture_observer", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"native_playback_diagnostics.status", "native_playback_diagnostics.read"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, operation := range []string{"start", "status", "read", "stop"} {
		code := 403
		if operation == "status" || operation == "read" {
			code = 202
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", token, nativeCaptureTestRequest(t, nativeCaptureTestParams(operation)), nil), code)
	}
	expect(t, request(s, "POST", "/api/requests?device_id=second", token, nativeCaptureTestRequest(t, nativeCaptureTestParams("read")), nil), 403)
}

type nativeCaptureContractCase struct {
	Name    string                     `json:"name"`
	Request map[string]json.RawMessage `json:"request"`
	Status  string                     `json:"status"`
	Data    json.RawMessage            `json:"data"`
}

type nativeCaptureContract struct {
	Capability json.RawMessage             `json:"capability"`
	Cases      []nativeCaptureContractCase `json:"cases"`
	Errors     []nativeCaptureContractCase `json:"error_cases"`
	Fields     map[string]struct {
		Type     string   `json:"type"`
		Minimum  int64    `json:"minimum"`
		Maximum  int64    `json:"maximum"`
		Nullable bool     `json:"nullable"`
		Values   []string `json:"values"`
	} `json:"record_fields"`
}

func nativeCaptureFixture(t testing.TB) nativeCaptureContract {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/native-playback-capture-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var f nativeCaptureContract
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	if len(f.Cases) != 19 || len(f.Errors) != 28 || len(f.Fields) != 20 || !validNativePlaybackCaptureCapability(f.Capability) {
		t.Fatal("incomplete native capture shared fixture")
	}
	return f
}

func (f nativeCaptureContract) row(t testing.TB, name string) nativeCaptureContractCase {
	t.Helper()
	for _, row := range f.Cases {
		if row.Name == name {
			return row
		}
	}
	t.Fatalf("missing shared native capture case %s", name)
	return nativeCaptureContractCase{}
}

func (c nativeCaptureContractCase) data(t testing.TB) map[string]any {
	t.Helper()
	var result map[string]any
	if err := json.Unmarshal(c.Data, &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func (c nativeCaptureContractCase) request(t *testing.T) string {
	t.Helper()
	return `{"action":"native_playback_diagnostics","params":` + string(transferJSON(t, c.Request)) + `}`
}

func TestNativePlaybackCaptureSharedContractRoundTrip(t *testing.T) {
	f := nativeCaptureFixture(t)
	for _, row := range append(f.Cases, f.Errors...) {
		t.Run(row.Name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, row.request(t))
			expected := parseNativePlaybackCaptureRequest(row.Request)
			if got := s.devices[0].queue[0].nativePlaybackCapture; got == nil || expected == nil || *got != *expected {
				t.Fatal("capture expectation lost at enqueue")
			}
			body := screenshotEnvelope(id, row.Status, row.Data)
			expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
			expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != body || s.resultBytes != len(body) || s.bytes != 0 || len(s.devices[0].queue) != 0 {
				t.Fatal("capture receipt rewritten, duplicated, or left queued")
			}
			if got := s.devices[0].results[id].nativePlaybackCapture; got == nil || *got != *expected {
				t.Fatal("capture expectation lost in retained receipt")
			}
			bad := row.data(t)
			bad["operation"] = "other"
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, row.Status, bad), nil), 400)
			if s.resultBytes != len(body) || string(s.devices[0].results[id].data) != body {
				t.Fatal("bad duplicate replaced original receipt")
			}
		})
	}
}

func TestNativePlaybackCaptureRecordContractBoundaries(t *testing.T) {
	f := nativeCaptureFixture(t)
	row := f.row(t, "read_active")
	for name, field := range f.Fields {
		t.Run(name, func(t *testing.T) {
			newRecord := func() map[string]any { return row.data(t)["records"].([]any)[0].(map[string]any) }
			check := func(value any, want bool) {
				t.Helper()
				record := newRecord()
				record[name] = value
				if got := validNativePlaybackCaptureRecord(transferJSON(t, record)); got != want {
					t.Fatalf("field %s value %v accepted=%v, want %v", name, value, got, want)
				}
			}
			check(nil, field.Nullable)
			check(true, false)
			check(map[string]any{"private": "fixture"}, false)
			if field.Type == "integer" {
				check(field.Minimum, true)
				check(field.Maximum, true)
				check(field.Minimum-1, false)
				check(field.Maximum+1, false)
				check(json.Number("1.0"), false)
				check(json.Number("1e0"), false)
				check("1", false)
			} else {
				for _, value := range field.Values {
					check(value, true)
				}
				check("unknown-private-text", false)
			}
			record := newRecord()
			delete(record, name)
			if validNativePlaybackCaptureRecord(transferJSON(t, record)) {
				t.Fatal("missing required record field accepted")
			}
		})
	}
}

func TestNativePlaybackCapturePrimingExtension(t *testing.T) {
	base := nativeCaptureFixture(t).row(t, "read_active").data(t)["records"].([]any)[0].(map[string]any)
	if !validNativePlaybackCaptureRecord(transferJSON(t, base)) {
		t.Fatal("legacy record refused")
	}
	fields := map[string]any{
		"prime_pending": true, "prime_kind": "fresh", "prime_required_runway_ms": 2000,
		"prime_accepted_video_ms": 1000, "prime_accepted_audio_ms": 1000,
		"prime_queued_video_ms": 2700, "prime_queued_audio_ms": 2700,
		"prime_external_lease": true, "prime_audio_expected": true,
	}
	for key, value := range fields {
		base[key] = value
	}
	if !validNativePlaybackCaptureRecord(transferJSON(t, base)) {
		t.Fatal("complete priming record refused")
	}
	for key, value := range fields {
		delete(base, key)
		if validNativePlaybackCaptureRecord(transferJSON(t, base)) {
			t.Fatalf("partial extension accepted without %s", key)
		}
		base[key] = value
	}
	for key, bad := range map[string]any{
		"prime_kind": "private-text", "prime_pending": 1,
		"prime_queued_video_ms": -1, "prime_queued_audio_ms": 86400001,
		"prime_required_runway_ms": true, "private_url": "private-text",
	} {
		before, existed := base[key]
		base[key] = bad
		if validNativePlaybackCaptureRecord(transferJSON(t, base)) {
			t.Fatalf("invalid priming field accepted: %s", key)
		}
		if existed {
			base[key] = before
		} else {
			delete(base, key)
		}
	}
}

func TestNativePlaybackCaptureRejectsPrivateAndInconsistentResponses(t *testing.T) {
	row := nativeCaptureFixture(t).row(t, "read_active")
	mutations := map[string]func(map[string]any){
		"version bool":    func(v map[string]any) { v["version"] = true },
		"version float":   func(v map[string]any) { v["version"] = json.Number("1.0") },
		"operation":       func(v map[string]any) { v["operation"] = "status" },
		"capture":         func(v map[string]any) { v["capture_id"] = nativeCaptureTestID },
		"null capture":    func(v map[string]any) { v["capture_id"] = nil },
		"idle":            func(v map[string]any) { v["state"] = "idle" },
		"free text":       func(v map[string]any) { v["state"] = "fixture-secret" },
		"extra runtime":   func(v map[string]any) { v["runtime"] = "fixture-secret" },
		"extra field":     func(v map[string]any) { v["url"] = "fixture-secret" },
		"elapsed bound":   func(v map[string]any) { v["elapsed_ms"] = 60001 },
		"dropped bound":   func(v map[string]any) { v["dropped"] = uint64(4294967296) },
		"missing records": func(v map[string]any) { delete(v, "records") },
		"null records":    func(v map[string]any) { v["records"] = nil },
		"count records":   func(v map[string]any) { v["records"] = 1 },
		"late sample":     func(v map[string]any) { v["records"].([]any)[0].(map[string]any)["t_ms"] = 1001 },
		"record path":     func(v map[string]any) { v["records"].([]any)[0].(map[string]any)["path"] = "fixture-secret" },
		"too many records": func(v map[string]any) {
			records := make([]any, 65)
			for i := range records {
				records[i] = v["records"].([]any)[0]
			}
			v["records"] = records
		},
		"reverse sample order": func(v map[string]any) {
			r := row.data(t)["records"].([]any)[0].(map[string]any)
			r["t_ms"] = 999
			v["records"] = append(v["records"].([]any), r)
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, row.request(t))
			data := row.data(t)
			mutate(data)
			got := request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil)
			expect(t, got, 400)
			if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || s.resultBytes != 0 || strings.Contains(got.Body.String(), "fixture-secret") {
				t.Fatal("invalid capture response consumed request, was stored, or leaked details")
			}
		})
	}
	for _, bad := range []string{
		strings.Replace(string(row.Data), `"version": 1`, `"version":1,"version":1`, 1),
		strings.Replace(string(row.Data), `"frames": 7`, `"frames":7,"frames":7`, 1),
		strings.Replace(string(row.Data), `"frames": 7`, `"frames":NaN`, 1),
	} {
		if validNativePlaybackCaptureResult(json.RawMessage(bad), "ok", parseNativePlaybackCaptureRequest(row.Request)) {
			t.Fatal("duplicate or non-JSON record accepted")
		}
	}
}

func TestNativePlaybackCaptureAllowsRingAndInternalCounterReset(t *testing.T) {
	row := nativeCaptureFixture(t).row(t, "read_active")
	data := row.data(t)
	records := make([]any, 64)
	for i := range records {
		record := row.data(t)["records"].([]any)[0].(map[string]any)
		record["t_ms"] = 1000 // Repeated bounded timestamps are valid.
		record["frames"] = 64 - i
		record["network_rx_bytes"] = 100 - i
		records[i] = record
	}
	data["records"] = records
	data["dropped"] = uint64(4294967295)
	if !validNativePlaybackCaptureResult(transferJSON(t, data), "ok", parseNativePlaybackCaptureRequest(row.Request)) {
		t.Fatal("valid bounded ring or internal counter reset rejected")
	}
}

func TestNativePlaybackCaptureResponseStateAndErrorBinding(t *testing.T) {
	f := nativeCaptureFixture(t)
	for _, tc := range []struct {
		name, fixture string
		mutate        func(map[string]any)
	}{
		{"idle capture", "idle_discovery", func(v map[string]any) { v["capture_id"] = nativeCaptureTestID }},
		{"idle elapsed", "idle_discovery", func(v map[string]any) { v["elapsed_ms"] = 1 }},
		{"idle count", "idle_discovery", func(v map[string]any) { v["records"] = 1 }},
		{"idle dropped", "idle_discovery", func(v map[string]any) { v["dropped"] = 1 }},
		{"status count overflow", "active_bound_status", func(v map[string]any) { v["records"] = 65 }},
		{"status records array", "active_bound_status", func(v map[string]any) { v["records"] = []any{} }},
		{"bound idle", "active_bound_status", func(v map[string]any) {
			v["state"] = "idle"
			v["capture_id"] = nil
			v["elapsed_ms"] = 0
			v["records"] = 0
		}},
		{"start idle", "start", func(v map[string]any) { v["state"] = "idle" }},
		{"start stopped", "start", func(v map[string]any) { v["state"] = "stopped" }},
		{"start extra count", "start", func(v map[string]any) { v["records"] = 0 }},
		{"stop capturing", "stop_active", func(v map[string]any) { v["state"] = "capturing" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			row := f.row(t, tc.fixture)
			data := row.data(t)
			tc.mutate(data)
			if validNativePlaybackCaptureResult(transferJSON(t, data), "ok", parseNativePlaybackCaptureRequest(row.Request)) {
				t.Fatal("inconsistent native capture state accepted")
			}
		})
	}
	expected := parseNativePlaybackCaptureRequest(f.row(t, "read_active").Request)
	for _, body := range []string{
		`{"version":1,"operation":"read","error":"fixture-secret"}`,
		`{"version":1,"operation":"stop","error":"capture_not_found"}`,
		`{"version":1,"operation":"read","error":"capture_not_found","capture_id":"` + nativeCaptureTestID + `"}`,
		`{"version":1,"operation":"read","error":"capture_not_found","runtime":"fixture-secret"}`,
		`{"version":1,"operation":"read","error":{"code":"capture_not_found"}}`,
		`{"version":1,"operation":"read","error":"unsupported"}`,
	} {
		if validNativePlaybackCaptureResult(json.RawMessage(body), "rejected", expected) {
			t.Fatal("unbound or private capture error accepted")
		}
	}
	if validNativePlaybackCaptureResult(json.RawMessage(`{"version":1,"operation":"read","error":"capture_not_found"}`), "unsupported", expected) {
		t.Fatal("unsupported status accepted a different error")
	}
}

func TestNativePlaybackCaptureEnvelopeLimitAndDeviceTransport(t *testing.T) {
	row := nativeCaptureFixture(t).row(t, "read_active")
	s := newTestServer(t)
	id := rpcID(t, s, row.request(t))
	body := screenshotEnvelope(id, "ok", row.Data)
	padded := strings.TrimSuffix(body, "}") + strings.Repeat(" ", maxNativePlaybackCaptureOuterBytes-len(body)) + "}"
	expect(t, request(s, "POST", "/api/responses", firstToken, padded+" ", nil), 400)
	if s.resultBytes != 0 || len(s.devices[0].queue) != 1 {
		t.Fatal("oversized response consumed request")
	}
	expect(t, transportCall(s, "POST", "/api/responses", firstToken, padded), 200)
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != padded || s.resultBytes != maxNativePlaybackCaptureOuterBytes {
		t.Fatal("device transport changed response bytes or limit accounting")
	}
	expect(t, request(s, "POST", "/api/responses", firstToken, padded+" ", nil), 400)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(strings.Repeat("f", 32), "ok", row.Data), nil), 404)
	if len(s.devices[0].results) != 1 || s.resultBytes != maxNativePlaybackCaptureOuterBytes {
		t.Fatal("unknown/oversized duplicate mutated retained capture")
	}
}

func TestNativePlaybackCaptureJournalRetainsOnlyReceiptMetadata(t *testing.T) {
	row := nativeCaptureFixture(t).row(t, "read_active")
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	key := "native-capture-read-0001"
	input := strings.TrimSuffix(row.request(t), "}") + `,"idempotency_key":"` + key + `"}`
	id := rpcID(t, s, input)
	body := screenshotEnvelope(id, "ok", row.Data)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	disk, err := os.ReadFile(c.Journal.Path)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"capture_id", "frames", "network_rx_bytes", "native_fixture_1", "cap_aaaaaaaa"} {
		if strings.Contains(string(disk), forbidden) {
			t.Fatalf("capture payload persisted: %s", forbidden)
		}
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	receipt := operationValue(t, s, key)
	if receipt["state"] != "responded" || receipt["result_status"] != "ok" || receipt["action"] != "native_playback_diagnostics.read" || receipt["id"] != id {
		t.Fatal("journal lost bounded receipt metadata")
	}
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil), 404)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, input, nil), 202)
	if len(s.devices[0].queue) != 0 || len(s.devices[0].results) != 0 {
		t.Fatal("journal replayed capture or restored volatile report")
	}
}
