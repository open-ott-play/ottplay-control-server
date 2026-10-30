package control

// This is a bounded relay, not a torrent client or a catalog document parser.
// The player owns operation deduplication, revision fencing and persistence;
// the caller binds snapshot totals/hashes across separately relayed chunks.

import (
	"encoding/base64"
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
	"unicode"
)

const maxTorrentCatalogDocumentBytes = 8 * 1024 * 1024
const maxTorrentCatalogChunkBytes = 6144
const maxTorrentCatalogResultBytes = 65536
const torrentCatalogError = "Torrent catalog request failed."

var torrentCatalogToken = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,128}$`)
var torrentCatalogID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

var torrentCatalogRequestFields = map[string][]string{
	"read":         {"op", "runtime"},
	"read_chunk":   {"op", "runtime", "snapshot_id", "offset", "max_bytes"},
	"metadata":     {"op", "runtime", "operation_id", "source"},
	"status":       {"op", "runtime", "operation_id"},
	"cancel":       {"op", "runtime", "operation_id"},
	"write_begin":  {"op", "runtime", "operation_id", "revision", "mode", "size_bytes", "sha256"},
	"write_chunk":  {"op", "runtime", "operation_id", "offset", "data_b64"},
	"write_commit": {"op", "runtime", "operation_id"},
	"play":         {"op", "runtime", "operation_id", "revision", "entry_id", "file_index"},
}

var torrentCatalogReasons = map[string]bool{
	"invalid_request": true, "unsupported": true, "policy_restricted": true,
	"runtime_changed": true, "source_changed": true, "revision_conflict": true,
	"snapshot_not_found": true, "operation_not_found": true, "operation_conflict": true,
	"busy": true, "expired": true, "cancelled": true, "metadata_failed": true,
	"metadata_timeout": true, "no_files": true, "invalid_document": true,
	"document_too_large": true, "hash_mismatch": true, "offset_mismatch": true,
	"incomplete": true, "catalog_mismatch": true, "entry_conflict": true,
	"entry_not_found": true, "file_not_found": true, "storage_failed": true,
	"effect_failed": true,
}

type torrentCatalogRequest struct {
	Runtime, Op, OperationID, SnapshotID, EntryID      string
	Offset, MaxBytes, ChunkBytes, SizeBytes, FileIndex int
}

func torrentCatalogMatch(raw json.RawMessage, pattern *regexp.Regexp) bool {
	value, ok := inspectString(raw, 128)
	return ok && pattern.MatchString(value)
}

// inspectEnum deliberately limits text to 96 bytes; runtime tokens may be 128.
func torrentCatalogEqual(raw json.RawMessage, expected string) bool {
	value, ok := inspectString(raw, 128)
	return ok && value == expected
}

func validTorrentCatalogSource(raw json.RawMessage) bool {
	value, ok := inspectString(raw, 8192)
	if !ok || value == "" {
		return false
	}
	for _, r := range value {
		if unicode.IsControl(r) || unicode.IsSpace(r) {
			return false
		}
	}
	if strings.HasPrefix(strings.ToLower(value), "magnet:?") {
		return true
	}
	u, err := url.Parse(value)
	return err == nil && (strings.EqualFold(u.Scheme, "http") || strings.EqualFold(u.Scheme, "https")) &&
		u.User == nil && u.Hostname() != "" && u.Opaque == "" && strings.HasSuffix(strings.ToLower(u.EscapedPath()), ".torrent")
}

func torrentCatalogChunk(raw json.RawMessage, maximum int) (int, bool) {
	value, ok := inspectString(raw, base64.StdEncoding.EncodedLen(maxTorrentCatalogChunkBytes))
	if !ok || value == "" {
		return 0, false
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(value)
	return len(decoded), err == nil && len(decoded) > 0 && len(decoded) <= maximum && base64.StdEncoding.EncodeToString(decoded) == value
}

func parseTorrentCatalogRequest(raw json.RawMessage) *torrentCatalogRequest {
	if len(raw) > MaxBodyBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	if err != nil || !torrentCatalogMatch(m["runtime"], torrentCatalogToken) {
		return nil
	}
	q := &torrentCatalogRequest{Runtime: favoritesGlobalString(m["runtime"]), Op: favoritesGlobalString(m["op"])}
	fields, ok := torrentCatalogRequestFields[q.Op]
	if !ok || !favoritesTransferFields(m, fields...) {
		return nil
	}
	if _, exists := m["operation_id"]; exists {
		q.OperationID, ok = favoritesTransferUUID(m["operation_id"])
		if !ok {
			return nil
		}
	}
	if revision, exists := m["revision"]; exists && !torrentCatalogMatch(revision, torrentCatalogToken) {
		return nil
	}
	switch q.Op {
	case "read_chunk":
		q.SnapshotID, ok = favoritesTransferUUID(m["snapshot_id"])
		if !ok || !profileInteger(m["offset"], 0, maxTorrentCatalogDocumentBytes-1) || !profileInteger(m["max_bytes"], 1, maxTorrentCatalogChunkBytes) {
			return nil
		}
		q.Offset, q.MaxBytes = favoritesGlobalInt(m["offset"]), favoritesGlobalInt(m["max_bytes"])
	case "metadata":
		if !validTorrentCatalogSource(m["source"]) {
			return nil
		}
	case "write_begin":
		_, hashOK := favoritesTransferHash(m["sha256"])
		if !hashOK || !inspectEnum(m["mode"], "merge", "replace") || !profileInteger(m["size_bytes"], 1, maxTorrentCatalogDocumentBytes) {
			return nil
		}
		q.SizeBytes = favoritesGlobalInt(m["size_bytes"])
	case "write_chunk":
		q.ChunkBytes, ok = torrentCatalogChunk(m["data_b64"], maxTorrentCatalogChunkBytes)
		if !ok || !profileInteger(m["offset"], 0, maxTorrentCatalogDocumentBytes-q.ChunkBytes) {
			return nil
		}
		q.Offset = favoritesGlobalInt(m["offset"])
	case "play":
		if !torrentCatalogMatch(m["entry_id"], torrentCatalogID) || !profileInteger(m["file_index"], 0, 999999999) {
			return nil
		}
		q.EntryID, q.FileIndex = favoritesGlobalString(m["entry_id"]), favoritesGlobalInt(m["file_index"])
	}
	return q
}

func validTorrentCatalogEnvelope(raw json.RawMessage) bool {
	if len(raw) > MaxBodyBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "action", "params", "expires_at")
	id, valid := inspectString(m["id"], 32)
	return ok && valid && commandID.MatchString(id) && inspectEnum(m["action"], "torrent_catalog") &&
		numberValue(m["expires_at"], 1, inspectMaxInteger, false) && parseTorrentCatalogRequest(m["params"]) != nil
}

func validTorrentCatalogCapability(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "version")
	return ok && validFavoritesTransferJSON(raw) && profileInteger(m["version"], 1, 1)
}

func validTorrentCatalogSnapshot(raw json.RawMessage, kind string) bool {
	m, ok := inspectObject(raw, "id", "kind", "revision", "sha256", "size_bytes")
	_, idOK := favoritesTransferUUID(m["id"])
	_, hashOK := favoritesTransferHash(m["sha256"])
	if !ok || !idOK || !hashOK || !inspectEnum(m["kind"], kind) || !profileInteger(m["size_bytes"], 1, maxTorrentCatalogDocumentBytes) {
		return false
	}
	return kind == "metadata" && inspectNull(m["revision"]) || kind == "catalog" && torrentCatalogMatch(m["revision"], torrentCatalogToken)
}

func validTorrentCatalogOperation(raw json.RawMessage, q *torrentCatalogRequest) bool {
	m, err := decodeObject(raw)
	if err != nil || !torrentCatalogEqual(m["id"], q.OperationID) {
		return false
	}
	kind, state := favoritesGlobalString(m["kind"]), favoritesGlobalString(m["state"])
	fields := []string{"id", "kind", "state", "next_offset", "snapshot", "reason"}
	if kind == "play" {
		fields = append(fields, "target")
	}
	if !favoritesTransferFields(m, fields...) ||
		(state == "failed" && !torrentCatalogReasons[favoritesGlobalString(m["reason"])]) ||
		(state != "failed" && !inspectNull(m["reason"])) {
		return false
	}
	if q.Op == "metadata" && kind != "metadata" || strings.HasPrefix(q.Op, "write_") && kind != "write" {
		return false
	}
	switch kind {
	case "metadata":
		if !inspectNull(m["next_offset"]) {
			return false
		}
		if state == "ready" {
			return validTorrentCatalogSnapshot(m["snapshot"], "metadata")
		}
		return (state == "pending" || state == "failed" || state == "cancelled") && inspectNull(m["snapshot"])
	case "write":
		if state == "uploading" {
			if !profileInteger(m["next_offset"], 0, maxTorrentCatalogDocumentBytes) || !inspectNull(m["snapshot"]) {
				return false
			}
			next := favoritesGlobalInt(m["next_offset"])
			return (q.Op != "write_begin" || next <= q.SizeBytes) && (q.Op != "write_chunk" || next >= q.Offset+q.ChunkBytes)
		}
		if !inspectNull(m["next_offset"]) {
			return false
		}
		if state == "committed" {
			return validTorrentCatalogSnapshot(m["snapshot"], "catalog")
		}
		return (state == "failed" || state == "cancelled") && inspectNull(m["snapshot"])
	case "play":
		target, ok := inspectObject(m["target"], "catalog_id", "entry_id", "file_index")
		return ok && torrentCatalogMatch(target["catalog_id"], torrentCatalogID) && torrentCatalogMatch(target["entry_id"], torrentCatalogID) &&
			profileInteger(target["file_index"], 0, 999999999) && inspectNull(m["next_offset"]) && inspectNull(m["snapshot"]) &&
			(state == "pending" || state == "accepted" || state == "failed" || state == "cancelled")
	}
	return false
}

func validTorrentCatalogResultEnvelope(raw json.RawMessage, id string, q *torrentCatalogRequest) bool {
	// Keep the existing outer HTTP cap; enforce the smaller data cap on raw
	// bytes, before compaction. Duplicate fields/invalid Unicode cannot hide in it.
	if q == nil || !commandID.MatchString(id) || len(raw) > maxResponseBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "status", "data")
	return ok && torrentCatalogEqual(m["id"], id) && validTorrentCatalogResult(m["data"], favoritesGlobalString(m["status"]), q)
}

func validTorrentCatalogResult(raw json.RawMessage, status string, q *torrentCatalogRequest) bool {
	if q == nil || len(raw) > maxTorrentCatalogResultBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || !torrentCatalogEqual(m["runtime"], q.Runtime) || !inspectEnum(m["op"], q.Op) {
		return false
	}
	if status == "rejected" || status == "unsupported" {
		return favoritesTransferFields(m, "version", "runtime", "op", "error", "reason") &&
			inspectEnum(m["error"], torrentCatalogError) && torrentCatalogReasons[favoritesGlobalString(m["reason"])]
	}
	if status != "ok" {
		return false
	}
	switch q.Op {
	case "read":
		return favoritesTransferFields(m, "version", "runtime", "op", "snapshot") && validTorrentCatalogSnapshot(m["snapshot"], "catalog")
	case "read_chunk":
		if !favoritesTransferFields(m, "version", "runtime", "op", "snapshot_id", "offset", "data_b64", "next_offset") ||
			!torrentCatalogEqual(m["snapshot_id"], q.SnapshotID) || !profileInteger(m["offset"], q.Offset, q.Offset) {
			return false
		}
		n, ok := torrentCatalogChunk(m["data_b64"], q.MaxBytes)
		end := q.Offset + n
		return ok && end <= maxTorrentCatalogDocumentBytes && (inspectNull(m["next_offset"]) ||
			end < maxTorrentCatalogDocumentBytes && profileInteger(m["next_offset"], end, end))
	case "play":
		return favoritesTransferFields(m, "version", "runtime", "op", "operation_id", "accepted", "catalog_id", "entry_id", "file_index") &&
			torrentCatalogEqual(m["operation_id"], q.OperationID) && string(m["accepted"]) == "true" &&
			torrentCatalogMatch(m["catalog_id"], torrentCatalogID) && torrentCatalogEqual(m["entry_id"], q.EntryID) && profileInteger(m["file_index"], q.FileIndex, q.FileIndex)
	default:
		return favoritesTransferFields(m, "version", "runtime", "op", "operation") && validTorrentCatalogOperation(m["operation"], q)
	}
}
