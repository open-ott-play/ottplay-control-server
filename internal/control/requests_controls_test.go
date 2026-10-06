package control

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestControlRequestsRoundTrip(t *testing.T) {
	bodies := []string{`{"action":"capabilities","params":{}}`}
	for _, op := range []string{"restart_stream", "reload_player", "restart_app", "standby", "wake", "exit_app", "reboot_device"} {
		bodies = append(bodies, `{"action":"lifecycle","params":{"operation":"`+op+`"}}`)
	}
	for _, key := range []string{"up", "down", "left", "right", "ok", "back", "menu", "settings", "channels", "guide", "info", "channel_up", "channel_down", "volume_up", "volume_down", "mute", "play_pause", "audio", "aspect", "zoom", "pip", "fullscreen"} {
		bodies = append(bodies, `{"action":"input","params":{"key":"`+key+`"}}`)
	}
	for _, params := range []string{`{"operation":"pause"}`, `{"operation":"resume"}`, `{"operation":"previous_channel"}`, `{"operation":"next_channel"}`, `{"operation":"seek","position":0}`, `{"operation":"seek","position":12.5}`} {
		bodies = append(bodies, `{"action":"playback","params":`+params+`}`)
	}
	for _, body := range bodies {
		t.Run(body, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, body)
			poll := request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
			expect(t, poll, 200)
			var envelope struct {
				Requests []map[string]json.RawMessage `json:"requests"`
			}
			if err := json.Unmarshal(poll.Body.Bytes(), &envelope); err != nil || len(envelope.Requests) != 1 {
				t.Fatal("request was not delivered")
			}
			original, _ := decodeObject([]byte(body))
			for _, key := range []string{"action", "params"} {
				var before, after any
				if json.Unmarshal(original[key], &before) != nil || json.Unmarshal(envelope.Requests[0][key], &after) != nil || !reflect.DeepEqual(before, after) {
					t.Fatal("control request changed in transit")
				}
			}
			// Acceptance is intent, never execution. The relay must preserve it
			// verbatim and deliver the response before an unloading effect.
			ack := `{"id":"` + id + `","status":"ok","data":{"accepted":true,"dispatched":false,"effect":"lifecycle-after-ack"}}`
			expect(t, request(s, "POST", "/api/responses", firstToken, ack, nil), 200)
			result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, result, 200)
			if result.Body.String() != ack || len(s.devices[0].queue) != 0 {
				t.Fatal("acceptance receipt changed or remained queued")
			}
		})
	}
}

func TestControlRequestsRejectAmbiguityAndPrivilegeExpansion(t *testing.T) {
	bodies := []string{
		`{"action":"capabilities","params":{"all":true}}`,
		`{"action":"lifecycle","params":{}}`,
		`{"action":"lifecycle","params":{"operation":"reboot"}}`,
		`{"action":"lifecycle","params":{"operation":"exit_app","force":true}}`,
		`{"action":"lifecycle","params":{"operation":"wake","operation":"exit_app"}}`,
		`{"action":"lifecycle","params":{"operation":"EXIT_APP"}}`,
		`{"action":"lifecycle","params":{"operation":"reboot_device","command":"sudo reboot"}}`,
		`{"action":"input","params":{}}`,
		`{"action":"input","params":{"key":"power"}}`,
		`{"action":"input","params":{"key":"exit"}}`,
		`{"action":"input","params":{"key":"stop"}}`,
		`{"action":"input","params":{"key":"1"}}`,
		`{"action":"input","params":{"text":"1234"}}`,
		`{"action":"input","params":{"keyCode":13}}`,
		`{"action":"input","params":{"key":"ok","repeat":3}}`,
		`{"action":"input","params":{"key":"ok","key":"back"}}`,
		`{"action":"playback","params":{"operation":"seek"}}`,
		`{"action":"playback","params":{"operation":"pause","position":0}}`,
		`{"action":"playback","params":{"operation":"play"}}`,
		`{"action":"playback","params":{"operation":"seek","position":0,"relative":true}}`,
		`{"action":"playback","params":{"operation":"seek","position":0,"position":1}}`,
		`{"action":"playback","params":{"operation":null}}`,
		`{"action":"playback","params":{"operation":"previous_chanel"}}`,
		`{"action":"playback","params":{"operation":"next_chanel"}}`,
	}
	for _, op := range []string{"previous_channel", "next_channel"} {
		for _, extra := range []string{`"position":0`, `"position":null`, `"repeat":2`, `"key":"channel_up"`, `"operation":"pause"`} {
			bodies = append(bodies, `{"action":"playback","params":{"operation":"`+op+`",`+extra+`}}`)
		}
		bodies = append(bodies, `{"action":"input","params":{"key":"`+op+`"}}`)
	}
	for _, alias := range []string{"prev", "previous", "next", "PREVIOUS_CHANNEL", "NEXT_CHANNEL"} {
		bodies = append(bodies, `{"action":"playback","params":{"operation":"`+alias+`"}}`)
		bodies = append(bodies, `{"action":"input","params":{"key":"`+alias+`"}}`)
	}
	for _, value := range []string{`null`, `true`, `"1"`, `-1`, `1e309`, `9007199254740992`, `[]`, `{}`} {
		bodies = append(bodies, `{"action":"playback","params":{"operation":"seek","position":`+value+`}}`)
	}
	for _, action := range []string{"capabilities", "lifecycle", "input", "playback"} {
		for _, params := range []string{`null`, `[]`, `true`, `"value"`} {
			bodies = append(bodies, `{"action":"`+action+`","params":`+params+`}`)
		}
	}
	for _, body := range bodies {
		t.Run(body, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("invalid control request entered the queue")
			}
		})
	}
}

func TestControlRequestsKeepOperatorAndDeviceBoundaries(t *testing.T) {
	for _, body := range []string{`{"action":"capabilities","params":{}}`, `{"action":"lifecycle","params":{"operation":"reboot_device"}}`, `{"action":"input","params":{"key":"ok"}}`, `{"action":"playback","params":{"operation":"pause"}}`, `{"action":"playback","params":{"operation":"previous_channel"}}`, `{"action":"playback","params":{"operation":"next_channel"}}`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
		expect(t, request(s, "POST", "/api/requests?device_id=first", "", body, nil), 401)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("unauthorized control queued")
		}
		id := rpcID(t, s, body)
		expect(t, request(s, "POST", "/api/responses", secondToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 404)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("another device acknowledged the control")
		}
	}
}

func TestChannelStepRequestsDoNotExpandLegacyCommands(t *testing.T) {
	for _, op := range []string{"previous_channel", "next_channel"} {
		t.Run(op, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{"command":"`+op+`"}`, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("modern playback operation entered the legacy command queue")
			}
		})
	}
}
