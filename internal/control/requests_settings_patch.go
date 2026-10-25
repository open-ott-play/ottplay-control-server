package control

import "encoding/json"

var settingsPatchGroups = map[string]map[string]int{
	"command_server": {"commandServerAddress": 4096, "commandServerToken": 256, "commandServerEnabled": 0},
	"local_http":     {"localCmdUrl": 4096, "localHttpDeviceCode": 256, "localHttpEnabled": 0},
}

type settingsPatchRequest struct {
	Runtime, Op, Group, Context string
	Values                      map[string]json.RawMessage
}

func parseSettingsPatchRequest(m map[string]json.RawMessage) *settingsPatchRequest {
	v := &settingsPatchRequest{Runtime: settingString(m["runtime"]), Op: settingString(m["op"]), Group: settingString(m["group"])}
	fields := settingsPatchGroups[v.Group]
	if !settingToken.MatchString(v.Runtime) || fields == nil {
		return nil
	}
	keys := []string{"op", "runtime", "group"}
	if v.Op == "get" {
		if !settingKeys(m, keys, nil) {
			return nil
		}
		return v
	}
	if v.Op != "set" || !settingKeys(m, append(keys, "context", "revision", "values"), nil) || !settingPattern(m["context"], settingToken) || !settingPattern(m["revision"], settingToken) {
		return nil
	}
	values, err := decodeObject(m["values"])
	if err != nil || len(values) == 0 || len(values) > 3 {
		return nil
	}
	for id, value := range values {
		limit, exists := fields[id]
		if !exists || (limit == 0 && !profileInteger(value, 0, 1)) || (limit > 0 && !profileText(value, limit)) {
			return nil
		}
	}
	v.Context, v.Values = settingString(m["context"]), values
	return v
}

func validSettingsPatchTransition(raw json.RawMessage) bool {
	v, err := decodeObject(raw)
	if err != nil || !settingKeys(v, []string{"state"}, []string{"reason"}) {
		return false
	}
	switch settingString(v["state"]) {
	case "idle", "pending", "applied":
		return v["reason"] == nil
	case "failed", "rolled_back":
		return settingEnum(v["reason"], "handshake_timeout", "authorization_failed", "network_error", "storage_failed", "ack_timeout", "effect_failed") || (settingString(v["state"]) == "failed" && settingString(v["reason"]) == "superseded")
	}
	return false
}

func validSettingsPatchResult(raw json.RawMessage, status string, expected *settingsPatchRequest) bool {
	if expected == nil || len(raw) > maxPlayerSettingsBytes {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || settingString(m["runtime"]) != expected.Runtime || settingString(m["op"]) != expected.Op || settingString(m["group"]) != expected.Group {
		return false
	}
	keys := []string{"version", "op", "runtime", "group"}
	if status != "ok" {
		return settingKeys(m, append(keys, "error", "reason"), nil) && profileText(m["error"], 256) && settingEnum(m["reason"], "invalid_request", "invalid_value", "runtime_changed", "source_changed", "revision_conflict", "policy_restricted", "unsupported", "storage_failed", "effect_failed")
	}
	keys = append(keys, "context", "revision", "settings", "transition")
	if expected.Op == "set" {
		keys = append(keys, "persisted", "changed", "application")
	}
	if !settingKeys(m, keys, nil) || !validSettingContext(m["context"], expected.Context) || !settingPattern(m["revision"], settingToken) || !validSettingsPatchTransition(m["transition"]) {
		return false
	}
	var snapshots []json.RawMessage
	if len(m["settings"]) == 0 || m["settings"][0] != '[' || json.Unmarshal(m["settings"], &snapshots) != nil || len(snapshots) != 3 {
		return false
	}
	seen := map[string]bool{}
	for _, raw := range snapshots {
		row, e := decodeObject(raw)
		id := settingString(row["id"])
		limit, exists := settingsPatchGroups[expected.Group][id]
		if e != nil || !exists || seen[id] || !settingEnum(row["storage"], "default", "stored", "invalid") || (expected.Op == "set" && settingString(row["storage"]) != "stored") {
			return false
		}
		seen[id] = true
		if limit > 0 {
			if !settingKeys(row, []string{"id", "storage", "redacted", "configured"}, nil) || string(row["redacted"]) != "true" || !settingBool(row["configured"]) {
				return false
			}
		} else {
			if !settingKeys(row, []string{"id", "storage", "value"}, []string{"persisted_value"}) || !profileInteger(row["value"], 0, 1) {
				return false
			}
			if settingString(row["storage"]) == "stored" {
				if !profileInteger(row["persisted_value"], 0, 1) {
					return false
				}
			} else if row["persisted_value"] != nil {
				return false
			}
			if expected.Op == "set" {
				var value, persisted, requested int
				_ = json.Unmarshal(row["value"], &value)
				_ = json.Unmarshal(row["persisted_value"], &persisted)
				if value != persisted {
					return false
				}
				if requestedRaw, ok := expected.Values[id]; ok {
					_ = json.Unmarshal(requestedRaw, &requested)
					if requested != persisted {
						return false
					}
				}
			}
		}
	}
	if expected.Op == "get" {
		return true
	}
	if string(m["persisted"]) != "true" || !settingBool(m["changed"]) {
		return false
	}
	a, e := decodeObject(m["application"])
	t, _ := decodeObject(m["transition"])
	if e != nil {
		return false
	}
	if string(m["changed"]) == "false" && settingString(a["state"]) == "completed" {
		return settingKeys(a, []string{"state"}, nil) && settingString(t["state"]) == "applied"
	}
	return settingKeys(a, []string{"state", "reason"}, nil) && settingString(a["state"]) == "deferred" && settingString(a["reason"]) == "after_ack" && settingString(t["state"]) == "pending"
}
