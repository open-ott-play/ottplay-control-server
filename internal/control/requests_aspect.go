package control

import "encoding/json"

type aspectRequest struct {
	Runtime   string
	Operation string
	Mode      string
}

// The player checks the requested runtime and local access policy, and applies
// a set operation only after its acceptance response has been acknowledged.
// Fit preserves the whole picture; fill crops it to the display without stretch.
func parseAspectRequest(params map[string]json.RawMessage) *aspectRequest {
	v := &aspectRequest{}
	if json.Unmarshal(params["operation"], &v.Operation) != nil ||
		json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) {
		return nil
	}
	switch v.Operation {
	case "get":
		if len(params) == 2 {
			return v
		}
	case "set":
		if len(params) == 3 && json.Unmarshal(params["mode"], &v.Mode) == nil && (v.Mode == "fit" || v.Mode == "fill") {
			return v
		}
	}
	return nil
}

func validAspectResult(raw json.RawMessage, status string, expected *aspectRequest) bool {
	if expected == nil || (status != "ok" && status != "rejected" && status != "unsupported") {
		return false
	}
	fields, err := decodeObject(raw)
	keys := []string{"version", "runtime", "operation", "mode", "saved_mode", "persisted"}
	if expected.Operation == "set" {
		keys = []string{"version", "runtime", "operation", "mode", "accepted", "dispatched", "effect"}
	}
	if status != "ok" {
		keys = []string{"version", "runtime", "operation", "error"}
		if expected.Operation == "set" {
			keys = append(keys, "mode")
		}
	}
	if err != nil || len(fields) != len(keys) {
		return false
	}
	// Require exact keys before unmarshalling: struct decoding otherwise accepts
	// case-insensitive names, and null booleans must not become a false value.
	for _, key := range keys {
		if _, exists := fields[key]; !exists {
			return false
		}
	}
	var v struct {
		Version    int     `json:"version"`
		Runtime    string  `json:"runtime"`
		Operation  string  `json:"operation"`
		Mode       string  `json:"mode"`
		SavedMode  *string `json:"saved_mode"`
		Persisted  *bool   `json:"persisted"`
		Accepted   *bool   `json:"accepted"`
		Dispatched *bool   `json:"dispatched"`
		Effect     string  `json:"effect"`
		Error      string  `json:"error"`
	}
	if json.Unmarshal(raw, &v) != nil || v.Version != 1 || v.Runtime != expected.Runtime ||
		v.Operation != expected.Operation {
		return false
	}
	if status != "ok" {
		switch v.Error {
		case "invalid_request", "runtime_mismatch", "restricted", "unsupported", "unavailable":
			return expected.Operation == "get" || (expected.Operation == "set" && v.Mode == expected.Mode)
		}
		return false
	}
	if v.Mode != "fit" && v.Mode != "fill" {
		return false
	}
	switch expected.Operation {
	case "get":
		if v.Persisted == nil || (v.SavedMode != nil && *v.SavedMode != "fit" && *v.SavedMode != "fill") {
			return false
		}
		return *v.Persisted == (v.SavedMode != nil && *v.SavedMode == v.Mode)
	case "set":
		return v.Mode == expected.Mode && v.Accepted != nil && *v.Accepted &&
			v.Dispatched != nil && !*v.Dispatched && v.Effect == "aspect-after-ack"
	}
	return false
}
