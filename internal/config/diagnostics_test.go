package config

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"
)

func diagnosticTestConfig() Config {
	digest := sha256.Sum256([]byte(strings.Repeat("o", 32)))
	c := Config{AdminToken: strings.Repeat("a", 32), Devices: []Device{{ID: "tv", Token: strings.Repeat("d", 32), Diagnostics: &DeviceDiagnostics{Enabled: true}}}, Diagnostics: &Diagnostics{Operators: []DiagnosticsOperator{{ID: "operator", CredentialSHA256: hex.EncodeToString(digest[:]), DeviceIDs: []string{"tv"}, Actions: []string{"sessions.start", "sessions.stop"}}}}}
	c.Defaults()
	return c
}
func TestDiagnosticsConfigurationBoundsAndScope(t *testing.T) {
	if e := diagnosticTestConfig().Validate(); e != nil {
		t.Fatal(e)
	}
	cases := map[string]func(*Config){
		"missing configuration":    func(c *Config) { c.Diagnostics = nil },
		"runtime ceiling":          func(c *Config) { c.Diagnostics.MaxRuntimesTotal = 257 },
		"session ceiling":          func(c *Config) { c.Diagnostics.SessionLeaseMSMax = 600001 },
		"event ceiling":            func(c *Config) { c.Diagnostics.EventBytesTotal = 16*1024*1024 + 1 },
		"missing stop reservation": func(c *Config) { c.Diagnostics.MaxIdempotencyRecords = 1 },
		"record ceiling":           func(c *Config) { c.Diagnostics.MaxRetainedSessions = 1025 },
		"idempotency ceiling":      func(c *Config) { c.Diagnostics.MaxIdempotencyRecords = 1025 },
		"raw credential":           func(c *Config) { c.Diagnostics.Operators[0].CredentialSHA256 = strings.Repeat("s", 32) },
		"shared admin credential": func(c *Config) {
			h := sha256.Sum256([]byte(c.AdminToken))
			c.Diagnostics.Operators[0].CredentialSHA256 = hex.EncodeToString(h[:])
		},
		"unknown device":     func(c *Config) { c.Diagnostics.Operators[0].DeviceIDs = []string{"unknown"} },
		"wildcard action":    func(c *Config) { c.Diagnostics.Operators[0].Actions = []string{"*"} },
		"duplicate action":   func(c *Config) { c.Diagnostics.Operators[0].Actions = []string{"sessions.start", "sessions.start"} },
		"duplicate operator": func(c *Config) { c.Diagnostics.Operators = append(c.Diagnostics.Operators, c.Diagnostics.Operators[0]) },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			c := diagnosticTestConfig()
			mutate(&c)
			if e := c.Validate(); e == nil {
				t.Fatal("accepted invalid diagnostics configuration")
			} else if strings.Contains(e.Error(), c.AdminToken) || strings.Contains(e.Error(), strings.Repeat("o", 32)) {
				t.Fatal("error exposed credential material")
			}
		})
	}
}
