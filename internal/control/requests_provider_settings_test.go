package control

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestPlexSettingsRoundTripIsolationAndSecretFreeAcknowledgement(t *testing.T) {
	s := newTestServer(t)
	const server = "http://nas.example:32400"
	const secret = "test-only-plex-token"
	const payload = `{"action":"provider_settings","params":{"provider":"plex","settings":{"server":"http://nas.example:32400","token":"test-only-plex-token"}}}`
	expect(t, request(s, "POST", "/api/requests?device_id=first", "", payload, nil), 401)
	expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, payload, nil), 403)
	id := rpcID(t, s, payload)
	if len(s.devices[0].queue) != 1 || len(s.devices[1].queue) != 0 {
		t.Fatal("Plex settings entered the wrong device queue")
	}
	poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
	expect(t, poll, 200)
	var delivery struct {
		Requests []struct {
			ID     string         `json:"id"`
			Action string         `json:"action"`
			Params map[string]any `json:"params"`
		} `json:"requests"`
	}
	if err := json.Unmarshal(poll.Body.Bytes(), &delivery); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"provider": "plex", "settings": map[string]any{"server": server, "token": secret}}
	if len(delivery.Requests) != 1 || delivery.Requests[0].ID != id || delivery.Requests[0].Action != "provider_settings" || !reflect.DeepEqual(delivery.Requests[0].Params, want) {
		t.Fatal("Plex settings changed during authenticated delivery")
	}
	other := request(s, "GET", "/api/webhook/commands?delivery=ack", secondToken, "", nil)
	expect(t, other, 200)
	if strings.Contains(other.Body.String(), id) || strings.Contains(other.Body.String(), secret) {
		t.Fatal("another device received the Plex request")
	}
	for _, metadata := range []struct {
		path string
		code int
	}{{"/api/devices", 200}, {"/api/requests?device_id=first&id=" + id, 202}} {
		status := request(s, "GET", metadata.path, adminToken, "", nil)
		expect(t, status, metadata.code)
		if strings.Contains(status.Body.String(), server) || strings.Contains(status.Body.String(), secret) {
			t.Fatal("queue metadata disclosed provider settings")
		}
	}
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, firstToken, "", nil), 403)
	expect(t, request(s, "GET", "/api/requests?device_id=second&id="+id, adminToken, "", nil), 404)
	// The player returns field names and saved state, never credential values.
	resultBody := `{"id":"` + id + `","status":"ok","data":{"fields":["server","token"],"provider":"plex","saved":true}}`
	expect(t, request(s, "POST", "/api/responses", secondToken, resultBody, nil), 404)
	expect(t, request(s, "POST", "/api/responses", adminToken, resultBody, nil), 403)
	ack := request(s, "POST", "/api/responses", firstToken, resultBody, nil)
	expect(t, ack, 200)
	result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, result, 200)
	if result.Body.String() != resultBody || strings.Contains(result.Body.String(), secret) || strings.Contains(ack.Body.String(), secret) {
		t.Fatal("the acknowledgement changed or disclosed the Plex token")
	}
	if len(s.devices[0].queue) != 0 || s.bytes != 0 {
		t.Fatal("completed Plex settings retained their queued payload")
	}
}

func TestProviderSettingsDriverValidationRemainsPlayerOwned(t *testing.T) {
	for _, provider := range []string{"m3u", "xtream", "stalker", "ottclub", "plex"} {
		for _, settings := range []string{`{}`, `{"driver_specific":"value"}`} {
			t.Run(provider+"/"+settings, func(t *testing.T) {
				s := newTestServer(t)
				// The controller checks the envelope; the active player decides
				// whether this provider-specific object is meaningful or valid.
				id := rpcID(t, s, `{"action":"provider_settings","params":{"provider":"`+provider+`","settings":`+settings+`}}`)
				poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
				expect(t, poll, 200)
				if !strings.Contains(poll.Body.String(), id) || !strings.Contains(poll.Body.String(), `"settings":`+settings) {
					t.Fatal("provider-specific settings were not preserved for player validation")
				}
			})
		}
	}
}

func TestPlexSettingsRejectMalformedEnvelopeBeforeQueueing(t *testing.T) {
	for _, params := range []string{
		`{"provider":"PLEX","settings":{}}`, `{"provider":"plex-extra","settings":{}}`,
		`{"provider":"unknown","settings":{}}`, `{"provider":null,"settings":{}}`,
		`{"provider":"plex"}`, `{"provider":"plex","settings":null}`,
		`{"provider":"plex","settings":[]}`, `{"provider":"plex","settings":"token"}`,
		`{"provider":"plex","settings":{"token":"one","token":"two"}}`,
		`{"provider":"plex","settings":{},"token":"outside-settings"}`,
	} {
		t.Run(params, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"provider_settings","params":`+params+`}`, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("invalid Plex settings entered the queue")
			}
		})
	}
}
