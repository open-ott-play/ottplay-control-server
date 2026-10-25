package control

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func vportalSettingsFixture(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	b, err := os.ReadFile("../../contracts/vportal-settings-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]json.RawMessage
	if err := json.Unmarshal(b, &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func TestVPortalSettingsBoundContract(t *testing.T) {
	fixture := vportalSettingsFixture(t)
	for _, operation := range []string{"compare", "write"} {
		s := newTestServer(t)
		id := rpcID(t, s, string(fixture[operation+"_request"]))
		if s.devices[0].queue[0].vportalSettings == nil {
			t.Fatal("missing VPortal binding")
		}
		body := `{"id":"` + id + `","status":"ok","data":` + string(fixture[operation+"_result"]) + `}`
		for range 2 {
			expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		}
		bad := strings.Replace(body, "source-1", "wrong-source", 1)
		if operation == "compare" {
			bad = strings.Replace(body, "page-123", "wrong-page", 1)
		}
		expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
	}
}

func TestVPortalSettingsRejectUnboundAndCrossSlotRequests(t *testing.T) {
	for _, params := range []string{
		`{"provider":"vportal","settings":{"profile":1,"vportal":"private"}}`,
		`{"provider":"vportal","settings":{"profile":1,"vportal":"private"},"runtime":"r","context":"c","revision":"r","force":true}`,
		`{"provider":"vportal","settings":{"profile":0,"vportal":"private"},"runtime":"r","context":"c","revision":"r"}`,
		`{"provider":"vportal","settings":{"profile":1,"vportal":"private","active":true},"runtime":"r","context":"c","revision":"r"}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"provider_settings","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("unbound VPortal write queued")
		}
	}
}

func TestVPortalSettingsRejectFalseSaveOrLeakedValues(t *testing.T) {
	fixture := vportalSettingsFixture(t)
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["saved"] = false },
		func(v map[string]any) { v["profile"] = 2 },
		func(v map[string]any) { v["fields"] = []string{"profile", "vportal", "name"} },
		func(v map[string]any) { v["vportal"] = "private-link" },
		func(v map[string]any) { v["reloaded"] = true; v["reload_required"] = true },
		func(v map[string]any) { delete(v, "reload_required") },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, string(fixture["write_request"]))
		var value map[string]any
		_ = json.Unmarshal(fixture["write_result"], &value)
		mutate(value)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", value), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("bad result consumed request")
		}
	}
}

func TestVPortalSettingsSafeErrorBoundToSlotAndRuntime(t *testing.T) {
	fixture := vportalSettingsFixture(t)
	for _, status := range []string{"rejected", "unsupported"} {
		s := newTestServer(t)
		id := rpcID(t, s, string(fixture["write_request"]))
		body := `{"id":"` + id + `","status":"` + status + `","data":` + string(fixture["write_error"]) + `}`
		expect(t, request(s, "POST", "/api/responses", firstToken, strings.Replace(body, `"profile": 1`, `"profile": 2`, 1), nil), 400)
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
	}
}
