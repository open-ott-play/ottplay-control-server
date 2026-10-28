package control

// Bounded validation for docs/design/global-favorites-v1.schema.json
// Includes the confirmed one-use describe.admission extension.
// Routing recognizes this action; only a qualified player advertises support.
// A valid result is a bounded, correlated claim, not proof of disk persistence.
// The orchestrator must retain begin/preview context and bind every subsequent
// page/receipt to it; the player must derive required_kinds from received bytes.

import (
	"encoding/json"
	"slices"
	"time"
)

const maxFavoritesGlobalEntries = 1000
const maxFavoritesGlobalExportBytes = 65536
const maxFavoritesGlobalPreviewRows = 64

type favoritesGlobalRequest struct {
	Runtime, Op, Mode                         string
	OperationID, TransactionID, SnapshotID    string
	ExpectedRevision, ExpectedContextRevision string
	DocumentSHA256, PlanDigest, ChunkSHA256   string
	RequiredKinds                             []string
	SizeBytes, ChunkCount, BudgetMS           int
	Seq, Offset, ChunkBytes, MaxBytes         int
	PageOffset, MaxEntries                    int
}

var favoritesGlobalRequestFields = map[string][]string{
	"describe": {"op", "runtime"},
	"export":   {"op", "runtime", "snapshot_id", "offset", "max_bytes"},
	"begin":    {"op", "runtime", "operation_id", "mode", "expected_revision", "expected_context_revision", "document_sha256", "size_bytes", "chunk_count", "budget_ms", "required_kinds"},
	"append":   {"op", "runtime", "transaction_id", "seq", "offset", "chunk_sha256", "data_base64"},
	"preview":  {"op", "runtime", "transaction_id", "page_offset", "max_entries"},
	"commit":   {"op", "runtime", "transaction_id", "operation_id", "plan_digest"},
	"status":   {"op", "runtime", "operation_id"},
	"abort":    {"op", "runtime", "transaction_id", "operation_id"},
}

var favoritesGlobalResultFields = map[string][]string{
	"describe": {"version", "runtime", "op", "subject", "revision", "context_revision", "write_ready", "write_blocker", "export", "export_error", "limits", "locator_kinds", "admission"},
	"export":   {"version", "runtime", "op", "subject", "snapshot_id", "revision", "document_sha256", "size_bytes", "offset", "chunk_sha256", "data_base64", "next_offset", "eof"},
	"begin":    {"version", "runtime", "op", "subject", "operation_id", "transaction_id", "expected_revision", "context_revision", "document_sha256", "size_bytes", "chunk_count", "next_seq", "received_bytes", "idle_expires_at", "absolute_expires_at", "required_kinds"},
	"append":   {"version", "runtime", "op", "subject", "operation_id", "transaction_id", "seq", "offset", "chunk_sha256", "next_seq", "received_bytes", "duplicate"},
	"preview":  {"version", "runtime", "op", "subject", "operation_id", "transaction_id", "expected_revision", "context_revision", "input_document_sha256", "result_document_sha256", "plan_digest", "counts", "resolution_counts", "ready", "blocker", "page_offset", "rows", "next_offset", "required_kinds"},
	"commit":   {"version", "runtime", "op", "subject", "operation_id", "transaction_id", "receipt"},
	"status":   {"version", "runtime", "op", "subject", "operation_id", "operation_runtime", "transaction_id", "state", "outcome", "receipt"},
	"abort":    {"version", "runtime", "op", "subject", "operation_id", "transaction_id", "state", "outcome"},
}

var favoritesGlobalLimits = map[string]int{
	"max_document_bytes": maxFavoritesTransferDocumentBytes,
	"max_entries":        maxFavoritesGlobalEntries, "max_append_bytes": maxFavoritesTransferChunkBytes,
	"max_chunks": maxFavoritesTransferChunks, "max_export_bytes": maxFavoritesGlobalExportBytes,
	"max_preview_rows": maxFavoritesGlobalPreviewRows, "max_envelope_bytes": maxFavoritesTransferEnvelopeBytes,
	"max_metadata_bytes": maxFavoritesTransferMetadataBytes, "max_active_transactions": 1,
	"idle_ttl_seconds": 120, "absolute_ttl_seconds": 600,
	"max_receipts": 32, "max_receipt_ledger_bytes": 65536,
}

func favoritesGlobalString(raw json.RawMessage) string {
	var value string
	_ = json.Unmarshal(raw, &value)
	return value
}

func favoritesGlobalInt(raw json.RawMessage) int {
	var value int
	_ = json.Unmarshal(raw, &value)
	return value
}

func favoritesGlobalBool(raw json.RawMessage) bool {
	var value bool
	_ = json.Unmarshal(raw, &value)
	return value
}

func favoritesGlobalKinds(raw json.RawMessage) ([]string, bool) {
	rows, ok := inspectArray(raw, 5)
	if !ok {
		return nil, false
	}
	kinds := make([]string, 0, len(rows))
	for _, row := range rows {
		if !inspectEnum(row, "channel", "channel-name", "local-media", "plex-media", "vportal-media") {
			return nil, false
		}
		kind := favoritesGlobalString(row)
		if len(kinds) > 0 && kinds[len(kinds)-1] >= kind {
			return nil, false
		}
		kinds = append(kinds, kind)
	}
	return kinds, true
}

func validFavoritesGlobalCapability(raw json.RawMessage) bool {
	if len(raw) > maxFavoritesTransferMetadataBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "version")
	return ok && profileInteger(m["version"], 1, 1)
}

// Validate original params bytes, before normalization can erase duplicates or
// excess whitespace. The actual client/queued envelope needs its own size check.
func parseFavoritesGlobalRequest(raw json.RawMessage) *favoritesGlobalRequest {
	if len(raw) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	op := favoritesGlobalString(m["op"])
	fields, known := favoritesGlobalRequestFields[op]
	runtime, valid := inspectString(m["runtime"], 64)
	if err != nil || !known || !favoritesTransferFields(m, fields...) || !valid || !screenshotRuntimePattern.MatchString(runtime) {
		return nil
	}
	q := &favoritesGlobalRequest{Runtime: runtime, Op: op}
	for key, destination := range map[string]*string{
		"operation_id": &q.OperationID, "transaction_id": &q.TransactionID, "snapshot_id": &q.SnapshotID,
	} {
		if value, exists := m[key]; exists {
			if *destination, valid = favoritesTransferUUID(value); !valid {
				return nil
			}
		}
	}
	for key, destination := range map[string]*string{
		"expected_revision": &q.ExpectedRevision, "expected_context_revision": &q.ExpectedContextRevision,
		"document_sha256": &q.DocumentSHA256, "plan_digest": &q.PlanDigest, "chunk_sha256": &q.ChunkSHA256,
	} {
		if value, exists := m[key]; exists {
			if *destination, valid = favoritesTransferHash(value); !valid {
				return nil
			}
		}
	}
	for key, bounds := range map[string][2]int{
		"size_bytes": {1, maxFavoritesTransferDocumentBytes}, "chunk_count": {1, maxFavoritesTransferChunks},
		"budget_ms": {1, 600000}, "seq": {0, maxFavoritesTransferChunks - 1},
		"offset": {0, maxFavoritesTransferDocumentBytes - 1}, "max_bytes": {1, maxFavoritesGlobalExportBytes},
		"page_offset": {0, maxFavoritesGlobalEntries}, "max_entries": {1, maxFavoritesGlobalPreviewRows},
	} {
		if value, exists := m[key]; exists && !profileInteger(value, bounds[0], bounds[1]) {
			return nil
		}
	}
	q.SizeBytes, q.ChunkCount = favoritesGlobalInt(m["size_bytes"]), favoritesGlobalInt(m["chunk_count"])
	q.BudgetMS, q.Seq = favoritesGlobalInt(m["budget_ms"]), favoritesGlobalInt(m["seq"])
	q.Offset, q.MaxBytes = favoritesGlobalInt(m["offset"]), favoritesGlobalInt(m["max_bytes"])
	q.PageOffset, q.MaxEntries = favoritesGlobalInt(m["page_offset"]), favoritesGlobalInt(m["max_entries"])
	switch op {
	case "begin":
		if !inspectEnum(m["mode"], "merge", "replace") || q.ChunkCount > q.SizeBytes || q.SizeBytes > q.ChunkCount*maxFavoritesTransferChunkBytes {
			return nil
		}
		q.Mode = favoritesGlobalString(m["mode"])
		q.RequiredKinds, valid = favoritesGlobalKinds(m["required_kinds"])
		if !valid {
			return nil
		}
	case "append":
		chunk, valid := favoritesTransferBase64(m["data_base64"], q.ChunkSHA256, maxFavoritesTransferChunkBytes)
		if !valid || q.Offset+len(chunk) > maxFavoritesTransferDocumentBytes {
			return nil
		}
		q.ChunkBytes = len(chunk)
	}
	return q
}

func validFavoritesGlobalClientEnvelope(raw json.RawMessage) bool {
	if len(raw) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "action", "params")
	return ok && inspectEnum(m["action"], "favorites_global") && parseFavoritesGlobalRequest(m["params"]) != nil
}

func validFavoritesGlobalEnvelope(raw json.RawMessage) bool {
	if len(raw) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "action", "params", "expires_at")
	id, valid := inspectString(m["id"], 32)
	return ok && valid && commandID.MatchString(id) && inspectEnum(m["action"], "favorites_global") && numberValue(m["expires_at"], 1, inspectMaxInteger, false) && parseFavoritesGlobalRequest(m["params"]) != nil
}

// Outer ACK gate. Validate original data bytes, not re-marshaled data.
func validFavoritesGlobalResultEnvelope(raw json.RawMessage, requestID string, q *favoritesGlobalRequest) bool {
	if !commandID.MatchString(requestID) || len(raw) > maxFavoritesTransferResultBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "status", "data")
	return ok && inspectEnum(m["id"], requestID) && validFavoritesGlobalResult(m["data"], favoritesGlobalString(m["status"]), q)
}

// This optional preflight checks a validated describe against a begin descriptor.
// It is not a substitute for player-side current support and document checks.
func validFavoritesGlobalBeginNegotiation(q *favoritesGlobalRequest, describe json.RawMessage) bool {
	if q == nil || q.Op != "begin" || !validFavoritesGlobalResult(describe, "ok", &favoritesGlobalRequest{Runtime: q.Runtime, Op: "describe"}) {
		return false
	}
	m, _ := decodeObject(describe)
	kinds, _ := favoritesGlobalKinds(m["locator_kinds"])
	limits, _ := decodeObject(m["limits"])
	admission, _ := decodeObject(m["admission"])
	if favoritesGlobalString(admission["operation_id"]) != q.OperationID || !favoritesGlobalBool(m["write_ready"]) || favoritesGlobalString(m["revision"]) != q.ExpectedRevision || favoritesGlobalString(m["context_revision"]) != q.ExpectedContextRevision || q.SizeBytes > favoritesGlobalInt(limits["max_document_bytes"]) || q.ChunkCount > favoritesGlobalInt(limits["max_chunks"]) || q.SizeBytes > q.ChunkCount*favoritesGlobalInt(limits["max_append_bytes"]) || q.BudgetMS > 1000*favoritesGlobalInt(limits["absolute_ttl_seconds"]) {
		return false
	}
	for _, required := range q.RequiredKinds {
		if !slices.Contains(kinds, required) {
			return false
		}
	}
	return true
}

func validFavoritesGlobalCounts(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "before", "after", "added", "removed", "retained", "updated", "moved", "duplicates_skipped")
	if !ok {
		return false
	}
	for _, value := range m {
		if !profileInteger(value, 0, maxFavoritesGlobalEntries) {
			return false
		}
	}
	retained := favoritesGlobalInt(m["retained"])
	return favoritesGlobalInt(m["before"]) == favoritesGlobalInt(m["removed"])+retained && favoritesGlobalInt(m["after"]) == favoritesGlobalInt(m["added"])+retained && favoritesGlobalInt(m["updated"]) <= retained && favoritesGlobalInt(m["moved"]) <= retained
}

func validFavoritesGlobalReceipt(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "version", "subject", "operation_id", "operation_runtime", "transaction_id", "input_document_sha256", "result_document_sha256", "expected_revision", "new_revision", "context_revision", "plan_digest", "counts", "changed", "outcome", "persistence", "required_kinds")
	if !ok || !profileInteger(m["version"], 1, 1) || !inspectEnum(m["subject"], "global") || !inspectEnum(m["outcome"], "verified") || !inspectEnum(m["persistence"], "persistent") || !validFavoritesGlobalCounts(m["counts"]) || !inspectBool(m["changed"], false) {
		return false
	}
	runtime, valid := inspectString(m["operation_runtime"], 64)
	if !valid || !screenshotRuntimePattern.MatchString(runtime) {
		return false
	}
	for _, key := range []string{"operation_id", "transaction_id"} {
		if _, valid := favoritesTransferUUID(m[key]); !valid {
			return false
		}
	}
	for _, key := range []string{"input_document_sha256", "result_document_sha256", "expected_revision", "new_revision", "context_revision", "plan_digest"} {
		if _, valid := favoritesTransferHash(m[key]); !valid {
			return false
		}
	}
	_, valid = favoritesGlobalKinds(m["required_kinds"])
	if !favoritesGlobalBool(m["changed"]) {
		counts, _ := decodeObject(m["counts"])
		for _, key := range []string{"added", "removed", "updated", "moved"} {
			if favoritesGlobalInt(counts[key]) != 0 {
				return false
			}
		}
	}
	return valid && favoritesGlobalBool(m["changed"]) == (favoritesGlobalString(m["new_revision"]) != favoritesGlobalString(m["expected_revision"]))
}

func validFavoritesGlobalLimits(raw json.RawMessage) bool {
	m, err := decodeObject(raw)
	if err != nil || len(m) != len(favoritesGlobalLimits) {
		return false
	}
	for key, maximum := range favoritesGlobalLimits {
		if !profileInteger(m[key], 1, maximum) {
			return false
		}
	}
	return favoritesGlobalInt(m["idle_ttl_seconds"]) <= favoritesGlobalInt(m["absolute_ttl_seconds"])
}

func favoritesGlobalTime(raw json.RawMessage) (time.Time, bool) {
	value, ok := inspectString(raw, 64)
	parsed, err := time.Parse(time.RFC3339Nano, value)
	return parsed, ok && err == nil
}

func validFavoritesGlobalDescription(m map[string]json.RawMessage) bool {
	if !inspectBool(m["write_ready"], false) || !validFavoritesGlobalLimits(m["limits"]) {
		return false
	}
	if _, valid := favoritesGlobalKinds(m["locator_kinds"]); !valid {
		return false
	}
	if favoritesGlobalBool(m["write_ready"]) {
		admission, ok := inspectObject(m["admission"], "operation_id", "ttl_ms")
		_, id := favoritesTransferUUID(admission["operation_id"])
		if !ok || !id || !profileInteger(admission["ttl_ms"], 10000, 10000) || !inspectNull(m["write_blocker"]) {
			return false
		}
	} else if !inspectNull(m["admission"]) || !inspectEnum(m["write_blocker"], "busy", "policy_denied", "unsupported_persistence", "storage_unavailable", "limits_exceeded", "reconciliation_required") {
		return false
	}
	if inspectNull(m["export"]) {
		return inspectEnum(m["export_error"], "unportable_export", "limits_exceeded", "storage_unavailable")
	}
	x, ok := inspectObject(m["export"], "snapshot_id", "document_sha256", "size_bytes", "entries", "expires_at")
	_, id := favoritesTransferUUID(x["snapshot_id"])
	_, hash := favoritesTransferHash(x["document_sha256"])
	_, expires := favoritesGlobalTime(x["expires_at"])
	return ok && inspectNull(m["export_error"]) && id && hash && expires && profileInteger(x["size_bytes"], 1, maxFavoritesTransferDocumentBytes) && profileInteger(x["entries"], 0, maxFavoritesGlobalEntries)
}

func validFavoritesGlobalPreview(m map[string]json.RawMessage, q *favoritesGlobalRequest) bool {
	if !validFavoritesGlobalCounts(m["counts"]) || !inspectBool(m["ready"], false) || !profileInteger(m["page_offset"], q.PageOffset, q.PageOffset) {
		return false
	}
	if _, valid := favoritesGlobalKinds(m["required_kinds"]); !valid {
		return false
	}
	if favoritesGlobalBool(m["ready"]) {
		if !inspectNull(m["blocker"]) {
			return false
		}
	} else if !inspectEnum(m["blocker"], "revision_conflict", "context_changed", "invalid_document", "policy_denied", "unsupported_persistence", "storage_unavailable", "limits_exceeded", "reconciliation_required") {
		return false
	}
	resolution, ok := inspectObject(m["resolution_counts"], "resolved", "missing", "ambiguous", "unsupported")
	if !ok {
		return false
	}
	total := 0
	for _, value := range resolution {
		if !profileInteger(value, 0, maxFavoritesGlobalEntries) {
			return false
		}
		total += favoritesGlobalInt(value)
	}
	counts, _ := decodeObject(m["counts"])
	if total != favoritesGlobalInt(counts["after"]) || q.PageOffset > total {
		return false
	}
	rows, ok := inspectArray(m["rows"], q.MaxEntries)
	if !ok || q.PageOffset+len(rows) > total || (q.PageOffset < total && len(rows) == 0) {
		return false
	}
	seen := map[string]int{}
	for i, row := range rows {
		x, valid := inspectObject(row, "entry_index", "state")
		if !valid || !profileInteger(x["entry_index"], q.PageOffset+i, q.PageOffset+i) || !inspectEnum(x["state"], "resolved", "missing", "ambiguous", "unsupported") {
			return false
		}
		state := favoritesGlobalString(x["state"])
		seen[state]++
		if seen[state] > favoritesGlobalInt(resolution[state]) {
			return false
		}
	}
	next := q.PageOffset + len(rows)
	if next == total {
		return inspectNull(m["next_offset"])
	}
	return profileInteger(m["next_offset"], next, next)
}

func validFavoritesGlobalResult(raw json.RawMessage, status string, q *favoritesGlobalRequest) bool {
	if q == nil || len(raw) > maxFavoritesTransferMetadataBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	fields, known := favoritesGlobalResultFields[q.Op]
	if err != nil || !known || !profileInteger(m["version"], 1, 1) || !inspectEnum(m["subject"], "global") || !inspectEnum(m["runtime"], q.Runtime) || !inspectEnum(m["op"], q.Op) {
		return false
	}
	if _, negative := m["error"]; negative {
		return (status == "rejected" || status == "unsupported") && favoritesTransferFields(m, "version", "runtime", "op", "subject", "error") && inspectEnum(m["error"], "invalid_request", "envelope_too_large", "unsupported", "runtime_changed", "busy", "expired", "operation_conflict", "transaction_not_found", "snapshot_not_found", "out_of_order", "chunk_conflict", "incomplete", "hash_mismatch", "revision_conflict", "context_changed", "invalid_document", "unportable_export", "not_ready", "policy_denied", "unsupported_persistence", "storage_unavailable", "limits_exceeded", "reconciliation_required")
	}
	if status != "ok" || !favoritesTransferFields(m, fields...) {
		return false
	}
	for _, key := range []string{"revision", "context_revision", "expected_revision", "document_sha256", "input_document_sha256", "result_document_sha256", "chunk_sha256", "plan_digest"} {
		if value, exists := m[key]; exists {
			if _, valid := favoritesTransferHash(value); !valid {
				return false
			}
		}
	}
	for key, expected := range map[string]string{"operation_id": q.OperationID, "transaction_id": q.TransactionID, "snapshot_id": q.SnapshotID} {
		if value, exists := m[key]; exists {
			if q.Op == "status" && key == "transaction_id" && inspectNull(value) {
				continue
			}
			id, valid := favoritesTransferUUID(value)
			if !valid || (expected != "" && id != expected) {
				return false
			}
		}
	}
	switch q.Op {
	case "describe":
		return validFavoritesGlobalDescription(m)
	case "export":
		if !profileInteger(m["size_bytes"], 1, maxFavoritesTransferDocumentBytes) || !profileInteger(m["offset"], q.Offset, q.Offset) || !profileInteger(m["next_offset"], 1, maxFavoritesTransferDocumentBytes) || !inspectBool(m["eof"], false) {
			return false
		}
		chunk, valid := favoritesTransferBase64(m["data_base64"], favoritesGlobalString(m["chunk_sha256"]), q.MaxBytes)
		next, size := favoritesGlobalInt(m["next_offset"]), favoritesGlobalInt(m["size_bytes"])
		if !valid || next != q.Offset+len(chunk) || next > size || favoritesGlobalBool(m["eof"]) != (next == size) {
			return false
		}
		return q.Offset != 0 || next != size || favoritesGlobalString(m["chunk_sha256"]) == favoritesGlobalString(m["document_sha256"])
	case "begin":
		kinds, valid := favoritesGlobalKinds(m["required_kinds"])
		idle, i := favoritesGlobalTime(m["idle_expires_at"])
		absolute, a := favoritesGlobalTime(m["absolute_expires_at"])
		return valid && slices.Equal(kinds, q.RequiredKinds) && i && a && !idle.After(absolute) && inspectEnum(m["expected_revision"], q.ExpectedRevision) && inspectEnum(m["context_revision"], q.ExpectedContextRevision) && inspectEnum(m["document_sha256"], q.DocumentSHA256) && profileInteger(m["size_bytes"], q.SizeBytes, q.SizeBytes) && profileInteger(m["chunk_count"], q.ChunkCount, q.ChunkCount) && profileInteger(m["next_seq"], 0, 0) && profileInteger(m["received_bytes"], 0, 0)
	case "append":
		return profileInteger(m["seq"], q.Seq, q.Seq) && profileInteger(m["offset"], q.Offset, q.Offset) && inspectEnum(m["chunk_sha256"], q.ChunkSHA256) && profileInteger(m["next_seq"], q.Seq+1, q.Seq+1) && profileInteger(m["received_bytes"], q.Offset+q.ChunkBytes, q.Offset+q.ChunkBytes) && inspectBool(m["duplicate"], false)
	case "preview":
		return validFavoritesGlobalPreview(m, q)
	case "commit":
		if !validFavoritesGlobalReceipt(m["receipt"]) {
			return false
		}
		receipt, _ := decodeObject(m["receipt"])
		return inspectEnum(receipt["operation_id"], q.OperationID) && inspectEnum(receipt["transaction_id"], q.TransactionID) && inspectEnum(receipt["operation_runtime"], q.Runtime) && inspectEnum(receipt["plan_digest"], q.PlanDigest)
	case "status":
		return validFavoritesGlobalStatus(m, q)
	case "abort":
		return inspectEnum(m["state"], "aborted") && inspectEnum(m["outcome"], "not_applied")
	}
	return false
}

func validFavoritesGlobalStatus(m map[string]json.RawMessage, q *favoritesGlobalRequest) bool {
	runtime := favoritesGlobalString(m["operation_runtime"])
	if !inspectNull(m["operation_runtime"]) && (!screenshotRuntimePattern.MatchString(runtime) || len(runtime) > 64) {
		return false
	}
	switch favoritesGlobalString(m["state"]) {
	case "committed":
		if !inspectEnum(m["outcome"], "verified") || !validFavoritesGlobalReceipt(m["receipt"]) {
			return false
		}
		receipt, _ := decodeObject(m["receipt"])
		return inspectEnum(receipt["operation_id"], q.OperationID) && inspectEnum(receipt["operation_runtime"], runtime) && inspectEnum(receipt["transaction_id"], favoritesGlobalString(m["transaction_id"]))
	case "not_found":
		return inspectEnum(m["outcome"], "unknown") && inspectNull(m["operation_runtime"]) && inspectNull(m["transaction_id"]) && inspectNull(m["receipt"])
	case "receiving", "ready", "expired", "aborted":
		return runtime == q.Runtime && !inspectNull(m["transaction_id"]) && inspectEnum(m["outcome"], "not_applied") && inspectNull(m["receipt"])
	case "applying":
		return runtime == q.Runtime && !inspectNull(m["transaction_id"]) && inspectEnum(m["outcome"], "unknown") && inspectNull(m["receipt"])
	case "unknown":
		return inspectEnum(m["outcome"], "unknown") && inspectNull(m["receipt"])
	}
	return false
}
