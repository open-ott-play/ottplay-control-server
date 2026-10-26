package control

import (
	"encoding/json"
	"regexp"
	"unicode/utf16"
)

var plexFolderRoot = regexp.MustCompile(`^/library/(?:sections/[1-9][0-9]{0,19}/(?:all(?:\?type=(?:[134]|10))?|folder(?:\?parent=[0-9]{1,20})?)|metadata/[1-9][0-9]{0,19}/children)$`)
var plexFolderReasons = []string{"invalid_request", "invalid_value", "runtime_changed", "source_changed", "revision_conflict", "policy_restricted", "unsupported", "storage_failed", "capacity_exceeded"}

type plexFoldersRequest struct {
	Runtime, Op, Context, Cursor string
	Limit                        int
}

func validPlexFolderTitle(raw json.RawMessage) bool {
	return profileText(raw, 4096) && len(utf16.Encode([]rune(settingString(raw)))) <= 1024
}

func parsePlexFoldersRequest(m map[string]json.RawMessage) *plexFoldersRequest {
	v := &plexFoldersRequest{Runtime: settingString(m["runtime"]), Op: settingString(m["op"]), Limit: 10}
	if !settingToken.MatchString(v.Runtime) {
		return nil
	}
	if v.Op == "list" {
		if !settingKeys(m, []string{"op", "runtime"}, []string{"limit", "cursor"}) {
			return nil
		}
		if raw, ok := m["limit"]; ok {
			if !profileInteger(raw, 1, 10) {
				return nil
			}
			_ = json.Unmarshal(raw, &v.Limit)
		}
		if raw, ok := m["cursor"]; ok {
			if !settingPattern(raw, settingToken) {
				return nil
			}
			v.Cursor = settingString(raw)
		}
		return v
	}
	keys := []string{"op", "runtime", "context", "revision"}
	switch v.Op {
	case "add":
		keys = append(keys, "path", "title")
		if !validPlexFolderTitle(m["title"]) {
			return nil
		}
	case "remove":
		keys = append(keys, "path")
	case "clear":
	default:
		return nil
	}
	if !settingKeys(m, keys, nil) || !settingPattern(m["context"], settingToken) || !settingPattern(m["revision"], settingToken) ||
		(v.Op != "clear" && !settingPattern(m["path"], plexFolderRoot)) {
		return nil
	}
	v.Context = settingString(m["context"])
	return v
}

func validPlexFoldersResult(raw json.RawMessage, status string, expected *plexFoldersRequest) bool {
	if expected == nil || len(raw) > 65536 {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || settingString(m["runtime"]) != expected.Runtime || settingString(m["op"]) != expected.Op {
		return false
	}
	if status != "ok" {
		return settingKeys(m, []string{"version", "op", "runtime", "error", "reason"}, nil) && profileText(m["error"], 256) &&
			settingEnum(m["reason"], plexFolderReasons...) && (settingString(m["reason"]) != "capacity_exceeded" || expected.Op == "add")
	}
	if !validSettingContext(m["context"], expected.Context) || !settingPattern(m["revision"], settingToken) || !profileInteger(m["total"], 0, 1000) {
		return false
	}
	context, _ := decodeObject(m["context"])
	if settingString(context["provider"]) != "plex" {
		return false
	}
	base := []string{"version", "op", "runtime", "context", "revision", "total"}
	if expected.Op == "list" {
		if !settingKeys(m, append(base, "items", "max_items", "next_cursor"), nil) || !profileInteger(m["max_items"], 1000, 1000) ||
			(!settingNull(m["next_cursor"]) && !settingPattern(m["next_cursor"], settingToken)) {
			return false
		}
		var items []json.RawMessage
		var total int
		_ = json.Unmarshal(m["total"], &total)
		if len(m["items"]) == 0 || m["items"][0] != '[' || json.Unmarshal(m["items"], &items) != nil || len(items) > expected.Limit || len(items) > total || (len(items) == 0 && total > 0) {
			return false
		}
		if !settingNull(m["next_cursor"]) && (len(items) == 0 || len(items) == total || settingString(m["next_cursor"]) == expected.Cursor) {
			return false
		}
		if expected.Cursor == "" && settingNull(m["next_cursor"]) && len(items) != total {
			return false
		}
		seen := map[string]bool{}
		for _, raw := range items {
			item, e := decodeObject(raw)
			if e != nil || !settingKeys(item, []string{"path", "title"}, nil) || !settingPattern(item["path"], plexFolderRoot) || !validPlexFolderTitle(item["title"]) {
				return false
			}
			path := settingString(item["path"])
			if seen[path] {
				return false
			}
			seen[path] = true
		}
		return true
	}
	if !settingKeys(m, append(base, "changed", "persisted", "application"), nil) || !settingBool(m["changed"]) || string(m["persisted"]) != "true" ||
		(expected.Op == "add" && !profileInteger(m["total"], 1, 1000)) ||
		(expected.Op == "clear" && !profileInteger(m["total"], 0, 0)) {
		return false
	}
	a, e := decodeObject(m["application"])
	return e == nil && settingKeys(a, []string{"state", "reason"}, nil) &&
		((settingString(a["state"]) == "deferred" && settingString(a["reason"]) == "next_catalogue") ||
			(settingString(a["state"]) == "failed" && settingString(a["reason"]) == "effect_failed"))
}
