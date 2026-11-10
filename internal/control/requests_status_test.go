package control

import (
	"encoding/json"
	"testing"
)

func TestStatusDiagnosticVersionRoundTrip(t *testing.T) {
	for _, tc := range []struct {
		name    string
		params  string
		version string
	}{
		{"legacy", `{}`, "2"},
		{"explicit_v2", `{"playback_failure_version":2}`, "2"},
		{"explicit_v3", `{"playback_failure_version":3}`, "3"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"status","params":`+tc.params+`}`)
			poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
			expect(t, poll, 200)
			var delivery struct {
				Requests []struct {
					ID     string          `json:"id"`
					Action string          `json:"action"`
					Params json.RawMessage `json:"params"`
				} `json:"requests"`
			}
			if err := json.Unmarshal(poll.Body.Bytes(), &delivery); err != nil {
				t.Fatal(err)
			}
			if len(delivery.Requests) != 1 || delivery.Requests[0].ID != id ||
				delivery.Requests[0].Action != "status" || string(delivery.Requests[0].Params) != tc.params {
				t.Fatalf("status request changed in transit: %s", poll.Body.String())
			}
			// The relay must preserve the player's bounded diagnostic result,
			// including wide integers and fields it does not itself interpret.
			data := `{"playback_failure":{"version":` + tc.version + `,"play_generation":18446744073709551615,"context":{"pipeline_error":2147483647},"future_detail":{"value":-2147483648}}}`
			body := `{"id":"` + id + `","status":"ok","data":` + data + `}`
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			for _, wait := range []string{"", "&wait_ms=100"} {
				result := request(s, "GET", "/api/requests?device_id=first&id="+id+wait, adminToken, "", nil)
				expect(t, result, 200)
				if result.Body.String() != body {
					t.Fatal("status result changed diagnostic JSON")
				}
			}
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("completed status request remained queued")
			}
		})
	}
}

func TestStatusDiagnosticV3PreservesHLSOutcomes(t *testing.T) {
	for _, tc := range []struct {
		name    string
		outcome string
	}{
		{"transport", `"transport"`},
		{"server_state", `"server_state"`},
		{"refused", `"refused"`},
		{"inconclusive", `"inconclusive"`},
		{"deadline", `"deadline"`},
		{"none", `null`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"status","params":{"playback_failure_version":3}}`)
			// Status is opaque to the relay. Preserve the player's HLS outcome,
			// wide generation and whitespace without interpreting or rewriting them.
			data := `{ "playback_failure":{"version":3,"play_generation":18446744073709551615,"code":"playback_interrupted","context":{"demux_exit":"none","pipeline_error":0,"io_failure":{"hls":{"exit":"other","outcome":` + tc.outcome + `}}}} }`
			body := `{"id":"` + id + `","status":"ok","data":` + data + `}`
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			for _, wait := range []string{"", "&wait_ms=100"} {
				result := request(s, "GET", "/api/requests?device_id=first&id="+id+wait, adminToken, "", nil)
				expect(t, result, 200)
				if result.Body.String() != body || result.Header().Get("Content-Type") != "application/json" {
					t.Fatal("status receipt changed HLS diagnostic JSON")
				}
			}
			if len(s.devices[0].queue) != 0 || s.bytes != 0 || s.resultBytes != len(body) {
				t.Fatal("completed HLS status request has invalid queue or receipt accounting")
			}
		})
	}
}

func TestStatusDiagnosticVersionRejectsInvalidParams(t *testing.T) {
	for _, params := range []string{
		`null`, `[]`, `3`,
		`{"playback_failure_version":null}`, `{"playback_failure_version":true}`,
		`{"playback_failure_version":false}`, `{"playback_failure_version":1}`,
		`{"playback_failure_version":4}`, `{"playback_failure_version":-3}`,
		`{"playback_failure_version":3.0}`, `{"playback_failure_version":2.0}`,
		`{"playback_failure_version":3e0}`, `{"playback_failure_version":"3"}`,
		`{"playback_failure_version":"2"}`, `{"playback_failure_version":[]}`,
		`{"playback_failure_version":{}}`, `{"version":3}`,
		`{"playback_failure_version":3,"extra":true}`,
		`{"playback_failure_version":3,"playback_failure_version":3}`,
		`{"playback_failure_version":2,"playback_failure_version":3}`,
	} {
		t.Run(params, func(t *testing.T) {
			s := newTestServer(t)
			body := `{"action":"status","params":` + params + `}`
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 || len(s.devices[0].results) != 0 {
				t.Fatal("invalid status params created queued work or a result")
			}
		})
	}
}

func TestStatusDiagnosticVersionDoesNotExpandOtherReadActions(t *testing.T) {
	for _, action := range []string{"providers", "profiles", "epg_catalog"} {
		t.Run(action, func(t *testing.T) {
			s := newTestServer(t)
			body := `{"action":"` + action + `","params":{"playback_failure_version":3}}`
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("status diagnostic parameter expanded another action")
			}
		})
	}
}
