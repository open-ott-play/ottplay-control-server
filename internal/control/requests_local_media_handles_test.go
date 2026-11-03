package control

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestLocalBrowserAndHandleCapabilitiesAreCompleteAndOptional(t *testing.T) {
	f := readLocalBrowserFixture(t)
	base := localMediaContract(t)["capability"]
	if !validLocalBrowserCapability(f.Capabilities["local_browser"]) || !validLocalMediaHandlesCapability(f.Capabilities["local_media_handles"], base) {
		t.Fatal("shared capability rejected")
	}
	for _, field := range []string{"local_browser", "local_media_handles"} {
		for _, mutate := range []func(map[string]json.RawMessage){
			func(v map[string]json.RawMessage) { v["version"] = json.RawMessage(`true`) },
			func(v map[string]json.RawMessage) { v["version"] = json.RawMessage(`1.0`) },
			func(v map[string]json.RawMessage) { v["version"] = json.RawMessage(`2`) },
			func(v map[string]json.RawMessage) { v["operations"] = json.RawMessage(`[]`) },
			func(v map[string]json.RawMessage) { v["operations"] = json.RawMessage(`["select"]`) },
			func(v map[string]json.RawMessage) { v["operations"] = json.RawMessage(`["connect","play","play"]`) },
			func(v map[string]json.RawMessage) { v["private"] = json.RawMessage(`"secret"`) },
		} {
			s := newTestServer(t)
			id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
			marker := transferObject(t, f.Capabilities[field])
			mutate(marker)
			data := map[string]json.RawMessage{field: transferJSON(t, marker), "local_media": base}
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, data)), nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid marker consumed request")
			}
		}
	}
	for _, limit := range []json.RawMessage{json.RawMessage(`63`), json.RawMessage(`65`), json.RawMessage(`64.0`), json.RawMessage(`true`)} {
		marker := transferObject(t, f.Capabilities["local_browser"])
		marker["max_entries"] = limit
		if validLocalBrowserCapability(transferJSON(t, marker)) {
			t.Fatal("wrong page limit accepted")
		}
	}
	for _, raw := range []json.RawMessage{nil, json.RawMessage(`null`), json.RawMessage(`{"version":1}`)} {
		if validLocalMediaHandlesCapability(f.Capabilities["local_media_handles"], raw) {
			t.Fatal("missing/invalid base accepted")
		}
	}
	for _, ops := range []string{`[]`, `["sources"]`, `["play","shuffle"]`, `["connect","play"]`} {
		marker := transferObject(t, base)
		marker["operations"] = json.RawMessage(ops)
		if validLocalMediaHandlesCapability(f.Capabilities["local_media_handles"], transferJSON(t, marker)) {
			t.Fatal("unsupported base operations accepted")
		}
	}
	// Complete operation sets are order-independent.
	marker := transferObject(t, f.Capabilities["local_browser"])
	marker["operations"] = json.RawMessage(`["select","more","list","roots"]`)
	if !validLocalBrowserCapability(transferJSON(t, marker)) {
		t.Fatal("complete reordered operations rejected")
	}
	for _, data := range []json.RawMessage{json.RawMessage(`{}`), transferJSON(t, map[string]json.RawMessage{"local_media": base}), transferJSON(t, map[string]json.RawMessage{"local_media": base, "local_browser": f.Capabilities["local_browser"], "local_media_handles": f.Capabilities["local_media_handles"]})} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := transferResponse(t, id, "ok", data)
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		if got.Body.String() != body {
			t.Fatal("controller manufactured/changed capabilities")
		}
	}
}

func TestLocalMediaHandleProbeIsTrueOnlyAndDoesNotClaimPlayerSupport(t *testing.T) {
	for _, params := range []string{`{"local_media_handles":true}`, `{"local_media_handles":true,"local_media_session":true,"local_media_progressive":true}`} {
		s, err := New(journalConfig(t))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = s.Close() })
		body := `{"action":"capabilities","params":` + params + `,"idempotency_key":"local-handle-probe-1"}`
		id := rpcID(t, s, body)
		if rpcID(t, s, body) != id || len(s.devices[0].queue) != 1 {
			t.Fatal("probe duplicated")
		}
		forwarded := transferObject(t, s.devices[0].queue[0].data)
		if string(forwarded["params"]) != `{}` {
			t.Fatal("relay probe flags reached player")
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":{},"idempotency_key":"local-handle-probe-1"}`, nil), 409)
		// An old player can answer without a marker; the relay must retain that absence.
		response := transferResponse(t, id, "ok", json.RawMessage(`{}`))
		expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		if got.Body.String() != response {
			t.Fatal("relay invented handle support")
		}
	}
	for _, raw := range []string{`false`, `null`, `1`, `"true"`, `{}`, `[]`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":{"local_media_handles":`+raw+`}}`, nil), 400)
	}
	s := newTestServer(t)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":{"local_media_handles":true,"local_media_handles":true}}`, nil), 400)
}

func TestLocalMediaHandlesSharedHTTPReceiptsRemainBound(t *testing.T) {
	for _, row := range readLocalBrowserFixture(t).HandleCases {
		t.Run(row.Name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, "local_media", row.Request, ""))
			bound := s.devices[0].queue[0].localMedia
			if bound == nil || bound.SourceHandle != "handle-1" || bound.Source != "" {
				t.Fatal("handle correlation lost")
			}
			good := transferResponse(t, id, row.Status, row.Data)
			bad := transferObject(t, row.Data)
			bad["source_handle"] = json.RawMessage(`"another-handle"`)
			wrong := transferResponse(t, id, row.Status, transferJSON(t, bad))
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("wrong handle consumed request")
			}
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
			}
			if s.devices[0].results[id].localMedia.SourceHandle != "handle-1" {
				t.Fatal("retained handle lost")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			if got.Body.String() != good {
				t.Fatal("handle response changed")
			}
		})
	}
}

func TestLocalMediaHandleRequestAndReplyGuardsPreserveLegacy(t *testing.T) {
	f := readLocalBrowserFixture(t)
	for _, row := range f.HandleCases {
		for _, patch := range []map[string]any{{"source": "Movies"}, {"source_handle": nil}, {"source_handle": ""}, {"source_handle": "/tmp/usb"}, {"source_handle": strings.Repeat("a", 257)}, {"path": "/private"}, {"version": 1}} {
			value := transferObject(t, row.Request)
			for key, v := range patch {
				value[key] = transferJSON(t, v)
			}
			if parseLocalMediaRequest(value) != nil {
				t.Fatal("invalid handle parameters accepted", patch)
			}
		}
		q := parseLocalMediaRequest(transferObject(t, row.Request))
		for _, patch := range []map[string]any{{"source_handle": "other"}, {"source_handle": nil}, {"playback_confirmed": true}, {"count": 50001}, {"runtime": "other"}, {"secret": "private"}} {
			value := transferObject(t, row.Data)
			for key, v := range patch {
				value[key] = transferJSON(t, v)
			}
			if validLocalMediaResult(transferJSON(t, value), "ok", q) {
				t.Fatal("invalid handle result accepted", patch)
			}
		}
		value := transferObject(t, row.Data)
		delete(value, "source_handle")
		if validLocalMediaResult(transferJSON(t, value), "ok", q) {
			t.Fatal("missing handle echo accepted")
		}
		if q.Session {
			value = transferObject(t, row.Data)
			delete(value, "session")
			if validLocalMediaResult(transferJSON(t, value), "ok", q) {
				t.Fatal("missing session echo accepted")
			}
		}
		errorData := map[string]any{"version": 1, "runtime": q.Runtime, "op": q.Op, "state": "error", "error": "source_unavailable"}
		if !validLocalMediaResult(transferJSON(t, errorData), "rejected", q) {
			t.Fatal("fixed handle error rejected")
		}
		errorData["source_handle"] = q.SourceHandle
		if validLocalMediaResult(transferJSON(t, errorData), "rejected", q) {
			t.Fatal("error leaked handle")
		}
		legacy := transferObject(t, row.Request)
		delete(legacy, "source_handle")
		legacy["source"] = json.RawMessage(`"Family movies"`)
		legacyQ := parseLocalMediaRequest(legacy)
		legacyResult := transferObject(t, row.Data)
		delete(legacyResult, "source_handle")
		if legacyQ == nil || !validLocalMediaResult(transferJSON(t, legacyResult), "ok", legacyQ) {
			t.Fatal("legacy source result changed")
		}
		if validLocalMediaResult(row.Data, "ok", legacyQ) {
			t.Fatal("unsolicited handle accepted in legacy reply")
		}
	}
	for _, params := range []string{
		`{"runtime":"native-qa-1","op":"sources","source_handle":"handle-1"}`,
		`{"runtime":"native-qa-1","op":"connect","source_handle":"handle-1","session":true}`,
		`{"runtime":"native-qa-1","op":"connect","source_handle":"handle-1","progressive":true}`,
	} {
		if parseLocalMediaRequest(transferObject(t, json.RawMessage(params))) != nil {
			t.Fatal("handle bypassed operation-specific guard")
		}
	}
}
