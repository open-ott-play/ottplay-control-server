package control

import (
	"encoding/json"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func TestRelativeSeekRoundTripAndRetainedCorrelation(t *testing.T) {
	for _, offset := range []string{"10", "-10", "0.25", "-0.25", "9007199254740991", "-9007199254740991"} {
		t.Run(offset, func(t *testing.T) {
			s := newTestServer(t)
			params := `{"operation":"seek_relative","offset":` + offset + `}`
			id := rpcID(t, s, `{"action":"playback","params":`+params+`}`)
			if len(s.devices[0].queue) != 1 || s.devices[0].queue[0].relativeSeek == nil || s.devices[0].queue[0].permission != "playback.seek_relative" {
				t.Fatal("missing exact request correlation")
			}
			var n float64
			_ = json.Unmarshal([]byte(offset), &n)
			position := n
			clamped := false
			if n < 0 {
				position, clamped = 0, true
			}
			data, _ := json.Marshal(map[string]any{"operation": "seek_relative", "dispatched": true, "offset": n, "from": 0, "position": position, "clamped": clamped})
			ack := `{"id":"` + id + `","status":"ok","data":` + string(data) + `}`
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, ack, nil), 200)
			}
			if len(s.devices[0].queue) != 0 || s.devices[0].results[id].relativeSeek == nil {
				t.Fatal("completed correlation lost or request replayed")
			}
			wrong := `{"id":"` + id + `","status":"ok","data":{"operation":"seek_relative","dispatched":true,"offset":0,"from":0,"position":0,"clamped":false}}`
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
		})
	}
}

func TestRelativeSeekRejectsInvalidRequestsWithoutQueueing(t *testing.T) {
	for _, value := range []string{`0`, `-0`, `null`, `true`, `"10"`, `[]`, `{}`, `1e309`, `9007199254740992`, `-9007199254740992`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"playback","params":{"operation":"seek_relative","offset":`+value+`}}`, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid relative request queued")
		}
	}
	for _, params := range []string{`{"operation":"seek_relative"}`, `{"operation":"seek_relative","offset":1,"position":3}`, `{"operation":"seek_relative","offset":1,"offset":2}`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"playback","params":`+params+`}`, nil), 400)
	}
	if !config.ValidCommandAction("playback.seek_relative") || config.ValidCommandAction("playback.*") {
		t.Fatal("relative seek requires its exact operator permission")
	}
}

func TestRelativeSeekResultClampAndTypes(t *testing.T) {
	q := &relativeSeekRequest{Offset: 10}
	valid := `{"operation":"seek_relative","dispatched":true,"offset":10,"from":90,"position":99.999,"clamped":true}`
	if !validRelativeSeekResult([]byte(valid), "ok", q) {
		t.Fatal("upper clamp rejected")
	}
	for _, raw := range []string{
		`{"operation":"seek_relative","dispatched":true,"offset":10,"from":90,"position":100,"clamped":true}`,
		`{"operation":"seek_relative","dispatched":true,"offset":10,"from":90,"position":99,"clamped":false}`,
		`{"operation":"seek_relative","dispatched":true,"offset":-10,"from":90,"position":80,"clamped":false}`,
		`{"operation":"seek_relative","dispatched":true,"offset":10,"from":null,"position":10,"clamped":false}`,
		`{"operation":"seek_relative","dispatched":true,"offset":10,"from":0,"position":10,"clamped":null}`,
		`{"operation":"seek_relative","dispatched":true,"offset":10,"from":0,"position":10,"clamped":false,"secret":"x"}`,
	} {
		if validRelativeSeekResult([]byte(raw), "ok", q) {
			t.Fatal("invalid relative receipt accepted", raw)
		}
	}
	s := newTestServer(t)
	expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, `{"action":"playback","params":{"operation":"seek_relative","offset":10}}`, nil), 403)
	expect(t, request(s, "POST", "/api/webhook/commands?device_id=first", adminToken, `{"command":"seek_relative","offset":10}`, nil), 400)
}
