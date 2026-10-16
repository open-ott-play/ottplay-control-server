package control

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const liveHandoff = `{"version":1,"provider":"m3u","source_id":"opaque-source","item_id":"channel-v1:1234abcd.5678abcd","kind":"live"}`

func TestHandoffStrictSchemaAndBoundResult(t *testing.T) {
	s := newTestServer(t)
	for _, bad := range []string{
		strings.Replace(liveHandoff, `"version":1`, `"version":true`, 1),
		strings.Replace(liveHandoff, `"kind":"live"`, `"kind":"vod"`, 1),
		strings.Replace(liveHandoff, `"kind":"live"`, `"kind":"archive"`, 1),
		strings.Replace(liveHandoff, `channel-v1:1234abcd.5678abcd`, `https://private.example/stream?token=secret`, 1),
		strings.Replace(liveHandoff, `channel-v1:1234abcd.5678abcd`, `channel-v1:1234ABCD.5678abcd`, 1),
		strings.Replace(liveHandoff, `"kind":"live"`, `"kind":"live","url":"https://private.example"`, 1),
	} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"playback_handoff_import","params":{"handoff":`+bad+`}}`, nil), 400)
	}
	id := rpcID(t, s, `{"action":"playback_handoff_import","params":{"handoff":`+liveHandoff+`}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{"dispatched":true,"kind":"archive"}}`, nil), 400)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{"dispatched":true,"kind":"live"}}`, nil), 200)
	id = rpcID(t, s, `{"action":"playback_handoff_export","params":{}}`)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{"handoff":`+strings.Replace(liveHandoff, `channel-v1:1234abcd.5678abcd`, `https://private.example/stream?token=secret`, 1)+`}}`, nil), 400)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{"handoff":`+liveHandoff+`}}`, nil), 200)
}
func TestSettingsComparisonRequiresBoundProviderShape(t *testing.T) {
	s := newTestServer(t)
	for _, row := range []struct {
		params string
		code   int
	}{
		{`{"provider":"m3u","number":1,"settings":{"name":"Local","playlist":"https://private.example","history_hours":0,"vportal":""}}`, 202},
		{`{"provider":"m3u","number":1,"settings":{"playlist":"https://private.example"}}`, 400},
		{`{"provider":"m3u","settings":{"playlist":"x"}}`, 400},
		{`{"provider":"plex","settings":{"server":"https://private.example","token":"secret"}}`, 202},
		{`{"provider":"plex","settings":{"server":"https://private.example","token":"secret","extra":true}}`, 400},
		{`{"provider":"stalker","number":2,"settings":{"name":"p","server":"https://private.example","mac":"02:00:00:00:00:01"}}`, 202},
	} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"settings_compare","params":`+row.params+`}`, nil), row.code)
	}
}
func TestJournalOperatorCannotReadAnotherOwnerOrExpandedAction(t *testing.T) {
	c := journalConfig(t)
	token := strings.Repeat("o", 32)
	h := sha256.Sum256([]byte(token))
	c.Operators = []config.CommandOperator{{ID: "remote", CredentialSHA256: hex.EncodeToString(h[:]), DeviceIDs: []string{"first"}, Actions: []string{"status"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	body := `{"action":"status","params":{},"idempotency_key":"operator-read-0001"}`
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	expect(t, request(s, "GET", "/api/operations?device_id=first&key=operator-read-0001", token, "", nil), 404)
	expect(t, request(s, "POST", "/api/requests?device_id=first", token, body, nil), 202)
	expect(t, request(s, "GET", "/api/operations?device_id=first&key=operator-read-0001", token, "", nil), 200)
	expect(t, request(s, "GET", "/api/operations?device_id=second", token, "", nil), 403)
	s.operators[0].actions = map[string]bool{}
	expect(t, request(s, "GET", "/api/operations?device_id=first&key=operator-read-0001", token, "", nil), 403)
}
