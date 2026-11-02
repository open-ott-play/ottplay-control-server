package control

import (
	"encoding/json"
	"math"
)

type relativeSeekRequest struct {
	Offset  float64
	Archive bool
}

func parseRelativeSeekRequest(params map[string]json.RawMessage) *relativeSeekRequest {
	var operation string
	var offset float64
	var timeline string
	archive := false
	if raw, exists := params["timeline"]; exists {
		if json.Unmarshal(raw, &timeline) != nil || timeline != "archive" {
			return nil
		}
		archive = true
	}
	fields := 2
	if archive {
		fields++
	}
	if len(params) != fields || json.Unmarshal(params["operation"], &operation) != nil || operation != "seek_relative" ||
		!numberValue(params["offset"], -9007199254740991, 9007199254740991, false) ||
		json.Unmarshal(params["offset"], &offset) != nil || offset == 0 {
		return nil
	}
	return &relativeSeekRequest{Offset: offset, Archive: archive}
}

func validRelativeSeekResult(raw json.RawMessage, status string, expected *relativeSeekRequest) bool {
	if expected == nil {
		return false
	}
	if status != "ok" {
		return status == "rejected" || status == "unsupported"
	}
	m, err := decodeObject(raw)
	fields := 6
	if expected.Archive {
		fields = 9
	}
	if err != nil || len(m) != fields {
		return false
	}
	var operation string
	var dispatched, clamped *bool
	var offset, from, position float64
	if json.Unmarshal(m["operation"], &operation) != nil || operation != "seek_relative" ||
		json.Unmarshal(m["dispatched"], &dispatched) != nil || dispatched == nil || !*dispatched ||
		json.Unmarshal(m["clamped"], &clamped) != nil || clamped == nil ||
		!numberValue(m["offset"], -9007199254740991, 9007199254740991, false) ||
		!numberValue(m["from"], 0, 9007199254740991, false) ||
		!numberValue(m["position"], 0, 9007199254740991, false) ||
		json.Unmarshal(m["offset"], &offset) != nil || offset != expected.Offset ||
		json.Unmarshal(m["from"], &from) != nil || json.Unmarshal(m["position"], &position) != nil {
		return false
	}
	target := from + offset
	if expected.Archive {
		var timeline string
		var earliest, latest float64
		if json.Unmarshal(m["timeline"], &timeline) != nil || timeline != "archive" ||
			!numberValue(m["earliest"], 1, 253402300799, true) ||
			!numberValue(m["latest"], 1, 253402300799, true) ||
			json.Unmarshal(m["earliest"], &earliest) != nil || json.Unmarshal(m["latest"], &latest) != nil ||
			latest < earliest || from < earliest || from > latest || position != math.Floor(position) {
			return false
		}
		rounded := math.Floor(target)
		bounded := math.Max(earliest, math.Min(latest, rounded))
		return position == bounded && *clamped == (bounded != rounded)
	}
	if !*clamped {
		return position == target
	}
	// The player owns duration and its end guard. Validate the lower clamp and
	// the direction of an upper clamp without inventing a duration in the relay.
	if target < 0 {
		return position == 0
	}
	return position < target
}
