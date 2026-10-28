package control

// Bounded relay for transfer and current-Plex binding metadata. Validating a
// player capability never advertises it or creates/rebinds private identities.

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
	"unicode"
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
var favoritesTransferPMSPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$`)
var favoritesProvisionTargetPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)
var favoritesProvisionRevisionPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,15})$`)
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
	ChunkBytes, BudgetMS                    int
	TargetID, Mode                          string
	PriorScope                              *favoritesTransferScope
}

var favoritesTransferCapabilityLimits = map[string]int{
	"max_document_bytes": 1048576, "max_chunk_bytes": 8192, "max_chunks": 256,
	"max_envelope_bytes": 16384, "max_snapshot_response_bytes": 2097152,
	"max_active_stages": 1, "idle_ttl_seconds": 120, "absolute_ttl_seconds": 600,
	"max_plan_bytes": 65536, "max_metadata_bytes": 131072, "max_json_depth": 20,
	"max_json_nodes": 32768, "max_channel_lists": 256, "max_entries_per_list": 10000,
	"max_media_favorites": 1000, "max_receipt_entries": 32,
	"max_receipt_ledger_bytes": 65536, "max_retained_admissions_per_adapter_instance": 16,
	"max_unstarted_drafts": 1,
}

func validFavoritesTransferCapability(raw json.RawMessage) bool {
	if len(raw) > maxFavoritesTransferMetadataBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "version", "persistence", "codecs", "limits", "receipt_retention_seconds", "receipt_retention_origin", "receipt_retention_policy", "admission")
	if !ok || !profileInteger(m["version"], 1, 1) || !profileInteger(m["receipt_retention_seconds"], 1, 86400) || !inspectEnum(m["receipt_retention_origin"], "logical-reservation-plan") || !inspectEnum(m["receipt_retention_policy"], "up-to-count-and-byte-bounded") {
		return false
	}
	admission, a := inspectObject(m["admission"], "op", "max_unconsumed_grants", "admission_ttl_ms", "max_budget_ms", "operation_id")
	if !a || !inspectEnum(admission["op"], "prepare") || !profileInteger(admission["max_unconsumed_grants"], 1, 1) || !profileInteger(admission["admission_ttl_ms"], 1, 10000) || !profileInteger(admission["max_budget_ms"], 1, 300000) || !inspectEnum(admission["operation_id"], "player-generated-uuidv4") {
		return false
	}
	persistence, p := inspectArray(m["persistence"], 1)
	codecs, c := inspectArray(m["codecs"], 1)
	if !p || len(persistence) != 1 || !inspectEnum(persistence[0], "persistent") || !c || len(codecs) != 1 {
		return false
	}
	codec, c := inspectObject(codecs[0], "provider", "codecVersion", "locatorKinds", "targetScope", "coverage")
	if !c || !inspectEnum(codec["provider"], "plex") || !profileInteger(codec["codecVersion"], 1, 1) || !inspectEnum(codec["targetScope"], "current-verified-source") {
		return false
	}
	kinds, k := inspectArray(codec["locatorKinds"], 1)
	coverage, v := inspectArray(codec["coverage"], 2)
	if !k || len(kinds) != 1 || !inspectEnum(kinds[0], "plex-media") || !v || len(coverage) != 2 || !inspectEnum(coverage[0], "channelLists") || !inspectEnum(coverage[1], "mediaFavorites") {
		return false
	}
	limits, err := decodeObject(m["limits"])
	if err != nil || len(limits) != len(favoritesTransferCapabilityLimits) {
		return false
	}
	for field, maximum := range favoritesTransferCapabilityLimits {
		if !profileInteger(limits[field], 1, maximum) {
			return false
		}
	}
	var idle, absolute int
	_ = json.Unmarshal(limits["idle_ttl_seconds"], &idle)
	_ = json.Unmarshal(limits["absolute_ttl_seconds"], &absolute)
	return idle <= absolute
}

func favoritesTransferPublicText(raw json.RawMessage, token bool) (string, bool) {
	value, ok := favoritesTransferText(raw, 256)
	if !ok {
		return "", false
	}
	for _, character := range value {
		// Graphic/space excludes all Unicode C categories, including unassigned
		// code points. Control whitespace is also forbidden in display labels.
		if unicode.IsControl(character) || !unicode.IsGraphic(character) && !unicode.IsSpace(character) || token && unicode.IsSpace(character) {
			return "", false
		}
	}
	return value, true
}

func favoritesProvisionTarget(raw json.RawMessage) (string, bool) {
	value, ok := inspectString(raw, 128)
	return value, ok && favoritesProvisionTargetPattern.MatchString(value)
}

func favoritesProvisionRevision(raw json.RawMessage) (string, bool) {
	value, ok := inspectString(raw, 16)
	return value, ok && favoritesProvisionRevisionPattern.MatchString(value) && (len(value) < 16 || value <= "9007199254740991")
}

func validFavoritesTransferBindingCatalog(m map[string]json.RawMessage) bool {
	if !favoritesTransferFields(m, "version", "runtime", "op", "binding_revision", "targets", "bindings") {
		return false
	}
	if _, ok := favoritesTransferText(m["binding_revision"], 256); !ok {
		return false
	}
	targets, t := inspectArray(m["targets"], 1)
	bindings, b := inspectArray(m["bindings"], 256)
	if !t || len(targets) != 1 || !b {
		return false
	}
	target, t := inspectObject(targets[0], "target_id", "display", "provider", "codecVersion", "serverId")
	id, i := favoritesTransferPublicText(target["target_id"], true)
	_, display := favoritesTransferPublicText(target["display"], false)
	server, s := favoritesTransferText(target["serverId"], 256)
	if !t || !i || !display || !s || !favoritesTransferPMSPattern.MatchString(server) || !inspectEnum(target["provider"], "plex") || !profileInteger(target["codecVersion"], 1, 1) {
		return false
	}
	seen := map[favoritesTransferScope]bool{}
	profiles, sources := map[string]bool{}, map[string]bool{}
	for _, rawBinding := range bindings {
		binding, ok := inspectObject(rawBinding, "scope", "target_id", "codecVersion")
		scope := parseFavoritesTransferScope(binding["scope"])
		bindingTarget, valid := favoritesTransferPublicText(binding["target_id"], true)
		if !ok || scope == nil || scope.Provider != "plex" || seen[*scope] || !valid || bindingTarget != id || !profileInteger(binding["codecVersion"], 1, 1) {
			return false
		}
		// Registry row IDs are globally unique: one source has one parent,
		// and an ID cannot simultaneously identify a profile and a source.
		if scope.ProfileBindingID == scope.SourceBindingID || sources[scope.SourceBindingID] || sources[scope.ProfileBindingID] || profiles[scope.SourceBindingID] {
			return false
		}
		// Several exact public scope pairs may resolve to one physical target.
		// Keep them distinct; only the host can disambiguate the selected scope.
		seen[*scope] = true
		profiles[scope.ProfileBindingID] = true
		sources[scope.SourceBindingID] = true
	}
	return true
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
	case "prepare":
		if !favoritesTransferFields(params, "op", "runtime", "scope", "expected_revision", "binding_revision", "document_sha256", "size_bytes", "budget_ms") || !profileInteger(params["size_bytes"], 1, maxFavoritesTransferDocumentBytes) || !profileInteger(params["budget_ms"], 1, 300000) {
			return nil
		}
		v.Scope = parseFavoritesTransferScope(params["scope"])
		if v.Scope == nil || v.Scope.Provider != "plex" || v.Scope.ProfileBindingID == v.Scope.SourceBindingID {
			return nil
		}
		var base, binding, digest bool
		v.ExpectedRevision, base = favoritesTransferText(params["expected_revision"], 256)
		v.BindingRevision, binding = favoritesTransferText(params["binding_revision"], 256)
		v.DocumentSHA256, digest = favoritesTransferHash(params["document_sha256"])
		if base && binding && digest {
			_ = json.Unmarshal(params["size_bytes"], &v.SizeBytes)
			_ = json.Unmarshal(params["budget_ms"], &v.BudgetMS)
			return v
		}
	case "bindingcatalog":
		if favoritesTransferFields(params, "op", "runtime") {
			return v
		}
	case "bind":
		if !favoritesTransferFields(params, "op", "runtime", "scope", "target_id", "expected_binding_revision", "codecVersion") || !profileInteger(params["codecVersion"], 1, 1) {
			return nil
		}
		var target, revision bool
		v.TargetID, target = favoritesTransferPublicText(params["target_id"], true)
		v.BindingRevision, revision = favoritesTransferText(params["expected_binding_revision"], 256)
		v.Scope = parseFavoritesTransferScope(params["scope"])
		if target && revision && v.Scope != nil && v.Scope.Provider == "plex" && v.Scope.ProfileBindingID != v.Scope.SourceBindingID {
			return v
		}
	case "provision":
		if !inspectEnum(params["mode"], "create", "import", "pair", "replace-current") || !profileInteger(params["codecVersion"], 1, 1) {
			return nil
		}
		_ = json.Unmarshal(params["mode"], &v.Mode)
		fields := []string{"op", "runtime", "mode", "target_id", "expected_binding_revision", "codecVersion"}
		if v.Mode != "create" {
			fields = append(fields, "scope")
			v.Scope = parseFavoritesTransferScope(params["scope"])
			if v.Scope == nil || v.Scope.Provider != "plex" || v.Scope.ProfileBindingID == v.Scope.SourceBindingID {
				return nil
			}
		}
		if v.Mode == "replace-current" {
			fields = append(fields, "prior_scope")
			v.PriorScope = parseFavoritesTransferScope(params["prior_scope"])
			if v.PriorScope == nil || v.PriorScope.Provider != "plex" {
				return nil
			}
			ids := map[string]bool{v.Scope.ProfileBindingID: true, v.Scope.SourceBindingID: true, v.PriorScope.ProfileBindingID: true, v.PriorScope.SourceBindingID: true}
			if len(ids) != 4 {
				return nil
			}
		}
		var target, revision bool
		v.TargetID, target = favoritesProvisionTarget(params["target_id"])
		v.BindingRevision, revision = favoritesProvisionRevision(params["expected_binding_revision"])
		if favoritesTransferFields(params, fields...) && target && revision {
			return v
		}
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
	// Provisioning requires its explicit operation/mode and captured revision;
	// no implicit name matching or destination UUID guessing is admitted.
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
		return favoritesTransferFields(m, "version", "runtime", "op", "error") && (status == "rejected" || status == "unsupported") && valid && (favoritesTransferErrors[code] || request.Op == "provision" && code == "outcome_unknown")
	}
	if status != "ok" || (request.Op != "snapshot" && len(raw) > maxFavoritesTransferMetadataBytes) {
		return false
	}
	switch request.Op {
	case "prepare":
		baseRevision, base := favoritesTransferText(m["expected_revision"], 256)
		bindingRevision, binding := favoritesTransferText(m["binding_revision"], 256)
		if !favoritesTransferFields(m, "version", "runtime", "op", "scope", "expected_revision", "binding_revision", "document_sha256", "size_bytes", "budget_ms", "operation_id", "admission_ttl_ms") || !sameFavoritesTransferScope(parseFavoritesTransferScope(m["scope"]), request.Scope) || !base || baseRevision != request.ExpectedRevision || !binding || bindingRevision != request.BindingRevision || !inspectEnum(m["document_sha256"], request.DocumentSHA256) || !profileInteger(m["size_bytes"], request.SizeBytes, request.SizeBytes) || !profileInteger(m["budget_ms"], request.BudgetMS, request.BudgetMS) {
			return false
		}
		_, operation := favoritesTransferUUID(m["operation_id"])
		return operation && profileInteger(m["admission_ttl_ms"], 1, min(10000, request.BudgetMS))
	case "bindingcatalog":
		return validFavoritesTransferBindingCatalog(m)
	case "bind":
		target, valid := favoritesTransferPublicText(m["target_id"], true)
		revision, r := favoritesTransferText(m["binding_revision"], 256)
		// This packet confirms an existing pairing only: no creation, new
		// revision or different-target rebinding can be reported as success.
		return favoritesTransferFields(m, "version", "runtime", "op", "binding_revision", "scope", "target_id", "paired") && valid && target == request.TargetID && r && revision == request.BindingRevision && sameFavoritesTransferScope(parseFavoritesTransferScope(m["scope"]), request.Scope) && bytes.Equal(bytes.TrimSpace(m["paired"]), []byte("true"))
	case "provision":
		if !favoritesTransferFields(m, "version", "runtime", "op", "mode", "paired", "changed", "binding_revision", "target_id", "scope", "persistence", "outcome") || !inspectEnum(m["mode"], request.Mode) || !bytes.Equal(bytes.TrimSpace(m["paired"]), []byte("true")) || !inspectEnum(m["persistence"], "persistent") || !inspectEnum(m["outcome"], "verified") {
			return false
		}
		target, valid := favoritesProvisionTarget(m["target_id"])
		revision, r := favoritesProvisionRevision(m["binding_revision"])
		scope := parseFavoritesTransferScope(m["scope"])
		var changed *bool
		if !valid || target != request.TargetID || !r || json.Unmarshal(m["changed"], &changed) != nil || changed == nil || scope == nil || scope.Provider != "plex" || scope.ProfileBindingID == scope.SourceBindingID {
			return false
		}
		// Binding verification is separate from transfer commit receipts. A
		// create reply supplies player-generated identities; all other modes
		// must echo the caller's exact scope without inventing a new pairing.
		return (request.Mode == "create" || sameFavoritesTransferScope(scope, request.Scope)) && (*changed == (revision != request.BindingRevision))
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
