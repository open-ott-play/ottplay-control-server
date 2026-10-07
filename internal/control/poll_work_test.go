package control

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// Fixed clock and payloads exercise the actual ServeHTTP poll path.
var pollClock = time.Unix(1_700_000_000, 0).UTC()

const (
	pollCommandBody = `{"command":"popup_message","message":"synthetic"}`
	pollRPCBody     = `{"action":"status","params":{}}`
)

func pollServer(t *testing.T) *Server {
	t.Helper()
	s := newTestServer(t)
	s.now = func() time.Time { return pollClock }
	return s
}

func fillPollQueue(t *testing.T, s *Server, commands, rpcs int) {
	t.Helper()
	for i := 0; i < commands; i++ {
		enqueue(t, s, "first", pollCommandBody)
	}
	for i := 0; i < rpcs; i++ {
		rpcID(t, s, pollRPCBody)
	}
}

func pollPath(ack bool) string {
	if ack {
		return "/api/webhook/commands?delivery=ack"
	}
	return "/api/webhook/commands"
}

type responseBody struct {
	code int
	body string
}

func doPoll(s *Server, ack bool) responseBody {
	s.devices[0].rate.count = 0
	s.devices[0].rate.start = time.Time{}
	w := request(s, "GET", pollPath(ack), firstToken, "", nil)
	return responseBody{code: w.Code, body: w.Body.String()}
}

func TestPollDeliveryContract(t *testing.T) {
	cases := []struct {
		name     string
		commands int
		rpcs     int
	}{
		{"empty", 0, 0},
		{"commands", 4, 0},
		{"rpc", 0, 4},
		{"mixed", 2, 2},
		{"bounded_commands", 50, 0},
		{"bounded_mixed", 25, 25},
	}
	for _, tc := range cases {
		t.Run(tc.name+"/ack", func(t *testing.T) {
			assertPoll(t, tc.commands, tc.rpcs, true)
		})
		t.Run(tc.name+"/legacy", func(t *testing.T) {
			assertPoll(t, tc.commands, tc.rpcs, false)
		})
	}
	t.Run("ttl", func(t *testing.T) {
		for _, ack := range []bool{true, false} {
			s := pollServer(t)
			fillPollQueue(t, s, 1, 1)
			s.now = func() time.Time { return pollClock.Add(61 * time.Second) }
			body := doPoll(s, ack)
			if body.code != 200 || s.bytes != 0 || len(s.devices[0].queue) != 0 {
				t.Fatalf("ack=%v expiry failed code=%d bytes=%d queue=%d body=%s", ack, body.code, s.bytes, len(s.devices[0].queue), body.body)
			}
			if ack {
				assertAckShape(t, body.body, 0, 0, float64(pollClock.Add(61*time.Second).UnixNano())/1e9, nil)
			} else if strings.TrimSpace(body.body) != "[]" {
				t.Fatalf("legacy expiry body %s", body.body)
			}
		}
	})
}

func assertPoll(t *testing.T, commands, rpcs int, ack bool) {
	t.Helper()
	s := pollServer(t)
	fillPollQueue(t, s, commands, rpcs)
	d := s.devices[0]
	before := append([]entry(nil), d.queue...)
	beforeBytes := s.bytes
	var backing []entry
	if cap(d.queue) > 0 {
		// Check occupied slots only; spare capacity is already zero.
		backing = d.queue[:len(d.queue)]
	}
	d.lastSeen = time.Time{}
	body := doPoll(s, ack)
	if body.code != 200 {
		t.Fatalf("status %d body %s", body.code, body.body)
	}
	if !d.lastSeen.Equal(pollClock) {
		t.Fatalf("lastSeen %s", d.lastSeen)
	}
	if ack {
		if s.bytes != beforeBytes || len(d.queue) != len(before) {
			t.Fatalf("ack changed queue bytes %d->%d len %d->%d", beforeBytes, s.bytes, len(before), len(d.queue))
		}
		for i := range before {
			if d.queue[i].id != before[i].id || d.queue[i].rpc != before[i].rpc || string(d.queue[i].data) != string(before[i].data) {
				t.Fatalf("ack reordered or rewrote slot %d", i)
			}
		}
		assertAckShape(t, body.body, commands, rpcs, float64(pollClock.UnixNano())/1e9, before)
		return
	}
	var got []map[string]any
	if err := json.Unmarshal([]byte(body.body), &got); err != nil {
		t.Fatalf("legacy json: %v body %s", err, body.body)
	}
	if got == nil || len(got) != commands {
		t.Fatalf("legacy commands %d want %d", len(got), commands)
	}
	for i, item := range got {
		if item["command"] != "popup_message" || item["message"] != "synthetic" {
			t.Fatalf("legacy item %d %#v", i, item)
		}
	}
	for i := 1; i < len(got); i++ {
		if got[i-1]["ts"].(float64) >= got[i]["ts"].(float64) {
			t.Fatal("legacy command order changed")
		}
	}
	if len(d.queue) != rpcs {
		t.Fatalf("legacy retained %d rpc, want %d", len(d.queue), rpcs)
	}
	wantBytes := 0
	rpcIndex := 0
	for _, e := range before {
		if !e.rpc {
			continue
		}
		wantBytes += len(e.data)
		if d.queue[rpcIndex].id != e.id || string(d.queue[rpcIndex].data) != string(e.data) {
			t.Fatalf("rpc slot %d not retained in order", rpcIndex)
		}
		rpcIndex++
	}
	if s.bytes != wantBytes {
		t.Fatalf("bytes %d want %d", s.bytes, wantBytes)
	}
	for i, e := range backing {
		if e.id != "" || e.data != nil || !e.expires.IsZero() || e.rpc {
			t.Fatalf("legacy poll retained data in occupied slot %d", i)
		}
	}
}

func assertAckShape(t *testing.T, body string, commands, rpcs int, serverTime float64, expected []entry) {
	t.Helper()
	var raw map[string]json.RawMessage
	if err := json.Unmarshal([]byte(body), &raw); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"commands", "requests", "request_protocol", "server_time"} {
		if _, ok := raw[key]; !ok {
			t.Fatalf("missing %s in %s", key, body)
		}
	}
	if len(raw) != 4 {
		t.Fatalf("unexpected ack keys %s", body)
	}
	var commandsOut []map[string]any
	var requestsOut []map[string]any
	var protocol int
	var clock float64
	if json.Unmarshal(raw["commands"], &commandsOut) != nil || commandsOut == nil || len(commandsOut) != commands {
		t.Fatalf("commands shape %s", raw["commands"])
	}
	if json.Unmarshal(raw["requests"], &requestsOut) != nil || requestsOut == nil || len(requestsOut) != rpcs {
		t.Fatalf("requests shape %s", raw["requests"])
	}
	if json.Unmarshal(raw["request_protocol"], &protocol) != nil || protocol != 1 {
		t.Fatalf("protocol %s", raw["request_protocol"])
	}
	if json.Unmarshal(raw["server_time"], &clock) != nil || clock != serverTime {
		t.Fatalf("server_time %s want %v", raw["server_time"], serverTime)
	}
	for i, item := range commandsOut {
		if item["command"] != "popup_message" {
			t.Fatalf("ack command %d %#v", i, item)
		}
	}
	for i := 1; i < len(commandsOut); i++ {
		if commandsOut[i-1]["ts"].(float64) >= commandsOut[i]["ts"].(float64) {
			t.Fatal("ack command order changed")
		}
	}
	for i, item := range requestsOut {
		if item["action"] != "status" {
			t.Fatalf("ack request %d %#v", i, item)
		}
	}
	if len(expected) != commands+rpcs {
		t.Fatal("expected queue does not match response counts")
	}
	commandIndex, requestIndex := 0, 0
	for _, e := range expected {
		if e.rpc {
			if requestsOut[requestIndex]["id"] != e.id {
				t.Fatalf("ack request order changed at index %d", requestIndex)
			}
			requestIndex++
		} else {
			if commandsOut[commandIndex]["id"] != e.id {
				t.Fatalf("ack command order changed at index %d", commandIndex)
			}
			commandIndex++
		}
	}
}
