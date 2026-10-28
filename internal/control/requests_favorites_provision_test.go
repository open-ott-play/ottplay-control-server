package control

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func provisionPacket(t *testing.T) map[string]favoritesTransferFixture {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/favorites-provisioning-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var packet struct {
		Schema    int                                 `json:"schema"`
		Operation string                              `json:"operation"`
		Examples  map[string]favoritesTransferFixture `json:"examples"`
	}
	if json.Unmarshal(raw, &packet) != nil || packet.Schema != 1 || packet.Operation != "provision" || len(packet.Examples) != 4 {
		t.Fatal("missing frozen provision packet")
	}
	return packet.Examples
}

func provisionScope(t *testing.T, prior bool) json.RawMessage {
	t.Helper()
	mode := "create"
	if prior {
		mode = "replace-current"
	}
	return transferObject(t, provisionPacket(t)[mode].Response)["scope"]
}

func provisionRequest(t *testing.T, mode string) json.RawMessage {
	t.Helper()
	return provisionPacket(t)[mode].Request
}

func provisionResult(t *testing.T, mode string, changed bool) json.RawMessage {
	t.Helper()
	fixture := provisionPacket(t)[mode]
	request, response := transferObject(t, fixture.Request), transferObject(t, fixture.Response)
	response["changed"] = transferJSON(t, changed)
	if !changed {
		response["binding_revision"] = request["expected_binding_revision"]
	} else if string(response["binding_revision"]) == string(request["expected_binding_revision"]) {
		response["binding_revision"] = json.RawMessage(`"2"`)
	}
	return transferJSON(t, response)
}

func TestFavoritesProvisionModesRetainExactRequestExpectations(t *testing.T) {
	for _, mode := range []string{"create", "import", "pair", "replace-current"} {
		t.Run(mode, func(t *testing.T) {
			raw := provisionRequest(t, mode)
			bound := transferBound(t, raw)
			request := transferObject(t, raw)
			if bound.Mode != mode || !inspectEnum(request["target_id"], bound.TargetID) || !inspectEnum(request["expected_binding_revision"], bound.BindingRevision) || (bound.Scope == nil) != (mode == "create") || (bound.PriorScope != nil) != (mode == "replace-current") {
				t.Fatal("provision descriptor lost mode, target, revision or exact scopes")
			}
			if !validFavoritesTransferResult(provisionPacket(t)[mode].Response, "ok", bound) {
				t.Fatal("frozen packet response rejected")
			}
			for field := range transferObject(t, raw) {
				missing := transferObject(t, raw)
				delete(missing, field)
				if parseFavoritesTransferRequest(missing) != nil {
					t.Fatalf("missing %s accepted", field)
				}
			}
			invalid := map[string]json.RawMessage{
				"extra": json.RawMessage(`true`), "mode": json.RawMessage(`"rebind"`),
				"runtime": json.RawMessage(`"UPPER"`), "target_id": json.RawMessage(`"with space"`),
				"expected_binding_revision": json.RawMessage(`""`), "codecVersion": json.RawMessage(`2`),
				"scope": json.RawMessage(`null`), "prior_scope": transferObject(t, provisionPacket(t)[mode].Response)["scope"],
			}
			for field, value := range invalid {
				bad := transferObject(t, raw)
				bad[field] = value
				if parseFavoritesTransferRequest(bad) != nil {
					t.Fatalf("invalid/forbidden %s accepted", field)
				}
			}
			if mode == "create" {
				bad := transferObject(t, raw)
				bad["scope"] = provisionScope(t, false)
				if parseFavoritesTransferRequest(bad) != nil {
					t.Fatal("create accepted caller-specified generated scope")
				}
			} else {
				for _, field := range []string{"provider", "sourceBindingId"} {
					scope := transferObject(t, provisionScope(t, false))
					if field == "provider" {
						scope[field] = json.RawMessage(`"m3u"`)
					} else {
						scope[field] = scope["profileBindingId"]
					}
					bad := transferObject(t, raw)
					bad["scope"] = transferJSON(t, scope)
					if parseFavoritesTransferRequest(bad) != nil {
						t.Fatal("non-Plex or overlapping identity roles accepted")
					}
				}
			}
			if mode == "replace-current" {
				bad := transferObject(t, raw)
				bad["prior_scope"] = json.RawMessage(`null`)
				if parseFavoritesTransferRequest(bad) != nil {
					t.Fatal("invalid previous scope accepted")
				}
			}
		})
	}
}

func TestFavoritesProvisionSuccessIsBoundWithoutTransferReceiptFields(t *testing.T) {
	for _, mode := range []string{"create", "import", "pair", "replace-current"} {
		t.Run(mode, func(t *testing.T) {
			bound := transferBound(t, provisionRequest(t, mode))
			for _, changed := range []bool{false, true} {
				raw := provisionResult(t, mode, changed)
				if !validFavoritesTransferResult(raw, "ok", bound) {
					t.Fatal("valid binding result rejected")
				}
				for field := range transferObject(t, raw) {
					missing := transferObject(t, raw)
					delete(missing, field)
					if validFavoritesTransferResult(transferJSON(t, missing), "ok", bound) {
						t.Fatalf("missing result %s accepted", field)
					}
				}
				for field, value := range map[string]json.RawMessage{
					"mode": json.RawMessage(`"other"`), "runtime": json.RawMessage(`"different"`),
					"target_id": json.RawMessage(`"different-target"`), "paired": json.RawMessage(`false`),
					"changed": json.RawMessage(`null`), "persistence": json.RawMessage(`"session"`),
					"outcome": json.RawMessage(`"unknown"`), "receipt": json.RawMessage(`{}`),
					"operation_id": json.RawMessage(`"11111111-1111-4111-8111-111111111111"`),
				} {
					bad := transferObject(t, raw)
					bad[field] = value
					if validFavoritesTransferResult(transferJSON(t, bad), "ok", bound) {
						t.Fatalf("unbound/invalid result %s accepted", field)
					}
				}
				for _, invalidChanged := range []json.RawMessage{json.RawMessage(`1`), json.RawMessage(`"true"`), transferJSON(t, !changed)} {
					bad := transferObject(t, raw)
					bad["changed"] = invalidChanged
					if validFavoritesTransferResult(transferJSON(t, bad), "ok", bound) {
						t.Fatal("invalid changed/revision correlation accepted")
					}
				}
				for _, status := range []string{"rejected", "unsupported", "unknown"} {
					if validFavoritesTransferResult(raw, status, bound) {
						t.Fatal("binding success accepted without ok outer status")
					}
				}
			}
			different := transferObject(t, provisionResult(t, mode, true))
			different["scope"] = provisionScope(t, mode != "replace-current")
			if validFavoritesTransferResult(transferJSON(t, different), "ok", bound) != (mode == "create") {
				t.Fatal("only create may accept a player-generated scope")
			}
			for _, invalidScope := range []json.RawMessage{json.RawMessage(`null`), json.RawMessage(`{"provider":"plex","profileBindingId":"11111111-1111-4111-8111-111111111111","sourceBindingId":"11111111-1111-4111-8111-111111111111"}`), json.RawMessage(`{"provider":"m3u","profileBindingId":"11111111-1111-4111-8111-111111111111","sourceBindingId":"22222222-2222-4222-8222-222222222222"}`)} {
				bad := transferObject(t, provisionResult(t, mode, true))
				bad["scope"] = invalidScope
				if validFavoritesTransferResult(transferJSON(t, bad), "ok", bound) {
					t.Fatal("invalid generated/result scope accepted")
				}
			}
			negative := transferJSON(t, map[string]any{"version": 1, "runtime": bound.Runtime, "op": "provision", "error": "binding_changed"})
			for _, status := range []string{"rejected", "unsupported"} {
				if !validFavoritesTransferResult(negative, status, bound) {
					t.Fatal("correlated minimal error rejected")
				}
			}
			if validFavoritesTransferResult(negative, "ok", bound) {
				t.Fatal("negative reply accepted as success")
			}
		})
	}
}

func TestFavoritesProvisionFrozenIdentifierBoundaries(t *testing.T) {
	for field, values := range map[string][]string{
		"target_id":                 {"A", "A._:-09", strings.Repeat("a", 128)},
		"expected_binding_revision": {"0", "1", "9007199254740991"},
		"runtime":                   {strings.Repeat("a", 64)},
	} {
		for _, value := range values {
			request := transferObject(t, provisionRequest(t, "create"))
			request[field] = transferJSON(t, value)
			bound := parseFavoritesTransferRequest(request)
			if bound == nil {
				t.Fatalf("valid %s boundary rejected", field)
			}
			response := transferObject(t, provisionResult(t, "create", false))
			response["target_id"] = request["target_id"]
			response["runtime"] = request["runtime"]
			response["binding_revision"] = request["expected_binding_revision"]
			if !validFavoritesTransferResult(transferJSON(t, response), "ok", bound) {
				t.Fatalf("valid response %s boundary rejected", field)
			}
		}
	}
	for field, values := range map[string][]json.RawMessage{
		"target_id":                 {json.RawMessage(`""`), json.RawMessage(`"_first"`), json.RawMessage(`"é"`), json.RawMessage(`"a/b"`), json.RawMessage(`"a b"`), json.RawMessage(`"a\n"`), transferJSON(t, strings.Repeat("a", 129))},
		"expected_binding_revision": {json.RawMessage(`0`), json.RawMessage(`true`), json.RawMessage(`null`), json.RawMessage(`""`), json.RawMessage(`"00"`), json.RawMessage(`"01"`), json.RawMessage(`"-1"`), json.RawMessage(`"+1"`), json.RawMessage(`"1.0"`), json.RawMessage(`"1e1"`), json.RawMessage(`"1 "`), json.RawMessage(`"１"`), json.RawMessage(`"9007199254740992"`), json.RawMessage(`"10000000000000000"`)},
		"runtime":                   {transferJSON(t, strings.Repeat("a", 65))},
	} {
		for _, value := range values {
			request := transferObject(t, provisionRequest(t, "create"))
			request[field] = value
			if parseFavoritesTransferRequest(request) != nil {
				t.Fatalf("invalid %s boundary admitted", field)
			}
			response := transferObject(t, provisionResult(t, "create", true))
			responseField := field
			if field == "expected_binding_revision" {
				responseField = "binding_revision"
			}
			response[responseField] = value
			if validFavoritesTransferResult(transferJSON(t, response), "ok", transferBound(t, provisionRequest(t, "create"))) {
				t.Fatalf("invalid response %s boundary admitted", field)
			}
		}
	}
	// Every collision among the four roles is invalid, not just equal scopes.
	roles := []struct{ scope, id string }{{"scope", "profileBindingId"}, {"scope", "sourceBindingId"}, {"prior_scope", "profileBindingId"}, {"prior_scope", "sourceBindingId"}}
	for i := range roles {
		for j := i + 1; j < len(roles); j++ {
			request := transferObject(t, provisionRequest(t, "replace-current"))
			source := transferObject(t, request[roles[i].scope])
			target := transferObject(t, request[roles[j].scope])
			target[roles[j].id] = source[roles[i].id]
			request[roles[j].scope] = transferJSON(t, target)
			if parseFavoritesTransferRequest(request) != nil {
				t.Fatalf("replacement reused UUID roles %d and %d", i, j)
			}
		}
	}
}

func TestFavoritesProvisionOutcomeUnknownDoesNotExpandTransferErrors(t *testing.T) {
	request := transferBound(t, provisionRequest(t, "create"))
	negative := transferJSON(t, map[string]any{"version": 1, "runtime": request.Runtime, "op": "provision", "error": "outcome_unknown"})
	for _, status := range []string{"rejected", "unsupported"} {
		if !validFavoritesTransferResult(negative, status, request) {
			t.Fatal("provision uncertainty was rejected")
		}
	}
	if validFavoritesTransferResult(negative, "ok", request) || favoritesTransferErrors["outcome_unknown"] {
		t.Fatal("provision uncertainty expanded success or legacy error union")
	}
	nested := transferObject(t, negative)
	nested["error"] = json.RawMessage(`{"code":"outcome_unknown"}`)
	if validFavoritesTransferResult(transferJSON(t, nested), "rejected", request) {
		t.Fatal("provision admitted an alternate nested error grammar")
	}
	for op, fixture := range readFavoritesTransferContract(t).Fixtures {
		if op == "provision" {
			continue
		}
		params := transferObject(t, fixture.Request)
		bound := parseFavoritesTransferRequest(params)
		if bound == nil {
			continue // Obsolete, deliberately unsupported packet operations.
		}
		negative := transferJSON(t, map[string]any{"version": 1, "runtime": bound.Runtime, "op": bound.Op, "error": "outcome_unknown"})
		for _, status := range []string{"rejected", "unsupported"} {
			if validFavoritesTransferResult(negative, status, bound) {
				t.Fatalf("provision-only uncertainty expanded %s errors", op)
			}
		}
	}
}

func TestFavoritesProvisionHTTPExactPermissionIdempotencyAndRetainedResult(t *testing.T) {
	for _, mode := range []string{"create", "import", "pair", "replace-current"} {
		t.Run(mode, func(t *testing.T) {
			c := journalConfig(t)
			legacyToken, ownerToken := strings.Repeat("l", 32), strings.Repeat("o", 32)
			operator := func(id, token string, actions []string) config.CommandOperator {
				hash := sha256.Sum256([]byte(token))
				return config.CommandOperator{ID: id, CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: actions}
			}
			c.Operators = []config.CommandOperator{operator("old", legacyToken, []string{"favorites_transfer.bind", "favorites_transfer.bindingcatalog", "favorites.add"}), operator("owner", ownerToken, []string{"favorites_transfer.provision"})}
			s, err := New(c)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			body := transferRPC(t, provisionRequest(t, mode), "provision-test-0001")
			for _, auth := range []struct {
				token string
				code  int
			}{{"", 401}, {firstToken, 403}, {legacyToken, 403}} {
				expect(t, request(s, "POST", "/api/requests?device_id=first", auth.token, body, nil), auth.code)
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
				t.Fatal("same idempotency key queued twice or changed request id")
			}
			queued := s.devices[0].queue[0]
			if queued.permission != "favorites_transfer.provision" || queued.favoritesTransfer == nil || queued.favoritesTransfer.Mode != mode || !validFavoritesTransferEnvelope(queued.data) {
				t.Fatal("provision descriptor or final envelope lost")
			}
			conflict := transferObject(t, provisionRequest(t, mode))
			conflict["target_id"] = json.RawMessage(`"another-target"`)
			expect(t, request(s, "POST", "/api/requests?device_id=first", ownerToken, transferRPC(t, transferJSON(t, conflict), "provision-test-0001"), nil), 409)
			stale := transferObject(t, provisionResult(t, mode, true))
			stale["runtime"] = json.RawMessage(`"stale"`)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, stale)), nil), 400)
			response := transferResponse(t, id, "ok", provisionResult(t, mode, true))
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			}
			retained := s.devices[0].results[id]
			if retained.favoritesTransfer == nil || retained.favoritesTransfer.Mode != mode || retained.permission != "favorites_transfer.provision" || len(s.devices[0].queue) != 0 {
				t.Fatal("completed receipt lost provision expectations")
			}
			stale = transferObject(t, provisionResult(t, mode, true))
			stale["target_id"] = json.RawMessage(`"late-wrong-target"`)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, stale)), nil), 400)
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, legacyToken, "", nil), 403)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, ownerToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response {
				t.Fatal("correlated result was changed after completion")
			}
		})
	}
}
