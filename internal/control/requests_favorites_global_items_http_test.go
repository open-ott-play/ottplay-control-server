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

func TestGlobalItemsHTTPBoundDescriptorsAndRetainedResults(t *testing.T) {
	for _, f := range globalItemsFixtures(t) {
		t.Run(f.op, func(t *testing.T) {
			s := newTestServer(t)
			body := globalRPC(t, f.action, f.params, "")
			expect(t, request(s, "POST", "/api/requests?device_id=first", firstToken, body, nil), 403)
			id := rpcID(t, s, body)
			queued := s.devices[0].queue[0]
			if queued.permission != "favorites_global_items."+f.op || queued.favoritesGlobalItems == nil || queued.favoritesGlobalItems.Op != f.op || queued.favorites != nil || !validFavoritesGlobalItemsEnvelope(queued.data) {
				t.Fatal("wrong queued descriptor/permission")
			}
			good := transferResponse(t, id, "ok", f.result)
			expect(t, request(s, "POST", "/api/responses", secondToken, good, nil), 404)
			bad := transferObject(t, f.result)
			bad["runtime"] = transferJSON(t, "stale")
			wrong := transferResponse(t, id, "ok", transferJSON(t, bad))
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("stale response consumed request")
			}
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, good, nil), 200)
			}
			saved := s.devices[0].results[id]
			if saved.favoritesGlobalItems == nil || saved.favoritesGlobalItems.Op != f.op {
				t.Fatal("retained descriptor lost")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, wrong, nil), 400)
			result := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, result, 200)
			if result.Body.String() != good {
				t.Fatal("retained result changed")
			}
		})
	}
}
func TestGlobalItemsHTTPNegativeAndRawBounds(t *testing.T) {
	for _, f := range globalItemsFixtures(t) {
		for _, status := range []string{"rejected", "unsupported"} {
			s := newTestServer(t)
			id := rpcID(t, s, globalRPC(t, f.action, f.params, ""))
			good := transferJSON(t, map[string]any{"version": 1, "runtime": "items-runtime", "op": f.op, "subject": "global", "error": "runtime_changed"})
			for _, field := range []string{"runtime", "op", "subject", "error"} {
				bad := transferObject(t, good)
				bad[field] = transferJSON(t, "wrong")
				expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, transferJSON(t, bad)), nil), 400)
			}
			padded := `{"id":"` + id + `","status":"` + status + `","data":` + string(good[:len(good)-1]) + strings.Repeat(" ", maxFavoritesGlobalItemsMetadata) + `}}`
			expect(t, request(s, "POST", "/api/responses", firstToken, padded, nil), 400)
			if len(s.devices[0].queue) != 1 {
				t.Fatal("invalid negative consumed request")
			}
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, good), nil), 200)
		}
		s := newTestServer(t)
		body := globalRPC(t, f.action, f.params, "")
		over := body[:len(body)-1] + strings.Repeat(" ", maxFavoritesTransferEnvelopeBytes) + "}"
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, over, nil), 400)
		duplicate := strings.Replace(body, `"subject":"global"`, `"subject":"global","subject":"global"`, 1)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, duplicate, nil), 400)
		if len(s.devices[0].queue) != 0 {
			t.Fatal("invalid raw request queued")
		}
		id := rpcID(t, s, body)
		bad := `{"id":"` + id + `","id":"` + id + `","status":"ok","data":` + string(f.result) + `}`
		expect(t, request(s, "POST", "/api/responses", firstToken, bad, nil), 400)
		s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.result), nil), 404)
	}
}
func TestGlobalItemsHTTPExactPermissionAndOwner(t *testing.T) {
	c := testConfig()
	add := func(id, token string, actions []string) config.CommandOperator {
		h := sha256.Sum256([]byte(token))
		return config.CommandOperator{ID: id, CredentialSHA256: hex.EncodeToString(h[:]), DeviceIDs: []string{"first"}, Actions: actions}
	}
	legacy, reader, other, player := strings.Repeat("l", 32), strings.Repeat("m", 32), strings.Repeat("n", 32), strings.Repeat("p", 32)
	reads := []string{"favorites_global_items.list", "favorites_global_items.current"}
	c.Operators = []config.CommandOperator{add("legacy", legacy, []string{"favorites.list", "favorites.play", "favorites_global.describe"}), add("reader", reader, reads), add("other", other, reads), add("player", player, []string{"favorites_global_items.play"})}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, f := range globalItemsFixtures(t) {
		body := globalRPC(t, f.action, f.params, "")
		expect(t, request(s, "POST", "/api/requests?device_id=first", legacy, body, nil), 403)
		token := reader
		denied := player
		if f.op == "play" {
			token, denied = player, reader
		}
		expect(t, request(s, "POST", "/api/requests?device_id=first", denied, body, nil), 403)
		expect(t, request(s, "POST", "/api/requests?device_id=second", token, body, nil), 403)
		accepted := request(s, "POST", "/api/requests?device_id=first", token, body, nil)
		expect(t, accepted, 202)
		var parsed map[string]string
		if err = json.Unmarshal(accepted.Body.Bytes(), &parsed); err != nil {
			t.Fatal(err)
		}
		id := parsed["id"]
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", f.result), nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, token, "", nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
	}
}
func TestGlobalItemsHTTPCapabilityOptionalAndStrict(t *testing.T) {
	for _, raw := range []string{`{}`, `{"favorites_global_items":{"version":1}}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", json.RawMessage(raw)), nil), 200)
	}
	for _, marker := range []string{`null`, `{"version":true}`, `{"version":1.0}`, `{"version":1,"play":true}`, `{"version":1,"version":1}`} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", json.RawMessage(`{"favorites_global_items":`+marker+`}`)), nil), 400)
	}
}
func TestGlobalItemsHTTPPlayNeverReplaysAfterRestart(t *testing.T) {
	f := globalItemsFixtures(t)[2]
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	key := "items-play-no-replay-0001"
	body := globalRPC(t, f.action, f.params, key)
	id := rpcID(t, s, body)
	if rpcID(t, s, body) != id || len(s.devices[0].queue) != 1 {
		t.Fatal("idempotent play queued twice")
	}
	changed := transferObject(t, f.params)
	changed["id"] = transferJSON(t, globalTestTransaction)
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, globalRPC(t, f.action, transferJSON(t, changed), key), nil), 409)
	if err = s.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if operationValue(t, s, key)["state"] != "unknown" {
		t.Fatal("restart invented playback outcome")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 0 {
		t.Fatal("uncertain play replayed")
	}
}
