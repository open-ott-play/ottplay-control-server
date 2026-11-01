package config

import (
	"encoding/hex"
	"errors"
	"net"
	"net/url"
	"path/filepath"
	"regexp"
	"strings"
)

// Television binds an existing player identity to one physical television.
// Addresses and credentials come only from administrator-owned configuration,
// never from remote command parameters.
type Television struct {
	DeviceID          string              `json:"device_id"`
	Host              string              `json:"host"`
	Port              int                 `json:"port,omitempty"`
	TLS               *bool               `json:"tls,omitempty"`
	CertificateSHA256 string              `json:"certificate_sha256,omitempty"`
	ClientKeyFile     string              `json:"client_key_file"`
	MAC               string              `json:"mac,omitempty"`
	Broadcast         string              `json:"broadcast,omitempty"`
	WOLPort           int                 `json:"wol_port,omitempty"`
	AppID             string              `json:"app_id,omitempty"`
	LaunchURL         string              `json:"launch_url,omitempty"`
	Fallback          *TelevisionFallback `json:"fallback,omitempty"`
}

type TelevisionFallback struct {
	URL       string `json:"url"`
	TokenFile string `json:"token_file"`
	EntityID  string `json:"entity_id"`
}

var televisionHost = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$`)
var televisionApp = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$`)

func (c Television) Secure() bool { return c.TLS == nil || *c.TLS }

func (c *Television) Defaults() {
	if c.Port == 0 {
		c.Port = 3001
		if !c.Secure() {
			c.Port = 3000
		}
	}
	if c.Broadcast == "" {
		c.Broadcast = "255.255.255.255"
	}
	if c.WOLPort == 0 {
		c.WOLPort = 9
	}
}

func televisionPrivatePath(path string) bool {
	return filepath.IsAbs(path) && filepath.Clean(path) == path && filepath.Dir(path) != path && !strings.ContainsAny(path, "\x00\r\n")
}

func televisionURL(value string, base bool) bool {
	u, err := url.Parse(value)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Hostname() == "" || u.User != nil || u.Opaque != "" || u.Fragment != "" || u.RawPath != "" || strings.ContainsAny(value, "\\\x00\r\n\t ") {
		return false
	}
	if base && (u.RawQuery != "" || u.ForceQuery) {
		return false
	}
	for _, part := range strings.Split(u.Path, "/") {
		if part == "." || part == ".." {
			return false
		}
	}
	return len(value) <= 2048
}

func (c Television) Validate() error {
	if !deviceID.MatchString(c.DeviceID) {
		return errors.New("television requires a valid device_id")
	}
	if net.ParseIP(c.Host) == nil && (!televisionHost.MatchString(c.Host) || strings.Contains(c.Host, "..")) {
		return errors.New("television host must be an IP address or hostname")
	}
	if c.Port < 1 || c.Port > 65535 || c.WOLPort < 1 || c.WOLPort > 65535 {
		return errors.New("television ports must be in 1..65535")
	}
	if !televisionPrivatePath(c.ClientKeyFile) {
		return errors.New("television client_key_file must be an absolute private file path")
	}
	if c.CertificateSHA256 != "" {
		pin, err := hex.DecodeString(c.CertificateSHA256)
		if err != nil || len(pin) != 32 || hex.EncodeToString(pin) != c.CertificateSHA256 {
			return errors.New("television certificate_sha256 must be 64 lowercase hexadecimal characters")
		}
	}
	if c.MAC != "" {
		mac, err := net.ParseMAC(c.MAC)
		if err != nil || len(mac) != 6 || mac[0]&1 != 0 {
			return errors.New("television mac must be a unicast Ethernet MAC address")
		}
		zero := true
		for _, b := range mac {
			zero = zero && b == 0
		}
		if zero {
			return errors.New("television mac must not be all zero")
		}
	}
	if ip := net.ParseIP(c.Broadcast); ip == nil || ip.To4() == nil {
		return errors.New("television broadcast must be an IPv4 address")
	}
	if (c.AppID != "" && !televisionApp.MatchString(c.AppID)) || (c.LaunchURL != "" && !televisionURL(c.LaunchURL, false)) || (c.AppID != "" && c.LaunchURL != "") {
		return errors.New("television requires at most one valid app_id or HTTP(S) launch_url")
	}
	if c.Fallback != nil {
		f := c.Fallback
		if !televisionURL(f.URL, true) || !televisionPrivatePath(f.TokenFile) || !haEntity.MatchString(f.EntityID) || !strings.HasPrefix(f.EntityID, "media_player.") || len(f.EntityID) > 255 {
			return errors.New("television fallback requires a base URL, private token_file and media_player entity_id")
		}
	}
	return nil
}

func ValidateTelevisions(items []Television) error {
	if len(items) > MaxDevices {
		return errors.New("too many television bindings")
	}
	seen := map[string]bool{}
	for _, item := range items {
		if err := item.Validate(); err != nil {
			return err
		}
		if seen[item.DeviceID] {
			return errors.New("television device_id must be unique")
		}
		seen[item.DeviceID] = true
	}
	return nil
}
