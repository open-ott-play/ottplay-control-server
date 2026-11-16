package control

import "encoding/json"

// Preparation diagnostics are a single passive snapshot. They cannot start a
// capture, resolve a source, retry preparation or change playback.
const maxPlaybackPreparationResultBytes = 4096
const maxPlaybackPreparationOuterBytes = 4096

type playbackPreparationRequest struct {
	Runtime string
}

func validPlaybackPreparationCapability(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "version")
	return ok && inspectNumber(m["version"], 1, 1, true, false)
}

func parsePlaybackPreparationRequest(m map[string]json.RawMessage) *playbackPreparationRequest {
	runtime, ok := inspectToken(m["runtime"], 96)
	if !ok || !settingKeys(m, []string{"version", "runtime"}, nil) || !inspectNumber(m["version"], 1, 1, true, false) {
		return nil
	}
	return &playbackPreparationRequest{Runtime: runtime}
}

func validPlaybackPreparationResult(raw json.RawMessage, status string, expected *playbackPreparationRequest) bool {
	if expected == nil || len(raw) > maxPlaybackPreparationResultBytes {
		return false
	}
	if status != "ok" {
		m, ok := inspectObject(raw, "version", "runtime", "error")
		if !ok || status != "rejected" || !inspectNumber(m["version"], 1, 1, true, false) ||
			!inspectEnum(m["error"], "invalid_request", "runtime_mismatch", "unavailable", "unsupported", "policy_denied") {
			return false
		}
		// The native mismatch rejection names the receiver's current runtime,
		// not the stale requested one. It remains a negative observation only.
		if inspectEnum(m["error"], "runtime_mismatch") {
			_, valid := inspectToken(m["runtime"], 96)
			return valid
		}
		return inspectEnum(m["runtime"], expected.Runtime)
	}
	m, ok := inspectObject(raw, "version", "runtime", "phase", "attempt_generation", "last_failure", "cause", "retry")
	if !ok || !inspectNumber(m["version"], 1, 1, true, false) || !inspectEnum(m["runtime"], expected.Runtime) ||
		!inspectEnum(m["phase"], "idle", "resolving", "inspecting", "awaiting_quality", "admitting", "admitted", "failed", "cancelled") ||
		!inspectNumber(m["attempt_generation"], 0, inspectMaxInteger, true, true) ||
		(!inspectNull(m["last_failure"]) && !inspectEnum(m["last_failure"], "resolve_failed", "inspect_failed", "worker_unavailable", "admission_failed", "quality_save_failed")) ||
		(!inspectNull(m["cause"]) && !inspectEnum(m["cause"], "unknown", "timeout", "transport", "http_401", "http_403", "http_404", "http_429", "http_server", "hls_invalid_header", "hls_invalid_utf8", "media_open_refused", "media_read_failed", "media_container_unavailable", "media_stream_probe_failed", "media_no_video", "inspection_stopped", "inspection_cancelled", "inspection_unavailable", "resource_limit", "track_identity", "codec_unsupported")) {
		return false
	}
	retry, ok := inspectObject(m["retry"], "pending", "failures", "retry_after_ms")
	if !ok || !inspectBool(retry["pending"], false) {
		return false
	}
	var pending bool
	_ = json.Unmarshal(retry["pending"], &pending)
	if !pending {
		return inspectNull(retry["failures"]) && inspectNull(retry["retry_after_ms"])
	}
	return inspectNumber(retry["failures"], 0, 4294967295, true, false) &&
		inspectNumber(retry["retry_after_ms"], 0, 30000, true, false)
}
