package control

import (
	"encoding/json"
	"strings"
)

const maxDebugSnapshotBytes = 16 * 1024

const debugBrowserNumbers = "uptimeMs loopSamples loopDelayMs loopMaxDelayMs loopLongDelays jsHeapUsedBytes jsHeapTotalBytes jsHeapLimitBytes hardwareConcurrency deviceMemoryGiB errorCount rejectionCount controlPendingRequests controlPendingResponses controlConsecutiveFailures"
const debugBrowserBools = "online focused visible secureContext controlActive"
const debugMediaNumbers = "positionSeconds durationSeconds bufferAheadSeconds videoWidth videoHeight totalFrames decodedFrames droppedFrames corruptedFrames readyState networkState mediaErrorCode volume"
const debugMediaBools = "paused ended muted seeking"
const debugNativeNumbers = "uptimeMs systemUptimeMs residentBytes pssBytes footprintBytes heapUsedBytes heapLimitBytes systemAvailableBytes systemTotalBytes thermalState logicalProcessors nativeHlsSessions nativeHlsBytes nativeHlsErrors epgChannels epgProgrammes epgMappings epgShifts requestsTotal requestsActive requestsFailed"
const debugNativeBools = "lowMemory foreground lowPower"
const debugEventCodes = "started resumed suspended visible hidden online offline focus blur error unhandled_rejection loop_delay media_error media_waiting media_stalled media_playing media_ended"

func debugField(fields, key string) bool {
	for _, allowed := range strings.Fields(fields) {
		if key == allowed {
			return true
		}
	}
	return false
}

// Missing metrics mean unavailable. Unknown metrics and arbitrary nested data
// are rejected here, before either storage or an operator response.
func validDebugMetrics(raw json.RawMessage, numbers, bools string, media bool) bool {
	m, err := decodeObject(raw)
	if err != nil {
		return false
	}
	for key, value := range m {
		switch {
		case debugField(numbers, key):
			maximum := float64(inspectMaxInteger)
			if media {
				switch key {
				case "volume":
					maximum = 1
				case "readyState", "mediaErrorCode":
					maximum = 4
				case "networkState":
					maximum = 3
				}
			}
			if !inspectNumber(value, 0, maximum, false, false) {
				return false
			}
		case debugField(bools, key):
			if !inspectBool(value, false) {
				return false
			}
		default:
			return false
		}
	}
	return true
}

func validDebugNative(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "state", "data")
	if !ok || !inspectEnum(m["state"], "available", "unsupported", "unavailable", "timeout", "invalid") {
		return false
	}
	if !inspectEnum(m["state"], "available") {
		return inspectNull(m["data"])
	}
	v, ok := inspectObject(m["data"], "version", "platform", "appVersion", "osVersion", "webviewVersion", "metrics")
	if !ok || !inspectNumber(v["version"], 1, 1, true, false) || !inspectEnum(v["platform"], "android", "ios", "tauri", "server") {
		return false
	}
	for _, key := range []string{"appVersion", "osVersion", "webviewVersion"} {
		if inspectNull(v[key]) {
			continue
		}
		value, valid := inspectString(v[key], 64)
		if !valid || !inspectVersionPattern.MatchString(value) {
			return false
		}
	}
	return validDebugMetrics(v["metrics"], debugNativeNumbers, debugNativeBools, false)
}

func validDebugSnapshot(raw json.RawMessage, runtime string) bool {
	if len(raw) > maxDebugSnapshotBytes {
		return false
	}
	m, ok := inspectObject(raw, "version", "runtime", "capturedAt", "platform", "metrics", "media", "events", "eventsDropped", "native")
	if !ok || !inspectNumber(m["version"], 1, 1, true, false) || !inspectEnum(m["runtime"], runtime) ||
		!inspectNumber(m["capturedAt"], 0, inspectMaxInteger, true, false) ||
		!inspectEnum(m["platform"], "browser", "webos", "capacitor-android", "capacitor-ios", "tauri", "unknown") ||
		!validDebugMetrics(m["metrics"], debugBrowserNumbers, debugBrowserBools, false) ||
		!inspectNumber(m["eventsDropped"], 0, inspectMaxInteger, true, false) || !validDebugNative(m["native"]) {
		return false
	}
	lanes, ok := inspectArray(m["media"], 2)
	if !ok {
		return false
	}
	seen := map[string]bool{}
	for _, rawLane := range lanes {
		lane, valid := inspectObject(rawLane, "lane", "generation", "handleId", "metrics")
		if !valid || !inspectEnum(lane["lane"], "main", "pip") ||
			!inspectNumber(lane["generation"], 0, inspectMaxInteger, true, true) ||
			!inspectNumber(lane["handleId"], 0, inspectMaxInteger, true, true) ||
			!validDebugMetrics(lane["metrics"], debugMediaNumbers, debugMediaBools, true) {
			return false
		}
		name, _ := inspectString(lane["lane"], 8)
		if seen[name] {
			return false
		}
		seen[name] = true
	}
	events, ok := inspectArray(m["events"], 32)
	if !ok {
		return false
	}
	var previous int64
	for _, rawEvent := range events {
		event, valid := inspectObject(rawEvent, "sequence", "elapsedMs", "code")
		if !valid || !inspectNumber(event["sequence"], 1, inspectMaxInteger, true, false) ||
			!inspectNumber(event["elapsedMs"], 0, inspectMaxInteger, false, false) ||
			!inspectEnum(event["code"], strings.Fields(debugEventCodes)...) {
			return false
		}
		var sequence int64
		if json.Unmarshal(event["sequence"], &sequence) != nil || sequence <= previous {
			return false
		}
		previous = sequence
	}
	return true
}
