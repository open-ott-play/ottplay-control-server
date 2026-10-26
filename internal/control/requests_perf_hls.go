package control

import "encoding/json"

var perfHLSConfigFields = []string{"max_buffer_length_seconds", "max_max_buffer_length_seconds", "back_buffer_length_seconds", "max_buffer_hole_seconds", "high_buffer_watchdog_period_seconds", "nudge_offset_seconds", "live_sync_duration_seconds", "live_sync_duration_count", "live_max_latency_duration_seconds", "live_max_latency_duration_count"}

func isPerfHLSDetail(kind string) bool {
	return kind == "hls_buffer" || kind == "hls_fragment" || kind == "hls_config" || kind == "hls_live"
}

func perfOrderedPair(m map[string]json.RawMessage, start, end string) bool {
	return inspectNull(m[start]) || inspectNull(m[end]) || perfFloat(m[start]) <= perfFloat(m[end])
}

func validPerfElementaryStreams(raw json.RawMessage) bool {
	rows, ok := inspectArray(raw, 3)
	if !ok {
		return false
	}
	seen := map[string]bool{}
	for _, row := range rows {
		m, ok := perfObject(row, map[string]perfRule{
			"track":             perfEnum(false, "audio", "video", "audiovideo"),
			"start_pts_seconds": perfNumber(-inspectMaxInteger, inspectMaxInteger, false, true),
			"end_pts_seconds":   perfNumber(-inspectMaxInteger, inspectMaxInteger, false, true),
		})
		if !ok || !perfOrderedPair(m, "start_pts_seconds", "end_pts_seconds") {
			return false
		}
		track := settingString(m["track"])
		if seen[track] {
			return false
		}
		seen[track] = true
	}
	return true
}

func validPerfHLSData(kind string, raw json.RawMessage) bool {
	signed := perfNumber(-inspectMaxInteger, inspectMaxInteger, false, true)
	fields := map[string]perfRule{}
	switch kind {
	case "hls_buffer":
		fields = map[string]perfRule{"track": perfEnum(false, "audio", "video", "audiovideo"), "position_seconds": signed, "buffered": validPerfBuffered}
	case "hls_fragment":
		fields = map[string]perfRule{"event": perfEnum(false, "fragment", "fragment_buffered"), "elementary_streams": validPerfElementaryStreams}
		perfAdd(fields, "level part_index fragment_sequence discontinuity_counter", perfNumber(0, inspectMaxInteger, true, true))
		perfAdd(fields, "start_seconds end_seconds start_pts_seconds end_pts_seconds", signed)
	case "hls_config":
		for _, key := range perfHLSConfigFields {
			fields[key] = perfNumber(0, inspectMaxInteger, false, true)
		}
		perfAdd(fields, "max_buffer_size_bytes nudge_max_retry", perfNumber(0, inspectMaxInteger, true, true))
	case "hls_live":
		perfAdd(fields, "position_seconds edge_seconds live_sync_position_seconds latency_seconds", signed)
		perfAdd(fields, "target_duration_seconds target_latency_seconds max_latency_seconds effective_buffer_length_seconds", perfNumber(0, inspectMaxInteger, false, true))
		perfAdd(fields, "current_level load_level", validPerfHLSLevel)
	default:
		return false
	}
	m, ok := perfObject(raw, fields)
	return ok && (kind != "hls_fragment" || perfOrderedPair(m, "start_seconds", "end_seconds") && perfOrderedPair(m, "start_pts_seconds", "end_pts_seconds"))
}

func validPerfHLSLevel(raw json.RawMessage) bool {
	m, ok := perfObject(raw, map[string]perfRule{
		"index":       perfNumber(-1, 65535, true, true),
		"bitrate":     perfNumber(0, inspectMaxInteger, false, true),
		"max_bitrate": perfNumber(0, inspectMaxInteger, false, true),
	})
	if !ok {
		return false
	}
	return !inspectNull(m["index"]) && perfInteger(m["index"]) >= 0 || inspectNull(m["bitrate"]) && inspectNull(m["max_bitrate"])
}
