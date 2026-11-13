package control

import (
	"bytes"
	"encoding/json"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

const maxMultiviewRequestBytes = 16 * 1024
const maxMultiviewResultBytes = 256 * 1024
const maxMultiviewSlots = 9

var multiviewToken = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,64}$`)
var multiviewTicket = regexp.MustCompile(`^[A-Za-z0-9_-]{1,256}$`)
var multiviewProvider = regexp.MustCompile(`^[a-z0-9][a-z0-9_.-]*(/[a-z0-9][a-z0-9_.-]*)?$`)
var multiviewOperations = []string{"status", "sources", "layout", "play", "focus", "audio", "stop", "off"}
var multiviewLayouts = []string{"single", "pip", "grid4", "grid9", "perimeter"}
var multiviewAudioPolicies = []string{"follows_focus", "muted", "slot"}
var multiviewOffModes = []string{"promote_active", "restore_previous"}
var multiviewSourceKinds = []string{"live", "vod", "file"}
var multiviewReasons = []string{"invalid_request", "unsupported", "runtime_changed", "scene_changed", "layout_changed", "focus_changed", "audio_changed", "slot_changed", "source_changed", "source_unavailable", "source_expired", "policy_restricted", "limit_exceeded", "busy", "expired", "unavailable", "receipt_unavailable"}

// multiviewRequest is the immutable admission binding retained with a queued
// request and its result. The player owns slot allocation, playback admission,
// focus/audio policy and execution-time revision checks.
type multiviewRequest struct {
	Op, Runtime string
	Binding     map[string]any
}

func multiviewPattern(raw json.RawMessage, pattern *regexp.Regexp, limit int) bool {
	s, ok := inspectString(raw, limit)
	return ok && pattern.MatchString(s)
}

func multiviewInteger(raw json.RawMessage, minimum, maximum int) bool {
	if !profileInteger(raw, minimum, maximum) {
		return false
	}
	var value int
	_ = json.Unmarshal(raw, &value)
	return string(bytes.TrimSpace(raw)) == strconv.Itoa(value)
}

func multiviewText(raw json.RawMessage, nonempty bool) bool {
	s, ok := inspectString(raw, 512)
	if !ok || (nonempty && strings.TrimSpace(s) == "") || mediaTracksPrivateLabelPattern.MatchString(s) {
		return false
	}
	for _, r := range s {
		if unicode.IsControl(r) || unicode.In(r, unicode.Cf) {
			return false
		}
	}
	return true
}

func validMultiviewContext(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "scene", "layout_revision", "focus_revision", "audio_revision")
	if !ok {
		return false
	}
	for _, value := range m {
		if !multiviewPattern(value, multiviewToken, 64) {
			return false
		}
	}
	return true
}

func multiviewObject(raw json.RawMessage) map[string]any {
	var value map[string]any
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if d.Decode(&value) != nil {
		return nil
	}
	return value
}

func parseMultiviewRequest(raw json.RawMessage) *multiviewRequest {
	if len(raw) > maxMultiviewRequestBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	q := &multiviewRequest{Op: settingString(m["op"]), Runtime: settingString(m["runtime"])}
	if err != nil || !multiviewInteger(m["version"], 1, 1) || !inspectEnum(m["op"], multiviewOperations...) {
		return nil
	}
	required := []string{"version", "op"}
	var optional []string
	if q.Op == "status" {
		optional = []string{"runtime"}
	} else {
		required = append(required, "runtime", "expected")
		if !validMultiviewContext(m["expected"]) {
			return nil
		}
	}
	if value, supplied := m["runtime"]; (supplied || q.Op != "status") && !multiviewPattern(value, multiviewToken, 64) {
		return nil
	}
	slot := q.Op == "sources" || q.Op == "play" || q.Op == "focus" || q.Op == "stop"
	switch q.Op {
	case "sources":
		optional = []string{"provider", "profile", "query", "cursor"}
		for key, value := range m {
			switch key {
			case "provider":
				if !multiviewPattern(value, multiviewProvider, 64) {
					return nil
				}
			case "profile":
				if !multiviewPattern(value, multiviewToken, 64) {
					return nil
				}
			case "query":
				if !multiviewText(value, false) {
					return nil
				}
			case "cursor":
				if !multiviewPattern(value, multiviewTicket, 256) {
					return nil
				}
			}
		}
	case "layout":
		required = append(required, "layout")
		if !inspectEnum(m["layout"], multiviewLayouts...) {
			return nil
		}
		if settingString(m["layout"]) == "perimeter" {
			required = append(required, "satellites")
			if !multiviewInteger(m["satellites"], 0, 8) {
				return nil
			}
		}
	case "play":
		required = append(required, "source_ticket")
		if !multiviewPattern(m["source_ticket"], multiviewTicket, 256) {
			return nil
		}
	case "audio":
		required = append(required, "policy")
		if !inspectEnum(m["policy"], multiviewAudioPolicies...) {
			return nil
		}
		slot = settingString(m["policy"]) == "slot"
	case "off":
		required = append(required, "mode")
		if !inspectEnum(m["mode"], multiviewOffModes...) {
			return nil
		}
	}
	if slot {
		required = append(required, "slot_id", "slot_generation", "source_revision")
		if !multiviewInteger(m["slot_id"], 0, 8) || !multiviewPattern(m["slot_generation"], multiviewToken, 64) || !multiviewPattern(m["source_revision"], multiviewToken, 64) {
			return nil
		}
	}
	if !settingKeys(m, required, optional) {
		return nil
	}
	q.Binding = multiviewObject(raw)
	delete(q.Binding, "version")
	delete(q.Binding, "runtime")
	delete(q.Binding, "op")
	return q
}

func multiviewSet(raw json.RawMessage, allowed []string) (map[string]bool, bool) {
	rows, ok := inspectArray(raw, len(allowed))
	seen := make(map[string]bool, len(rows))
	for _, row := range rows {
		if !inspectEnum(row, allowed...) || seen[settingString(row)] {
			return nil, false
		}
		seen[settingString(row)] = true
	}
	return seen, ok
}

func multiviewLayoutSlots(layout string, satellites int) int {
	switch layout {
	case "off":
		return 0
	case "single":
		return 1
	case "pip":
		return 2
	case "grid4":
		return 4
	case "grid9":
		return 9
	case "perimeter":
		return satellites + 1
	}
	return -1
}

func validMultiviewCapability(raw json.RawMessage) bool {
	if !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "version", "operations", "layouts", "max_slots", "source_kinds", "audio_policies", "off_modes")
	if !ok || !multiviewInteger(m["version"], 1, 1) || !multiviewInteger(m["max_slots"], 0, maxMultiviewSlots) {
		return false
	}
	operations, validOps := multiviewSet(m["operations"], multiviewOperations)
	layouts, validLayouts := multiviewSet(m["layouts"], multiviewLayouts)
	sources, validSources := multiviewSet(m["source_kinds"], multiviewSourceKinds)
	audio, validAudio := multiviewSet(m["audio_policies"], multiviewAudioPolicies)
	off, validOff := multiviewSet(m["off_modes"], multiviewOffModes)
	if !validOps || !operations["status"] || !validLayouts || !validSources || !validAudio || !validOff {
		return false
	}
	var capacity int
	_ = json.Unmarshal(m["max_slots"], &capacity)
	if capacity == 0 {
		return len(operations) == 1 && len(layouts)+len(sources)+len(audio)+len(off) == 0
	}
	for layout := range layouts {
		if multiviewLayoutSlots(layout, 0) > capacity {
			return false
		}
	}
	return (!operations["layout"] || len(layouts) > 0) && (!(operations["sources"] || operations["play"]) || len(sources) > 0) && (!operations["audio"] || len(audio) > 0) && (!operations["off"] || len(off) > 0)
}

func validMultiviewSource(raw json.RawMessage, ticket bool) bool {
	keys := []string{"source_id", "label", "provider", "profile", "kind"}
	if ticket {
		keys = append(keys, "ticket")
	}
	m, ok := inspectObject(raw, keys...)
	return ok && multiviewPattern(m["source_id"], multiviewToken, 64) && multiviewText(m["label"], true) &&
		multiviewPattern(m["provider"], multiviewProvider, 64) && multiviewPattern(m["profile"], multiviewToken, 64) &&
		inspectEnum(m["kind"], multiviewSourceKinds...) && (!ticket || multiviewPattern(m["ticket"], multiviewTicket, 256))
}

func validMultiviewSnapshot(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "context", "layout", "satellites", "focus_slot", "audio_policy", "audio_slot", "slots")
	if !ok || !validMultiviewContext(m["context"]) || !inspectEnum(m["layout"], "off", "single", "pip", "grid4", "grid9", "perimeter") ||
		!multiviewInteger(m["satellites"], 0, 8) || !inspectEnum(m["audio_policy"], multiviewAudioPolicies...) {
		return false
	}
	var satellites int
	_ = json.Unmarshal(m["satellites"], &satellites)
	layout := settingString(m["layout"])
	if layout != "perimeter" && satellites != 0 {
		return false
	}
	count := multiviewLayoutSlots(layout, satellites)
	slots, valid := inspectArray(m["slots"], maxMultiviewSlots)
	if !valid || len(slots) != count {
		return false
	}
	seen := map[int]bool{}
	for _, rawSlot := range slots {
		row, valid := inspectObject(rawSlot, "slot_id", "slot_generation", "source_revision", "state", "source", "error")
		if !valid || !multiviewInteger(row["slot_id"], 0, count-1) || !multiviewPattern(row["slot_generation"], multiviewToken, 64) ||
			!multiviewPattern(row["source_revision"], multiviewToken, 64) || !inspectEnum(row["state"], "empty", "starting", "playing", "paused", "stalled", "ended", "failed", "stopped") {
			return false
		}
		var id int
		_ = json.Unmarshal(row["slot_id"], &id)
		if seen[id] {
			return false
		}
		seen[id] = true
		if inspectEnum(row["state"], "starting", "playing", "paused", "stalled") && inspectNull(row["source"]) {
			return false
		}
		if !inspectNull(row["source"]) && !validMultiviewSource(row["source"], false) {
			return false
		}
		if !inspectNull(row["error"]) {
			if !inspectEnum(row["error"], multiviewReasons...) {
				return false
			}
		} else if settingString(row["state"]) == "failed" {
			return false
		}
	}
	policy := settingString(m["audio_policy"])
	if count == 0 {
		return inspectNull(m["focus_slot"]) && inspectNull(m["audio_slot"]) && policy == "muted"
	}
	if !multiviewInteger(m["focus_slot"], 0, count-1) {
		return false
	}
	if policy == "slot" && inspectNull(m["audio_slot"]) {
		return false
	}
	if !inspectNull(m["audio_slot"]) {
		if !multiviewInteger(m["audio_slot"], 0, count-1) || policy == "muted" {
			return false
		}
		if policy == "follows_focus" && !bytes.Equal(bytes.TrimSpace(m["audio_slot"]), bytes.TrimSpace(m["focus_slot"])) {
			return false
		}
	}
	return true
}

func validMultiviewResult(raw json.RawMessage, status string, expected *multiviewRequest) bool {
	if expected == nil || len(raw) > maxMultiviewResultBytes || !validFavoritesTransferJSON(raw) || (status != "ok" && status != "rejected" && status != "unsupported") {
		return false
	}
	keys := []string{"version", "runtime", "op", "binding"}
	if status != "ok" {
		keys = append(keys, "reason")
	} else if expected.Op == "status" {
		keys = append(keys, "snapshot")
	} else if expected.Op == "sources" {
		keys = append(keys, "items", "cursor", "complete")
	} else {
		keys = append(keys, "accepted", "outcome")
	}
	m, ok := inspectObject(raw, keys...)
	if !ok || !multiviewInteger(m["version"], 1, 1) || !multiviewPattern(m["runtime"], multiviewToken, 64) ||
		(expected.Runtime != "" && !inspectEnum(m["runtime"], expected.Runtime)) || !inspectEnum(m["op"], expected.Op) ||
		!reflect.DeepEqual(multiviewObject(m["binding"]), expected.Binding) {
		return false
	}
	if status != "ok" {
		if status == "unsupported" {
			return inspectEnum(m["reason"], "unsupported")
		}
		return !inspectEnum(m["reason"], "unsupported") && inspectEnum(m["reason"], multiviewReasons...)
	}
	if expected.Op == "status" {
		return validMultiviewSnapshot(m["snapshot"])
	}
	if expected.Op != "sources" {
		return string(m["accepted"]) == "true" && inspectEnum(m["outcome"], "requested", "applied")
	}
	rows, valid := inspectArray(m["items"], 128)
	if !valid || !inspectBool(m["complete"], false) {
		return false
	}
	seenIDs, seenTickets := map[string]bool{}, map[string]bool{}
	for _, row := range rows {
		if !validMultiviewSource(row, true) {
			return false
		}
		item, _ := decodeObject(row)
		id, ticket := settingString(item["source_id"]), settingString(item["ticket"])
		if seenIDs[id] || seenTickets[ticket] {
			return false
		}
		seenIDs[id], seenTickets[ticket] = true, true
	}
	if string(m["complete"]) == "true" {
		return inspectNull(m["cursor"])
	}
	return multiviewPattern(m["cursor"], multiviewTicket, 256)
}
