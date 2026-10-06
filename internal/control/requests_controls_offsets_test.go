package control

import (
	"encoding/json"
	"testing"
)

func TestChannelOffsetRequestsPreserveExactIntegerAndSingleDelivery(t *testing.T) {
	for _, offset := range []string{"1", "-1", "15", "-15", "100", "-100", "9007199254740991", "-9007199254740991"} {
		t.Run(offset, func(t *testing.T) {
			s := newTestServer(t)
			params := `{"operation":"step_channel","offset":` + offset + `}`
			id := rpcID(t, s, `{"action":"playback","params":`+params+`}`)
			if len(s.devices[0].queue) != 1 || len(s.devices[1].queue) != 0 {
				t.Fatal("one offset request must create exactly one entry owned by its device")
			}
			var envelope struct {
				Commands []json.RawMessage `json:"commands"`
				Requests []struct {
					ID     string          `json:"id"`
					Action string          `json:"action"`
					Params json.RawMessage `json:"params"`
				} `json:"requests"`
			}
			poll := request(s, "GET", "/api/webhook/commands?delivery=ack", secondToken, "", nil)
			expect(t, poll, 200)
			if err := json.Unmarshal(poll.Body.Bytes(), &envelope); err != nil || len(envelope.Commands) != 0 || len(envelope.Requests) != 0 {
				t.Fatal("relative channel request leaked to another device")
			}
			poll = request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil)
			expect(t, poll, 200)
			if err := json.Unmarshal(poll.Body.Bytes(), &envelope); err != nil || len(envelope.Commands) != 0 || len(envelope.Requests) != 1 {
				t.Fatal("relative offset must remain one playback RPC, without fallback commands")
			}
			got := envelope.Requests[0]
			if got.ID != id || got.Action != "playback" || string(got.Params) != params {
				t.Fatal("offset request changed in transit")
			}
			// The relay preserves the player's offset/target receipt; acknowledging
			// it again must not enqueue or replay this relative operation.
			ack := `{"id":"` + id + `","status":"ok","data":{"operation":"step_channel","offset":` + offset + `,"dispatched":true,"channel":{"id":"target","name":"Target","number":15}}}`
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, ack, nil), 200)
			}
			result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, result, 200)
			if result.Body.String() != ack || len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("offset receipt changed or completed request remained queued")
			}
		})
	}
}

func TestChannelOffsetRequestsRejectNoncanonicalOrAmbiguousParameters(t *testing.T) {
	params := []string{
		`{"operation":"step_channel"}`,
		`{"offset":15}`,
		`{"operation":null,"offset":15}`,
		`{"operation":"step_chanel","offset":15}`,
		`{"operation":"STEP_CHANNEL","offset":15}`,
		`{"operation":"step","offset":15}`,
		`{"operation":"+15","offset":15}`,
		`{"operation":"step_channel","steps":15}`,
		`{"operation":"step_channel","offset":15,"offset":-15}`,
		`{"operation":"step_channel","operation":"next_channel","offset":15}`,
	}
	for _, value := range []string{
		`null`, `true`, `false`, `"15"`, `"-15"`, `[]`, `{}`,
		`0`, `-0`, `0.0`, `-0.0`, `1.5`, `-1.5`, `15.0`, `1e1`, `-1e1`,
		`1.0000000000000001`, `9007199254740991.1`, `-9007199254740991.1`,
		`9007199254740992`, `-9007199254740992`, `9223372036854775807`, `-9223372036854775808`,
		`9223372036854775808`, `-9223372036854775809`, `1e309`, `NaN`, `Infinity`, `+15`, `015`,
	} {
		params = append(params, `{"operation":"step_channel","offset":`+value+`}`)
	}
	for _, extra := range []string{`"position":0`, `"position":null`, `"repeat":2`, `"key":"channel_up"`, `"query":"15"`} {
		params = append(params, `{"operation":"step_channel","offset":15,`+extra+`}`)
	}
	for _, value := range params {
		t.Run(value, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"playback","params":`+value+`}`, nil), 400)
			if len(s.devices[0].queue) != 0 || len(s.devices[1].queue) != 0 || s.bytes != 0 {
				t.Fatal("invalid offset request entered the queue")
			}
		})
	}
}

func TestChannelOffsetRequestsDoNotExpandOtherControls(t *testing.T) {
	for _, body := range []string{
		`{"action":"input","params":{"key":"step_channel"}}`,
		`{"action":"input","params":{"key":"step_channel","offset":15}}`,
		`{"action":"lifecycle","params":{"operation":"step_channel","offset":15}}`,
		`{"action":"playback","params":{"operation":"pause","offset":15}}`,
		`{"action":"command","params":{"command":"step_channel","offset":15}}`,
	} {
		t.Run(body, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("relative channel offset entered another control path")
			}
		})
	}
	s := newTestServer(t)
	expect(t, request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{"command":"step_channel","offset":15}`, nil), 400)
	if len(s.devices[0].queue) != 0 || s.bytes != 0 {
		t.Fatal("relative channel offset entered the legacy command queue")
	}
}
