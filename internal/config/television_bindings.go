package config

import (
	"errors"
	"net"
	"path/filepath"
	"strconv"
	"strings"
)

func (c Config) validateTelevisions() error {
	if err := ValidateTelevisions(c.Televisions); err != nil {
		return err
	}
	configured := make(map[string]bool, len(c.Devices))
	for _, d := range c.Devices {
		configured[d.ID] = true
	}
	protected := make(map[string]bool)
	protect := func(path string) {
		if path != "" {
			protected[filepath.Clean(path)] = true
			protected[filepath.Clean(path)+".lock"] = true
		}
	}
	if c.Journal != nil {
		protect(c.Journal.Path)
	}
	if c.PlayerState != nil {
		protect(c.PlayerState.Path)
	}
	if c.DeviceRegistry != nil {
		protect(c.DeviceRegistry.Path)
	}
	if c.HomeAssistant != nil {
		protect(c.HomeAssistant.TokenFile)
	}
	for _, tv := range c.Televisions {
		if tv.Fallback != nil {
			protect(tv.Fallback.TokenFile)
		}
	}
	endpoints := make(map[string]bool)
	keys := make(map[string]bool)
	for _, tv := range c.Televisions {
		if !configured[tv.DeviceID] && c.DeviceRegistry == nil {
			return errors.New("television must name a configured player device_id")
		}
		host := strings.ToLower(tv.Host)
		if ip := net.ParseIP(host); ip != nil {
			host = ip.String()
		}
		endpoint := net.JoinHostPort(host, strconv.Itoa(tv.Port))
		if endpoints[endpoint] {
			return errors.New("television endpoints must have one canonical player binding")
		}
		endpoints[endpoint] = true
		if protected[tv.ClientKeyFile] || keys[tv.ClientKeyFile] {
			return errors.New("television key files must be separate from other keys, tokens and state")
		}
		keys[tv.ClientKeyFile] = true
	}
	return nil
}
