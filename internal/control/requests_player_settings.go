package control

import (
	"encoding/json"
	"math"
	"reflect"
	"regexp"
	"strings"
)

const maxPlayerSettingsBytes = 64 * 1024

var settingID = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]{0,63}$`)
var settingAlias = regexp.MustCompile(`^[A-Za-z0-9_.+\-]{1,64}$`)
var settingToken = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,128}$`)
var settingProvider = regexp.MustCompile(`^[a-z0-9][a-z0-9_./-]{0,31}$`)
var settingReasons = []string{"invalid_request", "unknown_setting", "atomic_patch_required", "invalid_value", "runtime_changed", "source_changed", "revision_conflict", "policy_restricted", "local_only", "unsupported", "storage_failed", "effect_failed"}

type playerSettingsRequest struct {
	Runtime, Op, ID, Context string
	Limit                    int
	Value                    json.RawMessage
}

func settingKeys(m map[string]json.RawMessage, required, optional []string) bool {
	if len(m) < len(required) || len(m) > len(required)+len(optional) {
		return false
	}
	allowed := map[string]bool{}
	for _, key := range required {
		if m[key] == nil {
			return false
		}
		allowed[key] = true
	}
	for _, key := range optional {
		allowed[key] = true
	}
	for key := range m {
		if !allowed[key] {
			return false
		}
	}
	return true
}

func settingString(raw json.RawMessage) string {
	var value string
	_ = json.Unmarshal(raw, &value)
	return value
}
func settingPattern(raw json.RawMessage, pattern *regexp.Regexp) bool {
	return pattern.MatchString(settingString(raw))
}
func settingEnum(raw json.RawMessage, values ...string) bool {
	value := settingString(raw)
	for _, candidate := range values {
		if value == candidate {
			return true
		}
	}
	return false
}
func settingBool(raw json.RawMessage) bool { return string(raw) == "true" || string(raw) == "false" }
func settingNull(raw json.RawMessage) bool { return string(raw) == "null" }

// This API accepts bounded scalar preferences and string arrays, never objects.
func settingValue(raw json.RawMessage, kind string) bool {
	if len(raw) == 0 {
		return false
	}
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return false
	}
	switch v := value.(type) {
	case float64:
		return (kind == "" || kind == "integer") && math.Abs(v) <= 9007199254740991 && math.Trunc(v) == v
	case string:
		return (kind == "" || kind == "string") && profileText(raw, 4096)
	case []any:
		if (kind != "" && kind != "string_array") || len(v) > 64 {
			return false
		}
		var rows []json.RawMessage
		_ = json.Unmarshal(raw, &rows)
		for _, row := range rows {
			if !profileText(row, 4096) {
				return false
			}
		}
		return true
	}
	return false
}

func parsePlayerSettingsRequest(m map[string]json.RawMessage) *playerSettingsRequest {
	v := &playerSettingsRequest{Runtime: settingString(m["runtime"]), Op: settingString(m["op"]), Limit: 20}
	if !settingToken.MatchString(v.Runtime) {
		return nil
	}
	switch v.Op {
	case "schema":
		if !settingKeys(m, []string{"runtime", "op"}, []string{"limit", "cursor"}) {
			return nil
		}
		if raw, ok := m["limit"]; ok {
			if !profileInteger(raw, 1, 20) {
				return nil
			}
			_ = json.Unmarshal(raw, &v.Limit)
		}
		if raw, ok := m["cursor"]; ok && !settingPattern(raw, settingToken) {
			return nil
		}
	case "get", "set":
		keys := []string{"runtime", "op", "id"}
		if v.Op == "set" {
			keys = append(keys, "context", "revision", "value")
		}
		if !settingKeys(m, keys, nil) || !settingPattern(m["id"], settingID) {
			return nil
		}
		v.ID = settingString(m["id"])
		if v.Op == "set" {
			if !settingPattern(m["context"], settingToken) || !settingPattern(m["revision"], settingToken) || !settingValue(m["value"], "") {
				return nil
			}
			v.Context, v.Value = settingString(m["context"]), append(json.RawMessage(nil), m["value"]...)
		}
	default:
		return nil
	}
	return v
}

func validSettingContext(raw json.RawMessage, expected string) bool {
	m, err := decodeObject(raw)
	return err == nil && settingKeys(m, []string{"token", "provider", "profile"}, nil) &&
		settingPattern(m["token"], settingToken) && (expected == "" || settingString(m["token"]) == expected) &&
		(settingNull(m["provider"]) || validSettingProvider(m["provider"])) &&
		(settingNull(m["profile"]) || profileInteger(m["profile"], 1, 15))
}

func validSettingProvider(raw json.RawMessage) bool {
	value := settingString(raw)
	return settingProvider.MatchString(value) && !strings.Contains(value, "..") &&
		!strings.Contains(value, "//") && !strings.HasSuffix(value, "/")
}

func validSettingDescriptor(raw json.RawMessage) bool {
	m, err := decodeObject(raw)
	if err != nil || !settingKeys(m, []string{"id", "type", "scope", "sensitive", "access", "constraints", "apply"}, []string{"default_value", "choices", "description", "unit"}) ||
		!settingPattern(m["id"], settingID) || !settingEnum(m["type"], "integer", "string", "string_array") ||
		!settingEnum(m["scope"], "application", "provider") || !settingBool(m["sensitive"]) || !settingEnum(m["apply"], "immediate", "next_stream", "manual", "after_ack") {
		return false
	}
	kind := settingString(m["type"])
	access, e := decodeObject(m["access"])
	if e != nil || !settingKeys(access, []string{"read", "write"}, []string{"reason"}) || !settingBool(access["read"]) || !settingBool(access["write"]) {
		return false
	}
	if reason, ok := access["reason"]; ok && !settingEnum(reason, append(settingReasons, "device_forced_auto", "lifecycle_required", "local_action_required")...) {
		return false
	}
	constraints, e := decodeObject(m["constraints"])
	if e != nil || !settingKeys(constraints, nil, []string{"minimum", "maximum", "values", "max_utf8_bytes", "max_items"}) {
		return false
	}
	for _, key := range []string{"minimum", "maximum"} {
		if value, ok := constraints[key]; ok && (kind != "integer" || !settingValue(value, "integer")) {
			return false
		}
	}
	if value, ok := constraints["max_utf8_bytes"]; ok && !profileInteger(value, 0, 4096) {
		return false
	}
	if constraints["minimum"] != nil && constraints["maximum"] != nil {
		var minimum, maximum float64
		_ = json.Unmarshal(constraints["minimum"], &minimum)
		_ = json.Unmarshal(constraints["maximum"], &maximum)
		if minimum > maximum {
			return false
		}
	}
	if value, ok := constraints["max_items"]; ok && !profileInteger(value, 0, 64) {
		return false
	}
	if value, ok := constraints["values"]; ok {
		var values []json.RawMessage
		if len(value) == 0 || value[0] != '[' || json.Unmarshal(value, &values) != nil || len(values) > 256 {
			return false
		}
		for _, v := range values {
			if !settingValue(v, kind) || (len(v) > 0 && v[0] == '[') {
				return false
			}
		}
	}
	if value, ok := m["default_value"]; ok && (string(m["sensitive"]) == "true" || !settingValue(value, kind)) {
		return false
	}
	if value, ok := m["description"]; ok && !profileText(value, 512) {
		return false
	}
	if value, ok := m["unit"]; ok && !settingEnum(value, "seconds", "percent") {
		return false
	}
	if value, ok := m["choices"]; ok {
		seenAliases := map[string]bool{}
		var choices []json.RawMessage
		if len(value) == 0 || value[0] != '[' || json.Unmarshal(value, &choices) != nil || len(choices) > 256 {
			return false
		}
		for _, row := range choices {
			choice, e := decodeObject(row)
			if e != nil || !settingKeys(choice, []string{"value", "label", "aliases"}, nil) || !settingValue(choice["value"], kind) || !profileText(choice["label"], 160) {
				return false
			}
			var aliases []json.RawMessage
			if len(choice["aliases"]) == 0 || choice["aliases"][0] != '[' || json.Unmarshal(choice["aliases"], &aliases) != nil || len(aliases) > 16 {
				return false
			}
			for _, alias := range aliases {
				folded := strings.ToLower(settingString(alias))
				if !settingPattern(alias, settingAlias) || seenAliases[folded] {
					return false
				}
				seenAliases[folded] = true
			}
		}
	}
	return true
}

func validSettingSnapshot(raw json.RawMessage, id string, descriptor json.RawMessage, value json.RawMessage) bool {
	m, err := decodeObject(raw)
	if err != nil || !settingKeys(m, []string{"id", "revision", "storage"}, []string{"value", "persisted_value", "effective_value", "redacted", "configured"}) ||
		!settingPattern(m["id"], settingID) || settingString(m["id"]) != id || !settingPattern(m["revision"], settingToken) ||
		!settingEnum(m["storage"], "stored", "default", "invalid", "unavailable") {
		return false
	}
	kind, sensitive, readable := "", false, false
	if descriptor != nil {
		d, _ := decodeObject(descriptor)
		kind = settingString(d["type"])
		sensitive = string(d["sensitive"]) == "true"
		a, _ := decodeObject(d["access"])
		readable = string(a["read"]) == "true" && !sensitive
	}
	if v, ok := m["redacted"]; ok && string(v) != "true" {
		return false
	}
	redacted := sensitive || string(m["redacted"]) == "true"
	if descriptor != nil && !sensitive && redacted {
		return false
	}
	stored := settingString(m["storage"]) == "stored"
	if !stored && m["persisted_value"] != nil {
		return false
	}
	if readable && settingString(m["storage"]) != "unavailable" && m["value"] == nil {
		return false
	}
	if readable && stored && m["persisted_value"] == nil {
		return false
	}
	if value != nil && !stored {
		return false
	}
	if v, ok := m["configured"]; ok && !settingBool(v) {
		return false
	}
	for _, key := range []string{"value", "persisted_value", "effective_value"} {
		if v, ok := m[key]; ok && (redacted || !settingValue(v, kind)) {
			return false
		}
	}
	if sensitive && string(m["redacted"]) != "true" {
		return false
	}
	if value != nil && !redacted {
		var expected, actual any
		if json.Unmarshal(value, &expected) != nil || json.Unmarshal(m["persisted_value"], &actual) != nil || !reflect.DeepEqual(expected, actual) {
			return false
		}
	}
	return true
}

func validPlayerSettingsResult(raw json.RawMessage, status string, expected *playerSettingsRequest) bool {
	if expected == nil || len(raw) > maxPlayerSettingsBytes {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || settingString(m["runtime"]) != expected.Runtime || settingString(m["op"]) != expected.Op {
		return false
	}
	if status != "ok" {
		return settingKeys(m, []string{"version", "op", "runtime", "error", "reason"}, nil) && profileText(m["error"], 256) && settingEnum(m["reason"], settingReasons...)
	}
	if !validSettingContext(m["context"], expected.Context) {
		return false
	}
	base := []string{"version", "op", "runtime", "context"}
	switch expected.Op {
	case "schema":
		if !settingKeys(m, append(base, "settings", "next_cursor"), nil) || (!settingNull(m["next_cursor"]) && !settingPattern(m["next_cursor"], settingToken)) {
			return false
		}
		var entries []json.RawMessage
		if len(m["settings"]) == 0 || m["settings"][0] != '[' || json.Unmarshal(m["settings"], &entries) != nil || len(entries) > expected.Limit || (len(entries) == 0 && !settingNull(m["next_cursor"])) {
			return false
		}
		seen := map[string]bool{}
		for _, entry := range entries {
			if !validSettingDescriptor(entry) {
				return false
			}
			row, _ := decodeObject(entry)
			id := settingString(row["id"])
			if seen[id] {
				return false
			}
			seen[id] = true
		}
		return true
	case "get":
		if !settingKeys(m, append(base, "descriptor", "setting"), nil) || !validSettingDescriptor(m["descriptor"]) {
			return false
		}
		d, _ := decodeObject(m["descriptor"])
		return settingString(d["id"]) == expected.ID && validSettingSnapshot(m["setting"], expected.ID, m["descriptor"], nil)
	case "set":
		if !settingKeys(m, append(base, "setting", "changed", "persisted", "application"), nil) || !settingBool(m["changed"]) || string(m["persisted"]) != "true" || !validSettingSnapshot(m["setting"], expected.ID, nil, expected.Value) {
			return false
		}
		a, e := decodeObject(m["application"])
		if e != nil || !settingKeys(a, []string{"state"}, []string{"reason"}) || !settingEnum(a["state"], "completed", "deferred", "failed") {
			return false
		}
		return (settingString(a["state"]) == "completed" && a["reason"] == nil) ||
			(settingString(a["state"]) == "deferred" && settingEnum(a["reason"], "next_stream", "manual", "after_ack")) ||
			(settingString(a["state"]) == "failed" && settingEnum(a["reason"], "effect_failed"))
	}
	return false
}
