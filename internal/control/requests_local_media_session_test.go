package control

import (
	"encoding/json"
	"os"
	"testing"
)

func localMediaSessionContract(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	b, err := os.ReadFile("../../contracts/local-media-session-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var v map[string]json.RawMessage
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func TestLocalMediaSessionProbeForwardsAndBindsOriginalParams(t *testing.T) {
	c := localMediaContract(t)
	probes := []string{`{}`, `{"local_media_progressive":true}`, `{"local_media_session":true}`, `{"local_media_session":true,"local_media_progressive":true}`}
	for _, params := range probes {
		t.Run(params, func(t *testing.T) {
			s, err := New(journalConfig(t))
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			body := `{"action":"capabilities","params":` + params + `,"idempotency_key":"local-session-probe-1"}`
			id := rpcID(t, s, body)
			if retry := rpcID(t, s, body); retry != id || len(s.devices[0].queue) != 1 {
				t.Fatal("capability probe retry was queued twice")
			}
			var forwarded map[string]json.RawMessage
			if err := json.Unmarshal(s.devices[0].queue[0].data, &forwarded); err != nil {
				t.Fatal(err)
			}
			if string(forwarded["params"]) != `{}` || string(forwarded["action"]) != `"capabilities"` || string(forwarded["id"]) != `"`+id+`"` {
				t.Fatal("negotiation flags leaked into the player request")
			}
			for _, other := range probes {
				if other != params {
					conflict := `{"action":"capabilities","params":` + other + `,"idempotency_key":"local-session-probe-1"}`
					expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, conflict, nil), 409)
				}
			}
			if len(s.devices[0].queue) != 1 {
				t.Fatal("idempotency conflict changed the queue")
			}
			// The relay proves only its own support. An old player still answers
			// the empty-params probe without claiming session-only playback.
			response := `{"id":"` + id + `","status":"ok","data":{"local_media":` + string(c["capability"]) + `}}`
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response {
				t.Fatal("relay invented a player session capability")
			}
		})
	}
}

func TestLocalMediaSessionProbeRejectsMalformedParamsBeforeQueue(t *testing.T) {
	for _, params := range []string{
		`null`, `[]`, `true`,
		`{"local_media_session":false}`, `{"local_media_session":null}`,
		`{"local_media_session":1}`, `{"local_media_session":"true"}`,
		`{"local_media_session":{}}`, `{"local_media_session":[]}`,
		`{"local_media_session":true,"extra":true}`,
		`{"local_media_session":true,"local_media_session":true}`,
		`{"local_media_session":true,"local_media_progressive":false}`,
		`{"local_media_session":true,"local_media_progressive":null}`,
		`{"local_media_session":true,"local_media_progressive":1}`,
		`{"local_media_session":true,"local_media_progressive":"true"}`,
		`{"local_media_session":false,"local_media_progressive":true}`,
		`{"local_media_session":null,"local_media_progressive":true}`,
		`{"local_media_session":1,"local_media_progressive":true}`,
		`{"local_media_session":"true","local_media_progressive":true}`,
		`{"local_media_session":true,"local_media_progressive":true,"extra":true}`,
		`{"local_media_session":true,"local_media_progressive":true,"local_media_progressive":true}`,
		`{"session":true}`,
	} {
		t.Run(params, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":`+params+`}`, nil), 400)
			if len(s.devices[0].queue) != 0 {
				t.Fatal("malformed capability probe was queued")
			}
		})
	}
}

func TestLocalMediaSessionCapabilityRequiresValidBase(t *testing.T) {
	c, base := localMediaSessionContract(t), localMediaContract(t)
	for _, marker := range []string{`null`, `true`, `[]`, `{}`, `{"version":true}`, `{"version":2}`, `{"version":1,"extra":true}`, `{"version":1,"version":1}`} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c["probe_request"]))
		response := `{"id":"` + id + `","status":"ok","data":{"local_media":` + string(base["capability"]) + `,"local_media_session":` + marker + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid session marker consumed the probe", marker)
		}
	}
	for _, local := range []string{``, `,"local_media":null`, `,"local_media":{"version":1}`} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c["probe_request"]))
		response := `{"id":"` + id + `","status":"ok","data":{"local_media_session":` + string(c["capability"]) + local + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 400)
	}
	for _, probe := range []string{`{"action":"capabilities","params":{}}`, string(c["session_probe_request"]), string(c["probe_request"])} {
		for _, progressive := range []bool{false, true} {
			s := newTestServer(t)
			id := rpcID(t, s, probe)
			optional := ""
			if progressive {
				optional = `,"local_media_progressive":` + string(base["progressive_capability"])
			}
			response := `{"id":"` + id + `","status":"ok","data":{"local_media":` + string(base["capability"]) + `,"local_media_session":` + string(c["capability"]) + optional + `}}`
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
		}
	}
}

func TestLocalMediaSessionRequestOnlyAcceptsExplicitPlaybackOptIn(t *testing.T) {
	for _, op := range []string{"sources", "connect", "play", "shuffle"} {
		for _, session := range []any{true, false, nil, 1, "true", map[string]any{}, []any{}} {
			for _, progressive := range []any{nil, true, false} {
				s := newTestServer(t)
				params := map[string]any{"runtime": "page-123", "op": op, "session": session}
				if op != "sources" {
					params["source"] = "Movies"
				}
				if progressive != nil {
					params["progressive"] = progressive
				}
				body, _ := json.Marshal(map[string]any{"action": "local_media", "params": params})
				code := 400
				if enabled, ok := session.(bool); ok && enabled && (op == "play" || op == "shuffle") && progressive != false {
					code = 202
				}
				expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, string(body), nil), code)
				if code == 400 && len(s.devices[0].queue) != 0 {
					t.Fatal("invalid session request was queued", op, session, progressive)
				}
				if code == 202 {
					bound := s.devices[0].queue[0].localMedia
					if bound == nil || !bound.Session || bound.Progressive != (progressive == true) {
						t.Fatal("session/progressive intent was not bound")
					}
					var forwarded struct{ Params map[string]json.RawMessage }
					if json.Unmarshal(s.devices[0].queue[0].data, &forwarded) != nil || string(forwarded.Params["session"]) != "true" {
						t.Fatal("session playback intent was not forwarded")
					}
				}
			}
		}
	}
	for _, extra := range []string{`,"extra":true`, `,"session":true`, `,"path":"/Movies"`, `,"include_paths":true`, `,"progressive":null`, `,"progressive":1`, `,"progressive":"true"`, `,"progressive":{}`, `,"progressive":[]`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"local_media","params":{"runtime":"page-123","op":"shuffle","source":"Movies","session":true`+extra+`}}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid session request changed the queue")
		}
	}
}

func TestLocalMediaSessionSharedResultsAndDurableCorrelation(t *testing.T) {
	c := localMediaSessionContract(t)
	for _, op := range []string{"play", "shuffle"} {
		for _, progressive := range []bool{false, true} {
			for _, partial := range []bool{false, true} {
				s := newTestServer(t)
				requestKey, resultKey := op+"_request", op+"_result"
				if progressive {
					requestKey = "progressive_" + requestKey
				}
				if partial {
					resultKey = "progressive_" + resultKey
				}
				id := rpcID(t, s, string(c[requestKey]))
				body := `{"id":"` + id + `","status":"ok","data":` + string(c[resultKey]) + `}`
				if partial && !progressive {
					expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 400)
					if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
						t.Fatal("unnegotiated partial response consumed the request")
					}
					continue
				}
				for range 2 {
					expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
				}
				bound := s.devices[0].results[id].localMedia
				if bound == nil || !bound.Session || bound.Progressive != progressive || len(s.devices[0].queue) != 0 {
					t.Fatal("durable response lost its request intent")
				}
				// After dequeue, malformed retries must still be fenced by the
				// original session/runtime/source and cannot replace the receipt.
				for _, field := range []string{"session", "runtime", "op", "source"} {
					var invalid map[string]any
					_ = json.Unmarshal(c[resultKey], &invalid)
					delete(invalid, field)
					expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", invalid), nil), 400)
				}
				got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
				expect(t, got, 200)
				if got.Body.String() != body {
					t.Fatal("durable session receipt was changed")
				}
			}
		}
	}
}

func TestLocalMediaSessionResultRequiresBoundTrueEcho(t *testing.T) {
	c, legacy := localMediaSessionContract(t), localMediaContract(t)
	for _, key := range []string{"play", "shuffle", "progressive_play", "progressive_shuffle"} {
		for _, value := range []any{nil, false, 1, "true", map[string]any{}, []any{}} {
			s := newTestServer(t)
			id := rpcID(t, s, string(c[key+"_request"]))
			var result map[string]any
			_ = json.Unmarshal(c[key+"_result"], &result)
			result["session"] = value
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", result), nil), 400)
			delete(result, "session")
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", result), nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid or missing session echo consumed the request")
			}
		}
	}
	for _, key := range []string{"sources", "connect", "play", "shuffle", "progressive_play", "progressive_shuffle"} {
		s := newTestServer(t)
		id := rpcID(t, s, string(legacy[key+"_request"]))
		var result map[string]any
		_ = json.Unmarshal(legacy[key+"_result"], &result)
		result["session"] = true
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", result), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("unsolicited session echo consumed a legacy request", key)
		}
		delete(result, "session")
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", result), nil), 200)
	}
}

func TestLocalMediaSessionResultRetainsBoundsAndPrivacy(t *testing.T) {
	c := localMediaSessionContract(t)
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["runtime"] = "other-page" },
		func(v map[string]any) { v["op"] = "play" },
		func(v map[string]any) { v["count"] = 0 },
		func(v map[string]any) { v["count"] = maxLocalMediaItems + 1 },
		func(v map[string]any) { v["count"] = 1.5 },
		func(v map[string]any) { v["accepted"] = false },
		func(v map[string]any) { v["playback_confirmed"] = true },
		func(v map[string]any) { v["shuffled"] = false },
		func(v map[string]any) { v["source"].(map[string]any)["name"] = "Other folder" },
		func(v map[string]any) { v["source"].(map[string]any)["id"] = "bad/id" },
		func(v map[string]any) { v["source"].(map[string]any)["available"] = false },
		func(v map[string]any) { v["source"].(map[string]any)["path"] = "/private/secret" },
		func(v map[string]any) { v["scanning"] = false },
		func(v map[string]any) { v["scanning"] = nil },
		func(v map[string]any) { v["scanning"] = 1 },
		func(v map[string]any) { v["scanning"] = "true" },
		func(v map[string]any) { v["extra"] = true },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c["progressive_shuffle_request"]))
		var result map[string]any
		_ = json.Unmarshal(c["progressive_shuffle_result"], &result)
		mutate(result)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", result), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid session result consumed the request")
		}
	}
}

func TestLocalMediaSessionErrorsRemainFixedAndBound(t *testing.T) {
	c := localMediaSessionContract(t)
	for _, key := range []string{"shuffle_request", "progressive_shuffle_request"} {
		for _, status := range []string{"rejected", "unsupported"} {
			for _, mutate := range []func(map[string]any){
				func(v map[string]any) { v["session"] = true },
				func(v map[string]any) { v["scanning"] = true },
				func(v map[string]any) { v["runtime"] = "other-page" },
				func(v map[string]any) { v["op"] = "play" },
				func(v map[string]any) { v["error"] = "private path or token" },
			} {
				s := newTestServer(t)
				id := rpcID(t, s, string(c[key]))
				var result map[string]any
				_ = json.Unmarshal(c["error_result"], &result)
				mutate(result)
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, result), nil), 400)
				if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
					t.Fatal("malformed session rejection consumed the request")
				}
				body := `{"id":"` + id + `","status":"` + status + `","data":` + string(c["error_result"]) + `}`
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			}
		}
	}
}
