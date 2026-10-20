package control

import (
	"bytes"
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
)

var offlineBindingID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

// Only portable descriptions cross the command boundary. Access grants,
// bookmarks, file handles and media bytes remain in device-local storage.
func validOfflineProfile(raw json.RawMessage) bool {
	m, err := decodeObject(raw)
	var compact bytes.Buffer
	if json.Compact(&compact, raw) != nil || compact.Len() > 12288 {
		return false
	}
	if err != nil || len(m) != 6 || !profileInteger(m["version"], 1, 1) || !offlineEnum(m["provider"], "offline") || !profileText(m["name"], 256) {
		return false
	}
	var binding string
	if json.Unmarshal(m["binding_id"], &binding) != nil || !offlineBindingID.MatchString(binding) {
		return false
	}
	var sources []json.RawMessage
	if len(m["sources"]) == 0 || m["sources"][0] != '[' || json.Unmarshal(m["sources"], &sources) != nil || len(sources) > 32 {
		return false
	}
	for index, rawSource := range sources {
		source, err := decodeObject(rawSource)
		if err != nil || len(source) != 3 || !offlineEnum(source["key"], "s"+strconv.Itoa(index)) || !offlineEnum(source["kind"], "folder", "file") || !offlineSourceName(source["name"], 512, false) {
			return false
		}
	}
	if string(m["selected"]) == "null" {
		return true
	}
	selected, err := decodeObject(m["selected"])
	if err != nil || len(selected) != 2 || !offlineSourceName(selected["name"], 1024, true) {
		return false
	}
	for index := range sources {
		if offlineEnum(selected["source_key"], "s"+strconv.Itoa(index)) {
			return true
		}
	}
	return false
}

func offlineEnum(raw json.RawMessage, choices ...string) bool {
	var value string
	if json.Unmarshal(raw, &value) != nil {
		return false
	}
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}

func offlineNonemptyText(raw json.RawMessage, maximum int) bool {
	var value string
	return profileText(raw, maximum) && json.Unmarshal(raw, &value) == nil && strings.TrimSpace(value) != ""
}

var offlineNameScheme = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9+.-]*:`)

func offlineSourceName(raw json.RawMessage, maximum int, relative bool) bool {
	var value string
	if !offlineNonemptyText(raw, maximum) || json.Unmarshal(raw, &value) != nil || strings.Contains(value, "\\") || offlineNameScheme.MatchString(value) {
		return false
	}
	if !relative {
		return !strings.Contains(value, "/") && value != "." && value != ".."
	}
	if strings.HasPrefix(value, "/") {
		return false
	}
	for _, part := range strings.Split(value, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
}

// The pending request binds successful profile replies to the requested slot.
type offlineProfileRequest struct {
	Action string
	Number int
}

func validOfflineProfileResult(raw json.RawMessage, status string, request *offlineProfileRequest) bool {
	m, err := decodeObject(raw)
	if err != nil || len(raw) > 16*1024 {
		return false
	}
	if status != "ok" {
		return len(m) == 1 && offlineNonemptyText(m["error"], 1024)
	}
	if !offlineEnum(m["provider"], "offline") {
		return false
	}
	if request.Action == "profile_export" {
		return len(m) == 3 && profileInteger(m["number"], request.Number, request.Number) && validOfflineProfile(m["profile"])
	}
	row, err := decodeObject(m["profile"])
	if err != nil || len(m) != 4 || string(m["saved"]) != "true" || string(row["configured"]) != "true" || len(row) != 7 || !profileInteger(row["number"], request.Number, request.Number) || !profileText(row["name"], 256) || !profileText(row["selected_name"], 1024) || !profileInteger(row["source_count"], 0, 32) {
		return false
	}
	for _, key := range []string{"active", "configured", "binding_required"} {
		if string(row[key]) != "true" && string(row[key]) != "false" {
			return false
		}
	}
	if string(m["binding_required"]) != string(row["binding_required"]) {
		return false
	}
	var count int
	var selected string
	_ = json.Unmarshal(row["source_count"], &count)
	_ = json.Unmarshal(row["selected_name"], &selected)
	return !(count == 0 && selected != "")
}
