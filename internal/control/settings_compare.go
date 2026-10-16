package control

import "encoding/json"

// Comparison never selects a provider or exposes stored credentials.
func validSettingsCompare(p map[string]json.RawMessage) bool {
	var provider string
	if json.Unmarshal(p["provider"], &provider) != nil {
		return false
	}
	s, err := decodeObject(p["settings"])
	if err != nil {
		return false
	}
	switch provider {
	case "m3u":
		return len(p) == 3 && profileInteger(p["number"], 1, 15) && validProfileSettings(p["settings"])
	case "plex":
		return len(p) == 2 && len(s) == 2 && profileText(s["server"], 8192) && profileText(s["token"], 1024)
	case "stalker":
		return len(p) == 3 && profileInteger(p["number"], 1, 15) && len(s) == 3 && profileText(s["name"], 256) && profileText(s["server"], 8192) && profileText(s["mac"], 17)
	}
	return false
}
