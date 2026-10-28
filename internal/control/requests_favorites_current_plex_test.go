package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
	"os"
	"strings"
	"testing"
)

func currentPlexContract(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/favorites-current-plex-protocol-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	packet := transferObject(t, raw)
	// The coherent packet records the catalog response directly; derive only
	// its existing two-field read request from that exact runtime fixture.
	catalog := transferObject(t, packet["bindingcatalog"])
	packet["bindingcatalog_response"] = packet["bindingcatalog"]
	packet["bindingcatalog_request"] = transferJSON(t, map[string]any{"op": "bindingcatalog", "runtime": catalog["runtime"]})
	return packet
}
func currentPlexValue(t *testing.T, raw json.RawMessage) map[string]any {
	t.Helper()
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	return value
}
func currentPlexCapability(t *testing.T) json.RawMessage {
	t.Helper()
	return transferObject(t, currentPlexContract(t)["capability"])["favorites_transfer"]
}
func TestFavoritesCurrentPlexCapabilityExactShapeAndAllLimitBounds(t *testing.T) {
	raw := currentPlexCapability(t)
	if !validFavoritesTransferCapability(raw) {
		t.Fatal("agreed player capability rejected")
	}
	minimum := currentPlexValue(t, raw)
	for field := range favoritesTransferCapabilityLimits {
		minimum["limits"].(map[string]any)[field] = 1
	}
	minimum["receipt_retention_seconds"] = 1
	for _, field := range []string{"max_unconsumed_grants", "admission_ttl_ms", "max_budget_ms"} {
		minimum["admission"].(map[string]any)[field] = 1
	}
	if !validFavoritesTransferCapability(transferJSON(t, minimum)) {
		t.Fatal("valid lower limits rejected")
	}
	for field, maximum := range favoritesTransferCapabilityLimits {
		for _, invalid := range []any{nil, true, "1", 1.5, 0, -1, maximum + 1} {
			changed := currentPlexValue(t, raw)
			changed["limits"].(map[string]any)[field] = invalid
			if validFavoritesTransferCapability(transferJSON(t, changed)) {
				t.Fatalf("invalid %s=%v accepted", field, invalid)
			}
		}
		changed := currentPlexValue(t, raw)
		delete(changed["limits"].(map[string]any), field)
		if validFavoritesTransferCapability(transferJSON(t, changed)) {
			t.Fatal("missing limit accepted", field)
		}
	}
	for field := range transferObject(t, raw) {
		changed := transferObject(t, raw)
		delete(changed, field)
		if validFavoritesTransferCapability(transferJSON(t, changed)) {
			t.Fatal("missing capability field accepted", field)
		}
	}
	for field, maximum := range map[string]int{"max_unconsumed_grants": 1, "admission_ttl_ms": 10000, "max_budget_ms": 300000} {
		for _, invalid := range []any{nil, true, "1", 1.5, 0, -1, maximum + 1} {
			changed := currentPlexValue(t, raw)
			changed["admission"].(map[string]any)[field] = invalid
			if validFavoritesTransferCapability(transferJSON(t, changed)) {
				t.Fatalf("invalid admission %s=%v accepted", field, invalid)
			}
		}
	}
	for field := range currentPlexValue(t, raw)["admission"].(map[string]any) {
		changed := currentPlexValue(t, raw)
		delete(changed["admission"].(map[string]any), field)
		if validFavoritesTransferCapability(transferJSON(t, changed)) {
			t.Fatal("missing admission field accepted", field)
		}
	}
	for name, mutate := range map[string]func(map[string]any){
		"unknown":        func(v map[string]any) { v["supports_creation"] = true },
		"version":        func(v map[string]any) { v["version"] = 2 },
		"retention-zero": func(v map[string]any) { v["receipt_retention_seconds"] = 0 },
		"retention-high": func(v map[string]any) { v["receipt_retention_seconds"] = 86401 },
		"origin":         func(v map[string]any) { v["receipt_retention_origin"] = "completion" },
		"old-origin":     func(v map[string]any) { v["receipt_retention_origin"] = "reservation" },
		"policy":         func(v map[string]any) { v["receipt_retention_policy"] = "guaranteed-24h" },
		"old-limit": func(v map[string]any) {
			v["limits"].(map[string]any)["max_admitted_operations_per_adapter_instance"] = 16
		},
		"admission-op":          func(v map[string]any) { v["admission"].(map[string]any)["op"] = "begin" },
		"admission-owner":       func(v map[string]any) { v["admission"].(map[string]any)["operation_id"] = "client-generated-uuidv4" },
		"admission-extra":       func(v map[string]any) { v["admission"].(map[string]any)["renewable"] = true },
		"session":               func(v map[string]any) { v["persistence"] = []any{"session"} },
		"duplicate-persistence": func(v map[string]any) { v["persistence"] = []any{"persistent", "persistent"} },
		"empty-codecs":          func(v map[string]any) { v["codecs"] = []any{} },
		"duplicate-codecs":      func(v map[string]any) { v["codecs"] = append(v["codecs"].([]any), v["codecs"].([]any)[0]) },
		"unknown-limit":         func(v map[string]any) { v["limits"].(map[string]any)["unknown"] = 1 },
		"idle-after-absolute":   func(v map[string]any) { v["limits"].(map[string]any)["absolute_ttl_seconds"] = 1 },
	} {
		changed := currentPlexValue(t, raw)
		mutate(changed)
		if validFavoritesTransferCapability(transferJSON(t, changed)) {
			t.Fatal("invalid capability accepted", name)
		}
	}
	for field, value := range map[string]any{"provider": "m3u", "codecVersion": 2, "targetScope": "all-sources", "locatorKinds": []any{"plex"}, "coverage": []any{"channelLists", "channelLists"}, "secret": "fixture-only"} {
		changed := currentPlexValue(t, raw)
		changed["codecs"].([]any)[0].(map[string]any)[field] = value
		if validFavoritesTransferCapability(transferJSON(t, changed)) {
			t.Fatal("unsupported codec accepted", field)
		}
	}
	changed := currentPlexValue(t, raw)
	changed["codecs"].([]any)[0].(map[string]any)["coverage"] = []any{"mediaFavorites", "channelLists"}
	if validFavoritesTransferCapability(transferJSON(t, changed)) {
		t.Fatal("reversed frozen coverage order accepted")
	}
	padded := append(append([]byte{}, raw...), bytes.Repeat([]byte{' '}, maxFavoritesTransferMetadataBytes-len(raw))...)
	if !validFavoritesTransferCapability(padded) || validFavoritesTransferCapability(append(padded, ' ')) {
		t.Fatal("capability byte boundary changed")
	}
	legacy, err := os.ReadFile("../../contracts/favorites-current-plex-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	oldCapability := transferObject(t, transferObject(t, legacy)["capability"])["favorites_transfer"]
	if validFavoritesTransferCapability(oldCapability) {
		t.Fatal("historical pre-admission capability was accepted")
	}
}

func TestFavoritesCurrentPlexBindingRequestsAndResponsesCorrelateExactly(t *testing.T) {
	c := currentPlexContract(t)
	for _, op := range []string{"bindingcatalog", "bind"} {
		rawRequest, rawResult := c[op+"_request"], c[op+"_response"]
		bound := transferBound(t, rawRequest)
		if !validFavoritesTransferResult(rawResult, "ok", bound) {
			t.Fatal("frozen result rejected", op)
		}
		for field := range transferObject(t, rawRequest) {
			changed := transferObject(t, rawRequest)
			delete(changed, field)
			if parseFavoritesTransferRequest(changed) != nil {
				t.Fatal("missing request field accepted", op, field)
			}
		}
		for field, value := range map[string]json.RawMessage{"runtime": json.RawMessage(`"stale"`), "op": json.RawMessage(`"snapshot"`), "private_selector": json.RawMessage(`"fixture-only"`)} {
			changed := transferObject(t, rawResult)
			changed[field] = value
			if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
				t.Fatal("uncorrelated/private result accepted", op, field)
			}
		}
		negative := transferJSON(t, map[string]any{"version": 1, "runtime": bound.Runtime, "op": op, "error": "binding_changed"})
		for _, status := range []string{"rejected", "unsupported"} {
			if !validFavoritesTransferResult(negative, status, bound) || validFavoritesTransferResult(rawResult, status, bound) {
				t.Fatal("negative shape/status not fenced", op)
			}
		}
		for _, raw := range []json.RawMessage{rawResult, negative} {
			status := "ok"
			if bytes.Equal(raw, negative) {
				status = "rejected"
			}
			padded := append(append([]byte{}, raw...), bytes.Repeat([]byte{' '}, maxFavoritesTransferMetadataBytes-len(raw))...)
			if !validFavoritesTransferResult(padded, status, bound) || validFavoritesTransferResult(append(padded, ' '), status, bound) {
				t.Fatal("binding byte boundary changed", op)
			}
		}
	}
	bound := transferBound(t, c["bind_request"])
	for field, value := range map[string]any{"target_id": "another-target", "binding_revision": "3", "paired": false, "codecVersion": 1} {
		changed := currentPlexValue(t, c["bind_response"])
		changed[field] = value
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("creation/rebind or uncorrelated result accepted", field)
		}
	}
	changed := currentPlexValue(t, c["bind_response"])
	changed["scope"].(map[string]any)["sourceBindingId"] = "33333333-3333-4333-8333-333333333333"
	if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
		t.Fatal("foreign scope paired")
	}
	for _, target := range []string{"", strings.Repeat("x", 257), "two words", "a\u0085b", "a\u200bb", "a\ue000b"} {
		request := transferObject(t, c["bind_request"])
		request["target_id"] = transferJSON(t, target)
		if parseFavoritesTransferRequest(request) != nil {
			t.Fatal("invalid target admitted")
		}
	}
	for field, value := range map[string]any{"codecVersion": 2, "private_selector": "fixture-only", "expected_binding_revision": ""} {
		request := transferObject(t, c["bind_request"])
		request[field] = transferJSON(t, value)
		if parseFavoritesTransferRequest(request) != nil {
			t.Fatal("invalid bind request admitted", field)
		}
	}
}

func TestFavoritesCurrentPlexCatalogUsesExplicitCompatibleUniqueScopes(t *testing.T) {
	c := currentPlexContract(t)
	bound := transferBound(t, c["bindingcatalog_request"])
	base := currentPlexValue(t, c["bindingcatalog_response"])
	base["bindings"] = []any{}
	if !validFavoritesTransferResult(transferJSON(t, base), "ok", bound) {
		t.Fatal("unbound current target rejected")
	}
	rows := []any{}
	for number := range 256 {
		row := currentPlexValue(t, c["bindingcatalog_response"])["bindings"].([]any)[0].(map[string]any)
		row["scope"].(map[string]any)["sourceBindingId"] = fmt.Sprintf("33333333-3333-4333-8333-%012x", number)
		rows = append(rows, row)
	}
	base["bindings"] = rows
	if !validFavoritesTransferResult(transferJSON(t, base), "ok", bound) {
		t.Fatal("distinct scopes for same target rejected")
	}
	base["bindings"] = append(rows, rows[0])
	if validFavoritesTransferResult(transferJSON(t, base), "ok", bound) {
		t.Fatal("257 bindings accepted")
	}
	for name, mutate := range map[string]func(map[string]any){
		"duplicate-scope":    func(v map[string]any) { v["bindings"] = append(v["bindings"].([]any), v["bindings"].([]any)[0]) },
		"missing-target":     func(v map[string]any) { v["targets"] = []any{} },
		"two-targets":        func(v map[string]any) { v["targets"] = append(v["targets"].([]any), v["targets"].([]any)[0]) },
		"foreign-target":     func(v map[string]any) { v["bindings"].([]any)[0].(map[string]any)["target_id"] = "other" },
		"incompatible-codec": func(v map[string]any) { v["bindings"].([]any)[0].(map[string]any)["codecVersion"] = 2 },
		"incompatible-provider": func(v map[string]any) {
			v["bindings"].([]any)[0].(map[string]any)["scope"].(map[string]any)["provider"] = "m3u"
		},
		"private-binding": func(v map[string]any) { v["bindings"].([]any)[0].(map[string]any)["owner"] = "fixture-only" },
		"server-url": func(v map[string]any) {
			v["targets"].([]any)[0].(map[string]any)["serverId"] = "https://fixture.invalid/?token=x"
		},
		"private-target":  func(v map[string]any) { v["targets"].([]any)[0].(map[string]any)["token"] = "fixture-only" },
		"display-control": func(v map[string]any) { v["targets"].([]any)[0].(map[string]any)["display"] = "a\u200bb" },
	} {
		changed := currentPlexValue(t, c["bindingcatalog_response"])
		mutate(changed)
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("invalid catalog accepted", name)
		}
	}
}

func TestFavoritesCurrentPlexBindingRegistryIdentityIsGloballyConsistent(t *testing.T) {
	c := currentPlexContract(t)
	bound := transferBound(t, c["bindingcatalog_request"])
	for name, mutate := range map[string]func(map[string]any, map[string]any){
		"same-source-different-parent": func(first, second map[string]any) {
			second["profileBindingId"] = "33333333-3333-4333-8333-333333333333"
		},
		"profile-becomes-source": func(first, second map[string]any) {
			second["profileBindingId"] = "33333333-3333-4333-8333-333333333333"
			second["sourceBindingId"] = first["profileBindingId"]
		},
		"source-becomes-profile": func(first, second map[string]any) {
			second["profileBindingId"] = first["sourceBindingId"]
			second["sourceBindingId"] = "33333333-3333-4333-8333-333333333333"
		},
		"self-scope": func(first, second map[string]any) {
			second["sourceBindingId"] = second["profileBindingId"]
		},
	} {
		catalog := currentPlexValue(t, c["bindingcatalog_response"])
		first := catalog["bindings"].([]any)[0].(map[string]any)
		second := currentPlexValue(t, c["bindingcatalog_response"])["bindings"].([]any)[0].(map[string]any)
		mutate(first["scope"].(map[string]any), second["scope"].(map[string]any))
		for _, rows := range [][]any{{first, second}, {second, first}} {
			catalog["bindings"] = rows
			if validFavoritesTransferResult(transferJSON(t, catalog), "ok", bound) {
				t.Fatal("impossible registry projection accepted", name)
			}
		}
	}
	request := currentPlexValue(t, c["bind_request"])
	scope := request["scope"].(map[string]any)
	scope["sourceBindingId"] = scope["profileBindingId"]
	if parseFavoritesTransferRequest(transferObject(t, transferJSON(t, request))) != nil {
		t.Fatal("bind admitted one UUID in both registry roles")
	}
	// This is a new-binding invariant, not a change to the original seven-op
	// scope grammar or its existing player-side ownership checks.
	legacy := transferObject(t, readFavoritesTransferContract(t).Fixtures["snapshot"].Request)
	legacy["scope"] = transferJSON(t, scope)
	if parseFavoritesTransferRequest(legacy) == nil {
		t.Fatal("original seven-op scope grammar changed")
	}
}

func TestFavoritesCurrentPlexHTTPCapabilityIsValidatedNeverInvented(t *testing.T) {
	marker := currentPlexCapability(t)
	for _, capabilities := range []json.RawMessage{json.RawMessage(`{}`), transferJSON(t, map[string]any{"favorites_transfer": marker})} {
		s := newTestServer(t)
		id := rpcID(t, s, `{"action":"capabilities","params":{}}`)
		bad := currentPlexValue(t, marker)
		bad["limits"].(map[string]any)["max_active_stages"] = 2
		expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, map[string]any{"favorites_transfer": bad})), nil), 400)
		if len(s.devices[0].queue) != 1 {
			t.Fatal("invalid capability consumed pending request")
		}
		response := transferResponse(t, id, "ok", capabilities)
		expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
		got := request(s, "GET", "/api/requests?device_id=first&id="+id, adminToken, "", nil)
		expect(t, got, 200)
		if got.Body.String() != response {
			t.Fatal("relay changed/advertised capability")
		}
	}
}
func TestFavoritesCurrentPlexHTTPBindingsRetainDescriptorsPermissionsAndResults(t *testing.T) {
	packet := currentPlexContract(t)
	for _, op := range []string{"bindingcatalog", "bind"} {
		t.Run(op, func(t *testing.T) {
			c := journalConfig(t)
			legacyToken, ownerToken := strings.Repeat("l", 32), strings.Repeat("o", 32)
			operator := func(id, token string, actions []string) config.CommandOperator {
				hash := sha256.Sum256([]byte(token))
				return config.CommandOperator{ID: id, CredentialSHA256: hex.EncodeToString(hash[:]), DeviceIDs: []string{"first"}, Actions: actions}
			}
			c.Operators = []config.CommandOperator{
				operator("old", legacyToken, []string{"favorites.list", "favorites.add", "favorites_transfer.snapshot"}),
				operator("owner", ownerToken, []string{"favorites_transfer." + op}),
			}
			s, err := New(c)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			body := transferRPC(t, packet[op+"_request"], "binding-packet-0001")
			expect(t, request(s, "POST", "/api/requests?device_id=first", legacyToken, body, nil), 403)
			expect(t, request(s, "POST", "/api/requests?device_id=second", ownerToken, body, nil), 403)
			accepted := request(s, "POST", "/api/requests?device_id=first", ownerToken, body, nil)
			expect(t, accepted, 202)
			var receipt map[string]string
			if err := json.Unmarshal(accepted.Body.Bytes(), &receipt); err != nil {
				t.Fatal(err)
			}
			id := receipt["id"]
			repeated := request(s, "POST", "/api/requests?device_id=first", ownerToken, body, nil)
			expect(t, repeated, 202)
			if len(s.devices[0].queue) != 1 || s.devices[0].queue[0].permission != "favorites_transfer."+op || s.devices[0].queue[0].favoritesTransfer == nil || !validFavoritesTransferEnvelope(s.devices[0].queue[0].data) {
				t.Fatal("binding descriptor/idempotence lost")
			}
			stale := transferObject(t, packet[op+"_response"])
			stale["runtime"] = json.RawMessage(`"stale"`)
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "ok", transferJSON(t, stale)), nil), 400)
			negative := transferJSON(t, map[string]any{"version": 1, "runtime": "stale", "op": op, "error": "binding_changed"})
			expect(t, request(s, "POST", "/api/responses", firstToken, transferResponse(t, id, "rejected", negative), nil), 400)
			response := transferResponse(t, id, "ok", packet[op+"_response"])
			expect(t, request(s, "POST", "/api/responses", secondToken, response, nil), 404)
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, response, nil), 200)
			}
			if s.devices[0].results[id].favoritesTransfer == nil || s.devices[0].results[id].favoritesTransfer.Op != op {
				t.Fatal("retained descriptor missing")
			}
			expect(t, request(s, "GET", "/api/requests?device_id=first&id="+id, legacyToken, "", nil), 403)
			got := request(s, "GET", "/api/requests?device_id=first&id="+id, ownerToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != response {
				t.Fatal("retained binding result changed")
			}
			// A fresh negative receipt is equally fenced and retained; no legacy
			// permission or stale acknowledgement may consume it.
			negativeBody := transferRPC(t, packet[op+"_request"], "binding-negative-0001")
			accepted = request(s, "POST", "/api/requests?device_id=first", ownerToken, negativeBody, nil)
			expect(t, accepted, 202)
			if err := json.Unmarshal(accepted.Body.Bytes(), &receipt); err != nil {
				t.Fatal(err)
			}
			negativeID := receipt["id"]
			bound := transferBound(t, packet[op+"_request"])
			negative = transferJSON(t, map[string]any{"version": 1, "runtime": bound.Runtime, "op": op, "error": "binding_changed"})
			negativeResponse := transferResponse(t, negativeID, "rejected", negative)
			for range 2 {
				expect(t, request(s, "POST", "/api/responses", firstToken, negativeResponse, nil), 200)
			}
			got = request(s, "GET", "/api/requests?device_id=first&id="+negativeID, ownerToken, "", nil)
			expect(t, got, 200)
			if got.Body.String() != negativeResponse || s.devices[0].results[negativeID].favoritesTransfer == nil {
				t.Fatal("negative binding result/descriptor not retained")
			}
		})
	}
}
