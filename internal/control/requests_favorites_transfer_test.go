package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

type favoritesTransferFixture struct {
	Request  json.RawMessage `json:"request"`
	Response json.RawMessage `json:"response"`
}

type favoritesTransferContract struct {
	Status            string                              `json:"status"`
	Fixtures          map[string]favoritesTransferFixture `json:"fixtures"`
	ErrorAllowlist    []string                            `json:"error_allowlist"`
	ErrorFixture      favoritesTransferFixture            `json:"error_fixture"`
	OutcomeFixtures   map[string]json.RawMessage          `json:"outcome_fixtures"`
	StatusFixtures    map[string]json.RawMessage          `json:"status_fixtures"`
	UnresolvedExample json.RawMessage                     `json:"preview_unresolved_example"`
}

func readFavoritesTransferContract(t *testing.T) favoritesTransferContract {
	t.Helper()
	raw, err := os.ReadFile("../../contracts/favorites-transfer-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var contract favoritesTransferContract
	if json.Unmarshal(raw, &contract) != nil || !strings.HasPrefix(contract.Status, "PROPOSED") {
		t.Fatal("missing draft contract marker")
	}
	return contract
}

func transferObject(t *testing.T, raw json.RawMessage) map[string]json.RawMessage {
	t.Helper()
	value, err := decodeObject(raw)
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func transferJSON(t *testing.T, value any) json.RawMessage {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func transferBound(t *testing.T, raw json.RawMessage) *favoritesTransferRequest {
	t.Helper()
	request := parseFavoritesTransferRequest(transferObject(t, raw))
	if request == nil {
		t.Fatal("shared request rejected")
	}
	return request
}

func TestFavoritesTransferDraftSharedStableOperations(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for _, op := range []string{"snapshot", "begin", "append", "preview", "commit", "status", "abort"} {
		t.Run(op, func(t *testing.T) {
			fixture := c.Fixtures[op]
			bound := transferBound(t, fixture.Request)
			for range 2 {
				if !validFavoritesTransferResult(fixture.Response, "ok", bound) {
					t.Fatal("valid result/repeated acknowledgement rejected")
				}
			}
			for _, status := range []string{"rejected", "unsupported", "unknown", ""} {
				if validFavoritesTransferResult(fixture.Response, status, bound) {
					t.Fatal("successful payload accepted on non-ok transport status")
				}
			}
		})
	}
	for _, pending := range []string{"bindingcatalog", "bind"} {
		if parseFavoritesTransferRequest(transferObject(t, c.Fixtures[pending].Request)) != nil {
			t.Fatal("unfrozen target binding API was enabled")
		}
	}
}

func TestFavoritesTransferRequestsRequireExactFieldsAndTypes(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for _, op := range []string{"snapshot", "begin", "append", "preview", "commit", "status", "abort"} {
		for field := range transferObject(t, c.Fixtures[op].Request) {
			changed := transferObject(t, c.Fixtures[op].Request)
			delete(changed, field)
			if parseFavoritesTransferRequest(changed) != nil {
				t.Fatalf("%s missing %s accepted", op, field)
			}
		}
		for field, value := range map[string]json.RawMessage{
			"extra": json.RawMessage(`true`), "operation": json.RawMessage(`"snapshot"`),
			"runtime": json.RawMessage(`"foreign/runtime"`), "op": json.RawMessage(`"unknown"`),
		} {
			changed := transferObject(t, c.Fixtures[op].Request)
			changed[field] = value
			if parseFavoritesTransferRequest(changed) != nil {
				t.Fatal("invalid request field accepted", op, field)
			}
		}
	}
	for field, values := range map[string][]string{
		"size_bytes":      {`0`, `1048577`, `1.0`, `true`, `null`},
		"chunk_count":     {`0`, `257`, `1.0`, `false`, `null`},
		"persistence":     {`"session-only"`, `null`, `true`},
		"operation_id":    {`"95e21aca-76b0-4db1-b3ce-bd9a4bd64770\n"`, `"95E21ACA-76B0-4DB1-B3CE-BD9A4BD64770"`},
		"document_sha256": {`"bad"`, `null`},
	} {
		for _, value := range values {
			changed := transferObject(t, c.Fixtures["begin"].Request)
			changed[field] = json.RawMessage(value)
			if parseFavoritesTransferRequest(changed) != nil {
				t.Fatal("invalid begin field accepted", field)
			}
		}
	}
	for _, pair := range [][2]int{{1, 2}, {8193, 1}} {
		changed := transferObject(t, c.Fixtures["begin"].Request)
		changed["size_bytes"], changed["chunk_count"] = transferJSON(t, pair[0]), transferJSON(t, pair[1])
		if parseFavoritesTransferRequest(changed) != nil {
			t.Fatal("impossible stage size/chunk count accepted")
		}
	}
}

func TestFavoritesTransferChunkCanonicalBase64AndExactDigest(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for _, size := range []int{1, 2, 3, 8192, 8193} {
		chunk := bytes.Repeat([]byte{0x8f}, size) // Byte chunks may split a UTF-8 sequence.
		digest := sha256.Sum256(chunk)
		params := transferObject(t, c.Fixtures["append"].Request)
		params["data_base64"] = transferJSON(t, base64.StdEncoding.EncodeToString(chunk))
		params["chunk_sha256"] = transferJSON(t, hex.EncodeToString(digest[:]))
		bound := parseFavoritesTransferRequest(params)
		if (size > maxFavoritesTransferChunkBytes && bound != nil) || (size <= maxFavoritesTransferChunkBytes && (bound == nil || bound.ChunkBytes != size)) {
			t.Fatal("decoded chunk bound not enforced", size)
		}
	}
	for field, values := range map[string][]string{
		"data_base64":  {`""`, `"AB=="`, `"AA==\n"`, `"AA"`, `"_A=="`, `1`, `null`},
		"chunk_sha256": {`"` + strings.Repeat("0", 64) + `"`, `"bad"`},
		"seq":          {`-1`, `256`, `1.0`, `true`, `null`},
		"offset":       {`-1`, `1048576`, `1048575`, `1.0`, `true`, `null`},
	} {
		for _, value := range values {
			params := transferObject(t, c.Fixtures["append"].Request)
			params[field] = json.RawMessage(value)
			if parseFavoritesTransferRequest(params) != nil {
				t.Fatal("invalid chunk accepted", field)
			}
		}
	}
}

func TestFavoritesTransferMinimalErrorsDoNotInventOutcome(t *testing.T) {
	c := readFavoritesTransferContract(t)
	bound := transferBound(t, c.ErrorFixture.Request)
	if len(c.ErrorAllowlist) != len(favoritesTransferErrors) {
		t.Fatal("error allowlist drift")
	}
	for _, code := range c.ErrorAllowlist {
		if !favoritesTransferErrors[code] {
			t.Fatal("unknown shared error")
		}
		result := transferObject(t, c.ErrorFixture.Response)
		result["error"] = transferJSON(t, code)
		for _, status := range []string{"rejected", "unsupported"} {
			if !validFavoritesTransferResult(transferJSON(t, result), status, bound) {
				t.Fatal("allowed error rejected", code, status)
			}
		}
		if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
			t.Fatal("error advertised as success")
		}
	}
	for field, value := range map[string]string{"error": `"fixture-secret"`, "runtime": `"stale-runtime"`, "op": `"commit"`, "version": `2`, "outcome": `"not_applied"`} {
		result := transferObject(t, c.ErrorFixture.Response)
		result[field] = json.RawMessage(value)
		if validFavoritesTransferResult(transferJSON(t, result), "rejected", bound) {
			t.Fatal("malformed/uncorrelated error accepted", field)
		}
	}
}

func TestFavoritesTransferCorrelationSurvivesDuplicateAcknowledgements(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for _, op := range []string{"snapshot", "begin", "append", "preview", "commit", "status", "abort"} {
		fixture := c.Fixtures[op]
		bound := transferBound(t, fixture.Request)
		for _, field := range []string{"runtime", "op", "version", "operation_id", "transaction_id", "plan_digest"} {
			result := transferObject(t, fixture.Response)
			if _, exists := result[field]; !exists || (field == "plan_digest" && op != "commit") {
				continue
			}
			delete(result, field)
			if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
				t.Fatal("missing correlation field accepted", op, field)
			}
			result[field] = json.RawMessage(`"different"`)
			if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
				t.Fatal("changed correlation field accepted", op, field)
			}
		}
		result := transferObject(t, fixture.Response)
		result["private_config"] = json.RawMessage(`{"token":"fixture-secret"}`)
		if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
			t.Fatal("unknown/private result field accepted")
		}
	}
	bound := transferBound(t, c.Fixtures["append"].Request)
	for field, value := range map[string]int{"next_seq": 2, "received_bytes": 651} {
		result := transferObject(t, c.Fixtures["append"].Response)
		result[field] = transferJSON(t, value)
		if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
			t.Fatal("duplicate acknowledgement advanced beyond original chunk", field)
		}
	}
}

func TestFavoritesTransferBeginRetryCanReportAcceptedProgress(t *testing.T) {
	c := readFavoritesTransferContract(t)
	bound := transferBound(t, c.Fixtures["begin"].Request)
	result := transferObject(t, c.Fixtures["begin"].Response)
	result["next_seq"], result["received_bytes"] = json.RawMessage(`1`), json.RawMessage(`650`)
	if !validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("idempotent begin progress rejected")
	}
	for field, value := range map[string]string{"next_seq": `2`, "received_bytes": `651`, "idle_expires_at": `"2026-10-27T23:11:00Z"`, "absolute_expires_at": `"invalid"`} {
		changed := transferObject(t, transferJSON(t, result))
		changed[field] = json.RawMessage(value)
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("invalid progress/expiry accepted", field)
		}
	}
	result["next_seq"] = json.RawMessage(`0`)
	if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("received bytes without a chunk accepted")
	}
}

func TestFavoritesTransferSnapshotPreservesExactHashAndScope(t *testing.T) {
	c := readFavoritesTransferContract(t)
	fixture := c.Fixtures["snapshot"]
	bound := transferBound(t, fixture.Request)
	result := transferObject(t, fixture.Response)
	var encoded string
	_ = json.Unmarshal(result["document_base64"], &encoded)
	original, _ := base64.StdEncoding.DecodeString(encoded)
	for _, document := range [][]byte{
		bytes.Replace(original, []byte(`"schema":1`), []byte(`"schema":1,"schema":1`), 1),
		bytes.Replace(original, []byte(`"present":false`), []byte(`"present":false,"present":true`), 1),
		bytes.Replace(original, []byte(`Synthetic example`), []byte(`\ud800`), 1),
		bytes.Replace(original, []byte(`Synthetic example`), []byte{0xff}, 1),
		bytes.Replace(original, []byte(`"ratingKey":"12345"`), []byte(`"ratingKey":1e400`), 1),
		bytes.Replace(original, []byte(`88dbcf12-1d8c-417e-b6c5-f8810e3a3285`), []byte(`88dbcf12-1d8c-417e-b6c5-f8810e3a3286`), 1),
		append(append([]byte{}, original...), []byte(` {}`)...),
	} {
		changed := transferObject(t, fixture.Response)
		digest := sha256.Sum256(document)
		changed["document_base64"] = transferJSON(t, base64.StdEncoding.EncodeToString(document))
		changed["document_sha256"] = transferJSON(t, hex.EncodeToString(digest[:]))
		changed["size_bytes"] = transferJSON(t, len(document))
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("invalid/cross-scope JSON accepted despite recomputed digest")
		}
	}
	for field, value := range map[string]json.RawMessage{
		"document_sha256": transferJSON(t, strings.Repeat("0", 64)),
		"size_bytes":      json.RawMessage(`651`), "document_base64": transferJSON(t, encoded+"\n"),
	} {
		changed := transferObject(t, fixture.Response)
		changed[field] = value
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("mismatched snapshot bytes/hash/size accepted", field)
		}
	}
}

func TestFavoritesTransferHonestPartialUnknownAndVerifiedStores(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for name, op := range map[string]string{"partial": "commit", "unknown": "status"} {
		if !validFavoritesTransferResult(c.OutcomeFixtures[name], "ok", transferBound(t, c.Fixtures[op].Request)) {
			t.Fatal("honest non-success outcome rejected", name)
		}
	}
	bound := transferBound(t, c.Fixtures["commit"].Request)
	for _, rows := range []string{`[]`, `[{"id":"a","outcome":"unknown"}]`, `[{"id":"a","outcome":"verified"},{"id":"a","outcome":"verified"}]`, `[{"id":"a","outcome":"verified","token":"fixture-secret"}]`} {
		result := transferObject(t, c.Fixtures["commit"].Response)
		result["per_store"] = json.RawMessage(rows)
		if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
			t.Fatal("unproved/duplicate/private store evidence accepted")
		}
	}
	result := transferObject(t, c.OutcomeFixtures["partial"])
	result["outcome"] = json.RawMessage(`"not_applied"`)
	if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("changed store advertised as not-applied")
	}
	result = transferObject(t, c.OutcomeFixtures["unknown"])
	result["receipt"] = transferObject(t, c.Fixtures["status"].Response)["receipt"]
	if validFavoritesTransferResult(transferJSON(t, result), "ok", transferBound(t, c.Fixtures["status"].Request)) {
		t.Fatal("unknown outcome carried authoritative receipt")
	}
	for _, field := range []string{"operation_id", "new_revision", "document_sha256", "plan_digest", "counts", "outcome", "persistence"} {
		result = transferObject(t, c.Fixtures["status"].Response)
		receipt := transferObject(t, result["receipt"])
		delete(receipt, field)
		result["receipt"] = transferJSON(t, receipt)
		if validFavoritesTransferResult(transferJSON(t, result), "ok", transferBound(t, c.Fixtures["status"].Request)) {
			t.Fatal("incomplete status receipt accepted", field)
		}
	}
}

func TestFavoritesTransferActualEnvelopeAndResultByteLimits(t *testing.T) {
	c := readFavoritesTransferContract(t)
	envelope := map[string]any{"action": "favorites_transfer", "params": c.Fixtures["append"].Request,
		"id": "0123456789abcdef0123456789abcdef", "expires_at": 1800000000.5}
	raw := transferJSON(t, envelope)
	if !validFavoritesTransferEnvelope(raw) {
		t.Fatal("actual queued envelope rejected")
	}
	atLimit := append(append([]byte{}, raw...), bytes.Repeat([]byte{' '}, maxFavoritesTransferEnvelopeBytes-len(raw))...)
	if !validFavoritesTransferEnvelope(atLimit) || validFavoritesTransferEnvelope(append(atLimit, ' ')) {
		t.Fatal("actual serialized envelope boundary not enforced")
	}
	for _, field := range []string{"size_hint", "ts"} {
		envelope[field] = 1
		if validFavoritesTransferEnvelope(transferJSON(t, envelope)) {
			t.Fatal("unknown envelope field admitted", field)
		}
		delete(envelope, field)
	}
	for _, op := range []string{"snapshot", "preview"} {
		limit := maxFavoritesTransferResultBytes
		if op != "snapshot" {
			limit = maxFavoritesTransferMetadataBytes
		}
		raw = append(append([]byte{}, c.Fixtures[op].Response...), bytes.Repeat([]byte{' '}, limit)...)
		if validFavoritesTransferResult(raw, "ok", transferBound(t, c.Fixtures[op].Request)) {
			t.Fatal("oversized result accepted", op)
		}
	}
	if validFavoritesTransferResult(c.Fixtures["append"].Response, "ok", nil) {
		t.Fatal("result without retained request accepted")
	}
}

func TestFavoritesTransferStageStatusAndPrewriteTerminalEvidence(t *testing.T) {
	c := readFavoritesTransferContract(t)
	bound := transferBound(t, c.Fixtures["status"].Request)
	for _, state := range []string{"receiving", "ready", "expired", "aborted"} {
		fixture := c.StatusFixtures[state]
		if !validFavoritesTransferResult(fixture, "ok", bound) {
			t.Fatal("frozen stage status rejected", state)
		}
		for field := range transferObject(t, fixture) {
			changed := transferObject(t, fixture)
			delete(changed, field)
			if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
				t.Fatal("missing stage-status field accepted", state, field)
			}
		}
		changed := transferObject(t, fixture)
		changed["operation_id"] = json.RawMessage(`"00000000-0000-4000-8000-000000000001"`)
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("different valid operation UUID accepted", state)
		}
	}
	for _, state := range []string{"expired", "aborted"} {
		changed := transferObject(t, c.StatusFixtures[state])
		changed["per_store"] = json.RawMessage(`[{"id":"written","outcome":"verified"}]`)
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("terminal pre-write result claimed a written store")
		}
	}
	changed := transferObject(t, c.StatusFixtures["receiving"])
	changed["received_bytes"] = json.RawMessage(`1`)
	if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
		t.Fatal("stage reported bytes without an accepted chunk")
	}
	for _, invalid := range []string{`1`, `false`} {
		changed = transferObject(t, c.StatusFixtures["ready"])
		changed["ready"] = json.RawMessage(invalid)
		if validFavoritesTransferResult(transferJSON(t, changed), "ok", bound) {
			t.Fatal("false/nonboolean ready-state assertion accepted")
		}
	}
}

func TestFavoritesTransferJSONRejectsNumberOverflowWithoutReserializing(t *testing.T) {
	for _, valid := range []string{`{"finite":1e308}`, `{"finite":-1e308}`, `{"finite":0}`, `{"finite":9007199254740991}`} {
		if !validFavoritesTransferJSON([]byte(valid)) {
			t.Fatal("finite JSON token rejected")
		}
	}
	for _, invalid := range []string{`{"overflow":1e400}`, `{"overflow":-1e400}`, `{"nested":[{"overflow":1e9999}]}`} {
		if validFavoritesTransferJSON([]byte(invalid)) {
			t.Fatal("overflow JSON number accepted")
		}
	}
}

func TestFavoritesTransferNegativeMetadataUsesSameSmallByteLimit(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for _, op := range []string{"preview", "snapshot"} {
		bound := transferBound(t, c.Fixtures[op].Request)
		result := transferObject(t, c.ErrorFixture.Response)
		result["op"] = transferJSON(t, op)
		raw := transferJSON(t, result)
		atLimit := append(append([]byte{}, raw...), bytes.Repeat([]byte{' '}, maxFavoritesTransferMetadataBytes-len(raw))...)
		if !validFavoritesTransferResult(atLimit, "rejected", bound) || validFavoritesTransferResult(append(atLimit, ' '), "rejected", bound) {
			t.Fatal("negative metadata boundary not enforced", op)
		}
	}
}

func TestFavoritesTransferPrivateDiagnosticsAndMalformedStatusFailClosed(t *testing.T) {
	c := readFavoritesTransferContract(t)
	result := transferObject(t, c.Fixtures["preview"].Response)
	result["unresolved"] = json.RawMessage(`[{"token":"fixture-secret"}]`)
	if validFavoritesTransferResult(transferJSON(t, result), "ok", transferBound(t, c.Fixtures["preview"].Request)) {
		t.Fatal("arbitrary private diagnostic accepted")
	}
	for _, state := range []string{"receiving", "ready", "expired", "aborted"} {
		result = transferObject(t, c.Fixtures["status"].Response)
		result["state"] = transferJSON(t, state)
		if validFavoritesTransferResult(transferJSON(t, result), "ok", transferBound(t, c.Fixtures["status"].Request)) {
			t.Fatal("invented status shape admitted", state)
		}
	}
}

func TestFavoritesTransferFrozenCountBoundIsSeparateFromByteBound(t *testing.T) {
	c := readFavoritesTransferContract(t)
	for _, field := range []string{"channelLists", "channelMemberships", "mediaFavorites", "unresolved"} {
		counts := map[string]int{"channelLists": 0, "channelMemberships": 1000000, "mediaFavorites": 1000000, "unresolved": 0}
		counts[field] = 1000000
		if !validFavoritesTransferCounts(transferJSON(t, counts)) {
			t.Fatal("frozen maximum count rejected", field)
		}
		counts[field]++
		if validFavoritesTransferCounts(transferJSON(t, counts)) {
			t.Fatal("count above frozen maximum accepted", field)
		}
	}
	request := transferObject(t, c.Fixtures["begin"].Request)
	request["size_bytes"], request["chunk_count"] = json.RawMessage(`1048576`), json.RawMessage(`128`)
	if parseFavoritesTransferRequest(request) == nil {
		t.Fatal("count ceiling incorrectly narrowed document byte budget")
	}
}

func TestFavoritesTransferBoundedTypedUnresolvedDiagnostics(t *testing.T) {
	c := readFavoritesTransferContract(t)
	bound := transferBound(t, c.Fixtures["preview"].Request)
	result := transferObject(t, c.Fixtures["preview"].Response)
	result["counts"] = json.RawMessage(`{"channelLists":2,"channelMemberships":3,"mediaFavorites":5,"unresolved":2}`)
	result["unresolved"] = c.UnresolvedExample
	if !validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("frozen nonempty diagnostic example rejected")
	}
	for _, row := range []string{
		`{"collection":"channelLists","entry_index":0,"reason":"missing"}`,
		`{"collection":"mediaFavorites","entry_index":0,"list_index":0,"reason":"missing"}`,
		`{"collection":"mediaFavorites","entry_index":0,"reason":"missing","title":"fixture-private"}`,
		`{"collection":"other","entry_index":0,"reason":"missing"}`,
		`{"collection":"mediaFavorites","entry_index":0,"reason":"other"}`,
		`{"collection":"mediaFavorites","entry_index":1000001,"reason":"missing"}`,
		`{"collection":"mediaFavorites","entry_index":true,"reason":"missing"}`,
		`{"collection":"channelLists","entry_index":0,"list_index":-1,"reason":"missing"}`,
	} {
		result["unresolved"] = json.RawMessage("[" + row + "]")
		if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
			t.Fatal("malformed/private diagnostic accepted")
		}
	}
	rows := make([]map[string]any, maxFavoritesTransferDiagnostics)
	for i := range rows {
		rows[i] = map[string]any{"collection": "mediaFavorites", "entry_index": i, "reason": "codec-unavailable"}
	}
	result["counts"] = json.RawMessage(`{"channelLists":0,"channelMemberships":0,"mediaFavorites":1000000,"unresolved":1000000}`)
	result["unresolved"] = transferJSON(t, rows)
	if !validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("bounded diagnostics cannot summarize full unresolved count")
	}
	result["unresolved"] = transferJSON(t, append(rows, rows[0]))
	if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("too many diagnostics accepted")
	}
	result["unresolved"] = c.UnresolvedExample
	result["counts"] = json.RawMessage(`{"channelLists":2,"channelMemberships":3,"mediaFavorites":5,"unresolved":1}`)
	if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("diagnostics exceeding full unresolved count accepted")
	}
}

func TestFavoritesTransferCommitReceiptMustMatchRepeatedClaim(t *testing.T) {
	c := readFavoritesTransferContract(t)
	bound := transferBound(t, c.Fixtures["commit"].Request)
	original := transferObject(t, c.Fixtures["commit"].Response)
	delete(original, "receipt")
	if validFavoritesTransferResult(transferJSON(t, original), "ok", bound) {
		t.Fatal("verified commit without persisted receipt accepted")
	}
	for field, changed := range map[string]string{
		"operation_id": `"00000000-0000-4000-8000-000000000001"`,
		"scope":        `{"profileBindingId":"00000000-0000-4000-8000-000000000001","sourceBindingId":"00000000-0000-4000-8000-000000000002","provider":"plex"}`,
		"new_revision": `"different"`, "document_sha256": `"` + strings.Repeat("0", 64) + `"`,
		"plan_digest": `"` + strings.Repeat("0", 64) + `"`,
		"counts":      `{"channelLists":2,"channelMemberships":0,"mediaFavorites":2,"unresolved":0}`,
		"outcome":     `"unknown"`, "persistence": `"session-only"`,
	} {
		result := transferObject(t, c.Fixtures["commit"].Response)
		receipt := transferObject(t, result["receipt"])
		receipt[field] = json.RawMessage(changed)
		result["receipt"] = transferJSON(t, receipt)
		if validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
			t.Fatal("conflicting nested receipt accepted", field)
		}
	}
	result := transferObject(t, c.Fixtures["commit"].Response)
	var reordered any
	_ = json.Unmarshal(result["receipt"], &reordered)
	pretty, _ := json.MarshalIndent(reordered, "", "  ")
	result["receipt"] = pretty
	if !validFavoritesTransferResult(transferJSON(t, result), "ok", bound) {
		t.Fatal("equivalent reordered/pretty receipt rejected")
	}
}
