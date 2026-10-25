package config

import (
	"errors"
	"path/filepath"
)

// Serving never replaces a missing persistent volume with an empty registry.
type DeviceRegistry struct {
	Path string `json:"path"`
}

func (r DeviceRegistry) Validate() error {
	if !filepath.IsAbs(r.Path) || filepath.Clean(r.Path) != r.Path {
		return errors.New("device_registry requires an absolute private file path")
	}
	return nil
}

// Run after merging static and durable identities. Revoked identities remain
// known for scope validation, but do not authenticate or receive commands.
func (c Config) ValidateDeviceScopes(known map[string]bool) error {
	for _, op := range c.Operators {
		for _, id := range op.DeviceIDs {
			if !known[id] {
				return errors.New("unknown command operator device scope")
			}
		}
	}
	if c.Diagnostics != nil {
		for _, op := range c.Diagnostics.Operators {
			for _, id := range op.DeviceIDs {
				if !known[id] {
					return errors.New("unknown diagnostic operator device scope")
				}
			}
		}
	}
	if c.HomeAssistant != nil && c.HomeAssistant.Enabled {
		for _, rule := range c.HomeAssistant.Rules {
			for _, id := range rule.Targets {
				if !known[id] {
					return errors.New("unknown Home Assistant target")
				}
			}
		}
	}
	return nil
}
