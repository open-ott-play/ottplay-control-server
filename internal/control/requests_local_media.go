package control

import (
	"encoding/json"
	"regexp"
	"strings"
)

const maxLocalMediaSources = 32
const maxLocalMediaItems = 50000
const maxLocalMediaResultBytes = 192 * 1024

var localMediaAbsolutePattern = regexp.MustCompile(`^(?:/|[A-Za-z]:[\\/])`)
var localMediaIDPattern = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,256}$`)
var localMediaErrors = map[string]bool{
	"invalid_request": true, "runtime_changed": true, "restricted": true,
	"unsupported": true, "source_not_found": true, "source_ambiguous": true,
	"source_unavailable": true, "catalog_loading": true, "catalog_unavailable": true,
	"empty_source": true, "too_many_items": true, "operation_in_progress": true,
	"native_error": true,
}

type localMediaRequest struct {
	Runtime      string
	Op           string
	Source       string
	Path         string
	IncludePaths bool
	Progressive  bool
}

func localMediaSelector(raw json.RawMessage) bool {
	var value string
	if !offlineNonemptyText(raw, 4096) || json.Unmarshal(raw, &value) != nil {
		return false
	}
	if localMediaAbsolutePattern.MatchString(value) {
		return true
	}
	return len([]byte(value)) <= 512 && !strings.ContainsAny(value, `/\\`) && value != "." && value != ".."
}

func parseLocalMediaRequest(params map[string]json.RawMessage) *localMediaRequest {
	v := &localMediaRequest{}
	if json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) || json.Unmarshal(params["op"], &v.Op) != nil {
		return nil
	}
	if v.Op == "sources" {
		count := len(params)
		if raw, exists := params["path"]; exists {
			if !offlineNonemptyText(raw, 4096) || json.Unmarshal(raw, &v.Path) != nil || !localMediaAbsolutePattern.MatchString(v.Path) {
				return nil
			}
			count--
		}
		if raw, exists := params["include_paths"]; exists {
			var include *bool
			if json.Unmarshal(raw, &include) != nil || include == nil {
				return nil
			}
			v.IncludePaths = *include
			count--
		}
		if count == 2 {
			return v
		}
		return nil
	}
	count := len(params)
	if raw, exists := params["progressive"]; exists {
		if (v.Op != "play" && v.Op != "shuffle") || string(raw) != "true" {
			return nil
		}
		v.Progressive = true
		count--
	}
	if (v.Op != "play" && v.Op != "shuffle" && v.Op != "connect") || count != 3 || !localMediaSelector(params["source"]) || json.Unmarshal(params["source"], &v.Source) != nil {
		return nil
	}
	return v
}

func validLocalMediaSource(raw json.RawMessage, includePaths bool) (string, bool) {
	m, err := decodeObject(raw)
	var id string
	count := len(m)
	if path, exists := m["path"]; exists {
		var value string
		if !includePaths || !offlineNonemptyText(path, 4096) || json.Unmarshal(path, &value) != nil || !localMediaAbsolutePattern.MatchString(value) {
			return "", false
		}
		count--
	}
	if err != nil || count != 4 || json.Unmarshal(m["id"], &id) != nil || !localMediaIDPattern.MatchString(id) || !localMediaName(m["name"]) || !offlineEnum(m["kind"], "folder", "file") || (string(m["available"]) != "true" && string(m["available"]) != "false") {
		return "", false
	}
	return id, true
}

func localMediaName(raw json.RawMessage) bool {
	var value string
	return offlineNonemptyText(raw, 512) && json.Unmarshal(raw, &value) == nil &&
		!strings.ContainsAny(value, `/\`) && value != "." && value != ".." &&
		!strings.ContainsFunc(value, func(character rune) bool { return character >= 127 && character <= 159 })
}

func validLocalMediaResult(raw json.RawMessage, status string, request *localMediaRequest) bool {
	if len(raw) > maxLocalMediaResultBytes {
		return false
	}
	m, err := decodeObject(raw)
	var runtime, op, state string
	if err != nil || !profileInteger(m["version"], 1, 1) || json.Unmarshal(m["runtime"], &runtime) != nil || runtime != request.Runtime || json.Unmarshal(m["op"], &op) != nil || op != request.Op || json.Unmarshal(m["state"], &state) != nil {
		return false
	}
	if state == "error" {
		var problem string
		return status != "ok" && len(m) == 5 && json.Unmarshal(m["error"], &problem) == nil && localMediaErrors[problem]
	}
	if state != "ready" || status != "ok" {
		return false
	}
	if op == "sources" {
		var rows []json.RawMessage
		if len(m) != 5 || len(m["sources"]) == 0 || m["sources"][0] != '[' || json.Unmarshal(m["sources"], &rows) != nil || len(rows) > maxLocalMediaSources {
			return false
		}
		seen := map[string]bool{}
		for _, row := range rows {
			id, valid := validLocalMediaSource(row, request.IncludePaths)
			if !valid || seen[id] {
				return false
			}
			seen[id] = true
		}
		return true
	}
	_, valid := validLocalMediaSource(m["source"], false)
	sourceFields, _ := decodeObject(m["source"])
	var sourceID, sourceName string
	_ = json.Unmarshal(sourceFields["id"], &sourceID)
	_ = json.Unmarshal(sourceFields["name"], &sourceName)
	if !localMediaAbsolutePattern.MatchString(request.Source) && request.Source != sourceID && request.Source != sourceName {
		return false
	}
	minimum := 1
	if op == "connect" {
		minimum = 0
	}
	wantShuffle := "false"
	if op == "shuffle" {
		wantShuffle = "true"
	}
	count := len(m)
	if scanning, exists := m["scanning"]; exists {
		if !request.Progressive || (op != "play" && op != "shuffle") || string(scanning) != "true" {
			return false
		}
		count--
	}
	return count == 9 && valid && string(sourceFields["available"]) == "true" && profileInteger(m["count"], minimum, maxLocalMediaItems) && string(m["shuffled"]) == wantShuffle && string(m["accepted"]) == "true" && string(m["playback_confirmed"]) == "false"
}

func validLocalMediaProgressiveCapability(raw json.RawMessage) bool {
	m, err := decodeObject(raw)
	return err == nil && len(m) == 1 && profileInteger(m["version"], 1, 1)
}

func validLocalMediaCapability(raw json.RawMessage) bool {
	m, err := decodeObject(raw)
	var operations []string
	if err != nil || len(m) != 5 || !profileInteger(m["version"], 1, 1) || !profileInteger(m["max_sources"], 1, maxLocalMediaSources) || !profileInteger(m["max_items"], 1, maxLocalMediaItems) || (string(m["absolute_paths"]) != "true" && string(m["absolute_paths"]) != "false") || len(m["operations"]) == 0 || m["operations"][0] != '[' || json.Unmarshal(m["operations"], &operations) != nil || len(operations) > 4 {
		return false
	}
	seen := map[string]bool{}
	for _, op := range operations {
		if (op != "sources" && op != "play" && op != "shuffle" && op != "connect") || seen[op] {
			return false
		}
		seen[op] = true
	}
	return true
}
