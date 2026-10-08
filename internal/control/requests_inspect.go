package control

import (
	"bytes"
	"encoding/json"
	"regexp"
)

const maxInspectResultBytes = 32 * 1024
const maxDoctorSnapshotBytes = 8 * 1024
const inspectMaxInteger = 9007199254740991
const inspectMaxSeconds = 315576000

var inspectTokenPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)
var inspectVersionPattern = regexp.MustCompile(`^[A-Za-z0-9_.+-]{1,64}$`)
var inspectRevisionPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)

type inspectRequest struct {
	Runtime     string
	Section     string
	OperationID string
}

func inspectObject(raw json.RawMessage, keys ...string) (map[string]json.RawMessage, bool) {
	m, err := decodeObject(raw)
	if err != nil || len(m) != len(keys) {
		return nil, false
	}
	for _, key := range keys {
		if _, ok := m[key]; !ok {
			return nil, false
		}
	}
	return m, true
}

func inspectNull(raw json.RawMessage) bool { return bytes.Equal(bytes.TrimSpace(raw), []byte("null")) }

func inspectString(raw json.RawMessage, maximum int) (string, bool) {
	var value string
	if !profileText(bytes.TrimSpace(raw), maximum) || json.Unmarshal(raw, &value) != nil {
		return "", false
	}
	return value, true
}

func inspectToken(raw json.RawMessage, maximum int) (string, bool) {
	value, ok := inspectString(raw, maximum)
	return value, ok && inspectTokenPattern.MatchString(value)
}

func inspectEnum(raw json.RawMessage, values ...string) bool {
	value, ok := inspectString(raw, 96)
	if !ok {
		return false
	}
	for _, allowed := range values {
		if value == allowed {
			return true
		}
	}
	return false
}

func inspectNumber(raw json.RawMessage, minimum, maximum float64, integer, nullable bool) bool {
	if inspectNull(raw) {
		return nullable
	}
	if integer {
		// Decode integer tokens directly. A float64 round trip can turn an
		// unsafe or fractional integer token into an apparently valid counter.
		var value *int64
		return json.Unmarshal(raw, &value) == nil && value != nil && float64(*value) >= minimum && float64(*value) <= maximum
	}
	return numberValue(raw, minimum, maximum, integer)
}

func inspectBool(raw json.RawMessage, nullable bool) bool {
	if inspectNull(raw) {
		return nullable
	}
	var value *bool
	return json.Unmarshal(raw, &value) == nil && value != nil
}

func inspectArray(raw json.RawMessage, maximum int) ([]json.RawMessage, bool) {
	var value []json.RawMessage
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || trimmed[0] != '[' || json.Unmarshal(trimmed, &value) != nil || len(value) > maximum {
		return nil, false
	}
	return value, true
}

func parseInspectRequest(params map[string]json.RawMessage) *inspectRequest {
	runtime, ok := inspectToken(params["runtime"], 96)
	if !ok || !inspectNumber(params["version"], 1, 1, true, false) || !inspectEnum(params["section"], "doctor", "snapshot", "operation") {
		return nil
	}
	section, _ := inspectString(params["section"], 32)
	v := &inspectRequest{Runtime: runtime, Section: section}
	if section == "operation" {
		id, valid := inspectString(params["operation_id"], 80)
		if len(params) != 4 || !valid || !commandID.MatchString(id) {
			return nil
		}
		v.OperationID = id
	} else if len(params) != 3 {
		return nil
	}
	return v
}

// The expectation comes from the queued request, never from the responding
// document. All result statuses are bound, including unsupported and rejected.
func validInspectResult(raw json.RawMessage, status string, expected *inspectRequest) bool {
	if expected == nil || len(raw) > maxInspectResultBytes {
		return false
	}
	payload := "data"
	if status != "ok" {
		if status != "rejected" && status != "unsupported" {
			return false
		}
		payload = "error"
	}
	m, ok := inspectObject(raw, "version", "runtime", "section", payload)
	if !ok || !inspectNumber(m["version"], 1, 1, true, false) || !inspectEnum(m["runtime"], expected.Runtime) || !inspectEnum(m["section"], expected.Section) {
		return false
	}
	if status != "ok" {
		return inspectEnum(m["error"], "invalid_request", "runtime_mismatch", "unsupported", "unavailable")
	}
	if expected.Section == "operation" {
		return validInspectOperation(m["data"], expected.OperationID)
	}
	return validDoctorSnapshot(m["data"], expected.Runtime)
}

func validInspectOperation(raw json.RawMessage, operationID string) bool {
	m, ok := inspectObject(raw, "operation_id", "state", "action", "evidence")
	if !ok || !inspectEnum(m["operation_id"], operationID) || !inspectEnum(m["state"], "unknown", "accepted", "invoked", "observed", "rejected", "unsupported", "expired") {
		return false
	}
	if !inspectNull(m["action"]) && !inspectEnum(m["action"], "command", "play", "provider", "profile", "profile_settings", "provider_settings", "kiosk", "restart", "lifecycle", "input", "playback", "play_catalog", "play_archive_catalog", "vportal", "vportal_search", "vportal_random", "plex_queue", "vportal_queue", "maintenance") {
		return false
	}
	evidence, ok := inspectObject(m["evidence"], "kind", "generation", "position")
	if !ok || !inspectEnum(evidence["kind"], "none", "handler_completed", "media_progress", "runtime_changed") ||
		!inspectNumber(evidence["generation"], 0, inspectMaxInteger, true, true) ||
		!inspectNumber(evidence["position"], 0, inspectMaxSeconds, false, true) {
		return false
	}
	if inspectEnum(m["state"], "observed") && !inspectEnum(evidence["kind"], "media_progress", "runtime_changed") {
		return false
	}
	if inspectEnum(evidence["kind"], "media_progress") && (inspectNull(evidence["generation"]) || inspectNull(evidence["position"])) {
		return false
	}
	return true
}

func inspectPhase(raw json.RawMessage) bool {
	return inspectEnum(raw, "idle", "loading", "playing", "paused", "stopped", "ended", "error", "unknown")
}

func inspectOwnerKind(raw json.RawMessage) bool {
	return inspectEnum(raw, "list", "about", "dialog", "editor", "picker", "unknown")
}

func inspectPaneKind(raw json.RawMessage) bool {
	return inspectOwnerKind(raw) || inspectEnum(raw, "pin", "launch", "osd")
}

func validDoctorRect(raw json.RawMessage) bool {
	if inspectNull(raw) {
		return true
	}
	m, ok := inspectObject(raw, "x", "y", "width", "height")
	return ok && inspectNumber(m["x"], -32768, 32768, true, false) && inspectNumber(m["y"], -32768, 32768, true, false) &&
		inspectNumber(m["width"], 0, 32768, true, false) && inspectNumber(m["height"], 0, 32768, true, false)
}

func validDoctorVideo(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "exists", "cssVisible", "rect", "paused", "ended", "readyState", "networkState", "videoWidth", "videoHeight")
	return ok && inspectBool(m["exists"], false) && inspectBool(m["cssVisible"], true) && validDoctorRect(m["rect"]) &&
		inspectBool(m["paused"], true) && inspectBool(m["ended"], true) &&
		inspectNumber(m["readyState"], 0, 4, true, true) && inspectNumber(m["networkState"], 0, 3, true, true) &&
		inspectNumber(m["videoWidth"], 0, 32768, true, true) && inspectNumber(m["videoHeight"], 0, 32768, true, true)
}

func validDoctorUI(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "documentVisibility", "documentFocused", "owner", "revision", "panes", "focus")
	if !ok || !inspectEnum(m["documentVisibility"], "visible", "hidden", "unknown") || !inspectBool(m["documentFocused"], true) ||
		!inspectNumber(m["revision"], 0, inspectMaxInteger, true, true) || !(inspectPaneKind(m["focus"]) || inspectEnum(m["focus"], "player", "body", "other")) {
		return false
	}
	if !inspectNull(m["owner"]) {
		owner, valid := inspectObject(m["owner"], "kind", "id")
		if !valid || !inspectOwnerKind(owner["kind"]) || !inspectNumber(owner["id"], 0, inspectMaxInteger, true, false) {
			return false
		}
	}
	panes, ok := inspectArray(m["panes"], 8)
	if !ok {
		return false
	}
	for _, rawPane := range panes {
		pane, valid := inspectObject(rawPane, "kind", "exists", "cssVisible", "rect")
		if !valid || !inspectPaneKind(pane["kind"]) || !inspectBool(pane["exists"], false) || !inspectBool(pane["cssVisible"], true) || !validDoctorRect(pane["rect"]) {
			return false
		}
	}
	return true
}

func validDoctorMedia(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "generation", "kind", "phase", "lanes", "displayEvidence")
	if !ok || !inspectNumber(m["generation"], 0, inspectMaxInteger, true, true) || !inspectEnum(m["kind"], "live", "archive", "vod", "none", "unknown") ||
		!inspectPhase(m["phase"]) || !inspectEnum(m["displayEvidence"], "unavailable") {
		return false
	}
	lanes, ok := inspectArray(m["lanes"], 2)
	if !ok {
		return false
	}
	seen := map[string]bool{}
	for _, rawLane := range lanes {
		lane, valid := inspectObject(rawLane, "lane", "handleId", "phase", "position", "duration", "video")
		if !valid || !inspectEnum(lane["lane"], "main", "pip") || !inspectNumber(lane["handleId"], 0, inspectMaxInteger, true, true) ||
			!inspectPhase(lane["phase"]) || !inspectNumber(lane["position"], 0, inspectMaxSeconds, false, true) ||
			!inspectNumber(lane["duration"], 0, inspectMaxSeconds, false, true) || !validDoctorVideo(lane["video"]) {
			return false
		}
		name, _ := inspectString(lane["lane"], 8)
		if seen[name] {
			return false
		}
		seen[name] = true
	}
	return true
}

func validDoctorSnapshot(raw json.RawMessage, runtime string) bool {
	if len(raw) > maxDoctorSnapshotBytes {
		return false
	}
	m, ok := inspectObject(raw, "version", "runtime", "capturedAt", "collectionMs", "consistent", "build", "ui", "media", "capabilities", "reasons")
	if !ok || !inspectNumber(m["version"], 1, 1, true, false) || !inspectEnum(m["runtime"], runtime) ||
		!inspectNumber(m["capturedAt"], 0, inspectMaxInteger, true, false) || !inspectNumber(m["collectionMs"], 0, 60000, false, true) ||
		!inspectBool(m["consistent"], false) || !validDoctorUI(m["ui"]) || !validDoctorMedia(m["media"]) {
		return false
	}
	build, ok := inspectObject(m["build"], "version", "sourceRevision", "buildId", "identity")
	if !ok || !inspectEnum(build["identity"], "embedded", "partial") {
		return false
	}
	version, valid := inspectString(build["version"], 64)
	if !valid || !inspectVersionPattern.MatchString(version) {
		return false
	}
	if !inspectNull(build["sourceRevision"]) {
		revision, valid := inspectString(build["sourceRevision"], 40)
		if !valid || !inspectRevisionPattern.MatchString(revision) {
			return false
		}
	}
	if !inspectNull(build["buildId"]) {
		if _, valid := inspectToken(build["buildId"], 96); !valid {
			return false
		}
	}
	caps, ok := inspectArray(m["capabilities"], 10)
	if !ok {
		return false
	}
	seen := map[string]bool{}
	for _, rawCapability := range caps {
		capability, valid := inspectObject(rawCapability, "name", "state", "reason")
		if !valid || !inspectEnum(capability["name"], "screenshot", "diagnostics", "input", "restart_stream", "reload_player", "restart_app", "exit_app", "reboot_device", "standby", "wake") ||
			!inspectEnum(capability["state"], "available", "unavailable", "unknown") ||
			!inspectEnum(capability["reason"], "ready", "not_implemented", "producer_unavailable", "remote_disconnected", "source_selection_required", "busy", "no_active_media", "current_state_unsupported", "policy_restricted") {
			return false
		}
		name, _ := inspectString(capability["name"], 32)
		if seen[name] {
			return false
		}
		seen[name] = true
	}
	reasons, ok := inspectArray(m["reasons"], 16)
	if !ok {
		return false
	}
	seen = map[string]bool{}
	for _, rawReason := range reasons {
		if !inspectEnum(rawReason, "producer_unavailable", "producer_failed", "invalid_sample", "state_changed_during_snapshot", "build_identity_partial", "document_hidden", "document_unfocused", "owned_overlay_open", "video_element_missing", "video_css_hidden", "video_zero_rect", "decoder_not_ready", "decoder_paused", "decoder_ended", "decoder_error", "physical_display_unverified") {
			return false
		}
		reason, _ := inspectString(rawReason, 64)
		if seen[reason] {
			return false
		}
		seen[reason] = true
	}
	return true
}
