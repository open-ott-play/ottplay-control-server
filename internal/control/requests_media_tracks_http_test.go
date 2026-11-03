package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func TestMediaTracksHTTPBoundAndRetainedResponses(t *testing.T) {
	for _, f := range mediaTracksFixture(t).Valid {
		t.Run(f.Name, func(t *testing.T) {
			s := newTestServer(t)
			body := globalRPC(t, "media_tracks", f.Params, "")
			expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
			id := rpcID(t, s, body)
			q := parseMediaTracksRequest(f.Params)
			queued := s.devices[0].queue[0]
			if queued.mediaTracks == nil || *queued.mediaTracks != *q || queued.permission != "media_tracks."+q.Op {
				t.Fatal("request lost its binding or exact permission")
			}
			good := transferResponse(t, id, f.Status, f.Data)
			expect(t, request(s, "POST", "/api/responses", secondToken, good, nil), 404)
			bad := transferObject(t, f.Data)
			bad["runtime"] = transferJSON(t, "wrong-runtime")
			wrong := transferResponse(t, id, f.Status, transferJSON(t, bad))
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("invalid response consumed or stored request")
			}
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
			}
			retained := s.devices[0].results[id]
			if retained.mediaTracks == nil || *retained.mediaTracks != *q {
				t.Fatal("retained response lost binding")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != good || s.resultBytes != len(good) || s.bytes != 0 {
				t.Fatal("result changed, duplicated or remained queued")
			}
		})
	}
}

func TestMediaTracksHTTPMalformedRequestsAndResponses(t *testing.T) {
	f := mediaTracksFixture(t)
	for _, row := range f.InvalidRequests {
		t.Run(row.Name, func(t *testing.T) {
			s := newTestServer(t)
			expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, "media_tracks", row.Params, ""), nil), 400)
			if len(s.devices[0].queue) != 0 || s.bytes != 0 {
				t.Fatal("malformed request entered queue")
			}
		})
	}
	for _, row := range f.InvalidResults {
		t.Run(row.Name, func(t *testing.T) {
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, "media_tracks", row.Params, ""))
			got := request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, row.Status, row.Data), nil)
			expect(t, got, 400)
			if len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0 || s.resultBytes != 0 {
				t.Fatal("malformed result consumed or stored request")
			}
			if strings.Contains(got.Body.String(), "fixture") {
				t.Fatal("error reflected private result content")
			}
		})
	}
}

func TestMediaTracksHTTPBudgetsDuplicatesAndExpiry(t *testing.T) {
	f := mediaTracksFixture(t).named(t, "select-audio-requested")
	for _, size := range []int{maxMediaTracksRequestBytes, maxMediaTracksRequestBytes + 1} {
		s := newTestServer(t)
		body := globalRPC(t, "media_tracks", f.Params, "")
		body = body[:len(body)-1] + strings.Repeat(" ", size-len(body)) + "}"
		code := 202
		if size > maxMediaTracksRequestBytes {
			code = 400
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), code)
	}
	s := newTestServer(t)
	body := globalRPC(t, "media_tracks", f.Params, "")
	duplicate := strings.Replace(body, `"action":`, `"action":"media_tracks","action":`, 1)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, duplicate, nil), 400)
	id := rpcID(t, s, body)
	duplicateResponse := `{"id":"` + id + `","status":"ok","status":"ok","data":` + string(f.Data) + `}`
	expect(t, request(s, "POST", "/api/responses", firstToken, duplicateResponse, nil), 400)
	over := `{"id":"` + id + `","status":"ok","data":` + string(f.Data[:len(f.Data)-1]) + strings.Repeat(" ", maxMediaTracksResultBytes) + `}}`
	expect(t, request(s, "POST", "/api/responses", firstToken, over, nil), 400)
	if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
		t.Fatal("invalid result consumed request")
	}
	s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.Data), nil), 404)
	if len(s.devices[0].results) != 0 {
		t.Fatal("expired request result was stored")
	}
}

func TestMediaTracksHTTPCapabilityOptionalAndStrict(t *testing.T) {
	for _, data := range []string{`{}`, `{"player":{"runtime":"native-example"},"media_tracks":{"version":1}}`, `{"native_playback_diagnostics":{"version":1}}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := transferResponse(t, id, "ok", json.RawMessage(data))
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		if got.Body.String() != body {
			t.Fatal("controller manufactured or changed capabilities")
		}
	}
	for _, marker := range []string{`null`, `true`, `[]`, `{}`, `{"version":true}`, `{"version":null}`, `{"version":1.0}`, `{"version":1e0}`, `{"version":"1"}`, `{"version":2}`, `{"version":1,"version":1}`, `{"version":1,"url":"https://example.invalid/fixture"}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		body := `{"id":"` + id + `","status":"ok","data":{"player":{"runtime":"native-example"},"media_tracks":` + marker + `}}`
		expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 400)
		if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
			t.Fatal("invalid capability closed request")
		}
	}
	for _, runtime := range []any{nil, "", "Native_Example", strings.Repeat("a", 65), "https://example.invalid/private"} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		data := map[string]any{"player": map[string]any{"runtime": runtime}, "media_tracks": map[string]any{"version": 1}, "native_playback_diagnostics": map[string]any{"version": 1}}
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, data)), nil), 400)
	}
}

func TestMediaTracksHTTPExactOperatorAndDeviceScope(t *testing.T) {
	f := mediaTracksFixture(t)
	for _, op := range []string{"list", "select"} {
		t.Run(op, func(t *testing.T) {
			c := testConfig()
			owner, other := strings.Repeat("q", 32), strings.Repeat("v", 32)
			for _, row := range []struct{ id, token string }{{"owner", owner}, {"other", other}} {
				hash := sha256.Sum256([]byte(row.token))
				c.Operators = append(c.Operators, config.CommandOperator{ID: row.id, CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: []string{"media_tracks." + op}})
			}
			s, err := New(c)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			good, denied := f.named(t, "list-mixed"), f.named(t, "select-audio-requested")
			if op == "select" {
				good, denied = denied, good
			}
			body := globalRPC(t, "media_tracks", good.Params, "")
			expect(t, request(s, "POST", "/api/requests?device_id=second", owner, body, nil), 403)
			expect(t, request(s, "POST", "/api/requests?device_id=first", owner, globalRPC(t, "media_tracks", denied.Params, ""), nil), 403)
			accepted := request(s, "POST", "/api/requests?device_id=first", owner, body, nil)
			expect(t, accepted, 202)
			var queued map[string]string
			if err := json.Unmarshal(accepted.Body.Bytes(), &queued); err != nil {
				t.Fatal(err)
			}
			id := queued["id"]
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, good.Status, good.Data), nil), 200)
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, owner, "", nil), 200)
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
		})
	}
}

func TestMediaTracksHTTPDoesNotUseLegacyQueueOrAcknowledgements(t *testing.T) {
	f := mediaTracksFixture(t).named(t, "select-audio-requested")
	s := newTestServer(t)
	id := rpcID(t, s, globalRPC(t, "media_tracks", f.Params, ""))
	if len(commands(t, request(s, "GET", "/api/webhook/commands", firstToken, "", nil), false)) != 0 {
		t.Fatal("typed track request leaked into legacy commands")
	}
	expect(t, request(s, "POST", "/api/webhook/commands/ack", firstToken, `{"ids":["`+id+`"]}`, nil), 200)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("legacy acknowledgement consumed typed track request")
	}
	body := transferResponse(t, id, "ok", f.Data)
	expect(t, request(s, "POST", "/api/responses", adminToken, body, nil), 403)
	expect(t, request(s, "POST", "/api/responses?device_id=second", firstToken, body, nil), 403)
	expect(t, request(s, "POST", "/api/responses", firstToken, body, nil), 200)
}
