package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func playerSettingsFixture(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	b, err := os.ReadFile("../../contracts/player-settings-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]json.RawMessage
	if err := json.Unmarshal(b, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func playerSettingsParts(t *testing.T, op string) (string, map[string]any) {
	t.Helper()
	fixture := playerSettingsFixture(t)
	var requests, responses map[string]json.RawMessage
	_ = json.Unmarshal(fixture["requests"], &requests)
	_ = json.Unmarshal(fixture["responses"], &responses)
	var data map[string]any
	_ = json.Unmarshal(responses[op], &data)
	return `{"action":"player_settings","params":` + string(requests[op]) + `}`, data
}

func TestPlayerSettingsFixtureBoundResults(t *testing.T) {
	for _, op := range []string{"get", "set"} {
		s := newTestServer(t)
		payload, data := playerSettingsParts(t, op)
		id := rpcID(t, s, payload)
		if s.devices[0].queue[0].playerSettings == nil {
			t.Fatal("missing request binding")
		}
		for range 2 {
			expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 200)
		}
		data["runtime"] = "different-page"
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
	}
}

func TestPlayerSettingsRegisteredProviderContextIDs(t *testing.T) {
	fixture := playerSettingsFixture(t)
	for key, valid := range map[string]bool{"valid_provider_ids": true, "invalid_provider_ids": false} {
		var providers []any
		if err := json.Unmarshal(fixture[key], &providers); err != nil {
			t.Fatal(err)
		}
		for _, provider := range providers {
			body, _ := json.Marshal(map[string]any{"token": "context-1", "provider": provider, "profile": 1})
			if got := validSettingContext(body, "context-1"); got != valid {
				t.Fatalf("provider %v: accepted=%v, want %v", provider, got, valid)
			}
		}
	}
}

func TestPlayerSettingsRejectMalformedRequests(t *testing.T) {
	for _, params := range []string{
		`{}`, `{"op":"get","runtime":"page-123","id":"bufSize","extra":1}`,
		`{"op":"schema","runtime":"page-123","limit":21}`,
		`{"op":"schema","runtime":"page-123","cursor":""}`,
		`{"op":"get","runtime":"page-123","id":"__proto__"}`,
		`{"op":"set","runtime":"page-123","id":"bufSize","value":3}`,
		`{"op":"set","runtime":"page-123","id":"bufSize","context":"c","revision":"r","value":true}`,
		`{"op":"set","runtime":"page-123","id":"bufSize","context":"c","revision":"r","value":1.5}`,
		`{"op":"set","runtime":"page-123","id":"bufSize","context":"c","revision":"r","value":{"eval":"x"}}`,
		`{"op":"set","runtime":"page-123","id":"bufSize","context":"c","revision":"r","value":"\ud800"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"player_settings","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid request was queued")
		}
	}
}

func TestPlayerSettingsRejectUnconfirmedOrCrossSourceWrites(t *testing.T) {
	for _, edit := range []func(map[string]any){
		func(v map[string]any) { v["persisted"] = false },
		func(v map[string]any) { v["context"].(map[string]any)["token"] = "other-source" },
		func(v map[string]any) { v["setting"].(map[string]any)["id"] = "players" },
		func(v map[string]any) { v["setting"].(map[string]any)["persisted_value"] = 4 },
		func(v map[string]any) { v["setting"].(map[string]any)["value"] = true },
		func(v map[string]any) { v["application"].(map[string]any)["state"] = "maybe" },
	} {
		s := newTestServer(t)
		payload, data := playerSettingsParts(t, "set")
		id := rpcID(t, s, payload)
		edit(data)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("bad result consumed request")
		}
	}
}

func TestPlayerSettingsSecretDescriptorNeverReturnsValues(t *testing.T) {
	for _, field := range []string{"value", "persisted_value", "effective_value", "default_value"} {
		s := newTestServer(t)
		payload, data := playerSettingsParts(t, "get")
		id := rpcID(t, s, payload)
		descriptor := data["descriptor"].(map[string]any)
		descriptor["sensitive"] = true
		delete(descriptor, "default_value")
		data["setting"] = map[string]any{"id": "bufSize", "revision": "r", "storage": "stored", "redacted": true, "configured": true}
		if field == "default_value" {
			descriptor[field] = 3
		} else {
			data["setting"].(map[string]any)[field] = 3
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil), 400)
	}
}

func TestPlayerSettingsBootstrapAndScopedOperators(t *testing.T) {
	s := newTestServer(t)
	const credential = "settings-reader-credential-at-least-32-bytes"
	hash := sha256.Sum256([]byte(credential))
	s.configureOperators([]config.CommandOperator{{ID: "settings-reader", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"player_settings.get"}}})
	payload, data := playerSettingsParts(t, "get")
	for _, auth := range []struct {
		token string
		code  int
	}{{"", 401}, {firstToken, 403}, {credential, 202}} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", auth.token, payload, nil), auth.code)
	}
	setPayload, _ := playerSettingsParts(t, "set")
	expect(t, request(s, "POST", "/api/requests?device_id=first", credential, setPayload, nil), 403)
	data["context"].(map[string]any)["provider"] = nil
	data["context"].(map[string]any)["profile"] = nil
	params, _ := decodeObject(json.RawMessage(`{"op":"get","runtime":"page-123","id":"bufSize"}`))
	b, _ := json.Marshal(data)
	if !validPlayerSettingsResult(b, "ok", parsePlayerSettingsRequest(params)) {
		t.Fatal("application settings require no mounted provider")
	}
	for _, action := range []string{"player_settings", "player_settings.*", "player_settings.eval"} {
		if config.ValidCommandAction(action) {
			t.Fatal("unexpected broad settings permission")
		}
	}
	for _, action := range []string{"schema", "get", "set"} {
		if !config.ValidCommandAction("player_settings." + action) {
			t.Fatal("missing exact permission")
		}
	}
}

func TestPlayerSettingsQueuedMetadataDoesNotExposeValue(t *testing.T) {
	s := newTestServer(t)
	const secret = "fixture-private-setting-token"
	payload := `{"action":"player_settings","params":{"op":"set","runtime":"page-123","id":"commandServerToken","context":"c","revision":"r","value":"` + secret + `"}}`
	id := rpcID(t, s, payload)
	for _, endpoint := range []string{"/api/devices", "/api/requests?device_id=first&id=" + id} {
		result := request(s, "GET", endpoint, adminToken, "", nil)
		if strings.Contains(result.Body.String(), secret) {
			t.Fatal("queue metadata leaked setting value")
		}
	}
}

func TestPlayerSettingsJournalStoresNoSettingValue(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	const secret = "private-setting-in-arbitrary-id"
	// The policy is action-based, not a guess that an ID containing 'token'
	// carries credentials. An arbitrary preference can hold a private value.
	payload := `{"action":"player_settings","params":{"op":"set","runtime":"page-123","id":"example","context":"c","revision":"r","value":"` + secret + `"},"idempotency_key":"settings-private-key-01"}`
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, payload, nil), 202)
	b, err := os.ReadFile(c.Journal.Path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(b), secret) || strings.Contains(string(b), `"value"`) || strings.Contains(string(b), `"params"`) {
		t.Fatal("journal exposed setting contents")
	}
	operation := operationValue(t, s, "settings-private-key-01")
	encoded, _ := json.Marshal(operation)
	if strings.Contains(string(encoded), secret) {
		t.Fatal("operation status exposed setting contents")
	}
}

func TestPlayerSettingsSharedNegativeCorpus(t *testing.T) {
	fixture := playerSettingsFixture(t)
	var overrides []map[string]json.RawMessage
	if err := json.Unmarshal(fixture["invalid_descriptor_overrides"], &overrides); err != nil {
		t.Fatal(err)
	}
	for _, override := range overrides {
		descriptor, _ := decodeObject(fixture["descriptor"])
		for key, value := range override {
			descriptor[key] = value
		}
		body, _ := json.Marshal(descriptor)
		if validSettingDescriptor(body) {
			t.Fatal("invalid descriptor accepted", string(body))
		}
	}
	var applications []json.RawMessage
	_ = json.Unmarshal(fixture["invalid_applications"], &applications)
	var requests, responses map[string]json.RawMessage
	_ = json.Unmarshal(fixture["requests"], &requests)
	_ = json.Unmarshal(fixture["responses"], &responses)
	params, _ := decodeObject(requests["set"])
	bound := parsePlayerSettingsRequest(params)
	for _, application := range applications {
		value, _ := decodeObject(responses["set"])
		value["application"] = application
		body, _ := json.Marshal(value)
		if validPlayerSettingsResult(body, "ok", bound) {
			t.Fatal("invalid application accepted", string(application))
		}
	}
}

func TestPlayerSettingsNonsecretGetRequiresHonestValues(t *testing.T) {
	fixture := playerSettingsFixture(t)
	var requests, responses map[string]json.RawMessage
	_ = json.Unmarshal(fixture["requests"], &requests)
	_ = json.Unmarshal(fixture["responses"], &responses)
	params, _ := decodeObject(requests["get"])
	for _, field := range []string{"value", "persisted_value", "redacted"} {
		response, _ := decodeObject(responses["get"])
		snapshot, _ := decodeObject(response["setting"])
		if field == "redacted" {
			snapshot[field] = json.RawMessage("true")
		} else {
			delete(snapshot, field)
		}
		response["setting"], _ = json.Marshal(snapshot)
		body, _ := json.Marshal(response)
		if validPlayerSettingsResult(body, "ok", parsePlayerSettingsRequest(params)) {
			t.Fatal("dishonest snapshot accepted", field)
		}
	}
}
