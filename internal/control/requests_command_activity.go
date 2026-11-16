package control

import (
	"encoding/json"
	"strings"
	"time"
)

const maxCommandActivityBytes = 8192

// Owner and device are trusted admission metadata, never client parameters.
type commandActivityRequest struct{ Runtime, Owner, Device string }

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

// Called under s.mu, after validating the complete unfiltered response. A
// scoped observer receives only entries joinable to its own retained ingress
// metadata and current original action grants. Missing evidence stays omitted;
// the result is a subset, never a claim of complete device history.
func (s *Server) filterCommandActivityResponse(raw []byte, expected *commandActivityRequest, now time.Time) ([]byte, bool) {
	if expected == nil {
		return nil, false
	}
	if expected.Owner == "admin" {
		return raw, true
	}
	if !strings.HasPrefix(expected.Owner, "operator:") || expected.Device == "" {
		return nil, false
	}
	outer, err := decodeObject(raw)
	if err != nil {
		return nil, false
	}
	if !inspectEnum(outer["status"], "ok") {
		return raw, true
	}
	data, ok := inspectObject(outer["data"], "version", "runtime", "scope", "entries")
	if !ok {
		return nil, false
	}
	rows, ok := inspectArray(data["entries"], 8)
	if !ok {
		return nil, false
	}
	var observer *commandOperator
	for _, candidate := range s.operators {
		if "operator:"+candidate.id == expected.Owner {
			observer = candidate
			break
		}
	}
	allowed := map[string]string{}
	if observer != nil && observer.devices[expected.Device] {
		for _, audit := range s.requestAudit {
			if audit.device != expected.Device || audit.owner != expected.Owner || now.Sub(audit.accepted) > requestAuditAgeMS*time.Millisecond || !observer.actions[audit.action] {
				continue
			}
			family, _, _ := strings.Cut(audit.action, ".")
			allowed[audit.id] = family
		}
	}
	kept := make([]json.RawMessage, 0, len(rows))
	for _, rawRow := range rows {
		row, err := decodeObject(rawRow)
		if err != nil {
			return nil, false
		}
		if inspectNull(row["request_id"]) {
			continue
		}
		id, valid := inspectString(row["request_id"], 32)
		family, found := allowed[id]
		if valid && commandID.MatchString(id) && found && inspectEnum(row["action"], family) {
			kept = append(kept, rawRow)
		}
	}
	data["entries"], err = json.Marshal(kept)
	if err != nil {
		return nil, false
	}
	outer["data"], err = json.Marshal(data)
	if err != nil {
		return nil, false
	}
	filtered, err := json.Marshal(outer)
	return filtered, err == nil && len(filtered) <= maxCommandActivityBytes
}
