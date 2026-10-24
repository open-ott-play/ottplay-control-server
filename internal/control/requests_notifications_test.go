package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func notificationSettingsPayload(operation string) string {
	fields := `"version":1,"runtime":"page-123","operation":"` + operation + `"`
	if operation == "set" {
		fields += `,"enabled":false`
	}
	return `{"action":"notifications","params":{` + fields + `}}`
}

func notificationSettingsResult(operation string) map[string]any {
	return map[string]any{"version": 1, "runtime": "page-123", "operation": operation, "enabled": false, "persisted": true}
}

func TestNotificationSettingsContractAndDurableResult(t *testing.T) {
	b, err := os.ReadFile("../../contracts/notifications-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var contract map[string]json.RawMessage
	if err := json.Unmarshal(b, &contract); err != nil {
		t.Fatal(err)
	}
	for _, op := range []string{"get", "set"} {
		s := newTestServer(t)
		id := rpcID(t, s, string(contract[op+"_request"]))
		if len(s.devices[0].queue) != 1 || s.devices[0].queue[0].notificationSettings == nil {
			t.Fatal("request is not fenced")
		}
		body := `{"id":"` + id + `","status":"ok","data":` + string(contract[op+"_result"]) + `}`
		for range 2 {
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		}
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		expect(t, got, 200)
		if got.Body.String() != body || len(s.devices[0].queue) != 0 || s.resultBytes != len(body) {
			t.Fatal("durable result changed or replayed")
		}
	}
}

func TestNotificationSettingsRejectInvalidRequests(t *testing.T) {
	for _, params := range []string{
		`{}`, `null`, `[]`,
		`{"operation":"get","runtime":"page-123"}`,
		`{"version":true,"operation":"get","runtime":"page-123"}`,
		`{"version":1.0,"operation":"get","runtime":"page-123"}`,
		`{"version":1,"operation":"toggle","runtime":"page-123"}`,
		`{"version":1,"operation":"get","runtime":"page-123","enabled":true}`,
		`{"version":1,"operation":"set","runtime":"page-123"}`,
		`{"version":1,"operation":"set","runtime":"page-123","enabled":null}`,
		`{"version":1,"operation":"set","runtime":"page-123","enabled":0}`,
		`{"version":1,"operation":"set","runtime":"page-123","enabled":"false"}`,
		`{"version":1,"operation":"set","runtime":"page-123","enabled":false,"force":true}`,
		`{"version":1,"operation":"get","runtime":"page-123","runtime":"other"}`,
		`{"version":1,"operation":"get","runtime":"../secret"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken,
			`{"action":"notifications","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid request enqueued")
		}
	}
}

func TestNotificationSettingsRejectBadResultsWithoutConsumingRequest(t *testing.T) {
	for _, op := range []string{"get", "set"} {
		for _, edit := range []func(map[string]any){
			func(v map[string]any) { v["runtime"] = "stale-page" },
			func(v map[string]any) { v["version"] = true },
			func(v map[string]any) { v["operation"] = "toggle" },
			func(v map[string]any) { v["persisted"] = false },
			func(v map[string]any) { v["persisted"] = nil },
			func(v map[string]any) { v["enabled"] = nil },
			func(v map[string]any) { v["enabled"] = 0 },
			func(v map[string]any) { v["extra"] = "private-value" },
		} {
			s := newTestServer(t)
			id := rpcID(t, s, notificationSettingsPayload(op))
			v := notificationSettingsResult(op)
			edit(v)
			bad := screenshotEnvelope(id, "ok", v)
			expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid result consumed request")
			}
			good := screenshotEnvelope(id, "ok", notificationSettingsResult(op))
			expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
			expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
		}
	}
	s := newTestServer(t)
	id := rpcID(t, s, notificationSettingsPayload("set"))
	v := notificationSettingsResult("set")
	v["enabled"] = true
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", v), nil), 400)
	for _, errorCode := range []string{"invalid_request", "runtime_mismatch", "storage_unavailable", "unsupported"} {
		s := newTestServer(t)
		id := rpcID(t, s, notificationSettingsPayload("set"))
		v := map[string]any{"version": 1, "runtime": "page-123", "operation": "set", "error": errorCode}
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "rejected", v), nil), 200)
	}
}

func TestNotificationSettingsRejectUnboundNegativeResults(t *testing.T) {
	for _, status := range []string{"rejected", "unsupported"} {
		for _, edit := range []func(map[string]any){
			func(v map[string]any) { v["runtime"] = "stale-page" },
			func(v map[string]any) { delete(v, "operation") },
			func(v map[string]any) { v["error"] = "private-storage-value" },
			func(v map[string]any) { v["raw"] = "private-storage-value" },
		} {
			s := newTestServer(t)
			id := rpcID(t, s, notificationSettingsPayload("set"))
			v := map[string]any{"version": 1, "runtime": "page-123", "operation": "set", "error": "storage_unavailable"}
			edit(v)
			got := request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, v), nil)
			expect(t, got, 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 || strings.Contains(got.Body.String(), "private-storage-value") {
				t.Fatal("unbound negative result consumed request or exposed storage detail")
			}
		}
	}
}

func TestNotificationSettingsScopesAndExpiry(t *testing.T) {
	credential := strings.Repeat("n", 32)
	hash := sha256.Sum256([]byte(credential))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "reader", CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"notifications.get"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range []struct {
		token, device, op string
		code              int
	}{
		{"", "first", "get", 401}, {firstToken, "first", "set", 403},
		{credential, "first", "get", 202}, {credential, "first", "set", 403}, {credential, "second", "get", 403},
	} {
		expect(t, request(s, "POST", "/api/requests?device_id="+row.device, row.token, notificationSettingsPayload(row.op), nil), row.code)
	}
	s.operators[0].actions["notifications.set"] = true
	expect(t, request(s, "POST", "/api/requests?device_id=first", credential, notificationSettingsPayload("set"), nil), 202)
	s = newTestServer(t)
	now := time.Now()
	s.now = func() time.Time { return now }
	id := rpcID(t, s, notificationSettingsPayload("set"))
	now = now.Add(s.ttl + time.Second)
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", notificationSettingsResult("set")), nil), 404)
}
