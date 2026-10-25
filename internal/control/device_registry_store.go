package control

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
	"github.com/open-ott-play/ottplay-control-server/internal/wire"
)

const maxRegistryBytes = 4 * 1024 * 1024
const maxRegistryRecords = 1024
const maxRegistryHashes = 64
const maxRegistryOrdinaryOperations = 128
const maxRegistryOperations = maxRegistryOrdinaryOperations + config.MaxDevices

var registryError = errors.New("device registry unavailable")
var registryCapacityError = errors.New("device registry capacity reached")
var registryRevokedCredentialError = errors.New("active device credential matches revoked history")
var registryID = regexp.MustCompile(wire.DeviceIdPattern)
var registryHash = regexp.MustCompile(`^[a-f0-9]{64}$`)

type registryRecord struct {
	DeviceID          string   `json:"device_id"`
	Generation        string   `json:"generation"`
	State             string   `json:"state"`
	Origin            string   `json:"origin"`
	Token             string   `json:"token,omitempty"`
	Diagnostics       bool     `json:"diagnostics_enabled"`
	StaticFingerprint string   `json:"static_fingerprint,omitempty"`
	RevokedHashes     []string `json:"revoked_token_sha256"`
}

type registryClaim struct {
	ID          string           `json:"id"`
	OperationID string           `json:"operation_id"`
	SecretHash  string           `json:"secret_sha256"`
	CodeHash    string           `json:"code_sha256"`
	DeviceID    string           `json:"device_id"`
	Generation  string           `json:"generation"`
	Server      discoveredServer `json:"server"`
	Address     string           `json:"address"`
	ExpiresAt   int64            `json:"expires_at"`
}

type registryReceipt struct {
	Version             int    `json:"version"`
	OperationID         string `json:"operation_id"`
	DeviceID            string `json:"device_id"`
	Generation          string `json:"generation"`
	State               string `json:"state"`
	Persisted           bool   `json:"persisted"`
	DeviceStopConfirmed *bool  `json:"device_stop_confirmed,omitempty"`
}

type registryOperation struct {
	ID          string          `json:"id"`
	RequestHash string          `json:"request_sha256"`
	Kind        string          `json:"kind"`
	CreatedAt   int64           `json:"created_at"`
	ExpiresAt   int64           `json:"expires_at"`
	Result      registryReceipt `json:"result"`
}

type registryDisk struct {
	Version    int                 `json:"version"`
	StoreID    string              `json:"store_id"`
	Revision   int64               `json:"revision"`
	Records    []registryRecord    `json:"records"`
	Claims     []registryClaim     `json:"approved_claims"`
	Operations []registryOperation `json:"operations"`
}

type deviceRegistry struct {
	mu      sync.Mutex
	path    string
	lock    *os.File
	disk    registryDisk
	config  config.Config
	failed  bool
	persist func(registryDisk) error
}

func registryDigest(value string) string {
	h := sha256.Sum256([]byte(value))
	return hex.EncodeToString(h[:])
}

func registryRandom(size int, token bool) (string, error) {
	b := make([]byte, size)
	if _, err := rand.Read(b); err != nil {
		return "", registryError
	}
	if token {
		return base64.RawURLEncoding.EncodeToString(b), nil
	}
	return hex.EncodeToString(b), nil
}

func registryPrivatePath(path string, createParent bool) error {
	parent := filepath.Dir(path)
	if createParent && os.MkdirAll(parent, 0700) != nil {
		return registryError
	}
	info, err := os.Lstat(parent)
	if err != nil || !info.IsDir() || (runtime.GOOS != "windows" && info.Mode().Perm()&0077 != 0) {
		return registryError
	}
	for _, name := range []string{path, path + ".lock"} {
		info, err := os.Lstat(name)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return registryError
		}
		if !info.Mode().IsRegular() || (runtime.GOOS != "windows" && info.Mode().Perm()&0077 != 0) {
			return registryError
		}
	}
	return nil
}

func syncRegistryParent(path string) error {
	if runtime.GOOS == "windows" {
		return nil
	}
	f, err := os.Open(filepath.Dir(path))
	if err != nil {
		return registryError
	}
	defer f.Close()
	if f.Sync() != nil {
		return registryError
	}
	return nil
}

// InitDeviceRegistry refuses to overwrite either live state or a symlink.
func InitDeviceRegistry(c config.Config) error {
	if c.DeviceRegistry == nil || c.DeviceRegistry.Validate() != nil {
		return registryError
	}
	path := c.DeviceRegistry.Path
	if registryPrivatePath(path, true) != nil {
		return registryError
	}
	lock, err := lockJournal(path + ".lock")
	if err != nil {
		return registryError
	}
	defer lock.Close()
	id, err := registryRandom(16, false)
	if err != nil {
		return err
	}
	disk := registryDisk{Version: 1, StoreID: id, Records: []registryRecord{}, Claims: []registryClaim{}, Operations: []registryOperation{}}
	b, _ := json.Marshal(disk)
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return registryError
	}
	if _, err = f.Write(b); err != nil {
		f.Close()
		return registryError
	}
	if f.Sync() != nil {
		f.Close()
		return registryError
	}
	if f.Close() != nil {
		return registryError
	}
	return syncRegistryParent(path)
}

func readRegistry(path string) (registryDisk, error) {
	var disk registryDisk
	if registryPrivatePath(path, false) != nil {
		return disk, registryError
	}
	f, err := os.Open(path)
	if err != nil {
		return disk, registryError
	}
	b, err := io.ReadAll(io.LimitReader(f, maxRegistryBytes+1))
	f.Close()
	if err != nil || len(b) > maxRegistryBytes || !diagStrictJSON(b) {
		return disk, registryError
	}
	if !diagDecode(b, &disk, []string{"version", "store_id", "revision", "records", "approved_claims", "operations"}) || !registryNestedFields(b) || validateRegistryDisk(disk) != nil {
		return disk, registryError
	}
	return disk, nil
}

func registryNestedFields(raw []byte) bool {
	root, _ := decodeObject(raw)
	for _, name := range []string{"records", "approved_claims", "operations"} {
		var rows []json.RawMessage
		if json.Unmarshal(root[name], &rows) != nil || rows == nil {
			return false
		}
		for _, row := range rows {
			var value any
			switch name {
			case "records":
				if !diagDecode(row, &value, []string{"device_id", "generation", "state", "origin", "diagnostics_enabled", "revoked_token_sha256"}, "token", "static_fingerprint") {
					return false
				}
			case "approved_claims":
				if !diagDecode(row, &value, []string{"id", "operation_id", "secret_sha256", "code_sha256", "device_id", "generation", "server", "address", "expires_at"}) {
					return false
				}
			case "operations":
				if !diagDecode(row, &value, []string{"id", "request_sha256", "kind", "created_at", "expires_at", "result"}) {
					return false
				}
				fields, _ := decodeObject(row)
				if !diagDecode(fields["result"], &value, []string{"version", "operation_id", "device_id", "generation", "state", "persisted"}, "device_stop_confirmed") {
					return false
				}
			}
		}
	}
	return true
}

func openDeviceRegistry(c config.Config) (*deviceRegistry, error) {
	if c.DeviceRegistry == nil {
		return nil, nil
	}
	if registryPrivatePath(c.DeviceRegistry.Path, false) != nil {
		return nil, registryError
	}
	lock, err := lockJournal(c.DeviceRegistry.Path + ".lock")
	if err != nil {
		return nil, registryError
	}
	disk, err := readRegistry(c.DeviceRegistry.Path)
	if err != nil {
		lock.Close()
		return nil, err
	}
	r := &deviceRegistry{path: c.DeviceRegistry.Path, lock: lock, disk: disk, config: c}
	r.persist = r.save
	return r, nil
}

// Retiring a configured address invalidates its approved claims durably before
// serving. Restoring an alias later must not resurrect an old claim secret.
func (r *deviceRegistry) pruneConfiguredClaims(now time.Time) error {
	next := cloneRegistry(r.disk, now)
	allowed := map[string]bool{}
	canonical, domain := "", ""
	if discovery := r.config.Discovery; discovery != nil {
		canonical, _ = config.HTTPSBase(discovery.PublicURL)
		domain = strings.TrimSuffix(strings.ToLower(discovery.Domain), ".")
		allowed[canonical] = true
		for _, alias := range discovery.PublicAliases {
			address, _ := config.HTTPSBase(alias)
			allowed[address] = true
		}
	}
	kept := next.Claims[:0]
	for _, claim := range next.Claims {
		if allowed[claim.Address] && claim.Server.Address == canonical && (domain == "" || strings.TrimSuffix(strings.ToLower(claim.Server.Domain), ".") == domain) {
			kept = append(kept, claim)
		}
	}
	next.Claims = kept
	if len(next.Claims) == len(r.disk.Claims) {
		return nil
	}
	if err := r.persist(next); err != nil {
		return registryError
	}
	r.disk = next
	return nil
}

func (r *deviceRegistry) save(disk registryDisk) error {
	if validateRegistryDisk(disk) != nil || registryPrivatePath(r.path, false) != nil {
		return registryError
	}
	b, err := json.Marshal(disk)
	if err != nil || len(b) > maxRegistryBytes {
		return registryError
	}
	f, err := os.CreateTemp(filepath.Dir(r.path), ".ott-registry-*")
	if err != nil {
		return registryError
	}
	name := f.Name()
	defer os.Remove(name)
	defer f.Close()
	if f.Chmod(0600) != nil {
		return registryError
	}
	if _, err = f.Write(b); err != nil {
		return registryError
	}
	if f.Sync() != nil || f.Close() != nil || os.Rename(name, r.path) != nil {
		return registryError
	}
	return syncRegistryParent(r.path)
}

func cloneRegistry(disk registryDisk, now time.Time) registryDisk {
	copy := disk
	copy.Records = append([]registryRecord{}, disk.Records...)
	for i := range copy.Records {
		copy.Records[i].RevokedHashes = append([]string{}, disk.Records[i].RevokedHashes...)
	}
	copy.Claims = []registryClaim{}
	for _, c := range disk.Claims {
		if c.ExpiresAt > now.Unix() {
			copy.Claims = append(copy.Claims, c)
		}
	}
	copy.Operations = []registryOperation{}
	for _, op := range disk.Operations {
		if op.ExpiresAt > now.Unix() {
			copy.Operations = append(copy.Operations, op)
		}
	}
	copy.Revision++
	return copy
}
