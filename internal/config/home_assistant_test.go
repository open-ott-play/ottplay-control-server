package config

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

func validHAConfig(t *testing.T) HomeAssistant {
	t.Helper()
	c := HomeAssistant{Enabled: true, URL: "https://ha.example/house", TokenFile: filepath.Join(t.TempDir(), "ha-token"), Rules: []HomeAssistantRule{{ID: "door", EventType: "state_changed", EntityIDs: []string{"binary_sensor.front_door"}, Title: "Home", Message: "{{name}}: {{state}}", Targets: []string{"tv"}}}}
	c.Defaults()
	return c
}

func TestHomeAssistantDefaultsAndDisabledCompatibility(t *testing.T) {
	if err := (HomeAssistant{}).Validate(); err != nil {
		t.Fatal(err)
	}
	c := validHAConfig(t)
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	r := c.Rules[0]
	if c.MaxPerMinute != 30 || c.EventMaxAgeSeconds != 30 || r.Severity != "info" || r.DurationMS != 5000 || r.TTLSeconds != 15 || r.CooldownSeconds != 60 {
		t.Fatalf("unexpected defaults: %+v %+v", c, r)
	}
}

func TestHomeAssistantConfigurationRejectsUnsafeOrUnboundedInputs(t *testing.T) {
	tests := map[string]func(*HomeAssistant){
		"url credentials":      func(c *HomeAssistant) { c.URL = "https://admin:SECRET@ha.example" },
		"url query":            func(c *HomeAssistant) { c.URL += "?token=SECRET" },
		"url fragment":         func(c *HomeAssistant) { c.URL += "#SECRET" },
		"url scheme":           func(c *HomeAssistant) { c.URL = "file:///run/secrets/token" },
		"url dot path":         func(c *HomeAssistant) { c.URL += "/../other" },
		"relative token":       func(c *HomeAssistant) { c.TokenFile = "secret" },
		"all events":           func(c *HomeAssistant) { c.Rules[0].EventType = "" },
		"no entity filter":     func(c *HomeAssistant) { c.Rules[0].EntityIDs = nil },
		"entity wildcard":      func(c *HomeAssistant) { c.Rules[0].EntityIDs = []string{"sensor.*"} },
		"duplicate entity":     func(c *HomeAssistant) { c.Rules[0].EntityIDs = []string{"sensor.a", "sensor.a"} },
		"duplicate rule":       func(c *HomeAssistant) { c.Rules = append(c.Rules, c.Rules[0]) },
		"unknown template":     func(c *HomeAssistant) { c.Rules[0].Message = "{{ attributes.token }}" },
		"broken template":      func(c *HomeAssistant) { c.Rules[0].Message = "{{ state" },
		"long message":         func(c *HomeAssistant) { c.Rules[0].Message = strings.Repeat("x", 513) },
		"astral message":       func(c *HomeAssistant) { c.Rules[0].Message = strings.Repeat("😀", 257) },
		"duration":             func(c *HomeAssistant) { c.Rules[0].DurationMS = 15001 },
		"ttl":                  func(c *HomeAssistant) { c.Rules[0].TTLSeconds = 61 },
		"severity":             func(c *HomeAssistant) { c.Rules[0].Severity = "error" },
		"cooldown":             func(c *HomeAssistant) { c.Rules[0].CooldownSeconds = -1 },
		"rate":                 func(c *HomeAssistant) { c.MaxPerMinute = 121 },
		"age":                  func(c *HomeAssistant) { c.EventMaxAgeSeconds = 301 },
		"custom entity filter": func(c *HomeAssistant) { c.Rules[0].EventType = "ottplay_notification" },
	}
	for name, change := range tests {
		t.Run(name, func(t *testing.T) {
			c := validHAConfig(t)
			change(&c)
			err := c.Validate()
			if err == nil {
				t.Fatal("invalid configuration was accepted")
			}
			if strings.Contains(err.Error(), "SECRET") {
				t.Fatal("error leaked credential")
			}
		})
	}
}

func TestHomeAssistantCustomTemplatesAndConfiguredTargets(t *testing.T) {
	ha := validHAConfig(t)
	ha.Rules = []HomeAssistantRule{{ID: "notice", EventType: "ottplay_notification", Title: "{{title}}", Message: "{{message}}", Severity: "critical", Targets: []string{"tv"}}}
	c := Config{AdminToken: strings.Repeat("a", 32), Devices: []Device{{ID: "tv", Token: strings.Repeat("b", 32)}}, HomeAssistant: &ha}
	c.Defaults()
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	c.HomeAssistant.Rules[0].Targets = []string{"missing"}
	if err := c.Validate(); err == nil {
		t.Fatal("target must exist in server device configuration")
	}
	c.HomeAssistant.Enabled = false
	if err := c.Validate(); err != nil {
		t.Fatal("disabled bridge must not prevent old server config from starting", err)
	}
	c.HomeAssistant = nil
	b, err := json.Marshal(c)
	if err != nil || strings.Contains(string(b), "home_assistant") {
		t.Fatal("nil integration should preserve serialized configuration", err)
	}
}
