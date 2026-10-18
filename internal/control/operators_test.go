package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func TestOperatorScopesEveryCommandRouteAndReceipt(t *testing.T) {
	token := strings.Repeat("o", 32)
	sum := sha256.Sum256([]byte(token))
	c := testConfig()
	c.Operators = []config.CommandOperator{{ID: "remote", CredentialSHA256: hex.EncodeToString(sum[:]), DeviceIDs: []string{"first"}, Actions: []string{"status", "input.channel_up", "command.set_volume"}}}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range []struct {
		method, path, body string
		code               int
	}{
		{"POST", "/api/requests?device_id=first", `{"action":"status","params":{}}`, 202},
		{"POST", "/api/requests?device_id=second", `{"action":"status","params":{}}`, 403},
		{"POST", "/api/requests?device_id=first", `{"action":"input","params":{"key":"channel_up"}}`, 202},
		{"POST", "/api/requests?device_id=first", `{"action":"input","params":{"key":"settings"}}`, 403},
		{"POST", "/api/requests?device_id=first", `{"action":"command","params":{"command":"exit_player"}}`, 403},
		{"POST", "/api/requests?device_id=first", `{"action":"command","params":{"command":"set_volume","volume":10}}`, 202},
		{"POST", "/api/webhook/commands?device_id=first", `{"command":"set_volume","volume":10}`, 202},
		{"POST", "/api/webhook/commands?device_id=second", `{"command":"set_volume","volume":10}`, 403},
		{"POST", "/api/webhook/commands?device_id=first", `{"command":"exit_player"}`, 403},
		{"GET", "/api/devices", "", 403}, {"GET", "/api/debug", "", 403},
		{"GET", "/api/webhook/commands?device_id=first", "", 403},
		{"POST", "/api/responses", `{"id":"` + strings.Repeat("0", 32) + `","status":"ok","data":{}}`, 403},
	} {
		t.Run(row.path+row.body, func(t *testing.T) { expect(t, request(s, row.method, row.path, token, row.body, nil), row.code) })
	}
	adminID := rpcID(t, s, `{"action":"status","params":{}}`)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+adminID, token, "", nil), 403)
	own := request(s, "POST", "/api/requests?device_id=first", token, `{"action":"status","params":{}}`, nil)
	expect(t, own, 202)
	var result map[string]string
	_ = json.Unmarshal(own.Body.Bytes(), &result)
	id := result["id"]
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, token, "", nil), 202)
	expect(t, request(s, "POST", "/api/responses", firstToken, `{"id":"`+id+`","status":"ok","data":{}}`, nil), 200)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, token, "", nil), 200)
	s.operators[0].actions = map[string]bool{}
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, token, "", nil), 403)
}

func TestOperatorConfigurationRejectsAmbiguousAuthority(t *testing.T) {
	token := strings.Repeat("o", 32)
	sum := sha256.Sum256([]byte(token))
	base := config.CommandOperator{ID: "remote", CredentialSHA256: hex.EncodeToString(sum[:]), DeviceIDs: []string{"first"}, Actions: []string{"status"}}
	for _, edit := range []func(*config.CommandOperator){
		func(o *config.CommandOperator) { o.Actions = []string{"input"} },
		func(o *config.CommandOperator) { o.Actions = []string{"*"} },
		func(o *config.CommandOperator) { o.Actions = []string{"status", "status"} },
		func(o *config.CommandOperator) { o.DeviceIDs = []string{"missing"} },
		func(o *config.CommandOperator) { o.DeviceIDs = nil },
		func(o *config.CommandOperator) {
			h := sha256.Sum256([]byte(adminToken))
			o.CredentialSHA256 = hex.EncodeToString(h[:])
		},
	} {
		c := testConfig()
		o := base
		edit(&o)
		c.Operators = []config.CommandOperator{o}
		if _, err := New(c); err == nil {
			t.Fatal("accepted invalid scope")
		}
	}
}
