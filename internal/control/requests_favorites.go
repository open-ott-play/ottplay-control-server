package control

import (
	"encoding/json"
	"regexp"
	"strings"
)

const maxFavorites = 100
const maxFavoritesResultBytes = 1024 * 1024

var favoriteIDPattern = regexp.MustCompile(`^fav-[a-z0-9-]{1,60}$`)
var favoriteProviderPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_./-]{0,63}$`)
var favoritesErrors = map[string]bool{
	"invalid_request": true, "unsupported": true, "no_current_item": true,
	"unsupported_item": true, "profile_unavailable": true, "profile_changed": true,
	"item_unavailable": true, "ambiguous_favorite": true, "favorite_not_found": true,
	"too_many_favorites": true, "storage_unavailable": true, "policy_denied": true,
	"busy": true, "timeout": true, "cancelled": true, "context_changed": true,
	"playback_failed": true,
}

type favoritesRequest struct {
	Runtime string
	Op      string
	ID      string
}

func favoriteText(raw json.RawMessage, maximum int) bool {
	value, ok := inspectString(raw, maximum)
	return ok && strings.TrimSpace(value) != ""
}

func parseFavoritesRequest(params map[string]json.RawMessage) *favoritesRequest {
	v := &favoritesRequest{}
	if json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) ||
		json.Unmarshal(params["op"], &v.Op) != nil {
		return nil
	}
	switch v.Op {
	case "list":
		if len(params) == 2 {
			return v
		}
	case "add":
		if len(params) == 2 || (len(params) == 3 && favoriteText(params["name"], 160)) {
			return v
		}
	case "play", "remove":
		if len(params) != 3 {
			return nil
		}
		if raw, exists := params["id"]; exists {
			if json.Unmarshal(raw, &v.ID) == nil && favoriteIDPattern.MatchString(v.ID) {
				return v
			}
		} else if favoriteText(params["query"], 160) {
			return v
		}
	}
	return nil
}

func favoriteEntryID(raw json.RawMessage) (string, bool) {
	m, ok := inspectObject(raw, "id", "name", "provider", "profile_number", "profile_name", "kind", "title")
	var id, provider string
	if !ok || json.Unmarshal(m["id"], &id) != nil || !favoriteIDPattern.MatchString(id) ||
		json.Unmarshal(m["provider"], &provider) != nil || !favoriteProviderPattern.MatchString(provider) ||
		!favoriteText(m["name"], 160) || !favoriteText(m["title"], 512) ||
		!profileText(m["profile_name"], 256) || !inspectEnum(m["kind"], "live", "vod") ||
		(!inspectNull(m["profile_number"]) && !profileInteger(m["profile_number"], 1, 15)) {
		return "", false
	}
	return id, true
}

// The player owns durable favorites and their private provider references. The
// controller stores only bounded metadata tied to this exact request/runtime.
func validFavoritesResult(raw json.RawMessage, status string, request *favoritesRequest) bool {
	if request == nil || len(raw) > maxFavoritesResultBytes {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || !inspectEnum(m["runtime"], request.Runtime) ||
		!inspectEnum(m["op"], request.Op) {
		return false
	}
	if inspectEnum(m["state"], "error") {
		var code string
		return len(m) == 5 && json.Unmarshal(m["error"], &code) == nil && favoritesErrors[code]
	}
	if status != "ok" {
		return false
	}
	if request.Op == "list" {
		rows, ok := inspectArray(m["entries"], maxFavorites)
		if len(m) != 5 || !ok || !inspectEnum(m["state"], "ready") {
			return false
		}
		seen := make(map[string]bool)
		for _, row := range rows {
			id, valid := favoriteEntryID(row)
			if !valid || seen[id] {
				return false
			}
			seen[id] = true
		}
		return true
	}
	id, ok := favoriteEntryID(m["entry"])
	if len(m) != 6 || !ok || (request.ID != "" && request.ID != id) {
		return false
	}
	switch request.Op {
	case "add":
		return inspectEnum(m["state"], "ready") && inspectBool(m["created"], false)
	case "remove":
		var removed bool
		return inspectEnum(m["state"], "ready") && json.Unmarshal(m["removed"], &removed) == nil && removed
	case "play":
		var dispatched bool
		return inspectEnum(m["state"], "dispatched") && json.Unmarshal(m["dispatched"], &dispatched) == nil && dispatched
	}
	return false
}
