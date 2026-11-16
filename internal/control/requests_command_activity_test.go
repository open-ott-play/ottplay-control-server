package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

const activityRequest = `{"action":"command_activity_diagnostics","params":{"version":2,"runtime":"native_123"}}`

func activityData() map[string]any {
	return map[string]any{"version": 2, "runtime": "native_123", "scope": "remote_ingress", "entries": []any{map[string]any{"sequence": 1, "action": "plex_library", "age_ms": 0, "result": "ok", "request_id": strings.Repeat("1", 32)}}}
}
func TestCommandActivityRelayBoundAndStrict(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	id := rpcID(t, s, activityRequest)
	expected := &commandActivityRequest{Runtime: "native_123"}
	if bound := s.devices[0].queue[0].commandActivity; bound == nil || *bound != *expected {
		t.Fatal("lost binding")
	}
	body := screenshotEnvelope(id, "ok", activityData())
	expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	out := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	if out.Code != 200 || out.Body.String() != body {
		t.Fatal("wire result changed")
	}
	bad := activityData()
	bad["runtime"] = "other"
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", bad), nil), 400)
	if s.devices[0].results[id].commandActivity == nil {
		t.Fatal("receipt lost binding")
	}
	if len(s.requestAudit) != 0 {
		t.Fatal("activity diagnosis logged as action")
	}
}
func TestCommandActivityRequestCapabilityAndNegativeStrictness(t *testing.T) {
	for _, bad := range []string{`{}`, `null`, `{"version":1,"runtime":"r"}`, `{"version":2.0,"runtime":"r"}`, `{"version":2,"runtime":"r","runtime":"r"}`, `{"version":2,"runtime":"r/a"}`, `{"version":2,"runtime":"r","op":"play"}`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"command_activity_diagnostics","params":`+bad+`}`, nil), 400)
	}
	for _, marker := range []string{`{"version":2}`, `{"version":1}`, `{"version":2.0}`, `{"version":2,"version":2}`, `{"version":2,"url":"secret"}`, `null`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := `{"id":"` + id + `","status":"ok","data":{"player":{"runtime":"native_123"},"command_activity_diagnostics":` + marker + `}}`
		want := 400
		if marker == `{"version":2}` {
			want = 200
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), want)
	}
	expected := &commandActivityRequest{Runtime: "native_123"}
	for _, reason := range []string{"invalid_request", "runtime_mismatch", "unavailable", "policy_denied"} {
		runtime := "native_123"
		if reason == "runtime_mismatch" {
			runtime = "receiver_current"
		}
		data := map[string]any{"version": 2, "runtime": runtime, "error": reason}
		if !validCommandActivityResult(transferJSON(t, data), "rejected", expected) {
			t.Fatal("negative enum refused")
		}
		if validCommandActivityResult(transferJSON(t, data), "unsupported", expected) {
			t.Fatal("unsupported top level accepted")
		}
	}
	for _, data := range []map[string]any{{"version": 2, "runtime": "other", "error": "unavailable"}, {"version": 2, "runtime": "native_123", "error": "secret"}, {"version": 2, "runtime": "a/b", "error": "runtime_mismatch"}} {
		if validCommandActivityResult(transferJSON(t, data), "rejected", expected) {
			t.Fatal("unbound or private negative accepted")
		}
	}
}
func TestCommandActivityMalformedSuccessNeverConsumes(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"runtime": func(m map[string]any) { m["runtime"] = "other" }, "version": func(m map[string]any) { m["version"] = 1 }, "scope": func(m map[string]any) { m["scope"] = "all" }, "secret": func(m map[string]any) { m["url"] = "fixture-secret" },
		"sequence0": func(m map[string]any) { m["entries"].([]any)[0].(map[string]any)["sequence"] = 0 }, "sequencefraction": func(m map[string]any) { m["entries"].([]any)[0].(map[string]any)["sequence"] = json.Number("1.0") },
		"action": func(m map[string]any) { m["entries"].([]any)[0].(map[string]any)["action"] = "status" }, "operation": func(m map[string]any) { m["entries"].([]any)[0].(map[string]any)["action"] = "plex_library.play" },
		"id": func(m map[string]any) { m["entries"].([]any)[0].(map[string]any)["request_id"] = "fixture-secret" }, "iduppercase": func(m map[string]any) {
			m["entries"].([]any)[0].(map[string]any)["request_id"] = strings.Repeat("A", 32)
		},
		"age": func(m map[string]any) {
			m["entries"].([]any)[0].(map[string]any)["age_ms"] = json.Number("9007199254740992")
		}, "privateResult": func(m map[string]any) { m["entries"].([]any)[0].(map[string]any)["result"] = "fixture-secret" },
		"extra": func(m map[string]any) { m["entries"].([]any)[0].(map[string]any)["params"] = "fixture-secret" },

		"tooMany": func(m map[string]any) {
			var rows []any
			for i := 0; i < 9; i++ {
				rows = append(rows, map[string]any{"sequence": i + 1, "action": "play", "age_ms": 0, "result": "pending", "request_id": nil})
			}
			m["entries"] = rows
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, activityRequest)
			data := activityData()
			mutate(data)
			out := request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil)
			expect(t, out, 400)
			if len(s.devices[0].results) != 0 || len(s.devices[0].queue) != 1 || strings.Contains(out.Body.String(), "fixture-secret") {
				t.Fatal("invalid reply consumed or leaked")
			}
		})
	}
	expected := &commandActivityRequest{Runtime: "native_123"}
	for _, raw := range []string{`{"version":2,"runtime":"native_123","scope":"remote_ingress","entries":[],"version":2}`, `{"version":2,"runtime":"native_123","scope":"remote_ingress","entries":[{"sequence":1,"action":"play","age_ms":0,"result":"ok","request_id":null,"request_id":null}]}`} {
		// The HTTP relay applies recursive duplicate rejection in addition to shape validation.
		s := newTestServer(t)
		id := rpcID(t, s, activityRequest)
		expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":`+raw+`}`, nil), 400)
	}
	data := activityData()
	row := data["entries"].([]any)[0].(map[string]any)
	row["sequence"] = json.Number("9007199254740991")
	row["age_ms"] = json.Number("9007199254740991")
	row["request_id"] = nil
	if !validCommandActivityResult(transferJSON(t, data), "ok", expected) {
		t.Fatal("nullable legacy id/max ints refused")
	}
	data["entries"] = []any{}
	if !validCommandActivityResult(transferJSON(t, data), "ok", expected) {
		t.Fatal("empty history refused")
	}
}
func TestCommandActivitySharedContract(t *testing.T) {
	raw, err := os.ReadFile("../../contracts/command-activity-v2.json")
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Version                  int                        `json:"version"`
		Synthetic                bool                       `json:"synthetic"`
		Action                   string                     `json:"action"`
		Capability               map[string]json.RawMessage `json:"capability"`
		Request                  json.RawMessage            `json:"request"`
		Actions, Results, Errors []string
		Limits                   map[string]int64 `json:"limits"`
		Example                  json.RawMessage  `json:"example"`
		Legacy                   json.RawMessage  `json:"legacy_example"`
		Clamped                  json.RawMessage  `json:"clamped_example"`
	}
	if json.Unmarshal(raw, &f) != nil || f.Version != 2 || !f.Synthetic || f.Action != "command_activity_diagnostics" || len(f.Actions) != 13 || len(f.Results) != 5 || len(f.Errors) != 4 || f.Limits["receipt_bytes"] != maxCommandActivityBytes || f.Limits["entries"] != 8 || f.Limits["integer"] != inspectMaxInteger {
		t.Fatal("incomplete shared fixture")
	}
	expected := parseCommandActivityRequest(transferObject(t, f.Request))
	if expected == nil || !validCommandActivityCapability(f.Capability[f.Action]) || !validCommandActivityResult(f.Example, "ok", expected) || !validCommandActivityResult(f.Legacy, "ok", expected) || !validCommandActivityResult(f.Clamped, "ok", expected) {
		t.Fatal("shared contract refused")
	}
	for field, values := range map[string][]string{"action": f.Actions, "result": f.Results} {
		for _, v := range values {
			var data map[string]any
			_ = json.Unmarshal(f.Example, &data)
			data["entries"].([]any)[0].(map[string]any)[field] = v
			if !validCommandActivityResult(transferJSON(t, data), "ok", expected) {
				t.Fatalf("shared %s %s refused", field, v)
			}
		}
	}
	for _, reason := range f.Errors {
		if !validCommandActivityResult(transferJSON(t, map[string]any{"version": 2, "runtime": expected.Runtime, "error": reason}), "rejected", expected) {
			t.Fatal("shared negative refused")
		}
	}
}

func TestCommandActivityClampedSequencesRemainValid(t *testing.T) {
	expected := &commandActivityRequest{Runtime: "native_123"}
	data := activityData()
	data["entries"] = []any{
		map[string]any{"sequence": json.Number("9007199254740991"), "action": "plex_library", "age_ms": 1000, "result": "ok", "request_id": strings.Repeat("1", 32)},
		map[string]any{"sequence": json.Number("9007199254740991"), "action": "playback", "age_ms": 0, "result": "pending", "request_id": strings.Repeat("2", 32)},
	}
	if !validCommandActivityResult(transferJSON(t, data), "ok", expected) {
		t.Fatal("successive saturated sequence samples refused")
	}
	// Sequence is bounded telemetry, not an identity or request-order fence.
	data["entries"].([]any)[1].(map[string]any)["sequence"] = 1
	if !validCommandActivityResult(transferJSON(t, data), "ok", expected) {
		t.Fatal("sequence used as an invented order fence")
	}
}
