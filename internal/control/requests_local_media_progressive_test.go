package control

import (
	"encoding/json"
	"testing"
)

func TestLocalMediaProgressiveProbeForwardsLegacyCapabilities(t *testing.T) {
	c := localMediaContract(t)
	s, err := New(journalConfig(t))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(c["progressive_probe_request"], &probe); err != nil {
		t.Fatal(err)
	}
	probe["idempotency_key"] = json.RawMessage(`"local-progressive-probe-1"`)
	body, _ := json.Marshal(probe)
	id := rpcID(t, s, string(body))
	if retry := rpcID(t, s, string(body)); retry != id || len(s.devices[0].queue) != 1 {
		t.Fatal("capability probe retry was queued twice")
	}
	var forwarded map[string]json.RawMessage
	if err := json.Unmarshal(s.devices[0].queue[0].data, &forwarded); err != nil {
		t.Fatal(err)
	}
	if string(forwarded["params"]) != `{}` || string(forwarded["action"]) != `"capabilities"` || string(forwarded["id"]) != `"`+id+`"` {
		t.Fatal("relay probe leaked into the player request", string(s.devices[0].queue[0].data))
	}
	probe["params"] = json.RawMessage(`{}`)
	legacy, _ := json.Marshal(probe)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, string(legacy), nil), 409)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("idempotency conflict changed the queue")
	}
	// Older players can answer this negotiated relay probe without a marker.
	response := `{"id":"` + id + `","status":"ok","data":{"local_media":` + string(c["capability"]) + `}}`
	expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != response {
		t.Fatal("relay invented player progressive support")
	}
}

func TestLocalMediaProgressiveProbeRejectsMalformedParams(t *testing.T) {
	for _, params := range []string{
		`{"local_media_progressive":false}`, `{"local_media_progressive":null}`,
		`{"local_media_progressive":1}`, `{"local_media_progressive":"true"}`,
		`{"local_media_progressive":{}}`, `{"local_media_progressive":[]}`,
		`{"local_media_progressive":true,"extra":true}`,
		`{"local_media_progressive":true,"local_media_progressive":true}`,
		`{"progressive":true}`,
	} {
		t.Run(params, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":`+params+`}`, nil), 400)
			if len(s.devices[0].queue) != 0 {
				t.Fatal("malformed probe was queued")
			}
		})
	}
}

func TestLocalMediaProgressiveCapabilityRequiresValidBase(t *testing.T) {
	c := localMediaContract(t)
	for _, marker := range []string{`null`, `true`, `[]`, `{}`, `{"version":true}`, `{"version":2}`, `{"version":1,"extra":true}`, `{"version":1,"version":1}`} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c["progressive_probe_request"]))
		body := `{"id":"` + id + `","status":"ok","data":{"local_media":` + string(c["capability"]) + `,"local_media_progressive":` + marker + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid progressive marker consumed probe", marker)
		}
	}
	for _, base := range []string{``, `,"local_media":null`, `,"local_media":{"version":1}`} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c["progressive_probe_request"]))
		body := `{"id":"` + id + `","status":"ok","data":{"local_media_progressive":` + string(c["progressive_capability"]) + base + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 400)
	}
	for _, params := range []string{`{}`, `{"local_media_progressive":true}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":`+params+`}`)
		body := `{"id":"` + id + `","status":"ok","data":{"local_media":` + string(c["capability"]) + `,"local_media_progressive":` + string(c["progressive_capability"]) + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	}
}

func TestLocalMediaProgressiveRequestOnlyAcceptsExplicitPlaybackOptIn(t *testing.T) {
	for _, op := range []string{"sources", "connect", "play", "shuffle"} {
		for _, value := range []any{true, false, nil, 1, "true", map[string]any{}, []any{}} {
			s := newTestServer(t)
			params := map[string]any{"runtime": "page-123", "op": op, "progressive": value}
			if op != "sources" {
				params["source"] = "Movies"
			}
			body, _ := json.Marshal(map[string]any{"action": "local_media", "params": params})
			code := 400
			if enabled, ok := value.(bool); ok && enabled && (op == "play" || op == "shuffle") {
				code = 202
			}
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, string(body), nil), code)
			if code == 400 && len(s.devices[0].queue) != 0 {
				t.Fatal("invalid progressive request queued", op, value)
			}
			if code == 202 && !s.devices[0].queue[0].localMedia.Progressive {
				t.Fatal("progressive opt-in was not bound")
			}
		}
	}
	s := newTestServer(t)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"local_media","params":{"runtime":"page-123","op":"shuffle","source":"Movies","progressive":true,"extra":true}}`, nil), 400)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"local_media","params":{"runtime":"page-123","op":"shuffle","source":"Movies","progressive":true,"progressive":true}}`, nil), 400)
}

func TestLocalMediaProgressiveRepliesRetainNegotiatedPartialOrCompleteResult(t *testing.T) {
	c := localMediaContract(t)
	for _, op := range []string{"play", "shuffle"} {
		for _, partial := range []bool{false, true} {
			s := newTestServer(t)
			id := rpcID(t, s, string(c["progressive_"+op+"_request"]))
			key := op + "_result"
			if partial {
				key = "progressive_" + key
			}
			body := `{"id":"` + id + `","status":"ok","data":` + string(c[key]) + `}`
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			}
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != body || len(s.devices[0].queue) != 0 || !s.devices[0].results[id].localMedia.Progressive {
				t.Fatal("progressive response or request correlation was changed")
			}
		}
	}
}

func TestLocalMediaProgressiveReplyRejectsUnnegotiatedOrMalformedResults(t *testing.T) {
	c := localMediaContract(t)
	for _, op := range []string{"play", "shuffle", "sources", "connect"} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c[op+"_request"]))
		var result map[string]any
		_ = json.Unmarshal(c[op+"_result"], &result)
		result["scanning"] = true
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", result), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("unnegotiated partial result consumed request", op)
		}
	}
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["scanning"] = false },
		func(v map[string]any) { v["scanning"] = nil },
		func(v map[string]any) { v["scanning"] = 1 },
		func(v map[string]any) { v["scanning"] = "true" },
		func(v map[string]any) { v["runtime"] = "other-page" },
		func(v map[string]any) { v["op"] = "play" },
		func(v map[string]any) { v["count"] = 0 },
		func(v map[string]any) { v["count"] = maxLocalMediaItems + 1 },
		func(v map[string]any) { v["count"] = 1.5 },
		func(v map[string]any) { v["accepted"] = false },
		func(v map[string]any) { v["playback_confirmed"] = true },
		func(v map[string]any) { v["shuffled"] = false },
		func(v map[string]any) { v["source"].(map[string]any)["name"] = "Other folder" },
		func(v map[string]any) { v["source"].(map[string]any)["available"] = false },
		func(v map[string]any) { v["source"].(map[string]any)["path"] = "/private/secret" },
		func(v map[string]any) { v["extra"] = true },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, string(c["progressive_shuffle_request"]))
		var result map[string]any
		_ = json.Unmarshal(c["progressive_shuffle_result"], &result)
		mutate(result)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", result), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid progressive reply consumed request")
		}
	}
	// Rejections keep their fixed, correlated shape even after opt-in.
	s := newTestServer(t)
	id := rpcID(t, s, string(c["progressive_shuffle_request"]))
	var result map[string]any
	_ = json.Unmarshal(c["error_result"], &result)
	result["scanning"] = true
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "rejected", result), nil), 400)
	delete(result, "scanning")
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "rejected", result), nil), 200)
}
