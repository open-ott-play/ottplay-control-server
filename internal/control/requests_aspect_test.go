package control

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestAspectRequestsPreserveExactPayloadAndReceipt(t *testing.T) {
	for _, tc := range []struct {
		name, params, data string
	}{
		{"get_default", `{"operation":"get","runtime":"a"}`, `{"version":1,"runtime":"a","operation":"get","mode":"fit","saved_mode":null,"persisted":false}`},
		{"get_saved", `{"operation":"get","runtime":"page-123"}`, `{"version":1,"runtime":"page-123","operation":"get","mode":"fill","saved_mode":"fill","persisted":true}`},
		{"get_unsaved", `{"operation":"get","runtime":"page-123"}`, `{"version":1,"runtime":"page-123","operation":"get","mode":"fit","saved_mode":"fill","persisted":false}`},
		{"set_fit", `{"operation":"set","runtime":"page-123","mode":"fit"}`, `{"version":1,"runtime":"page-123","operation":"set","mode":"fit","accepted":true,"dispatched":false,"effect":"aspect-after-ack"}`},
		{"set_fill", `{"operation":"set","runtime":"` + strings.Repeat("a", 64) + `","mode":"fill"}`, `{"version":1,"runtime":"` + strings.Repeat("a", 64) + `","operation":"set","mode":"fill","accepted":true,"dispatched":false,"effect":"aspect-after-ack"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"aspect","params":`+tc.params+`}`)
			if len(s.devices[0].queue) != 1 || len(s.devices[1].queue) != 0 {
				t.Fatal("aspect request did not create exactly one device-owned entry")
			}
			// Repeated polls retain one request ID; the player owns deduplication.
			for range 2 {
				poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
				expect(t, poll, 200)
				var envelope struct {
					Commands []json.RawMessage `json:"commands"`
					Requests []struct {
						ID     string          `json:"id"`
						Action string          `json:"action"`
						Params json.RawMessage `json:"params"`
					} `json:"requests"`
				}
				if err := json.Unmarshal(poll.Body.Bytes(), &envelope); err != nil || len(envelope.Commands) != 0 || len(envelope.Requests) != 1 {
					t.Fatal("aspect request was not delivered as one modern RPC")
				}
				got := envelope.Requests[0]
				if got.ID != id || got.Action != "aspect" || string(got.Params) != tc.params {
					t.Fatal("aspect request changed in transit")
				}
			}
			// Set acceptance remains intent, not proof of persistence or display.
			body := `{"id":"` + id + `","status":"ok","data":` + tc.data + `}`
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			}
			result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, result, 200)
			if result.Body.String() != body || len(s.devices[0].queue) != 0 || s.bytes != 0 || s.resultBytes != len(body) {
				t.Fatal("aspect receipt changed, was stored twice, or remained queued")
			}
		})
	}
}

func TestAspectRequestsRejectAmbiguousParameters(t *testing.T) {
	params := []string{
		`null`, `[]`, `true`, `"get"`, `{}`,
		`{"operation":"get"}`,
		`{"operation":"set","mode":"fit"}`,
		`{"runtime":"page-123"}`,
		`{"operation":"set","runtime":"page-123"}`,
		`{"Operation":"get","runtime":"page-123"}`,
		`{"operation":"get","Runtime":"page-123"}`,
		`{"operation":"set","runtime":"page-123","Mode":"fit"}`,
		`{"operation":"get","runtime":"page-123","mode":"fit"}`,
		`{"operation":"get","runtime":"page-123","mode":null}`,
		`{"operation":"get","operation":"set","runtime":"page-123"}`,
		`{"operation":"get","runtime":"page-123","runtime":"page-456"}`,
		`{"operation":"set","runtime":"page-123","mode":"fit","mode":"fill"}`,
	}
	for _, value := range []string{`null`, `true`, `false`, `1`, `[]`, `{}`, `""`} {
		params = append(params,
			`{"operation":`+value+`,"runtime":"page-123"}`,
			`{"operation":"get","runtime":`+value+`}`,
			`{"operation":"set","runtime":"page-123","mode":`+value+`}`)
	}
	for _, runtime := range []string{"PAGE", "page_123", "page/123", " page-123", "page-123 ", "page\\n123", strings.Repeat("a", 65), "страница"} {
		for _, operation := range []string{`"get"`, `"set","mode":"fit"`} {
			params = append(params, `{"operation":`+operation+`,"runtime":"`+runtime+`"}`)
		}
	}
	for _, operation := range []string{"GET", "SET", "status", "read", "fit", "fill", "toggle", "cycle", "aspect", "get_aspect", "set_aspect"} {
		params = append(params, `{"operation":"`+operation+`","runtime":"page-123"}`)
	}
	for _, mode := range []string{"FIT", "FILL", "contain", "cover", "stretch", "auto", "normal", "16:9", "4:3", "0", "fit "} {
		params = append(params, `{"operation":"set","runtime":"page-123","mode":"`+mode+`"}`)
	}
	for _, extra := range []string{`"persist":true`, `"force":true`, `"pin":"1234"`, `"ratio":1.5`, `"key":"aspect"`, `"version":1`} {
		params = append(params,
			`{"operation":"get","runtime":"page-123",`+extra+`}`,
			`{"operation":"set","runtime":"page-123","mode":"fill",`+extra+`}`)
	}
	for _, value := range params {
		t.Run(value, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"aspect","params":`+value+`}`, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("invalid aspect request entered the queue")
			}
		})
	}
}

func TestAspectRequestsKeepAuthenticationAndAcknowledgementBoundaries(t *testing.T) {
	for _, params := range []string{`{"operation":"get","runtime":"page-123"}`, `{"operation":"set","runtime":"page-123","mode":"fit"}`, `{"operation":"set","runtime":"page-123","mode":"fill"}`} {
		t.Run(params, func(t *testing.T) {
			s := newTestServer(t)
			payload := `{"action":"aspect","params":` + params + `}`
			expect(t, request(s, "POST", "/api/requests?device_id=first", "", payload, nil), 401)
			expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, payload, nil), 403)
			if len(s.devices[0].queue) != 0 {
				t.Fatal("unauthorized aspect request queued")
			}
			id := rpcID(t, s, payload)
			// Legacy consumers cannot receive or acknowledge a typed aspect request.
			if len(commands(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), false)) != 0 {
				t.Fatal("aspect request leaked into legacy commands")
			}
			expect(t, request(s, "POST", "/api/webhook/commands/ack", firstToken, `{"ids":["`+id+`"]}`, nil), 200)
			poll := request(s, "GET", "/api/webhook/commands?delivery=ack", secondToken, "", nil)
			expect(t, poll, 200)
			if strings.Contains(poll.Body.String(), id) {
				t.Fatal("aspect request leaked to another device")
			}
			var negative map[string]any
			if err := json.Unmarshal([]byte(params), &negative); err != nil {
				t.Fatal(err)
			}
			negative["version"], negative["error"] = 1, "restricted"
			body := screenshotEnvelope(id, "rejected", negative)
			expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
			expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
			expect(t, request(s, "POST", "/api/responses?device_id=second", firstToken, body, nil), 403)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("legacy acknowledgement or another device consumed aspect request")
			}
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, firstToken, "", nil), 403)
			expect(t, request(s, "GET", "/api/requests?device_id=second&id="+id, adminToken, "", nil), 404)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, result, 200)
			if result.Body.String() != body || len(s.devices[0].queue) != 0 {
				t.Fatal("player rejection was changed or left the request pending")
			}
		})
	}
}

func TestAspectRequestsExpireWithoutReplaying(t *testing.T) {
	for _, operation := range []string{"get", "set"} {
		t.Run(operation, func(t *testing.T) {
			s := newTestServer(t)
			now := time.Unix(1_700_000_000, 0)
			s.now = func() time.Time { return now }
			id := rpcID(t, s, aspectRequestFixture(operation))
			now = now.Add(s.ttl + time.Second)
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "unsupported", aspectNegativeResultFixture(operation)), nil), 404)
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil), 404)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 || s.resultBytes != 0 {
				t.Fatal("expired aspect request or response remained stored")
			}
		})
	}
}

func TestAspectRequestsDoNotExpandOtherControlPaths(t *testing.T) {
	bodies := []string{
		`{"action":"playback","params":{"operation":"aspect","runtime":"page-123","mode":"fill"}}`,
		`{"action":"lifecycle","params":{"operation":"aspect","runtime":"page-123","mode":"fill"}}`,
		`{"action":"input","params":{"key":"aspect","mode":"fill"}}`,
		`{"action":"command","params":{"command":"aspect","mode":"fill"}}`,
		`{"action":"aspect","action":"status","params":{"operation":"get","runtime":"page-123"}}`,
	}
	for _, action := range []string{"Aspect", "ASPECT", "aspect_ratio", "get_aspect", "set_aspect", "fit", "fill"} {
		bodies = append(bodies, `{"action":"`+action+`","params":{"operation":"get","runtime":"page-123"}}`)
	}
	for _, body := range bodies {
		t.Run(body, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("aspect request accepted through another control path")
			}
		})
	}
	s := newTestServer(t)
	expect(t, request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{"command":"aspect","mode":"fill"}`, nil), 400)
	if len(s.devices[0].queue) != 0 || s.bytes != 0 {
		t.Fatal("aspect expanded the legacy command contract")
	}
}
