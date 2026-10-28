package control

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
)

const globalTestOperation = "10000000-0000-4000-8000-000000000001"
const globalTestTransaction = "20000000-0000-4000-8000-000000000002"
const globalTestSnapshot = "30000000-0000-4000-8000-000000000003"
const globalTestRequestID = "1234567890abcdef1234567890abcdef"
const globalTestDocument = `{"version":1,"entries":[]}`

func globalTestHash(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

func globalTestRequests(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	values := map[string]map[string]any{
		"describe": {},
		"export":   {"snapshot_id": globalTestSnapshot, "offset": 0, "max_bytes": 65536},
		"begin":    {"operation_id": globalTestOperation, "mode": "replace", "expected_revision": strings.Repeat("1", 64), "expected_context_revision": strings.Repeat("2", 64), "document_sha256": globalTestHash(globalTestDocument), "size_bytes": len(globalTestDocument), "chunk_count": 1, "budget_ms": 45000, "required_kinds": []string{}},
		"append":   {"transaction_id": globalTestTransaction, "seq": 0, "offset": 0, "chunk_sha256": globalTestHash(globalTestDocument), "data_base64": base64.StdEncoding.EncodeToString([]byte(globalTestDocument))},
		"preview":  {"transaction_id": globalTestTransaction, "page_offset": 0, "max_entries": 64},
		"commit":   {"transaction_id": globalTestTransaction, "operation_id": globalTestOperation, "plan_digest": strings.Repeat("3", 64)},
		"status":   {"operation_id": globalTestOperation},
		"abort":    {"transaction_id": globalTestTransaction, "operation_id": globalTestOperation},
	}
	result := map[string]json.RawMessage{}
	for op, value := range values {
		value["op"], value["runtime"] = op, "global-runtime"
		result[op] = transferJSON(t, value)
	}
	return result
}

func globalTestCounts() map[string]any {
	return map[string]any{"before": 1, "after": 0, "added": 0, "removed": 1, "retained": 0, "updated": 0, "moved": 0, "duplicates_skipped": 0}
}
func globalTestReceipt() map[string]any {
	return map[string]any{
		"version": 1, "subject": "global", "operation_id": globalTestOperation, "operation_runtime": "global-runtime", "transaction_id": globalTestTransaction,
		"input_document_sha256": globalTestHash(globalTestDocument), "result_document_sha256": globalTestHash(globalTestDocument),
		"expected_revision": strings.Repeat("1", 64), "new_revision": strings.Repeat("4", 64), "context_revision": strings.Repeat("2", 64), "plan_digest": strings.Repeat("3", 64),
		"counts": globalTestCounts(), "changed": true, "outcome": "verified", "persistence": "persistent", "required_kinds": []string{},
	}
}
func globalTestResults(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	values := map[string]map[string]any{
		"describe": {"revision": strings.Repeat("1", 64), "context_revision": strings.Repeat("2", 64), "write_ready": true, "write_blocker": nil, "export": map[string]any{"snapshot_id": globalTestSnapshot, "document_sha256": globalTestHash(globalTestDocument), "size_bytes": len(globalTestDocument), "entries": 0, "expires_at": "2026-10-28T20:05:00Z"}, "export_error": nil, "limits": favoritesGlobalLimits, "locator_kinds": []string{"channel", "channel-name", "local-media", "plex-media", "vportal-media"}},
		"export":   {"snapshot_id": globalTestSnapshot, "revision": strings.Repeat("1", 64), "document_sha256": globalTestHash(globalTestDocument), "size_bytes": len(globalTestDocument), "offset": 0, "chunk_sha256": globalTestHash(globalTestDocument), "data_base64": base64.StdEncoding.EncodeToString([]byte(globalTestDocument)), "next_offset": len(globalTestDocument), "eof": true},
		"begin":    {"operation_id": globalTestOperation, "transaction_id": globalTestTransaction, "expected_revision": strings.Repeat("1", 64), "context_revision": strings.Repeat("2", 64), "document_sha256": globalTestHash(globalTestDocument), "size_bytes": len(globalTestDocument), "chunk_count": 1, "next_seq": 0, "received_bytes": 0, "idle_expires_at": "2026-10-28T20:01:00.000000001Z", "absolute_expires_at": "2026-10-28T20:05:00Z", "required_kinds": []string{}},
		"append":   {"operation_id": globalTestOperation, "transaction_id": globalTestTransaction, "seq": 0, "offset": 0, "chunk_sha256": globalTestHash(globalTestDocument), "next_seq": 1, "received_bytes": len(globalTestDocument), "duplicate": false},
		"preview":  {"operation_id": globalTestOperation, "transaction_id": globalTestTransaction, "expected_revision": strings.Repeat("1", 64), "context_revision": strings.Repeat("2", 64), "input_document_sha256": globalTestHash(globalTestDocument), "result_document_sha256": globalTestHash(globalTestDocument), "plan_digest": strings.Repeat("3", 64), "counts": globalTestCounts(), "resolution_counts": map[string]int{"resolved": 0, "missing": 0, "ambiguous": 0, "unsupported": 0}, "legacy_effects": map[string]int{"linked_removed": 0, "unmapped_retained": 0}, "ready": true, "blocker": nil, "page_offset": 0, "rows": []any{}, "next_offset": nil, "required_kinds": []string{}},
		"commit":   {"operation_id": globalTestOperation, "transaction_id": globalTestTransaction, "receipt": globalTestReceipt()},
		"status":   {"operation_id": globalTestOperation, "operation_runtime": "global-runtime", "transaction_id": globalTestTransaction, "state": "committed", "outcome": "verified", "receipt": globalTestReceipt()},
		"abort":    {"operation_id": globalTestOperation, "transaction_id": globalTestTransaction, "state": "aborted", "outcome": "not_applied"},
	}
	result := map[string]json.RawMessage{}
	for op, value := range values {
		value["version"], value["runtime"], value["op"], value["subject"] = 1, "global-runtime", op, "global"
		if op == "describe" {
			value["admission"] = map[string]any{"operation_id": globalTestOperation, "ttl_ms": 10000}
		}
		result[op] = transferJSON(t, value)
	}
	return result
}
func globalTestBound(t *testing.T, raw json.RawMessage) *favoritesGlobalRequest {
	t.Helper()
	q := parseFavoritesGlobalRequest(raw)
	if q == nil {
		t.Fatal("valid global request rejected")
	}
	return q
}

func TestFavoritesGlobalEightOperations(t *testing.T) {
	requests, results := globalTestRequests(t), globalTestResults(t)
	for op, raw := range requests {
		t.Run(op, func(t *testing.T) {
			q := globalTestBound(t, raw)
			for range 2 {
				if !validFavoritesGlobalResult(results[op], "ok", q) {
					t.Fatal("valid response/repeated ACK rejected")
				}
			}
			queued := transferJSON(t, map[string]any{"id": globalTestRequestID, "action": "favorites_global", "params": raw, "expires_at": 1793217900.5})
			if !validFavoritesGlobalEnvelope(queued) || validFavoritesTransferEnvelope(queued) {
				t.Fatal("new envelope invalid or accepted by legacy action gate")
			}
			if !validFavoritesGlobalClientEnvelope(transferJSON(t, map[string]any{"action": "favorites_global", "params": raw})) {
				t.Fatal("client envelope rejected")
			}
			ack := transferJSON(t, map[string]any{"id": globalTestRequestID, "status": "ok", "data": results[op]})
			if !validFavoritesGlobalResultEnvelope(ack, globalTestRequestID, q) || validFavoritesGlobalResultEnvelope(ack, strings.Repeat("0", 32), q) {
				t.Fatal("outer ACK correlation failed")
			}
			for _, status := range []string{"rejected", "unsupported", "unknown", ""} {
				if validFavoritesGlobalResult(results[op], status, q) {
					t.Fatal("success accepted under non-ok status")
				}
			}
		})
	}
}

func TestFavoritesGlobalExactRequestAndResponseFields(t *testing.T) {
	requests, results := globalTestRequests(t), globalTestResults(t)
	for op, request := range requests {
		q := globalTestBound(t, request)
		for field := range transferObject(t, request) {
			bad := transferObject(t, request)
			delete(bad, field)
			if parseFavoritesGlobalRequest(transferJSON(t, bad)) != nil {
				t.Errorf("%s missing request %s", op, field)
			}
			bad = transferObject(t, request)
			bad[field] = json.RawMessage("null")
			if parseFavoritesGlobalRequest(transferJSON(t, bad)) != nil {
				t.Errorf("%s null request %s", op, field)
			}
		}
		for field := range transferObject(t, results[op]) {
			bad := transferObject(t, results[op])
			delete(bad, field)
			if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
				t.Errorf("%s missing response %s", op, field)
			}
		}
		badRequest := transferObject(t, request)
		badRequest["extra"] = json.RawMessage("null")
		if parseFavoritesGlobalRequest(transferJSON(t, badRequest)) != nil {
			t.Errorf("%s extra request accepted", op)
		}
		for _, field := range []string{"extra", "runtime", "op", "subject"} {
			bad := transferObject(t, results[op])
			bad[field] = transferJSON(t, "wrong")
			if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
				t.Errorf("%s wrong response %s", op, field)
			}
		}
	}
}

func TestFavoritesGlobalUnchangedReceiptHasNoChanges(t *testing.T) {
	receipt := globalTestReceipt()
	receipt["changed"] = false
	receipt["new_revision"] = receipt["expected_revision"]
	if validFavoritesGlobalReceipt(transferJSON(t, receipt)) {
		t.Fatal("unchanged receipt accepted removed entries")
	}
	counts := map[string]any{"before": 1, "after": 1, "added": 0, "removed": 0, "retained": 1, "updated": 0, "moved": 0, "duplicates_skipped": 0}
	receipt["counts"] = counts
	if !validFavoritesGlobalReceipt(transferJSON(t, receipt)) {
		t.Fatal("valid unchanged receipt rejected")
	}
	for _, key := range []string{"updated", "moved"} {
		counts[key] = 1
		if validFavoritesGlobalReceipt(transferJSON(t, receipt)) {
			t.Errorf("unchanged receipt accepted %s", key)
		}
		counts[key] = 0
	}
}

func TestFavoritesGlobalStrictJSONAndNumbers(t *testing.T) {
	for _, raw := range []string{
		`{"op":"describe","runtime":"one","runtime":"two"}`,
		`{"op":"describe","runtime":"bad\ud800"}`,
		`{"op":"describe","runtime":"bad\n"}`,
		`{"op":"describe","runtime":"ok","extra":1e400}`,
	} {
		if parseFavoritesGlobalRequest([]byte(raw)) != nil {
			t.Fatal("invalid original JSON accepted")
		}
	}
	requests := globalTestRequests(t)
	for _, field := range []string{"size_bytes", "chunk_count", "budget_ms"} {
		for _, invalid := range []string{"true", `"1"`, "1.0", "1e0", "-1", "9007199254740993"} {
			bad := transferObject(t, requests["begin"])
			bad[field] = json.RawMessage(invalid)
			if parseFavoritesGlobalRequest(transferJSON(t, bad)) != nil {
				t.Errorf("accepted %s=%s", field, invalid)
			}
		}
	}
	for field, value := range map[string]any{"operation_id": "10000000-0000-1000-8000-000000000001", "document_sha256": strings.Repeat("A", 64), "mode": "append", "chunk_count": 256, "budget_ms": 600001} {
		bad := transferObject(t, requests["begin"])
		bad[field] = transferJSON(t, value)
		if parseFavoritesGlobalRequest(transferJSON(t, bad)) != nil {
			t.Errorf("invalid begin %s accepted", field)
		}
	}
}

func TestFavoritesGlobalRequiredKindsAndNegotiation(t *testing.T) {
	request, describe := globalTestRequests(t)["begin"], globalTestResults(t)["describe"]
	if !validFavoritesGlobalCapability([]byte(`{"version":1}`)) || validFavoritesGlobalCapability([]byte(`{"version":1,"locator_kinds":[]}`)) {
		t.Fatal("marker must remain exact")
	}
	for _, raw := range []string{`["channel","channel"]`, `["plex-media","channel"]`, `["url"]`, `[1]`, `null`, `{}`} {
		bad := transferObject(t, request)
		bad["required_kinds"] = json.RawMessage(raw)
		if parseFavoritesGlobalRequest(transferJSON(t, bad)) != nil {
			t.Errorf("invalid kinds %s accepted", raw)
		}
	}
	for _, kinds := range [][]string{{}, {"channel-name"}, {"channel", "channel-name", "local-media", "plex-media", "vportal-media"}} {
		m := transferObject(t, request)
		m["required_kinds"] = transferJSON(t, kinds)
		q := globalTestBound(t, transferJSON(t, m))
		if !validFavoritesGlobalBeginNegotiation(q, describe) {
			t.Fatal("supported kinds rejected")
		}
		ack := transferObject(t, globalTestResults(t)["begin"])
		ack["required_kinds"] = transferJSON(t, kinds)
		if !validFavoritesGlobalResult(transferJSON(t, ack), "ok", q) {
			t.Fatal("exact kind echo rejected")
		}
		ack["required_kinds"] = transferJSON(t, []string{"local-media"})
		if validFavoritesGlobalResult(transferJSON(t, ack), "ok", q) {
			t.Fatal("different kind echo accepted")
		}
	}
	m := transferObject(t, request)
	m["required_kinds"] = transferJSON(t, []string{"plex-media"})
	q := globalTestBound(t, transferJSON(t, m))
	for field, value := range map[string]any{"locator_kinds": []string{"channel"}, "revision": strings.Repeat("f", 64), "context_revision": strings.Repeat("e", 64)} {
		bad := transferObject(t, describe)
		bad[field] = transferJSON(t, value)
		if validFavoritesGlobalBeginNegotiation(q, transferJSON(t, bad)) {
			t.Errorf("wrong negotiation %s admitted", field)
		}
	}
	bad := transferObject(t, describe)
	bad["write_ready"], bad["write_blocker"] = json.RawMessage("false"), transferJSON(t, "reconciliation_required")
	bad["admission"] = json.RawMessage("null")
	if validFavoritesGlobalBeginNegotiation(q, transferJSON(t, bad)) {
		t.Fatal("protected outcome admitted")
	}
}

func TestFavoritesGlobalChunkHashAndBoundaries(t *testing.T) {
	base := globalTestRequests(t)["append"]
	for _, length := range []int{1, 8192, 8193} {
		body := strings.Repeat("x", length)
		m := transferObject(t, base)
		m["data_base64"] = transferJSON(t, base64.StdEncoding.EncodeToString([]byte(body)))
		m["chunk_sha256"] = transferJSON(t, globalTestHash(body))
		if (parseFavoritesGlobalRequest(transferJSON(t, m)) != nil) != (length <= 8192) {
			t.Errorf("incorrect chunk boundary %d", length)
		}
	}
	for field, value := range map[string]any{"data_base64": "eB==", "chunk_sha256": globalTestHash("wrong"), "offset": 1048575, "seq": 256} {
		bad := transferObject(t, base)
		bad[field] = transferJSON(t, value)
		if parseFavoritesGlobalRequest(transferJSON(t, bad)) != nil {
			t.Errorf("invalid chunk %s accepted", field)
		}
	}
	bad := transferObject(t, base)
	bad["data_base64"] = transferJSON(t, base64.StdEncoding.EncodeToString([]byte("x"))+"\n")
	bad["chunk_sha256"] = transferJSON(t, globalTestHash("x"))
	if parseFavoritesGlobalRequest(transferJSON(t, bad)) != nil {
		t.Fatal("noncanonical base64 accepted")
	}
	q := globalTestBound(t, base)
	for field, value := range map[string]any{"chunk_sha256": globalTestHash("wrong"), "next_seq": 2, "received_bytes": 1, "transaction_id": globalTestSnapshot, "duplicate": 1} {
		bad := transferObject(t, globalTestResults(t)["append"])
		bad[field] = transferJSON(t, value)
		if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Errorf("uncorrelated append %s accepted", field)
		}
	}
	duplicate := transferObject(t, globalTestResults(t)["append"])
	duplicate["duplicate"] = json.RawMessage("true")
	if !validFavoritesGlobalResult(transferJSON(t, duplicate), "ok", q) {
		t.Fatal("identical duplicate ACK rejected")
	}
}

func TestFavoritesGlobalOriginalByteLimits(t *testing.T) {
	request := globalTestRequests(t)["describe"]
	padded := append(append([]byte{}, request...), bytes.Repeat([]byte(" "), maxFavoritesTransferEnvelopeBytes-len(request))...)
	if parseFavoritesGlobalRequest(padded) == nil || parseFavoritesGlobalRequest(append(padded, ' ')) != nil {
		t.Fatal("params original-byte cap not enforced")
	}
	queued := []byte(`{"id":"` + globalTestRequestID + `","action":"favorites_global","expires_at":1,"params":`)
	queued = append(append(queued, padded...), '}')
	if validFavoritesGlobalEnvelope(queued) {
		t.Fatal("oversized final queue accepted")
	}
	result := globalTestResults(t)["describe"]
	q := globalTestBound(t, request)
	padded = append(append([]byte{}, result...), bytes.Repeat([]byte(" "), maxFavoritesTransferMetadataBytes-len(result))...)
	if !validFavoritesGlobalResult(padded, "ok", q) || validFavoritesGlobalResult(append(padded, ' '), "ok", q) {
		t.Fatal("metadata original-byte cap not enforced")
	}
	ack := transferJSON(t, map[string]any{"id": globalTestRequestID, "status": "ok", "data": result})
	ack = append(ack, bytes.Repeat([]byte(" "), maxFavoritesTransferResultBytes-len(ack))...)
	if !validFavoritesGlobalResultEnvelope(ack, globalTestRequestID, q) || validFavoritesGlobalResultEnvelope(append(ack, ' '), globalTestRequestID, q) {
		t.Fatal("outer result original-byte cap not enforced")
	}
}

func TestFavoritesGlobalBoundedNegativeAcknowledgement(t *testing.T) {
	for op, raw := range globalTestRequests(t) {
		q := globalTestBound(t, raw)
		negative := transferJSON(t, map[string]any{"version": 1, "runtime": q.Runtime, "op": op, "subject": "global", "error": "runtime_changed"})
		for _, status := range []string{"rejected", "unsupported"} {
			if !validFavoritesGlobalResult(negative, status, q) {
				t.Fatal("valid bounded negative rejected")
			}
		}
		if validFavoritesGlobalResult(negative, "ok", q) {
			t.Fatal("error accepted as success")
		}
		bad := transferObject(t, negative)
		bad["error"] = transferJSON(t, "private detail")
		if validFavoritesGlobalResult(transferJSON(t, bad), "rejected", q) {
			t.Fatal("non-allowlisted error accepted")
		}
		negative = append(negative, bytes.Repeat([]byte(" "), maxFavoritesTransferMetadataBytes+1-len(negative))...)
		if validFavoritesGlobalResult(negative, "rejected", q) {
			t.Fatal("oversized negative ACK accepted")
		}
	}
}

func TestFavoritesGlobalExportAndPreviewSemantics(t *testing.T) {
	requests, results := globalTestRequests(t), globalTestResults(t)
	q := globalTestBound(t, requests["export"])
	for field, value := range map[string]any{"snapshot_id": globalTestOperation, "next_offset": 1, "eof": false, "document_sha256": globalTestHash("other")} {
		bad := transferObject(t, results["export"])
		bad[field] = transferJSON(t, value)
		if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Errorf("invalid export %s accepted", field)
		}
	}
	q = globalTestBound(t, requests["preview"])
	bad := transferObject(t, results["preview"])
	counts := globalTestCounts()
	counts["after"] = 1
	bad["counts"] = transferJSON(t, counts)
	if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
		t.Fatal("inconsistent count arithmetic accepted")
	}
	bad = transferObject(t, results["preview"])
	bad["ready"] = json.RawMessage("false")
	if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
		t.Fatal("blocked preview without reason accepted")
	}
	bad["blocker"] = transferJSON(t, "revision_conflict")
	if !validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
		t.Fatal("proper blocked preview rejected")
	}
	page := transferObject(t, results["preview"])
	page["counts"] = transferJSON(t, map[string]int{"before": 0, "after": 2, "added": 2, "removed": 0, "retained": 0, "updated": 0, "moved": 0, "duplicates_skipped": 0})
	page["resolution_counts"] = transferJSON(t, map[string]int{"resolved": 1, "missing": 1, "ambiguous": 0, "unsupported": 0})
	page["rows"] = transferJSON(t, []any{map[string]any{"entry_index": 0, "state": "resolved"}})
	page["next_offset"] = json.RawMessage("1")
	if !validFavoritesGlobalResult(transferJSON(t, page), "ok", q) {
		t.Fatal("bounded first preview page rejected")
	}
	page["next_offset"] = json.RawMessage("2")
	if validFavoritesGlobalResult(transferJSON(t, page), "ok", q) {
		t.Fatal("skipped preview entry accepted")
	}
	moved := map[string]int{"before": 3, "after": 3, "added": 1, "removed": 1, "retained": 2, "updated": 1, "moved": 2, "duplicates_skipped": 0}
	if !validFavoritesGlobalCounts(transferJSON(t, moved)) {
		t.Fatal("valid moved retained entries rejected")
	}
	moved["moved"] = 3
	if validFavoritesGlobalCounts(transferJSON(t, moved)) {
		t.Fatal("moved exceeds retained")
	}
}

func TestFavoritesGlobalReceiptAndRestartStatus(t *testing.T) {
	requests, results := globalTestRequests(t), globalTestResults(t)
	q := globalTestBound(t, requests["commit"])
	for field, value := range map[string]any{"operation_id": globalTestSnapshot, "transaction_id": globalTestSnapshot, "operation_runtime": "other", "plan_digest": strings.Repeat("f", 64), "changed": false, "required_kinds": []string{"url"}, "outcome": "unknown"} {
		bad := transferObject(t, results["commit"])
		receipt := globalTestReceipt()
		receipt[field] = value
		bad["receipt"] = transferJSON(t, receipt)
		if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Errorf("invalid receipt %s accepted", field)
		}
	}
	statusRequest := transferObject(t, requests["status"])
	statusRequest["runtime"] = transferJSON(t, "new-runtime")
	q = globalTestBound(t, transferJSON(t, statusRequest))
	status := transferObject(t, results["status"])
	status["runtime"] = transferJSON(t, "new-runtime")
	if !validFavoritesGlobalResult(transferJSON(t, status), "ok", q) {
		t.Fatal("persistent original-runtime receipt rejected after restart")
	}
	for _, state := range []string{"receiving", "ready", "applying", "expired", "aborted"} {
		bad := transferObject(t, transferJSON(t, status))
		bad["state"], bad["receipt"] = transferJSON(t, state), json.RawMessage("null")
		outcome := "not_applied"
		if state == "applying" {
			outcome = "unknown"
		}
		bad["outcome"] = transferJSON(t, outcome)
		if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Errorf("old runtime RAM state %s accepted", state)
		}
		bad["operation_runtime"] = transferJSON(t, "new-runtime")
		if !validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Errorf("same-runtime state %s rejected", state)
		}
	}
	status["state"], status["outcome"] = transferJSON(t, "not_found"), transferJSON(t, "unknown")
	status["operation_runtime"], status["transaction_id"], status["receipt"] = json.RawMessage("null"), json.RawMessage("null"), json.RawMessage("null")
	if !validFavoritesGlobalResult(transferJSON(t, status), "ok", q) {
		t.Fatal("missing receipt must stay unknown")
	}
	status["outcome"] = transferJSON(t, "not_applied")
	if validFavoritesGlobalResult(transferJSON(t, status), "ok", q) {
		t.Fatal("missing receipt used as not-applied proof")
	}
}

func TestFavoritesGlobalDescribeAdmissionIsExactAndConditional(t *testing.T) {
	describe := globalTestResults(t)["describe"]
	q := globalTestBound(t, globalTestRequests(t)["describe"])
	for _, admission := range []any{
		nil, true, []any{},
		map[string]any{"operation_id": globalTestOperation},
		map[string]any{"operation_id": globalTestOperation, "ttl_ms": 9999},
		map[string]any{"operation_id": globalTestOperation, "ttl_ms": 10001},
		map[string]any{"operation_id": globalTestOperation, "ttl_ms": true},
		map[string]any{"operation_id": globalTestOperation, "ttl_ms": "10000"},
		map[string]any{"operation_id": "10000000-0000-1000-8000-000000000001", "ttl_ms": 10000},
		map[string]any{"operation_id": globalTestOperation, "ttl_ms": 10000, "extra": nil},
	} {
		bad := transferObject(t, describe)
		bad["admission"] = transferJSON(t, admission)
		if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Fatalf("invalid admission accepted: %T", admission)
		}
	}
	blocked := transferObject(t, describe)
	blocked["write_ready"] = json.RawMessage("false")
	blocked["write_blocker"] = transferJSON(t, "busy")
	if validFavoritesGlobalResult(transferJSON(t, blocked), "ok", q) {
		t.Fatal("blocked writer advertised a live admission")
	}
	blocked["admission"] = json.RawMessage("null")
	if !validFavoritesGlobalResult(transferJSON(t, blocked), "ok", q) {
		t.Fatal("blocked writer with null admission rejected")
	}
}

func TestFavoritesGlobalBeginMustUseDescribedOperation(t *testing.T) {
	describe := globalTestResults(t)["describe"]
	request := globalTestRequests(t)["begin"]
	if !validFavoritesGlobalBeginNegotiation(globalTestBound(t, request), describe) {
		t.Fatal("minted operation rejected")
	}
	other := transferObject(t, request)
	other["operation_id"] = transferJSON(t, globalTestSnapshot)
	if validFavoritesGlobalBeginNegotiation(globalTestBound(t, transferJSON(t, other)), describe) {
		t.Fatal("caller-generated operation admitted")
	}
	// Consumption/expiry and duplicate payload ownership are player RAM duties;
	// this pure validator only binds the exact grant and current context claims.
}

func TestFavoritesGlobalPreviewMetadataConflictsAndLegacyEffects(t *testing.T) {
	q := globalTestBound(t, globalTestRequests(t)["preview"])
	base := transferObject(t, globalTestResults(t)["preview"])
	for _, badValue := range []json.RawMessage{json.RawMessage(`null`), json.RawMessage(`{"linked_removed":true,"unmapped_retained":0}`), json.RawMessage(`{"linked_removed":1.0,"unmapped_retained":0}`), json.RawMessage(`{"linked_removed":0,"unmapped_retained":101}`), json.RawMessage(`{"linked_removed":-1,"unmapped_retained":0}`), json.RawMessage(`{"linked_removed":0,"unmapped_retained":0,"extra":0}`)} {
		bad := transferObject(t, transferJSON(t, base))
		bad["legacy_effects"] = badValue
		if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Fatal("invalid legacy effects accepted")
		}
	}
	base["legacy_effects"] = json.RawMessage(`{"linked_removed":100,"unmapped_retained":100}`)
	if !validFavoritesGlobalResult(transferJSON(t, base), "ok", q) {
		t.Fatal("legacy effects incorrectly tied to global counts")
	}
	base["ready"] = json.RawMessage("false")
	base["blocker"] = transferJSON(t, "metadata_conflict")
	if validFavoritesGlobalResult(transferJSON(t, base), "ok", q) {
		t.Fatal("metadata conflict with empty result accepted")
	}
	base["counts"] = transferJSON(t, map[string]int{"before": 1, "after": 1, "added": 0, "removed": 0, "retained": 1, "updated": 0, "moved": 0, "duplicates_skipped": 0})
	base["resolution_counts"] = json.RawMessage(`{"resolved":1,"missing":0,"ambiguous":0,"unsupported":0}`)
	detail := map[string]any{"reason": "title_mismatch", "incoming_entry_index": 0}
	row := map[string]any{"entry_index": 0, "state": "resolved", "metadata_conflict": detail}
	base["rows"] = transferJSON(t, []any{row})
	if !validFavoritesGlobalResult(transferJSON(t, base), "ok", q) {
		t.Fatal("bounded metadata conflict rejected")
	}
	for field, value := range map[string]any{"ready": true, "blocker": "invalid_document", "rows": []any{map[string]any{"entry_index": 0, "state": "resolved"}}} {
		bad := transferObject(t, transferJSON(t, base))
		bad[field] = transferJSON(t, value)
		if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Fatal("contradictory metadata conflict", field)
		}
	}
	for _, badDetail := range []any{nil, map[string]any{"reason": "title_mismatch", "incoming_entry_index": true}, map[string]any{"reason": "title_mismatch", "incoming_entry_index": 1000}, map[string]any{"reason": "other", "incoming_entry_index": 0}, map[string]any{"reason": "title_mismatch", "incoming_entry_index": 0, "title": "private"}} {
		row["metadata_conflict"] = badDetail
		bad := transferObject(t, transferJSON(t, base))
		bad["rows"] = transferJSON(t, []any{row})
		if validFavoritesGlobalResult(transferJSON(t, bad), "ok", q) {
			t.Fatal("invalid conflict detail accepted")
		}
	}
	// A first page may contain no conflict when a later result row has it.
	base["counts"] = transferJSON(t, map[string]int{"before": 2, "after": 2, "added": 0, "removed": 0, "retained": 2, "updated": 0, "moved": 0, "duplicates_skipped": 0})
	base["resolution_counts"] = json.RawMessage(`{"resolved":2,"missing":0,"ambiguous":0,"unsupported":0}`)
	base["rows"] = json.RawMessage(`[{"entry_index":0,"state":"resolved"}]`)
	base["next_offset"] = json.RawMessage("1")
	if !validFavoritesGlobalResult(transferJSON(t, base), "ok", q) {
		t.Fatal("nonconflicting prefix page rejected")
	}
	base["rows"] = json.RawMessage(`[{"entry_index":0,"state":"resolved","metadata_conflict":{"reason":"title_mismatch","incoming_entry_index":0}},{"entry_index":1,"state":"resolved","metadata_conflict":{"reason":"title_mismatch","incoming_entry_index":0}}]`)
	base["next_offset"] = json.RawMessage("null")
	if validFavoritesGlobalResult(transferJSON(t, base), "ok", q) {
		t.Fatal("duplicate incoming conflict index accepted")
	}
}
