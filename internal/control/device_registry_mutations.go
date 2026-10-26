package control

import "github.com/open-ott-play/ottplay-control-server/internal/config"

func registryRecordIndex(d registryDisk, id string) int {
	for i, row := range d.Records {
		if row.DeviceID == id {
			return i
		}
	}
	return -1
}

func appendRevokedHash(row *registryRecord, token string) bool {
	if token == "" {
		return true
	}
	hash := registryDigest(token)
	for _, previous := range row.RevokedHashes {
		if hash == previous {
			return true
		}
	}
	if len(row.RevokedHashes) >= maxRegistryHashes {
		return false
	}
	row.RevokedHashes = append(row.RevokedHashes, hash)
	return true
}

func registryTokenAvailable(c config.Config, disk registryDisk, token string) bool {
	hash := registryDigest(token)
	if hash == registryDigest(c.AdminToken) {
		return false
	}
	for _, row := range c.Devices {
		if hash == registryDigest(row.Token) {
			return false
		}
	}
	for _, row := range disk.Records {
		if hash == registryDigest(row.Token) {
			return false
		}
		for _, revoked := range row.RevokedHashes {
			if hash == revoked {
				return false
			}
		}
	}
	for _, op := range c.Operators {
		if hash == op.CredentialSHA256 {
			return false
		}
	}
	if c.Diagnostics != nil {
		for _, op := range c.Diagnostics.Operators {
			if hash == op.CredentialSHA256 {
				return false
			}
		}
	}
	return true
}

func (r *deviceRegistry) freshToken(disk registryDisk) (string, error) {
	for attempt := 0; attempt < 8; attempt++ {
		token, err := registryRandom(32, true)
		if err != nil {
			return "", err
		}
		if registryTokenAvailable(r.config, disk, token) {
			return token, nil
		}
	}
	return "", registryError
}

// Called with registry.mu held. Planning, validation and persistence all precede
// publication; callers never see a generated credential before durable success.
func (s *Server) mutateRegistry(kind string, m registryMutation, digest string) (int, any) {
	r := s.registry
	rows := s.registryView.Load().identities
	current, exists := rows[m.DeviceID]
	disk := cloneRegistry(r.disk, s.now())
	if len(disk.Operations) >= maxRegistryOperations || (kind != "revoke" && len(disk.Operations) >= maxRegistryOrdinaryOperations) {
		return registryFailure(409, "registry operation capacity reached")
	}
	index := registryRecordIndex(disk, m.DeviceID)
	row := registryRecord{DeviceID: m.DeviceID, Origin: "dynamic", State: "active", RevokedHashes: []string{}}
	if index >= 0 {
		row = disk.Records[index]
	}
	var static *config.Device
	for i := range r.config.Devices {
		if r.config.Devices[i].ID == m.DeviceID {
			static = &r.config.Devices[i]
			break
		}
	}
	if kind == "add" {
		if m.Diagnostics == nil || (*m.Diagnostics && r.config.Diagnostics == nil) {
			return registryFailure(400, "diagnostics configuration is required")
		}
		if exists || m.ConvertToDynamic != nil {
			if !exists || m.ConvertToDynamic == nil || !*m.ConvertToDynamic || static != nil || current.State != "conflict" || current.Generation != m.ExpectedGeneration {
				return registryFailure(409, "device already exists; use an explicit lifecycle operation")
			}
		}
		row.Origin, row.StaticFingerprint, row.Diagnostics = "dynamic", "", *m.Diagnostics
	} else {
		if !exists || current.Generation != m.ExpectedGeneration {
			return registryFailure(409, "device generation changed")
		}
		if kind == "reconcile" {
			if static == nil || current.StaticFingerprint != m.StaticFingerprint {
				return registryFailure(409, "static configuration changed or is absent")
			}
			row.Origin, row.StaticFingerprint = "static_override", current.StaticFingerprint
			row.Diagnostics = static.Diagnostics != nil && static.Diagnostics.Enabled
		} else if current.State == "conflict" {
			return registryFailure(409, "static identity requires explicit reconciliation")
		} else if index < 0 {
			row.Origin, row.StaticFingerprint, row.Diagnostics = "static_override", current.StaticFingerprint, current.Diagnostics
		}
	}
	if index < 0 && len(disk.Records) >= maxRegistryRecords {
		return registryFailure(409, "registry record capacity reached")
	}
	if !appendRevokedHash(&row, row.Token) || !appendRevokedHash(&row, current.Token) || (static != nil && !appendRevokedHash(&row, static.Token)) {
		return registryFailure(409, "revoked credential history capacity reached")
	}
	if kind == "revoke" && current.State == "revoked" {
		row.Generation = current.Generation
	} else {
		generation, err := registryRandom(16, false)
		if err != nil {
			return registryFailure(503, "cannot create device generation")
		}
		row.Generation = generation
	}
	row.State, row.Token = "revoked", ""
	if kind != "revoke" {
		token, err := r.freshToken(disk)
		if err != nil {
			return registryFailure(503, "cannot create device credential")
		}
		row.State, row.Token = "active", token
	}
	if index < 0 {
		disk.Records = append(disk.Records, row)
	} else {
		disk.Records[index] = row
	}
	kept := disk.Claims[:0]
	for _, claim := range disk.Claims {
		if claim.DeviceID != m.DeviceID {
			kept = append(kept, claim)
		}
	}
	disk.Claims = kept
	receipt := registryReceipt{Version: 1, OperationID: m.OperationID, DeviceID: m.DeviceID, Generation: row.Generation, State: row.State, Persisted: true}
	if kind == "revoke" {
		no := false
		receipt.DeviceStopConfirmed = &no
	}
	now := s.now().Unix()
	disk.Operations = append(disk.Operations, registryOperation{ID: m.OperationID, RequestHash: digest, Kind: kind, CreatedAt: now, ExpiresAt: now + 86400, Result: receipt})
	if _, err := registryIdentities(r.config, disk); err != nil {
		return registryFailure(409, "device registry capacity or identity conflict")
	}
	if err := s.commitRegistry(disk); err != nil {
		return registryCommitFailure(err, "device registry persistence is uncertain; reconcile before retrying")
	}
	return 200, receipt
}
