package control

import (
	"encoding/json"
	"reflect"
	"regexp"
)

const maxPlexQueueItems = 500
const maxPlexQueueResultBytes = 16 * 1024
const maxPlexPreviewResultBytes = 1024 * 1024
const maxPlexQueueStateV2Items = 1000
const maxPlexQueueStateV2ResultBytes = 32 * 1024

var plexQueueIDPattern = regexp.MustCompile(`^[1-9][0-9]{0,19}$`)

var plexQueueErrors = map[string]bool{
	"Plex configuration is missing or invalid.":               true,
	"Plex provider module could not be loaded.":               true,
	"Plex server is unreachable or access was denied.":        true,
	"One or more Plex items are unavailable or not playable.": true,
	"Plex playback is unavailable on this player.":            true,
	"Plex queue request timed out.":                           true,
	"Plex queue request was cancelled.":                       true,
	"Plex queue request is already in progress.":              true,
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
	Runtime      string
	Op           string
	IDs          []string
	StateVersion int
}

func plexQueueIDs(raw json.RawMessage, minimum int) ([]string, bool) {
	return plexQueueIDsBounded(raw, minimum, maxPlexQueueItems)
}

func plexQueueIDsBounded(raw json.RawMessage, minimum, maximum int) ([]string, bool) {
	var ids []string
	if len(raw) == 0 || raw[0] != '[' || json.Unmarshal(raw, &ids) != nil || len(ids) < minimum || len(ids) > maximum {
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
	v := &plexQueueRequest{StateVersion: 1}
	if json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) || json.Unmarshal(params["op"], &v.Op) != nil {
		return nil
	}
	count := len(params)
	if raw, exists := params["state_version"]; exists {
		if !profileInteger(raw, 2, 2) || v.Op == "preview" {
			return nil
		}
		v.StateVersion = 2
		count--
	}
	switch v.Op {
	case "play", "preview":
		var valid bool
		v.IDs, valid = plexQueueIDs(params["ids"], 1)
		if count != 3 || !valid {
			return nil
		}
	case "status", "next", "previous", "stop":
		if count != 2 {
			return nil
		}
	default:
		return nil
	}
	return v
}

// Only explicit queue requests may negotiate v2. Nil and zero-value request
// contexts are used by the existing library and generic playback v1 receipts.
func plexQueueStateLimits(request *plexQueueRequest) (version, items, bytes int) {
	if request != nil && request.StateVersion == 2 {
		return 2, maxPlexQueueStateV2Items, maxPlexQueueStateV2ResultBytes
	}
	return 1, maxPlexQueueItems, maxPlexQueueResultBytes
}

func validPlexQueueResponse(raw json.RawMessage, status string, request *plexQueueRequest) bool {
	if status == "ok" {
		return validPlexQueueResult(raw, request)
	}
	if request.StateVersion != 2 {
		// Preserve the legacy negative-envelope contract, including previews.
		return len(raw) <= maxPlexPreviewResultBytes
	}
	// Rejected v2 requests may carry the fixed upgrade-required message rather
	// than a queue state. Do not add it to the v1 queue-state error vocabulary.
	_, _, maximum := plexQueueStateLimits(request)
	if len(raw) > maximum {
		return false
	}
	_, err := decodeObject(raw)
	return err == nil && validFavoritesTransferJSON(raw)
}

func validPlexQueueStateV2Capability(raw, base json.RawMessage) bool {
	m, err := decodeObject(raw)
	if err != nil || len(m) != 5 || !profileInteger(m["version"], 2, 2) ||
		!profileInteger(m["max_write_items"], maxPlexQueueItems, maxPlexQueueItems) ||
		!profileInteger(m["max_state_items"], maxPlexQueueStateV2Items, maxPlexQueueStateV2Items) ||
		!profileInteger(m["max_state_bytes"], maxPlexQueueStateV2ResultBytes, maxPlexQueueStateV2ResultBytes) {
		return false
	}
	operations, valid := plexQueueCapabilityOperations(m["operations"], false)
	if !valid || len(operations) != 5 {
		return false
	}
	legacy, err := decodeObject(base)
	if err != nil || len(legacy) != 3 || !profileInteger(legacy["version"], 1, 1) ||
		!profileInteger(legacy["max_items"], maxPlexQueueItems, maxPlexQueueItems) {
		return false
	}
	baseOperations, valid := plexQueueCapabilityOperations(legacy["operations"], true)
	if !valid {
		return false
	}
	for operation := range operations {
		if !baseOperations[operation] {
			return false
		}
	}
	return true
}

func plexQueueCapabilityOperations(raw json.RawMessage, allowPreview bool) (map[string]bool, bool) {
	var operations []string
	if len(raw) == 0 || raw[0] != '[' || json.Unmarshal(raw, &operations) != nil {
		return nil, false
	}
	seen := make(map[string]bool)
	for _, operation := range operations {
		switch operation {
		case "play", "status", "next", "previous", "stop":
		case "preview":
			if !allowPreview {
				return nil, false
			}
		default:
			return nil, false
		}
		if seen[operation] {
			return nil, false
		}
		seen[operation] = true
	}
	return seen, true
}

func validPlexQueueResult(raw json.RawMessage, request *plexQueueRequest) bool {
	if request != nil && request.Op == "preview" {
		return validPlexPreviewResult(raw, request)
	}
	version, maxItems, maxBytes := plexQueueStateLimits(request)
	if len(raw) > maxBytes {
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
	if !profileInteger(m["version"], version, version) || json.Unmarshal(m["runtime"], &runtime) != nil || !screenshotRuntimePattern.MatchString(runtime) ||
		json.Unmarshal(m["active"], &active) != nil || active == nil || json.Unmarshal(m["state"], &state) != nil ||
		json.Unmarshal(m["repeat"], &repeat) != nil || (repeat != "none" && repeat != "all") || json.Unmarshal(m["order"], &order) != nil || order != "listed" {
		return false
	}
	ids, valid := plexQueueIDsBounded(m["ids"], 0, maxItems)
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
