// Package config loads the static credentials and limits for one server instance.
package config

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
)

const MaxDevices = 64

type Device struct {
	ID    string `json:"id"`
	Token string `json:"token"`
}

type Config struct {
	Listen              string   `json:"listen"`
	AdminToken          string   `json:"admin_token"`
	AllowedOrigins      []string `json:"allowed_origins"`
	AllowNullOrigin     bool     `json:"allow_null_origin,omitempty"`
	Devices             []Device `json:"devices"`
	CommandTTLSeconds   int      `json:"command_ttl_seconds"`
	MaxPendingPerDevice int      `json:"max_pending_per_device"`
}

var deviceID = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,128}$`)
var token = regexp.MustCompile(`^[A-Za-z0-9_-]{32,256}$`)

func (c *Config) Defaults() {
	if c.Listen == "" {
		c.Listen = "127.0.0.1:8081"
	}
	if c.CommandTTLSeconds == 0 {
		c.CommandTTLSeconds = 60
	}
	if c.MaxPendingPerDevice == 0 {
		c.MaxPendingPerDevice = 50
	}
	if c.AllowedOrigins == nil {
		c.AllowedOrigins = []string{}
	}
}

func (c Config) Validate() error {
	_, port, err := net.SplitHostPort(c.Listen)
	if err != nil {
		return errors.New("listen must be a host:port address")
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 0 || n > 65535 {
		return errors.New("listen port must be in 0..65535")
	}
	if !token.MatchString(c.AdminToken) {
		return errors.New("admin_token must contain 32..256 URL-safe characters")
	}
	if len(c.Devices) == 0 || len(c.Devices) > MaxDevices {
		return errors.New("devices must contain 1..64 entries")
	}
	ids, tokens := map[string]bool{}, map[string]bool{c.AdminToken: true}
	for _, d := range c.Devices {
		if !deviceID.MatchString(d.ID) {
			return errors.New("device id must contain 1..128 letters, digits, dots, underscores, colons or hyphens")
		}
		if ids[d.ID] {
			return errors.New("device ids must be unique")
		}
		if !token.MatchString(d.Token) || tokens[d.Token] {
			return errors.New("device tokens must be unique, distinct from admin_token, and contain 32..256 URL-safe characters")
		}
		ids[d.ID], tokens[d.Token] = true, true
	}
	if c.CommandTTLSeconds < 1 || c.CommandTTLSeconds > 3600 {
		return errors.New("command_ttl_seconds must be in 1..3600")
	}
	if c.MaxPendingPerDevice < 1 || c.MaxPendingPerDevice > 50 {
		return errors.New("max_pending_per_device must be in 1..50")
	}
	if len(c.AllowedOrigins) > 64 {
		return errors.New("allowed_origins must contain at most 64 entries")
	}
	origins := map[string]bool{}
	for _, origin := range c.AllowedOrigins {
		u, err := url.Parse(origin)
		if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || u.Opaque != "" || strings.ContainsAny(origin, "\r\n\t *") || origin != u.Scheme+"://"+u.Host || origins[origin] {
			return errors.New("allowed_origins must contain unique exact HTTP(S) origins without paths or wildcards")
		}
		origins[origin] = true
	}
	return nil
}

func Load(path string) (Config, error) {
	var c Config
	f, err := os.Open(path)
	if err != nil {
		return c, errors.New("cannot open configuration file")
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, 1024*1024+1))
	if err != nil || len(b) > 1024*1024 {
		return c, errors.New("cannot read configuration or file exceeds 1 MiB")
	}
	d := json.NewDecoder(strings.NewReader(string(b)))
	d.DisallowUnknownFields()
	if d.Decode(&c) != nil {
		return c, errors.New("configuration must be valid JSON with known fields")
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return c, errors.New("configuration must contain one JSON object")
	}
	c.Defaults()
	return c, c.Validate()
}

func randomToken() (string, error) {
	var b [32]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", errors.New("cannot generate credentials")
	}
	return base64.RawURLEncoding.EncodeToString(b[:]), nil
}

// Init creates a new file exclusively; existing files and symlinks are never replaced.
func Init(path, id string) error {
	c := Config{Devices: []Device{{ID: id}}}
	c.Defaults()
	var err error
	c.AdminToken, err = randomToken()
	if err != nil {
		return err
	}
	c.Devices[0].Token, err = randomToken()
	if err != nil {
		return err
	}
	if err = c.Validate(); err != nil {
		return err
	}
	b, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return errors.New("cannot encode configuration")
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errors.New("cannot create configuration exclusively; choose a new path")
	}
	ok := false
	defer func() {
		f.Close()
		if !ok {
			os.Remove(path)
		}
	}()
	if _, err = f.Write(append(b, '\n')); err != nil {
		return errors.New("cannot write configuration")
	}
	if err = f.Sync(); err != nil {
		return errors.New("cannot sync configuration")
	}
	if err = f.Close(); err != nil {
		return errors.New("cannot close configuration")
	}
	ok = true
	return nil
}
