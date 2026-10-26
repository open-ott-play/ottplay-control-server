package control

import (
	"encoding/json"
	"regexp"
	"strings"
)

const maxPerfDataBytes = 60 * 1024
const maxPerfOuterBytes = 64 * 1024
const maxPerfEventBytes = 2048

var perfSourceID = regexp.MustCompile(`^s[1-9][0-9]{0,9}$`)
var perfNotificationID = regexp.MustCompile(`^n[1-9][0-9]{0,9}$`)
var perfStages = []string{"received", "queued", "dom_created", "media_fetch_start", "media_received", "image_loaded", "shown", "updated", "hidden", "dropped", "coalesced"}
var perfTimings = []string{"fetch_ms", "base64_ms", "validation_ms", "blob_ms", "image_load_decode_ms", "decode_ms", "queue_ms"}
var perfStopReasons = []string{"requested", "expired", "disconnected", "page_hidden", "runtime_changed", "unsupported"}

type perfCaptureRequest struct {
	Runtime, Op, Session string
	After                int64
	Duration, Limit      int
	Version              int
	RAF, LongTasks       bool
}

type perfRule func(json.RawMessage) bool

func perfNumber(minimum, maximum float64, integer, nullable bool) perfRule {
	return func(raw json.RawMessage) bool { return inspectNumber(raw, minimum, maximum, integer, nullable) }
}
func perfEnum(nullable bool, values ...string) perfRule {
	return func(raw json.RawMessage) bool { return nullable && inspectNull(raw) || inspectEnum(raw, values...) }
}
func perfPattern(pattern *regexp.Regexp, nullable bool) perfRule {
	return func(raw json.RawMessage) bool { return nullable && inspectNull(raw) || settingPattern(raw, pattern) }
}
func perfBoolean(nullable bool) perfRule {
	return func(raw json.RawMessage) bool { return inspectBool(raw, nullable) }
}
func perfObject(raw json.RawMessage, fields map[string]perfRule) (map[string]json.RawMessage, bool) {
	m, err := decodeObject(raw)
	if err != nil || len(m) != len(fields) {
		return nil, false
	}
	for key, rule := range fields {
		if !rule(m[key]) {
			return nil, false
		}
	}
	return m, true
}
func perfInteger(raw json.RawMessage) int64 { var v int64; _ = json.Unmarshal(raw, &v); return v }
func perfFloat(raw json.RawMessage) float64 { var v float64; _ = json.Unmarshal(raw, &v); return v }
func perfTrue(raw json.RawMessage) bool     { var v bool; _ = json.Unmarshal(raw, &v); return v }

func parsePerfCaptureRequest(m map[string]json.RawMessage) *perfCaptureRequest {
	v := &perfCaptureRequest{Runtime: settingString(m["runtime"]), Op: settingString(m["op"]), Session: settingString(m["session_id"]), Duration: 120, Limit: 64, LongTasks: true}
	if !inspectNumber(m["version"], 1, 2, true, false) || !settingToken.MatchString(v.Runtime) {
		return nil
	}
	v.Version = int(perfInteger(m["version"]))
	base := []string{"version", "op", "runtime"}
	switch v.Op {
	case "start":
		if !settingKeys(m, append(base, "session_id"), []string{"duration_seconds", "raf", "long_tasks"}) {
			return nil
		}
		if raw, ok := m["duration_seconds"]; ok {
			if !inspectNumber(raw, 10, 600, true, false) {
				return nil
			}
			v.Duration = int(perfInteger(raw))
		}
		for _, key := range []string{"raf", "long_tasks"} {
			if raw, ok := m[key]; ok && !inspectBool(raw, false) {
				return nil
			}
		}
		if raw, ok := m["raf"]; ok {
			v.RAF = perfTrue(raw)
		}
		if raw, ok := m["long_tasks"]; ok {
			v.LongTasks = perfTrue(raw)
		}
	case "status":
		if !settingKeys(m, base, []string{"session_id"}) {
			return nil
		}
	case "read":
		if !settingKeys(m, append(base, "session_id", "after_seq"), []string{"limit"}) || !inspectNumber(m["after_seq"], 0, inspectMaxInteger, true, false) {
			return nil
		}
		v.After = perfInteger(m["after_seq"])
		if raw, ok := m["limit"]; ok {
			if !inspectNumber(raw, 1, 64, true, false) {
				return nil
			}
			v.Limit = int(perfInteger(raw))
		}
	case "stop":
		if !settingKeys(m, append(base, "session_id"), nil) {
			return nil
		}
	default:
		return nil
	}
	if raw, ok := m["session_id"]; ok {
		if !settingPattern(raw, commandID) {
			return nil
		}
	} else if v.Op != "status" {
		return nil
	}
	return v
}

func validPerfCapability(raw json.RawMessage) bool {
	_, ok := perfObject(raw, map[string]perfRule{
		"version": perfNumber(1, 1, true, false), "default_duration_seconds": perfNumber(120, 120, true, false),
		"max_duration_seconds": perfNumber(600, 600, true, false), "max_events": perfNumber(1024, 1024, true, false),
		"max_bytes": perfNumber(524288, 524288, true, false), "max_read_events": perfNumber(64, 64, true, false),
	})
	return ok
}

func validPerfHLSCapability(raw json.RawMessage) bool {
	_, ok := perfObject(raw, map[string]perfRule{"version": perfNumber(2, 2, true, false)})
	return ok
}

func perfSet(raw json.RawMessage, values []string) (map[string]bool, bool) {
	rows, ok := inspectArray(raw, len(values))
	if !ok {
		return nil, false
	}
	result := map[string]bool{}
	for _, row := range rows {
		name := settingString(row)
		if !inspectEnum(row, values...) || result[name] {
			return nil, false
		}
		result[name] = true
	}
	return result, true
}

type perfSession struct {
	ID, State          string
	First, Next, Count int64
	Elapsed            float64
	Stages, Timings    map[string]bool
	RAF, LongTasks     bool
	CaptureVersion     int
}

func validPerfSession(raw json.RawMessage, expected *perfCaptureRequest) *perfSession {
	fields := map[string]perfRule{
		"id": perfPattern(commandID, false), "state": perfEnum(false, "active", "stopped"), "reason": perfEnum(true, perfStopReasons...),
		"duration_seconds": perfNumber(10, 600, true, false), "sample_interval_ms": perfNumber(1000, 1000, true, false),
		"elapsed_ms": perfNumber(0, inspectMaxInteger, false, false), "remaining_ms": perfNumber(0, inspectMaxInteger, false, false),
		"started_at_unix_ms": perfNumber(0, inspectMaxInteger, true, false), "clock": perfEnum(false, "performance", "wall_fallback"),
		"first_seq": perfNumber(1, inspectMaxInteger, true, false), "next_seq": perfNumber(1, inspectMaxInteger, true, false),
		"event_count": perfNumber(0, 1024, true, false), "bytes": perfNumber(0, 524288, true, false),
		"requested": func(raw json.RawMessage) bool {
			_, ok := perfObject(raw, map[string]perfRule{"raf": perfBoolean(false), "long_tasks": perfBoolean(false)})
			return ok
		},
		"supported": func(raw json.RawMessage) bool {
			_, ok := perfObject(raw, map[string]perfRule{"raf": perfBoolean(false), "long_tasks": perfBoolean(false),
				"notification_stages":  func(v json.RawMessage) bool { _, ok := perfSet(v, perfStages); return ok },
				"notification_timings": func(v json.RawMessage) bool { _, ok := perfSet(v, perfTimings); return ok },
			})
			return ok
		},
	}
	for _, key := range []string{"dropped_events", "rate_dropped", "ring_dropped", "oversized_dropped"} {
		fields[key] = perfNumber(0, inspectMaxInteger, true, false)
	}
	if expected.Version == 2 {
		fields["capture_version"] = perfNumber(1, 2, true, false)
	}
	m, ok := perfObject(raw, fields)
	if !ok {
		return nil
	}
	v := &perfSession{ID: settingString(m["id"]), State: settingString(m["state"]), First: perfInteger(m["first_seq"]), Next: perfInteger(m["next_seq"]), Count: perfInteger(m["event_count"]), Elapsed: perfFloat(m["elapsed_ms"])}
	v.CaptureVersion = 1
	if expected.Version == 2 {
		v.CaptureVersion = int(perfInteger(m["capture_version"]))
	}
	if expected.Op == "start" && v.CaptureVersion != expected.Version {
		return nil
	}
	if expected.Session != "" && v.ID != expected.Session || v.First > v.Next || v.Count > v.Next-v.First || (v.Count == 0) != (v.First == v.Next) ||
		(v.State == "active") != inspectNull(m["reason"]) || v.State == "stopped" && perfFloat(m["remaining_ms"]) != 0 ||
		perfFloat(m["remaining_ms"]) > float64(perfInteger(m["duration_seconds"]))*1000 ||
		v.Count+perfInteger(m["dropped_events"]) != v.Next-1 ||
		perfInteger(m["dropped_events"]) != perfInteger(m["rate_dropped"])+perfInteger(m["ring_dropped"])+perfInteger(m["oversized_dropped"]) {
		return nil
	}
	requested, _ := decodeObject(m["requested"])
	supported, _ := decodeObject(m["supported"])
	v.RAF = perfTrue(requested["raf"]) && perfTrue(supported["raf"])
	v.LongTasks = perfTrue(requested["long_tasks"]) && perfTrue(supported["long_tasks"])
	v.Stages, _ = perfSet(supported["notification_stages"], perfStages)
	v.Timings, _ = perfSet(supported["notification_timings"], perfTimings)
	if expected.Op == "start" && (perfInteger(m["duration_seconds"]) != int64(expected.Duration) || perfTrue(requested["raf"]) != expected.RAF || perfTrue(requested["long_tasks"]) != expected.LongTasks) || expected.Op == "stop" && v.State != "stopped" {
		return nil
	}
	return v
}

func perfAdd(fields map[string]perfRule, names string, rule perfRule) {
	for _, name := range strings.Fields(names) {
		fields[name] = rule
	}
}

func validPerfBuffered(raw json.RawMessage) bool {
	m, ok := perfObject(raw, map[string]perfRule{
		"range_count": perfNumber(0, 65535, true, true), "truncated_count": perfNumber(0, 65535, true, true),
		"ranges": func(raw json.RawMessage) bool { _, ok := inspectArray(raw, 8); return ok },
	})
	if !ok {
		return false
	}
	rows, _ := inspectArray(m["ranges"], 8)
	if inspectNull(m["range_count"]) || inspectNull(m["truncated_count"]) {
		return inspectNull(m["range_count"]) && inspectNull(m["truncated_count"]) && len(rows) == 0
	}
	count, truncated := perfInteger(m["range_count"]), perfInteger(m["truncated_count"])
	if count-truncated != int64(len(rows)) {
		return false
	}
	last := int64(-1)
	for _, row := range rows {
		rangeValue, valid := perfObject(row, map[string]perfRule{"index": perfNumber(0, 65534, true, false), "start_seconds": perfNumber(-inspectMaxInteger, inspectMaxInteger, false, false), "end_seconds": perfNumber(-inspectMaxInteger, inspectMaxInteger, false, false)})
		if !valid {
			return false
		}
		index := perfInteger(rangeValue["index"])
		if index >= count || last >= 0 && index != last+1 || perfFloat(rangeValue["start_seconds"]) > perfFloat(rangeValue["end_seconds"]) {
			return false
		}
		last = index
	}
	return true
}

func validPerfFrames(raw json.RawMessage) bool {
	fields := map[string]perfRule{"source": perfEnum(false, "quality", "webkit", "unsupported")}
	perfAdd(fields, "total dropped corrupted", perfNumber(0, inspectMaxInteger, true, true))
	m, ok := perfObject(raw, fields)
	if !ok {
		return false
	}
	if settingString(m["source"]) == "unsupported" {
		return inspectNull(m["total"]) && inspectNull(m["dropped"]) && inspectNull(m["corrupted"])
	}
	return inspectNull(m["total"]) || inspectNull(m["dropped"]) || perfInteger(m["dropped"]) <= perfInteger(m["total"])
}

func validPerfEventData(kind string, raw json.RawMessage, session *perfSession) bool {
	number := perfNumber(0, inspectMaxInteger, false, true)
	integer := perfNumber(0, inspectMaxInteger, true, true)
	fields := map[string]perfRule{}
	switch kind {
	case "hls_buffer", "hls_fragment", "hls_config", "hls_live":
		return session.CaptureVersion == 2 && validPerfHLSData(kind, raw)
	case "sample":
		fields = map[string]perfRule{
			"position_seconds": perfNumber(-inspectMaxInteger, inspectMaxInteger, false, true), "buffer_ahead_seconds": number,
			"playback_rate": perfNumber(0, 16, false, true),
			"buffered":      validPerfBuffered, "frames": validPerfFrames, "ready_state": perfNumber(0, 4, true, true), "network_state": perfNumber(0, 3, true, true),
			"attempt":      func(raw json.RawMessage) bool { return validDebugAttempt(raw, false) },
			"last_attempt": func(raw json.RawMessage) bool { return validDebugAttempt(raw, true) },
		}
		perfAdd(fields, "paused seeking ended", perfBoolean(true))
		perfAdd(fields, "width height", perfNumber(0, 65535, true, true))
	case "media":
		fields = map[string]perfRule{"event": perfEnum(false, "waiting", "stalled", "playing", "pause", "seeking", "seeked", "error", "ended"), "error_code": perfNumber(1, 4, true, true), "reason": inspectNull}
	case "hls":
		fields = map[string]perfRule{
			"event": perfEnum(false, "fragment", "playlist", "error", "fragment_buffered"), "http_status": perfNumber(100, 599, true, true),
			"timing_source": perfEnum(false, "performance", "unavailable"), "fatal": perfBoolean(true), "category": perfEnum(true, "network", "media", "other"),
			"detail": perfEnum(true, "manifest_load_error", "manifest_load_timeout", "level_load_error", "level_load_timeout", "level_empty_error", "frag_load_error", "frag_load_timeout", "frag_parsing_error", "buffer_append_error", "buffer_stalled_error", "buffer_nudge_on_stall", "buffer_seek_over_hole", "other"),
		}
		perfAdd(fields, "level part_index fragment_sequence bytes retries", integer)
		perfAdd(fields, "load_start_ms first_byte_at_ms load_end_ms", perfNumber(-inspectMaxInteger, inspectMaxInteger, false, true))
		perfAdd(fields, "load_duration_ms first_byte_duration_ms parse_duration_ms buffer_duration_ms", number)
	case "abr":
		fields = map[string]perfRule{"from_level": integer, "to_level": integer, "automatic": perfBoolean(true), "bitrate": number}
		perfAdd(fields, "width height", perfNumber(0, 65535, true, true))
	case "recovery":
		fields = map[string]perfRule{"event": perfEnum(false, "engine_start", "fatal_error", "native_fallback", "stream_retry"), "attempt": func(raw json.RawMessage) bool { return validDebugAttempt(raw, false) }}
	case "loop":
		fields = map[string]perfRule{"window_ms": perfNumber(0, inspectMaxInteger, false, false), "samples": perfNumber(0, inspectMaxInteger, true, false)}
		perfAdd(fields, "max_delay_ms raf_max_gap_ms long_tasks_total_ms long_tasks_max_ms", number)
		perfAdd(fields, "raf_callbacks long_tasks long_task_entries_dropped", integer)
	case "notification":
		fields = map[string]perfRule{
			"notification_id": perfPattern(perfNotificationID, true), "stage": perfEnum(false, perfStages...),
			"reason": perfEnum(true, "disabled", "expired", "duplicate", "invalid", "capacity", "superseded", "hidden", "stopped", "media_error", "unsupported"),
			"media":  perfEnum(false, "none", "image"), "active_count": perfNumber(0, 3, true, false),
			"image_width": perfNumber(1, 640, true, true), "image_height": perfNumber(1, 360, true, true), "image_bytes": perfNumber(1, 163840, true, true),
		}
		for _, key := range perfTimings {
			fields[key] = number
		}
	case "session":
		fields = map[string]perfRule{"event": perfEnum(false, "started", "stopped", "source_changed"), "reason": perfEnum(true, perfStopReasons...)}
	default:
		return false
	}
	m, ok := perfObject(raw, fields)
	if !ok {
		return false
	}
	if kind == "notification" {
		if !session.Stages[settingString(m["stage"])] {
			return false
		}
		for _, key := range perfTimings {
			if !inspectNull(m[key]) && !session.Timings[key] {
				return false
			}
		}
	}
	if kind == "loop" {
		if !session.RAF && (!inspectNull(m["raf_callbacks"]) || !inspectNull(m["raf_max_gap_ms"])) {
			return false
		}
		if !session.LongTasks {
			for _, key := range []string{"long_tasks", "long_tasks_total_ms", "long_tasks_max_ms", "long_task_entries_dropped"} {
				if !inspectNull(m[key]) {
					return false
				}
			}
		}
	}
	return true
}

func validPerfEvent(raw json.RawMessage, session *perfSession) (int64, float64, bool) {
	if len(raw) > maxPerfEventBytes {
		return 0, 0, false
	}
	m, ok := inspectObject(raw, "seq", "at_ms", "kind", "lane", "generation", "handle_id", "source_id", "data")
	if !ok || !inspectNumber(m["seq"], 1, inspectMaxInteger, true, false) || !inspectNumber(m["at_ms"], 0, session.Elapsed, false, false) ||
		!perfEnum(true, "main", "pip")(m["lane"]) || !inspectNumber(m["generation"], 0, inspectMaxInteger, true, true) ||
		!inspectNumber(m["handle_id"], 0, inspectMaxInteger, true, true) || !perfPattern(perfSourceID, true)(m["source_id"]) ||
		!validPerfEventData(settingString(m["kind"]), m["data"], session) {
		return 0, 0, false
	}
	seq := perfInteger(m["seq"])
	if isPerfHLSDetail(settingString(m["kind"])) && (inspectNull(m["lane"]) || inspectNull(m["source_id"])) {
		return 0, 0, false
	}
	return seq, perfFloat(m["at_ms"]), seq >= session.First && seq < session.Next
}

func validPerfCaptureResult(raw json.RawMessage, status string, expected *perfCaptureRequest) bool {
	if expected == nil || len(raw) > maxPerfDataBytes {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !inspectNumber(m["version"], float64(expected.Version), float64(expected.Version), true, false) || settingString(m["op"]) != expected.Op || settingString(m["runtime"]) != expected.Runtime {
		return false
	}
	if status != "ok" {
		return (status == "rejected" || status == "unsupported") && settingKeys(m, []string{"version", "op", "runtime", "error"}, nil) &&
			inspectEnum(m["error"], "invalid_request", "runtime_mismatch", "unsupported", "not_found", "busy", "disconnected", "expired")
	}
	keys := []string{"version", "op", "runtime", "session"}
	if expected.Op == "read" {
		keys = append(keys, "events", "next_after_seq", "more", "lost_before")
	}
	if !settingKeys(m, keys, nil) {
		return false
	}
	if inspectNull(m["session"]) {
		return expected.Op == "status" && expected.Session == ""
	}
	session := validPerfSession(m["session"], expected)
	if session == nil {
		return false
	}
	if expected.Op != "read" {
		return true
	}
	if expected.After >= session.Next || !inspectBool(m["more"], false) || !inspectNumber(m["next_after_seq"], 0, inspectMaxInteger, true, false) || !inspectNumber(m["lost_before"], 0, inspectMaxInteger, true, false) {
		return false
	}
	events, ok := inspectArray(m["events"], expected.Limit)
	if !ok || int64(len(events)) > session.Count {
		return false
	}
	lost := session.First - expected.After - 1
	if lost < 0 {
		lost = 0
	}
	if perfInteger(m["lost_before"]) != lost {
		return false
	}
	last, elapsed := expected.After, float64(0)
	for _, event := range events {
		seq, at, valid := validPerfEvent(event, session)
		if !valid || seq <= last || at < elapsed {
			return false
		}
		last, elapsed = seq, at
	}
	next := perfInteger(m["next_after_seq"])
	if perfTrue(m["more"]) {
		return len(events) > 0 && next == last && last < session.Next-1
	}
	return next == session.Next-1 && (expected.After >= session.First || int64(len(events)) == session.Count)
}
