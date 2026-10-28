package control

// Source preparation for the agreed favorites_global_items v1 three-operation
// packet. Controller support does not advertise player capability or establish
// receiver bindings. Portable entries remain public data, never playable URLs.

import (
	"encoding/json"
	"regexp"
	"strings"
)

const maxFavoritesGlobalItemsMetadata = 64 * 1024
const maxFavoritesGlobalItemsPage = 100

var favoritesGlobalItemsCursorPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,512}$`)
var favoritesGlobalProviderPattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,63}$`)
var favoritesGlobalIdentifierPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$`)
var favoritesGlobalPMSPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$`)
var favoritesGlobalRatingPattern = regexp.MustCompile(`^[1-9][0-9]{0,19}$`)
var favoritesGlobalReservedChannelPattern = regexp.MustCompile(`^channel:[0-9]+$`)
var favoritesGlobalDrivePattern = regexp.MustCompile(`^[A-Za-z]:`)

// Count UTF-16 units and use the exact ECMAScript edge-whitespace set, matching
// the existing portable codec. Never normalize identities or trim values.
func favoritesGlobalPortableText(raw json.RawMessage, maximum int) (string, bool) {
	var value *string
	if json.Unmarshal(raw, &value) != nil || value == nil || *value == "" {
		return "", false
	}
	const trim = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
	if strings.Trim(*value, trim) != *value || strings.Contains(*value, "://") {
		return "", false
	}
	units := 0
	for _, r := range *value {
		if r < 32 || r == 127 {
			return "", false
		}
		units++
		if r > 0xffff {
			units++
		}
		if units > maximum {
			return "", false
		}
	}
	return *value, true
}

func favoritesGlobalPortablePattern(raw json.RawMessage, pattern *regexp.Regexp) bool {
	var value *string
	return json.Unmarshal(raw, &value) == nil && value != nil && pattern.MatchString(*value)
}

// The identity excludes only the title. Full strict JSON validation precedes
// this entry boundary, including invalid Unicode and duplicate-key rejection.
func favoritesGlobalPortableIdentity(raw json.RawMessage) (string, bool) {
	if len(raw) > maxFavoritesGlobalItemsMetadata || !validFavoritesTransferJSON(raw) {
		return "", false
	}
	entry, ok := inspectObject(raw, "title", "origin", "locator")
	if !ok {
		return "", false
	}
	if _, ok = favoritesGlobalPortableText(entry["title"], 1024); !ok {
		return "", false
	}
	origin, ok := inspectObject(entry["origin"], "provider", "profile", "subprofile", "sourceAlias")
	if !ok || !favoritesGlobalPortablePattern(origin["provider"], favoritesGlobalProviderPattern) {
		return "", false
	}
	for _, key := range []string{"profile", "sourceAlias"} {
		if _, ok = favoritesGlobalPortableText(origin[key], 256); !ok {
			return "", false
		}
	}
	if !inspectNull(origin["subprofile"]) {
		if _, ok = favoritesGlobalPortableText(origin["subprofile"], 256); !ok {
			return "", false
		}
	}
	locator, err := decodeObject(entry["locator"])
	if err != nil {
		return "", false
	}
	provider := favoritesGlobalString(origin["provider"])
	switch {
	case provider == "plex":
		if !favoritesTransferFields(locator, "kind", "serverId", "ratingKey") || !inspectEnum(locator["kind"], "plex-media") || !favoritesGlobalPortablePattern(locator["serverId"], favoritesGlobalPMSPattern) || !favoritesGlobalPortablePattern(locator["ratingKey"], favoritesGlobalRatingPattern) {
			return "", false
		}
	case provider == "local":
		if !favoritesTransferFields(locator, "kind", "rootAlias", "relativePath") || !inspectEnum(locator["kind"], "local-media") {
			return "", false
		}
		if _, ok = favoritesGlobalPortableText(locator["rootAlias"], 256); !ok {
			return "", false
		}
		path, valid := favoritesGlobalPortableText(locator["relativePath"], 8192)
		if !valid || strings.HasPrefix(path, "/") || favoritesGlobalDrivePattern.MatchString(path) || strings.Contains(path, `\`) {
			return "", false
		}
		for _, segment := range strings.Split(path, "/") {
			if segment == "" || segment == "." || segment == ".." {
				return "", false
			}
		}
	case provider == "vportal" || (provider == "m3u" && inspectEnum(locator["kind"], "vportal-media")):
		// The receiver must separately prove that an M3U profile owns its embedded portal.
		if !favoritesTransferFields(locator, "kind", "idField", "id") || !inspectEnum(locator["kind"], "vportal-media") || !inspectEnum(locator["idField"], "id", "fid", "stream_id", "itemId", "media_id", "episode_id") || !favoritesGlobalPortablePattern(locator["id"], favoritesGlobalIdentifierPattern) {
			return "", false
		}
	default:
		if inspectEnum(locator["kind"], "channel-name") {
			if !favoritesTransferFields(locator, "kind", "name", "group") {
				return "", false
			}
			if _, ok = favoritesGlobalPortableText(locator["name"], 1024); !ok {
				return "", false
			}
			if !inspectNull(locator["group"]) {
				if _, ok = favoritesGlobalPortableText(locator["group"], 256); !ok {
					return "", false
				}
			}
		} else if !favoritesTransferFields(locator, "kind", "channelId") || !inspectEnum(locator["kind"], "channel") || !favoritesGlobalPortablePattern(locator["channelId"], favoritesGlobalIdentifierPattern) || favoritesGlobalReservedChannelPattern.MatchString(favoritesGlobalString(locator["channelId"])) {
			return "", false
		}
	}
	var values map[string]any
	if json.Unmarshal(raw, &values) != nil {
		return "", false
	}
	delete(values, "title")
	identity, err := json.Marshal(values)
	return string(identity), err == nil
}

type favoritesGlobalItemsRequest struct {
	Runtime, Op, Cursor, ID, ExpectedRevision, ExpectedContextRevision string
	MaxEntries                                                         int
}

func favoritesGlobalItemsCursor(raw json.RawMessage) (string, bool) {
	if inspectNull(raw) {
		return "", true
	}
	value, ok := inspectString(raw, 512)
	return value, ok && favoritesGlobalItemsCursorPattern.MatchString(value)
}

func parseFavoritesGlobalItemsRequest(raw json.RawMessage) *favoritesGlobalItemsRequest {
	if len(raw) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	runtime, valid := inspectString(m["runtime"], 64)
	if err != nil || !valid || !screenshotRuntimePattern.MatchString(runtime) || !inspectEnum(m["subject"], "global") {
		return nil
	}
	q := &favoritesGlobalItemsRequest{Runtime: runtime, Op: favoritesGlobalString(m["op"])}
	switch q.Op {
	case "list":
		if !favoritesTransferFields(m, "op", "runtime", "subject", "cursor", "max_entries") || !profileInteger(m["max_entries"], 1, maxFavoritesGlobalItemsPage) {
			return nil
		}
		q.Cursor, valid = favoritesGlobalItemsCursor(m["cursor"])
		if !valid {
			return nil
		}
		q.MaxEntries = favoritesGlobalInt(m["max_entries"])
	case "current":
		if !favoritesTransferFields(m, "op", "runtime", "subject") {
			return nil
		}
	case "play":
		if !favoritesTransferFields(m, "op", "runtime", "subject", "id", "expected_revision", "expected_context_revision") {
			return nil
		}
		if q.ID, valid = favoritesTransferUUID(m["id"]); !valid {
			return nil
		}
		if q.ExpectedRevision, valid = favoritesTransferHash(m["expected_revision"]); !valid {
			return nil
		}
		if q.ExpectedContextRevision, valid = favoritesTransferHash(m["expected_context_revision"]); !valid {
			return nil
		}
	default:
		return nil
	}
	return q
}

func validFavoritesGlobalItemsEnvelope(raw json.RawMessage) bool {
	if len(raw) > maxFavoritesTransferEnvelopeBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "action", "params", "expires_at")
	id, valid := inspectString(m["id"], 32)
	return ok && valid && commandID.MatchString(id) && inspectEnum(m["action"], "favorites_global_items") && numberValue(m["expires_at"], 1, inspectMaxInteger, false) && parseFavoritesGlobalItemsRequest(m["params"]) != nil
}

func validFavoritesGlobalItemsResultEnvelope(raw json.RawMessage, id string, q *favoritesGlobalItemsRequest) bool {
	if !commandID.MatchString(id) || len(raw) > maxFavoritesTransferResultBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "status", "data")
	return ok && inspectEnum(m["id"], id) && validFavoritesGlobalItemsResult(m["data"], favoritesGlobalString(m["status"]), q)
}

func validFavoritesGlobalItemsResult(raw json.RawMessage, status string, q *favoritesGlobalItemsRequest) bool {
	if q == nil || len(raw) > maxFavoritesGlobalItemsMetadata || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || !inspectEnum(m["runtime"], q.Runtime) || !inspectEnum(m["op"], q.Op) || !inspectEnum(m["subject"], "global") {
		return false
	}
	if _, exists := m["error"]; exists {
		return (status == "rejected" || status == "unsupported") && favoritesTransferFields(m, "version", "runtime", "op", "subject", "error") && inspectEnum(m["error"], "invalid_request", "envelope_too_large", "unsupported", "runtime_changed", "expired", "cancelled", "policy_denied", "busy", "storage_unavailable", "unsupported_persistence", "limits_exceeded", "invalid_cursor", "revision_conflict", "context_changed", "favorite_not_found", "source_unavailable", "ambiguous_source", "item_unavailable", "ambiguous_item", "unsupported_item", "playback_failed")
	}
	if status != "ok" {
		return false
	}
	for _, key := range []string{"revision", "context_revision"} {
		if _, valid := favoritesTransferHash(m[key]); !valid {
			return false
		}
	}
	switch q.Op {
	case "current":
		if !favoritesTransferFields(m, "version", "runtime", "op", "subject", "revision", "context_revision", "entry", "unavailable_reason") {
			return false
		}
		if inspectNull(m["entry"]) {
			return inspectEnum(m["unavailable_reason"], "no_portable_current_item", "standby", "unsupported_provider")
		}
		_, valid := favoritesGlobalPortableIdentity(m["entry"])
		return valid && inspectNull(m["unavailable_reason"])
	case "play":
		return favoritesTransferFields(m, "version", "runtime", "op", "subject", "revision", "context_revision", "id", "dispatched") && inspectEnum(m["id"], q.ID) && inspectEnum(m["revision"], q.ExpectedRevision) && inspectEnum(m["context_revision"], q.ExpectedContextRevision) && inspectBool(m["dispatched"], false) && favoritesGlobalBool(m["dispatched"])
	case "list":
		if !favoritesTransferFields(m, "version", "runtime", "op", "subject", "revision", "context_revision", "cursor", "page_offset", "total", "entries", "next_cursor", "unresolved_legacy_count") || !profileInteger(m["page_offset"], 0, maxFavoritesGlobalEntries) || !profileInteger(m["total"], 0, maxFavoritesGlobalEntries) || !profileInteger(m["unresolved_legacy_count"], 0, 100) {
			return false
		}
		cursor, valid := favoritesGlobalItemsCursor(m["cursor"])
		next, nextValid := favoritesGlobalItemsCursor(m["next_cursor"])
		offset, total := favoritesGlobalInt(m["page_offset"]), favoritesGlobalInt(m["total"])
		if !valid || !nextValid || cursor != q.Cursor || (cursor == "" && offset != 0) || (cursor != "" && offset == 0) {
			return false
		}
		entries, valid := inspectArray(m["entries"], q.MaxEntries)
		end := offset + len(entries)
		if !valid || end > total || (len(entries) == 0 && (offset != 0 || total != 0)) || ((end == total) != (next == "")) || (next != "" && next == cursor) {
			return false
		}
		ids, identities := map[string]bool{}, map[string]bool{}
		for _, rawEntry := range entries {
			row, valid := inspectObject(rawEntry, "id", "entry")
			if !valid {
				return false
			}
			id, valid := favoritesTransferUUID(row["id"])
			identity, portable := favoritesGlobalPortableIdentity(row["entry"])
			if !valid || !portable || ids[id] || identities[identity] {
				return false
			}
			ids[id], identities[identity] = true, true
		}
		return true
	}
	return false
}
