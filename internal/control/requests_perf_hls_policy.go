package control

import "encoding/json"

func perfHLSPolicyFields() map[string]perfRule {
	fields := map[string]perfRule{}
	perfAdd(fields, "back_buffer_length_seconds max_buffer_length_seconds max_max_buffer_length_seconds", perfNumber(0, inspectMaxInteger, false, true))
	fields["max_buffer_size_bytes"] = perfNumber(0, inspectMaxInteger, true, true)
	return fields
}

func validPerfHLSPolicyData(kind string, raw json.RawMessage) bool {
	fields := map[string]perfRule{}
	switch kind {
	case "hls_buffer_policy":
		fields = map[string]perfRule{
			"event":                                  perfEnum(false, "snapshot", "cap_observation_changed"),
			"initial_source":                         perfEnum(false, "constructor_snapshot", "unavailable"),
			"previous_max_max_buffer_length_seconds": perfNumber(0, inspectMaxInteger, false, true),
			"observation":                            perfEnum(false, "attachment", "sample", "hls_event"),
			"cause":                                  perfEnum(false, "unknown"),
		}
		for _, name := range []string{"initial", "current"} {
			fields[name] = func(value json.RawMessage) bool { _, ok := perfObject(value, perfHLSPolicyFields()); return ok }
		}
	case "hls_error_action":
		fields = map[string]perfRule{
			"category": perfEnum(false, "network", "media", "other"),
			"detail":   perfHLSDetailRule(3),
			"action":   perfEnum(true, "do_nothing", "send_end_callback", "send_alternate_to_penalty_box", "remove_alternate_permanently", "insert_discontinuity", "retry_request"),
			"flags":    perfNumber(0, 31, true, true),
			"resolved": perfBoolean(true),
		}
		perfAdd(fields, "fragment_sequence part_index level retry_budget_count", perfNumber(0, inspectMaxInteger, true, true))
	default:
		return false
	}
	m, ok := perfObject(raw, fields)
	if !ok || kind != "hls_buffer_policy" {
		return ok
	}
	initial, _ := decodeObject(m["initial"])
	if settingString(m["initial_source"]) == "unavailable" {
		for _, value := range initial {
			if !inspectNull(value) {
				return false
			}
		}
	}
	previous := m["previous_max_max_buffer_length_seconds"]
	if settingString(m["event"]) == "snapshot" {
		return inspectNull(previous)
	}
	current, _ := decodeObject(m["current"])
	next := current["max_max_buffer_length_seconds"]
	if inspectNull(previous) || inspectNull(next) {
		return inspectNull(previous) != inspectNull(next)
	}
	return perfFloat(previous) != perfFloat(next)
}

func perfHLSDetailRule(version int) perfRule {
	details := []string{"manifest_load_error", "manifest_load_timeout", "level_load_error", "level_load_timeout", "level_empty_error", "frag_load_error", "frag_load_timeout", "frag_parsing_error", "buffer_append_error", "buffer_stalled_error", "buffer_nudge_on_stall", "buffer_seek_over_hole", "other"}
	if version == 3 {
		details = append(details, "buffer_full_error", "internal_exception", "buffer_append_no_progress", "buffer_add_codec_error")
	}
	return perfEnum(true, details...)
}
