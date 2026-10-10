package control

import (
	"encoding/json"
	"strings"
)

const maxPlexLibraries = 1000
const maxPlexLibraryResultBytes = 1024 * 1024

var plexLibraryErrors = map[string]bool{
	"ambiguous_library": true, "library_not_found": true, "unsupported_library": true,
	"too_many_libraries": true, "too_many_items": true, "incomplete_library": true,
}

type plexLibraryRequest struct {
	Runtime   string
	Op        string
	LibraryID string
	Shuffle   bool
}

func parsePlexLibraryRequest(params map[string]json.RawMessage) *plexLibraryRequest {
	v := &plexLibraryRequest{}
	if json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) ||
		json.Unmarshal(params["op"], &v.Op) != nil {
		return nil
	}
	if v.Op == "list" {
		if len(params) == 2 {
			return v
		}
		return nil
	}
	if v.Op != "preview" && v.Op != "play" {
		return nil
	}
	count := len(params)
	if raw, exists := params["shuffle"]; exists {
		var shuffle *bool
		if json.Unmarshal(raw, &shuffle) != nil || shuffle == nil {
			return nil
		}
		v.Shuffle = *shuffle
		count--
	}
	if count != 3 {
		return nil
	}
	if raw, exists := params["library_id"]; exists {
		if json.Unmarshal(raw, &v.LibraryID) != nil || !plexQueueIDPattern.MatchString(v.LibraryID) {
			return nil
		}
	} else {
		var query string
		if !profileText(params["query"], 256) || json.Unmarshal(params["query"], &query) != nil || strings.TrimSpace(query) == "" {
			return nil
		}
	}
	return v
}

func plexLibraryID(raw json.RawMessage) (string, bool) {
	m, err := decodeObject(raw)
	var id, title, kind string
	if err != nil || len(m) != 3 || json.Unmarshal(m["id"], &id) != nil || !plexQueueIDPattern.MatchString(id) ||
		!profileText(m["title"], 160) || json.Unmarshal(m["title"], &title) != nil || strings.TrimSpace(title) == "" ||
		json.Unmarshal(m["type"], &kind) != nil || (kind != "movie" && kind != "show") {
		return "", false
	}
	return id, true
}

func validPlexLibraryResult(raw json.RawMessage, status string, request *plexLibraryRequest) bool {
	if len(raw) > maxPlexLibraryResultBytes {
		return false
	}
	m, err := decodeObject(raw)
	var runtime, op, state string
	if err != nil || !profileInteger(m["version"], 1, 1) || json.Unmarshal(m["runtime"], &runtime) != nil || runtime != request.Runtime ||
		json.Unmarshal(m["op"], &op) != nil || op != request.Op || json.Unmarshal(m["state"], &state) != nil {
		return false
	}
	if state == "error" {
		var problem string
		return len(m) == 5 && json.Unmarshal(m["error"], &problem) == nil && (plexLibraryErrors[problem] || plexQueueErrors[problem])
	}
	if state != "ready" || status != "ok" {
		return false
	}
	if op == "list" {
		var libraries []json.RawMessage
		if len(m) != 5 || len(m["libraries"]) == 0 || m["libraries"][0] != '[' ||
			json.Unmarshal(m["libraries"], &libraries) != nil || len(libraries) > maxPlexLibraries {
			return false
		}
		seen := make(map[string]bool)
		for _, library := range libraries {
			id, valid := plexLibraryID(library)
			if !valid || seen[id] {
				return false
			}
			seen[id] = true
		}
		return true
	}
	id, valid := plexLibraryID(m["library"])
	var shuffled *bool
	if len(m) != 7 || !valid || (request.LibraryID != "" && request.LibraryID != id) ||
		json.Unmarshal(m["shuffled"], &shuffled) != nil || shuffled == nil || *shuffled != request.Shuffle {
		return false
	}
	queue, err := decodeObject(m["queue"])
	if err != nil {
		return false
	}
	ids, valid := plexQueueIDs(queue["ids"], 1)
	if !valid {
		return false
	}
	// Discovery establishes these IDs inside the player, rather than trusting a
	// second controller-side lookup. The existing queue validator still checks
	// runtime, preview titles and a play response starting at index zero.
	var queueState string
	return json.Unmarshal(queue["state"], &queueState) == nil && queueState != "error" &&
		validPlexQueueResult(m["queue"], &plexQueueRequest{Runtime: runtime, Op: op, IDs: ids})
}
