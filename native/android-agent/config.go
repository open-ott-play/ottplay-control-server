package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
)

const packageName = "play.ott.kitkat"
const dataDir = "/data/local/ott-remote"
const binaryPath = dataDir + "/agent"

var version = "0.1.0"

type Config struct {
	Server          string `json:"server"`
	Token           string `json:"token"`
	Device          string `json:"device"`
	ParentDevice    string `json:"parent_device"`
	Origin          string `json:"origin"`
	UpdatePublicKey string `json:"update_public_key"`
	Model           string `json:"model"`
	Serial          string `json:"serial"`
}

func (c Config) validate() error {
	u, e := url.Parse(c.Server)
	if e != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("invalid HTTPS controller")
	}
	if !regexp.MustCompile(`^[a-zA-Z0-9_-]{32,256}$`).MatchString(c.Token) {
		return errors.New("invalid device credential")
	}
	if c.Device == "" || c.ParentDevice == "" || c.Device == c.ParentDevice {
		return errors.New("separate native device identity required")
	}
	if c.Model != "Q1001L4B2" || !regexp.MustCompile(`^[a-zA-Z0-9]{8,64}$`).MatchString(c.Serial) {
		return errors.New("unsupported device identity")
	}
	o, e := url.Parse(c.Origin)
	if e != nil || o.Scheme != "https" || o.Hostname() == "" || o.User != nil || o.RawQuery != "" || o.Fragment != "" || (o.Path != "" && o.Path != "/") {
		return errors.New("invalid player origin")
	}
	key, e := base64.StdEncoding.DecodeString(c.UpdatePublicKey)
	if e != nil || len(key) != ed25519.PublicKeySize {
		return errors.New("invalid update verification key")
	}
	return nil
}

func loadConfig(path string) (Config, error) {
	var c Config
	st, e := os.Lstat(path)
	if e != nil {
		return c, e
	}
	if !st.Mode().IsRegular() || st.Mode().Perm() != 0600 || st.Size() > 16384 || st.Sys().(*syscall.Stat_t).Uid != uint32(os.Getuid()) {
		return c, errors.New("configuration must be an owned private regular file")
	}
	b, e := os.ReadFile(path)
	if e != nil {
		return c, e
	}
	d := json.NewDecoder(strings.NewReader(string(b)))
	d.DisallowUnknownFields()
	if e = d.Decode(&c); e != nil {
		return c, e
	}
	if d.Decode(new(any)) != io.EOF {
		return c, errors.New("trailing configuration data")
	}
	return c, c.validate()
}

func atomicJSON(path string, value any) error {
	b, e := json.Marshal(value)
	if e != nil {
		return e
	}
	return atomicFile(path, b, 0600)
}
func atomicFile(path string, b []byte, mode os.FileMode) error {
	f, e := os.CreateTemp(filepath.Dir(path), ".remote-")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	if e = f.Chmod(mode); e == nil {
		_, e = f.Write(b)
	}
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return e
	}
	if e = os.Rename(f.Name(), path); e != nil {
		return e
	}
	dir, e := os.Open(filepath.Dir(path))
	if e != nil {
		return e
	}
	defer dir.Close()
	return dir.Sync()
}
