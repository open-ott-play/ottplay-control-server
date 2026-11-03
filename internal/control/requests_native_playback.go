package control

import "encoding/json"

// The budget covers the complete inner inspect envelope, not only its data.
const maxNativePlaybackResultBytes = 4096

func validNativePlaybackCapability(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "version")
	return ok && inspectNumber(m["version"], 1, 1, true, false)
}

func validNativePlaybackSnapshot(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "version", "scope", "native_epoch", "phase", "counters", "ages_ms", "pending_command", "last_error")
	if !ok || !inspectNumber(m["version"], 1, 1, true, false) || !inspectEnum(m["scope"], "native_load") ||
		!inspectNumber(m["native_epoch"], 1, 4294967295, true, true) ||
		!inspectEnum(m["phase"], "idle", "resolving", "opening", "buffering", "playing", "paused", "seeking", "stopping", "error", "standby", "unknown") {
		return false
	}
	counters, ok := inspectObject(m["counters"], "video_demuxed_units", "audio_demuxed_units", "video_submitted_units", "audio_submitted_units", "video_position_events", "video_displayed_reports", "video_displayed_frames", "video_decoded_frames", "audio_decoded_frames", "audio_presented_frames")
	if !ok {
		return false
	}
	// V1 has no measurement source for these fields. Zero would incorrectly
	// turn unavailable evidence into an observed absence of progress.
	for _, key := range []string{"video_demuxed_units", "audio_demuxed_units", "video_decoded_frames", "audio_decoded_frames", "audio_presented_frames"} {
		if !inspectNull(counters[key]) {
			return false
		}
	}
	noEpoch := inspectNull(m["native_epoch"])
	for _, value := range counters {
		if !inspectNumber(value, 0, inspectMaxInteger, true, true) || (noEpoch && !inspectNull(value)) {
			return false
		}
	}
	ages, ok := inspectObject(m["ages_ms"], "video_submission", "audio_submission", "video_position_event", "video_displayed_report")
	if !ok {
		return false
	}
	for _, value := range ages {
		if !inspectNumber(value, 0, inspectMaxInteger, true, true) || (noEpoch && !inspectNull(value)) {
			return false
		}
	}
	if !inspectNull(m["pending_command"]) {
		pending, valid := inspectObject(m["pending_command"], "stage", "age_ms")
		if !valid || !inspectEnum(pending["stage"], "admitted", "waiting_catalog", "resolving_source") ||
			!inspectNumber(pending["age_ms"], 0, inspectMaxInteger, true, true) {
			return false
		}
	}
	if !inspectNull(m["last_error"]) {
		lastError, valid := inspectObject(m["last_error"], "code")
		if !valid || !inspectEnum(m["phase"], "error") ||
			!inspectEnum(lastError["code"], "media_source", "playback_interrupted", "tv_pipeline", "load_timeout", "release_timeout", "resolve_failed", "unspecified") {
			return false
		}
	}
	return true
}
