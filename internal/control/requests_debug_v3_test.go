package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func debugV3Fixture(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/runtime-debug-v3.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]any
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture["response"].(map[string]any)
}

func TestDebugV3ExactRoundtripAndLegacyIsolation(t *testing.T) {
	for _, empty := range []bool{false, true} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"inspect","params":{"version":3,"runtime":"page-one","section":"debug"}}`)
		body := debugV3Fixture(t)
		if empty {
			lane := body["data"].(map[string]any)["media"].([]any)[0].(map[string]any)
			lane["attempt"], lane["lastAttempt"] = nil, nil
		}
		result := screenshotEnvelope(id, "ok", body)
		expect(t, request(s, "POST", "/api/responses", firstToken, result, nil), 200)
		response := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		expect(t, response, 200)
		if response.Body.String() != result || s.bytes != 0 {
			t.Fatal("v3 result altered or stayed queued")
		}
	}
	for _, version := range []int{1, 2} {
		body := debugV3Fixture(t)["data"].(map[string]any)
		body["version"] = version
		raw, _ := json.Marshal(body)
		if validDebugSnapshotVersion(raw, "page-one", version) {
			t.Fatal("legacy version accepted v3 fields")
		}
	}
}

func TestDebugV3RejectsInvalidAttemptsBeforeConsumingRequest(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"missing attempt": func(l map[string]any) { delete(l, "attempt") },
		"missing retired": func(l map[string]any) { delete(l, "lastAttempt") },
		"private field": func(l map[string]any) {
			l["attempt"].(map[string]any)["url"] = "https://private.invalid/secret"
		},
		"missing counter":       func(l map[string]any) { delete(l["attempt"].(map[string]any), "attachment") },
		"private reason":        func(l map[string]any) { l["lastAttempt"].(map[string]any)["reason"] = "secret" },
		"null handle":           func(l map[string]any) { l["lastAttempt"].(map[string]any)["handleId"] = nil },
		"fractional generation": func(l map[string]any) { l["lastAttempt"].(map[string]any)["generation"] = 0.5 },
	}
	for _, field := range []string{"attachment", "engineStarts", "fatalErrors", "nativeFallbacks", "streamRetries"} {
		for _, bad := range []any{true, nil, -1, 0.5, "1", json.Number("9007199254740992")} {
			field, bad := field, bad
			label, _ := json.Marshal(bad)
			mutations[field+string(label)] = func(l map[string]any) { l["attempt"].(map[string]any)[field] = bad }
		}
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"inspect","params":{"version":3,"runtime":"page-one","section":"debug"}}`)
			body := debugV3Fixture(t)
			mutate(body["data"].(map[string]any)["media"].([]any)[0].(map[string]any))
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", body), nil), 400)
			if s.resultBytes != 0 || len(s.devices[0].queue) != 1 {
				t.Fatal("invalid v3 result consumed request")
			}
		})
	}
	raw, _ := json.Marshal(debugV3Fixture(t)["data"])
	for _, bad := range []string{`"engineStarts":1,"engineStarts":2`, `"engineStarts":1e999`} {
		changed := strings.Replace(string(raw), `"engineStarts":3`, bad, 1)
		if validDebugSnapshotVersion([]byte(changed), "page-one", 3) {
			t.Fatal("invalid JSON counter accepted")
		}
	}
}

func TestDebugV3VersionAndNegativeResultBinding(t *testing.T) {
	for _, section := range []string{"doctor", "snapshot", "operation"} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken,
			`{"action":"inspect","params":{"version":3,"runtime":"page-one","section":"`+section+`"}}`, nil), 400)
	}
	for _, status := range []string{"unsupported", "rejected"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"inspect","params":{"version":3,"runtime":"page-one","section":"debug"}}`)
		body := map[string]any{"version": 2, "runtime": "page-one", "section": "debug", "error": "unavailable"}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, body), nil), 400)
		body["version"] = 3
		body["runtime"] = "other-runtime"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, body), nil), 400)
		body["runtime"] = "page-one"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, body), nil), 200)
	}
}
