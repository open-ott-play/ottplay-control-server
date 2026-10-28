package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func favoritesPrepareFixture(t *testing.T) favoritesTransferFixture {
	t.Helper()
	prepare := transferObject(t, currentPlexContract(t)["prepare"])
	return favoritesTransferFixture{Request: prepare["request_example"], Response: prepare["response_example"]}
}

func TestFavoritesPrepareFrozenPacketAndExactCorrelation(t *testing.T) {
	fixture := favoritesPrepareFixture(t)
	bound := transferBound(t, fixture.Request)
	if bound.Op != "prepare" || bound.BudgetMS != 300000 || bound.SizeBytes != 650 || bound.OperationID != "" || bound.DocumentSHA256 != strings.Repeat("a", 64) || bound.ExpectedRevision != "revision-1" || bound.BindingRevision != "2" {
		t.Fatal("prepare descriptor lost captured budget, content or revision expectations")
	}
	if !validFavoritesTransferResult(fixture.Response, "ok", bound) {
		t.Fatal("frozen prepare result rejected")
	}
	for field := range transferObject(t, fixture.Request) {
		missing := transferObject(t, fixture.Request)
		delete(missing, field)
		if parseFavoritesTransferRequest(missing) != nil {
			t.Fatal("missing prepare field accepted", field)
		}
	}
	for field := range transferObject(t, fixture.Response) {
		missing := transferObject(t, fixture.Response)
		delete(missing, field)
		if validFavoritesTransferResult(transferJSON(t, missing), "ok", bound) {
			t.Fatal("missing prepare result field accepted", field)
		}
	}
	for field, value := range map[string]any{
		"runtime": "different", "op": "begin", "expected_revision": "different", "binding_revision": "3",
		"document_sha256": strings.Repeat("b", 64), "size_bytes": 651, "budget_ms": 299999,
		"operation_id": "55555555-5555-1555-8555-555555555555", "extra": true, "persistence": "persistent",
	} {
		bad := transferObject(t, fixture.Response)
		bad[field] = transferJSON(t, value)
		if validFavoritesTransferResult(transferJSON(t, bad), "ok", bound) {
			t.Fatal("uncorrelated/malformed prepare result accepted", field)
		}
	}
	for _, status := range []string{"rejected", "unsupported", "unknown"} {
		if validFavoritesTransferResult(fixture.Response, status, bound) {
			t.Fatal("prepare success accepted under another outer status")
		}
	}
	for _, status := range []string{"rejected", "unsupported"} {
		negative := transferJSON(t, map[string]any{"version": 1, "runtime": bound.Runtime, "op": "prepare", "error": "busy"})
		if !validFavoritesTransferResult(negative, status, bound) || validFavoritesTransferResult(negative, "ok", bound) {
			t.Fatal("prepare minimal error/status changed")
		}
		unknown := transferObject(t, negative)
		unknown["error"] = json.RawMessage(`"outcome_unknown"`)
		if validFavoritesTransferResult(transferJSON(t, unknown), status, bound) {
			t.Fatal("provision-only error admitted for prepare")
		}
	}
	padded := append(append([]byte{}, fixture.Response...), bytes.Repeat([]byte{' '}, maxFavoritesTransferMetadataBytes-len(fixture.Response))...)
	if !validFavoritesTransferResult(padded, "ok", bound) || validFavoritesTransferResult(append(padded, ' '), "ok", bound) {
		t.Fatal("prepare metadata byte boundary changed")
	}
}

func TestFavoritesPrepareRequestBoundsAndArbitraryPlexScope(t *testing.T) {
	fixture := favoritesPrepareFixture(t)
	for field, values := range map[string][]any{
		"size_bytes": {1, maxFavoritesTransferDocumentBytes}, "budget_ms": {1, 300000},
		"expected_revision": {"x", strings.Repeat("x", 256)}, "binding_revision": {"opaque-binding", strings.Repeat("x", 256)},
	} {
		for _, value := range values {
			valid := transferObject(t, fixture.Request)
			valid[field] = transferJSON(t, value)
			if parseFavoritesTransferRequest(valid) == nil {
				t.Fatal("valid prepare bound rejected", field)
			}
		}
	}
	for field, values := range map[string][]any{
		"size_bytes":        {nil, true, "650", 0, -1, 1.5, maxFavoritesTransferDocumentBytes + 1},
		"budget_ms":         {nil, true, "300000", 0, -1, 1.5, 300001},
		"expected_revision": {"", "line\nbreak", strings.Repeat("x", 257)},
		"binding_revision":  {"", true, "\u007f", strings.Repeat("x", 257)},
		"document_sha256":   {strings.Repeat("A", 64), strings.Repeat("a", 63)},
		"operation_id":      {"55555555-5555-4555-8555-555555555555"}, "persistence": {"persistent"}, "chunk_count": {1},
	} {
		for _, value := range values {
			bad := transferObject(t, fixture.Request)
			bad[field] = transferJSON(t, value)
			if parseFavoritesTransferRequest(bad) != nil {
				t.Fatal("invalid/extra prepare field accepted", field)
			}
		}
	}
	scope := transferObject(t, transferObject(t, fixture.Request)["scope"])
	scope["profileBindingId"] = json.RawMessage(`"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"`)
	scope["sourceBindingId"] = json.RawMessage(`"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"`)
	request, response := transferObject(t, fixture.Request), transferObject(t, fixture.Response)
	request["scope"], response["scope"] = transferJSON(t, scope), transferJSON(t, scope)
	bound := parseFavoritesTransferRequest(request)
	if bound == nil || !validFavoritesTransferResult(transferJSON(t, response), "ok", bound) || validFavoritesTransferResult(fixture.Response, "ok", bound) {
		t.Fatal("arbitrary valid Plex scope was guessed or result scope was not correlated")
	}
	for field, value := range map[string]json.RawMessage{"provider": json.RawMessage(`"m3u"`), "sourceBindingId": scope["profileBindingId"]} {
		badScope := transferObject(t, request["scope"])
		badScope[field] = value
		bad := transferObject(t, fixture.Request)
		bad["scope"] = transferJSON(t, badScope)
		if parseFavoritesTransferRequest(bad) != nil {
			t.Fatal("prepare accepted non-Plex or overlapping scope IDs")
		}
	}
}

func TestFavoritesPrepareResultRevisionTokensUseFullUTF8ByteBound(t *testing.T) {
	fixture := favoritesPrepareFixture(t)
	for _, field := range []string{"expected_revision", "binding_revision"} {
		for _, value := range []string{strings.Repeat("a", 256), strings.Repeat("é", 128)} {
			request, response := transferObject(t, fixture.Request), transferObject(t, fixture.Response)
			request[field], response[field] = transferJSON(t, value), transferJSON(t, value)
			bound := parseFavoritesTransferRequest(request)
			if len(value) != 256 || bound == nil || !validFavoritesTransferResult(transferJSON(t, response), "ok", bound) {
				t.Fatal("matching 256-byte revision rejected", field)
			}
			for _, invalid := range []string{value + "a", strings.Repeat("b", 256)} {
				response[field] = transferJSON(t, invalid)
				if validFavoritesTransferResult(transferJSON(t, response), "ok", bound) {
					t.Fatal("oversized or mismatching revision accepted", field)
				}
			}
			request[field] = transferJSON(t, value+"a")
			if parseFavoritesTransferRequest(request) != nil {
				t.Fatal("257-byte request revision accepted", field)
			}
		}
	}
}

func TestFavoritesPrepareAdmissionTTLIsBoundByOriginalBudget(t *testing.T) {
	fixture := favoritesPrepareFixture(t)
	for _, budget := range []int{1, 9999, 10000, 300000} {
		request, response := transferObject(t, fixture.Request), transferObject(t, fixture.Response)
		request["budget_ms"], response["budget_ms"] = transferJSON(t, budget), transferJSON(t, budget)
		bound := parseFavoritesTransferRequest(request)
		for _, ttl := range []int{1, min(10000, budget)} {
			response["admission_ttl_ms"] = transferJSON(t, ttl)
			if !validFavoritesTransferResult(transferJSON(t, response), "ok", bound) {
				t.Fatal("valid bounded grant TTL rejected")
			}
		}
		for _, ttl := range []any{nil, true, "1", 0, -1, 1.5, min(10000, budget) + 1} {
			response["admission_ttl_ms"] = transferJSON(t, ttl)
			if validFavoritesTransferResult(transferJSON(t, response), "ok", bound) {
				t.Fatal("grant TTL exceeded original budget/10 seconds or accepted invalid type")
			}
		}
	}
}

func TestFavoritesPrepareHTTPPermissionQueueExpiryAndRetainedDescriptor(t *testing.T) {
	c := journalConfig(t)
	legacyToken, ownerToken := strings.Repeat("l", 32), strings.Repeat("o", 32)
	operator := func(id, token string, actions []string) config.CommandOperator {
		hash := sha256.Sum256([]byte(token))
		return config.CommandOperator{ID: id, CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: actions}
	}
	c.Operators = []config.CommandOperator{operator("old", legacyToken, []string{"favorites_transfer.begin", "favorites_transfer.provision"}), operator("owner", ownerToken, []string{"favorites_transfer.prepare"})}
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	now := time.Now().Truncate(time.Second)
	s.now = func() time.Time { return now }
	fixture := favoritesPrepareFixture(t)
	body := transferRPC(t, fixture.Request, "prepare-test-0001")
	for _, token := range []string{firstToken, legacyToken} {
		expect(t, request(s, "POST", "/api/requests?device_id=first", token, body, nil), 403)
	}
	expect(t, request(s, "POST", "/api/requests?device_id=second", ownerToken, body, nil), 403)
	accepted := request(s, "POST", "/api/requests?device_id=first", ownerToken, body, nil)
	expect(t, accepted, 202)
	var receipt map[string]string
	if json.Unmarshal(accepted.Body.Bytes(), &receipt) != nil {
		t.Fatal("invalid enqueue receipt")
	}
	id := receipt["id"]
	repeated := request(s, "POST", "/api/requests?device_id=first", ownerToken, body, nil)
	expect(t, repeated, 202)
	if json.Unmarshal(repeated.Body.Bytes(), &receipt) != nil || receipt["id"] != id || len(s.devices[0].queue) != 1 {
		t.Fatal("prepare was replayed despite its idempotency key")
	}
	queued := s.devices[0].queue[0]
	if queued.permission != "favorites_transfer.prepare" || queued.favoritesTransfer == nil || queued.favoritesTransfer.BudgetMS != 300000 || queued.favoritesTransfer.SizeBytes != 650 || !validFavoritesTransferEnvelope(queued.data) {
		t.Fatal("prepare expectation or actual queued envelope lost")
	}
	envelope := transferObject(t, queued.data)
	var expiry float64
	if json.Unmarshal(envelope["expires_at"], &expiry) != nil || c.CommandTTLSeconds != 60 || expiry != float64(now.Unix()+60) {
		t.Fatal("prepare silently changed the configured command delivery TTL")
	}
	bad := transferObject(t, fixture.Response)
	bad["budget_ms"] = json.RawMessage(`299999`)
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, bad)), nil), 400)
	response := transferResponse(t, id, "ok", fixture.Response)
	expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
	for range 2 {
		expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
	}
	retained := s.devices[0].results[id]
	if retained.favoritesTransfer == nil || retained.favoritesTransfer.BudgetMS != 300000 || retained.favoritesTransfer.DocumentSHA256 != strings.Repeat("a", 64) {
		t.Fatal("completed prepare lost original expectations")
	}
	expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, bad)), nil), 400)
	expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, legacyToken, "", nil), 403)
	got := request(s, "GET", "/api/requests?device_id=first&id="+id, ownerToken, "", nil)
	expect(t, got, 200)
	if got.Body.String() != response {
		t.Fatal("prepare result changed after completion")
	}
}

func TestFavoritesPrepareJournalRestartDoesNotMintAnotherGrant(t *testing.T) {
	c := journalConfig(t)
	s, err := New(c)
	if err != nil {
		t.Fatal(err)
	}
	body := transferRPC(t, favoritesPrepareFixture(t).Request, "prepare-restart-0001")
	id := rpcID(t, s, body)
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = New(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if rpcID(t, s, body) != id || len(s.devices[0].queue) != 0 || operationValue(t, s, "prepare-restart-0001")["state"] != "unknown" {
		t.Fatal("unknown prepare was replayed after controller restart")
	}
}
