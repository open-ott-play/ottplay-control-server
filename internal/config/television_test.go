package config

import (
	"path/filepath"
	"testing"
)

func TestTelevisionDefaultsAndValidation(t *testing.T) {
	privateDir := t.TempDir()
	base := Television{DeviceID: "living", Host: "192.168.1.5", ClientKeyFile: filepath.Join(privateDir, "tv-key.json"), AppID: "de.benzac.msx"}
	base.Defaults()
	if base.Port != 3001 || !base.Secure() || base.Broadcast != "255.255.255.255" || base.WOLPort != 9 {
		t.Fatalf("defaults=%+v", base)
	}
	if err := base.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		name   string
		change func(*Television)
	}{
		{"url instead of host", func(c *Television) { c.Host = "http://example.org" }},
		{"relative key", func(c *Television) { c.ClientKeyFile = "key.json" }},
		{"both launch targets", func(c *Television) { c.LaunchURL = "https://example.org" }},
		{"url credentials", func(c *Television) { c.AppID = ""; c.LaunchURL = "https://user:pass@example.org" }},
		{"invalid pin", func(c *Television) { c.CertificateSHA256 = "123" }},
		{"multicast MAC", func(c *Television) { c.MAC = "01:00:00:00:00:01" }},
		{"zero MAC", func(c *Television) { c.MAC = "00:00:00:00:00:00" }},
		{"invalid broadcast", func(c *Television) { c.Broadcast = "example.org" }},
		{"uppercase app alias", func(c *Television) { c.AppAliases = map[string]string{"Plex": "cdp-30"} }},
		{"long app alias", func(c *Television) { c.AppAliases = map[string]string{"abcdefghijklmnopqrstuvwxyz1234567": "cdp-30"} }},
		{"empty app target", func(c *Television) { c.AppAliases = map[string]string{"plex": ""} }},
		{"app target URL", func(c *Television) { c.AppAliases = map[string]string{"plex": "https://example.org"} }},
		{"too many app aliases", func(c *Television) {
			c.AppAliases = map[string]string{}
			for i := range 17 {
				c.AppAliases[string(rune('a'+i))] = "cdp-30"
			}
		}},
		{"fallback token", func(c *Television) {
			c.Fallback = &TelevisionFallback{URL: "https://ha.example.org", TokenFile: "relative", EntityID: "media_player.tv"}
		}},
		{"fallback non-player", func(c *Television) {
			c.Fallback = &TelevisionFallback{URL: "https://ha.example.org", TokenFile: filepath.Join(privateDir, "token"), EntityID: "sensor.tv"}
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			c := base
			test.change(&c)
			if c.Validate() == nil {
				t.Fatal("invalid configuration accepted")
			}
		})
	}
	plain := false
	c := base
	c.TLS = &plain
	c.Port = 0
	c.Defaults()
	if c.Port != 3000 || c.Secure() {
		t.Fatal("explicit plaintext not respected")
	}
	if ValidateTelevisions([]Television{base, base}) == nil {
		t.Fatal("duplicate binding accepted")
	}
	c = base
	c.AppAliases = map[string]string{"plex": "cdp-30", "media_station-x": "de.benzac.msx"}
	if err := c.Validate(); err != nil {
		t.Fatalf("valid named applications rejected: %v", err)
	}
}
