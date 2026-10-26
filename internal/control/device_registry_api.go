package control

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type registryMutation struct {
	Version            int    `json:"version"`
	OperationID        string `json:"operation_id"`
	DeviceID           string `json:"device_id"`
	ExpectedGeneration string `json:"expected_generation,omitempty"`
	Diagnostics        *bool  `json:"diagnostics_enabled,omitempty"`
	ConvertToDynamic   *bool  `json:"convert_to_dynamic,omitempty"`
	StaticFingerprint  string `json:"observed_static_fingerprint,omitempty"`
}

func (s *Server) serveDeviceRegistry(w http.ResponseWriter, req *http.Request, path string) {
	if s.registry == nil {
		failure(w, 404, "device registry is not configured")
		return
	}
	method := ""
	switch path {
	case "/api/device-registry", "/api/device-registry/operations", "/api/device-registry/credential":
		method = "GET"
	case "/api/device-registry/add", "/api/device-registry/revoke", "/api/device-registry/rotate", "/api/device-registry/reconcile":
		method = "POST"
	default:
		failure(w, 404, "not found")
		return
	}
	if !s.cors(w, req, method, false) {
		return
	}
	if req.Method != method {
		w.Header().Set("Allow", method+", OPTIONS")
		failure(w, 405, "method is not allowed")
		return
	}
	if req.URL.Path != path || req.URL.RawPath != "" {
		failure(w, 400, "invalid registry path")
		return
	}
	s.mu.Lock()
	allowed := s.globalRate.allow(s.now(), len(s.currentDevices())*120+240)
	s.mu.Unlock()
	if !allowed {
		w.Header().Set("Retry-After", "60")
		failure(w, 429, "request rate exceeded")
		return
	}
	admin, _ := s.authenticate(req)
	if !admin {
		failure(w, 403, "administrator credentials required")
		return
	}
	s.mu.Lock()
	allowed = s.adminRate.allow(s.now(), 240)
	s.mu.Unlock()
	if !allowed {
		w.Header().Set("Retry-After", "60")
		failure(w, 429, "credential rate exceeded")
		return
	}
	var body []byte
	if method == "POST" {
		var ok bool
		body, ok = readBody(w, req)
		if !ok {
			return
		}
	}
	r := s.registry
	r.mu.Lock()
	status, result := s.registryRequest(req, path, body)
	r.mu.Unlock()
	reply(w, status, result)
}

func registryFailure(status int, message string) (int, any) {
	return status, map[string]string{"error": message}
}

// registry.mu is held; response writing stays outside this lock.
func (s *Server) registryRequest(req *http.Request, path string, body []byte) (int, any) {
	r := s.registry
	if r.failed {
		return registryFailure(503, "device registry requires recovery")
	}
	rows := s.registryView.Load().identities
	if req.Method == "GET" {
		switch path {
		case "/api/device-registry":
			if _, ok := query(req); !ok {
				return registryFailure(400, "invalid query")
			}
			return 200, map[string]any{"version": 1, "store_id": r.disk.StoreID, "revision": r.disk.Revision, "devices": sortedRegistryIdentities(rows), "limits": map[string]int{"active_devices": config.MaxDevices, "inventory_devices": maxRegistryRecords + config.MaxDevices, "records": maxRegistryRecords, "operations": maxRegistryOperations, "ordinary_operations": maxRegistryOrdinaryOperations, "reserved_revocations": config.MaxDevices, "revoked_hashes": maxRegistryHashes}}
		case "/api/device-registry/operations":
			q, ok := query(req, "id")
			if !ok || !commandID.MatchString(q.Get("id")) {
				return registryFailure(400, "invalid operation id")
			}
			for _, op := range r.disk.Operations {
				if op.ID == q.Get("id") && op.ExpiresAt > s.now().Unix() {
					return 200, op.Result
				}
			}
			return registryFailure(404, "operation not found")
		case "/api/device-registry/credential":
			q, ok := query(req, "device_id", "generation")
			if !ok || !registryID.MatchString(q.Get("device_id")) || !commandID.MatchString(q.Get("generation")) {
				return registryFailure(400, "invalid credential identity")
			}
			row, found := rows[q.Get("device_id")]
			if !found || row.State != "active" || row.Generation != q.Get("generation") {
				return registryFailure(404, "active credential not found")
			}
			return 200, map[string]any{"version": 1, "device_id": row.DeviceID, "generation": row.Generation, "token": row.Token}
		}
	}
	if _, ok := query(req); !ok {
		return registryFailure(400, "invalid query")
	}
	kind := strings.TrimPrefix(path, "/api/device-registry/")
	var mutation registryMutation
	required := []string{"version", "operation_id", "device_id", "expected_generation"}
	optional := []string{}
	if kind == "add" {
		required = []string{"version", "operation_id", "device_id", "diagnostics_enabled"}
		optional = []string{"expected_generation", "convert_to_dynamic"}
	}
	if kind == "reconcile" {
		required = append(required, "observed_static_fingerprint")
	}
	if !diagDecode(body, &mutation, required, optional...) || mutation.Version != 1 || !commandID.MatchString(mutation.OperationID) || !registryID.MatchString(mutation.DeviceID) || (kind != "add" && !commandID.MatchString(mutation.ExpectedGeneration)) {
		return registryFailure(400, "invalid registry mutation")
	}
	if kind == "add" && ((mutation.ConvertToDynamic != nil && !*mutation.ConvertToDynamic) || (mutation.ConvertToDynamic != nil) != (mutation.ExpectedGeneration != "") || (mutation.ExpectedGeneration != "" && !commandID.MatchString(mutation.ExpectedGeneration))) {
		return registryFailure(400, "invalid dynamic conversion")
	}
	if kind == "reconcile" && !registryHash.MatchString(mutation.StaticFingerprint) {
		return registryFailure(400, "invalid static fingerprint")
	}
	canonical, _ := json.Marshal(mutation)
	digest := registryDigest(kind + ":" + string(canonical))
	for _, op := range r.disk.Operations {
		if op.ID == mutation.OperationID && op.ExpiresAt > s.now().Unix() {
			if op.RequestHash != digest {
				return registryFailure(409, "operation id was used for a different request")
			}
			return 200, op.Result
		}
	}
	if req.Context().Err() != nil {
		return registryFailure(408, "request was cancelled")
	}
	return s.mutateRegistry(kind, mutation, digest)
}
