package control

import (
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
	"unicode"
)

// App updates use the normal Capacitor player's queue, without a root agent.
func validAppUpdateRequest(p map[string]json.RawMessage) bool {
	var op, hash, address string
	if json.Unmarshal(p["operation"], &op) != nil {
		return false
	}
	if op == "status" {
		return len(p) == 1
	}
	if json.Unmarshal(p["sha256"], &hash) != nil || !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(hash) {
		return false
	}
	if op == "install" {
		return len(p) == 2
	}
	if op != "prepare" || len(p) != 3 || json.Unmarshal(p["url"], &address) != nil || len(address) > 2048 {
		return false
	}
	if strings.ContainsAny(address, `\`) || strings.IndexFunc(address, func(r rune) bool { return unicode.IsSpace(r) || unicode.IsControl(r) }) >= 0 {
		return false
	}
	u, err := url.Parse(address)
	return err == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.Fragment == ""
}
