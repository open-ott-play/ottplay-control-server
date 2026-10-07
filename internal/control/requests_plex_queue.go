package control

import (
	"encoding/json"
	"reflect"
	"regexp"
)

const maxPlexQueueItems = 100
const maxPlexQueueResultBytes = 16 * 1024
const maxPlexPreviewResultBytes = 128 * 1024

var plexQueueIDPattern = regexp.MustCompile(`^[1-9][0-9]{0,19}$`)

var plexQueueErrors = map[string]bool{
	"Plex configuration is missing or invalid.":               true,
	"Plex provider module could not be loaded.":               true,
	"Plex server is unreachable or access was denied.":        true,
	"One or more Plex items are unavailable or not playable.": true,
	"Plex playback is unavailable on this player.":            true,
	"Plex queue request timed out.":                           true,
	"Plex queue request was cancelled.":                       true,
	"Player context changed before Plex playback.":            true,
	"Plex playback could not start.":                          true,
	"Plex queue is empty.":                                    true,
	"Plex queue is already at its first item.":                true,
	"Plex queue is already at its last item.":                 true,
	"Unlock parental access before starting the Plex queue.":  true,
	"Kiosk mode does not allow a Plex queue.":                 true,
}

func validPlexQueueError(raw json.RawMessage) bool {
	var value string
	return json.Unmarshal(raw, &value) == nil && plexQueueErrors[value]
}

type plexQueueRequest struct {
	Runtime string
	Op      string
	IDs     []string
}

func plexQueueIDs(raw json.RawMessage, minimum int) ([]string, bool) {
	var ids []string
	if len(raw) == 0 || raw[0] != '[' || json.Unmarshal(raw, &ids) != nil || len(ids) < minimum || len(ids) > maxPlexQueueItems {
		return nil, false
	}
	for _, id := range ids {
		if !plexQueueIDPattern.MatchString(id) {
			return nil, false
		}
	}
	return ids, true
}

func parsePlexQueueRequest(params map[string]json.RawMessage) *plexQueueRequest {
	v := &plexQueueRequest{}
	if json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) || json.Unmarshal(params["op"], &v.Op) != nil {
		return nil
	}
	switch v.Op {
	case "play", "preview":
		var valid bool
		v.IDs, valid = plexQueueIDs(params["ids"], 1)
		if len(params) != 3 || !valid {
			return nil
		}
	case "status", "next", "previous", "stop":
		if len(params) != 2 {
			return nil
		}
	default:
		return nil
	}
	return v
}

func validPlexQueueResult(raw json.RawMessage, request *plexQueueRequest) bool {
	if request != nil && request.Op == "preview" {
		return validPlexPreviewResult(raw, request)
	}
	if len(raw) > maxPlexQueueResultBytes {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil {
		return false
	}
	required := []string{"version", "runtime", "active", "state", "ids", "index", "repeat", "order"}
	allowed := map[string]bool{"title": true, "error": true}
	for _, key := range required {
		allowed[key] = true
		if m[key] == nil {
			return false
		}
	}
	for key := range m {
		if !allowed[key] {
			return false
		}
	}
	var runtime, state, repeat, order string
	var active *bool
	if !profileInteger(m["version"], 1, 1) || json.Unmarshal(m["runtime"], &runtime) != nil || !screenshotRuntimePattern.MatchString(runtime) ||
		json.Unmarshal(m["active"], &active) != nil || active == nil || json.Unmarshal(m["state"], &state) != nil ||
		json.Unmarshal(m["repeat"], &repeat) != nil || repeat != "none" || json.Unmarshal(m["order"], &order) != nil || order != "listed" {
		return false
	}
	ids, valid := plexQueueIDs(m["ids"], 0)
	if !valid || (len(ids) == 0 && string(m["index"]) != "null") || (len(ids) > 0 && !profileInteger(m["index"], 0, len(ids)-1)) {
		return false
	}
	switch state {
	case "idle":
		valid = !*active && len(ids) == 0
	case "preparing", "playing", "paused":
		valid = *active && len(ids) > 0
	case "ended":
		valid = *active && len(ids) > 0 && profileInteger(m["index"], len(ids)-1, len(ids)-1)
	case "error":
		valid = *active == (len(ids) > 0)
	default:
		valid = false
	}
	if title, exists := m["title"]; exists && !profileText(title, 512) {
		return false
	}
	if problem, exists := m["error"]; exists && (state != "error" || !validPlexQueueError(problem)) {
		return false
	}
	if request != nil {
		if runtime != request.Runtime {
			return false
		}
		if request.Op == "play" && (!reflect.DeepEqual(ids, request.IDs) || !profileInteger(m["index"], 0, 0)) {
			return false
		}
		if request.Op == "stop" && state != "idle" {
			return false
		}
	}
	return valid
}

func validPlexPreviewResult(raw json.RawMessage, request *plexQueueRequest) bool {
	if len(raw) > maxPlexPreviewResultBytes {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || len(m) < 6 || len(m) > 7 {
		return false
	}
	for key := range m {
		switch key {
		case "version", "runtime", "state", "ids", "titles", "order", "error":
		default:
			return false
		}
	}
	var runtime, state, order string
	var titles []json.RawMessage
	ids, valid := plexQueueIDs(m["ids"], 1)
	if !valid || !reflect.DeepEqual(ids, request.IDs) || !profileInteger(m["version"], 1, 1) ||
		json.Unmarshal(m["runtime"], &runtime) != nil || runtime != request.Runtime ||
		json.Unmarshal(m["state"], &state) != nil || (state != "ready" && state != "error") ||
		json.Unmarshal(m["order"], &order) != nil || order != "listed" ||
		len(m["titles"]) == 0 || m["titles"][0] != '[' || json.Unmarshal(m["titles"], &titles) != nil ||
		(len(titles) != 0 && len(titles) != len(ids)) || (state == "ready" && len(titles) != len(ids)) {
		return false
	}
	for _, title := range titles {
		if !profileText(title, 512) {
			return false
		}
	}
	if problem, exists := m["error"]; exists && (state != "error" || !validPlexQueueError(problem)) {
		return false
	}
	return true
}

// Keep existing channel receipts compatible while bounding the additive Plex
// variant; a player must never return both destinations for one step.
func validPlexPlaybackResult(raw json.RawMessage, operation string) bool {
	m, err := decodeObject(raw)
	if err != nil || m["plex_queue"] == nil {
		return true
	}
	var op string
	var dispatched *bool
	return len(raw) <= maxPlexQueueResultBytes && len(m) == 3 && json.Unmarshal(m["operation"], &op) == nil && op == operation &&
		json.Unmarshal(m["dispatched"], &dispatched) == nil && dispatched != nil && *dispatched && validPlexQueueResult(m["plex_queue"], nil)
}
