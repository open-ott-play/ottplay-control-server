package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
	"os"
	"strings"
	"testing"
)

func settingsPatchFixture(t *testing.T) (map[string]json.RawMessage, map[string]json.RawMessage) {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/player-settings-patch-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct{ Requests, Responses map[string]json.RawMessage }
	if err = json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture.Requests, fixture.Responses
}

func TestSettingsPatchFixtureAndReplayBinding(t *testing.T) {
	requests, responses := settingsPatchFixture(t)
	for _, pair := range [][2]string{{"get", "get"}, {"set", "set"}, {"set", "unchanged"}, {"set", "unchanged_pending"}} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"player_settings_patch","params":`+string(requests[pair[0]])+`}`)
		if s.devices[0].queue[0].settingsPatch == nil {
			t.Fatal("missing patch binding")
		}
		var data map[string]any
		_ = json.Unmarshal(responses[pair[1]], &data)
		for range 2 {
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 200)
		}
		data["group"] = "local_http"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
	}
}

func TestSettingsPatchRequestBoundsAndFiniteFields(t *testing.T) {
	requests, _ := settingsPatchFixture(t)
	for _, change := range []func(map[string]any){
		func(m map[string]any) { m["group"] = "other" },
		func(m map[string]any) { m["extra"] = true },
		func(m map[string]any) { m["revision"] = nil },
		func(m map[string]any) { m["values"] = map[string]any{} },
		func(m map[string]any) { m["values"] = map[string]any{"localHttpEnabled": 1} },
		func(m map[string]any) { m["values"] = map[string]any{"commandServerEnabled": true} },
		func(m map[string]any) { m["values"] = map[string]any{"commandServerEnabled": 2} },
		func(m map[string]any) { m["values"] = map[string]any{"commandServerToken": strings.Repeat("x", 257)} },
		func(m map[string]any) {
			m["values"] = map[string]any{"commandServerAddress": strings.Repeat("x", 4097)}
		},
	} {
		var m map[string]any
		_ = json.Unmarshal(requests["set"], &m)
		change(m)
		raw, _ := json.Marshal(m)
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"player_settings_patch","params":`+string(raw)+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid patch queued")
		}
	}
	raw := `{"op":"set","runtime":"page-123","group":"command_server","context":"context-1","revision":"revision-1","values":{"commandServerToken":"first","commandServerToken":"second"}}`
	s := newTestServer(t)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"player_settings_patch","params":`+raw+`}`, nil), 400)
	for _, name := range []string{"get", "set", "partial"} {
		m, _ := decodeObject(requests[name])
		if parseSettingsPatchRequest(m) == nil {
			t.Fatalf("rejected fixture %s", name)
		}
	}
}

func TestSettingsPatchBadResultsPreservePending(t *testing.T) {
	requests, responses := settingsPatchFixture(t)
	for _, change := range []func(map[string]any){
		func(m map[string]any) { m["runtime"] = "other" },
		func(m map[string]any) { m["context"].(map[string]any)["token"] = "other" },
		func(m map[string]any) { m["persisted"] = false },
		func(m map[string]any) { m["settings"] = m["settings"].([]any)[:2] },
		func(m map[string]any) { m["settings"].([]any)[1] = m["settings"].([]any)[0] },
		func(m map[string]any) { m["settings"].([]any)[0].(map[string]any)["value"] = "secret" },
		func(m map[string]any) { m["settings"].([]any)[0].(map[string]any)["redacted"] = false },
		func(m map[string]any) { m["settings"].([]any)[2].(map[string]any)["value"] = 0 },
		func(m map[string]any) { delete(m["settings"].([]any)[2].(map[string]any), "persisted_value") },
		func(m map[string]any) { m["application"] = map[string]any{"state": "completed"} },
		func(m map[string]any) { m["transition"] = map[string]any{"state": "applied"} },
	} {
		var m map[string]any
		_ = json.Unmarshal(responses["set"], &m)
		change(m)
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"player_settings_patch","params":`+string(requests["set"])+`}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", m), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("bad result consumed patch")
		}
	}
}

func TestSettingsPatchTransitionAndStorageMatrix(t *testing.T) {
	requests, responses := settingsPatchFixture(t)
	m, _ := decodeObject(requests["get"])
	expected := parseSettingsPatchRequest(m)
	for _, state := range []string{"idle", "pending", "applied", "failed", "rolled_back"} {
		for _, reason := range []string{"", "handshake_timeout", "authorization_failed", "network_error", "storage_failed", "ack_timeout", "effect_failed", "superseded", "unknown"} {
			transition := map[string]string{"state": state}
			if reason != "" {
				transition["reason"] = reason
			}
			raw, _ := json.Marshal(transition)
			want := (state == "idle" || state == "pending" || state == "applied") && reason == "" || (state == "failed" || state == "rolled_back") && reason != "" && reason != "unknown" && reason != "superseded" || state == "failed" && reason == "superseded"
			if validSettingsPatchTransition(raw) != want {
				t.Fatalf("matrix %s/%s", state, reason)
			}
		}
	}
	var data map[string]any
	_ = json.Unmarshal(responses["get"], &data)
	row := data["settings"].([]any)[2].(map[string]any)
	row["storage"] = "default"
	delete(row, "persisted_value")
	raw, _ := json.Marshal(data)
	if !validSettingsPatchResult(raw, "ok", expected) {
		t.Fatal("default snapshot")
	}
	row["persisted_value"] = 1
	raw, _ = json.Marshal(data)
	if validSettingsPatchResult(raw, "ok", expected) {
		t.Fatal("default snapshot with persisted value")
	}
}

func TestSettingsPatchExactOperatorPermissionsAndErrors(t *testing.T) {
	requests, responses := settingsPatchFixture(t)
	s := newTestServer(t)
	const token = "settings-reader-credential-at-least-32-bytes"
	hash := sha256.Sum256([]byte(token))
	s.configureOperators([]config.CommandOperator{{ID: "settings-reader", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"player_settings_patch.get"}}})
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, `{"action":"player_settings_patch","params":`+string(requests["get"])+`}`, nil), 202)
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, `{"action":"player_settings_patch","params":`+string(requests["set"])+`}`, nil), 403)
	for _, permission := range []string{"player_settings_patch", "player_settings_patch.*", "player_settings_patch.schema"} {
		if config.ValidCommandAction(permission) {
			t.Fatal("broad patch permission")
		}
	}
	m, _ := decodeObject(requests["set"])
	expected := parseSettingsPatchRequest(m)
	if !validSettingsPatchResult(responses["error"], "error", expected) {
		t.Fatal("bound negative fixture")
	}
}
