package config

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"regexp"
)

type DeviceDiagnostics struct {
	Enabled bool `json:"enabled"`
}
type DiagnosticsOperator struct {
	ID               string   `json:"id"`
	CredentialSHA256 string   `json:"credential_sha256"`
	DeviceIDs        []string `json:"device_ids"`
	Actions          []string `json:"actions"`
}

// Diagnostics ceilings may be lowered, never raised beyond the wire contract.
type Diagnostics struct {
	Operators              []DiagnosticsOperator `json:"operators"`
	RuntimeTTLMS           int                   `json:"runtime_ttl_ms"`
	SessionLeaseMSMax      int                   `json:"session_lease_ms_max"`
	MaxRuntimesPerDevice   int                   `json:"max_runtimes_per_device"`
	MaxRuntimesTotal       int                   `json:"max_runtimes_total"`
	MaxRetainedSessions    int                   `json:"max_retained_sessions"`
	EventRetentionMS       int                   `json:"event_retention_ms"`
	EventBytesPerRuntime   int                   `json:"event_bytes_per_runtime"`
	EventBytesTotal        int                   `json:"event_bytes_total"`
	MaxIdempotencyRecords  int                   `json:"max_idempotency_records"`
	IdempotencyRetentionMS int                   `json:"idempotency_retention_ms"`
}

func (d *Diagnostics) Defaults() {
	for _, pair := range []struct {
		p *int
		n int
	}{
		{&d.RuntimeTTLMS, 600000}, {&d.SessionLeaseMSMax, 600000},
		{&d.MaxRuntimesPerDevice, 4}, {&d.MaxRuntimesTotal, 256},
		{&d.MaxRetainedSessions, 1024}, {&d.EventRetentionMS, 300000},
		{&d.EventBytesPerRuntime, 256 * 1024}, {&d.EventBytesTotal, 16 * 1024 * 1024},
		{&d.MaxIdempotencyRecords, 1024}, {&d.IdempotencyRetentionMS, 900000},
	} {
		if *pair.p == 0 {
			*pair.p = pair.n
		}
	}
}

var diagnosticLabel = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,80}$`)

func (c Config) validateDiagnostics() error {
	if c.Diagnostics == nil {
		for _, d := range c.Devices {
			if d.Diagnostics != nil && d.Diagnostics.Enabled {
				return errors.New("enabled device diagnostics requires diagnostics configuration")
			}
		}
		return nil
	}
	d := c.Diagnostics
	for _, p := range []struct{ n, min, max int }{
		{d.RuntimeTTLMS, 10000, 600000}, {d.SessionLeaseMSMax, 1000, 600000},
		{d.MaxRuntimesPerDevice, 1, 4}, {d.MaxRuntimesTotal, 1, 256}, {d.MaxRetainedSessions, 1, 1024},
		{d.EventRetentionMS, 1000, 300000}, {d.EventBytesPerRuntime, 1024, 256 * 1024},
		{d.EventBytesTotal, 1024, 16 * 1024 * 1024}, {d.MaxIdempotencyRecords, 2, 1024}, {d.IdempotencyRetentionMS, 1000, 900000},
	} {
		if p.n < p.min || p.n > p.max {
			return errors.New("diagnostics limit is outside allowed bounds")
		}
	}
	if len(d.Operators) > 64 {
		return errors.New("diagnostics operators exceeds limit")
	}
	devices := map[string]bool{}
	hashes := map[string]bool{}
	h := sha256.Sum256([]byte(c.AdminToken))
	hashes[hex.EncodeToString(h[:])] = true
	for _, v := range c.Devices {
		devices[v.ID] = true
		h = sha256.Sum256([]byte(v.Token))
		hashes[hex.EncodeToString(h[:])] = true
	}
	ids := map[string]bool{}
	for _, o := range d.Operators {
		b, e := hex.DecodeString(o.CredentialSHA256)
		if !diagnosticLabel.MatchString(o.ID) || ids[o.ID] || e != nil || len(b) != 32 || hex.EncodeToString(b) != o.CredentialSHA256 || hashes[o.CredentialSHA256] {
			return errors.New("invalid or duplicate diagnostic operator identity or credential digest")
		}
		ids[o.ID] = true
		hashes[o.CredentialSHA256] = true
		if len(o.DeviceIDs) == 0 || len(o.DeviceIDs) > MaxDevices || len(o.Actions) == 0 || len(o.Actions) > 5 {
			return errors.New("diagnostic operator requires bounded device and action scopes")
		}
		seen := map[string]bool{}
		for _, id := range o.DeviceIDs {
			if !devices[id] || seen[id] {
				return errors.New("invalid diagnostic operator device scope")
			}
			seen[id] = true
		}
		seen = map[string]bool{}
		for _, a := range o.Actions {
			if seen[a] {
				return errors.New("duplicate diagnostic operator action")
			}
			seen[a] = true
			switch a {
			case "runtimes.read", "sessions.start", "sessions.stop", "sessions.read", "runtimes.revoke":
			default:
				return errors.New("invalid diagnostic operator action")
			}
		}
	}
	return nil
}
