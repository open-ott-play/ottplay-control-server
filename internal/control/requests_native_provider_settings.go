package control

import (
	"encoding/json"
	"regexp"
	"sort"
	"strings"
	"unicode"
)

const maxNativeProviderSettingsResultBytes = 2048

// These are canonical native registry IDs, not provider families or aliases.
// The versioned route never widens the established unversioned settings API.
var nativeSettingsProviders = strings.Fields("edem antifriz kb-team itv ottclub shura 1ott bestlist/stalker only4 shara-tv tvteam all4you bestlist cbilling d/maxtv diamondtv dosug dragon drvao fabryka fox fxml great ipstream iptv-ott.ru korona moidom newlook ottg ottprime polmedia prost raduga rd russkoetv shara.club sharavoz shocktv tabox top topiptv tvclub ultifl1x vidok")
var nativeSettingsKey = regexp.MustCompile(`^[A-Za-z0-9_-]{1,512}$`)
var nativeSettingsReasons = []string{"invalid_request", "unsupported", "policy_restricted", "runtime_changed", "source_changed", "revision_conflict", "invalid_value", "storage_failed", "busy", "expired"}

// Retained bindings contain identity and field names only. Credential values
// exist solely in the authenticated, expiring request body, never this metadata.
type nativeProviderSettingsRequest struct {
	Runtime, Provider, Context, Revision string
	Number                               int
	Write                                bool
	Fields                               []string
}

func validNativeProviderSettingsCapability(raw json.RawMessage) bool {
	m, err := decodeObject(raw)
	if err != nil || !settingKeys(m, []string{"version", "operations", "providers"}, nil) || !profileInteger(m["version"], 1, 1) {
		return false
	}
	var operations, providers []string
	if json.Unmarshal(m["operations"], &operations) != nil || len(operations) != 2 ||
		!((operations[0] == "compare" && operations[1] == "set") || (operations[0] == "set" && operations[1] == "compare")) ||
		json.Unmarshal(m["providers"], &providers) != nil || len(providers) < 1 || len(providers) > len(nativeSettingsProviders) {
		return false
	}
	seen := map[string]bool{}
	for _, provider := range providers {
		if seen[provider] || !validNativeSettingsProvider(provider) {
			return false
		}
		seen[provider] = true
	}
	return true
}

func validNativeSettingsProvider(provider string) bool {
	for _, candidate := range nativeSettingsProviders {
		if provider == candidate {
			return true
		}
	}
	return false
}

func nativeSettingsText(raw json.RawMessage, maximum int) bool {
	if !profileText(raw, maximum) {
		return false
	}
	for _, r := range settingString(raw) {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}

func parseNativeProviderSettingsRequest(action string, m map[string]json.RawMessage) *nativeProviderSettingsRequest {
	v := &nativeProviderSettingsRequest{Runtime: settingString(m["runtime"]), Provider: settingString(m["provider"]), Write: action == "provider_settings"}
	keys := []string{"version", "runtime", "provider", "number", "settings"}
	if v.Write {
		keys = append(keys, "context", "revision")
	} else if action != "settings_compare" {
		return nil
	}
	if !settingKeys(m, keys, nil) || !profileInteger(m["version"], 1, 1) || !settingToken.MatchString(v.Runtime) ||
		!validNativeSettingsProvider(v.Provider) || !profileInteger(m["number"], 1, 15) {
		return nil
	}
	_ = json.Unmarshal(m["number"], &v.Number)
	if v.Write {
		if !settingPattern(m["context"], settingToken) || !settingPattern(m["revision"], settingToken) {
			return nil
		}
		v.Context, v.Revision = settingString(m["context"]), settingString(m["revision"])
	}
	settings, err := decodeObject(m["settings"])
	if err != nil || len(settings) == 0 {
		return nil
	}
	for field, value := range settings {
		valid := false
		if v.Provider == "edem" {
			switch field {
			case "key":
				valid = settingPattern(value, nativeSettingsKey)
			case "list":
				valid = profileInteger(value, 0, 3)
			case "portal":
				valid = nativeSettingsText(value, 8192)
			}
		} else {
			switch field {
			case "server", "username", "password", "playlist", "mac":
				valid = nativeSettingsText(value, 8192)
			case "list":
				valid = profileInteger(value, 0, 3)
			case "mode":
				valid = profileInteger(value, 0, 2)
			}
		}
		if !valid {
			return nil
		}
		v.Fields = append(v.Fields, field)
	}
	sort.Strings(v.Fields)
	return v
}

func validNativeProviderSettingsResult(raw json.RawMessage, status string, expected *nativeProviderSettingsRequest) bool {
	if expected == nil || len(raw) > maxNativeProviderSettingsResultBytes {
		return false
	}
	m, err := decodeObject(raw)
	base := []string{"version", "runtime", "provider", "number"}
	if err != nil || !profileInteger(m["version"], 1, 1) || settingString(m["runtime"]) != expected.Runtime ||
		settingString(m["provider"]) != expected.Provider || !profileInteger(m["number"], expected.Number, expected.Number) {
		return false
	}
	if status != "ok" {
		return (status == "rejected" || status == "unsupported") &&
			settingKeys(m, append(base, "reason"), nil) && settingEnum(m["reason"], nativeSettingsReasons...)
	}
	base = append(base, "context", "revision")
	if !settingPattern(m["context"], settingToken) || !settingPattern(m["revision"], settingToken) {
		return false
	}
	if !expected.Write {
		return settingKeys(m, append(base, "matched"), nil) && settingBool(m["matched"])
	}
	if !settingKeys(m, append(base, "saved", "fields", "reloaded", "reload_required"), nil) ||
		settingString(m["context"]) != expected.Context || settingString(m["revision"]) != expected.Revision ||
		string(m["saved"]) != "true" || !settingBool(m["reloaded"]) || !settingBool(m["reload_required"]) ||
		(string(m["reloaded"]) == "true" && string(m["reload_required"]) == "true") {
		return false
	}
	var fields []string
	if json.Unmarshal(m["fields"], &fields) != nil || len(fields) != len(expected.Fields) {
		return false
	}
	sort.Strings(fields)
	for i, field := range fields {
		if field != expected.Fields[i] {
			return false
		}
	}
	return true
}
