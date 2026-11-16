package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const preparationRuntime = "native_prepare.1"
const preparationRequestBody = `{"action":"playback_preparation_diagnostics","params":{"version":1,"runtime":"native_prepare.1"}}`

func preparationSample() map[string]any {
	return map[string]any{"version": 1, "runtime": preparationRuntime, "phase": "failed", "attempt_generation": 5, "last_failure": "inspect_failed", "cause": "timeout", "retry": map[string]any{"pending": true, "failures": 2, "retry_after_ms": 1500}}
}

func TestPlaybackPreparationRoundTripAndRetainedBinding(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, preparationRequestBody)
	expected := &playbackPreparationRequest{Runtime: preparationRuntime}
	if got := s.devices[0].queue[0].playbackPreparation; got == nil || *got != *expected {
		t.Fatal("request runtime not bound")
	}
	body := screenshotEnvelope(id, "ok", preparationSample())
	expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
	expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != body || s.resultBytes != len(body) || s.bytes != 0 || len(s.devices[0].queue) != 0 {
		t.Fatal("response not retained exactly once")
	}
	if bound := s.devices[0].results[id].playbackPreparation; bound == nil || *bound != *expected {
		t.Fatal("receipt lost runtime binding")
	}
	bad := preparationSample()
	bad["runtime"] = "another-runtime"
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", bad), nil), 400)
	if s.devices[0].results[id].data == nil || s.resultBytes != len(body) {
		t.Fatal("mismatched duplicate changed result")
	}
}

func TestPlaybackPreparationRequestExactShape(t *testing.T) {
	for _, runtime := range []string{"a", "A_0.-", strings.Repeat("a", 96)} {
		raw := transferJSON(t, map[string]any{"version": 1, "runtime": runtime})
		if parsePlaybackPreparationRequest(transferObject(t, raw)) == nil {
			t.Fatal("valid runtime refused")
		}
	}
	for _, bad := range []string{`{}`, `null`, `[]`, `{"version":2,"runtime":"r"}`, `{"version":true,"runtime":"r"}`, `{"version":1.0,"runtime":"r"}`, `{"version":1e0,"runtime":"r"}`, `{"version":1,"version":1,"runtime":"r"}`, `{"version":1,"runtime":""}`, `{"version":1,"runtime":"with space"}`, `{"version":1,"runtime":"r/a"}`, `{"version":1,"runtime":"` + strings.Repeat("a", 97) + `"}`, `{"version":1,"runtime":"r","operation":"restart"}`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"playback_preparation_diagnostics","params":`+bad+`}`, nil), 400)
		if s.bytes != 0 || len(s.devices[0].queue) != 0 {
			t.Fatal("invalid request queued")
		}
	}
}

func TestPlaybackPreparationClosedEnumsAndNulls(t *testing.T) {
	expected := &playbackPreparationRequest{Runtime: preparationRuntime}
	for field, values := range map[string][]any{
		"phase":              {"idle", "resolving", "inspecting", "awaiting_quality", "admitting", "admitted", "failed", "cancelled"},
		"last_failure":       {nil, "resolve_failed", "inspect_failed", "worker_unavailable", "admission_failed", "quality_save_failed"},
		"cause":              {nil, "unknown", "timeout", "transport", "http_401", "http_403", "http_404", "http_429", "http_server", "hls_invalid_header", "hls_invalid_utf8", "media_open_refused", "media_read_failed", "media_container_unavailable", "media_stream_probe_failed", "media_no_video", "inspection_stopped", "inspection_cancelled", "inspection_unavailable", "resource_limit", "track_identity", "codec_unsupported"},
		"attempt_generation": {nil, 0, json.Number("9007199254740991")},
	} {
		for _, value := range values {
			sample := preparationSample()
			sample[field] = value
			if !validPlaybackPreparationResult(transferJSON(t, sample), "ok", expected) {
				t.Fatalf("valid %s=%v rejected", field, value)
			}
		}
	}
	sample := preparationSample()
	sample["retry"] = map[string]any{"pending": false, "failures": nil, "retry_after_ms": nil}
	if !validPlaybackPreparationResult(transferJSON(t, sample), "ok", expected) {
		t.Fatal("no retry null evidence refused")
	}
	sample["retry"] = map[string]any{"pending": true, "failures": json.Number("4294967295"), "retry_after_ms": 30000}
	if !validPlaybackPreparationResult(transferJSON(t, sample), "ok", expected) {
		t.Fatal("retry maximum refused")
	}
}

func TestPlaybackPreparationRejectsMalformedOrPrivateResult(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"runtime":              func(m map[string]any) { m["runtime"] = "other" },
		"version":              func(m map[string]any) { m["version"] = true },
		"phase":                func(m map[string]any) { m["phase"] = "streaming" },
		"failure":              func(m map[string]any) { m["last_failure"] = "fixture-secret" },
		"cause":                func(m map[string]any) { m["cause"] = "https://private.invalid/fixture-secret" },
		"extra":                func(m map[string]any) { m["source_id"] = "fixture-secret" },
		"retry extra":          func(m map[string]any) { m["retry"].(map[string]any)["stop_reason"] = "fixture-secret" },
		"retry false counters": func(m map[string]any) { m["retry"].(map[string]any)["pending"] = false },
		"retry true null":      func(m map[string]any) { m["retry"].(map[string]any)["failures"] = nil },
		"retry null bool":      func(m map[string]any) { m["retry"].(map[string]any)["pending"] = nil },
		"retry delay":          func(m map[string]any) { m["retry"].(map[string]any)["retry_after_ms"] = 30001 },
		"retry failures":       func(m map[string]any) { m["retry"].(map[string]any)["failures"] = json.Number("4294967296") },
		"generation":           func(m map[string]any) { m["attempt_generation"] = json.Number("9007199254740992") },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, preparationRequestBody)
			sample := preparationSample()
			mutate(sample)
			response := request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", sample), nil)
			expect(t, response, 400)
			if s.resultBytes != 0 || len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || strings.Contains(response.Body.String(), "fixture-secret") {
				t.Fatal("invalid result consumed request or leaked data")
			}
		})
	}
	expected := &playbackPreparationRequest{Runtime: preparationRuntime}
	for _, key := range []string{"attempt_generation", "failures", "retry_after_ms"} {
		for _, value := range []any{-1, true, "1", json.Number("1.0"), json.Number("1e0"), json.Number("0.5"), json.Number("9223372036854775808")} {
			sample := preparationSample()
			if key == "attempt_generation" {
				sample[key] = value
			} else {
				sample["retry"].(map[string]any)[key] = value
			}
			if validPlaybackPreparationResult(transferJSON(t, sample), "ok", expected) {
				t.Fatalf("malformed integer %s=%v accepted", key, value)
			}
		}
	}
	for _, path := range []string{"root", "retry"} {
		sample := preparationSample()
		obj := sample
		if path == "retry" {
			obj = sample["retry"].(map[string]any)
		}
		for key := range obj {
			copy := preparationSample()
			target := copy
			if path == "retry" {
				target = copy["retry"].(map[string]any)
			}
			delete(target, key)
			if validPlaybackPreparationResult(transferJSON(t, copy), "ok", expected) {
				t.Fatalf("missing %s/%s accepted", path, key)
			}
		}
	}
	for _, raw := range []string{
		`{"version":1,"runtime":"native_prepare.1","phase":"idle","attempt_generation":null,"last_failure":null,"cause":null,"retry":{"pending":false,"failures":null,"retry_after_ms":null,"pending":false}}`,
		`{"version":1,"version":1,"runtime":"native_prepare.1","phase":"idle","attempt_generation":null,"last_failure":null,"cause":null,"retry":{"pending":false,"failures":null,"retry_after_ms":null}}`,
		`{"version":1,"runtime":"native_prepare.1","phase":"idle","attempt_generation":null,"last_failure":null,"cause":null,"retry":{"pending":false,"failures":null,"retry_after_ms":null},"padding":"` + strings.Repeat("x", 4096) + `"}`,
	} {
		if validPlaybackPreparationResult(json.RawMessage(raw), "ok", expected) {
			t.Fatal("duplicate/oversize result accepted")
		}
	}
}

func TestPlaybackPreparationNegativeResultsBoundExceptRuntimeMismatch(t *testing.T) {
	for _, reason := range []string{"invalid_request", "runtime_mismatch", "unavailable", "unsupported", "policy_denied"} {
		s := newTestServer(t)
		id := rpcID(t, s, preparationRequestBody)
		sample := map[string]any{"version": 1, "runtime": preparationRuntime, "error": reason}
		if reason == "runtime_mismatch" {
			sample["runtime"] = "receiver-current.2"
		}
		body := screenshotEnvelope(id, "rejected", sample)
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		if got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil); got.Body.String() != body {
			t.Fatal("negative result changed")
		}
	}
	expected := &playbackPreparationRequest{Runtime: preparationRuntime}
	for _, status := range []string{"ok", "unsupported", "rejected"} {
		for _, reason := range []string{"unavailable", "fixture-secret"} {
			raw := transferJSON(t, map[string]any{"version": 1, "runtime": "other-runtime", "error": reason})
			if validPlaybackPreparationResult(raw, status, expected) {
				t.Fatal("unbound or unknown negative accepted")
			}
		}
	}
	for _, runtime := range []string{"", "a/b", strings.Repeat("a", 97)} {
		raw := transferJSON(t, map[string]any{"version": 1, "runtime": runtime, "error": "runtime_mismatch"})
		if validPlaybackPreparationResult(raw, "rejected", expected) {
			t.Fatal("malformed mismatch runtime accepted")
		}
	}
}

func TestPlaybackPreparationCapabilityOptionalAndStrict(t *testing.T) {
	for _, marker := range []string{"", `{"version":1}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		data := `{"player":{"runtime":"native_prepare.1"}}`
		if marker != "" {
			data = strings.TrimSuffix(data, "}") + `,"playback_preparation_diagnostics":` + marker + `}`
		}
		body := screenshotEnvelope(id, "ok", json.RawMessage(data))
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		if got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil); got.Body.String() != body {
			t.Fatal("capabilities rewritten")
		}
	}
	for _, bad := range []string{`{}`, `null`, `[]`, `true`, `1`, `{"version":null}`, `{"version":true}`, `{"version":0}`, `{"version":2}`, `{"version":1.0}`, `{"version":1e0}`, `{"version":"1"}`, `{"version":1,"version":1}`, `{"version":1,"url":"fixture-secret"}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := `{"id":"` + id + `","status":"ok","data":{"player":{"runtime":"native_prepare.1"},"playback_preparation_diagnostics":` + bad + `}}`
		got := request(s, "POST", "/api/responses", firstToken, body, nil)
		expect(t, got, 400)
		if s.resultBytes != 0 || len(s.devices[0].queue) != 1 || strings.Contains(got.Body.String(), "fixture-secret") {
			t.Fatal("bad marker retained or leaked")
		}
	}
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{"playback_preparation_diagnostics":{"version":1}}}`, nil), 400)
}

func TestPlaybackPreparationOperatorScopeAndExpiry(t *testing.T) {
	token := strings.Repeat("q", 32)
	hash := sha256.Sum256([]byte(token))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "preparation_observer", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"playback_preparation_diagnostics"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, preparationRequestBody, nil), 202)
	expect(t, request(s, "POST", "/api/requests?device_id=second", token, preparationRequestBody, nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, `{"action":"status","params":{}}`, nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, preparationRequestBody, nil), 403)
	s2 := newTestServer(t)
	id := rpcID(t, s2, preparationRequestBody)
	s2.now = func() time.Time { return time.Now().Add(24 * time.Hour) }
	expect(t, request(s2, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", preparationSample()), nil), 404)
	if len(s2.devices[0].results) != 0 {
		t.Fatal("expired result retained")
	}
}
