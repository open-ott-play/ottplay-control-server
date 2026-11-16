package control

import (
	"encoding/json"
	"reflect"
)

// These fixed slots retain the first fatal and latest safe observations without
// accepting engine objects, messages, URLs or a growing error history.
func validDebugAttemptErrors(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "firstFatal", "latest", "observed", "dropped")
	if !ok || !inspectNumber(m["observed"], 0, inspectMaxInteger, true, false) ||
		!inspectNumber(m["dropped"], 0, inspectMaxInteger, true, false) {
		return false
	}
	observed := perfInteger(m["observed"])
	if perfInteger(m["dropped"]) > observed {
		return false
	}
	first, firstOK := validDebugAttemptError(m["firstFatal"], observed, true)
	latest, latestOK := validDebugAttemptError(m["latest"], observed, false)
	if !firstOK || !latestOK || first != 0 && (latest == 0 || first > latest) {
		return false
	}
	// Before saturation one sequence identifies one observation. Compare
	// decoded fields so JSON key order and equivalent numeric spellings do not
	// create a false mismatch. At saturation distinct later observations may
	// legitimately retain the same sequence number.
	if first != 0 && first == latest && first < inspectMaxInteger {
		var firstValue, latestValue map[string]any
		if json.Unmarshal(m["firstFatal"], &firstValue) != nil || json.Unmarshal(m["latest"], &latestValue) != nil {
			return false
		}
		return reflect.DeepEqual(firstValue, latestValue)
	}
	return true
}

func validDebugAttemptError(raw json.RawMessage, observed int64, fatal bool) (int64, bool) {
	if inspectNull(raw) {
		return 0, true
	}
	m, ok := inspectObject(raw, "sequence", "elapsedMs", "engine", "type", "detail", "fatal", "httpStatus", "mediaErrorCode")
	if !ok || !inspectNumber(m["sequence"], 1, float64(observed), true, false) ||
		!inspectNumber(m["elapsedMs"], 0, inspectMaxInteger, false, true) ||
		!inspectEnum(m["engine"], "hls.js", "html5", "shaka", "unknown") ||
		!inspectEnum(m["type"], "network", "media", "mux", "key_system", "manifest", "other") ||
		inspectNull(m["detail"]) || !perfHLSDetailRule(3)(m["detail"]) ||
		!inspectBool(m["fatal"], false) ||
		!inspectNumber(m["httpStatus"], 100, 599, true, true) ||
		!inspectNumber(m["mediaErrorCode"], 1, 4, true, true) {
		return 0, false
	}
	if fatal {
		var value bool
		if json.Unmarshal(m["fatal"], &value) != nil || !value {
			return 0, false
		}
	}
	return perfInteger(m["sequence"]), true
}
