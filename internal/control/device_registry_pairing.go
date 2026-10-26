package control

import (
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strings"
)

func (s *Server) approveRegistryPairing(w http.ResponseWriter, req *http.Request) {
	body, ok := readBody(w, req)
	if !ok {
		return
	}
	var v struct {
		ID          string `json:"id"`
		Code        string `json:"code"`
		OperationID string `json:"operation_id"`
		Diagnostics *bool  `json:"diagnostics_enabled"`
	}
	if !diagDecode(body, &v, []string{"id", "code"}, "operation_id", "diagnostics_enabled") || !commandID.MatchString(v.ID) || len(v.Code) != 8 || (v.OperationID != "" && !commandID.MatchString(v.OperationID)) {
		failure(w, 400, "invalid pairing approval")
		return
	}
	if v.OperationID == "" {
		v.OperationID = v.ID
	}
	r := s.registry
	r.mu.Lock()
	if r.failed {
		r.mu.Unlock()
		failure(w, 503, "device registry requires recovery")
		return
	}
	var selected registryClaim
	for _, claim := range r.disk.Claims {
		if claim.ID == v.ID && claim.ExpiresAt > s.now().Unix() {
			selected = claim
			break
		}
	}
	s.mu.Lock()
	s.expirePairings(s.now())
	p := s.pairings[v.ID]
	if selected.ID == "" && p != nil {
		if subtle.ConstantTimeCompare([]byte(p.code), []byte(v.Code)) != 1 {
			p.attempts++
			if p.attempts >= 5 {
				delete(s.pairings, v.ID)
			}
			s.mu.Unlock()
			r.mu.Unlock()
			failure(w, 403, "pairing code does not match")
			return
		}
		selected = registryClaim{ID: p.id, OperationID: v.OperationID, SecretHash: hex.EncodeToString(p.secret[:]), CodeHash: registryDigest(p.code), DeviceID: p.device, Server: p.server, Address: p.address, ExpiresAt: p.expires.Unix()}
	}
	s.mu.Unlock()
	r.mu.Unlock()
	if selected.ID == "" {
		failure(w, 404, "pairing expired or not found")
		return
	}
	if subtle.ConstantTimeCompare([]byte(selected.CodeHash), []byte(registryDigest(v.Code))) != 1 || selected.OperationID != v.OperationID {
		failure(w, 409, "pairing approval identity changed")
		return
	}
	current, err := s.currentPairingServer(req, selected.Server, selected.Address)
	if err != nil {
		failure(w, 503, "DNS discovery unavailable")
		return
	}
	if !current {
		failure(w, 409, "discovered controller changed; start pairing again")
		return
	}
	r.mu.Lock()
	status, result := s.finishRegistryApproval(req, selected, p, v.Diagnostics)
	r.mu.Unlock()
	reply(w, status, result)
}

func (s *Server) finishRegistryApproval(req *http.Request, selected registryClaim, p *pairing, diagnostics *bool) (int, any) {
	r := s.registry
	if r.failed {
		return registryFailure(503, "device registry requires recovery")
	}
	if selected.ExpiresAt <= s.now().Unix() || req.Context().Err() != nil {
		return registryFailure(409, "pairing expired or cancelled")
	}
	canonical, _ := json.Marshal(struct {
		ID, CodeHash, Address string
		Diagnostics           *bool
	}{selected.ID, selected.CodeHash, selected.Address, diagnostics})
	digest := registryDigest("approve:" + string(canonical))
	for _, op := range r.disk.Operations {
		if op.ID == selected.OperationID && op.RequestHash != digest {
			return registryFailure(409, "operation id was used for a different request")
		}
	}
	for _, claim := range r.disk.Claims {
		if claim.ID == selected.ID {
			row := s.registryView.Load().identities[claim.DeviceID]
			if claim.OperationID != selected.OperationID || claim.CodeHash != selected.CodeHash || row.State != "active" || row.Generation != claim.Generation {
				return registryFailure(409, "pairing identity changed")
			}
			return 200, map[string]string{"status": "approved"}
		}
	}
	s.mu.Lock()
	stillPending := p != nil && s.pairings[selected.ID] == p && s.now().Before(p.expires)
	s.mu.Unlock()
	if !stillPending {
		return registryFailure(404, "pairing expired or cancelled")
	}
	disk := cloneRegistry(r.disk, s.now())
	if len(disk.Claims) >= maxPairings || len(disk.Operations) >= maxRegistryOrdinaryOperations {
		return registryFailure(409, "registry approval capacity reached")
	}
	for _, op := range disk.Operations {
		if op.ID == selected.OperationID {
			return registryFailure(409, "operation id already used")
		}
	}
	row, exists := s.registryView.Load().identities[selected.DeviceID]
	if exists {
		if row.State != "active" || (diagnostics != nil && *diagnostics != row.Diagnostics) {
			return registryFailure(409, "device requires an explicit lifecycle operation")
		}
	} else {
		if len(disk.Records) >= maxRegistryRecords {
			return registryFailure(409, "registry record capacity reached")
		}
		enabled := diagnostics != nil && *diagnostics
		if enabled && r.config.Diagnostics == nil {
			return registryFailure(400, "diagnostics configuration is required")
		}
		generation, err := registryRandom(16, false)
		if err != nil {
			return registryFailure(503, "cannot create device generation")
		}
		token, err := r.freshToken(disk)
		if err != nil {
			return registryFailure(503, "cannot create device credential")
		}
		record := registryRecord{DeviceID: selected.DeviceID, Generation: generation, Origin: "dynamic", State: "active", Token: token, Diagnostics: enabled, RevokedHashes: []string{}}
		disk.Records = append(disk.Records, record)
		row = registryIdentity{DeviceID: record.DeviceID, Generation: generation, State: "active"}
	}
	selected.Generation = row.Generation
	disk.Claims = append(disk.Claims, selected)
	now := s.now().Unix()
	receipt := registryReceipt{Version: 1, OperationID: selected.OperationID, DeviceID: selected.DeviceID, Generation: row.Generation, State: "active", Persisted: true}
	disk.Operations = append(disk.Operations, registryOperation{ID: selected.OperationID, RequestHash: digest, Kind: "approve", CreatedAt: now, ExpiresAt: now + 86400, Result: receipt})
	if _, err := registryIdentities(r.config, disk); err != nil {
		return registryFailure(409, "registry capacity or identity conflict")
	}
	if err := s.commitRegistry(disk); err != nil {
		return registryCommitFailure(err, "approval persistence is uncertain; reconcile before retrying")
	}
	s.mu.Lock()
	if s.pairings[selected.ID] == p {
		p.approved = true
	}
	s.mu.Unlock()
	return 200, map[string]string{"status": "approved"}
}

func (s *Server) claimRegistryPairing(w http.ResponseWriter, req *http.Request, id string, cancel bool) {
	values := req.Header.Values("Authorization")
	if !commandID.MatchString(id) || len(values) != 1 || !strings.HasPrefix(values[0], "Bearer ") || len(strings.TrimPrefix(values[0], "Bearer ")) != 43 {
		failure(w, 401, "valid pairing claim required")
		return
	}
	hash := registryDigest(strings.TrimPrefix(values[0], "Bearer "))
	r := s.registry
	r.mu.Lock()
	if r.failed {
		r.mu.Unlock()
		failure(w, 503, "device registry requires recovery")
		return
	}
	var selected registryClaim
	for _, claim := range r.disk.Claims {
		if claim.ID == id && claim.ExpiresAt > s.now().Unix() && subtle.ConstantTimeCompare([]byte(hash), []byte(claim.SecretHash)) == 1 {
			selected = claim
			break
		}
	}
	s.mu.Lock()
	s.expirePairings(s.now())
	p := s.pairings[id]
	pending := p != nil && subtle.ConstantTimeCompare([]byte(hash), []byte(hex.EncodeToString(p.secret[:]))) == 1
	s.mu.Unlock()
	if selected.ID == "" && !pending {
		r.mu.Unlock()
		failure(w, 401, "valid pairing claim required")
		return
	}
	if cancel {
		if selected.ID != "" {
			disk := cloneRegistry(r.disk, s.now())
			kept := disk.Claims[:0]
			for _, claim := range disk.Claims {
				if claim.ID != id {
					kept = append(kept, claim)
				}
			}
			disk.Claims = kept
			if err := s.commitRegistry(disk); err != nil {
				r.mu.Unlock()
				status, result := registryCommitFailure(err, "claim cancellation persistence is uncertain")
				reply(w, status, result)
				return
			}
		}
		s.mu.Lock()
		delete(s.pairings, id)
		s.mu.Unlock()
		r.mu.Unlock()
		reply(w, 200, map[string]string{"status": "cancelled"})
		return
	}
	r.mu.Unlock()
	if selected.ID == "" {
		reply(w, 202, map[string]string{"status": "pending"})
		return
	}
	current, err := s.currentPairingServer(req, selected.Server, selected.Address)
	if err != nil {
		failure(w, 503, "DNS discovery unavailable")
		return
	}
	if !current {
		failure(w, 409, "discovered controller changed; start pairing again")
		return
	}
	r.mu.Lock()
	valid := false
	for _, claim := range r.disk.Claims {
		if claim.ID == selected.ID && claim.Generation == selected.Generation && claim.ExpiresAt > s.now().Unix() && claim.SecretHash == selected.SecretHash {
			valid = true
			break
		}
	}
	row := s.registryView.Load().identities[selected.DeviceID]
	if r.failed || !valid || row.State != "active" || row.Generation != selected.Generation {
		r.mu.Unlock()
		failure(w, 401, "valid pairing claim required")
		return
	}
	result := map[string]string{"status": "approved", "device_id": selected.DeviceID, "address": selected.Address, "token": row.Token}
	r.mu.Unlock()
	reply(w, 200, result)
}
