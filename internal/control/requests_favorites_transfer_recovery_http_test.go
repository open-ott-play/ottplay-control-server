package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func TestFavoritesRecoveryHTTPRetainsExactModeAndAcknowledgementDescriptor(t *testing.T) {
	for _, mode := range []string{"list", "inspect", "acknowledge"} {
		t.Run(mode, func(t *testing.T) {
			s := newTestServer(t)
			fixture := recoveryFixture(t, mode, false)
			id := rpcID(t, s, transferRPC(t, fixture.Request, ""))
			queued := s.devices[0].queue[0]
			if queued.permission != "favorites_transfer.recovery" || queued.favoritesTransfer == nil || queued.favoritesTransfer.Recovery == nil || queued.favoritesTransfer.Recovery.Mode != mode || !validFavoritesTransferEnvelope(queued.data) {
				t.Fatal("recovery lost exact permission, mode or envelope binding")
			}
			changed := transferObject(t, fixture.Result)
			changed["mode"] = json.RawMessage(`"wrong-mode"`)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, changed)), nil), 400)
			if len(s.devices[0].queue) != 1 {
				t.Fatal("bad mode consumed queued recovery")
			}
			response := transferResponse(t, id, "ok", fixture.Result)
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			}
			retained := s.devices[0].results[id]
			if retained.favoritesTransfer == nil || retained.favoritesTransfer.Recovery == nil || retained.favoritesTransfer.Recovery.Mode != mode || retained.permission != "favorites_transfer.recovery" {
				t.Fatal("retained receipt lost its strict recovery descriptor")
			}
			if mode == "acknowledge" && retained.favoritesTransfer.Recovery.AfterLedgerSHA == "" {
				t.Fatal("completed acknowledgement lost its after-image commitment")
			}
			changed = transferObject(t, fixture.Result)
			changed["runtime"] = json.RawMessage(`"late-other-runtime"`)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, changed)), nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response || len(s.devices[0].queue) != 0 {
				t.Fatal("late response changed retained recovery evidence")
			}
		})
	}
}

func TestFavoritesRecoveryHTTPPermissionDoesNotInheritCommitOrProvision(t *testing.T) {
	c := testConfig()
	operator := func(id, token string, actions []string) config.CommandOperator {
		digest := sha256.Sum256([]byte(token))
		return config.CommandOperator{ID: id, CredentialSHA256: hex.EncodeToString(digest[:]), DeviceIDs: []string{"first"}, Actions: actions}
	}
	legacy, owner, other := strings.Repeat("j", 32), strings.Repeat("k", 32), strings.Repeat("l", 32)
	c.Operators = []config.CommandOperator{
		operator("transfer-only", legacy, []string{"favorites.list", "favorites_transfer.commit", "favorites_transfer.prepare", "favorites_transfer.provision"}),
		operator("recovery-one", owner, []string{"favorites_transfer.recovery"}),
		operator("recovery-two", other, []string{"favorites_transfer.recovery"}),
	}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, mode := range []string{"list", "inspect", "acknowledge"} {
		fixture := recoveryFixture(t, mode, false)
		body := transferRPC(t, fixture.Request, "")
		expect(t, request(s, "POST", "/api/requests?device_id=first", legacy, body, nil), 403)
		expect(t, request(s, "POST", "/api/requests?device_id=second", owner, body, nil), 403)
		accepted := request(s, "POST", "/api/requests?device_id=first", owner, body, nil)
		expect(t, accepted, 202)
		var result map[string]string
		if json.Unmarshal(accepted.Body.Bytes(), &result) != nil {
			t.Fatal("missing queued recovery ID")
		}
		id := result["id"]
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", fixture.Result), nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, owner, "", nil), 200)
		expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, other, "", nil), 403)
	}
	commit := readFavoritesTransferContract(t).Fixtures["commit"]
	expect(t, request(s, "POST", "/api/requests?device_id=first", owner, transferRPC(t, commit.Request, ""), nil), 403)
}

func TestFavoritesRecoveryHTTPIndependentSiblingCapability(t *testing.T) {
	for _, tc := range []struct {
		data string
		code int
	}{
		{`{}`, 200}, // Legacy players remain valid.
		{`{"favorites_transfer_recovery":{"version":1}}`, 200}, // Global recovery without current-source transfer.
		{`{"favorites_transfer_recovery":null}`, 400},
		{`{"favorites_transfer_recovery":{"version":1.0}}`, 400},
		{`{"favorites_transfer_recovery":{"version":1,"reset":true}}`, 400},
		{`{"favorites_transfer_recovery":{"version":1,"version":1}}`, 400},
	} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", json.RawMessage(tc.data)), nil), tc.code)
		if tc.code == 400 && (len(s.devices[0].queue) != 1 || len(s.devices[0].results) != 0) {
			t.Fatal("malformed capability consumed request")
		}
	}
}

func TestFavoritesRecoveryHTTPNegativeAckAndJournalNeverAuthorizeReplay(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	fixture := recoveryFixture(t, "acknowledge", false)
	body := transferRPC(t, fixture.Request, "recovery-ack-replay-0001")
	id := rpcID(t, s, body)
	if again := rpcID(t, s, body); again != id || len(s.devices[0].queue) != 1 {
		t.Fatal("repeated transport ID created a second acknowledgement")
	}
	negative := map[string]any{"version": 1, "runtime": "wrong-runtime", "op": "recovery", "error": "storage_unavailable"}
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "rejected", transferJSON(t, negative)), nil), 400)
	if len(s.devices[0].queue) != 1 {
		t.Fatal("uncorrelated error consumed acknowledgement")
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if operationValue(t, s, "recovery-ack-replay-0001")["state"] != "unknown" {
		t.Fatal("restart invented a known acknowledgement")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 0 {
		t.Fatal("restart replayed an uncertain acknowledgement")
	}
}
