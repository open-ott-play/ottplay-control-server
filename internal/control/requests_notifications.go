package control

import "encoding/json"

type notificationsRequest struct {
	Runtime   string
	Operation string
	Enabled   bool
}

func parseNotificationsRequest(params map[string]json.RawMessage) *notificationsRequest {
	v := &notificationsRequest{}
	if !profileInteger(params["version"], 1, 1) ||
		json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) ||
		json.Unmarshal(params["operation"], &v.Operation) != nil {
		return nil
	}
	switch v.Operation {
	case "get":
		if len(params) == 3 {
			return v
		}
	case "set":
		var enabled *bool
		if len(params) == 4 && json.Unmarshal(params["enabled"], &enabled) == nil && enabled != nil {
			v.Enabled = *enabled
			return v
		}
	}
	return nil
}

// A receipt confirms the durable preference in this exact player runtime.
// It does not claim that an earlier camera fetch elsewhere has been cancelled.
func validNotificationsResult(raw json.RawMessage, status string, expected *notificationsRequest) bool {
	if expected == nil || (status != "ok" && status != "rejected" && status != "unsupported") {
		return false
	}
	fields, err := decodeObject(raw)
	keys := []string{"version", "runtime", "operation", "enabled", "persisted"}
	if status != "ok" {
		keys = []string{"version", "runtime", "operation", "error"}
	}
	if err != nil || len(fields) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, exists := fields[key]; !exists {
			return false
		}
	}
	var v struct {
		Version   int    `json:"version"`
		Runtime   string `json:"runtime"`
		Operation string `json:"operation"`
		Enabled   *bool  `json:"enabled"`
		Persisted *bool  `json:"persisted"`
		Error     string `json:"error"`
	}
	if json.Unmarshal(raw, &v) != nil || v.Version != 1 || v.Runtime != expected.Runtime || v.Operation != expected.Operation {
		return false
	}
	if status != "ok" {
		switch v.Error {
		case "invalid_request", "runtime_mismatch", "storage_unavailable", "unsupported":
			return true
		}
		return false
	}
	return v.Enabled != nil && v.Persisted != nil && *v.Persisted &&
		(expected.Operation == "get" || *v.Enabled == expected.Enabled)
}
