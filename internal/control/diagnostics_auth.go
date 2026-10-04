package control

import (
	"net/http"
	"strings"
)

func diagnosticRuntimeRoute(path string) bool {
	switch path {
	case diagnosticsPrefix + "/poll", diagnosticsPrefix + "/results", diagnosticsPrefix + "/events", diagnosticsPrefix + "/repairs/poll", diagnosticsPrefix + "/repairs/results":
		return true
	}
	return false
}

// Called with the diagnostics mutex held, before body reads or admission slots.
// Body-dependent target checks and live credential checks run again in handle.
func (d *diagnosticsState) preauthenticate(s *Server, r *http.Request, path string) (int, string) {
	hash, ok := diagBearer(r)
	if !ok {
		return 401, "invalid_credentials"
	}
	admin, dev := s.authenticate(r)
	rt := d.runtime(hash)
	op := d.operator(hash)
	if path == diagnosticsPrefix+"/runtimes" && r.Method == "POST" {
		if dev == nil || admin {
			if admin || op != nil || rt != nil {
				return 403, "credential_role_denied"
			}
			return 401, "invalid_credentials"
		}
		if !d.enabled[dev.id] {
			return 403, "diagnostics_disabled"
		}
		return 0, ""
	}
	if diagnosticRuntimeRoute(path) {
		if rt == nil {
			if admin || dev != nil || op != nil {
				return 403, "credential_role_denied"
			}
			return 401, "invalid_credentials"
		}
		if strings.HasPrefix(path, diagnosticsPrefix+"/repairs/") && !repairCapable(rt) {
			return 403, "capability_required"
		}
		return 0, ""
	}
	if op == nil {
		if admin || dev != nil || rt != nil {
			return 403, "credential_role_denied"
		}
		return 401, "invalid_credentials"
	}
	action := ""
	device := ""
	switch path {
	case diagnosticsPrefix + "/runtimes":
		action = "runtimes.read"
	case diagnosticsPrefix + "/sessions":
		action = "sessions.start"
	case diagnosticsPrefix + "/repairs":
		action = "repairs.start"
	default:
		parts := strings.Split(strings.TrimPrefix(path, diagnosticsPrefix+"/"), "/")
		switch parts[0] {
		case "runtimes":
			action = "runtimes.revoke"
			target := d.runtimes[parts[1]]
			if target == nil {
				return 404, "not_found"
			}
			device = target.device
		case "sessions":
			action = "sessions.read"
			if r.Method == "POST" {
				action = "sessions.stop"
			}
			target := d.sessions[parts[1]]
			if target == nil {
				return 404, "not_found"
			}
			device = target.device
		case "repairs":
			action = "repairs.read"
			target := d.repairs[parts[1]]
			if target == nil {
				return 404, "not_found"
			}
			device = target.device
		}
	}
	if !op.actions[action] || (device != "" && !op.devices[device]) {
		return 404, "not_found"
	}
	if device != "" && !d.enabled[device] {
		return 403, "diagnostics_disabled"
	}
	return 0, ""
}
