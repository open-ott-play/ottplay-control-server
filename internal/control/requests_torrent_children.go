package control

import (
	"encoding/json"
	"regexp"
	"strings"
)

var torrentChildID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`)

func validTorrentChildID(raw json.RawMessage) bool {
	id, ok := inspectString(raw, 128)
	return ok && torrentChildID.MatchString(id) && !strings.EqualFold(id, "all")
}

func validTorrentChildrenCapability(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "version", "operations", "scopedOperations")
	if !ok || !profileInteger(m["version"], 1, 1) {
		return false
	}
	ops, ok := inspectArray(m["operations"], 1)
	if !ok || len(ops) != 1 || !inspectEnum(ops[0], "indexers") {
		return false
	}
	scoped, ok := inspectArray(m["scopedOperations"], 2)
	return ok && len(scoped) == 2 &&
		((inspectEnum(scoped[0], "caps") && inspectEnum(scoped[1], "search")) ||
			(inspectEnum(scoped[0], "search") && inspectEnum(scoped[1], "caps")))
}

func validTorrentChildList(m map[string]json.RawMessage) bool {
	if !inspectBool(m["supported"], false) {
		return false
	}
	rows, ok := inspectArray(m["indexers"], 256)
	if !ok || (string(m["supported"]) == "false" && len(rows) != 0) {
		return false
	}
	seen := map[string]bool{}
	for _, raw := range rows {
		row, ok := inspectObject(raw, "id", "label", "language", "type")
		id := favoritesGlobalString(row["id"])
		if !ok || !validTorrentChildID(row["id"]) || seen[id] || !torrentIndexerText(row["label"], 128, false) {
			return false
		}
		for _, key := range []string{"language", "type"} {
			if !inspectNull(row[key]) && !torrentIndexerText(row[key], 32, false) {
				return false
			}
		}
		seen[id] = true
	}
	return true
}
