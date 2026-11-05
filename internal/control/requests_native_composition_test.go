package control

import (
	"encoding/json"
	"testing"
)

// Browser and managed-server support are returned capabilities, not probe flags.
// These four flags negotiate relay support and must never reach Native.
const nativeCompositionProbe = `{"plex_queue_state_v2":true,"local_media_handles":true,"local_media_session":true,"local_media_progressive":true}`

func nativeCompositionCapabilities(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	local := localMediaContract(t)
	session := localMediaSessionContract(t)
	browser := readLocalBrowserFixture(t)
	queue := plexQueueV2Contract(t)
	value := map[string]json.RawMessage{
		"player":                  json.RawMessage(`{"runtime":"native-qa-1"}`),
		"plex_queue":              json.RawMessage(plexQueueV2BaseCapability()),
		"plex_queue_state_v2":     queue["capability"],
		"local_media":             local["capability"],
		"local_media_progressive": local["progressive_capability"],
		"local_media_session":     session["capability"],
		"local_browser":           browser.Capabilities["local_browser"],
		"local_media_handles":     browser.Capabilities["local_media_handles"],
	}
	for _, row := range plexServersFixture(t).Capabilities {
		if row.ID == "complete" && row.MarkerJSON != nil {
			value["plex_servers"] = json.RawMessage(*row.MarkerJSON)
			return value
		}
	}
	t.Fatal("missing managed-server capability fixture")
	return nil
}

func nativeCompositionPoll(t *testing.T, s *Server) []map[string]json.RawMessage {
	t.Helper()
	got := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
	expect(t, got, 200)
	var poll struct {
		Commands []json.RawMessage            `json:"commands"`
		Requests []map[string]json.RawMessage `json:"requests"`
	}
	if err := json.Unmarshal(got.Body.Bytes(), &poll); err != nil {
		t.Fatal(err)
	}
	if len(poll.Commands) != 0 {
		t.Fatal("RPC composition unexpectedly created legacy commands")
	}
	return poll.Requests
}

func TestNativeCompositionCapabilityProbeAndOptionalMarkers(t *testing.T) {
	for _, advertised := range []bool{true, false} {
		name := "base-only"
		if advertised {
			name = "all-families"
		}
		t.Run(name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, "capabilities", json.RawMessage(nativeCompositionProbe), ""))
			poll := nativeCompositionPoll(t, s)
			if len(poll) != 1 || string(poll[0]["id"]) != `"`+id+`"` ||
				string(poll[0]["action"]) != `"capabilities"` || string(poll[0]["params"]) != `{}` {
				t.Fatal("combined relay flags changed the request identity or reached the player")
			}
			data := nativeCompositionCapabilities(t)
			if !advertised {
				for _, marker := range []string{"plex_queue_state_v2", "local_media_handles", "local_media_session", "local_media_progressive", "local_browser", "plex_servers"} {
					delete(data, marker)
				}
			}
			body := transferResponse(t, id, "ok", transferJSON(t, data))
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != body || len(nativeCompositionPoll(t, s)) != 0 {
				t.Fatal("combined probe manufactured, dropped or changed player capability evidence")
			}
		})
	}
}

func TestNativeCompositionInvalidFlagAndMarkerPreservePendingProbe(t *testing.T) {
	s := newTestServer(t)
	id := rpcID(t, s, globalRPC(t, "capabilities", json.RawMessage(nativeCompositionProbe), ""))
	original := string(s.devices[0].queue[0].data)
	queuedBytes := s.bytes
	for _, invalid := range []struct {
		key   string
		value json.RawMessage
	}{
		{"local_media_handles", json.RawMessage(`false`)},
		{"plex_queue_state_v2", json.RawMessage(`"true"`)},
		{"local_browser", json.RawMessage(`true`)},
		{"plex_servers", json.RawMessage(`true`)},
	} {
		params := transferObject(t, json.RawMessage(nativeCompositionProbe))
		params[invalid.key] = invalid.value
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken,
			globalRPC(t, "capabilities", transferJSON(t, params), ""), nil), 400)
		if len(s.devices[0].queue) != 1 || string(s.devices[0].queue[0].data) != original || s.bytes != queuedBytes {
			t.Fatalf("invalid %s flag altered the previously accepted probe", invalid.key)
		}
	}
	data := nativeCompositionCapabilities(t)
	validServerMarker := data["plex_servers"]
	data["plex_servers"] = json.RawMessage(`{"version":1,"operations":["list","select"],"max_servers":2}`)
	expect(t, request(s, "POST", "/api/responses", firstToken,
		transferResponse(t, id, "ok", transferJSON(t, data)), nil), 400)
	if len(s.devices[0].queue) != 1 || string(s.devices[0].queue[0].data) != original ||
		s.bytes != queuedBytes || s.resultBytes != 0 || len(s.devices[0].results) != 0 {
		t.Fatal("one invalid family marker consumed a combined capability request")
	}
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil), 202)
	data["plex_servers"] = validServerMarker
	body := transferResponse(t, id, "ok", transferJSON(t, data))
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != body || len(nativeCompositionPoll(t, s)) != 0 {
		t.Fatal("valid completion could not finish the original combined probe")
	}
}

func TestNativeCompositionRequestAndRetainedResultBindingsStayIndependent(t *testing.T) {
	queue := plexQueueV2Contract(t)
	queueParams := transferObject(t, queue["status_request"])
	queueData := transferObject(t, queue["idle_response"])
	// Equal runtimes ensure cross-family rejection is not merely a runtime check.
	queueParams["runtime"] = json.RawMessage(`"native-qa-1"`)
	queueData["runtime"] = json.RawMessage(`"native-qa-1"`)
	browser := readLocalBrowserFixture(t).named(t, "available_roots")
	servers := plexServersFixture(t).reply(t, "list-one")
	serverReply := transferObject(t, json.RawMessage(servers.ResponseJSON))
	cases := []struct {
		action string
		params json.RawMessage
		data   json.RawMessage
	}{
		{"plex_queue", transferJSON(t, queueParams), transferJSON(t, queueData)},
		{"local_browser", browser.Request, browser.Data},
		{"plex_servers", servers.RequestParams, serverReply["data"]},
	}
	s := newTestServer(t)
	ids := make([]string, len(cases))
	bodies := make([]string, len(cases))
	for i, row := range cases {
		ids[i] = rpcID(t, s, globalRPC(t, row.action, row.params, ""))
		bodies[i] = transferResponse(t, ids[i], "ok", row.data)
	}
	queuedBytes := s.bytes
	poll := nativeCompositionPoll(t, s)
	if len(poll) != len(cases) {
		t.Fatal("composed families did not retain all pending RPCs")
	}
	for i, row := range cases {
		if string(poll[i]["id"]) != `"`+ids[i]+`"` || string(poll[i]["action"]) != `"`+row.action+`"` {
			t.Fatal("poll associated a family with the wrong request identity")
		}
		wrong := transferResponse(t, ids[i], "ok", cases[(i+1)%len(cases)].data)
		expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+ids[i], adminToken, "", nil), 202)
	}
	if len(s.devices[0].queue) != len(cases) || s.bytes != queuedBytes || s.resultBytes != 0 {
		t.Fatal("cross-family responses consumed pending work")
	}
	// Completion order is independent of enqueue order.
	for _, i := range []int{2, 0, 1} {
		expect(t, request(s, "POST", "/api/responses", firstToken, bodies[i], nil), 200)
	}
	queueResult := s.devices[0].results[ids[0]]
	browserResult := s.devices[0].results[ids[1]]
	serverResult := s.devices[0].results[ids[2]]
	if queueResult.plexQueue == nil || queueResult.plexQueue.StateVersion != 2 || queueResult.localBrowser != nil || queueResult.plexServers != nil ||
		browserResult.localBrowser == nil || browserResult.plexQueue != nil || browserResult.plexServers != nil ||
		serverResult.plexServers == nil || serverResult.plexQueue != nil || serverResult.localBrowser != nil {
		t.Fatal("composed retained results lost or mixed their request descriptors")
	}
	wantBytes := 0
	for i, body := range bodies {
		wrong := transferResponse(t, ids[i], "ok", cases[(i+1)%len(cases)].data)
		expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+ids[i], adminToken, "", nil)
		expect(t, got, 200)
		if got.Body.String() != body {
			t.Fatalf("%s retained receipt was replaced by another family's reply", cases[i].action)
		}
		wantBytes += len(body)
	}
	if len(nativeCompositionPoll(t, s)) != 0 || len(s.devices[0].results) != len(cases) || s.bytes != 0 || s.resultBytes != wantBytes {
		t.Fatal("composed result replay changed queue or result accounting")
	}
}
