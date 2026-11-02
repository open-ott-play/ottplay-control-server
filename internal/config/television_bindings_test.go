package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func televisionConfig(t *testing.T) Config {
	t.Helper()
	c := Config{AdminToken: strings.Repeat("a", 32), Devices: []Device{{ID: "living", Token: strings.Repeat("b", 32)}}}
	c.Televisions = []Television{{DeviceID: "living", Host: "192.0.2.4", ClientKeyFile: filepath.Join(t.TempDir(), "lg.json")}}
	c.Defaults()
	return c
}

func TestTelevisionBindingsAreExplicitAndKeysIsolated(t *testing.T) {
	c := televisionConfig(t)
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	c.Televisions[0].DeviceID = "unknown"
	if c.Validate() == nil {
		t.Fatal("bound an unregistered player")
	}
	c.Televisions[0].DeviceID = "living"
	c.Devices = append(c.Devices, Device{ID: "other", Token: strings.Repeat("c", 32)})
	other := c.Televisions[0]
	other.DeviceID = "other"
	other.ClientKeyFile += ".other"
	c.Televisions = append(c.Televisions, other)
	if c.Validate() == nil {
		t.Fatal("duplicate physical endpoint admitted")
	}
	c.Televisions[1].Host = "192.0.2.5"
	c.Televisions[1].ClientKeyFile = c.Televisions[0].ClientKeyFile
	if c.Validate() == nil {
		t.Fatal("shared pairing key path admitted")
	}
	c.Televisions = c.Televisions[:1]
	c.Televisions[0].Fallback = &TelevisionFallback{URL: "https://ha.example", EntityID: "media_player.lg", TokenFile: c.Televisions[0].ClientKeyFile}
	if c.Validate() == nil {
		t.Fatal("pairing can overwrite HA credentials")
	}
}

func TestTelevisionPairingCannotOverwriteLoadedConfiguration(t *testing.T) {
	c := televisionConfig(t)
	path := filepath.Join(t.TempDir(), "server.json")
	c.Televisions[0].ClientKeyFile = path
	data, err := json.Marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("pairing may replace server configuration")
	}
	after, _ := os.ReadFile(path)
	if string(after) != string(data) {
		t.Fatal("validation mutated configuration")
	}
}
