package control

import (
	"encoding/json"
	"strings"
	"testing"
)

func stalkerCompareParams(t *testing.T, mac string) map[string]json.RawMessage {
	t.Helper()
	settings, err := json.Marshal(map[string]string{
		"name": "Fixture", "server": "https://fixture.invalid/c/", "mac": mac,
	})
	if err != nil {
		t.Fatal(err)
	}
	return map[string]json.RawMessage{
		"provider": json.RawMessage(`"stalker"`),
		"number":   json.RawMessage(`3`),
		"settings": settings,
	}
}

func stalkerComparePayload(t *testing.T, mac string) string {
	t.Helper()
	body, err := json.Marshal(map[string]any{
		"action": "settings_compare", "params": stalkerCompareParams(t, mac),
	})
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func TestSettingsCompareStalkerPreservesPaddedMAC(t *testing.T) {
	const mac = "  02:00:00:00:00:01  "
	s := newTestServer(t)
	id := rpcID(t, s, stalkerComparePayload(t, mac))
	poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
	expect(t, poll, 200)
	var delivery struct {
		Requests []struct {
			ID     string `json:"id"`
			Action string `json:"action"`
			Params struct {
				Settings struct {
					MAC string `json:"mac"`
				} `json:"settings"`
			} `json:"params"`
		} `json:"requests"`
	}
	if err := json.Unmarshal(poll.Body.Bytes(), &delivery); err != nil {
		t.Fatal(err)
	}
	if len(delivery.Requests) != 1 || delivery.Requests[0].ID != id ||
		delivery.Requests[0].Action != "settings_compare" || delivery.Requests[0].Params.Settings.MAC != mac {
		t.Fatal("literal padded MAC changed or was not delivered to the player")
	}
	if len(s.devices[1].queue) != 0 {
		t.Fatal("comparison entered another device queue")
	}
	metadata := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, metadata, 202)
	if strings.Contains(metadata.Body.String(), mac) || strings.Contains(metadata.Body.String(), "fixture.invalid") {
		t.Fatal("pending comparison metadata exposed submitted credentials")
	}
	response := `{"id":"` + id + `","status":"ok","data":{"provider":"stalker","number":3,"matched":true}}`
	expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
	result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
	expect(t, result, 200)
	if result.Body.String() != response || len(s.devices[0].queue) != 0 || s.bytes != 0 {
		t.Fatal("comparison did not complete with only its credential-free result")
	}
}

func TestSettingsCompareStalkerMACFieldBoundAndControls(t *testing.T) {
	const mac = "02:00:00:00:00:01"
	for _, size := range []int{len(mac), 16384, 16385} {
		value := mac + strings.Repeat(" ", size-len(mac))
		if got := validSettingsCompare(stalkerCompareParams(t, value)); got != (size <= 16384) {
			t.Errorf("decoded MAC bytes=%d accepted=%t", size, got)
		}
	}
	// These are the exact control characters rejected by the unchanged helper.
	for code := rune(0); code <= 127; code++ {
		if code >= 32 && code != 127 {
			continue
		}
		if validSettingsCompare(stalkerCompareParams(t, mac+string(code))) {
			t.Errorf("MAC control U+%04X was accepted", code)
		}
	}
	for _, raw := range []string{`"\ud800"`, `"\udc00"`, `null`, `123`, "\"" + string([]byte{0xff}) + "\""} {
		params := stalkerCompareParams(t, mac)
		params["settings"] = json.RawMessage(`{"name":"Fixture","server":"https://fixture.invalid/c/","mac":` + raw + `}`)
		if validSettingsCompare(params) {
			t.Fatal("malformed raw comparison MAC was accepted")
		}
	}
}

func TestSettingsCompareStalkerRetainsHTTPBodyLimit(t *testing.T) {
	if MaxBodyBytes != 16*1024 {
		t.Fatal("this proposal must retain the existing complete-request bound")
	}
	const mac = "02:00:00:00:00:01"
	emptySize := len(stalkerComparePayload(t, ""))
	padding := MaxBodyBytes - emptySize - len(mac)
	if padding < 0 {
		t.Fatal("fixture envelope exceeds the request limit")
	}
	for _, extra := range []int{0, 1} {
		s := newTestServer(t)
		body := stalkerComparePayload(t, mac+strings.Repeat(" ", padding+extra))
		if len(body) != MaxBodyBytes+extra {
			t.Fatal("fixture must exercise the exact complete-request boundary")
		}
		response := request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil)
		if extra == 0 {
			expect(t, response, 202)
			if len(s.devices[0].queue) != 1 {
				t.Fatal("bounded comparison did not enter its device queue")
			}
		} else {
			expect(t, response, 413)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("oversized comparison entered the queue")
			}
		}
	}
}

func TestSettingsCompareStalkerNameBytesAndPrivateRoundTrip(t *testing.T) {
	const mac = "  02:00:00:00:00:01  "
	for _, row := range []struct {
		label string
		name  string
		valid bool
	}{
		{"ascii-512", strings.Repeat("a", 512), true},
		{"ascii-513", strings.Repeat("a", 513), false},
		{"utf8-512", strings.Repeat("é", 256), true},
		{"utf8-514", strings.Repeat("é", 257), false},
		{"padded-utf8-512", "  " + strings.Repeat("é", 254) + "  ", true},
		{"control", "name\n", false},
		{"del", "name\x7f", false},
	} {
		t.Run(row.label, func(t *testing.T) {
			params := stalkerCompareParams(t, mac)
			settings, err := decodeObject(params["settings"])
			if err != nil {
				t.Fatal(err)
			}
			settings["name"], err = json.Marshal(row.name)
			if err != nil {
				t.Fatal(err)
			}
			params["settings"], err = json.Marshal(settings)
			if err != nil {
				t.Fatal(err)
			}
			body, err := json.Marshal(map[string]any{"action": "settings_compare", "params": params})
			if err != nil {
				t.Fatal(err)
			}
			s := newTestServer(t)
			if !row.valid {
				expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, string(body), nil), 400)
				if len(s.devices[0].queue) != 0 || s.bytes != 0 {
					t.Fatal("invalid comparison name entered the queue")
				}
				return
			}
			id := rpcID(t, s, string(body))
			poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
			expect(t, poll, 200)
			var delivery struct {
				Requests []struct {
					ID     string `json:"id"`
					Action string `json:"action"`
					Params struct {
						Settings map[string]string `json:"settings"`
					} `json:"params"`
				} `json:"requests"`
			}
			if err := json.Unmarshal(poll.Body.Bytes(), &delivery); err != nil {
				t.Fatal(err)
			}
			if len(delivery.Requests) != 1 || delivery.Requests[0].ID != id ||
				delivery.Requests[0].Action != "settings_compare" ||
				delivery.Requests[0].Params.Settings["name"] != row.name ||
				delivery.Requests[0].Params.Settings["mac"] != mac {
				t.Fatal("private comparison delivery changed the exact stored literals")
			}
			other := request(s, "GET", "/api/webhook/commands?delivery=ack", secondToken, "", nil)
			expect(t, other, 200)
			metadata := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, metadata, 202)
			for _, value := range []string{row.name, mac, "fixture.invalid"} {
				if strings.Contains(other.Body.String(), value) || strings.Contains(metadata.Body.String(), value) {
					t.Fatal("comparison metadata or another player exposed stored literals")
				}
			}
			response := `{"id":"` + id + `","status":"ok","data":{"provider":"stalker","number":3,"matched":true}}`
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, result, 200)
			if result.Body.String() != response || len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("comparison did not finish with only its credential-free result")
			}
		})
	}
}
