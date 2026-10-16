package control

import "encoding/json"

type handoffRequest struct {
	Kind   string
	Import bool
}

func validHandoff(raw json.RawMessage) (string, bool) {
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || !inspectEnum(m["provider"], "m3u", "xtream", "stalker") ||
		!favoriteText(m["source_id"], 256) || !favoriteText(m["item_id"], 512) {
		return "", false
	}
	var kind string
	if json.Unmarshal(m["kind"], &kind) != nil {
		return "", false
	}
	switch kind {
	case "live":
		return kind, len(m) == 5
	case "archive":
		return kind, len(m) == 6 && numberValue(m["archive_start"], 1, 9007199254740991, true)
	}
	return "", false
}
func parseHandoffRequest(action string, p map[string]json.RawMessage) *handoffRequest {
	if action == "playback_handoff_export" && len(p) == 0 {
		return &handoffRequest{}
	}
	if action == "playback_handoff_import" && len(p) == 1 {
		kind, ok := validHandoff(p["handoff"])
		if ok {
			return &handoffRequest{Kind: kind, Import: true}
		}
	}
	return nil
}
func validHandoffResult(raw json.RawMessage, status string, r *handoffRequest) bool {
	m, err := decodeObject(raw)
	if err != nil || len(raw) > 4096 {
		return false
	}
	if status != "ok" {
		// Do not persist arbitrary strings that can contain a provider credential.
		return len(m) == 1 && inspectEnum(m["error"], "unsupported", "invalid_request", "no_current_item", "source_mismatch", "item_unavailable", "ambiguous_item", "policy_denied", "busy", "playback_failed", "context_changed")
	}
	if r.Import {
		return len(m) == 2 && string(m["dispatched"]) == "true" && inspectEnum(m["kind"], r.Kind)
	}
	_, ok := validHandoff(m["handoff"])
	return len(m) == 1 && ok
}
