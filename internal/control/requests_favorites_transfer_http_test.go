package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func transferRPC(t *testing.T, params json.RawMessage, key string) string {
	t.Helper()
	body := map[string]any{"action": "favorites_transfer", "params": params}
	if key != "" {
		body["idempotency_key"] = key
	}
	return string(transferJSON(t, body))
}

func transferResponse(t *testing.T, id, status string, data json.RawMessage) string {
	t.Helper()
	return string(transferJSON(t, map[string]any{"id": id, "status": status, "data": data}))
}

func TestFavoritesTransferHTTPRoundTripRetainsEveryRequestDescriptor(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for _, op := range []string{"snapshot", "begin", "append", "preview", "commit", "status", "abort"} {
		t.Run(op, func(t *testing.T) {
			s := newTestServer(t)
			fixture := c.Fixtures[op]
			body := transferRPC(t, fixture.Request, "")
			for _, auth := range []struct {
				token string
				code  int
			}{{"", 401}, {firstToken, 403}} {
				expect(t, request(s, "POST", "/api/requests?device_id=first", auth.token, body, nil), auth.code)
			}
			id := rpcID(t, s, body)
			if len(s.devices[0].queue) != 1 || s.devices[0].queue[0].favoritesTransfer == nil || s.devices[0].queue[0].favoritesTransfer.Op != op || s.devices[0].queue[0].permission != "favorites_transfer."+op {
				t.Fatal("request descriptor/exact operation scope was not retained")
			}
			if !validFavoritesTransferEnvelope(s.devices[0].queue[0].data) {
				t.Fatal("final queued bytes were not validated")
			}
			response := transferResponse(t, id, "ok", fixture.Response)
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			}
			retained := s.devices[0].results[id]
			if retained.favoritesTransfer == nil || retained.favoritesTransfer.Op != op || retained.permission != "favorites_transfer."+op || len(s.devices[0].queue) != 0 {
				t.Fatal("completed receipt lost its original request descriptor")
			}
			stale := transferObject(t, fixture.Response)
			stale["runtime"] = json.RawMessage(`"different-runtime"`)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, stale)), nil), 400)
			negative := map[string]any{"version": 1, "runtime": "different-runtime", "op": op, "error": "runtime_changed"}
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "rejected", transferJSON(t, negative)), nil), 400)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response || string(retained.data) != response || s.bytes != 0 {
				t.Fatal("duplicate/stale result replaced the retained outcome")
			}
		})
	}
}

func TestFavoritesTransferHTTPNegativeResponsesAreFencedBeforeConsumption(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for _, status := range []string{"rejected", "unsupported"} {
		s := newTestServer(t)
		id := rpcID(t, s, transferRPC(t, c.ErrorFixture.Request, ""))
		for field, value := range map[string]string{"runtime": `"stale"`, "op": `"commit"`, "error": `"fixture-secret"`, "outcome": `"not_applied"`} {
			changed := transferObject(t, c.ErrorFixture.Response)
			changed[field] = json.RawMessage(value)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, transferJSON(t, changed)), nil), 400)
			if len(s.devices[0].queue) != 1 || s.resultBytes != 0 {
				t.Fatal("unbound negative result consumed a request")
			}
		}
		response := transferResponse(t, id, status, c.ErrorFixture.Response)
		for range 2 {
			expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
		}
		if s.devices[0].results[id].favoritesTransfer == nil {
			t.Fatal("negative receipt lost correlation descriptor")
		}
		bad := transferObject(t, c.ErrorFixture.Response)
		bad["runtime"] = json.RawMessage(`"late-stale"`)
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, status, transferJSON(t, bad)), nil), 400)
	}
}

func TestFavoritesTransferHTTPOperationPermissionsDoNotInheritLegacyFavorites(t *testing.T) {
	c := testConfig()
	operator := func(id, token string, actions []string) config.CommandOperator {
		digest := sha256.Sum256([]byte(token))
		return config.CommandOperator{ID: id, CredentialSHA256: hex.EncodeToString(digest[:]), DeviceIDs: []string{"first"}, Actions: actions}
	}
	legacy, first, second := strings.Repeat("l", 32), strings.Repeat("m", 32), strings.Repeat("n", 32)
	c.Operators = []config.CommandOperator{
		operator("legacy", legacy, []string{"favorites.list", "favorites.add", "favorites.play", "favorites.remove"}),
		operator("transfer-one", first, []string{"favorites_transfer.snapshot"}),
		operator("transfer-two", second, []string{"favorites_transfer.snapshot"}),
	}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	fixture := readFavoritesTransferContract(t)
	snapshot := transferRPC(t, fixture.Fixtures["snapshot"].Request, "")
	expect(t, request(s, "POST", "/api/requests?device_id=first", legacy, snapshot, nil), 403)
	expect(t, request(s, "POST", "/api/requests?device_id=second", first, snapshot, nil), 403)
	for _, op := range []string{"begin", "append", "preview", "commit", "status", "abort"} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", first, transferRPC(t, fixture.Fixtures[op].Request, ""), nil), 403)
	}
	accepted := request(s, "POST", "/api/requests?device_id=first", first, snapshot, nil)
	expect(t, accepted, 202)
	var receipt map[string]string
	_ = json.Unmarshal(accepted.Body.Bytes(), &receipt)
	id := receipt["id"]
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, second, "", nil), 403)
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", fixture.Fixtures["snapshot"].Response), nil), 200)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, first, "", nil), 200)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, second, "", nil), 403)
	s.operators[1].actions = map[string]bool{}
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, first, "", nil), 403)
	// The old command family remains usable only through its own permissions.
	expect(t, request(s, "POST", "/api/requests?device_id=first", legacy, string(favoritesContract(t)["list_request"]), nil), 202)
	if s.devices[0].queue[0].favorites == nil || s.devices[0].queue[0].favoritesTransfer != nil {
		t.Fatal("legacy favorites was reinterpreted as transfer")
	}
	for _, action := range []string{"favorites_transfer", "favorites_transfer.*", "favorites_transfer.bind", "favorites_transfer.bindingcatalog", "favorites_transfer.unknown"} {
		if config.ValidCommandAction(action) {
			t.Fatal("unscoped/unfrozen action allowed", action)
		}
	}
	for _, op := range []string{"snapshot", "begin", "append", "preview", "commit", "status", "abort"} {
		if !config.ValidCommandAction("favorites_transfer." + op) {
			t.Fatal("exact operation permission missing", op)
		}
	}
}

func TestFavoritesTransferHTTPFinalSizeCheckPrecedesJournalAndQueue(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	now := time.Unix(1800000000, 0)
	s.now = func() time.Time { return now }
	fixture := readFavoritesTransferContract(t)
	chunk := bytes.Repeat([]byte{0xff}, maxFavoritesTransferChunkBytes)
	digest := sha256.Sum256(chunk)
	encoded := base64.StdEncoding.EncodeToString(chunk)
	key := "transfer-boundary-0001"
	body := ""
	for escaped := 0; escaped <= len(encoded); escaped++ {
		params := transferObject(t, fixture.Fixtures["append"].Request)
		params["data_base64"] = json.RawMessage(`"` + strings.Replace(encoded, "/", `\u002f`, escaped) + `"`)
		params["chunk_sha256"] = transferJSON(t, hex.EncodeToString(digest[:]))
		candidate := transferRPC(t, transferJSON(t, params), key)
		if len(candidate) > MaxBodyBytes {
			break
		}
		queued := transferObject(t, json.RawMessage(candidate))
		delete(queued, "idempotency_key")
		queued["id"] = transferJSON(t, strings.Repeat("0", 32))
		queued["expires_at"] = transferJSON(t, float64(now.Add(s.ttl).UnixNano())/1e9)
		if len(transferJSON(t, queued)) > maxFavoritesTransferEnvelopeBytes {
			if parseFavoritesTransferRequest(params) == nil {
				t.Fatal("boundary fixture invalid before envelope growth")
			}
			body = candidate
			break
		}
	}
	if body == "" {
		t.Fatal("no valid input whose final envelope exceeds the limit")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 413)
	if len(s.devices[0].queue) != 0 || s.bytes != 0 || len(s.journal.records) != 0 {
		t.Fatal("oversized envelope changed queue or durable journal")
	}
	// The rejected attempt must not spend the idempotency key.
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, transferRPC(t, fixture.Fixtures["append"].Request, key), nil), 202)
}

func TestFavoritesTransferHTTPIdempotencyJournalDoesNotReplayAfterRestart(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	fixture := readFavoritesTransferContract(t)
	body := transferRPC(t, fixture.Fixtures["commit"].Request, "favorites-commit-replay-0001")
	id := rpcID(t, s, body)
	if retry := rpcID(t, s, body); retry != id || len(s.devices[0].queue) != 1 {
		t.Fatal("same transfer idempotency key queued twice")
	}
	changed := transferObject(t, fixture.Fixtures["commit"].Request)
	changed["plan_digest"] = transferJSON(t, strings.Repeat("1", 64))
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, transferRPC(t, transferJSON(t, changed), "favorites-commit-replay-0001"), nil), 409)
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if operationValue(t, s, "favorites-commit-replay-0001")["state"] != "unknown" {
		t.Fatal("restart invented a known transfer result")
	}
	expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, body, nil), 202)
	if len(s.devices[0].queue) != 0 {
		t.Fatal("uncertain commit was replayed after restart")
	}
}

func TestFavoritesTransferHTTPMalformedRequestsAndExpiredReplies(t *testing.T) {
	for _, params := range []string{`null`, `[]`, `{}`, `{"op":"status","runtime":"synthetic-runtime-v1","operation_id":"bad"}`, `{"op":"status","op":"abort","runtime":"synthetic-runtime-v1"}`} {
		s := newTestServer(t)
		expect(t, request(s, "POST", "/api/requests?device_id=first", adminToken, `{"action":"favorites_transfer","params":`+params+`}`, nil), 400)
		if len(s.devices[0].queue) != 0 || s.bytes != 0 {
			t.Fatal("malformed transfer was queued")
		}
	}
	s := newTestServer(t)
	fixture := readFavoritesTransferContract(t).Fixtures["snapshot"]
	id := rpcID(t, s, transferRPC(t, fixture.Request, ""))
	s.now = func() time.Time { return time.Now().Add(s.ttl + time.Second) }
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", fixture.Response), nil), 404)
	if len(s.devices[0].results) != 0 {
		t.Fatal("expired result was retained")
	}
}
