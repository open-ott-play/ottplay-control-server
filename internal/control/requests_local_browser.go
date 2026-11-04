package control

import (
	"encoding/json"
	"strings"
	"unicode"
)

const maxLocalBrowserEntries = 64
const maxLocalBrowserEnvelopeBytes = 64 * 1024

// Only the request's correlation fields are retained. The player owns the
// browser view and held-source grants; the relay never interprets their tokens.
type localBrowserRequest struct {
	Runtime, Op, Generation   string
	EntryID, Cursor, SelectID string
}

var localBrowserErrors = map[string]bool{
	"invalid_request": true, "unsupported": true, "busy": true, "stale": true,
	"source_changed": true, "unavailable": true, "restricted": true,
	"expired": true, "cancelled": true, "limit_exceeded": true,
}

func localBrowserToken(raw json.RawMessage) (string, bool) {
	var value string
	if json.Unmarshal(raw, &value) != nil || !localMediaIDPattern.MatchString(value) {
		return "", false
	}
	return value, true
}

func localBrowserOptionalToken(raw json.RawMessage) bool {
	if string(raw) == "null" {
		return true
	}
	_, valid := localBrowserToken(raw)
	return valid
}

// Browser labels are display text, not path selectors. Do not normalize them
// or impose the legacy Local source-basename restrictions on this new shape.
func localBrowserName(raw json.RawMessage) bool {
	var value string
	return profileText(raw, 512) && json.Unmarshal(raw, &value) == nil &&
		value != "" && !strings.ContainsFunc(value, unicode.IsControl)
}

func localBrowserOperations(raw json.RawMessage, required ...string) bool {
	var operations []string
	if json.Unmarshal(raw, &operations) != nil || len(operations) != len(required) {
		return false
	}
	wanted := make(map[string]bool, len(required))
	for _, op := range required {
		wanted[op] = true
	}
	for _, op := range operations {
		if !wanted[op] {
			return false
		}
		delete(wanted, op)
	}
	return len(wanted) == 0
}

func validLocalBrowserCapability(raw json.RawMessage) bool {
	m, err := decodeObject(raw)
	return err == nil && validFavoritesTransferJSON(raw) &&
		favoritesTransferFields(m, "version", "operations", "max_entries") &&
		profileInteger(m["version"], 1, 1) && profileInteger(m["max_entries"], maxLocalBrowserEntries, maxLocalBrowserEntries) &&
		localBrowserOperations(m["operations"], "roots", "list", "more", "select")
}

func parseLocalBrowserRequest(raw json.RawMessage) *localBrowserRequest {
	if len(raw) > MaxBodyBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	v := &localBrowserRequest{Op: settingString(m["op"]), Runtime: settingString(m["runtime"])}
	if err != nil || !profileInteger(m["version"], 1, 1) || !screenshotRuntimePattern.MatchString(v.Runtime) {
		return nil
	}
	if v.Op == "roots" {
		if favoritesTransferFields(m, "version", "runtime", "op") {
			return v
		}
		return nil
	}
	var valid bool
	v.Generation, valid = localBrowserToken(m["generation"])
	if !valid {
		return nil
	}
	switch v.Op {
	case "list":
		if favoritesTransferFields(m, "version", "runtime", "op", "generation", "entry_id") {
			v.EntryID, valid = localBrowserToken(m["entry_id"])
		} else if favoritesTransferFields(m, "version", "runtime", "op", "generation", "cursor") {
			v.Cursor, valid = localBrowserToken(m["cursor"])
		} else {
			return nil
		}
	case "more", "select":
		if !favoritesTransferFields(m, "version", "runtime", "op", "generation", "select_id") {
			return nil
		}
		v.SelectID, valid = localBrowserToken(m["select_id"])
	default:
		return nil
	}
	if !valid {
		return nil
	}
	return v
}

func validLocalBrowserResult(raw json.RawMessage, status string, request *localBrowserRequest) bool {
	if len(raw) > maxLocalBrowserEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || settingString(m["runtime"]) != request.Runtime || settingString(m["op"]) != request.Op {
		return false
	}
	if settingString(m["state"]) == "error" {
		return (status == "rejected" || status == "unsupported") && favoritesTransferFields(m, "version", "runtime", "op", "state", "error") &&
			localBrowserErrors[settingString(m["error"])]
	}
	if status != "ok" || settingString(m["state"]) != "ready" {
		return false
	}
	generation, valid := localBrowserToken(m["generation"])
	if !valid {
		return false
	}
	if request.Op == "select" {
		source, sourceErr := decodeObject(m["source"])
		_, idValid := localBrowserToken(source["id"])
		_, handleValid := localBrowserToken(source["handle"])
		return favoritesTransferFields(m, "version", "runtime", "op", "state", "generation", "source", "persisted", "activated") &&
			generation == request.Generation && string(m["persisted"]) == "false" && string(m["activated"]) == "false" &&
			sourceErr == nil && favoritesTransferFields(source, "id", "name", "kind", "handle", "stable_identity") &&
			idValid && handleValid && localBrowserName(source["name"]) && settingString(source["kind"]) == "folder" &&
			(string(source["stable_identity"]) == "true" || string(source["stable_identity"]) == "false")
	}
	if !favoritesTransferFields(m, "version", "runtime", "op", "state", "generation", "kind", "entries", "parent_id", "select_id", "next_cursor", "partial", "limit_reached") ||
		(request.Generation != "" && generation == request.Generation) ||
		!localBrowserOptionalToken(m["parent_id"]) || !localBrowserOptionalToken(m["next_cursor"]) ||
		(string(m["partial"]) != "true" && string(m["partial"]) != "false") ||
		(string(m["limit_reached"]) != "true" && string(m["limit_reached"]) != "false") ||
		(string(m["limit_reached"]) == "true" && string(m["partial"]) != "true") {
		return false
	}
	kind := settingString(m["kind"])
	entryKind := "directory"
	if (request.Op == "roots" && kind != "roots") || (request.Op == "more" && kind != "directories") {
		return false
	}
	if kind == "roots" {
		if string(m["parent_id"]) != "null" || string(m["select_id"]) != "null" {
			return false
		}
		entryKind = "root"
	} else {
		if kind != "directories" {
			return false
		}
		if _, valid := localBrowserToken(m["select_id"]); !valid {
			return false
		}
	}
	var entries []json.RawMessage
	if len(m["entries"]) == 0 || m["entries"][0] != '[' || json.Unmarshal(m["entries"], &entries) != nil || len(entries) > maxLocalBrowserEntries {
		return false
	}
	seen := map[string]bool{}
	for _, entry := range entries {
		row, err := decodeObject(entry)
		id, valid := localBrowserToken(row["id"])
		if err != nil || !favoritesTransferFields(row, "id", "name", "kind") || !valid || seen[id] ||
			!localBrowserName(row["name"]) || settingString(row["kind"]) != entryKind {
			return false
		}
		seen[id] = true
	}
	return true
}
