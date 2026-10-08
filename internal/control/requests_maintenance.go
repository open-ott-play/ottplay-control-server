package control

import (
	"encoding/hex"
	"encoding/json"
	"net/url"
)

// Maintenance is delivered to a separately provisioned native device queue.
// A web player and a native supervisor must never share a protocol-1 token.
func validMaintenanceRequest(p map[string]json.RawMessage) bool {
	var op string
	if json.Unmarshal(p["operation"], &op) != nil {
		return false
	}
	switch op {
	case "health", "logs", "recover_video":
		return len(p) == 1
	case "update":
		var address, digest string
		if len(p) != 3 || json.Unmarshal(p["manifest"], &address) != nil || json.Unmarshal(p["sha256"], &digest) != nil || len(address) > 2048 {
			return false
		}
		u, err := url.Parse(address)
		b, decodeErr := hex.DecodeString(digest)
		return err == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.Fragment == "" && decodeErr == nil && len(b) == 32
	}
	return false
}

func validVPortalQueueRequest(p map[string]json.RawMessage) bool {
	var op string
	if json.Unmarshal(p["operation"], &op) != nil {
		return false
	}
	switch op {
	case "status", "next", "previous", "restart", "stop":
		return len(p) == 1
	case "play":
		var ids []int64
		var loop *bool
		if len(p) != 3 || json.Unmarshal(p["ids"], &ids) != nil || len(ids) < 1 || len(ids) > 100 || json.Unmarshal(p["loop"], &loop) != nil || loop == nil {
			return false
		}
		seen := map[int64]bool{}
		for _, id := range ids {
			if id < 1 || id > 9007199254740991 || seen[id] {
				return false
			}
			seen[id] = true
		}
		return true
	}
	return false
}
