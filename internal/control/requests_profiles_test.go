package control

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestProfileAndRestartRequestRoundTrip(t *testing.T) {
	for _, body := range []string{
		`{"action":"profiles","params":{}}`,
		`{"action":"kiosk","params":{"mode":"status"}}`,
		`{"action":"kiosk","params":{"mode":"on"}}`,
		`{"action":"kiosk","params":{"mode":"on","strict":true}}`,
		`{"action":"kiosk","params":{"mode":"on","strict":false}}`,
		`{"action":"kiosk","params":{"mode":"set","query":"12","strict":true}}`,
		`{"action":"kiosk","params":{"mode":"on","query":"Новости"}}`,
		`{"action":"kiosk","params":{"mode":"set","query":"12"}}`,
		`{"action":"kiosk","params":{"mode":"off"}}`,
		`{"action":"profile","params":{"number":1}}`,
		`{"action":"profile","params":{"number":15}}`,
		`{"action":"profile_settings","params":{"number":2,"settings":{"name":"Гостиная 😀","playlist":"https://playlist.example/list?key=test-only","history_hours":8760,"vportal":"https://portal.example/link"}}}`,
		`{"action":"profile_settings","params":{"number":15,"settings":{"name":"","playlist":"","history_hours":0,"vportal":""}}}`,
		`{"action":"profile_settings","params":{"number":1,"settings":{"name":"\ud83d\ude00"}}}`,
		`{"action":"profile_settings","params":{"number":1,"settings":{"name":"\\ud800"}}}`,
		`{"action":"profile_settings","params":{"number":1,"settings":{"name":"` + strings.Repeat("я", 128) + `"}}}`,
		`{"action":"profile_settings","params":{"number":1,"settings":{"playlist":"` + strings.Repeat("я", 4096) + `"}}}`,
		`{"action":"profile_settings","params":{"number":1,"settings":{"vportal":"` + strings.Repeat("x", 8192) + `"}}}`,
		`{"action":"restart","params":{"target":"stream"}}`,
		`{"action":"restart","params":{"target":"player"}}`,
	} {
		t.Run(body[:min(len(body), 100)], func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, body)
			poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
			expect(t, poll, 200)
			var envelope struct {
				Requests []map[string]json.RawMessage `json:"requests"`
			}
			if err := json.Unmarshal(poll.Body.Bytes(), &envelope); err != nil || len(envelope.Requests) != 1 {
				t.Fatal("request did not reach the intended device")
			}
			original, _ := decodeObject([]byte(body))
			for _, field := range []string{"action", "params"} {
				var before, after any
				_ = json.Unmarshal(original[field], &before)
				_ = json.Unmarshal(envelope.Requests[0][field], &after)
				beforeJSON, _ := json.Marshal(before)
				afterJSON, _ := json.Marshal(after)
				if string(beforeJSON) != string(afterJSON) {
					t.Fatalf("%s changed in transit", field)
				}
			}
			result := `{"id":"` + id + `","status":"ok","data":{"accepted":true}}`
			expect(t, request(s, "POST", "/api/responses", firstToken, result, nil), 200)
			readback := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, readback, 200)
			if readback.Body.String() != result || len(s.devices[0].queue) != 0 {
				t.Fatal("result changed or request remained queued")
			}
		})
	}
}

func TestProfileRequestsRejectInvalidParametersBeforeQueueing(t *testing.T) {
	bodies := []string{
		`{"action":"kiosk","params":{}}`,
		`{"action":"kiosk","params":{"mode":"set"}}`,
		`{"action":"kiosk","params":{"mode":"status","query":"1"}}`,
		`{"action":"kiosk","params":{"mode":"off","query":"1"}}`,
		`{"action":"kiosk","params":{"mode":"on","query":1}}`,
		`{"action":"kiosk","params":{"mode":"on","query":""}}`,
		`{"action":"kiosk","params":{"mode":"on","query":" "}}`,
		`{"action":"kiosk","params":{"mode":"on","query":"` + strings.Repeat("x", 1025) + `"}}`,
		`{"action":"kiosk","params":{"mode":"on","extra":true}}`,
		`{"action":"kiosk","params":{"mode":"on","strict":null}}`,
		`{"action":"kiosk","params":{"mode":"on","strict":"true"}}`,
		`{"action":"kiosk","params":{"mode":"on","strict":1}}`,
		`{"action":"kiosk","params":{"mode":"on","strict":true,"extra":true}}`,
		`{"action":"kiosk","params":{"mode":"set","strict":true}}`,
		`{"action":"kiosk","params":{"mode":"status","strict":true}}`,
		`{"action":"kiosk","params":{"mode":"off","strict":true}}`,
		`{"action":"kiosk","params":{"mode":"on","mode":"off"}}`,
		`{"action":"kiosk","params":{"mode":"ON"}}`,
		`{"action":"profiles","params":{"provider":"m3u"}}`,
		`{"action":"profile","params":{}}`,
		`{"action":"profile","params":{"number":1,"extra":true}}`,
		`{"action":"profile","params":{"number":1,"number":2}}`,
		`{"action":"profile_settings","params":{"number":1}}`,
		`{"action":"profile_settings","params":{"number":1,"settings":{},"extra":true}}`,
		`{"action":"profile_settings","params":{"number":1,"settings":{"name":"one","name":"two"}}}`,
	}
	for _, value := range []string{`null`, `true`, `"1"`, `0`, `-1`, `16`, `1.1`, `1.0`, `1e0`, `[]`, `{}`} {
		bodies = append(bodies,
			`{"action":"profile","params":{"number":`+value+`}}`,
			`{"action":"profile_settings","params":{"number":`+value+`,"settings":{"name":"Valid"}}}`)
	}
	for _, settings := range []string{`null`, `[]`, `{}`, `{"provider":"m3u"}`, `{"name":"Valid","secret":"test-only"}`, `{"settings":{"name":"nested"}}`,
		`{"name":null}`, `{"playlist":true}`, `{"vportal":42}`, `{"name":[]}`, `{"playlist":{}}`,
		`{"history_hours":null}`, `{"history_hours":true}`, `{"history_hours":"24"}`, `{"history_hours":-1}`, `{"history_hours":8761}`, `{"history_hours":0.5}`, `{"history_hours":24.0}`, `{"history_hours":1e3}`,
		`{"name":"\ud800"}`, `{"name":"\udc00"}`, `{"playlist":"\ud800\u0041"}`, `{"vportal":"\ud800\\udc00"}`,
		`{"name":"Name\u001b"}`, `{"playlist":"url\n"}`, `{"vportal":"link\u007f"}`,
		`{"name":"` + string([]byte{0xff}) + `"}`,
		`{"name":"` + strings.Repeat("я", 129) + `"}`,
		`{"playlist":"` + strings.Repeat("я", 4097) + `"}`,
		`{"vportal":"` + strings.Repeat("x", 8193) + `"}`} {
		bodies = append(bodies, `{"action":"profile_settings","params":{"number":1,"settings":`+settings+`}}`)
	}
	for _, body := range bodies {
		t.Run(body[:min(len(body), 100)], func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("invalid profile request entered the queue")
			}
		})
	}
}

func TestRestartRequestNamesAndParametersAreExact(t *testing.T) {
	for _, body := range []string{
		`{"action":"Profiles","params":{}}`, `{"action":"profile-settings","params":{"number":1,"settings":{"name":"test"}}}`,
		`{"action":"Restart","params":{"target":"stream"}}`, `{"action":"restart","params":{}}`,
		`{"action":"restart","params":{"target":"Stream"}}`, `{"action":"restart","params":{"target":"all"}}`,
		`{"action":"restart","params":{"target":true}}`, `{"action":"restart","params":{"target":null}}`,
		`{"action":"restart","params":{"target":"player","retry":true}}`,
		`{"action":"restart","params":{"target":"stream","target":"player"}}`,
	} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("invalid action or restart entered the queue")
		}
	}
}
