package control

import "encoding/json"

type vportalSettingsRequest struct {
	Runtime, Context string
	Number           int
	Write            bool
	Name             bool
}

func parseVPortalSettingsRequest(action string, m map[string]json.RawMessage) *vportalSettingsRequest {
	v := &vportalSettingsRequest{Write: action == "provider_settings", Runtime: settingString(m["runtime"])}
	if settingString(m["provider"]) != "vportal" || !settingToken.MatchString(v.Runtime) {
		return nil
	}
	s, err := decodeObject(m["settings"])
	if err != nil {
		return nil
	}
	if v.Write {
		if !settingKeys(m, []string{"provider", "settings", "runtime", "context", "revision"}, nil) ||
			!settingPattern(m["context"], settingToken) || !settingPattern(m["revision"], settingToken) ||
			!settingKeys(s, []string{"profile", "vportal"}, []string{"name"}) || !profileInteger(s["profile"], 1, 15) {
			return nil
		}
		_ = json.Unmarshal(s["profile"], &v.Number)
		v.Context = settingString(m["context"])
	} else {
		if action != "settings_compare" || !settingKeys(m, []string{"provider", "number", "settings", "runtime"}, nil) ||
			!profileInteger(m["number"], 1, 15) || !settingKeys(s, []string{"vportal"}, []string{"name"}) {
			return nil
		}
		_ = json.Unmarshal(m["number"], &v.Number)
	}
	if !profileText(s["vportal"], 8192) {
		return nil
	}
	if name, exists := s["name"]; exists {
		if !profileText(name, 256) {
			return nil
		}
		v.Name = true
	}
	return v
}

func validVPortalSettingsResult(raw json.RawMessage, status string, expected *vportalSettingsRequest) bool {
	if expected == nil || len(raw) > 2048 {
		return false
	}
	m, err := decodeObject(raw)
	numberKey := "number"
	if expected.Write {
		numberKey = "profile"
	}
	if err != nil || settingString(m["provider"]) != "vportal" || !profileInteger(m[numberKey], expected.Number, expected.Number) ||
		settingString(m["runtime"]) != expected.Runtime {
		return false
	}
	if status != "ok" {
		return settingKeys(m, []string{"provider", numberKey, "runtime", "error", "reason"}, nil) &&
			profileText(m["error"], 256) && settingEnum(m["reason"], settingReasons...)
	}
	if !settingPattern(m["context"], settingToken) || !settingPattern(m["revision"], settingToken) ||
		(expected.Context != "" && settingString(m["context"]) != expected.Context) {
		return false
	}
	if !expected.Write {
		return settingKeys(m, []string{"provider", "number", "runtime", "context", "revision", "matched"}, nil) && settingBool(m["matched"])
	}
	if !settingKeys(m, []string{"provider", "profile", "runtime", "context", "revision", "fields", "saved", "reloaded", "reload_required"}, nil) ||
		string(m["saved"]) != "true" || !settingBool(m["reloaded"]) || !settingBool(m["reload_required"]) ||
		(string(m["reloaded"]) == "true" && string(m["reload_required"]) == "true") {
		return false
	}
	var fields []string
	if json.Unmarshal(m["fields"], &fields) != nil {
		return false
	}
	want := map[string]bool{"profile": true, "vportal": true}
	if expected.Name {
		want["name"] = true
	}
	if len(fields) != len(want) {
		return false
	}
	for _, field := range fields {
		if !want[field] {
			return false
		}
		delete(want, field)
	}
	return len(want) == 0
}
