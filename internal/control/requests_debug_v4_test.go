package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func debugV4Fixture(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/runtime-debug-v4.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]any
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture["response"].(map[string]any)
}
func debugV4Lane(body map[string]any) map[string]any {
	return body["data"].(map[string]any)["media"].([]any)[0].(map[string]any)
}
func debugV4Errors(lane map[string]any) map[string]any {
	return lane["lastAttempt"].(map[string]any)["errors"].(map[string]any)
}
func TestDebugV4RoundtripAndLegacyIsolation(t *testing.T) {
	for _, empty := range []bool{false, true} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"inspect","params":{"version":4,"runtime":"page-one","section":"debug"}}`)
		body := debugV4Fixture(t)
		if empty {
			lane := debugV4Lane(body)
			lane["attempt"], lane["lastAttempt"] = nil, nil
		}
		result := screenshotEnvelope(id, "ok", body)
		expect(t, request(s, "POST", "/api/responses", firstToken, result, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		expect(t, got, 200)
		if got.Body.String() != result || s.bytes != 0 {
			t.Fatal("v4 result altered or retained pending bytes")
		}
	}
	for _, version := range []int{1, 2, 3} {
		body := debugV4Fixture(t)["data"].(map[string]any)
		body["version"] = version
		raw, _ := json.Marshal(body)
		if validDebugSnapshotVersion(raw, "page-one", version) {
			t.Fatalf("legacy version %d accepted v4 fields", version)
		}
	}
	// The existing v3 fixture remains valid and failed is v4-only.
	body := debugV3Fixture(t)["data"].(map[string]any)
	raw, _ := json.Marshal(body)
	if !validDebugSnapshotVersion(raw, "page-one", 3) {
		t.Fatal("v3 changed")
	}
	body["media"].([]any)[0].(map[string]any)["lastAttempt"].(map[string]any)["reason"] = "failed"
	raw, _ = json.Marshal(body)
	if validDebugSnapshotVersion(raw, "page-one", 3) {
		t.Fatal("v3 accepted failed reason")
	}
}
func TestDebugV4RejectsUnsafeEvidenceBeforeStorage(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"missing errors":            func(l map[string]any) { delete(l["attempt"].(map[string]any), "errors") },
		"extra summary":             func(l map[string]any) { debugV4Errors(l)["message"] = "private" },
		"missing summary":           func(l map[string]any) { delete(debugV4Errors(l), "observed") },
		"negative observed":         func(l map[string]any) { debugV4Errors(l)["observed"] = -1 },
		"fraction observed":         func(l map[string]any) { debugV4Errors(l)["observed"] = 0.5 },
		"bool observed":             func(l map[string]any) { debugV4Errors(l)["observed"] = true },
		"oversafe observed":         func(l map[string]any) { debugV4Errors(l)["observed"] = json.Number("9007199254740992") },
		"drops exceed observed":     func(l map[string]any) { debugV4Errors(l)["dropped"] = 2 },
		"zero observed with errors": func(l map[string]any) { debugV4Errors(l)["observed"] = 0 },
		"first without latest":      func(l map[string]any) { debugV4Errors(l)["latest"] = nil },
		"backwards sequence": func(l map[string]any) {
			e := debugV4Errors(l)
			e["observed"] = 2
			e["firstFatal"].(map[string]any)["sequence"] = 2
		},
	}
	for _, slot := range []string{"firstFatal", "latest"} {
		for field, bad := range map[string]any{"sequence": 0, "elapsedMs": -1, "engine": "secret-url", "type": "networkError", "detail": "secret-url", "fatal": nil, "httpStatus": 600, "mediaErrorCode": 5, "url": "private"} {
			slot, field, bad := slot, field, bad
			mutations[slot+"/"+field] = func(l map[string]any) { debugV4Errors(l)[slot].(map[string]any)[field] = bad }
		}
	}
	mutations["nonfatal first"] = func(l map[string]any) { debugV4Errors(l)["firstFatal"].(map[string]any)["fatal"] = false }
	mutations["missing error field"] = func(l map[string]any) { delete(debugV4Errors(l)["latest"].(map[string]any), "httpStatus") }
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"inspect","params":{"version":4,"runtime":"page-one","section":"debug"}}`)
			body := debugV4Fixture(t)
			mutate(debugV4Lane(body))
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil), 400)
			if s.resultBytes != 0 || len(s.devices[0].queue) != 1 {
				t.Fatal("invalid evidence stored or consumed request")
			}
		})
	}
	raw, _ := json.Marshal(debugV4Fixture(t)["data"])
	for _, bad := range []string{`"observed":1,"observed":2`, `"observed":1e999`} {
		changed := strings.Replace(string(raw), `"observed":1`, bad, 1)
		if validDebugSnapshotVersion([]byte(changed), "page-one", 4) {
			t.Fatal("invalid raw JSON accepted")
		}
	}
	oversized := append(append([]byte{}, raw...), []byte(strings.Repeat(" ", maxDebugSnapshotBytes))...)
	if validDebugSnapshotVersion(oversized, "page-one", 4) {
		t.Fatal("oversized snapshot accepted")
	}
}
func TestDebugV4NullableEvidenceAndEnumParity(t *testing.T) {
	for _, detail := range []string{"manifest_load_error", "manifest_load_timeout", "level_load_error", "level_load_timeout", "level_empty_error", "frag_load_error", "frag_load_timeout", "frag_parsing_error", "buffer_append_error", "buffer_stalled_error", "buffer_nudge_on_stall", "buffer_seek_over_hole", "other", "buffer_full_error", "internal_exception", "buffer_append_no_progress", "buffer_add_codec_error"} {
		body := debugV4Fixture(t)
		e := debugV4Errors(debugV4Lane(body))
		e["firstFatal"] = nil
		latest := e["latest"].(map[string]any)
		latest["detail"] = detail
		latest["elapsedMs"] = nil
		latest["fatal"] = false
		raw, _ := json.Marshal(body["data"])
		if !validDebugSnapshotVersion(raw, "page-one", 4) {
			t.Fatalf("safe detail rejected: %s", detail)
		}
	}
	body := debugV4Fixture(t)
	e := debugV4Errors(debugV4Lane(body))
	e["firstFatal"], e["latest"] = nil, nil
	e["observed"], e["dropped"] = 5, 5
	raw, _ := json.Marshal(body["data"])
	if !validDebugSnapshotVersion(raw, "page-one", 4) {
		t.Fatal("unavailable slots rejected")
	}
}
func TestDebugV4VersionAndNegativeResultBinding(t *testing.T) {
	for _, section := range []string{"doctor", "snapshot", "operation", "native_playback"} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"inspect","params":{"version":4,"runtime":"page-one","section":"`+section+`"}}`, nil), 400)
	}
	for _, status := range []string{"unsupported", "rejected"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"inspect","params":{"version":4,"runtime":"page-one","section":"debug"}}`)
		body := map[string]any{"version": 3, "runtime": "page-one", "section": "debug", "error": "unavailable"}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, body), nil), 400)
		body["version"] = 4
		body["runtime"] = "other"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, body), nil), 400)
		body["runtime"] = "page-one"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, body), nil), 200)
	}
}

func TestDebugV4SameSequenceIdentityAndSaturation(t *testing.T) {
	for _, field := range []string{"elapsedMs", "engine", "type", "detail", "fatal", "httpStatus", "mediaErrorCode"} {
		t.Run(field, func(t *testing.T) {
			body := debugV4Fixture(t)
			e := debugV4Errors(debugV4Lane(body))
			changed := map[string]any{"elapsedMs": 1201, "engine": "html5", "type": "network", "detail": "other", "fatal": false, "httpStatus": 200, "mediaErrorCode": 3}
			e["latest"].(map[string]any)[field] = changed[field]
			raw, _ := json.Marshal(body["data"])
			if validDebugSnapshotVersion(raw, "page-one", 4) {
				t.Fatal("one unsaturated sequence described conflicting observations")
			}
			e["observed"] = inspectMaxInteger
			e["firstFatal"].(map[string]any)["sequence"] = inspectMaxInteger
			e["latest"].(map[string]any)["sequence"] = inspectMaxInteger
			raw, _ = json.Marshal(body["data"])
			if !validDebugSnapshotVersion(raw, "page-one", 4) {
				t.Fatal("saturated sequence could not retain distinct observations")
			}
		})
	}
	raw := json.RawMessage(`{"observed":1,"dropped":0,"firstFatal":{"sequence":1,"elapsedMs":1,"engine":"hls.js","type":"media","detail":"other","fatal":true,"httpStatus":null,"mediaErrorCode":null},"latest":{"mediaErrorCode":null,"httpStatus":null,"fatal":true,"detail":"other","type":"media","engine":"hls.js","elapsedMs":1.0,"sequence":1}}`)
	if !validDebugAttemptErrors(raw) {
		t.Fatal("semantic identity rejected key order or equivalent numeric spelling")
	}
}
