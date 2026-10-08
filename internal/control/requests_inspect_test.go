package control

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func inspectPayload(section string) string {
	params := `{"version":1,"runtime":"page-123","section":"` + section + `"`
	if section == "operation" {
		params += `,"operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"`
	}
	return `{"action":"inspect","params":` + params + `}}`
}

func inspectDoctorFixture() map[string]any {
	return map[string]any{
		"version": 1, "runtime": "page-123", "capturedAt": 1791264000000, "collectionMs": 1.5, "consistent": true,
		"build": map[string]any{"version": "1.1.53-beta.13", "sourceRevision": strings.Repeat("a", 40), "buildId": "10359", "identity": "embedded"},
		"ui": map[string]any{
			"documentVisibility": "visible", "documentFocused": true,
			"owner": map[string]any{"kind": "list", "id": 1}, "revision": 2,
			"panes": []any{map[string]any{"kind": "list", "exists": true, "cssVisible": true, "rect": map[string]any{"x": -10, "y": 0, "width": 1920, "height": 1080}}},
			"focus": "list",
		},
		"media": map[string]any{
			"generation": 5, "kind": "vod", "phase": "playing", "displayEvidence": "unavailable",
			"lanes": []any{map[string]any{
				"lane": "main", "handleId": 12, "phase": "playing", "position": 15.5, "duration": 600.0,
				"video": map[string]any{"exists": true, "cssVisible": true, "rect": map[string]any{"x": 0, "y": 0, "width": 1920, "height": 1080}, "paused": false, "ended": false, "readyState": 4, "networkState": 2, "videoWidth": 600, "videoHeight": 480},
			}},
		},
		"capabilities": []any{map[string]any{"name": "screenshot", "state": "unavailable", "reason": "not_implemented"}},
		"reasons":      []any{"owned_overlay_open", "physical_display_unverified"},
	}
}

func inspectData(section string) map[string]any {
	var payload any = inspectDoctorFixture()
	if section == "operation" {
		payload = map[string]any{"operation_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "state": "observed", "action": "playback", "evidence": map[string]any{"kind": "media_progress", "generation": 5, "position": 15.5}}
	}
	return map[string]any{"version": 1, "runtime": "page-123", "section": section, "data": payload}
}

func TestInspectRequestValidation(t *testing.T) {
	for _, params := range []string{
		`null`, `[]`, `{}`, `{"version":1,"runtime":"page-123","section":"eval"}`,
		`{"version":null,"runtime":"page-123","section":"doctor"}`,
		`{"version":2,"runtime":"page-123","section":"doctor"}`,
		`{"version":1,"runtime":"page 123","section":"doctor"}`,
		`{"version":1,"runtime":"page-123","section":"doctor","operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}`,
		`{"version":1,"runtime":"page-123","section":"doctor","section":"snapshot"}`,
		`{"version":1,"runtime":"page-123","section":"operation"}`,
		`{"version":1,"runtime":"page-123","section":"operation","operation_id":null}`,
		`{"version":1,"runtime":"page-123","section":"operation","operation_id":""}`,
		`{"version":1,"runtime":"page-123","section":"doctor","script":"alert(1)"}`,
		`{"version":1,"runtime":"` + strings.Repeat("a", 97) + `","section":"doctor"}`,
	} {
		t.Run(params, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"inspect","params":`+params+`}`, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("invalid inspection was queued")
			}
		})
	}
}

func TestInspectBoundRoundTripAndReceiptRetention(t *testing.T) {
	for _, section := range []string{"doctor", "snapshot", "operation"} {
		t.Run(section, func(t *testing.T) {
			s := newTestServer(t)
			payload := inspectPayload(section)
			expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, payload, nil), 403)
			id := rpcID(t, s, payload)
			poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
			expect(t, poll, 200)
			if !strings.Contains(poll.Body.String(), `"action":"inspect"`) || s.devices[0].queue[0].inspect.Runtime != "page-123" {
				t.Fatal("inspection lost its expectation")
			}
			body := screenshotEnvelope(id, "ok", inspectData(section))
			expect(t, request(s, "POST", "/api/responses", secondToken, body, nil), 404)
			expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, result, 200)
			if result.Body.String() != body || s.resultBytes != len(body) || s.bytes != 0 || len(s.devices[0].queue) != 0 {
				t.Fatal("inspection was duplicated, altered or stayed queued")
			}
			// Keep the expectation after dequeue: an old consumer cannot claim a
			// duplicate unbound negative reply was a valid inspection receipt.
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "unsupported", map[string]any{"error": "unsupported"}), nil), 400)
			s.now = func() time.Time { return time.Now().Add(61 * time.Second) }
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil), 404)
			if s.resultBytes != 0 {
				t.Fatal("inspection did not expire")
			}
		})
	}
}

func TestInspectRejectsUnboundAndMalformedSuccessWithoutDequeuing(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"outer runtime":   func(v map[string]any) { v["runtime"] = "page-other" },
		"section":         func(v map[string]any) { v["section"] = "operation" },
		"outer null":      func(v map[string]any) { v["version"] = nil },
		"outer extra":     func(v map[string]any) { v["error"] = "unavailable" },
		"inner runtime":   func(v map[string]any) { v["data"].(map[string]any)["runtime"] = "page-other" },
		"unsafe integer":  func(v map[string]any) { v["data"].(map[string]any)["capturedAt"] = int64(9007199254740992) },
		"missing boolean": func(v map[string]any) { delete(v["data"].(map[string]any), "consistent") },
		"null boolean":    func(v map[string]any) { v["data"].(map[string]any)["consistent"] = nil },
		"unknown nested":  func(v map[string]any) { v["data"].(map[string]any)["ui"].(map[string]any)["innerHTML"] = "private" },
		"physical claim": func(v map[string]any) {
			v["data"].(map[string]any)["media"].(map[string]any)["displayEvidence"] = "verified"
		},
		"url identity": func(v map[string]any) {
			v["data"].(map[string]any)["build"].(map[string]any)["version"] = "https://private.invalid/token"
		},
		"source revision": func(v map[string]any) {
			v["data"].(map[string]any)["build"].(map[string]any)["sourceRevision"] = strings.Repeat("A", 40)
		},
		"reason text": func(v map[string]any) { v["data"].(map[string]any)["reasons"] = []any{"credential is secret"} },
		"duplicate reason": func(v map[string]any) {
			v["data"].(map[string]any)["reasons"] = []any{"physical_display_unverified", "physical_display_unverified"}
		},
		"large doctor": func(v map[string]any) {
			v["data"].(map[string]any)["padding"] = strings.Repeat("x", maxDoctorSnapshotBytes)
		},
		"null array": func(v map[string]any) { v["data"].(map[string]any)["capabilities"] = nil },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, inspectPayload("doctor"))
			data := inspectData("doctor")
			mutate(data)
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
			if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || s.resultBytes != 0 {
				t.Fatal("invalid success consumed pending inspection")
			}
		})
	}
}

func TestInspectNegativeRepliesRequireTheRequestedBinding(t *testing.T) {
	for _, status := range []string{"unsupported", "rejected"} {
		for _, section := range []string{"doctor", "snapshot", "operation"} {
			s := newTestServer(t)
			id := rpcID(t, s, inspectPayload(section))
			for _, data := range []any{
				map[string]any{"error": "unsupported"},
				map[string]any{"version": 1, "runtime": "page-other", "section": section, "error": "unsupported"},
				map[string]any{"version": 1, "runtime": "page-123", "section": "other", "error": "unsupported"},
				map[string]any{"version": 1, "runtime": "page-123", "section": section, "error": "raw private exception"},
				map[string]any{"version": 1, "runtime": "page-123", "section": section, "error": "unavailable", "data": nil},
			} {
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, data), nil), 400)
				if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 {
					t.Fatal("unbound negative reply consumed inspection")
				}
			}
			body := screenshotEnvelope(id, status, map[string]any{"version": 1, "runtime": "page-123", "section": section, "error": "unavailable"})
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		}
	}
}

func TestInspectOperationCannotSubstituteAnotherReceipt(t *testing.T) {
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["operation_id"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" },
		func(v map[string]any) { v["state"] = "completed" },
		func(v map[string]any) { v["action"] = "shell" },
		func(v map[string]any) { v["evidence"].(map[string]any)["kind"] = "none" },
		func(v map[string]any) { v["evidence"].(map[string]any)["kind"] = "handler_completed" },
		func(v map[string]any) { v["evidence"].(map[string]any)["position"] = nil },
		func(v map[string]any) { v["evidence"].(map[string]any)["generation"] = nil },
		func(v map[string]any) { v["evidence"].(map[string]any)["position"] = -1 },
		func(v map[string]any) { v["evidence"].(map[string]any)["generation"] = 1.5 },
		func(v map[string]any) { v["evidence"].(map[string]any)["screen"] = "verified" },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, inspectPayload("operation"))
		data := inspectData("operation")
		mutate(data["data"].(map[string]any))
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("operation substitution consumed inspection")
		}
	}
}

func TestInspectRejectsNestedDuplicatesAndOversizedEnvelope(t *testing.T) {
	for _, transform := range []func(string) string{
		func(body string) string {
			return strings.Replace(body, `"consistent":true`, `"consistent":true,"consistent":false`, 1)
		},
		func(body string) string {
			return strings.Replace(body, `"position":15.5`, `"position":15.5,"position":0`, 1)
		},
		func(body string) string { return strings.Replace(body, `"width":1920`, `"width":1920,"width":0`, 1) },
		func(body string) string {
			return strings.Replace(body, `"capturedAt":1791264000000`, `"capturedAt":9007199254740990.9`, 1)
		},
		func(body string) string { return body[:1] + strings.Repeat(" ", maxInspectResultBytes) + body[1:] },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, inspectPayload("doctor"))
		expect(t, request(s, "POST", "/api/responses", firstToken, transform(screenshotEnvelope(id, "ok", inspectData("doctor"))), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("malformed inspection was dequeued")
		}
	}
}

func TestInspectUnknownObservationIsValidAndExistingActionsUnchanged(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, inspectPayload("operation"))
	data := inspectData("operation")
	data["data"] = map[string]any{"operation_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "state": "unknown", "action": nil, "evidence": map[string]any{"kind": "none", "generation": nil, "position": nil}}
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 200)
	id = rpcID(t, s, `{"action":"status","params":{}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", map[string]any{"unrestricted_existing_field": true}), nil), 200)
	fixture := inspectDoctorFixture()
	fixture["collectionMs"] = nil
	fixture["build"] = map[string]any{"version": "1.1.53-beta.9+plex-arrows", "sourceRevision": nil, "buildId": nil, "identity": "partial"}
	fixture["ui"] = map[string]any{"documentVisibility": "unknown", "documentFocused": nil, "owner": nil, "revision": nil, "panes": []any{}, "focus": "unknown"}
	fixture["media"] = map[string]any{"generation": nil, "kind": "unknown", "phase": "unknown", "lanes": []any{}, "displayEvidence": "unavailable"}
	fixture["capabilities"] = []any{}
	raw, _ := json.Marshal(fixture)
	if !validDoctorSnapshot(raw, "page-123") {
		t.Fatal("bounded unavailable observations must remain valid")
	}
}

func FuzzInspectResultBinding(f *testing.F) {
	for _, section := range []string{"doctor", "snapshot", "operation"} {
		body, _ := json.Marshal(inspectData(section))
		f.Add(body, "ok", section)
		for _, status := range []string{"unsupported", "rejected"} {
			negative, _ := json.Marshal(map[string]any{"version": 1, "runtime": "page-123", "section": section, "error": "unavailable"})
			f.Add(negative, status, section)
		}
	}
	f.Add([]byte(`{"version":1,"runtime":"page-123","runtime":"other","section":"doctor","error":"unavailable"}`), "rejected", "doctor")
	f.Add([]byte(`null`), "ok", "doctor")
	f.Fuzz(func(t *testing.T, raw []byte, status, section string) {
		// Production is bounded before semantic validation. Mirror that budget
		// so the fuzz target spends its time on reachable parser inputs.
		if len(raw) > maxInspectResultBytes || (section != "doctor" && section != "snapshot" && section != "operation") {
			return
		}
		expected := &inspectRequest{Runtime: "page-123", Section: section, OperationID: strings.Repeat("a", 32)}
		if !validInspectResult(raw, status, expected) {
			return
		}
		// A valid document cannot also validate as another page or another
		// section. Binding holds equally for positive and negative receipts.
		other := *expected
		other.Runtime = "other-page"
		if validInspectResult(raw, status, &other) {
			t.Fatal("inspection result is not bound to its runtime")
		}
		other = *expected
		other.Section = "doctor"
		if section == "doctor" {
			other.Section = "snapshot"
		}
		if validInspectResult(raw, status, &other) {
			t.Fatal("inspection result is not bound to its section")
		}
		if status == "ok" && section == "operation" {
			other = *expected
			other.OperationID = strings.Repeat("b", 32)
			if validInspectResult(raw, status, &other) {
				t.Fatal("inspection result is not bound to its operation")
			}
		}
	})
}
