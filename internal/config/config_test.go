package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestInitPrivateExclusiveAndLoad(t *testing.T) {
	p := filepath.Join(t.TempDir(), "config.json")
	if err := Init(p, "living-room"); err != nil {
		t.Fatal(err)
	}
	c, err := Load(p)
	if err != nil {
		t.Fatal(err)
	}
	if c.Listen != "127.0.0.1:8081" || c.CommandTTLSeconds != 60 || c.MaxPendingPerDevice != 50 || c.Devices[0].Token == c.AdminToken {
		t.Fatal("invalid generated defaults or credentials")
	}
	info, _ := os.Stat(p)
	// Windows exposes synthetic POSIX mode bits; access is controlled by ACLs.
	if runtime.GOOS != "windows" && info.Mode().Perm()&0077 != 0 {
		t.Fatal("configuration is not private")
	}
	before, _ := os.ReadFile(p)
	if Init(p, "other") == nil {
		t.Fatal("overwrote existing file")
	}
	after, _ := os.ReadFile(p)
	if string(before) != string(after) {
		t.Fatal("existing configuration changed")
	}
	symlink := filepath.Join(t.TempDir(), "link.json")
	if err := os.Symlink(p, symlink); err == nil && Init(symlink, "other") == nil {
		t.Fatal("followed existing symlink")
	}
}

func TestRejectInvalidConfigWithoutSecrets(t *testing.T) {
	p := filepath.Join(t.TempDir(), "config.json")
	if err := Init(p, "tv"); err != nil {
		t.Fatal(err)
	}
	c, _ := Load(p)
	tests := []func(*Config){
		func(c *Config) { c.Devices[0].Token = c.AdminToken },
		func(c *Config) { c.AllowedOrigins = []string{"*"} },
		func(c *Config) { c.AllowedOrigins = []string{"null"} },
		func(c *Config) { c.AllowedOrigins = []string{"https://example.com/path"} },
		func(c *Config) { c.Devices[0].ID = "../bad" },
		func(c *Config) { c.CommandTTLSeconds = -1 },
		func(c *Config) { c.MaxPendingPerDevice = 51 },
	}
	for _, mutate := range tests {
		d := c
		d.Devices = append([]Device(nil), c.Devices...)
		mutate(&d)
		if err := d.Validate(); err == nil {
			t.Fatal("accepted invalid config")
		} else if strings.Contains(err.Error(), c.AdminToken) || strings.Contains(err.Error(), c.Devices[0].Token) {
			t.Fatal("error leaked credentials")
		}
	}
	b, _ := json.Marshal(c)
	for _, bad := range []string{string(b) + ` {}`, strings.TrimSuffix(string(b), "}") + `,"unknown_secret":"do-not-print"}`} {
		if err := os.WriteFile(p, []byte(bad), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := Load(p); err == nil || strings.Contains(err.Error(), "do-not-print") {
			t.Fatal("unknown/trailing fields not safely rejected")
		}
	}
}
