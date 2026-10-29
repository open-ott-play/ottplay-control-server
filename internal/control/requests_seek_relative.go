package control

import "encoding/json"

type relativeSeekRequest struct {
	Offset float64
}

func parseRelativeSeekRequest(params map[string]json.RawMessage) *relativeSeekRequest {
	var operation string
	var offset float64
	if len(params) != 2 || json.Unmarshal(params["operation"], &operation) != nil || operation != "seek_relative" ||
		!numberValue(params["offset"], -9007199254740991, 9007199254740991, false) ||
		json.Unmarshal(params["offset"], &offset) != nil || offset == 0 {
		return nil
	}
	return &relativeSeekRequest{Offset: offset}
}

func validRelativeSeekResult(raw json.RawMessage, status string, expected *relativeSeekRequest) bool {
	if expected == nil {
		return false
	}
	if status != "ok" {
		return status == "rejected" || status == "unsupported"
	}
	m, err := decodeObject(raw)
	if err != nil || len(m) != 6 {
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
