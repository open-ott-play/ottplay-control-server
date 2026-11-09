package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func nativeSettingsFixture(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/native-provider-settings-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	return transferObject(t, raw)
}

func nativeSettingsParams(t *testing.T, operation string) map[string]any {
	t.Helper()
	var request struct {
		Params map[string]any `json:"params"`
	}
	if err := json.Unmarshal(nativeSettingsFixture(t)[operation+"_request"], &request); err != nil {
		t.Fatal(err)
	}
	return request.Params
}

func nativeSettingsAction(operation string) string {
	if operation == "compare" {
		return "settings_compare"
	}
	return "provider_settings"
}

func nativeSettingsBody(t *testing.T, operation string, params map[string]any) string {
	t.Helper()
	return string(transferJSON(t, map[string]any{"action": nativeSettingsAction(operation), "params": params}))
}

func nativeSettingsResult(t *testing.T, operation string) map[string]any {
	t.Helper()
	var data map[string]any
	if err := json.Unmarshal(nativeSettingsFixture(t)[operation+"_result"], &data); err != nil {
		t.Fatal(err)
	}
	return data
}

func TestNativeProviderSettingsSharedContractAndRetainedBinding(t *testing.T) {
	fixture := nativeSettingsFixture(t)
	var providers, reasons []string
	_ = json.Unmarshal(fixture["provider_ids"], &providers)
	_ = json.Unmarshal(fixture["reasons"], &reasons)
	if len(providers) != 44 || !reflect.DeepEqual(providers, nativeSettingsProviders) || !reflect.DeepEqual(reasons, nativeSettingsReasons) {
		t.Fatal("native settings registry or reasons diverged from shared contract")
	}
	for _, operation := range []string{"compare", "set"} {
		t.Run(operation, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, string(fixture[operation+"_request"]))
			bound := s.devices[0].queue[0].nativeProviderSettings
			if bound == nil || bound.Provider != "edem" || bound.Number != 3 || bound.Runtime != "native-runtime-1" || bound.Write != (operation == "set") || !reflect.DeepEqual(bound.Fields, []string{"key", "list"}) {
				t.Fatal("request identity/field binding was not retained")
			}
			if strings.Contains(string(transferJSON(t, bound)), "fixture_key") {
				t.Fatal("binding retained credential values")
			}
			body := `{"id":"` + id + `","status":"ok","data":` + string(fixture[operation+"_result"]) + `}`
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
			}
			if !reflect.DeepEqual(s.devices[0].results[id].nativeProviderSettings, bound) || len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("completion lost binding or retained private queue data")
			}
			// Duplicate validation must use retained expectations, not a generic path.
			expect(t, request(s, "POST", "/api/responses", firstToken, strings.Replace(body, "native-runtime-1", "wrong-runtime", 1), nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != body || strings.Contains(got.Body.String(), "fixture_key") {
				t.Fatal("retained receipt changed or disclosed a credential")
			}
		})
	}
}

func TestNativeProviderSettingsEveryCanonicalProviderAndFieldBounds(t *testing.T) {
	for _, provider := range nativeSettingsProviders {
		for _, operation := range []string{"compare", "set"} {
			t.Run(provider+"/"+operation, func(t *testing.T) {
				params := nativeSettingsParams(t, operation)
				params["provider"] = provider
				params["settings"] = map[string]any{"list": 3}
				if provider != "edem" {
					params["settings"] = map[string]any{"mode": 2, "server": "", "username": "", "password": "", "playlist": "", "mac": "", "list": 0}
				}
				s := newTestServer(t)
				rpcID(t, s, nativeSettingsBody(t, operation, params))
				if s.devices[0].queue[0].nativeProviderSettings.Provider != provider {
					t.Fatal("canonical provider identity changed")
				}
			})
		}
	}
	for _, row := range []struct {
		provider, field string
		value           any
	}{
		{"edem", "key", strings.Repeat("K", 512)}, {"edem", "portal", ""},
		{"edem", "portal", strings.Repeat("я", 4096)}, {"edem", "list", 0},
		{"ottclub", "mode", 0}, {"ottclub", "password", strings.Repeat("é", 4096)},
		{"d/maxtv", "server", "https://fixture.invalid/?key=fixture"},
	} {
		params := nativeSettingsParams(t, "compare")
		params["provider"] = row.provider
		params["settings"] = map[string]any{row.field: row.value}
		bound := parseNativeProviderSettingsRequest("settings_compare", transferObject(t, transferJSON(t, params)))
		if bound == nil {
			t.Fatalf("valid %s %s boundary rejected", row.provider, row.field)
		}
	}
}

func TestNativeProviderSettingsRejectMalformedRequestsWithoutLegacyFallback(t *testing.T) {
	mutations := []struct {
		name string
		edit func(map[string]any)
	}{
		{"missing runtime", func(p map[string]any) { delete(p, "runtime") }},
		{"runtime empty", func(p map[string]any) { p["runtime"] = "" }},
		{"runtime too long", func(p map[string]any) { p["runtime"] = strings.Repeat("a", 129) }},
		{"runtime path", func(p map[string]any) { p["runtime"] = "/private/path" }},
		{"version null", func(p map[string]any) { p["version"] = nil }},
		{"version bool", func(p map[string]any) { p["version"] = true }},
		{"version float", func(p map[string]any) { p["version"] = json.Number("1.0") }},
		{"version string", func(p map[string]any) { p["version"] = "1" }},
		{"version future", func(p map[string]any) { p["version"] = 2 }},
		{"slot zero", func(p map[string]any) { p["number"] = 0 }},
		{"slot high", func(p map[string]any) { p["number"] = 16 }},
		{"slot bool", func(p map[string]any) { p["number"] = true }},
		{"slot float", func(p map[string]any) { p["number"] = json.Number("3.0") }},
		{"empty patch", func(p map[string]any) { p["settings"] = map[string]any{} }},
		{"null patch", func(p map[string]any) { p["settings"] = nil }},
		{"array patch", func(p map[string]any) { p["settings"] = []string{"private"} }},
		{"unknown key", func(p map[string]any) { p["settings"] = map[string]any{"cdn": "private"} }},
		{"nested patch", func(p map[string]any) { p["settings"] = map[string]any{"key": map[string]any{"secret": "private"}} }},
		{"empty Edem key", func(p map[string]any) { p["settings"] = map[string]any{"key": ""} }},
		{"long Edem key", func(p map[string]any) { p["settings"] = map[string]any{"key": strings.Repeat("a", 513)} }},
		{"nonASCII Edem key", func(p map[string]any) { p["settings"] = map[string]any{"key": "ключ"} }},
		{"Edem key punctuation", func(p map[string]any) { p["settings"] = map[string]any{"key": "a/b"} }},
		{"Edem username", func(p map[string]any) { p["settings"] = map[string]any{"username": "secret"} }},
		{"extra outer", func(p map[string]any) { p["force"] = true }},
	}
	for _, operation := range []string{"compare", "set"} {
		for _, row := range mutations {
			t.Run(operation+"/"+row.name, func(t *testing.T) {
				params := nativeSettingsParams(t, operation)
				row.edit(params)
				s := newTestServer(t)
				expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, nativeSettingsBody(t, operation, params), nil), 400)
				if len(s.devices[0].queue) != 0 || s.bytes != 0 {
					t.Fatal("invalid native request queued")
				}
			})
		}
		for _, provider := range []any{"ilook", "ilooktv", "Edem", "operator", "m3u", "plex", "stalker", "xtream", "unknown", "../ottclub", []string{"edem"}} {
			params := nativeSettingsParams(t, operation)
			params["provider"] = provider
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, nativeSettingsBody(t, operation, params), nil), 400)
		}
	}
	for _, field := range []string{"context", "revision"} {
		for _, bad := range []any{nil, true, 3, "", "/private/source", strings.Repeat("a", 129)} {
			params := nativeSettingsParams(t, "set")
			params[field] = bad
			if parseNativeProviderSettingsRequest("provider_settings", transferObject(t, transferJSON(t, params))) != nil {
				t.Fatalf("invalid %s admitted", field)
			}
		}
		params := nativeSettingsParams(t, "compare")
		params[field] = "unexpected"
		if parseNativeProviderSettingsRequest("settings_compare", transferObject(t, transferJSON(t, params))) != nil {
			t.Fatal("compare admitted write tokens")
		}
	}
	for _, provider := range []string{"ottclub", "edem"} {
		for _, bad := range []any{-1, 4, true, "2", json.Number("2.0"), nil} {
			params := nativeSettingsParams(t, "compare")
			params["provider"], params["settings"] = provider, map[string]any{"list": bad}
			if parseNativeProviderSettingsRequest("settings_compare", transferObject(t, transferJSON(t, params))) != nil {
				t.Fatal("invalid list admitted")
			}
		}
	}
	for _, bad := range []any{-1, 3, true, "2", json.Number("2.0"), nil} {
		params := nativeSettingsParams(t, "compare")
		params["provider"], params["settings"] = "ottclub", map[string]any{"mode": bad}
		if parseNativeProviderSettingsRequest("settings_compare", transferObject(t, transferJSON(t, params))) != nil {
			t.Fatal("invalid operator mode admitted")
		}
	}
	for _, field := range []string{"server", "username", "password", "playlist", "mac", "portal"} {
		for _, bad := range []any{nil, false, 1, strings.Repeat("é", 4097), "secret\x00", "secret\x1b", "secret\x7f", "secret\u0085"} {
			params := nativeSettingsParams(t, "compare")
			params["provider"] = "ottclub"
			if field == "portal" {
				params["provider"] = "edem"
			}
			params["settings"] = map[string]any{field: bad}
			if parseNativeProviderSettingsRequest("settings_compare", transferObject(t, transferJSON(t, params))) != nil {
				t.Fatalf("invalid %s text admitted", field)
			}
		}
	}
	for _, raw := range []string{
		`{"action":"provider_settings","params":{"provider":"ottclub","version":null,"settings":{}}}`,
		`{"action":"provider_settings","params":{"provider":"ottclub","version":2,"settings":{"driver_specific":"legacy"}}}`,
		`{"action":"settings_compare","params":{"provider":"plex","version":1,"settings":{"server":"","token":""}}}`,
		strings.Replace(nativeSettingsBody(t, "compare", nativeSettingsParams(t, "compare")), `"version":1`, `"version":1,"version":1`, 1),
		strings.Replace(nativeSettingsBody(t, "compare", nativeSettingsParams(t, "compare")), `"key":"fixture_key"`, `"key":"first","key":"second"`, 1),
		strings.Replace(nativeSettingsBody(t, "compare", nativeSettingsParams(t, "compare")), `"key":"fixture_key"`, `"portal":"\ud800"`, 1),
		strings.Replace(nativeSettingsBody(t, "compare", nativeSettingsParams(t, "compare")), `"key":"fixture_key"`, "\"portal\":\"\xff\"", 1),
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, raw, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("malformed or versioned legacy request queued")
		}
	}
}

func TestNativeProviderSettingsClosedResponsesAndConsumedTokens(t *testing.T) {
	for _, operation := range []string{"compare", "set"} {
		mutations := []struct {
			name string
			edit func(map[string]any)
		}{
			{"runtime", func(v map[string]any) { v["runtime"] = "other" }},
			{"provider", func(v map[string]any) { v["provider"] = "ottclub" }},
			{"slot", func(v map[string]any) { v["number"] = 4 }},
			{"bool version", func(v map[string]any) { v["version"] = true }},
			{"float slot", func(v map[string]any) { v["number"] = json.Number("3.0") }},
			{"secret", func(v map[string]any) { v["key"] = "fixture_key" }},
			{"raw error", func(v map[string]any) { v["error"] = "https://private/?key=fixture_key" }},
			{"nested leak", func(v map[string]any) { v["diagnostics"] = map[string]any{"source": "fixture_key"} }},
			{"missing token", func(v map[string]any) { delete(v, "context") }},
			{"invalid token", func(v map[string]any) { v["revision"] = "/private/path" }},
		}
		if operation == "set" {
			mutations = append(mutations,
				struct {
					name string
					edit func(map[string]any)
				}{"new authority", func(v map[string]any) { v["revision"] = "new-revision" }},
				struct {
					name string
					edit func(map[string]any)
				}{"crossed context", func(v map[string]any) { v["context"] = "other-context" }},
				struct {
					name string
					edit func(map[string]any)
				}{"false saved", func(v map[string]any) { v["saved"] = false }},
				struct {
					name string
					edit func(map[string]any)
				}{"numeric saved", func(v map[string]any) { v["saved"] = 1 }},
				struct {
					name string
					edit func(map[string]any)
				}{"extra field", func(v map[string]any) { v["fields"] = []string{"key", "list", "portal"} }},
				struct {
					name string
					edit func(map[string]any)
				}{"duplicate field", func(v map[string]any) { v["fields"] = []string{"key", "key"} }},
				struct {
					name string
					edit func(map[string]any)
				}{"missing field", func(v map[string]any) { v["fields"] = []string{"key"} }},
				struct {
					name string
					edit func(map[string]any)
				}{"both reload flags", func(v map[string]any) { v["reload_required"] = true }},
				struct {
					name string
					edit func(map[string]any)
				}{"numeric reload", func(v map[string]any) { v["reloaded"] = 1 }},
			)
		} else {
			mutations = append(mutations, struct {
				name string
				edit func(map[string]any)
			}{"numeric matched", func(v map[string]any) { v["matched"] = 0 }})
		}
		for _, row := range mutations {
			t.Run(operation+"/"+row.name, func(t *testing.T) {
				s := newTestServer(t)
				id := rpcID(t, s, nativeSettingsBody(t, operation, nativeSettingsParams(t, operation)))
				data := nativeSettingsResult(t, operation)
				row.edit(data)
				response := request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", data), nil)
				expect(t, response, 400)
				if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || strings.Contains(response.Body.String(), "fixture_key") {
					t.Fatal("invalid result consumed request or exposed private value")
				}
			})
		}
	}
	for _, flags := range [][2]bool{{false, false}, {true, false}, {false, true}} {
		params := nativeSettingsParams(t, "set")
		bound := parseNativeProviderSettingsRequest("provider_settings", transferObject(t, transferJSON(t, params)))
		data := nativeSettingsResult(t, "set")
		data["fields"] = []string{"list", "key"}
		data["reloaded"], data["reload_required"] = flags[0], flags[1]
		if !validNativeProviderSettingsResult(transferJSON(t, data), "ok", bound) {
			t.Fatal("valid field order/reload state rejected")
		}
		oversize := append([]byte(strings.Repeat(" ", maxNativeProviderSettingsResultBytes)), transferJSON(t, data)...)
		if validNativeProviderSettingsResult(oversize, "ok", bound) {
			t.Fatal("oversized data receipt admitted")
		}
	}
}

func TestNativeProviderSettingsErrorsAreBoundClosedEnums(t *testing.T) {
	for _, operation := range []string{"compare", "set"} {
		for _, status := range []string{"rejected", "unsupported"} {
			for _, reason := range nativeSettingsReasons {
				s := newTestServer(t)
				id := rpcID(t, s, nativeSettingsBody(t, operation, nativeSettingsParams(t, operation)))
				data := nativeSettingsResult(t, "rejected")
				data["reason"] = reason
				expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, status, data), nil), 200)
			}
		}
	}
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["reason"] = "secret=fixture_key" },
		func(v map[string]any) { v["reason"] = nil },
		func(v map[string]any) { v["error"] = "fixture_key" },
		func(v map[string]any) { v["matches"] = []string{"fixture_key"} },
		func(v map[string]any) { v["runtime"] = "another" },
		func(v map[string]any) { v["provider"] = "ilook" },
		func(v map[string]any) { v["number"] = 2 },
	} {
		s := newTestServer(t)
		id := rpcID(t, s, nativeSettingsBody(t, "set", nativeSettingsParams(t, "set")))
		data := nativeSettingsResult(t, "rejected")
		mutate(data)
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "rejected", data), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("unbound/unsafe error consumed request")
		}
	}
}

func TestNativeProviderSettingsProbeAndClosedCapability(t *testing.T) {
	fixture := nativeSettingsFixture(t)
	marker := transferObject(t, fixture["capability"])["native_provider_settings"]
	if !validNativeProviderSettingsCapability(marker) {
		t.Fatal("shared capability rejected")
	}
	for _, providers := range [][]string{{"edem"}, nativeSettingsProviders} {
		capability := map[string]any{"version": 1, "operations": []string{"set", "compare"}, "providers": providers}
		if !validNativeProviderSettingsCapability(transferJSON(t, capability)) {
			t.Fatal("valid provider subset/operation order rejected")
		}
	}
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["version"] = true },
		func(v map[string]any) { v["version"] = json.Number("1.0") },
		func(v map[string]any) { v["version"] = 2 },
		func(v map[string]any) { v["providers"] = []string{} },
		func(v map[string]any) { v["providers"] = []string{"edem", "edem"} },
		func(v map[string]any) { v["providers"] = []string{"ilook"} },
		func(v map[string]any) { v["providers"] = []string{"plex"} },
		func(v map[string]any) { v["operations"] = []string{"set", "set"} },
		func(v map[string]any) { v["operations"] = []string{"compare", "set", "reset"} },
		func(v map[string]any) { v["secret"] = "fixture_key" },
	} {
		capability := map[string]any{"version": 1, "operations": []string{"compare", "set"}, "providers": []string{"edem"}}
		mutate(capability)
		s := newTestServer(t)
		id := rpcID(t, s, string(fixture["probe"]))
		expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", map[string]any{"native_provider_settings": capability}), nil), 400)
	}
	for _, reply := range []string{`{}`, string(fixture["capability"])} {
		s := newTestServer(t)
		id := rpcID(t, s, string(fixture["probe"]))
		queued := transferObject(t, s.devices[0].queue[0].data)
		if string(queued["params"]) != "{}" {
			t.Fatal("relay probe forwarded new parameters to the player")
		}
		body := `{"id":"` + id + `","status":"ok","data":` + reply + `}`
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		if result.Body.String() != body {
			t.Fatal("relay synthesized or changed capability")
		}
	}
	for _, raw := range []string{"false", "null", "1", `"true"`, `{}`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"capabilities","params":{"native_provider_settings":`+raw+`}}`, nil), 400)
	}
}

func TestNativeProviderSettingsQueuedEnvelopeLimitBeforeJournal(t *testing.T) {
	for _, size := range []int{MaxBodyBytes, MaxBodyBytes + 1} {
		c := journalConfig(t)
		s, err := New(c)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { s.Close() })
		now := time.Unix(1700000000, 123000000)
		s.now = func() time.Time { return now }
		params := nativeSettingsParams(t, "set")
		params["provider"] = "ottclub"
		settings := map[string]any{"server": strings.Repeat("s", 8192), "password": ""}
		params["settings"] = settings
		queued := map[string]any{"action": "provider_settings", "params": params, "id": strings.Repeat("0", 32), "expires_at": float64(now.Add(s.ttl).UnixNano()) / 1e9}
		padding := size - len(transferJSON(t, queued))
		if padding < 1 || padding > 8192 {
			t.Fatal("test boundary exceeds individual field limit")
		}
		settings["password"] = strings.Repeat("p", padding)
		body := string(transferJSON(t, map[string]any{"action": "provider_settings", "params": params, "idempotency_key": "native-settings-01"}))
		if len(body) > MaxBodyBytes || len(transferJSON(t, queued)) != size {
			t.Fatal("test did not isolate queued-envelope bound")
		}
		want := 202
		if size > MaxBodyBytes {
			want = 413
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), want)
		if want == 413 {
			if len(s.devices[0].queue) != 0 || len(s.journal.records) != 0 || s.bytes != 0 {
				t.Fatal("oversized envelope changed journal or queue")
			}
		} else if len(s.devices[0].queue) != 1 || len(s.devices[0].queue[0].data) != size || len(s.journal.records) != 1 {
			t.Fatal("exact queued boundary was not preserved")
		}
	}
	// Raw input is small, but controller JSON escaping enlarges the actual
	// delivery envelope. Admission must check the escaped queued representation.
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	params := nativeSettingsParams(t, "set")
	params["provider"], params["settings"] = "ottclub", map[string]any{"password": strings.Repeat("<", 3000)}
	body := string(transferJSON(t, map[string]any{"action": "provider_settings", "params": params, "idempotency_key": "native-escaping-01"}))
	body = strings.ReplaceAll(body, `\u003c`, "<")
	if len(body) >= MaxBodyBytes {
		t.Fatal("test input already exceeds transport bound")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 413)
	if len(s.devices[0].queue) != 0 || len(s.journal.records) != 0 || s.bytes != 0 {
		t.Fatal("escaped oversized envelope changed journal or queue")
	}
}

func TestNativeProviderSettingsDeviceOperatorAndJournalPrivacy(t *testing.T) {
	c := journalConfig(t)
	token := strings.Repeat("n", 32)
	sum := sha256.Sum256([]byte(token))
	c.Operators = []config.CommandOperator{{ID: "native_reader", CredentialSHA256: hex.EncodeToString(sum[:]), DeviceIDs: []string{"first"}, Actions: []string{"settings_compare"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	compare := nativeSettingsBody(t, "compare", nativeSettingsParams(t, "compare"))
	set := nativeSettingsBody(t, "set", nativeSettingsParams(t, "set"))
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, set, nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=second", token, compare, nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, set, nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=first", "", set, nil), 401)
	queued := request(s, "POST", "/api/requests?device_id=first", token, compare, nil)
	expect(t, queued, 202)
	id := settingString(transferObject(t, queued.Body.Bytes())["id"])
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, token, "", nil), 202)
	expect(t, request(s, "GET", "/api/requests?device_id=second&id="+id, adminToken, "", nil), 404)
	data := nativeSettingsResult(t, "compare")
	response := screenshotEnvelope(id, "ok", data)
	expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
	expect(t, request(s, "POST", "/api/responses", adminToken, response, nil), 403)
	expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, token, "", nil), 200)
	s.operators[0].actions = map[string]bool{}
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, token, "", nil), 403)
	params := nativeSettingsParams(t, "set")
	body := string(transferJSON(t, map[string]any{"action": "provider_settings", "params": params, "idempotency_key": "native-private-01"}))
	id = rpcID(t, s, body)
	before := len(s.devices[0].queue)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != before {
		t.Fatal("idempotent replay duplicated queued write")
	}
	params["settings"].(map[string]any)["key"] = "different_key"
	changed := string(transferJSON(t, map[string]any{"action": "provider_settings", "params": params, "idempotency_key": "native-private-01"}))
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, changed, nil), 409)
	for _, path := range []string{"/api/devices", "/api/requests?device_id=first&id=" + id, "/api/operations?device_id=first&key=native-private-01"} {
		metadata := request(s, "GET", path, adminToken, "", nil)
		if strings.Contains(metadata.Body.String(), "fixture_key") || strings.Contains(metadata.Body.String(), "different_key") {
			t.Fatal("metadata exposed credential")
		}
	}
	raw, err := os.ReadFile(c.Journal.Path)
	if err != nil || strings.Contains(string(raw), "fixture_key") || strings.Contains(string(raw), "context-123") || strings.Contains(string(raw), `"settings":`) {
		t.Fatal("journal retained private request content")
	}
	backing := s.devices[0].queue[:cap(s.devices[0].queue)]
	expect(t, request(s, "POST", "/api/responses", firstToken, screenshotEnvelope(id, "ok", nativeSettingsResult(t, "set")), nil), 200)
	if len(s.devices[0].queue) != 0 || s.bytes != 0 || backing[0].data != nil {
		t.Fatal("completion retained queued credential payload")
	}
	// The retained metadata has no credential members even after queue removal.
	fields := s.devices[0].results[id].nativeProviderSettings.Fields
	if !reflect.DeepEqual(fields, []string{"key", "list"}) {
		t.Fatal("retained binding fields changed")
	}
}
