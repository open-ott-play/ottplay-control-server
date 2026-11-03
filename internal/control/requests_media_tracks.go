package control

import (
	"encoding/json"
	"regexp"
	"strings"
	"unicode"
)

const maxMediaTracksRequestBytes = 2048
const maxMediaTracksResultBytes = 65536
const maxMediaTracks = 128
const maxMediaTracksPerKind = 64

var mediaTracksIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
var mediaTracksLanguagePattern = regexp.MustCompile(`^[A-Za-z0-9-]{1,32}$`)
var mediaTracksCodecPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,32}$`)
var mediaTracksPrivateLabelPattern = regexp.MustCompile(`(?i)([a-z][a-z0-9+.-]*://|\b(https?|magnet|data|file):|(^|[\s"'(])(/\S|~/|[a-z]:[\\/]|\\\\)|\b(token|api[_-]?key|authorization|password|secret)\s*[=:])`)

// Only request identities are retained. The player owns the ephemeral track map,
// lease and manual/remote intent epoch; the relay never resolves or replays IDs.
type mediaTracksRequest struct {
	Runtime, Op, Playback, SourceGeneration, Catalog string
	Target                                           mediaTracksTarget
}

type mediaTracksTarget struct {
	Kind, Mode, ID string
}

func validMediaTracksCapability(raw json.RawMessage) bool {
	m, ok := inspectObject(raw, "version")
	return ok && inspectNumber(m["version"], 1, 1, true, false)
}

func mediaTracksString(raw json.RawMessage, pattern *regexp.Regexp) (string, bool) {
	value, ok := inspectString(raw, 64)
	return value, ok && pattern.MatchString(value)
}

func parseMediaTracksTarget(raw json.RawMessage) (mediaTracksTarget, bool) {
	m, err := decodeObject(raw)
	target := mediaTracksTarget{Kind: settingString(m["kind"]), Mode: settingString(m["mode"])}
	if err != nil || (target.Kind != "audio" && target.Kind != "subtitle") {
		return target, false
	}
	if target.Mode == "off" {
		return target, target.Kind == "subtitle" && favoritesTransferFields(m, "kind", "mode")
	}
	var ok bool
	target.ID, ok = mediaTracksString(m["id"], mediaTracksIDPattern)
	return target, ok && target.Mode == "track" && favoritesTransferFields(m, "kind", "mode", "id")
}

func parseMediaTracksRequest(raw json.RawMessage) *mediaTracksRequest {
	if len(raw) > maxMediaTracksRequestBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	runtime, ok := mediaTracksString(m["runtime"], screenshotRuntimePattern)
	if err != nil || !ok {
		return nil
	}
	q := &mediaTracksRequest{Runtime: runtime, Op: settingString(m["op"])}
	switch q.Op {
	case "list":
		if favoritesTransferFields(m, "op", "runtime") {
			return q
		}
	case "select":
		if !favoritesTransferFields(m, "op", "runtime", "playback", "source_generation", "catalog", "target") {
			return nil
		}
		q.Playback, ok = mediaTracksString(m["playback"], mediaTracksIDPattern)
		if !ok {
			return nil
		}
		q.SourceGeneration, ok = mediaTracksString(m["source_generation"], mediaTracksIDPattern)
		if !ok {
			return nil
		}
		q.Catalog, ok = mediaTracksString(m["catalog"], mediaTracksIDPattern)
		if !ok {
			return nil
		}
		q.Target, ok = parseMediaTracksTarget(m["target"])
		if ok {
			return q
		}
	}
	return nil
}

func validMediaTracksLabel(raw json.RawMessage) bool {
	value, ok := inspectString(raw, 128)
	if !ok || strings.TrimSpace(value) == "" || mediaTracksPrivateLabelPattern.MatchString(value) {
		return false
	}
	for _, r := range value {
		if unicode.IsControl(r) || unicode.In(r, unicode.Cf) {
			return false
		}
	}
	return true
}

func validMediaTracksObservation(raw json.RawMessage, kind string, tracks map[string]string, target *mediaTracksTarget) bool {
	m, ok := inspectObject(raw, "state", "track_id", "age_ms")
	if !ok {
		return false
	}
	switch settingString(m["state"]) {
	case "not_observed":
		return target == nil && inspectNull(m["track_id"]) && inspectNull(m["age_ms"])
	case "off":
		return kind == "subtitle" && inspectNull(m["track_id"]) &&
			inspectNumber(m["age_ms"], 0, inspectMaxInteger, true, false) &&
			(target == nil || target.Mode == "off")
	case "track":
		id, valid := mediaTracksString(m["track_id"], mediaTracksIDPattern)
		if !valid || !inspectNumber(m["age_ms"], 0, inspectMaxInteger, true, false) {
			return false
		}
		if target != nil {
			return target.Kind == kind && target.Mode == "track" && target.ID == id
		}
		return tracks[id] == kind
	}
	return false
}

func validMediaTracksList(m map[string]json.RawMessage) bool {
	for _, key := range []string{"playback", "source_generation", "catalog"} {
		if _, ok := mediaTracksString(m[key], mediaTracksIDPattern); !ok {
			return false
		}
	}
	if !inspectBool(m["subtitle_off_supported"], false) {
		return false
	}
	rows, ok := inspectArray(m["tracks"], maxMediaTracks)
	if !ok {
		return false
	}
	tracks := make(map[string]string, len(rows))
	counts := map[string]int{}
	for _, raw := range rows {
		row, valid := inspectObject(raw, "id", "kind", "label", "language", "codec", "selectable")
		id, validID := mediaTracksString(row["id"], mediaTracksIDPattern)
		kind := settingString(row["kind"])
		if !valid || !validID || tracks[id] != "" || (kind != "audio" && kind != "subtitle") ||
			!validMediaTracksLabel(row["label"]) || !inspectBool(row["selectable"], false) {
			return false
		}
		for key, pattern := range map[string]*regexp.Regexp{"language": mediaTracksLanguagePattern, "codec": mediaTracksCodecPattern} {
			if !inspectNull(row[key]) {
				if _, valid := mediaTracksString(row[key], pattern); !valid {
					return false
				}
			}
		}
		tracks[id] = kind
		counts[kind]++
		if counts[kind] > maxMediaTracksPerKind {
			return false
		}
	}
	observed, ok := inspectObject(m["observed"], "audio", "subtitle")
	return ok && validMediaTracksObservation(observed["audio"], "audio", tracks, nil) &&
		validMediaTracksObservation(observed["subtitle"], "subtitle", tracks, nil)
}

func validMediaTracksResult(raw json.RawMessage, status string, expected *mediaTracksRequest) bool {
	if expected == nil || len(raw) > maxMediaTracksResultBytes || !validFavoritesTransferJSON(raw) ||
		(status != "ok" && status != "rejected" && status != "unsupported") {
		return false
	}
	keys := []string{"version", "runtime", "op", "scope", "playback", "source_generation", "catalog"}
	if status != "ok" {
		keys = []string{"version", "runtime", "op", "state", "error"}
		if expected.Op == "select" {
			keys = append(keys, "playback", "source_generation", "catalog", "target")
		}
	} else if expected.Op == "list" {
		keys = append(keys, "tracks", "subtitle_off_supported", "observed")
	} else if expected.Op == "select" {
		keys = append(keys, "target", "accepted", "outcome", "observed")
	} else {
		return false
	}
	m, ok := inspectObject(raw, keys...)
	if !ok || !inspectNumber(m["version"], 1, 1, true, false) ||
		!inspectEnum(m["runtime"], expected.Runtime) || !screenshotRuntimePattern.MatchString(expected.Runtime) ||
		!inspectEnum(m["op"], expected.Op) || (expected.Op != "list" && expected.Op != "select") {
		return false
	}
	if expected.Op == "select" {
		for key, value := range map[string]string{"playback": expected.Playback, "source_generation": expected.SourceGeneration, "catalog": expected.Catalog} {
			if !mediaTracksIDPattern.MatchString(value) || !inspectEnum(m[key], value) {
				return false
			}
		}
		target, valid := parseMediaTracksTarget(m["target"])
		if !valid || target != expected.Target {
			return false
		}
	}
	if status != "ok" {
		if !inspectEnum(m["state"], "error") {
			return false
		}
		if status == "unsupported" {
			return inspectEnum(m["error"], "unsupported")
		}
		return inspectEnum(m["error"], "invalid_request", "runtime_mismatch", "no_playback", "playback_changed",
			"catalog_changed", "catalog_incomplete", "track_not_found", "not_selectable", "restricted", "busy", "unavailable")
	}
	if !inspectEnum(m["scope"], "current_playback") {
		return false
	}
	if expected.Op == "list" {
		return validMediaTracksList(m)
	}
	if !inspectEnum(m["outcome"], "requested", "confirmed") || string(m["accepted"]) != "true" {
		return false
	}
	if inspectEnum(m["outcome"], "requested") {
		return inspectNull(m["observed"])
	}
	// This checks the producer's evidence shape and exact target. The producer
	// alone can prove post-admission freshness and native intent/source fencing.
	return validMediaTracksObservation(m["observed"], expected.Target.Kind, nil, &expected.Target)
}
