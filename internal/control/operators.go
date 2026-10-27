package control

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type commandOperator struct {
	id               string
	hash             [32]byte
	devices, actions map[string]bool
	rate             bucket
}
type operatorContextKey struct{}

func operatorFor(r *http.Request) *commandOperator {
	o, _ := r.Context().Value(operatorContextKey{}).(*commandOperator)
	return o
}
func requestOwner(r *http.Request) string {
	if o := operatorFor(r); o != nil {
		return "operator:" + o.id
	}
	return "admin"
}
func (s *Server) commandOperator(r *http.Request) *commandOperator {
	values := r.Header.Values("Authorization")
	if len(values) != 1 || !strings.HasPrefix(values[0], "Bearer ") {
		return nil
	}
	value := strings.TrimPrefix(values[0], "Bearer ")
	if len(value) < 32 || len(value) > 256 || strings.ContainsAny(value, " ,\t\r\n") {
		return nil
	}
	hash := sha256.Sum256([]byte(value))
	var found *commandOperator
	for _, o := range s.operators {
		if subtle.ConstantTimeCompare(hash[:], o.hash[:]) == 1 {
			found = o
		}
	}
	return found
}
func (s *Server) configureOperators(rows []config.CommandOperator) {
	for _, row := range rows {
		o := &commandOperator{id: row.ID, devices: map[string]bool{}, actions: map[string]bool{}}
		b, _ := hex.DecodeString(row.CredentialSHA256)
		copy(o.hash[:], b)
		for _, id := range row.DeviceIDs {
			o.devices[id] = true
		}
		for _, action := range row.Actions {
			o.actions[action] = true
		}
		s.operators = append(s.operators, o)
	}
}
func commandAction(action string, p map[string]json.RawMessage) string {
	field := ""
	switch action {
	case "command":
		field = "command"
	case "input":
		field = "key"
	case "restart":
		field = "target"
	case "kiosk":
		field = "mode"
	case "aspect", "playback", "lifecycle", "vportal_queue", "maintenance", "app_update", "notifications":
		field = "operation"
	case "local_media", "plex_queue", "plex_library", "favorites", "categories", "player_settings", "plex_folders", "player_settings_patch", "perf_capture":
		field = "op"
	}
	if field != "" {
		var op string
		_ = json.Unmarshal(p[field], &op)
		action += "." + op
	}
	return action
}
func allowedCommand(r *http.Request, d *device, action string) bool {
	o := operatorFor(r)
	return o == nil || (d != nil && o.devices[d.id] && o.actions[action])
}
func ownResult(r *http.Request, owner, action string) bool {
	o := operatorFor(r)
	return o == nil || (owner == requestOwner(r) && o.actions[action])
}
func withOperator(r *http.Request, o *commandOperator) *http.Request {
	return r.WithContext(context.WithValue(r.Context(), operatorContextKey{}, o))
}
