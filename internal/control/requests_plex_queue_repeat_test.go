package control

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func plexQueueRepeatContract(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/plex-queue-repeat-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	value, err := decodeObject(raw)
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func TestPlexQueueRepeatSharedContractAndRequestBoundary(t *testing.T) {
	c := plexQueueRepeatContract(t)
	if string(c["capability_name"]) != `"plex_queue_repeat"` ||
		!validPlexQueueRepeatCapability(c["capability"], json.RawMessage(plexQueueV2BaseCapability())) ||
		!config.ValidCommandAction("plex_queue.repeat") {
		t.Fatal("repeat contract or operator permission is missing")
	}
	for _, suffix := range []string{"", "_v2"} {
		params, err := decodeObject(c["repeat_request"+suffix])
		bound := parsePlexQueueRequest(params)
		if err != nil || bound == nil || !validPlexQueueResult(c["repeat_response"+suffix], bound) {
			t.Fatal("repeat request/response contract rejected", suffix)
		}
	}
	// The additive feature must not expand either frozen capability's operations.
	base, _ := decodeObject(json.RawMessage(plexQueueV2BaseCapability()))
	state, _ := decodeObject(plexQueueV2Contract(t)["capability"])
	for _, pair := range [][2]json.RawMessage{{base["operations"], c["unchanged_base_operations"]}, {state["operations"], c["unchanged_v2_operations"]}} {
		var actual, expected []string
		if json.Unmarshal(pair[0], &actual) != nil || json.Unmarshal(pair[1], &expected) != nil || !reflect.DeepEqual(actual, expected) {
			t.Fatal("repeat changed a frozen operations array")
		}
	}
	for _, fields := range []string{
		`"repeat":"one"`, `"repeat":"on"`, `"repeat":true`, `"repeat":null`, `"repeat":[]`,
		`"repeat":"all","repeat":"none"`, `"repeat":"all","ids":["42"]`,
		`"repeat":"all","save":true`, `"repeat":"all","state_version":1`,
		`"repeat":"all","state_version":2.0`, `"repeat":"all","state_version":true`,
	} {
		s := newTestServer(t)
		body := `{"action":"plex_queue","params":{"runtime":"page-123","op":"repeat",` + fields + `}}`
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid repeat mutation was queued", fields)
		}
	}
}

func TestPlexQueueRepeatProbeAndCapabilityAreIndependent(t *testing.T) {
	c := plexQueueRepeatContract(t)
	for _, includeMarker := range []bool{false, true} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{"plex_queue_repeat":true,"plex_queue_state_v2":true,"local_media_handles":true,"local_media_session":true,"local_media_progressive":true}}`)
		forwarded, err := decodeObject(s.devices[0].queue[0].data)
		if err != nil || string(forwarded["params"]) != `{}` {
			t.Fatal("controller probe flags reached the player")
		}
		value := nativeCompositionCapabilities(t)
		if includeMarker {
			value["plex_queue_repeat"] = c["capability"]
		}
		response := screenshotEnvelope(id, "ok", value)
		expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		expect(t, got, 200)
		if got.Body.String() != response {
			t.Fatal("controller invented or altered a repeat capability")
		}
	}
	for _, raw := range []string{`false`, `null`, `1`, `"true"`, `[]`, `{}`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":{"plex_queue_repeat":`+raw+`}}`, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("non-true repeat probe queued work")
		}
	}
	for _, bad := range []string{
		`null`, `{}`, `{"version":true,"modes":["none","all"]}`, `{"version":1.0,"modes":["none","all"]}`,
		`{"version":1,"modes":["all","all"]}`, `{"version":1,"modes":["none","one"]}`,
		`{"version":1,"modes":["none","all"],"extra":true}`,
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{"plex_queue_repeat":true}}`)
		value := map[string]json.RawMessage{
			"player": json.RawMessage(`{"runtime":"page-123"}`), "plex_queue": json.RawMessage(plexQueueV2BaseCapability()),
			"plex_queue_repeat": json.RawMessage(bad),
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("malformed repeat marker consumed pending discovery")
		}
		value["plex_queue_repeat"] = c["capability"]
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 200)
	}
	if validPlexQueueRepeatCapability(c["capability"], nil) ||
		validPlexQueueRepeatCapability(c["capability"], json.RawMessage(`{"version":1,"operations":["play"],"max_items":500}`)) {
		t.Fatal("repeat marker accepted without base status support")
	}
}

func TestPlexQueueRepeatCorrelationSurvivesCachedResult(t *testing.T) {
	for _, version := range []int{1, 2} {
		for _, mode := range []string{"none", "all"} {
			t.Run(fmt.Sprintf("v%d_%s", version, mode), func(t *testing.T) {
				s := newTestServer(t)
				params := map[string]any{"runtime": "page-123", "op": "repeat", "repeat": mode}
				if version == 2 {
					params["state_version"] = 2
				}
				body, _ := json.Marshal(map[string]any{"action": "plex_queue", "params": params})
				id := rpcID(t, s, string(body))
				if bound := s.devices[0].queue[0].plexQueue; bound == nil || bound.Repeat != mode || bound.StateVersion != version {
					t.Fatal("repeat descriptor lost request mode/version")
				}
				for _, change := range []map[string]any{
					{"repeat": map[string]string{"none": "all", "all": "none"}[mode]},
					{"runtime": "other-page"}, {"version": 3 - version},
					{"active": false, "state": "idle", "ids": []string{}, "index": nil},
				} {
					value := plexQueueV2State(version, 3)
					value["repeat"] = mode
					for key, item := range change {
						value[key] = item
					}
					expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 400)
					if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
						t.Fatal("unrelated repeat result consumed pending mutation")
					}
				}
				value := plexQueueV2State(version, 3)
				value["repeat"], value["state"] = mode, "paused"
				response := screenshotEnvelope(id, "ok", value)
				expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
				if bound := s.devices[0].results[id].plexQueue; bound == nil || bound.Repeat != mode || bound.StateVersion != version {
					t.Fatal("cached result lost repeat correlation")
				}
				value["repeat"] = map[string]string{"none": "all", "all": "none"}[mode]
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 400)
				got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
				expect(t, got, 200)
				if got.Body.String() != response {
					t.Fatal("invalid replay replaced the repeat receipt")
				}
			})
		}
	}
}

func TestPlexQueueRepeatRefusalsKeepClosedErrorContract(t *testing.T) {
	c := plexQueueRepeatContract(t)
	var refusals map[string]string
	if err := json.Unmarshal(c["refusals"], &refusals); err != nil {
		t.Fatal(err)
	}
	var common map[string]string
	if err := json.Unmarshal(c["common_fence_refusals"], &common); err != nil {
		t.Fatal(err)
	}
	for name, message := range common {
		refusals["common_"+name] = message
	}
	contract, err := decodeObject(c["refusal_contract"])
	if err != nil || !profileInteger(contract["max_error_utf8_bytes"], maxPlexQueueRepeatErrorBytes, maxPlexQueueRepeatErrorBytes) {
		t.Fatal("repeat error bound differs from the shared contract")
	}
	refusals["unknown-private-text"] = "private fixture https://private.example/?token=secret"
	for _, version := range []int{1, 2} {
		bound := &plexQueueRequest{Runtime: "page-123", Op: "repeat", Repeat: "all", StateVersion: version}
		for name, message := range refusals {
			raw, _ := json.Marshal(map[string]string{"error": message})
			for _, status := range []string{"rejected", "unsupported"} {
				if !validPlexQueueResponse(raw, status, bound) {
					t.Fatal("bounded repeat refusal rejected", name, version, status)
				}
			}
		}
		for _, raw := range []string{
			`{}`, `{"error":""}`, `{"error":null}`, `{"error":true}`, `{"error":123}`, `{"error":[]}`, `{"error":{}}`,
			`{"error":"Plex queue is empty.","token":"private"}`, `{"error":"Plex queue is empty.","error":"Plex queue is empty."}`,
			`{"error":"line\nfeed"}`, `{"error":"\u007f"}`, `{"error":"\ud800"}`, `{"error":"\udc00"}`,
			`{"error":"` + string([]byte{0xff}) + `"}`,
		} {
			if validPlexQueueResponse(json.RawMessage(raw), "rejected", bound) {
				t.Fatal("malformed repeat refusal accepted")
			}
		}
		for _, message := range []string{strings.Repeat("a", 1024), strings.Repeat("a", 1025), strings.Repeat("é", 512), strings.Repeat("é", 513)} {
			raw, _ := json.Marshal(map[string]string{"error": message})
			if validPlexQueueResponse(raw, "rejected", bound) != (len(message) <= 1024) {
				t.Fatal("repeat error UTF-8 byte boundary changed", len(message))
			}
		}
		_, _, maximum := plexQueueStateLimits(bound)
		for _, extra := range []int{0, 1} {
			raw := []byte(`{"error":"bounded"}`)
			raw = append(raw[:len(raw)-1], []byte(strings.Repeat(" ", maximum-len(raw)+extra)+"}")...)
			if validPlexQueueResponse(raw, "rejected", bound) != (extra == 0) {
				t.Fatal("repeat refusal raw-data boundary changed", version, extra)
			}
		}
	}
}

func TestPlexQueueRepeatCommonRefusalPendingAndCachedRoundTrip(t *testing.T) {
	for _, version := range []int{1, 2} {
		for _, status := range []string{"rejected", "unsupported"} {
			for _, message := range []string{"This action is restricted by kiosk policy.", "private fixture https://private.example/?token=secret"} {
				s := newTestServer(t)
				params := map[string]any{"runtime": "page-123", "op": "repeat", "repeat": "all"}
				if version == 2 {
					params["state_version"] = 2
				}
				body, _ := json.Marshal(map[string]any{"action": "plex_queue", "params": params})
				id := rpcID(t, s, string(body))
				malformed := screenshotEnvelope(id, status, map[string]any{"error": message, "extra": true})
				expect(t, request(s, "POST", "/api/responses", firstToken, malformed, nil), 400)
				if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
					t.Fatal("malformed refusal consumed the pending repeat request")
				}
				response := screenshotEnvelope(id, status, map[string]string{"error": message})
				expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
				expect(t, request(s, "POST", "/api/responses", firstToken, malformed, nil), 400)
				bound := s.devices[0].results[id].plexQueue
				if bound == nil || bound.Repeat != "all" || bound.StateVersion != version {
					t.Fatal("refusal receipt lost repeat correlation")
				}
				got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
				expect(t, got, 200)
				if got.Body.String() != response || len(s.devices[0].queue) != 0 || s.bytes != 0 {
					t.Fatal("valid repeat refusal was not retained or invalid replay replaced it")
				}
			}
		}
	}
}
