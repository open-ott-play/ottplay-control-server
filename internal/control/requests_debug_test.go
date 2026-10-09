package control

import (
	"encoding/json"
	"strings"
	"testing"
)

func inspectDebugFixture() map[string]any {
	return map[string]any{
		"version": 1, "runtime": "page-123", "capturedAt": 1791264000000, "platform": "capacitor-ios",
		"metrics": map[string]any{"uptimeMs": 42.5, "visible": true, "loopSamples": 2,
			"controlPendingRequests": 0, "controlPendingResponses": 1, "controlConsecutiveFailures": 2, "controlActive": false},
		"media": []any{map[string]any{"lane": "main", "generation": 2, "handleId": 3,
			"metrics": map[string]any{"paused": false, "volume": 0.5, "readyState": 4, "totalFrames": 10}}},
		"events": []any{map[string]any{"sequence": 2, "elapsedMs": 0.5, "code": "started"}}, "eventsDropped": 1,
		"native": map[string]any{"state": "available", "data": map[string]any{
			"version": 1, "platform": "ios", "appVersion": "1.2.3", "osVersion": "17.0", "webviewVersion": nil,
			"metrics": map[string]any{"footprintBytes": 42, "foreground": true},
		}},
	}
}

func debugEnvelope() map[string]any {
	return map[string]any{"version": 1, "runtime": "page-123", "section": "debug", "data": inspectDebugFixture()}
}

func TestDebugRequestRoundTripAndRuntimeBinding(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, inspectPayload("debug"))
	body := debugEnvelope()
	body["runtime"] = "another-page"
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil), 400)
	body["runtime"] = "page-123"
	body["section"] = "snapshot"
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil), 400)
	body["section"] = "debug"
	expect(t, request(s, "POST", "/api/responses", secondToken, screenshotEnvelope(id, "ok", body), nil), 404)
	result := screenshotEnvelope(id, "ok", body)
	expect(t, request(s, "POST", "/api/responses", firstToken, result, nil), 200)
	expect(t, request(s, "POST", "/api/responses", firstToken, result, nil), 200)
	response := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, response, 200)
	if response.Body.String() != result || s.bytes != 0 {
		t.Fatal("debug result altered or stayed queued")
	}
	// A duplicate must retain its original expectation, including negative replies.
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "unsupported", map[string]any{"error": "unsupported"}), nil), 400)
}

func TestDebugRejectsMalformedDataBeforeStoring(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"runtime":               func(v map[string]any) { v["runtime"] = "other" },
		"version bool":          func(v map[string]any) { v["version"] = true },
		"missing":               func(v map[string]any) { delete(v, "platform") },
		"raw field":             func(v map[string]any) { v["url"] = "secret" },
		"too big":               func(v map[string]any) { v["padding"] = strings.Repeat("x", maxDebugSnapshotBytes) },
		"safe integer":          func(v map[string]any) { v["capturedAt"] = json.Number("9007199254740992") },
		"fractional integer":    func(v map[string]any) { v["eventsDropped"] = 1.5 },
		"metric bool":           func(v map[string]any) { v["metrics"].(map[string]any)["uptimeMs"] = true },
		"metric negative":       func(v map[string]any) { v["metrics"].(map[string]any)["uptimeMs"] = -1 },
		"metric unknown":        func(v map[string]any) { v["metrics"].(map[string]any)["url"] = "secret" },
		"combined metric names": func(v map[string]any) { v["metrics"].(map[string]any)["uptimeMs loopSamples"] = 1 },
		"bool number":           func(v map[string]any) { v["metrics"].(map[string]any)["visible"] = 1 },
		"null metrics":          func(v map[string]any) { v["metrics"] = nil },
		"duplicate lane":        func(v map[string]any) { v["media"] = append(v["media"].([]any), v["media"].([]any)[0]) },
		"volume": func(v map[string]any) {
			v["media"].([]any)[0].(map[string]any)["metrics"].(map[string]any)["volume"] = 1.1
		},
		"null media":      func(v map[string]any) { v["media"] = nil },
		"event duplicate": func(v map[string]any) { v["events"] = append(v["events"].([]any), v["events"].([]any)[0]) },
		"event text":      func(v map[string]any) { v["events"].([]any)[0].(map[string]any)["code"] = "private" },
		"event sequence":  func(v map[string]any) { v["events"].([]any)[0].(map[string]any)["sequence"] = 0 },
		"native status":   func(v map[string]any) { v["native"].(map[string]any)["state"] = "timeout" },
		"native missing":  func(v map[string]any) { v["native"].(map[string]any)["data"] = nil },
		"native version":  func(v map[string]any) { v["native"].(map[string]any)["data"].(map[string]any)["version"] = true },
		"native name": func(v map[string]any) {
			v["native"].(map[string]any)["data"].(map[string]any)["osVersion"] = "private/value"
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, inspectPayload("debug"))
			body := debugEnvelope()
			mutate(body["data"].(map[string]any))
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil), 400)
			if s.resultBytes != 0 || len(s.devices[0].queue) != 1 {
				t.Fatal("malformed debug changed queue/results")
			}
		})
	}
}

func TestDebugOptionalMetricsAndNativeUnavailability(t *testing.T) {
	for _, state := range []string{"unsupported", "unavailable", "timeout", "invalid"} {
		body := inspectDebugFixture()
		body["metrics"], body["media"], body["events"] = map[string]any{}, []any{}, []any{}
		body["native"] = map[string]any{"state": state, "data": nil}
		raw, _ := json.Marshal(body)
		if !validDebugSnapshot(raw, "page-123") {
			t.Fatalf("valid unavailable state %s rejected", state)
		}
	}
}

func TestDebugRejectsDuplicateKeysAndNonFiniteTokens(t *testing.T) {
	for _, replacement := range []string{`"uptimeMs":1,"uptimeMs":2`, `"uptimeMs":1e999`, `"uptimeMs":null`} {
		body, _ := json.Marshal(inspectDebugFixture())
		raw := strings.Replace(string(body), `"uptimeMs":42.5`, replacement, 1)
		if validDebugSnapshot([]byte(raw), "page-123") {
			t.Fatal("ambiguous metric accepted")
		}
	}
}

func TestDebugNegativeRepliesStayBound(t *testing.T) {
	for _, status := range []string{"rejected", "unsupported"} {
		s := newTestServer(t)
		id := rpcID(t, s, inspectPayload("debug"))
		v := map[string]any{"version": 1, "runtime": "other", "section": "debug", "error": "unsupported"}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 400)
		v["runtime"] = "page-123"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil), 200)
	}
}
