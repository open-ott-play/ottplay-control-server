package control

import (
	"encoding/json"
	"regexp"
)

const maxNativePlaybackCaptureOuterBytes = 65536

var nativePlaybackCaptureID = regexp.MustCompile(`^cap_[0-9a-f]{32}$`)

// Keep operation and capture expectations in the queue and retained receipt.
// The player checks runtime before executing; capture replies do not echo it.
type nativePlaybackCaptureRequest struct {
	Runtime, Operation, CaptureID string
	Duration                      int
}

func validNativePlaybackCaptureCapability(raw json.RawMessage) bool {
	_, ok := perfObject(raw, map[string]perfRule{
		"version": perfNumber(1, 1, true, false),
		"operations": func(v json.RawMessage) bool {
			rows, valid := inspectArray(v, 4)
			return valid && len(rows) == 4 && inspectEnum(rows[0], "start") && inspectEnum(rows[1], "status") &&
				inspectEnum(rows[2], "read") && inspectEnum(rows[3], "stop")
		},
		"default_duration_s": perfNumber(30, 30, true, false),
		"min_duration_s":     perfNumber(5, 5, true, false),
		"max_duration_s":     perfNumber(60, 60, true, false),
		"sample_interval_ms": perfNumber(1000, 1000, true, false),
		"max_records":        perfNumber(64, 64, true, false),
	})
	return ok
}

func parseNativePlaybackCaptureRequest(m map[string]json.RawMessage) *nativePlaybackCaptureRequest {
	runtime, ok := inspectToken(m["runtime"], 96)
	if !ok || !inspectNumber(m["version"], 1, 1, true, false) {
		return nil
	}
	v := &nativePlaybackCaptureRequest{Runtime: runtime, Operation: settingString(m["operation"]), Duration: 30}
	base := []string{"version", "runtime", "operation"}
	switch v.Operation {
	case "start":
		if !settingKeys(m, append(base, "duration_s"), nil) || !inspectNumber(m["duration_s"], 5, 60, true, false) {
			return nil
		}
		v.Duration = int(perfInteger(m["duration_s"]))
	case "status", "read", "stop":
		if !settingKeys(m, append(base, "capture_id"), nil) {
			return nil
		}
		if v.Operation == "status" && inspectNull(m["capture_id"]) {
			break
		}
		if !settingPattern(m["capture_id"], nativePlaybackCaptureID) {
			return nil
		}
		v.CaptureID = settingString(m["capture_id"])
	default:
		return nil
	}
	return v
}

func validNativePlaybackCaptureRecord(raw json.RawMessage) bool {
	fields := map[string]perfRule{
		"t_ms":                  perfNumber(0, 60000, true, false),
		"stage":                 perfEnum(false, "loading", "playing", "bound", "streaming"),
		"load_state":            perfEnum(false, "pending", "complete", "failed"),
		"frames":                perfNumber(0, 4294967295, true, false),
		"video_queue_bytes":     perfNumber(0, 4294967295, true, false),
		"audio_queue_bytes":     perfNumber(0, 4294967295, true, false),
		"callback_count":        perfNumber(0, 4294967295, true, false),
		"fps_milli":             perfNumber(0, 4294967295, true, false),
		"frame_age_ms":          perfNumber(0, 60000, true, true),
		"playable_buffer_ms":    perfNumber(0, 60000, true, true),
		"callback_error_age_ms": perfNumber(0, 60000, true, true),
		"fed_video_units":       perfNumber(0, inspectMaxInteger, true, false),
		"fed_audio_units":       perfNumber(0, inspectMaxInteger, true, false),
		"network_rx_bytes":      perfNumber(0, inspectMaxInteger, true, false),
		"fed_pts_skew_ms":       perfNumber(-600000, 600000, true, true),
		"feed_state":            perfEnum(false, "none", "accepting", "full", "refused", "holding", "empty"),
		"callback_error_code":   perfNumber(-2147483648, 2147483647, true, true),
		"http_status":           perfNumber(100, 599, true, true),
		"video_width":           perfNumber(0, 65535, true, false),
		"video_height":          perfNumber(0, 65535, true, false),
	}
	object, err := decodeObject(raw)
	if err != nil {
		return false
	}
	priming := map[string]perfRule{
		"prime_pending":            perfBoolean(false),
		"prime_kind":               perfEnum(false, "none", "fresh", "rebuffer", "resume"),
		"prime_required_runway_ms": perfNumber(0, 86400000, true, false),
		"prime_accepted_video_ms":  perfNumber(0, 86400000, true, false),
		"prime_accepted_audio_ms":  perfNumber(0, 86400000, true, false),
		"prime_queued_video_ms":    perfNumber(0, 86400000, true, false),
		"prime_queued_audio_ms":    perfNumber(0, 86400000, true, false),
		"prime_external_lease":     perfBoolean(false),
		"prime_audio_expected":     perfBoolean(false),
	}
	for key := range priming {
		if _, present := object[key]; present {
			// The extension is an all-or-none closed group. Older players keep
			// their original record; partial or unbounded extensions are refused.
			for name, rule := range priming {
				fields[name] = rule
			}
			break
		}
	}
	_, ok := perfObject(raw, fields)
	return ok
}

// Only closed, numeric/enum reports are retained in the in-memory receipt.
// The command journal stores outcome metadata, never this payload.
func validNativePlaybackCaptureResult(raw json.RawMessage, status string, expected *nativePlaybackCaptureRequest) bool {
	if expected == nil || len(raw) > maxNativePlaybackCaptureOuterBytes ||
		(expected.Operation != "start" && expected.Operation != "status" && expected.Operation != "read" && expected.Operation != "stop") {
		return false
	}
	if status != "ok" {
		m, ok := inspectObject(raw, "version", "operation", "error")
		if !ok || !inspectNumber(m["version"], 1, 1, true, false) || !inspectEnum(m["operation"], expected.Operation) {
			return false
		}
		if status == "unsupported" {
			return inspectEnum(m["error"], "unsupported")
		}
		return status == "rejected" && inspectEnum(m["error"], "invalid_request", "runtime_mismatch", "no_active_playback", "capture_not_found", "policy_denied", "capture_busy")
	}
	keys := []string{"version", "operation", "state", "capture_id"}
	if expected.Operation != "start" {
		keys = append(keys, "elapsed_ms", "records", "dropped")
	}
	m, ok := inspectObject(raw, keys...)
	if !ok || !inspectNumber(m["version"], 1, 1, true, false) || !inspectEnum(m["operation"], expected.Operation) {
		return false
	}
	if expected.Operation == "start" {
		return inspectEnum(m["state"], "capturing") && settingPattern(m["capture_id"], nativePlaybackCaptureID)
	}
	if !inspectNumber(m["elapsed_ms"], 0, 60000, true, false) || !inspectNumber(m["dropped"], 0, 4294967295, true, false) {
		return false
	}
	if inspectEnum(m["state"], "idle") {
		return expected.Operation == "status" && expected.CaptureID == "" && inspectNull(m["capture_id"]) &&
			inspectNumber(m["records"], 0, 0, true, false) && perfInteger(m["elapsed_ms"]) == 0 && perfInteger(m["dropped"]) == 0
	}
	if !settingPattern(m["capture_id"], nativePlaybackCaptureID) || (expected.CaptureID != "" && settingString(m["capture_id"]) != expected.CaptureID) ||
		!inspectEnum(m["state"], "capturing", "complete", "stopped", "session_changed", "expired") ||
		(expected.Operation == "stop" && inspectEnum(m["state"], "capturing")) {
		return false
	}
	if expected.Operation != "read" {
		return inspectNumber(m["records"], 0, 64, true, false)
	}
	rows, ok := inspectArray(m["records"], 64)
	if !ok {
		return false
	}
	previous := int64(-1)
	for _, rawRecord := range rows {
		if !validNativePlaybackCaptureRecord(rawRecord) {
			return false
		}
		record, _ := decodeObject(rawRecord)
		at := perfInteger(record["t_ms"])
		if at < previous || at > perfInteger(m["elapsed_ms"]) {
			return false
		}
		previous = at
	}
	return true
}
