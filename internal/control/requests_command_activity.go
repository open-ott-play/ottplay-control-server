package control

import "encoding/json"

const maxCommandActivityBytes = 8192

type commandActivityRequest struct{ Runtime string }

func validCommandActivityCapability(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "version")
	return ok && inspectNumber(m["version"], 2, 2, true, false)
}
func parseCommandActivityRequest(m map[string]json.RawMessage) *commandActivityRequest {
	runtime, ok := inspectToken(m["runtime"], 96)
	if !ok || !settingKeys(m, []string{"version", "runtime"}, nil) || !inspectNumber(m["version"], 2, 2, true, false) {
		return nil
	}
	return &commandActivityRequest{Runtime: runtime}
}
func validCommandActivityResult(raw json.RawMessage, status string, expected *commandActivityRequest) bool {
	if expected == nil || len(raw) > maxCommandActivityBytes {
		return false
	}
	if status != "ok" {
		m, ok := inspectObject(raw, "version", "runtime", "error")
		if !ok || status != "rejected" || !inspectNumber(m["version"], 2, 2, true, false) || !inspectEnum(m["error"], "invalid_request", "runtime_mismatch", "unavailable", "policy_denied") {
			return false
		}
		if inspectEnum(m["error"], "runtime_mismatch") {
			_, valid := inspectToken(m["runtime"], 96)
			return valid
		}
		return inspectEnum(m["runtime"], expected.Runtime)
	}
	m, ok := inspectObject(raw, "version", "runtime", "scope", "entries")
	if !ok || !inspectNumber(m["version"], 2, 2, true, false) || !inspectEnum(m["runtime"], expected.Runtime) || !inspectEnum(m["scope"], "remote_ingress") {
		return false
	}
	rows, ok := inspectArray(m["entries"], 8)
	if !ok {
		return false
	}
	for _, rawRow := range rows {
		row, ok := inspectObject(rawRow, "sequence", "action", "age_ms", "result", "request_id")
		if !ok || !inspectNumber(row["sequence"], 1, inspectMaxInteger, true, false) || !inspectEnum(row["action"], "play", "play_catalog", "playback", "restart", "provider", "profile", "input", "command", "local_media", "plex_queue", "plex_library", "lifecycle", "kiosk") || !inspectNumber(row["age_ms"], 0, inspectMaxInteger, true, false) || !inspectEnum(row["result"], "pending", "ok", "rejected", "unsupported", "failed") {
			return false
		}
		if !inspectNull(row["request_id"]) {
			id, valid := inspectString(row["request_id"], 32)
			if !valid || !commandID.MatchString(id) {
				return false
			}
		}
	}
	return true
}
