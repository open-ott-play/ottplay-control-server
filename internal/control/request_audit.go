package control

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const requestAuditPath = "/api/request-audit"
const requestAuditPerDevice = 32
const requestAuditTotal = 512
const requestAuditAgeMS = 600000
const requestAuditMaxResponseBytes = 32768

type requestAuditEntry struct {
	id, device, action, owner string
	accepted, deadline        time.Time
	result                    string
}

// Keep only ingress metadata, never request/result payloads or caller hints.
// All writes are protected by s.mu; passive reads only filter a copy.
func requestAuditFamily(action string) bool {
	family, _, _ := strings.Cut(action, ".")
	switch family {
	case "play", "play_catalog", "playback", "restart", "provider", "profile", "input", "command", "local_media", "plex_queue", "plex_library", "lifecycle", "kiosk":
		return true
	}
	return false
}
func (s *Server) appendRequestAudit(e entry, device string, now time.Time) {
	if !requestAuditFamily(e.permission) || !config.ValidCommandAction(e.permission) {
		return
	}
	kept := make([]requestAuditEntry, 0, len(s.requestAudit)+1)
	count := 0
	for i := len(s.requestAudit) - 1; i >= 0; i-- {
		row := s.requestAudit[i]
		if now.Sub(row.accepted) > requestAuditAgeMS*time.Millisecond {
			continue
		}
		if row.device == device {
			count++
			if count >= requestAuditPerDevice {
				continue
			}
		}
		kept = append(kept, row)
		if len(kept) >= requestAuditTotal-1 {
			break
		}
	}
	for i, j := 0, len(kept)-1; i < j; i, j = i+1, j-1 {
		kept[i], kept[j] = kept[j], kept[i]
	}
	s.requestAudit = append(kept, requestAuditEntry{id: e.id, device: device, action: e.permission, owner: e.owner, accepted: now, deadline: e.expires})
}
func (s *Server) finishRequestAudit(id, status string) {
	for i := range s.requestAudit {
		if s.requestAudit[i].id == id && s.requestAudit[i].result == "" {
			s.requestAudit[i].result = status
			return
		}
	}
}
func auditUnixMS(now time.Time) uint64 {
	n := now.UnixMilli()
	if n < 0 {
		return 0
	}
	return debugCount(uint64(n))
}
func (s *Server) serveRequestAudit(w http.ResponseWriter, r *http.Request, now time.Time) {
	q, ok := query(r, "device_id", "id")
	if !ok || q.Get("device_id") == "" || (q.Has("id") && !commandID.MatchString(q.Get("id"))) {
		failure(w, 400, "invalid request audit query")
		return
	}
	device := q.Get("device_id")
	exists := false
	for _, d := range s.currentDevices() {
		if d.id == device {
			exists = true
			break
		}
	}
	if !exists {
		failure(w, 404, "not found")
		return
	}
	if o := operatorFor(r); o != nil && !o.devices[device] {
		failure(w, 404, "not found")
		return
	}
	rows := make([]map[string]any, 0, requestAuditPerDevice)
	s.mu.Lock()
	for i := len(s.requestAudit) - 1; i >= 0; i-- {
		row := s.requestAudit[i]
		elapsed := now.Sub(row.accepted)
		age := elapsed.Milliseconds()
		if age < 0 {
			age = 0
		}
		if row.device != device || elapsed > requestAuditAgeMS*time.Millisecond || (q.Has("id") && q.Get("id") != row.id) || !ownResult(r, row.owner, row.action) {
			continue
		}
		role := "admin"
		var operator any
		if strings.HasPrefix(row.owner, "operator:") {
			role = "operator"
			operator = strings.TrimPrefix(row.owner, "operator:")
		}
		state := "pending"
		var result any
		if row.result != "" {
			state = "responded"
			result = row.result
		} else if !now.Before(row.deadline) {
			state = "expired"
		}
		rows = append(rows, map[string]any{"request_id": row.id, "action": row.action, "initiator": map[string]any{"credential_role": role, "operator_id": operator}, "accepted_at_ms": auditUnixMS(row.accepted), "expires_at_ms": auditUnixMS(row.deadline), "age_ms": age, "state": state, "result_status": result})
	}
	s.mu.Unlock()
	lookup := "not_requested"
	if q.Has("id") {
		lookup = "not_retained"
		if len(rows) > 0 {
			lookup = "found"
		}
	}
	document := map[string]any{"version": 1, "scope": "controller_rpc_ingress", "controller_epoch": s.playerStateEpoch, "device_id": device, "sampled_at_ms": auditUnixMS(now), "retention": map[string]int{"max_entries_per_device": requestAuditPerDevice, "max_entries_total": requestAuditTotal, "max_age_ms": requestAuditAgeMS}, "lookup": lookup, "entries": rows}
	encoded, err := json.Marshal(document)
	if err != nil || len(encoded) > requestAuditMaxResponseBytes {
		failure(w, 503, "request audit response unavailable")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(encoded)
}
