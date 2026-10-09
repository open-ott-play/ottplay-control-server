package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"
)

func TestPlexQueueSharedContract(t *testing.T) {
	data, err := os.ReadFile("../../contracts/plex-queue-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Errors   []string        `json:"errors"`
		MaxItems int             `json:"max_items"`
		Queue    json.RawMessage `json:"queue_result"`
		Ended    json.RawMessage `json:"ended_result"`
		Stop     json.RawMessage `json:"stop_result"`
		Preview  json.RawMessage `json:"preview_result"`
	}
	if err = json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Errors) != len(plexQueueErrors) || fixture.MaxItems != maxPlexQueueItems {
		t.Fatal("contract constants differ")
	}
	for _, value := range fixture.Errors {
		if !plexQueueErrors[value] {
			t.Fatalf("unknown contract error %q", value)
		}
	}
	for _, value := range []json.RawMessage{fixture.Queue, fixture.Ended, fixture.Stop} {
		if !validPlexQueueResult(value, nil) {
			t.Fatal("contract queue example rejected")
		}
	}
	if !validPlexQueueResult(fixture.Preview, &plexQueueRequest{Runtime: "page-123", Op: "preview", IDs: []string{"78777", "78776", "78775"}}) {
		t.Fatal("contract preview example rejected")
	}
}

func plexQueueFixture() map[string]any {
	return map[string]any{"version": 1, "runtime": "page-123", "active": true, "state": "playing", "ids": []string{"78777", "78776", "78775"}, "index": 0, "repeat": "none", "order": "listed"}
}

func TestPlexQueueRequestValidation(t *testing.T) {
	for _, params := range []string{
		`{}`, `null`, `[]`, `{"op":"play","ids":["1"]}`, `{"op":"play","runtime":"page-123","ids":[]}`,
		`{"op":"play","runtime":"page-123","ids":[1]}`, `{"op":"play","runtime":"page-123","ids":["0"]}`,
		`{"op":"play","runtime":"page-123","ids":["01"]}`, `{"op":"play","runtime":"page-123","ids":["-1"]}`,
		`{"op":"play","runtime":"page-123","ids":["1.0"]}`, `{"op":"play","runtime":"page-123","ids":["https://private"]}`,
		`{"op":"play","runtime":"page-123","ids":[null]}`, `{"op":"play","runtime":"page-123","ids":null}`,
		`{"op":"play","runtime":"page-123","ids":["1"],"repeat":true}`, `{"op":"status","runtime":"page-123","ids":["1"]}`,
		`{"op":"prev","runtime":"page-123"}`, `{"op":"status","runtime":"page_123"}`,
		`{"op":"status","runtime":"page-123","op":"stop"}`, `{"op":"preview","runtime":"page-123","ids":["1e3"]}`,
		`{"op":"play","runtime":"page-123","ids":["` + strings.Repeat("1", 21) + `"]}`,
		`{"op":"play","runtime":"page-123","ids":[` + strings.Repeat(`"1",`, maxPlexQueueItems) + `"1"]}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"plex_queue","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid Plex request queued")
		}
	}
}

func TestPlexQueueAuthenticatedRuntimeBoundRoundTrip(t *testing.T) {
	for _, op := range []string{"play", "preview", "status", "next", "previous", "stop"} {
		t.Run(op, func(t *testing.T) {
			s, _ := diagnosticServer(t)
			params := map[string]any{"op": op, "runtime": "page-123"}
			if op == "play" || op == "preview" {
				params["ids"] = []string{"78777", "78776", "78775"}
			}
			body, _ := json.Marshal(map[string]any{"action": "plex_queue", "params": params})
			for _, auth := range []struct {
				token string
				code  int
			}{{"", 401}, {firstToken, 403}, {diagnosticOperatorToken, 401}} {
				expect(t, request(s, "POST", "/api/requests?device_id=first", auth.token, string(body), nil), auth.code)
			}
			id := rpcID(t, s, string(body))
			if s.devices[0].queue[0].plexQueue.Runtime != "page-123" {
				t.Fatal("runtime not bound to queued request")
			}
			value := plexQueueFixture()
			if op == "stop" {
				value["state"], value["active"], value["ids"], value["index"] = "idle", false, []string{}, nil
			} else if op == "preview" {
				value = map[string]any{"version": 1, "runtime": "page-123", "state": "ready", "ids": params["ids"], "titles": []string{"One", "Two", "Three"}, "order": "listed"}
			}
			response := screenshotEnvelope(id, "ok", value)
			expect(t, request(s, "POST", "/api/responses", adminToken, response, nil), 403)
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response || s.bytes != 0 || len(s.devices[0].queue) != 0 {
				t.Fatal("Plex receipt changed or request stayed queued")
			}
		})
	}
}

func TestPlexQueueRejectsStaleUnsafeOrInconsistentReceipts(t *testing.T) {
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["runtime"] = "another-page" },
		func(v map[string]any) { v["ids"] = []string{"78775", "78776", "78777"} },
		func(v map[string]any) { v["index"] = 1 },
		func(v map[string]any) { v["active"] = false },
		func(v map[string]any) { v["index"] = true },
		func(v map[string]any) { v["version"] = nil },
		func(v map[string]any) { v["url"] = "https://private/secret" },
		func(v map[string]any) { v["error"] = "private error containing token" },
		func(v map[string]any) { v["repeat"] = "invalid" },
		func(v map[string]any) { v["title"] = strings.Repeat("x", 513) },
		func(v map[string]any) { v["title"] = "bad\ncontrol" },
		func(v map[string]any) { delete(v, "index") },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"plex_queue","params":{"op":"play","runtime":"page-123","ids":["78777","78776","78775"]}}`)
		value := plexQueueFixture()
		mutate(value)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid result consumed queue or stored metadata")
		}
	}
	s := newTestServer(t)
	id := rpcID(t, s, `{"action":"plex_queue","params":{"op":"status","runtime":"page-123"}}`)
	s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", plexQueueFixture()), nil), 404)
}

func TestPlexPreviewBoundAndGenericPlaybackVariant(t *testing.T) {
	ids := make([]string, maxPlexQueueItems)
	titles := make([]string, maxPlexQueueItems)
	for i := range ids {
		ids[i] = "1"
		titles[i] = strings.Repeat("\"\\", 256)
	}
	value := map[string]any{"version": 1, "runtime": "page-123", "state": "ready", "ids": ids, "titles": titles, "order": "listed"}
	body, _ := json.Marshal(value)
	if !validPlexQueueResult(body, &plexQueueRequest{Op: "preview", Runtime: "page-123", IDs: ids}) {
		t.Fatal("maximum valid preview rejected")
	}
	if len(body) <= 64*1024 {
		t.Fatal("escaped-title fixture must exercise the larger preview bound")
	}
	for _, change := range []func(){func() { value["titles"] = []string{"one"} }, func() { value["titles"] = nil }, func() { value["ids"] = []string{"2"} }} {
		change()
		body, _ = json.Marshal(value)
		if validPlexQueueResult(body, &plexQueueRequest{Op: "preview", Runtime: "page-123", IDs: ids}) {
			t.Fatal("invalid preview accepted")
		}
	}
	for _, op := range []string{"next_channel", "previous_channel"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"playback","params":{"operation":"`+op+`"}}`)
		value := map[string]any{"operation": op, "dispatched": true, "plex_queue": plexQueueFixture(), "channel": map[string]any{"number": 2}}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 400)
		delete(value, "channel")
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 200)
	}
}
