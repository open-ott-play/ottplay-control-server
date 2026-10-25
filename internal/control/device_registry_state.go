package control

import (
	"encoding/json"
	"sort"
	"strings"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

type registryIdentity struct {
	DeviceID          string `json:"device_id"`
	Generation        string `json:"generation"`
	Origin            string `json:"origin"`
	State             string `json:"state"`
	Diagnostics       bool   `json:"diagnostics_enabled"`
	StaticFingerprint string `json:"static_fingerprint,omitempty"`
	Token             string `json:"-"`
}

func validateRegistryDisk(d registryDisk) error {
	if d.Version != 1 || !commandID.MatchString(d.StoreID) || d.Revision < 0 || d.Revision >= diagSafeInteger || d.Records == nil || d.Claims == nil || d.Operations == nil || len(d.Records) > maxRegistryRecords || len(d.Claims) > maxPairings || len(d.Operations) > maxRegistryOperations {
		return registryError
	}
	ids, generations := map[string]bool{}, map[string]bool{}
	for _, row := range d.Records {
		if !registryID.MatchString(row.DeviceID) || ids[row.DeviceID] || !commandID.MatchString(row.Generation) || generations[row.Generation] || (row.State != "active" && row.State != "revoked") || row.RevokedHashes == nil || len(row.RevokedHashes) > maxRegistryHashes {
			return registryError
		}
		ids[row.DeviceID], generations[row.Generation] = true, true
		if (row.Origin != "dynamic" && row.Origin != "static_override") || (row.Origin == "dynamic" && row.StaticFingerprint != "") || (row.Origin == "static_override" && !registryHash.MatchString(row.StaticFingerprint)) {
			return registryError
		}
		if (row.State == "active" && !deviceTransportToken.MatchString(row.Token)) || (row.State == "revoked" && row.Token != "") {
			return registryError
		}
		seen := map[string]bool{}
		for _, hash := range row.RevokedHashes {
			if !registryHash.MatchString(hash) || seen[hash] || hash == registryDigest(row.Token) {
				return registryError
			}
			seen[hash] = true
		}
	}
	ids = map[string]bool{}
	for _, c := range d.Claims {
		address, err := config.HTTPSBase(c.Address)
		canonical, err2 := config.HTTPSBase(c.Server.Address)
		if !commandID.MatchString(c.ID) || !commandID.MatchString(c.OperationID) || ids[c.ID] || !registryHash.MatchString(c.SecretHash) || !registryHash.MatchString(c.CodeHash) || !registryID.MatchString(c.DeviceID) || !commandID.MatchString(c.Generation) || c.ExpiresAt <= 0 || c.ExpiresAt >= diagSafeInteger || err != nil || err2 != nil || address != c.Address || canonical != c.Server.Address || !config.ValidDNSHost(c.Server.Domain) || c.Server.ID == "" || len(c.Server.ID) > 254 || strings.ContainsAny(c.Server.ID, " /\\\r\n\t") {
			return registryError
		}
		ids[c.ID] = true
	}
	ids = map[string]bool{}
	for _, op := range d.Operations {
		if !commandID.MatchString(op.ID) || ids[op.ID] || !registryHash.MatchString(op.RequestHash) || op.CreatedAt <= 0 || op.CreatedAt >= diagSafeInteger-86400 || op.ExpiresAt != op.CreatedAt+86400 || op.Result.Version != 1 || op.Result.OperationID != op.ID || !registryID.MatchString(op.Result.DeviceID) || !commandID.MatchString(op.Result.Generation) || !op.Result.Persisted || (op.Result.State != "active" && op.Result.State != "revoked") {
			return registryError
		}
		switch op.Kind {
		case "add", "rotate", "reconcile", "approve":
			if op.Result.State != "active" || op.Result.DeviceStopConfirmed != nil {
				return registryError
			}
		case "revoke":
			if op.Result.State != "revoked" || op.Result.DeviceStopConfirmed == nil || *op.Result.DeviceStopConfirmed {
				return registryError
			}
		default:
			return registryError
		}
		ids[op.ID] = true
	}
	return nil
}

func staticRegistryFingerprint(d config.Device) string {
	b, _ := json.Marshal(struct {
		ID, Token   string
		Diagnostics bool
	}{d.ID, d.Token, d.Diagnostics != nil && d.Diagnostics.Enabled})
	return registryDigest(string(b))
}

func registryIdentities(c config.Config, disk registryDisk) (map[string]registryIdentity, error) {
	rows := map[string]registryIdentity{}
	static := map[string]config.Device{}
	for _, d := range c.Devices {
		fingerprint := staticRegistryFingerprint(d)
		rows[d.ID] = registryIdentity{DeviceID: d.ID, Generation: registryDigest(disk.StoreID + ":" + fingerprint)[:32], Origin: "static", State: "active", Diagnostics: d.Diagnostics != nil && d.Diagnostics.Enabled, StaticFingerprint: fingerprint, Token: d.Token}
		static[d.ID] = d
	}
	for _, row := range disk.Records {
		original, present := static[row.DeviceID]
		view := registryIdentity{DeviceID: row.DeviceID, Generation: row.Generation, Origin: row.Origin, State: row.State, Diagnostics: row.Diagnostics, Token: row.Token}
		if present {
			view.StaticFingerprint = staticRegistryFingerprint(original)
		}
		if (row.Origin == "static_override" && (!present || view.StaticFingerprint != row.StaticFingerprint)) || (row.Origin == "dynamic" && present) {
			view.State = "conflict"
			view.Token = ""
		}
		rows[row.DeviceID] = view
	}
	hashes := map[string]bool{registryDigest(c.AdminToken): true}
	revoked := map[string]bool{}
	for _, record := range disk.Records {
		for _, hash := range record.RevokedHashes {
			revoked[hash] = true
		}
	}
	for _, op := range c.Operators {
		hashes[op.CredentialSHA256] = true
	}
	if c.Diagnostics != nil {
		for _, op := range c.Diagnostics.Operators {
			hashes[op.CredentialSHA256] = true
		}
	}
	active := 0
	for _, row := range rows {
		if row.State != "active" {
			continue
		}
		if row.Diagnostics && c.Diagnostics == nil {
			return nil, registryError
		}
		hash := registryDigest(row.Token)
		if revoked[hash] {
			return nil, registryRevokedCredentialError
		}
		if hashes[hash] {
			return nil, registryError
		}
		hashes[hash] = true
		active++
	}
	if active > config.MaxDevices {
		return nil, registryError
	}
	known := map[string]bool{}
	for id := range rows {
		known[id] = true
	}
	if c.ValidateDeviceScopes(known) != nil {
		return nil, registryError
	}
	return rows, nil
}

func sortedRegistryIdentities(rows map[string]registryIdentity) []registryIdentity {
	result := make([]registryIdentity, 0, len(rows))
	for _, row := range rows {
		row.Token = ""
		result = append(result, row)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].DeviceID < result[j].DeviceID })
	return result
}

func ValidateDeviceRegistry(c config.Config) error {
	if c.DeviceRegistry == nil {
		return nil
	}
	disk, err := readRegistry(c.DeviceRegistry.Path)
	if err != nil {
		return err
	}
	rows, err := registryIdentities(c, disk)
	if err != nil {
		return err
	}
	return registryRevocationReserve(c, disk, rows, time.Now())
}

// Reserve the actual record, hash and byte footprint of revoking every active
// identity. No ordinary mutation can consume the last revocation capacity.
func registryRevocationReserve(c config.Config, disk registryDisk, rows map[string]registryIdentity, now time.Time) error {
	reserved := cloneRegistry(disk, now)
	reserved.Revision = diagSafeInteger - 1
	no := false
	for _, identity := range rows {
		if identity.State != "active" {
			continue
		}
		index := registryRecordIndex(reserved, identity.DeviceID)
		row := registryRecord{DeviceID: identity.DeviceID, Generation: identity.Generation, Origin: "static_override", State: "revoked", Diagnostics: identity.Diagnostics, StaticFingerprint: identity.StaticFingerprint, RevokedHashes: []string{}}
		if index >= 0 {
			row = reserved.Records[index]
		}
		if !appendRevokedHash(&row, identity.Token) {
			return registryCapacityError
		}
		for _, original := range c.Devices {
			if original.ID == identity.DeviceID && !appendRevokedHash(&row, original.Token) {
				return registryCapacityError
			}
		}
		row.State, row.Token = "revoked", ""
		if index < 0 {
			reserved.Records = append(reserved.Records, row)
		} else {
			reserved.Records[index] = row
		}
		// Fixed-width IDs/hashes and maximum valid timestamps bound future
		// receipts even when their wall-clock digit width changes.
		id := strings.Repeat("0", 32)
		reserved.Operations = append(reserved.Operations, registryOperation{
			ID: id, RequestHash: strings.Repeat("0", 64), Kind: "revoke",
			CreatedAt: diagSafeInteger - 86401, ExpiresAt: diagSafeInteger - 1,
			Result: registryReceipt{Version: 1, OperationID: id, DeviceID: identity.DeviceID, Generation: identity.Generation, State: "revoked", Persisted: true, DeviceStopConfirmed: &no},
		})
	}
	if len(reserved.Records) > maxRegistryRecords || len(reserved.Operations) > maxRegistryOperations {
		return registryCapacityError
	}
	encoded, err := json.Marshal(reserved)
	if err != nil || len(encoded) > maxRegistryBytes {
		return registryCapacityError
	}
	return nil
}
