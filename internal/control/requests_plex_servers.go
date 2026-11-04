package control

import (
	"bytes"
	"encoding/json"
	"regexp"
	"unicode"
)

const maxPlexServersResultBytes = 64 * 1024

var plexServerIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$`)
var plexServerContextPattern = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,256}$`)

// The relay retains correlation only. The native owner alone can establish
// context authority, eligibility and the applied temporary activation boundary.
type plexServersRequest struct {
	Runtime, Op, ServerID, Context string
}

func validPlexServersCapability(raw json.RawMessage) bool {
	if !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "version", "operations", "max_servers")
	if !ok || !profileInteger(m["version"], 1, 1) || !profileInteger(m["max_servers"], 1, 1) {
		return false
	}
	ops, ok := inspectArray(m["operations"], 2)
	if !ok || len(ops) != 2 {
		return false
	}
	return (inspectEnum(ops[0], "list") && inspectEnum(ops[1], "select")) ||
		(inspectEnum(ops[0], "select") && inspectEnum(ops[1], "list"))
}

func parsePlexServersRequest(raw json.RawMessage) *plexServersRequest {
	if len(raw) > MaxBodyBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	q := &plexServersRequest{Runtime: settingString(m["runtime"]), Op: settingString(m["op"])}
	if err != nil || !profileInteger(m["version"], 1, 1) || !screenshotRuntimePattern.MatchString(q.Runtime) {
		return nil
	}
	switch q.Op {
	case "list":
		if favoritesTransferFields(m, "version", "runtime", "op") {
			return q
		}
	case "select":
		q.ServerID, q.Context = settingString(m["server_id"]), settingString(m["context"])
		if favoritesTransferFields(m, "version", "runtime", "op", "server_id", "context") &&
			plexServerIDPattern.MatchString(q.ServerID) && plexServerContextPattern.MatchString(q.Context) {
			return q
		}
	}
	return nil
}

func plexServerName(raw json.RawMessage) bool {
	name, ok := inspectString(raw, 512)
	if !ok || name == "" {
		return false
	}
	for _, character := range name {
		if unicode.IsControl(character) {
			return false
		}
	}
	return true
}

func validPlexServersResult(raw json.RawMessage, status string, q *plexServersRequest) bool {
	if q == nil || !screenshotRuntimePattern.MatchString(q.Runtime) || (q.Op != "list" && q.Op != "select") ||
		len(raw) > maxPlexServersResultBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || settingString(m["runtime"]) != q.Runtime || settingString(m["op"]) != q.Op {
		return false
	}
	if settingString(m["state"]) == "error" {
		return (status == "rejected" || status == "unsupported") &&
			favoritesTransferFields(m, "version", "runtime", "op", "state", "error") &&
			inspectEnum(m["error"], "invalid_request", "unsupported", "busy", "stale", "source_changed",
				"authorization_conflict", "unavailable", "restricted", "expired", "cancelled", "limit_exceeded",
				"activation_failed", "outcome_unknown")
	}
	if status != "ok" || settingString(m["state"]) != "ready" {
		return false
	}
	if q.Op == "select" {
		return favoritesTransferFields(m, "version", "runtime", "op", "state", "server_id", "context", "activated", "persisted", "playback_confirmed") &&
			plexServerIDPattern.MatchString(q.ServerID) && plexServerContextPattern.MatchString(q.Context) &&
			settingString(m["server_id"]) == q.ServerID && settingString(m["context"]) == q.Context &&
			bytes.Equal(bytes.TrimSpace(m["activated"]), []byte("true")) &&
			bytes.Equal(bytes.TrimSpace(m["persisted"]), []byte("false")) &&
			bytes.Equal(bytes.TrimSpace(m["playback_confirmed"]), []byte("false"))
	}
	if !favoritesTransferFields(m, "version", "runtime", "op", "state", "servers") {
		return false
	}
	rows, ok := inspectArray(m["servers"], 1)
	if !ok {
		return false
	}
	for _, rawRow := range rows {
		row, ok := inspectObject(rawRow, "server_id", "name", "context", "expires_in_ms")
		if !ok || !plexServerIDPattern.MatchString(settingString(row["server_id"])) ||
			!plexServerContextPattern.MatchString(settingString(row["context"])) || !plexServerName(row["name"]) ||
			!profileInteger(row["expires_in_ms"], 1, 120000) {
			return false
		}
	}
	return true
}

func validPlexServersResultEnvelope(raw []byte, id string, q *plexServersRequest) bool {
	// Check the original bytes, including JSON escaping and whitespace, before
	// decoding or projecting data. Compact reserialization cannot prove this cap.
	if len(raw) > maxPlexServersResultBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "status", "data")
	return ok && commandID.MatchString(id) && settingString(m["id"]) == id &&
		validPlexServersResult(m["data"], settingString(m["status"]), q)
}
