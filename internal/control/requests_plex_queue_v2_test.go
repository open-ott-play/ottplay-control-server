package control

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
)

func plexQueueV2Contract(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/plex-queue-state-v2.json")
	if err != nil {
		t.Fatal(err)
	}
	value, err := decodeObject(raw)
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func plexQueueV2BaseCapability() string {
	return `{"version":1,"operations":["play","preview","status","next","previous","stop"],"max_items":500}`
}

func plexQueueV2State(version, count int) map[string]any {
	ids := make([]string, count)
	for i := range ids {
		// Intentionally repeat the largest supported ID, preserving occurrences.
		ids[i] = "12345678901234567890"
	}
	value := plexQueueFixture()
	value["version"], value["ids"] = version, ids
	if count == 0 {
		value["active"], value["state"], value["index"] = false, "idle", nil
	} else {
		value["index"] = count - 1
	}
	return value
}

func TestPlexQueueStateV2SharedContract(t *testing.T) {
	c := plexQueueV2Contract(t)
	if string(c["capability_name"]) != `"plex_queue_state_v2"` ||
		!validPlexQueueStateV2Capability(c["capability"], json.RawMessage(plexQueueV2BaseCapability())) ||
		!profileInteger(c["legacy_state_max_items"], maxPlexQueueItems, maxPlexQueueItems) ||
		!profileInteger(c["legacy_state_max_bytes"], maxPlexQueueResultBytes, maxPlexQueueResultBytes) ||
		!profileInteger(c["preview_and_library_version"], 1, 1) {
		t.Fatal("producer fixture differs from the negotiated contract")
	}
	params, err := decodeObject(c["status_request"])
	bound := parsePlexQueueRequest(params)
	if err != nil || bound == nil || bound.StateVersion != 2 || !validPlexQueueResult(c["idle_response"], bound) {
		t.Fatal("producer status/idle fixture rejected")
	}
	if validPlexQueueResult(c["idle_response"], nil) {
		t.Fatal("v2 state accepted without an explicit request context")
	}
}

func TestPlexQueueStateV2RequestOptInAndWriteLimit(t *testing.T) {
	for _, version := range []int{1, 2} {
		for _, op := range []string{"play", "preview", "status", "next", "previous", "stop"} {
			for _, count := range []int{500, 501} {
				params := map[string]any{"runtime": "page-123", "op": op}
				if version == 2 {
					params["state_version"] = 2
				}
				if op == "play" || op == "preview" {
					ids := make([]string, count)
					for i := range ids {
						ids[i] = "42"
					}
					params["ids"] = ids
				}
				body, _ := json.Marshal(map[string]any{"action": "plex_queue", "params": params})
				s := newTestServer(t)
				valid := !(version == 2 && op == "preview") && (count == 500 || (op != "play" && op != "preview"))
				code := 400
				if valid {
					code = 202
				}
				expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, string(body), nil), code)
				if valid {
					bound := s.devices[0].queue[0].plexQueue
					if bound == nil || bound.StateVersion != version {
						t.Fatal("request version not retained")
					}
				} else if len(s.devices[0].queue) != 0 || s.bytes != 0 {
					t.Fatal("invalid request was queued")
				}
			}
		}
	}
	for _, value := range []string{`null`, `true`, `false`, `"2"`, `1`, `3`, `2.0`, `2e0`, `[]`, `{}`} {
		s := newTestServer(t)
		body := `{"action":"plex_queue","params":{"runtime":"page-123","op":"status","state_version":` + value + `}}`
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid state version was queued", value)
		}
	}
	for _, body := range []string{
		`{"action":"plex_queue","params":{"runtime":"page-123","op":"status","state_version":2,"state_version":2}}`,
		`{"action":"plex_queue","params":{"runtime":"page-123","op":"status","state_version":2,"extra":true}}`,
		`{"action":"plex_library","params":{"runtime":"page-123","op":"list","state_version":2}}`,
		`{"action":"plex_library","params":{"runtime":"page-123","op":"play","library_id":"17","state_version":2}}`,
		`{"action":"playback","params":{"operation":"next_channel","state_version":2}}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("unsupported extension was queued")
		}
	}
}

func TestPlexQueueStateV2CountsAndOccurrenceIndex(t *testing.T) {
	for _, version := range []int{1, 2} {
		for _, count := range []int{0, 1, 500, 501, 1000, 1001} {
			value := plexQueueV2State(version, count)
			raw, _ := json.Marshal(value)
			bound := &plexQueueRequest{Runtime: "page-123", Op: "status", StateVersion: version}
			want := count <= 500 || (version == 2 && count <= 1000)
			if validPlexQueueResult(raw, bound) != want {
				t.Fatalf("version=%d count=%d expected valid=%v", version, count, want)
			}
			if want && count > 0 {
				value["index"] = count
				raw, _ = json.Marshal(value)
				if validPlexQueueResult(raw, bound) {
					t.Fatal("out-of-range duplicate occurrence index accepted")
				}
			}
		}
	}
}

func TestPlexQueueStateV2RawSubtreeByteBounds(t *testing.T) {
	for _, version := range []int{1, 2} {
		maximum := maxPlexQueueResultBytes
		if version == 2 {
			maximum = maxPlexQueueStateV2ResultBytes
		}
		for _, length := range []int{maximum, maximum + 1} {
			s := newTestServer(t)
			extension := ""
			if version == 2 {
				extension = `,"state_version":2`
			}
			id := rpcID(t, s, `{"action":"plex_queue","params":{"runtime":"page-123","op":"status"`+extension+`}}`)
			raw, _ := json.Marshal(plexQueueV2State(version, 1))
			// Whitespace inside the data object must count at the raw boundary.
			// Remarshalling this object would silently discard the excess bytes.
			data := string(raw[:len(raw)-1]) + strings.Repeat(" ", length-len(raw)) + "}"
			body := `{"id":"` + id + `","status":"ok","data":` + data + `}`
			code := 400
			if length == maximum {
				code = 200
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), code)
			if code == 400 && (len(s.devices[0].queue) != 1 || s.resultBytes != 0) {
				t.Fatal("oversized raw data was retained")
			}
		}
	}
}

func TestPlexQueueStateV2ProbeForwardingAndOriginalHash(t *testing.T) {
	probes := []string{`{}`, `{"plex_queue_state_v2":true}`, `{"local_media_session":true}`, `{"local_media_progressive":true}`, `{"plex_queue_state_v2":true,"local_media_session":true}`, `{"plex_queue_state_v2":true,"local_media_progressive":true}`, `{"plex_queue_state_v2":true,"local_media_progressive":true,"local_media_session":true}`}
	for _, params := range probes {
		t.Run(params, func(t *testing.T) {
			s, err := New(journalConfig(t))
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			body := `{"action":"capabilities","params":` + params + `,"idempotency_key":"plex-state-probe-1"}`
			id := rpcID(t, s, body)
			if rpcID(t, s, body) != id || len(s.devices[0].queue) != 1 {
				t.Fatal("identical probe was enqueued twice")
			}
			forwarded, err := decodeObject(s.devices[0].queue[0].data)
			if err != nil || string(forwarded["params"]) != `{}` {
				t.Fatal("relay-only probe flags reached the player")
			}
			for _, other := range probes {
				if other != params {
					conflict := `{"action":"capabilities","params":` + other + `,"idempotency_key":"plex-state-probe-1"}`
					expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, conflict, nil), 409)
				}
			}
			if len(s.devices[0].queue) != 1 {
				t.Fatal("conflicting probe changed the queue")
			}
			response := `{"id":"` + id + `","status":"ok","data":{"plex_queue":` + plexQueueV2BaseCapability() + `}}`
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response {
				t.Fatal("relay invented a v2 player capability")
			}
		})
	}
}

func TestPlexQueueStateV2MalformedProbesNeverQueue(t *testing.T) {
	for _, params := range []string{
		`{"plex_queue_state_v2":false}`, `{"plex_queue_state_v2":null}`, `{"plex_queue_state_v2":1}`,
		`{"plex_queue_state_v2":"true"}`, `{"plex_queue_state_v2":[]}`, `{"plex_queue_state_v2":{}}`,
		`{"plex_queue_state_v2":true,"unknown":true}`, `{"plex_queue_state_v2":true,"plex_queue_state_v2":true}`,
		`{"plex_queue_state_v2":true,"local_media_progressive":false}`, `{"plex_queue_state_v2":true,"local_media_session":null}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("malformed probe queued work", params)
		}
	}
}

func TestPlexQueueStateV2CapabilityIsExactAndRequiresBase(t *testing.T) {
	c := plexQueueV2Contract(t)
	marker := string(c["capability"])
	for _, field := range []string{"version", "max_write_items", "max_state_items", "max_state_bytes", "operations"} {
		for _, replacement := range []string{`null`, `true`, `"2"`, `2.0`, `2e0`, `0`, `[]`, `["play","status","next","previous","previous"]`, `["play","status","next","previous","stop","preview"]`} {
			value, _ := decodeObject(c["capability"])
			value[field] = json.RawMessage(replacement)
			raw, _ := json.Marshal(value)
			if validPlexQueueStateV2Capability(raw, json.RawMessage(plexQueueV2BaseCapability())) {
				t.Fatal("invalid v2 capability accepted", field, replacement)
			}
		}
	}
	for _, bad := range []string{`null`, `{}`, `[]`, marker[:len(marker)-1] + `,"version":2}`, marker[:len(marker)-1] + `,"extra":true}`} {
		if validPlexQueueStateV2Capability(json.RawMessage(bad), json.RawMessage(plexQueueV2BaseCapability())) {
			t.Fatal("malformed v2 capability accepted")
		}
	}
	for _, base := range []string{`null`, `{}`, `{"version":1,"operations":["status"],"max_items":500}`, strings.Replace(plexQueueV2BaseCapability(), `500`, `499`, 1), strings.Replace(plexQueueV2BaseCapability(), `500`, `501`, 1), strings.Replace(plexQueueV2BaseCapability(), `"preview"`, `"stop"`, 1)} {
		if validPlexQueueStateV2Capability(c["capability"], json.RawMessage(base)) {
			t.Fatal("v2 capability without valid base accepted", base)
		}
	}
	for _, probe := range []string{`{}`, `{"plex_queue_state_v2":true}`} {
		for _, runtime := range []string{`"page-123"`, `null`, `"invalid_runtime"`} {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"capabilities","params":`+probe+`}`)
			body := `{"id":"` + id + `","status":"ok","data":{"player":{"runtime":` + runtime + `},"plex_queue":` + plexQueueV2BaseCapability() + `,"plex_queue_state_v2":` + marker + `}}`
			code := 400
			if runtime == `"page-123"` {
				code = 200
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), code)
		}
	}
}

func TestPlexQueueStateV2VersionRuntimeAndDurableReceiptBinding(t *testing.T) {
	for _, version := range []int{1, 2} {
		t.Run(fmt.Sprintf("v%d", version), func(t *testing.T) {
			s := newTestServer(t)
			extension := ""
			if version == 2 {
				extension = `,"state_version":2`
			}
			id := rpcID(t, s, `{"action":"plex_queue","params":{"runtime":"page-123","op":"status"`+extension+`}}`)
			bad := plexQueueV2State(3-version, 1)
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", bad), nil), 400)
			bad["version"], bad["runtime"] = version, "different-page"
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", bad), nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("wrong version/runtime consumed pending work")
			}
			response := screenshotEnvelope(id, "ok", plexQueueV2State(version, 1))
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			bound := s.devices[0].results[id].plexQueue
			if bound == nil || bound.StateVersion != version {
				t.Fatal("durable receipt lost version context")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", bad), nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			if got.Body.String() != response {
				t.Fatal("invalid replay changed the stored receipt")
			}
		})
	}
}

func TestPlexQueueStateV2RejectsMalformedRawState(t *testing.T) {
	value, _ := json.Marshal(plexQueueV2State(2, 3))
	for _, data := range []string{
		strings.Replace(string(value), `"version":2`, `"version":2,"version":2`, 1),
		strings.Replace(string(value), `"version":2`, `"version":true`, 1),
		strings.Replace(string(value), `"version":2`, `"version":2.0`, 1),
		strings.Replace(string(value), `"version":2`, `"version":2e0`, 1),
		strings.Replace(string(value), `"index":2`, `"index":true`, 1),
		strings.Replace(string(value), `"index":2`, `"index":null`, 1),
		strings.Replace(string(value), `"active":true`, `"active":false`, 1),
		strings.Replace(string(value), `"12345678901234567890"`, `"0123"`, 1),
		strings.Replace(string(value), `"state":"playing"`, `"state":"idle"`, 1),
		string(value[:len(value)-1]) + `,"title":"bad\ud800"}`,
		string(value[:len(value)-1]) + `,"title":"bad\ntext"}`,
		string(value[:len(value)-1]) + `,"title":"bad` + string([]byte{0xff}) + `"}`,
		string(value[:len(value)-1]) + `,"title":"` + strings.Repeat("я", 257) + `"}`,
		string(value[:len(value)-1]) + `,"url":"private"}`,
		string(value[:len(value)-1]) + `,"error":"Plex queue is empty."}`,
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"plex_queue","params":{"runtime":"page-123","op":"status","state_version":2}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":`+data+`}`, nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("malformed state was retained")
		}
	}
}

func TestPlexQueueStateV2StateAndOperationInvariants(t *testing.T) {
	for _, state := range []string{"preparing", "playing", "paused", "ended", "error"} {
		value := plexQueueV2State(2, 3)
		value["state"] = state
		if state == "error" {
			value["error"] = "Plex queue is empty."
		}
		raw, _ := json.Marshal(value)
		bound := &plexQueueRequest{Runtime: "page-123", Op: "status", StateVersion: 2}
		if !validPlexQueueResult(raw, bound) {
			t.Fatal("valid state rejected", state)
		}
		bound.Op = "stop"
		if validPlexQueueResult(raw, bound) {
			t.Fatal("non-idle stop accepted")
		}
		if state == "ended" {
			value["index"] = 1
			raw, _ = json.Marshal(value)
			bound.Op = "status"
			if validPlexQueueResult(raw, bound) {
				t.Fatal("ended before the last occurrence accepted")
			}
		}
	}
	value := plexQueueV2State(2, 0)
	value["state"], value["error"] = "error", "Plex queue is empty."
	raw, _ := json.Marshal(value)
	if !validPlexQueueResult(raw, &plexQueueRequest{Runtime: "page-123", Op: "status", StateVersion: 2}) {
		t.Fatal("inactive error state rejected")
	}
	for _, mutation := range []string{"none", "order", "index"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"plex_queue","params":{"runtime":"page-123","op":"play","ids":["42","43","42"],"state_version":2}}`)
		value := plexQueueFixture()
		value["version"], value["ids"] = 2, []string{"42", "43", "42"}
		if mutation == "order" {
			value["ids"] = []string{"42", "42", "43"}
		}
		if mutation == "index" {
			value["index"] = 1
		}
		code := 400
		if mutation == "none" {
			code = 200
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), code)
	}
}

func TestPlexQueueStateV2KeepsPreviewLibraryAndGenericPlaybackV1(t *testing.T) {
	data, err := os.ReadFile("../../contracts/plex-queue-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	v1, err := decodeObject(data)
	if err != nil {
		t.Fatal(err)
	}
	preview := &plexQueueRequest{Runtime: "page-123", Op: "preview", IDs: []string{"78777", "78776", "78775"}}
	if !validPlexQueueResult(v1["preview_result"], preview) ||
		validPlexQueueResult(json.RawMessage(strings.Replace(string(v1["preview_result"]), `"version": 1`, `"version": 2`, 1)), preview) {
		t.Fatal("preview did not preserve version 1")
	}
	for _, version := range []int{1, 2} {
		queue := plexQueueFixture()
		queue["version"] = version
		generic, _ := json.Marshal(map[string]any{"operation": "next_channel", "dispatched": true, "plex_queue": queue})
		if validPlexPlaybackResult(generic, "next_channel") != (version == 1) {
			t.Fatal("generic playback queue receipt changed its version contract")
		}
		library, _ := json.Marshal(map[string]any{"version": 1, "runtime": "page-123", "op": "play", "state": "ready", "library": map[string]any{"id": "17", "title": "Example", "type": "show"}, "shuffled": false, "queue": queue})
		bound := &plexLibraryRequest{Runtime: "page-123", Op: "play", LibraryID: "17"}
		if validPlexLibraryResult(library, "ok", bound) != (version == 1) {
			t.Fatal("library nested queue changed its version contract")
		}
	}
}

func TestPlexQueueStateV2NegativeEnvelopeBoundsAndCompatibilityError(t *testing.T) {
	const upgrade = "This queue requires plex_queue_state_v2 and state_version: 2; upgrade the controller"
	message, _ := json.Marshal(upgrade)
	if validPlexQueueError(message) {
		t.Fatal("compatibility error leaked into the queue-state error vocabulary")
	}
	for _, version := range []int{1, 2} {
		bound := &plexQueueRequest{Runtime: "page-123", Op: "status", StateVersion: version}
		if !validPlexQueueResponse(json.RawMessage(`{"error":`+string(message)+`}`), "rejected", bound) {
			t.Fatal("fixed compatibility rejection refused")
		}
	}
	bound := &plexQueueRequest{Runtime: "page-123", Op: "status", StateVersion: 2}
	for _, data := range []string{
		`{"error":"one","error":"two"}`,
		`{"details":{"error":"one","error":"two"}}`,
		`{"error":"` + strings.Repeat("x", maxPlexQueueStateV2ResultBytes) + `"}`,
	} {
		if validPlexQueueResponse(json.RawMessage(data), "rejected", bound) {
			t.Fatal("unbounded or duplicate-key negative v2 reply accepted")
		}
	}
}

func TestPlexQueueStateV2DeferredPlayAndV1StatusRemainIndependent(t *testing.T) {
	s := newTestServer(t)
	v2 := rpcID(t, s, `{"action":"plex_queue","params":{"runtime":"page-123","op":"play","ids":["78777","78776","78775"],"state_version":2}}`)
	v1 := rpcID(t, s, `{"action":"plex_queue","params":{"runtime":"page-123","op":"status"}}`)
	first := screenshotEnvelope(v1, "ok", plexQueueFixture())
	expect(t, request(s, "POST", "/api/responses", firstToken, first, nil), 200)
	value := plexQueueFixture()
	value["version"] = 2
	second := screenshotEnvelope(v2, "ok", value)
	expect(t, request(s, "POST", "/api/responses", firstToken, second, nil), 200)
	if len(s.devices[0].queue) != 0 || s.devices[0].results[v1].plexQueue.StateVersion != 1 || s.devices[0].results[v2].plexQueue.StateVersion != 2 {
		t.Fatal("interleaved reads changed the deferred play format")
	}
}
