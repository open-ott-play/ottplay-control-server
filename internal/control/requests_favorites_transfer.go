package control

// Bounded relay for the agreed seven-operation transfer subset. Capability
// advertisement and destination binding remain gated on actual player support.

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"math"
	"reflect"
	"regexp"
	"time"
	"unicode/utf8"
)

const maxFavoritesTransferDocumentBytes = 1024 * 1024
const maxFavoritesTransferChunkBytes = 8192
const maxFavoritesTransferChunks = 256
const maxFavoritesTransferEnvelopeBytes = 16384
const maxFavoritesTransferResultBytes = 2 * 1024 * 1024
const maxFavoritesTransferMetadataBytes = 128 * 1024
const maxFavoritesTransferStores = 64
const maxFavoritesTransferCount = 1000000
const maxFavoritesTransferDiagnostics = 128

var favoritesTransferUUIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
var favoritesTransferSHAPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var favoritesTransferErrors = map[string]bool{
	"invalid_request": true, "envelope_too_large": true, "runtime_changed": true,
	"busy": true, "expired": true, "operation_conflict": true,
	"transaction_not_found": true, "out_of_order": true, "chunk_conflict": true,
	"incomplete": true, "hash_mismatch": true, "revision_conflict": true,
	"binding_changed": true, "policy_denied": true, "unsupported_persistence": true,
	"codec_error": true, "not_ready": true, "reconciliation_required": true,
	"storage_unavailable": true, "limits_exceeded": true,
}

type favoritesTransferScope struct {
	ProfileBindingID string
	SourceBindingID  string
	Provider         string
}

// Retain this expectation with both a queued request and its completed receipt.
// Commit does not carry scope/document_sha256: the orchestrator must additionally
// bind those result fields to its retained begin/preview context.
type favoritesTransferRequest struct {
	Runtime, Op, OperationID, TransactionID string
	ExpectedRevision, BindingRevision       string
	DocumentSHA256, PlanDigest              string
	Scope                                   *favoritesTransferScope
	SizeBytes, ChunkCount, Seq, Offset      int
	ChunkBytes                              int
}

func favoritesTransferFields(value map[string]json.RawMessage, keys ...string) bool {
	if len(value) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, exists := value[key]; !exists {
			return false
		}
	}
	return true
}

func favoritesTransferText(raw json.RawMessage, maximum int) (string, bool) {
	value, ok := inspectString(raw, maximum)
	return value, ok && len(value) > 0
}

func favoritesTransferUUID(raw json.RawMessage) (string, bool) {
	value, ok := inspectString(raw, 36)
	return value, ok && favoritesTransferUUIDPattern.MatchString(value)
}

func favoritesTransferHash(raw json.RawMessage) (string, bool) {
	value, ok := inspectString(raw, 64)
	return value, ok && favoritesTransferSHAPattern.MatchString(value)
}

func parseFavoritesTransferScope(raw json.RawMessage) *favoritesTransferScope {
	m, ok := inspectObject(raw, "profileBindingId", "sourceBindingId", "provider")
	if !ok {
		return nil
	}
	profile, p := favoritesTransferUUID(m["profileBindingId"])
	source, s := favoritesTransferUUID(m["sourceBindingId"])
	provider, v := inspectString(m["provider"], 32)
	if !p || !s || !v || !inspectEnum(m["provider"], "m3u", "vportal", "plex", "offline") {
		return nil
	}
	return &favoritesTransferScope{ProfileBindingID: profile, SourceBindingID: source, Provider: provider}
}

func sameFavoritesTransferScope(a, b *favoritesTransferScope) bool {
	return a != nil && b != nil && *a == *b
}

func favoritesTransferBase64(raw json.RawMessage, hash string, maximum int) ([]byte, bool) {
	var encoded string
	if json.Unmarshal(raw, &encoded) != nil || len(encoded) > base64.StdEncoding.EncodedLen(maximum) {
		return nil, false
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(decoded) == 0 || len(decoded) > maximum || base64.StdEncoding.EncodeToString(decoded) != encoded {
		return nil, false
	}
	digest := sha256.Sum256(decoded)
	return decoded, hex.EncodeToString(digest[:]) == hash
}

// encoding/json alone accepts duplicate nested keys and repairs invalid UTF-16.
// Validate the bounded bytes without changing the document's hash or ordering.
func validFavoritesTransferJSON(raw []byte) bool {
	if !utf8.Valid(raw) || !json.Valid(raw) {
		return false
	}
	for i := 0; i < len(raw); i++ {
		if raw[i] != '"' {
			continue
		}
		start := i
		for i++; i < len(raw); i++ {
			if raw[i] == '\\' {
				i++
				continue
			}
			if raw[i] == '"' {
				break
			}
		}
		if i >= len(raw) || !profileText(raw[start:i+1], len(raw)) {
			return false
		}
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var value func(int) bool
	value = func(depth int) bool {
		if depth > 32 {
			return false
		}
		token, err := d.Token()
		if err != nil {
			return false
		}
		delim, container := token.(json.Delim)
		if !container {
			if number, ok := token.(json.Number); ok {
				finite, err := number.Float64()
				return err == nil && !math.IsNaN(finite) && !math.IsInf(finite, 0)
			}
			return true
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for d.More() {
				key, err := d.Token()
				name, ok := key.(string)
				if err != nil || !ok || seen[name] || !value(depth+1) {
					return false
				}
				seen[name] = true
			}
			end, err := d.Token()
			return err == nil && end == json.Delim('}')
		case '[':
			for d.More() {
				if !value(depth + 1) {
					return false
				}
			}
			end, err := d.Token()
			return err == nil && end == json.Delim(']')
		}
		return false
	}
	if !value(0) {
		return false
	}
	_, err := d.Token()
	return err == io.EOF
}

func parseFavoritesTransferRequest(params map[string]json.RawMessage) *favoritesTransferRequest {
	serialized, err := json.Marshal(params)
	if err != nil || len(serialized) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(serialized) {
		return nil
	}
	v := &favoritesTransferRequest{}
	if json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) || json.Unmarshal(params["op"], &v.Op) != nil {
		return nil
	}
	switch v.Op {
	case "snapshot":
		v.Scope = parseFavoritesTransferScope(params["scope"])
		if favoritesTransferFields(params, "op", "runtime", "scope") && v.Scope != nil {
			return v
		}
	case "begin":
		if !favoritesTransferFields(params, "op", "runtime", "operation_id", "scope", "expected_revision", "binding_revision", "document_sha256", "size_bytes", "chunk_count", "persistence") || !inspectEnum(params["persistence"], "persistent") {
			return nil
		}
		var ok bool
		if v.OperationID, ok = favoritesTransferUUID(params["operation_id"]); !ok {
			return nil
		}
		if v.ExpectedRevision, ok = favoritesTransferText(params["expected_revision"], 256); !ok {
			return nil
		}
		if v.BindingRevision, ok = favoritesTransferText(params["binding_revision"], 256); !ok {
			return nil
		}
		if v.DocumentSHA256, ok = favoritesTransferHash(params["document_sha256"]); !ok {
			return nil
		}
		v.Scope = parseFavoritesTransferScope(params["scope"])
		if v.Scope == nil || !profileInteger(params["size_bytes"], 1, maxFavoritesTransferDocumentBytes) || !profileInteger(params["chunk_count"], 1, maxFavoritesTransferChunks) {
			return nil
		}
		_ = json.Unmarshal(params["size_bytes"], &v.SizeBytes)
		_ = json.Unmarshal(params["chunk_count"], &v.ChunkCount)
		if v.ChunkCount <= v.SizeBytes && v.SizeBytes <= v.ChunkCount*maxFavoritesTransferChunkBytes {
			return v
		}
	case "append":
		if !favoritesTransferFields(params, "op", "runtime", "transaction_id", "seq", "offset", "chunk_sha256", "data_base64") || !profileInteger(params["seq"], 0, maxFavoritesTransferChunks-1) || !profileInteger(params["offset"], 0, maxFavoritesTransferDocumentBytes-1) {
			return nil
		}
		var ok bool
		if v.TransactionID, ok = favoritesTransferUUID(params["transaction_id"]); !ok {
			return nil
		}
		hash, ok := favoritesTransferHash(params["chunk_sha256"])
		if !ok {
			return nil
		}
		chunk, ok := favoritesTransferBase64(params["data_base64"], hash, maxFavoritesTransferChunkBytes)
		if !ok {
			return nil
		}
		_ = json.Unmarshal(params["seq"], &v.Seq)
		_ = json.Unmarshal(params["offset"], &v.Offset)
		v.ChunkBytes = len(chunk)
		if v.Offset+v.ChunkBytes <= maxFavoritesTransferDocumentBytes {
			return v
		}
	case "preview", "abort":
		id, ok := favoritesTransferUUID(params["transaction_id"])
		if favoritesTransferFields(params, "op", "runtime", "transaction_id") && ok {
			v.TransactionID = id
			return v
		}
	case "status":
		id, ok := favoritesTransferUUID(params["operation_id"])
		if favoritesTransferFields(params, "op", "runtime", "operation_id") && ok {
			v.OperationID = id
			return v
		}
	case "commit":
		if !favoritesTransferFields(params, "op", "runtime", "transaction_id", "operation_id", "expected_revision", "plan_digest") {
			return nil
		}
		var ok bool
		if v.TransactionID, ok = favoritesTransferUUID(params["transaction_id"]); !ok {
			return nil
		}
		if v.OperationID, ok = favoritesTransferUUID(params["operation_id"]); !ok {
			return nil
		}
		if v.ExpectedRevision, ok = favoritesTransferText(params["expected_revision"], 256); !ok {
			return nil
		}
		if v.PlanDigest, ok = favoritesTransferHash(params["plan_digest"]); ok {
			return v
		}
	}
	// bindingcatalog/bind await the explicit target_id wire contract. No implicit
	// name matching, destination UUID guessing or capability claim is admitted.
	return nil
}

// Called on the actual final queued envelope, not a caller-supplied size hint.
// A params-only check cannot account for IDs, expiry, escaping or timestamps.
func validFavoritesTransferEnvelope(raw json.RawMessage) bool {
	if len(raw) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	var id string
	params, parseErr := decodeObject(m["params"])
	return err == nil && parseErr == nil && favoritesTransferFields(m, "id", "action", "params", "expires_at") && inspectEnum(m["action"], "favorites_transfer") && json.Unmarshal(m["id"], &id) == nil && commandID.MatchString(id) && numberValue(m["expires_at"], 1, inspectMaxInteger, false) && parseFavoritesTransferRequest(params) != nil
}

func validFavoritesTransferCoverage(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "channelLists", "mediaFavorites", "remoteAliases", "history")
	return ok && inspectEnum(m["channelLists"], "complete-for-scope") && inspectEnum(m["mediaFavorites"], "complete-for-scope") && inspectEnum(m["remoteAliases"], "excluded-preserved") && inspectEnum(m["history"], "excluded-preserved")
}

func validFavoritesTransferCounts(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "channelLists", "channelMemberships", "mediaFavorites", "unresolved")
	if !ok {
		return false
	}
	for _, value := range m {
		if !profileInteger(value, 0, maxFavoritesTransferCount) {
			return false
		}
	}
	var channels, media, unresolved int
	_ = json.Unmarshal(m["channelMemberships"], &channels)
	_ = json.Unmarshal(m["mediaFavorites"], &media)
	_ = json.Unmarshal(m["unresolved"], &unresolved)
	return unresolved <= channels+media
}

func validFavoritesTransferDiagnostics(raw, counts json.RawMessage) bool {
	rows, ok := inspectArray(raw, maxFavoritesTransferDiagnostics)
	m, err := decodeObject(counts)
	var unresolved int
	if !ok || err != nil || json.Unmarshal(m["unresolved"], &unresolved) != nil || len(rows) > unresolved {
		return false
	}
	for _, rawRow := range rows {
		row, err := decodeObject(rawRow)
		if err != nil || !profileInteger(row["entry_index"], 0, maxFavoritesTransferCount) || !inspectEnum(row["reason"], "missing", "ambiguous", "legacy-unportable", "codec-unavailable") {
			return false
		}
		if inspectEnum(row["collection"], "channelLists") {
			if !favoritesTransferFields(row, "collection", "entry_index", "reason", "list_index") || !profileInteger(row["list_index"], 0, maxFavoritesTransferCount) {
				return false
			}
		} else if !inspectEnum(row["collection"], "mediaFavorites") || !favoritesTransferFields(row, "collection", "entry_index", "reason") {
			return false
		}
	}
	return true
}

func validFavoritesTransferStores(raw json.RawMessage, outcomes bool, outcome string) bool {
	rows, ok := inspectArray(raw, maxFavoritesTransferStores)
	if !ok || (!outcomes && len(rows) == 0) || (outcome == "verified" && len(rows) == 0) {
		return false
	}
	seen := map[string]bool{}
	var verified, notApplied, partial, unknown int
	for _, row := range rows {
		field := "expected_revision"
		if outcomes {
			field = "outcome"
		}
		m, ok := inspectObject(row, "id", field)
		id, valid := favoritesTransferText(m["id"], 256)
		if !ok || !valid || seen[id] {
			return false
		}
		seen[id] = true
		if !outcomes {
			if _, valid := favoritesTransferText(m[field], 256); !valid {
				return false
			}
			continue
		}
		state, _ := inspectString(m[field], 16)
		switch state {
		case "verified":
			verified++
		case "not_applied":
			notApplied++
		case "partial":
			partial++
		case "unknown":
			unknown++
		default:
			return false
		}
	}
	if !outcomes {
		return true
	}
	switch outcome {
	case "verified":
		return verified == len(rows)
	case "not_applied":
		return notApplied == len(rows)
	case "partial":
		return unknown == 0 && (partial > 0 || verified > 0 && notApplied > 0)
	case "unknown":
		// Overall receipt evidence can be unknown even if some stores were read.
		return true
	}
	return false
}

func validFavoritesTransferReceipt(raw json.RawMessage, operationID string) bool {
	m, ok := inspectObject(raw, "operation_id", "scope", "new_revision", "document_sha256", "plan_digest", "counts", "outcome", "persistence")
	_, revision := favoritesTransferText(m["new_revision"], 256)
	_, document := favoritesTransferHash(m["document_sha256"])
	_, plan := favoritesTransferHash(m["plan_digest"])
	return ok && inspectEnum(m["operation_id"], operationID) && parseFavoritesTransferScope(m["scope"]) != nil && revision && document && plan && validFavoritesTransferCounts(m["counts"]) && inspectEnum(m["outcome"], "verified") && inspectEnum(m["persistence"], "persistent")
}

func validFavoritesTransferCommitReceipt(result map[string]json.RawMessage, operationID string) bool {
	if !validFavoritesTransferReceipt(result["receipt"], operationID) {
		return false
	}
	receipt, err := decodeObject(result["receipt"])
	if err != nil {
		return false
	}
	// The repeated public claim and the persisted receipt must agree field for
	// field. Compare parsed values so object ordering/whitespace is immaterial.
	for _, field := range []string{"operation_id", "scope", "new_revision", "document_sha256", "plan_digest", "counts", "outcome", "persistence"} {
		var claim, persisted any
		if json.Unmarshal(result[field], &claim) != nil || json.Unmarshal(receipt[field], &persisted) != nil || !reflect.DeepEqual(claim, persisted) {
			return false
		}
	}
	return true
}

func validFavoritesTransferProgress(m map[string]json.RawMessage, chunks, size int) bool {
	if !profileInteger(m["next_seq"], 0, chunks) || !profileInteger(m["received_bytes"], 0, size) {
		return false
	}
	var next, received int
	_ = json.Unmarshal(m["next_seq"], &next)
	_ = json.Unmarshal(m["received_bytes"], &received)
	idle, i := inspectString(m["idle_expires_at"], 64)
	absolute, a := inspectString(m["absolute_expires_at"], 64)
	idleAt, ie := time.Parse(time.RFC3339Nano, idle)
	absoluteAt, ae := time.Parse(time.RFC3339Nano, absolute)
	return i && a && ie == nil && ae == nil && !idleAt.After(absoluteAt) && received >= next && received <= next*maxFavoritesTransferChunkBytes
}

func validFavoritesTransferResult(raw json.RawMessage, status string, request *favoritesTransferRequest) bool {
	if request == nil || len(raw) > maxFavoritesTransferResultBytes || (request.Op != "snapshot" && len(raw) > maxFavoritesTransferMetadataBytes) || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || !inspectEnum(m["runtime"], request.Runtime) || !inspectEnum(m["op"], request.Op) {
		return false
	}
	if problem, exists := m["error"]; exists {
		if len(raw) > maxFavoritesTransferMetadataBytes {
			return false
		}
		code, valid := inspectString(problem, 64)
		// The authenticated outer request ID correlates this minimal error. It
		// never proves that a commit was not applied and cannot permit replay.
		return favoritesTransferFields(m, "version", "runtime", "op", "error") && (status == "rejected" || status == "unsupported") && valid && favoritesTransferErrors[code]
	}
	if status != "ok" || (request.Op != "snapshot" && len(raw) > maxFavoritesTransferMetadataBytes) {
		return false
	}
	switch request.Op {
	case "snapshot":
		if !favoritesTransferFields(m, "version", "runtime", "op", "scope", "revision", "binding_revision", "document_sha256", "size_bytes", "document_base64") || !sameFavoritesTransferScope(parseFavoritesTransferScope(m["scope"]), request.Scope) || !profileInteger(m["size_bytes"], 1, maxFavoritesTransferDocumentBytes) {
			return false
		}
		if _, ok := favoritesTransferText(m["revision"], 256); !ok {
			return false
		}
		if _, ok := favoritesTransferText(m["binding_revision"], 256); !ok {
			return false
		}
		hash, ok := favoritesTransferHash(m["document_sha256"])
		if !ok {
			return false
		}
		document, ok := favoritesTransferBase64(m["document_base64"], hash, maxFavoritesTransferDocumentBytes)
		var size int
		_ = json.Unmarshal(m["size_bytes"], &size)
		if !ok || len(document) != size || !validFavoritesTransferJSON(document) {
			return false
		}
		inner, ok := inspectObject(document, "schema", "scope", "coverage", "channelLists", "mediaFavorites")
		// Full provider/list/locator semantics belong to the portable codec;
		// relay validation still binds the exact hashed document to this scope.
		return ok && profileInteger(inner["schema"], 1, 1) && sameFavoritesTransferScope(parseFavoritesTransferScope(inner["scope"]), request.Scope) && validFavoritesTransferCoverage(inner["coverage"])
	case "begin":
		if !favoritesTransferFields(m, "version", "runtime", "op", "operation_id", "transaction_id", "next_seq", "received_bytes", "idle_expires_at", "absolute_expires_at") || !inspectEnum(m["operation_id"], request.OperationID) || !profileInteger(m["next_seq"], 0, request.ChunkCount) || !profileInteger(m["received_bytes"], 0, request.SizeBytes) {
			return false
		}
		if _, ok := favoritesTransferUUID(m["transaction_id"]); !ok {
			return false
		}
		return validFavoritesTransferProgress(m, request.ChunkCount, request.SizeBytes)
	case "append":
		return favoritesTransferFields(m, "version", "runtime", "op", "transaction_id", "next_seq", "received_bytes") && inspectEnum(m["transaction_id"], request.TransactionID) && profileInteger(m["next_seq"], request.Seq+1, request.Seq+1) && profileInteger(m["received_bytes"], request.Offset+request.ChunkBytes, request.Offset+request.ChunkBytes)
	case "preview":
		if !favoritesTransferFields(m, "version", "runtime", "op", "transaction_id", "plan_digest", "base_revision", "binding_revision", "coverage", "counts", "unresolved", "ready", "persistence", "stores") || !inspectEnum(m["transaction_id"], request.TransactionID) || !inspectBool(m["ready"], false) || !inspectEnum(m["persistence"], "persistent") {
			return false
		}
		_, plan := favoritesTransferHash(m["plan_digest"])
		_, base := favoritesTransferText(m["base_revision"], 256)
		_, binding := favoritesTransferText(m["binding_revision"], 256)
		return plan && base && binding && validFavoritesTransferCoverage(m["coverage"]) && validFavoritesTransferCounts(m["counts"]) && validFavoritesTransferDiagnostics(m["unresolved"], m["counts"]) && validFavoritesTransferStores(m["stores"], false, "")
	case "commit":
		if !inspectEnum(m["transaction_id"], request.TransactionID) || !inspectEnum(m["operation_id"], request.OperationID) || parseFavoritesTransferScope(m["scope"]) == nil || !inspectEnum(m["persistence"], "persistent") {
			return false
		}
		outcome, _ := inspectString(m["outcome"], 16)
		if !validFavoritesTransferStores(m["per_store"], true, outcome) {
			return false
		}
		if outcome != "verified" {
			return favoritesTransferFields(m, "version", "runtime", "op", "transaction_id", "operation_id", "scope", "outcome", "persistence", "per_store")
		}
		_, revision := favoritesTransferText(m["new_revision"], 256)
		_, document := favoritesTransferHash(m["document_sha256"])
		return favoritesTransferFields(m, "version", "runtime", "op", "transaction_id", "operation_id", "scope", "outcome", "persistence", "per_store", "new_revision", "document_sha256", "plan_digest", "counts", "receipt") && revision && document && inspectEnum(m["plan_digest"], request.PlanDigest) && validFavoritesTransferCounts(m["counts"]) && validFavoritesTransferCommitReceipt(m, request.OperationID)
	case "status":
		if !inspectEnum(m["operation_id"], request.OperationID) || !inspectEnum(m["persistence"], "persistent") {
			return false
		}
		if inspectEnum(m["state"], "receiving", "ready") {
			if _, ok := favoritesTransferUUID(m["transaction_id"]); !ok || !validFavoritesTransferProgress(m, maxFavoritesTransferChunks, maxFavoritesTransferDocumentBytes) {
				return false
			}
			if inspectEnum(m["state"], "receiving") {
				return favoritesTransferFields(m, "version", "runtime", "op", "operation_id", "transaction_id", "persistence", "state", "next_seq", "received_bytes", "idle_expires_at", "absolute_expires_at")
			}
			_, plan := favoritesTransferHash(m["plan_digest"])
			_, base := favoritesTransferText(m["base_revision"], 256)
			_, binding := favoritesTransferText(m["binding_revision"], 256)
			return favoritesTransferFields(m, "version", "runtime", "op", "operation_id", "transaction_id", "persistence", "state", "next_seq", "received_bytes", "idle_expires_at", "absolute_expires_at", "ready", "plan_digest", "base_revision", "binding_revision") && plan && base && binding && bytes.Equal(bytes.TrimSpace(m["ready"]), []byte("true")) && profileInteger(m["next_seq"], 1, maxFavoritesTransferChunks) && profileInteger(m["received_bytes"], 1, maxFavoritesTransferDocumentBytes)
		}
		if inspectEnum(m["state"], "expired", "aborted") {
			_, transaction := favoritesTransferUUID(m["transaction_id"])
			stores, empty := inspectArray(m["per_store"], 0)
			return favoritesTransferFields(m, "version", "runtime", "op", "operation_id", "transaction_id", "persistence", "scope", "outcome", "per_store", "state") && transaction && parseFavoritesTransferScope(m["scope"]) != nil && inspectEnum(m["outcome"], "not_applied") && empty && len(stores) == 0
		}
		if !inspectEnum(m["state"], "committed") {
			return false
		}
		outcome, _ := inspectString(m["outcome"], 16)
		if !validFavoritesTransferStores(m["per_store"], true, outcome) {
			return false
		}
		if outcome == "verified" {
			return favoritesTransferFields(m, "version", "runtime", "op", "operation_id", "state", "outcome", "persistence", "per_store", "receipt") && validFavoritesTransferReceipt(m["receipt"], request.OperationID)
		}
		return favoritesTransferFields(m, "version", "runtime", "op", "operation_id", "state", "outcome", "persistence", "per_store")
	case "abort":
		_, operation := favoritesTransferUUID(m["operation_id"])
		stores, empty := inspectArray(m["per_store"], 0)
		return favoritesTransferFields(m, "version", "runtime", "op", "transaction_id", "operation_id", "state", "outcome", "persistence", "per_store") && inspectEnum(m["transaction_id"], request.TransactionID) && operation && inspectEnum(m["state"], "aborted") && inspectEnum(m["outcome"], "not_applied") && inspectEnum(m["persistence"], "persistent") && empty && len(stores) == 0
	}
	return false
}
