package control

import (
	"encoding/json"
	"regexp"
	"strings"
)

const maxCategoryPageSize = 200
const maxCategoryItems = 1000000
const maxCategoryResultBytes = 1024 * 1024

var categoryCatalogPattern = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,128}$`)
var categoryProviderPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,32}$`)

type categoryRequest struct {
	Runtime, Op, Catalog, Category string
	Offset, Limit                  int
}

func categoryText(raw json.RawMessage, maximum int) (string, bool) {
	value, ok := inspectString(raw, maximum)
	if !ok || strings.TrimSpace(value) == "" {
		return "", false
	}
	for _, character := range value {
		if character >= 0x80 && character <= 0x9f {
			return "", false
		}
	}
	return value, true
}

// Categories and paged channel queries opt into the source-bound catalog
// contract. The original channels {} / {search} requests remain unchanged.
func parseCategoryRequest(action string, params map[string]json.RawMessage) *categoryRequest {
	v := &categoryRequest{}
	if json.Unmarshal(params["runtime"], &v.Runtime) != nil || !screenshotRuntimePattern.MatchString(v.Runtime) {
		return nil
	}
	keys := []string{"runtime", "offset", "limit"}
	if action == "categories" {
		if json.Unmarshal(params["op"], &v.Op) != nil || (v.Op != "list" && v.Op != "select") {
			return nil
		}
		if v.Op == "select" {
			keys = []string{"runtime", "op", "catalog", "category"}
		} else {
			keys = append(keys, "op")
		}
	} else if action == "channels" {
		v.Op = "channels"
		keys = append(keys, "catalog", "category")
		if search, exists := params["search"]; exists {
			value, valid := inspectString(search, 1024)
			if !valid || strings.ContainsFunc(value, func(character rune) bool { return character >= 0x80 && character <= 0x9f }) {
				return nil
			}
			keys = append(keys, "search")
		}
	} else {
		return nil
	}
	if v.Op != "select" {
		if !profileInteger(params["offset"], 0, maxCategoryItems) || !profileInteger(params["limit"], 1, maxCategoryPageSize) {
			return nil
		}
		_ = json.Unmarshal(params["offset"], &v.Offset)
		_ = json.Unmarshal(params["limit"], &v.Limit)
	}
	if v.Op == "list" {
		if _, exists := params["catalog"]; exists {
			keys = append(keys, "catalog")
		} else if v.Offset != 0 {
			return nil
		}
	}
	if raw, exists := params["catalog"]; exists {
		if json.Unmarshal(raw, &v.Catalog) != nil || !categoryCatalogPattern.MatchString(v.Catalog) {
			return nil
		}
	}
	if v.Op != "list" {
		var ok bool
		v.Category, ok = categoryText(params["category"], 1024)
		if !ok || v.Catalog == "" {
			return nil
		}
	}
	if len(params) != len(keys) {
		return nil
	}
	for _, key := range keys {
		if _, exists := params[key]; !exists {
			return nil
		}
	}
	return v
}

func validCategoryResult(raw json.RawMessage, status string, expected *categoryRequest) bool {
	if expected == nil || len(raw) > maxCategoryResultBytes {
		return false
	}
	keys := []string{"version", "runtime", "op", "catalog", "provider", "selected", "offset", "total", "has_more", "loading", "categories"}
	if expected.Op == "select" {
		keys = []string{"version", "runtime", "op", "catalog", "provider", "selected", "dispatched"}
	} else if expected.Op == "channels" {
		keys = []string{"version", "runtime", "catalog", "category", "offset", "total", "has_more", "loading", "channels"}
	}
	if status != "ok" {
		if status != "rejected" && status != "unsupported" {
			return false
		}
		keys = []string{"version", "runtime", "op", "state", "error"}
	}
	m, ok := inspectObject(raw, keys...)
	if !ok || !profileInteger(m["version"], 1, 1) || !inspectEnum(m["runtime"], expected.Runtime) {
		return false
	}
	if status != "ok" {
		return inspectEnum(m["op"], expected.Op) && inspectEnum(m["state"], "error") &&
			inspectEnum(m["error"], "invalid_request", "runtime_mismatch", "catalog_changed", "category_not_found", "restricted", "unsupported", "unavailable")
	}
	var catalog string
	if json.Unmarshal(m["catalog"], &catalog) != nil || !categoryCatalogPattern.MatchString(catalog) {
		return false
	}
	// A channel query creates a category-bound playback receipt on its first
	// page. Later pages must retain that receipt; category listing and selection
	// retain the caller's existing catalog whenever one was supplied.
	if expected.Catalog != "" && (expected.Op != "channels" || expected.Offset > 0) && catalog != expected.Catalog {
		return false
	}
	if expected.Op == "channels" {
		category, valid := categoryText(m["category"], 1024)
		if !valid || category != expected.Category {
			return false
		}
	} else {
		var provider string
		selected, valid := categoryText(m["selected"], 1024)
		if !valid || !inspectEnum(m["op"], expected.Op) || json.Unmarshal(m["provider"], &provider) != nil || !categoryProviderPattern.MatchString(provider) {
			return false
		}
		if expected.Op == "select" {
			var dispatched *bool
			return selected == expected.Category && json.Unmarshal(m["dispatched"], &dispatched) == nil && dispatched != nil && *dispatched
		}
	}
	rowKey := "categories"
	if expected.Op == "channels" {
		rowKey = "channels"
	}
	rows, ok := inspectArray(m[rowKey], expected.Limit)
	var total int
	var more *bool
	if !ok || !profileInteger(m["offset"], expected.Offset, expected.Offset) ||
		!profileInteger(m["total"], expected.Offset+len(rows), maxCategoryItems) ||
		json.Unmarshal(m["total"], &total) != nil || json.Unmarshal(m["has_more"], &more) != nil || more == nil ||
		*more != (expected.Offset+len(rows) < total) || (*more && len(rows) == 0) || !inspectBool(m["loading"], false) {
		return false
	}
	seenIDs, seenNumbers := map[string]bool{}, map[int]bool{}
	for _, row := range rows {
		field, idLimit := "count", 1024
		if expected.Op == "channels" {
			field, idLimit = "number", 2048
		}
		item, valid := inspectObject(row, "id", "name", field)
		id, validID := categoryText(item["id"], idLimit)
		_, validName := categoryText(item["name"], 512)
		minimum := 0
		if expected.Op == "channels" {
			minimum = 1
		}
		if !valid || !validID || !validName || seenIDs[id] || !profileInteger(item[field], minimum, maxCategoryItems) {
			return false
		}
		seenIDs[id] = true
		if expected.Op == "channels" {
			var number int
			_ = json.Unmarshal(item[field], &number)
			if seenNumbers[number] {
				return false
			}
			seenNumbers[number] = true
		}
	}
	return true
}
